# ╔══════════════════════════════════════════════════════════════════╗
#  GIBBZ SMC COP — market_feed.py
#  ATAS UDP Bridge Receiver v1.2
#
#  v1.2 changes (bridge hardening):
#  - R1 fix: replaced single-slot _latest with bounded FIFO deque
#    (maxlen=128) — eliminates tick-loss during burst periods.
#    Producer: deque.append().  Consumer: deque.popleft() (FIFO).
#  - R2 fix: _parse() now adds recv_ts (Python wall-clock, sub-ms)
#    to every parsed tick.  Enables live latency measurement:
#    transport_ms = (recv_ts - timestamp) * 1000
#    Bridge v2.3+ sends millisecond-precision ATAS timestamps
#    (field 10); float() parse preserves decimal part.
#
#  DATA FORMAT FROM ATAS (CSV) — GibbzBridge.cs payload order:
#  pos 0  = Close  ← PRICE
#  pos 1  = Open
#  pos 2  = High
#  pos 3  = Low
#  pos 4  = Close  (duplicate — same as pos 0)
#  pos 5  = Volume
#  pos 6  = Delta
#  pos 7  = AskVol  (calculado: (Volume + Delta) / 2)
#  pos 8  = BidVol  (calculado: (Volume - Delta) / 2)
#  pos 9  = Trades  (siempre 0 desde el Bridge)
#  pos 10 = Timestamp (Unix seconds — float ms-precision from v2.3)
#  pos 11 = Symbol
#  pos 12 = BarIndex
#
#  USAGE:
#  feed = MarketFeed(port=9999)
#  feed.start()
#  raw = feed.get_latest_blocking()  # returns oldest buffered tick
# ╚══════════════════════════════════════════════════════════════════╝

import socket
import threading
import time
from collections import deque
from typing import Optional
from log_config import get_logger

_log = get_logger("market_feed")


# ══════════════════════════════════════════════════════════════════
#  FIELD INDEX MAP (must match GibbzBridge.cs payload order)
# ══════════════════════════════════════════════════════════════════

IDX_PRICE     = 0   # c.Close
IDX_OPEN      = 1   # c.Open
IDX_HIGH      = 2   # c.High
IDX_LOW       = 3   # c.Low
IDX_CLOSE     = 4   # c.Close (duplicate)
IDX_VOLUME    = 5   # c.Volume
IDX_DELTA     = 6   # c.Delta
IDX_ASK_VOL   = 7   # (Volume + Delta) / 2
IDX_BID_VOL   = 8   # (Volume - Delta) / 2
IDX_TRADES    = 9   # always 0
IDX_TIMESTAMP = 10  # Unix seconds
IDX_SYMBOL    = 11
IDX_BAR_INDEX = 12

# ══════════════════════════════════════════════════════════════════
#  DEBUG — precio verificado y confirmado vs ATAS ✅
#  Cambiar a True solo si necesitas re-verificar el feed
# ══════════════════════════════════════════════════════════════════
DEBUG_PRINT_RAW = False


class MarketFeed:
    """
    GIBBZ UDP Market Feed Receiver v1.2.

    Listens on UDP port for data from GibbzBridge ATAS indicator.
    Runs in a background thread — non-blocking.

    Tick storage (R1 fix):
    - _queue: deque(maxlen=128) — bounded FIFO, replaces single-slot _latest.
    - Producer (receiver thread): deque.append() — newest at right.
    - Consumer (engine thread):   deque.popleft() — processes oldest first.
    - At maxlen=128 and peak 327 ticks/sec, buffer holds ~390ms of data.
    - If queue fills, oldest tick is silently discarded (bounded drop vs
      unbounded accumulation). In TIME-5s mode the bar OHLCV is unaffected.

    Timestamp (R2 fix):
    - recv_ts added to every parsed tick (Python time.time(), sub-ms).
    - Live latency = (recv_ts - timestamp) * 1000  ms.
    - Bridge v2.3+ sends float ms-precision ATAS timestamp in field 10.

    Thread safety:
    - _queue is written by receiver thread, read/consumed by engine thread.
    - Lock protects the check+popleft sequence.
    """

    BUFFER_SIZE  = 1024
    TIMEOUT      = 0.1
    QUEUE_MAXLEN = 128    # ~390ms at peak 327 ticks/sec

    def __init__(self,
                 host: str = "127.0.0.1",
                 port: int = 9999):
        self._host:      str                             = host
        self._port:      int                             = port
        self._socket:    Optional[socket.socket]         = None
        self._thread:    Optional[threading.Thread]      = None
        self._running:   bool                            = False
        self._queue:     deque                           = deque(maxlen=self.QUEUE_MAXLEN)
        self._lock:      threading.Lock                  = threading.Lock()
        self._count:     int                             = 0
        self._errors:    int                             = 0
        self._last_raw:  str                             = ""
        self._connected: bool                            = False

    # ──────────────────────────────────────────────────────────────
    #  START / STOP
    # ──────────────────────────────────────────────────────────────

    def start(self) -> None:
        if self._running:
            return
        self._socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._socket.settimeout(self.TIMEOUT)
        self._socket.bind((self._host, self._port))
        self._running = True
        self._thread  = threading.Thread(
            target = self._receive_loop,
            daemon = True,
            name   = "GibbzUDPFeed"
        )
        self._thread.start()
        print("GIBBZ Feed listening on " +
              self._host + ":" + str(self._port))

    def stop(self) -> None:
        self._running = False
        try:
            if self._socket:
                self._socket.close()
        except Exception:
            pass

    # ──────────────────────────────────────────────────────────────
    #  GET TICK (public consumer API)
    # ──────────────────────────────────────────────────────────────

    def get_latest(self) -> Optional[dict]:
        """Peek at the most-recently received tick without consuming it."""
        with self._lock:
            return self._queue[-1] if self._queue else None

    def get_latest_blocking(self, timeout: float = 5.0) -> Optional[dict]:
        """
        Pop and return the oldest buffered tick (FIFO).
        Blocks up to `timeout` seconds if queue is empty.
        Returns None on timeout.
        """
        deadline = time.time() + timeout
        while time.time() < deadline:
            with self._lock:
                if self._queue:
                    return self._queue.popleft()
            time.sleep(0.05)
        return None

    # ──────────────────────────────────────────────────────────────
    #  STATUS
    # ──────────────────────────────────────────────────────────────

    @property
    def is_connected(self) -> bool:
        return self._connected

    @property
    def packets_received(self) -> int:
        return self._count

    @property
    def queue_size(self) -> int:
        """Number of ticks currently buffered and waiting to be consumed."""
        with self._lock:
            return len(self._queue)

    @property
    def last_raw(self) -> str:
        return self._last_raw

    def status_str(self) -> str:
        if not self._running:
            return "STOPPED"
        if self._connected:
            return "LIVE  packets=" + str(self._count) + "  queued=" + str(self.queue_size)
        return "WAITING for ATAS data on port " + str(self._port)

    # ──────────────────────────────────────────────────────────────
    #  RECEIVE LOOP
    # ──────────────────────────────────────────────────────────────

    def _receive_loop(self) -> None:
        assert self._socket is not None  # set by start() before thread launch
        last_packet_time = 0.0
        while self._running:
            try:
                data, addr     = self._socket.recvfrom(self.BUFFER_SIZE)
                raw            = data.decode("utf-8").strip()
                self._last_raw = raw

                if DEBUG_PRINT_RAW:
                    parts         = raw.split(",")
                    precio_python = parts[0]  if len(parts) > 0  else "?"
                    simbolo       = parts[11] if len(parts) > 11 else "?"
                    bar_idx       = parts[12] if len(parts) > 12 else "?"
                    print(
                        f"[UDP] precio={precio_python}  "
                        f"simbolo={simbolo}  "
                        f"bar={bar_idx}  "
                        f"← compara con ATAS"
                    )

                parsed = self._parse(raw)
                if parsed:
                    with self._lock:
                        self._queue.append(parsed)   # R1: buffered FIFO
                    self._count      += 1
                    last_packet_time  = time.time()
                    self._connected   = True

            except socket.timeout:
                if last_packet_time > 0:
                    if time.time() - last_packet_time > 10.0:
                        self._connected = False
                continue

            except OSError as e:
                _log.critical(
                    "UDP receiver thread dying on OSError: %s — "
                    "engine will process stale data until restarted", e
                )
                self._connected = False
                self._running   = False
                break

            except Exception as e:
                self._errors += 1
                _log.warning("receive loop error #%d: %s", self._errors, e)
                continue

    # ──────────────────────────────────────────────────────────────
    #  PARSE UDP PACKET → dict
    # ──────────────────────────────────────────────────────────────

    def _parse(self, raw: str) -> Optional[dict]:
        if not raw:
            return None
        try:
            parts = raw.split(",")
            if len(parts) < 13:
                return None

            price   = float(parts[IDX_PRICE])
            open_p  = float(parts[IDX_OPEN])
            high    = float(parts[IDX_HIGH])
            low     = float(parts[IDX_LOW])
            close   = float(parts[IDX_CLOSE])
            volume  = float(parts[IDX_VOLUME])
            delta   = float(parts[IDX_DELTA])
            ask_vol = float(parts[IDX_ASK_VOL])
            bid_vol = float(parts[IDX_BID_VOL])
            trades  = int(float(parts[IDX_TRADES]))
            ts      = float(parts[IDX_TIMESTAMP]) if parts[IDX_TIMESTAMP] else 0.0
            symbol  = parts[IDX_SYMBOL].strip() if len(parts) > IDX_SYMBOL else "UNKNOWN"

            if price <= 0 or volume < 0:
                return None

            return {
                "price":      price,
                "open":       open_p,
                "high":       high,
                "low":        low,
                "close":      close,
                "volume":     volume,
                "delta":      delta,
                "ask_volume": ask_vol,
                "bid_volume": bid_vol,
                "trades":     trades,
                "timestamp":  ts,
                "symbol":     symbol,
                # R2: Python wall-clock when packet arrived at receiver thread.
                # Live latency (ms) = (recv_ts - timestamp) * 1000
                # (only meaningful when bridge sends ms-precision timestamps)
                "recv_ts":    time.time(),
            }

        except (ValueError, IndexError):
            return None