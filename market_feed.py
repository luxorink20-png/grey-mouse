# ╔══════════════════════════════════════════════════════════════════╗
#  GIBBZ SMC COP — market_feed.py
#  ATAS UDP Bridge Receiver v1.3
#
#  v1.3 changes (Windows socket stability):
#  - R3 fix: stop() joins receiver thread BEFORE closing socket.
#    Prevents WinError 10038 (WSAENOTSOCK) on Windows — caused by closing
#    a socket from the main thread while recvfrom() is executing in the
#    receiver thread.  Thread exits within one TIMEOUT period (100ms).
#  - R3 fix: OSError in _receive_loop distinguishes shutdown (DEBUG) from
#    mid-session crash (CRITICAL + full traceback + auto-reconnect).
#  - R3 fix: SO_RCVBUF raised to 208 KB (Windows default is 8 KB).
#  - R3 fix: rate logging every 60s (packets/min + total + queue depth).
#  - R3 fix: first ATAS packet logged at INFO with symbol and price.
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
import traceback
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

    BUFFER_SIZE      = 1024
    TIMEOUT          = 0.1
    QUEUE_MAXLEN     = 128    # ~390ms at peak 327 ticks/sec
    RECV_BUF         = 212992 # 208 KB OS receive buffer (Windows default is 8 KB)
    _LOG_RATE_SECS   = 60     # log packets/min every N seconds

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
        self._count:          int                         = 0
        self._errors:         int                         = 0
        self._last_raw:       str                         = ""
        self._connected:      bool                        = False
        self._first_packet:   bool                        = True
        self._rate_count:     int                         = 0
        self._last_rate_log:  float                       = 0.0

    # ──────────────────────────────────────────────────────────────
    #  START / STOP
    # ──────────────────────────────────────────────────────────────

    def start(self) -> None:
        if self._running:
            return
        self._socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._socket.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, self.RECV_BUF)
        self._socket.settimeout(self.TIMEOUT)
        self._socket.bind((self._host, self._port))
        self._running = True
        self._thread  = threading.Thread(
            target = self._receive_loop,
            daemon = True,
            name   = "GibbzUDPFeed"
        )
        self._thread.start()
        msg = "GIBBZ Feed listening on " + self._host + ":" + str(self._port)
        print(msg)
        _log.info("UDP socket bound — %s", msg)

    def stop(self) -> None:
        self._running = False
        # Join the receiver thread BEFORE closing the socket.
        # On Windows, closing a socket from a different thread while recvfrom()
        # is executing raises WinError 10038 (WSAENOTSOCK) in the receiver thread.
        # With TIMEOUT=0.1s the thread exits its current recvfrom() within 100ms
        # and then sees _running=False and breaks cleanly.
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=1.0)
        try:
            if self._socket is not None:
                self._socket.close()
                self._socket = None
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
        self._last_rate_log = time.time()

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
                    self._rate_count += 1
                    last_packet_time  = time.time()
                    self._connected   = True

                    # Log first packet from ATAS — confirms bridge is live
                    if self._first_packet:
                        self._first_packet = False
                        sym = parsed.get("symbol", "?")
                        _log.info(
                            "First ATAS tick received | symbol=%s price=%.2f | "
                            "bridge is live on %s:%d",
                            sym, parsed["price"], self._host, self._port,
                        )

                    # Rate log every _LOG_RATE_SECS seconds
                    now = time.time()
                    if now - self._last_rate_log >= self._LOG_RATE_SECS:
                        _log.info(
                            "UDP feed: %d packets/min | total=%d | queued=%d",
                            self._rate_count,
                            self._count,
                            len(self._queue),
                        )
                        self._rate_count    = 0
                        self._last_rate_log = now

            except socket.timeout:
                if last_packet_time > 0:
                    if time.time() - last_packet_time > 10.0:
                        self._connected = False
                continue

            except OSError as e:
                if not self._running:
                    # Normal shutdown — stop() set _running=False before closing socket.
                    _log.debug("UDP receiver stopped cleanly (shutdown signal received)")
                    break

                # Unexpected mid-session crash — log full traceback and try to recover.
                _log.critical(
                    "UDP receiver OSError (winerror=%s): %s\n%s",
                    getattr(e, "winerror", "n/a"), e, traceback.format_exc(),
                )
                self._connected = False
                if self._reconnect():
                    last_packet_time = 0.0
                    _log.info("UDP receiver resumed after socket reconnect")
                else:
                    self._running = False
                    break

            except Exception as e:
                self._errors += 1
                _log.warning("receive loop error #%d: %s\n%s",
                             self._errors, e, traceback.format_exc())
                continue

    def _reconnect(self) -> bool:
        """Recreate the UDP socket after an unexpected mid-session crash.

        Called only when _running is True (real crash, not a shutdown).
        Returns True if the socket was successfully re-bound.
        """
        try:
            if self._socket is not None:
                try:
                    self._socket.close()
                except Exception:
                    pass
                self._socket = None
            time.sleep(0.5)
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, self.RECV_BUF)
            sock.settimeout(self.TIMEOUT)
            sock.bind((self._host, self._port))
            self._socket = sock
            _log.info(
                "UDP socket reconnected on %s:%d (auto-recovery after crash)",
                self._host, self._port,
            )
            return True
        except Exception as reconn_err:
            _log.critical("UDP socket reconnect failed — engine stopped: %s", reconn_err)
            return False

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