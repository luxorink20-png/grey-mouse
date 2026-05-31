"""
context_filter.py — GIBBZ Context Filter
Filtra contextos de mercado no rentables SIN modificar la logica de setups.

Principio: cada umbral usa definiciones objetivas de mercado, no parametros
optimizados. Todos los filtros son reversibles via toggle on/off.

Integracion:
  engine.py      — filtro live (bar-a-bar, deteccion dinamica)
  full_backtest.py — filtro backtest (nivel sesion, metadata-based)

Uso tipico:
    cf = ContextFilter()
    cf.update_bar(raw)                   # cada barra
    skip, reason = cf.should_skip(raw)   # antes de entrar al trade
    cf.register_trade(pnl, win)          # cuando cierra un trade
    cf.reset_session()                   # al inicio de cada sesion
"""

from __future__ import annotations

import math
from collections import deque
from datetime import datetime, timezone, timedelta
from typing import Optional

from log_config import get_logger

_log = get_logger(__name__)

# ── Constantes de mercado (no optimizadas) ─────────────────────────────────
_ET           = timezone(timedelta(hours=-4))   # EDT (UTC-4)
_MEDIODIA_START = 13    # 13:00 ET — inicio franja mediodía destructiva
_MEDIODIA_END   = 15    # 15:00 ET — fin franja mediodía

_ATR_RATIO_THRESHOLD    = 2.0   # ATR actual > 2.0x media → alta volatilidad (moderado, improvement-1)
_VOLUME_RATIO_THRESHOLD = 2.5   # volumen actual > 2.5x media → alta actividad (moderado, improvement-1)
# _ACTIVITY_RATIO_THRESHOLD removed — improvement-1 uses only 2 checks (ATR + volume)
_MIN_BARS_FOR_DYNAMIC   = 10    # barras mínimas antes de activar filtro dinámico

_DEFAULT_MAXDD_THRESHOLD = 30.0 # MaxDD de sesión antes de kill switch (pts)
_REGIME_MIN_TRADES       = 5    # trades mínimos para detectar régimen destructivo
_REGIME_WR_THRESHOLD     = 0.25 # WR < 25% → posible régimen destructivo
_REGIME_PF_THRESHOLD     = 0.80 # PF < 0.80 → régimen destructivo confirmado

# Session types identificados como destructivos en edge_contribution_audit.py
_DESTRUCTIVE_SESSION_TYPES = frozenset({"VOL_RELEASE"})


class ContextFilter:
    """
    Filtro de contexto de mercado para GIBBZ.

    Tres filtros independientes, todos reversibles:
      1. VOL_RELEASE  — sesiones/contextos de alta varianza con PF marginal
      2. Destructive regime — WR reciente <25% y PF reciente <0.8
      3. Session kill switch — MaxDD de sesión supera umbral configurado

    Uso en backtest: llamar is_session_filtered(session_type) al inicio
    de cada sesion para descartar sesiones completas de tipo VOL_RELEASE.

    Uso en live: llamar update_bar(raw) cada barra, should_skip(raw) antes
    de cada trade, register_trade() al cierre de cada trade.
    """

    def __init__(
        self,
        *,
        enable_vol_release: bool = True,
        enable_destructive_regime: bool = False,   # improvement-1: disabled (too aggressive, eliminates borderline trades)
        enable_session_kill_switch: bool = True,
        session_maxdd_threshold: float = _DEFAULT_MAXDD_THRESHOLD,
        atr_window: int = 20,
        vol_window: int = 20,
    ) -> None:
        self.enable_vol_release         = enable_vol_release
        self.enable_destructive_regime  = enable_destructive_regime
        self.enable_session_kill_switch = enable_session_kill_switch
        self.session_maxdd_threshold    = session_maxdd_threshold

        self._atr_history  = deque(maxlen=atr_window)
        self._vol_history  = deque(maxlen=vol_window)
        self._act_history  = deque(maxlen=vol_window)  # bar-level trades count

        # Regime tracking
        self._recent_trades: deque[tuple[float, bool]] = deque(maxlen=10)

        # Session-level DD tracking
        self._session_cumulative: float = 0.0
        self._session_peak:       float = 0.0

        _log.info(
            "ContextFilter initialized | "
            "vol_release=%s destructive_regime=%s kill_switch=%s maxdd=%.1f",
            enable_vol_release, enable_destructive_regime,
            enable_session_kill_switch, session_maxdd_threshold,
        )

    # ── Public API ─────────────────────────────────────────────────────────

    def update_bar(self, bar: dict) -> None:
        """
        Procesa una barra para mantener estadisticas rolling actualizadas.
        Debe llamarse una vez por barra, antes de should_skip().
        """
        high = bar.get("high", bar.get("price", 0.0))
        low  = bar.get("low",  bar.get("price", 0.0))
        atr  = max(high - low, 0.0)
        vol  = bar.get("volume", 0.0)
        act  = float(bar.get("trades", 0))

        if atr > 0:
            self._atr_history.append(atr)
        if vol > 0:
            self._vol_history.append(vol)
        if act >= 0:
            self._act_history.append(act)

    def register_trade(self, pnl: float, win: bool) -> None:
        """
        Registra el resultado de un trade cerrado.
        Actualiza el tracking de regimen destructivo y kill switch de sesion.
        """
        self._recent_trades.append((pnl, win))
        self._session_cumulative += pnl
        if self._session_cumulative > self._session_peak:
            self._session_peak = self._session_cumulative

    def reset_session(self) -> None:
        """
        Reinicia el estado de nivel-sesion.
        Llamar al inicio de cada nueva sesion de trading.
        """
        self._session_cumulative = 0.0
        self._session_peak       = 0.0
        _log.info("ContextFilter: session state reset")

    def is_session_filtered(self, session_type: str) -> bool:
        """
        Retorna True si la sesion completa debe ser ignorada (backtest).
        Usa metadata de sesion, no deteccion dinamica.
        """
        if not self.enable_vol_release:
            return False
        return session_type in _DESTRUCTIVE_SESSION_TYPES

    def should_skip(
        self,
        bar: dict,
        *,
        ts_et: Optional[datetime] = None,
    ) -> tuple[bool, str]:
        """
        Determina si debe SALTEAR el trade (no entrar).

        Args:
            bar:   dict de barra actual (price, high, low, volume, trades, ...)
            ts_et: timestamp en hora ET (opcional; si None se usa datetime.now(ET))

        Returns:
            (True, reason_str)  → SALTEAR
            (False, "")         → Puede operar normalmente
        """
        # Filtro 1: VOL_RELEASE dinamico
        if self.enable_vol_release:
            result = self._check_vol_release_dynamic(bar, ts_et)
            if result:
                _log.warning("CONTEXT SKIP | VOL_RELEASE | %s", result)
                return True, f"VOL_RELEASE:{result}"

        # Filtro 2: Regimen destructivo
        if self.enable_destructive_regime:
            result = self._check_destructive_regime()
            if result:
                _log.warning("CONTEXT SKIP | DESTRUCTIVE_REGIME | %s", result)
                return True, f"DESTRUCTIVE_REGIME:{result}"

        # Filtro 3: Session kill switch
        if self.enable_session_kill_switch:
            result = self._check_kill_switch()
            if result:
                _log.warning("CONTEXT SKIP | KILL_SWITCH | %s", result)
                return True, f"KILL_SWITCH:{result}"

        return False, ""

    def get_status(self) -> dict:
        """Estado actual de todos los filtros (para dashboard/logging)."""
        wr, pf = self._calc_recent_metrics()
        dd     = self._session_peak - self._session_cumulative
        return {
            "enable_vol_release":         self.enable_vol_release,
            "enable_destructive_regime":  self.enable_destructive_regime,
            "enable_session_kill_switch": self.enable_session_kill_switch,
            "session_maxdd_threshold":    self.session_maxdd_threshold,
            "bars_in_atr_history":        len(self._atr_history),
            "bars_in_vol_history":        len(self._vol_history),
            "recent_trades_n":            len(self._recent_trades),
            "recent_wr":                  round(wr, 3),
            "recent_pf":                  round(pf, 3),
            "session_cumulative_pnl":     round(self._session_cumulative, 2),
            "session_current_dd":         round(dd, 2),
        }

    # ── Private checks ─────────────────────────────────────────────────────

    def _check_vol_release_dynamic(
        self,
        bar: dict,
        ts_et: Optional[datetime],
    ) -> Optional[str]:
        """
        Detecta VOL_RELEASE usando criterios objetivos de mercado (live).

        Requiere TODOS los criterios para activarse:
          - Hora 13:00-15:00 ET (franja mediodía)
          - ATR actual > 1.5x media rolling
          - Volumen actual > 2.0x media rolling
          - Actividad (trades/barra) > 3.0x media rolling

        No activa si el historico rolling es insuficiente (< _MIN_BARS_FOR_DYNAMIC).
        """
        if len(self._atr_history) < _MIN_BARS_FOR_DYNAMIC:
            return None

        # Check 1: hora ET
        if ts_et is None:
            ts_et = datetime.now(tz=_ET)
        hour_et = ts_et.hour
        is_midday = _MEDIODIA_START <= hour_et < _MEDIODIA_END

        if not is_midday:
            return None

        # Check 2: ATR
        high    = bar.get("high", bar.get("price", 0.0))
        low     = bar.get("low",  bar.get("price", 0.0))
        cur_atr = max(high - low, 0.0)
        avg_atr = sum(self._atr_history) / len(self._atr_history)
        high_volatility = avg_atr > 0 and cur_atr > avg_atr * _ATR_RATIO_THRESHOLD

        # Check 3: volumen
        cur_vol = bar.get("volume", 0.0)
        avg_vol = sum(self._vol_history) / max(len(self._vol_history), 1)
        high_volume = avg_vol > 0 and cur_vol > avg_vol * _VOLUME_RATIO_THRESHOLD

        # improvement-1: only 2 checks needed (ATR + volume); activity check removed
        if is_midday and high_volatility and high_volume:
            return (
                f"hour={hour_et}ET "
                f"ATR={cur_atr:.2f}/{avg_atr:.2f}({cur_atr/max(avg_atr,0.01):.1f}x) "
                f"vol={cur_vol:.0f}/{avg_vol:.0f}({cur_vol/max(avg_vol,0.01):.1f}x)"
            )
        return None

    def _check_destructive_regime(self) -> Optional[str]:
        """
        Detecta regimen destructivo: WR reciente <25% Y PF reciente <0.8.
        Requiere minimo _REGIME_MIN_TRADES trades recientes.
        """
        if len(self._recent_trades) < _REGIME_MIN_TRADES:
            return None

        wr, pf = self._calc_recent_metrics()
        if wr < _REGIME_WR_THRESHOLD and pf < _REGIME_PF_THRESHOLD:
            return f"WR={wr:.1%} PF={pf:.2f} n={len(self._recent_trades)}"
        return None

    def _check_kill_switch(self) -> Optional[str]:
        """
        Kill switch: DD de sesion supera el umbral configurado.
        DD = session_peak - session_cumulative_pnl.
        """
        dd = self._session_peak - self._session_cumulative
        if dd > self.session_maxdd_threshold:
            return (
                f"session_dd={dd:.2f}pts "
                f"threshold={self.session_maxdd_threshold:.2f}pts "
                f"peak={self._session_peak:.2f} cum={self._session_cumulative:.2f}"
            )
        return None

    def _calc_recent_metrics(self) -> tuple[float, float]:
        """Calcula WR y PF de los trades recientes registrados."""
        if not self._recent_trades:
            return 0.0, 0.0
        wins   = [pnl for pnl, win in self._recent_trades if win]
        losses = [pnl for pnl, win in self._recent_trades if not win]
        wr     = len(wins) / len(self._recent_trades)
        gw     = sum(wins)
        gl     = abs(sum(losses))
        pf     = gw / gl if gl > 0 else (math.inf if gw > 0 else 0.0)
        return wr, pf
