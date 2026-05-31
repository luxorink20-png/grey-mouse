import os
import time
from collections import deque


class C:
    RESET   = "\033[0m"
    BOLD    = "\033[1m"
    DIM     = "\033[2m"
    WHITE   = "\033[97m"
    GRAY    = "\033[37m"
    CYAN    = "\033[96m"
    GREEN   = "\033[92m"
    YELLOW  = "\033[93m"
    RED     = "\033[91m"
    ORANGE  = "\033[38;5;208m"
    BLUE    = "\033[94m"
    MAGENTA = "\033[95m"


EVENT_COLOR = {
    "INTENTO":     C.YELLOW,
    "FALLO":       C.RED,
    "ACUMULACION": C.ORANGE,
    "ACUMULACIÓN": C.ORANGE,
    "AGOTAMIENTO": C.GREEN,
    "INIT":        C.CYAN,
    "NONE":        C.GRAY,
}

EVENT_ICON = {
    "INTENTO":     "^",
    "FALLO":       "X",
    "ACUMULACION": "o",
    "ACUMULACIÓN": "o",
    "AGOTAMIENTO": "!",
    "INIT":        "-",
    "NONE":        ".",
}

MOMENTUM_MAP = {
    "INTENTO":     ("EXPANSION",  C.YELLOW),
    "FALLO":       ("REVERSAL",   C.RED),
    "ACUMULACION": ("RANGE",      C.ORANGE),
    "ACUMULACIÓN": ("RANGE",      C.ORANGE),
    "AGOTAMIENTO": ("EXHAUSTION", C.GREEN),
    "INIT":        ("NEUTRAL",    C.CYAN),
    "NONE":        ("NEUTRAL",    C.GRAY),
}

MOMENTUM_BARS = {
    "EXPANSION":  "████████░░  expanding",
    "REVERSAL":   "██░░░░░░░░  reversing",
    "RANGE":      "█████░░░░░  ranging",
    "EXHAUSTION": "██████████  exhausted",
    "NEUTRAL":    "░░░░░░░░░░  neutral",
}

SCORE_COLOR = {
    "INSTITUTIONAL GRADE": C.MAGENTA,
    "HIGH QUALITY":        C.GREEN,
    "MEDIUM QUALITY":      C.YELLOW,
    "LOW QUALITY":         C.GRAY,
    "NO TRADE ZONE":       C.RED,
}

ACTION_COLOR = {
    "ENTER":   C.GREEN,
    "WATCH":   C.YELLOW,
    "OBSERVE": C.ORANGE,
    "IGNORE":  C.GRAY,
}

NARRATIVE_COLOR = {
    "INDUCTION":    C.RED,
    "DISTRIBUTION": C.ORANGE,
    "ACCUMULATION": C.GREEN,
    "SQUEEZE":      C.MAGENTA,
    "REBALANCE":    C.CYAN,
    "UNCLEAR":      C.GRAY,
}

NARRATIVE_ICON = {
    "INDUCTION":    "TRAP",
    "DISTRIBUTION": "DIST",
    "ACCUMULATION": "ACCM",
    "SQUEEZE":      "SQZE",
    "REBALANCE":    "REBL",
    "UNCLEAR":      "----",
}

RESULT_COLOR = {
    "WIN":       C.GREEN,
    "LOSS":      C.RED,
    "BREAKEVEN": C.YELLOW,
    "TIMEOUT":   C.GRAY,
    "PENDING":   C.CYAN,
}


class EngineView:

    WIDTH = 50

    def __init__(self, history_size=5):
        self._history      = deque(maxlen=history_size)
        self._tick         = 0
        self._session_high = float("-inf")
        self._session_low  = float("inf")
        self._start_time   = time.time()
        self._prev_price   = 0.0
        self._event_counts = {
            "INTENTO": 0, "FALLO": 0,
            "ACUMULACION": 0, "AGOTAMIENTO": 0
        }

    def add_event(self, event):
        key = event.replace("ACUMULACIÓN", "ACUMULACION")
        self._history.append({
            "event": event,
            "tick":  self._tick,
            "time":  time.strftime("%H:%M:%S"),
        })
        if key in self._event_counts:
            self._event_counts[key] += 1

    def update(self, price, result, context=None,
               analysis=None, session_name="",
               session_active=True, validation=None,
               narrative=None, risk_result=None,
               feedback=None, closed_trade=None,
               pending_trade=None):
        event = (result.get("event", "NONE")
                 if isinstance(result, dict) else str(result))
        self.add_event(event)
        self.render(price, result, context, analysis,
                    session_name, session_active, validation,
                    narrative, risk_result, feedback,
                    closed_trade, pending_trade)

    def render(self, price, result, context=None,
               analysis=None, session_name="",
               session_active=True, validation=None,
               narrative=None, risk_result=None,
               feedback=None, closed_trade=None,
               pending_trade=None):
        self._tick += 1

        if isinstance(result, dict):
            event      = result.get("event",      "NONE")
            confidence = result.get("confidence", 0)
            reason     = result.get("reason",     "")
            ctx        = result.get("context",    {})
            delta      = ctx.get("delta",      0)
            volume     = ctx.get("volume",     0)
            absorption = ctx.get("absorption", False)
        else:
            event      = str(result)
            confidence = 0
            reason     = ""
            delta      = 0
            volume     = 0
            absorption = False

        self._session_high = max(self._session_high, price)
        self._session_low  = min(self._session_low,  price)
        price_dir = ("^" if price > self._prev_price
                     else "v" if price < self._prev_price else "-")
        self._prev_price = price

        momentum_state, momentum_color = MOMENTUM_MAP.get(
            event, ("NEUTRAL", C.GRAY))
        elapsed     = int(time.time() - self._start_time)
        elapsed_str = (str(elapsed // 60).zfill(2) + ":" +
                       str(elapsed % 60).zfill(2))
        ev_color    = EVENT_COLOR.get(event, C.GRAY)
        ev_icon     = EVENT_ICON.get(event, ".")

        print("\033[2J\033[H", end="", flush=True)
        w    = self.WIDTH
        line = "=" * w

        # ── HEADER ────────────────────────────────────────────────
        print(C.CYAN + C.BOLD + line + C.RESET)
        print(self._center("GIBBZ  SMC  COP", w, C.CYAN + C.BOLD))
        print(self._center(
            "LIVE DASHBOARD  |  " + elapsed_str, w, C.DIM))
        print(C.CYAN + line + C.RESET)
        print()

        # ── SESSION ───────────────────────────────────────────────
        if session_name:
            sc   = C.GREEN if session_active else C.RED
            icon = "*" if session_active else "o"
            print("  " + sc + icon + " " + session_name + C.RESET)
            if not session_active:
                print("  " + C.RED + C.BOLD +
                      "  NO TRADE ZONE" + C.RESET)
            print()

        # ── PRICE ─────────────────────────────────────────────────
        pc = (C.GREEN if price_dir == "^" else
              C.RED   if price_dir == "v" else C.WHITE)
        print("  " + C.DIM + "PRICE" + C.RESET +
              "          " + pc + C.BOLD +
              price_dir + " " + str(price) + C.RESET)
        print("  " + C.DIM + "SESSION H/L" + C.RESET +
              "    " + C.GREEN + str(self._session_high) + C.RESET +
              " / " + C.RED + str(self._session_low) + C.RESET)
        print()

        # ── EVENT ─────────────────────────────────────────────────
        print("  " + C.DIM + "EVENT" + C.RESET +
              "          " + ev_color + C.BOLD +
              ev_icon + " " + event + C.RESET +
              "  " + C.DIM + str(confidence) + "%" + C.RESET)
        if reason:
            short = reason[:w - 6] if len(reason) > w - 6 else reason
            print("  " + C.DIM + "  " + short + C.RESET)
        print()

        # ── MOMENTUM ──────────────────────────────────────────────
        print("  " + C.DIM + "MOMENTUM" + C.RESET +
              "       " + momentum_color + C.BOLD +
              momentum_state + C.RESET)
        print("  " + momentum_color +
              MOMENTUM_BARS.get(momentum_state, "░░░░░░░░░░  neutral") +
              C.RESET)
        print()

        # ── ORDER FLOW ────────────────────────────────────────────
        print("  " + C.DIM + "ORDER FLOW" + C.RESET)
        dc = C.GREEN if delta > 0 else C.RED if delta < 0 else C.GRAY
        print("  " + C.DIM + "  Delta" + C.RESET +
              "        " + dc + str(int(delta)) + C.RESET)
        print("  " + C.DIM + "  Volume" + C.RESET +
              "       " + C.WHITE + str(int(volume)) + C.RESET)
        ac = C.YELLOW if absorption else C.DIM
        print("  " + C.DIM + "  Absorb" + C.RESET +
              "       " + ac + ("YES" if absorption else "no") + C.RESET)
        print()

        # ── INST LEVELS ───────────────────────────────────────────
        if context is not None:
            print("  " + C.DIM + "INST LEVELS" + C.RESET)
            zc = (C.GREEN  if context.zone in ("BELOW_VAL", "AT_VAL")
                  else C.RED    if context.zone in ("ABOVE_VAH", "AT_VAH")
                  else C.YELLOW if "POC" in context.zone
                  else C.WHITE)
            print("  " + C.DIM + "  Zone" + C.RESET +
                  "         " + zc + C.BOLD + context.zone + C.RESET)
            bc = (C.GREEN if context.reaction_bias == "BULLISH"
                  else C.RED if context.reaction_bias == "BEARISH"
                  else C.GRAY)
            print("  " + C.DIM + "  Bias" + C.RESET +
                  "         " + bc + C.BOLD +
                  context.reaction_bias + C.RESET)
            dc2 = C.YELLOW if abs(context.nearest_distance) <= 2 \
                else C.WHITE
            print("  " + C.DIM + "  Nearest" + C.RESET +
                  "      " + dc2 + context.nearest_level +
                  " " + str(round(context.nearest_distance, 2)) +
                  "pts" + C.RESET)
            if context.near_levels:
                print("  " + C.DIM + "  Near" + C.RESET +
                      "         " + C.YELLOW +
                      " | ".join(context.near_levels) + C.RESET)
            if context.high_prob_zone:
                print("  " + C.YELLOW + C.BOLD +
                      "  HPZ ACTIVE" + C.RESET)
            print()

        # ── CONFLUENCE ────────────────────────────────────────────
        if analysis is not None:
            sc2 = SCORE_COLOR.get(analysis.classification, C.GRAY)
            ac2 = ACTION_COLOR.get(analysis.action, C.GRAY)
            print("  " + C.DIM + "-" * (w - 4) + C.RESET)
            print("  " + C.BOLD + "CONFLUENCE" + C.RESET)
            print()
            filled = analysis.score // 10
            bar    = chr(9608) * filled + chr(9617) * (10 - filled)
            print("  " + C.DIM + "  Score" + C.RESET +
                  "        " + sc2 + C.BOLD +
                  str(analysis.score) + "/100" + C.RESET +
                  "  " + sc2 + bar + C.RESET)
            print("  " + C.DIM + "  Signal" + C.RESET +
                  "       " + sc2 + C.BOLD +
                  analysis.classification + C.RESET)
            print("  " + C.DIM + "  Action" + C.RESET +
                  "       " + ac2 + C.BOLD + analysis.action + C.RESET)
            print("  " + C.DIM + "  " +
                  analysis.confluence + C.RESET)
            bc2 = (C.GREEN if analysis.bias == "BULLISH"
                   else C.RED if analysis.bias == "BEARISH"
                   else C.GRAY)
            print("  " + C.DIM + "  Bias" + C.RESET +
                  "         " + bc2 + C.BOLD + analysis.bias + C.RESET)
            print()

        # ── VALIDATION ────────────────────────────────────────────
        if validation is not None:
            vc = C.GREEN if validation.validated else C.RED
            vt = "VALIDATED" if validation.validated else "REJECTED"
            print("  " + C.DIM + "-" * (w - 4) + C.RESET)
            print("  " + C.BOLD + "VALIDATION  " + C.RESET +
                  vc + C.BOLD + vt + C.RESET)
            passed_str = str(len(validation.filters_passed)) + "/4"
            print("  " + C.DIM + "  Filters" + C.RESET +
                  "      " + C.GREEN + passed_str + C.RESET)
            if validation.filters_failed:
                print("  " + C.DIM + "  Failed" + C.RESET +
                      "       " + C.RED +
                      " | ".join(validation.filters_failed) + C.RESET)
            short_vr = (validation.reason[:w - 6]
                        if len(validation.reason) > w - 6
                        else validation.reason)
            print("  " + C.DIM + "  " + short_vr + C.RESET)
            print()

        # ── INTENT ────────────────────────────────────────────────
        if narrative is not None:
            nc  = NARRATIVE_COLOR.get(narrative.narrative, C.GRAY)
            nic = NARRATIVE_ICON.get(narrative.narrative, "----")
            print("  " + C.DIM + "-" * (w - 4) + C.RESET)
            print("  " + C.BOLD + "INTENT" + C.RESET)
            print()
            print("  " + nc + C.BOLD +
                  "  [" + nic + "] " + narrative.narrative + C.RESET +
                  "  " + C.DIM + str(narrative.conviction) +
                  "%" + C.RESET)
            print("  " + C.DIM + "  Trapped" + C.RESET +
                  "      " + C.WHITE + narrative.trapped_side + C.RESET)
            if narrative.likely_target > 0:
                print("  " + C.DIM + "  Target" + C.RESET +
                      "       " + C.YELLOW +
                      str(round(narrative.likely_target, 2)) + C.RESET)
            if narrative.conviction >= 70:
                if narrative.narrative == "SQUEEZE":
                    print("  " + C.MAGENTA + C.BOLD +
                          "  !! SQUEEZE — BREAKOUT INCOMING" + C.RESET)
                elif narrative.narrative == "INDUCTION":
                    print("  " + C.RED + C.BOLD +
                          "  !! TRAP — FADE THE MOVE" + C.RESET)
            print()

        # ── RISK ENGINE ───────────────────────────────────────────
        if risk_result is not None:
            print("  " + C.DIM + "-" * (w - 4) + C.RESET)
            print("  " + C.BOLD + "RISK ENGINE" + C.RESET)
            print()
            if risk_result.approved:
                print("  " + C.GREEN + C.BOLD +
                      "  APPROVED — " + risk_result.direction + C.RESET)
                print()
                print("  " + C.DIM + "  Size" + C.RESET +
                      "         " + C.WHITE + C.BOLD +
                      str(risk_result.position_size) + "%" + C.RESET)
                print("  " + C.DIM + "  Stop" + C.RESET +
                      "         " + C.RED +
                      str(risk_result.stop) +
                      "  (-" + str(risk_result.risk_pts) + "pts)" + C.RESET)
                print("  " + C.DIM + "  Target 1" + C.RESET +
                      "     " + C.GREEN +
                      str(risk_result.target_1) +
                      "  (+" + str(risk_result.reward_pts) + "pts)" + C.RESET)
                print("  " + C.DIM + "  Target 2" + C.RESET +
                      "     " + C.CYAN +
                      str(risk_result.target_2) + C.RESET)
                rr_c = C.GREEN if risk_result.risk_reward >= 3 else C.YELLOW
                print("  " + C.DIM + "  R:R" + C.RESET +
                      "          " + rr_c + C.BOLD +
                      "1:" + str(risk_result.risk_reward) + C.RESET)
                print()
                print("  " + C.GREEN + C.BOLD +
                      "  EXECUTE " + risk_result.direction +
                      " | " + str(risk_result.position_size) +
                      "% | STOP " + str(risk_result.stop) + C.RESET)
            else:
                print("  " + C.GRAY + "  REJECTED" + C.RESET)
                short_r = (risk_result.reason[:w - 6]
                           if len(risk_result.reason) > w - 6
                           else risk_result.reason)
                print("  " + C.DIM + "  " + short_r + C.RESET)
            print()

        # ── ACTIVE TRADE ──────────────────────────────────────────
        if pending_trade is not None:
            pt = pending_trade
            print("  " + C.DIM + "-" * (w - 4) + C.RESET)
            print("  " + C.BOLD + "ACTIVE TRADE" + C.RESET)
            print()
            dc3 = C.GREEN if pt.direction == "LONG" else C.RED
            print("  " + dc3 + C.BOLD +
                  "  " + pt.direction +
                  " #" + str(pt.trade_id) + C.RESET +
                  "  " + C.DIM + "bar " +
                  str(pt.bars_held) + C.RESET)
            print("  " + C.DIM + "  Stop" + C.RESET +
                  "    " + C.RED + str(pt.stop) + C.RESET)
            print("  " + C.DIM + "  T1" + C.RESET +
                  "      " + C.GREEN + str(pt.target_1) + C.RESET)
            if pt.hit_target_1:
                print("  " + C.GREEN + C.BOLD +
                      "  T1 HIT — move stop to BE" + C.RESET)
            print()

        # ── LAST CLOSED TRADE ─────────────────────────────────────
        if closed_trade is not None:
            ct = closed_trade
            rc = RESULT_COLOR.get(ct.result, C.GRAY)
            print("  " + C.DIM + "-" * (w - 4) + C.RESET)
            print("  " + C.BOLD + "LAST TRADE  " + C.RESET +
                  rc + C.BOLD + ct.result + C.RESET)
            print("  " + C.DIM + "  " +
                  ct.direction + " | " +
                  str(round(ct.pnl_pts, 2)) + "pts | " +
                  str(ct.bars_held) + " bars" +
                  (" | TRAP" if ct.was_trap else "") + C.RESET)
            print()

        # ── FEEDBACK SUMMARY ──────────────────────────────────────
        if feedback is not None and feedback.total_trades > 0:
            print("  " + C.DIM + "-" * (w - 4) + C.RESET)
            print("  " + C.BOLD + "FEEDBACK" + C.RESET)
            print()
            wr_c = (C.GREEN if feedback.win_rate >= 55
                    else C.YELLOW if feedback.win_rate >= 40
                    else C.RED)
            print("  " + C.DIM + "  Win rate" + C.RESET +
                  "     " + wr_c + C.BOLD +
                  str(feedback.win_rate) + "%" + C.RESET +
                  C.DIM + "  (" +
                  str(feedback.wins) + "W " +
                  str(feedback.losses) + "L " +
                  str(feedback.timeouts) + "T)" + C.RESET)
            print("  " + C.DIM + "  Follow-thru" + C.RESET +
                  "   " + C.WHITE +
                  str(feedback.follow_through_rate) + "%" + C.RESET)
            if feedback.traps_detected > 0:
                print("  " + C.RED +
                      "  Traps: " + str(feedback.traps_detected) +
                      C.RESET)
            if feedback.best_zone:
                print("  " + C.DIM + "  Best zone" + C.RESET +
                      "    " + C.CYAN +
                      feedback.best_zone + C.RESET)
            print()

        # ── LAST 5 EVENTS ─────────────────────────────────────────
        print("  " + C.DIM + "LAST " + str(len(self._history)) +
              " EVENTS" + C.RESET)
        for i, entry in enumerate(reversed(list(self._history))):
            ev     = entry["event"]
            t      = entry["time"]
            tick   = entry["tick"]
            icon   = EVENT_ICON.get(ev, ".")
            color  = EVENT_COLOR.get(ev, C.GRAY)
            marker = C.BOLD + ">" + C.RESET if i == 0 else " "
            print("  " + marker + " " + color + icon + " " +
                  ev + C.RESET + C.DIM +
                  " #" + str(tick).zfill(3) +
                  "  " + t + C.RESET)
        print()

        # ── SESSION STATS ─────────────────────────────────────────
        total     = sum(self._event_counts.values()) or 1
        has_stats = any(v > 0 for v in self._event_counts.values())
        if has_stats:
            print("  " + C.DIM + "SESSION STATS" + C.RESET)
            for ev_name, count in self._event_counts.items():
                if count == 0:
                    continue
                pct   = int(count / total * 100)
                color = EVENT_COLOR.get(ev_name, C.GRAY)
                bar   = chr(9608) * (pct // 10) + \
                        chr(9617) * (10 - pct // 10)
                print("  " + color + ev_name + C.RESET +
                      " " + C.DIM + bar + C.RESET +
                      "  " + str(pct) + "%")
            print()

        # ── FOOTER ────────────────────────────────────────────────
        pulse = ["-", "\\", "|", "/"][self._tick % 4]
        print(C.CYAN + line + C.RESET)
        print("  " + C.DIM + pulse + " tick #" +
              str(self._tick).zfill(4) +
              "    Ctrl+C to stop" + C.RESET)
        print(C.CYAN + line + C.RESET)

    @staticmethod
    def _center(text, width, color=""):
        pad = max(0, (width - len(text)) // 2)
        return " " * pad + color + text + C.RESET