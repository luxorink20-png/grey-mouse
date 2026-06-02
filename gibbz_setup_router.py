# ╔══════════════════════════════════════════════════════════════════╗
#  GIBBZ SMC COP — gibbz_setup_router.py  (v3 — FINAL)
#  Priority router — evaluates every bar and returns the highest-
#  priority active setup with direction, confidence, stop, and target.
#
#  Priority order (highest → lowest):
#    1. INSTITUTIONAL_GRADE — GTAL_VALID + risk approved  (never blocked)
#    2. VA80_SETUP          — 80% Rule Value Area
#    3. FA_SETUP            — Failed Auction (LONG or SHORT)
#    4. NO_SETUP            — nothing active
#
#  Removed after 43-session backtest validation:
#    ORB_SETUP   — 0 trades fired across pool
#    VWAP_SETUP  — 0 trades fired (trap filter blocks all)
#    GAP_SETUP   — Exp=-1.43 unfiltered, -1.05 filtered → eliminated
#    POC_SETUP   — Exp degraded monotonically with every filter tested → eliminated
#    BOUNCE_SETUP — 0 trades fired across pool
#
#  Active filters (applied to VA80 and FA):
#    Trend:      EFFICIENT_TREND/LIQUIDATION + ABOVE_VWAP → block SHORT
#                EFFICIENT_TREND/LIQUIDATION + BELOW_VWAP → block LONG
#    Dislocation: |open - VAH| > 150 pts → block LONG (session opened far from VA)
#                 |open - VAL| > 150 pts → block SHORT
#    INSTITUTIONAL_GRADE bypasses all filters.
# ╚══════════════════════════════════════════════════════════════════╝

from dataclasses import dataclass

_TREND_ENVS    = {"EFFICIENT_TREND", "LIQUIDATION"}
_DISLOC_THRESH = 150.0  # pts — extreme opening dislocation from VA boundary


@dataclass
class SetupResult:
    signal_type: str   # INSTITUTIONAL_GRADE | VA80_SETUP | FA_SETUP | NO_SETUP
    direction:   str   # LONG | SHORT | NEUTRAL
    confidence:  int   # 0-100
    stop_pts:    float # suggested stop distance in points
    target_pts:  float # suggested target distance in points
    reason:      str   # one-line explanation


class SetupRouter:
    """
    Per-session router. Call .route(**kwargs) each bar.
    Stateless — all inputs come from detector results each bar.
    Detector references (or_r, vwap_r, gap_r, poc_r, bounce_r) are accepted
    but unused; kept in signature for pipeline compatibility.
    """

    def __init__(self, disloc_thresh: float = _DISLOC_THRESH):
        self._disloc_thresh = disloc_thresh

    def route(self, *,
              bar_count:  int,
              price:      float,
              vah:        float,
              val:        float,
              poc:        float,
              open_price: float = 0.0,
              # core engines
              gtal_r,
              risk,
              env_r,
              # detectors — accepted for pipeline compatibility, not used in routing
              or_r,
              ibh_setup: str,
              fa_r,
              va80_r,
              vwap_r,
              gap_r,
              poc_r,
              bounce_r) -> SetupResult:

        va_range = max(vah - val, 1.0)

        # ── Dislocation filter ────────────────────────────────────────
        disloc_block_long  = open_price > 0 and abs(open_price - vah) > self._disloc_thresh
        disloc_block_short = open_price > 0 and abs(open_price - val) > self._disloc_thresh

        # ── Trend filter ──────────────────────────────────────────────
        env_name   = getattr(env_r,  "environment", "ROTATIONAL")
        vwap_state = getattr(vwap_r, "state",       "NO_DATA")

        trend_block: str | None = None
        if env_name in _TREND_ENVS:
            if vwap_state == "ABOVE_VWAP":
                trend_block = "SHORT"
            elif vwap_state == "BELOW_VWAP":
                trend_block = "LONG"

        def blocked(direction: str) -> bool:
            if trend_block == direction:
                return True
            if direction == "LONG"  and disloc_block_long:
                return True
            if direction == "SHORT" and disloc_block_short:
                return True
            return False

        # ── 1. INSTITUTIONAL_GRADE (never filtered) ───────────────────
        gtal_valid = getattr(gtal_r, "execution_validity", "INVALID") == "VALID"
        risk_ok    = getattr(risk,   "approved",           False)
        if gtal_valid and risk_ok:
            direction = getattr(risk, "direction", "NEUTRAL")
            rt        = getattr(gtal_r, "real_tradeability_score", 70)
            conf      = min(90 + rt // 20, 98)
            stop_abs  = getattr(risk, "stop",     0.0)
            tgt_abs   = getattr(risk, "target_1", 0.0)
            stop_pts  = round(abs(price - stop_abs), 2) if stop_abs > 0 else 6.0
            tgt_pts   = round(abs(tgt_abs - price),  2) if tgt_abs  > 0 else 12.0
            return SetupResult("INSTITUTIONAL_GRADE", direction, conf,
                               stop_pts, tgt_pts, f"GTAL_VALID RT={rt}")

        # ── 2. VA80_SETUP ─────────────────────────────────────────────
        va80_sig = getattr(va80_r, "signal", "NONE")
        if va80_sig in ("VA_RULE80_LONG", "VA_RULE80_SHORT"):
            direction = "LONG" if va80_sig == "VA_RULE80_LONG" else "SHORT"
            if not blocked(direction):
                tgt_abs = getattr(va80_r, "target", 0.0)
                tgt_pts = round(abs(tgt_abs - price), 2) if tgt_abs > 0 \
                          else round(va_range * 0.8, 2)
                return SetupResult("VA80_SETUP", direction, 70,
                                   6.0, tgt_pts,
                                   f"VA80 {direction} target={tgt_abs:.0f}")

        # ── 3. FA_SETUP ───────────────────────────────────────────────
        fa_sig = getattr(fa_r, "signal", "NONE")
        if fa_sig in ("FAILED_AUCTION_LONG", "FAILED_AUCTION_SHORT"):
            direction = "LONG" if fa_sig == "FAILED_AUCTION_LONG" else "SHORT"
            if not blocked(direction):
                return SetupResult("FA_SETUP", direction, 75,
                                   8.0, round(va_range, 2),
                                   f"FailedAuction {direction}")

        # ── 4. NO_SETUP ───────────────────────────────────────────────
        return SetupResult("NO_SETUP", "NEUTRAL", 0, 0.0, 0.0, "")
