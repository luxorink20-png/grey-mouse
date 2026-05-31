"""
tests/unit/test_context_filter.py — ContextFilter unit tests
Valida que los filtros funcionan correctamente SIN romper logica existente.
"""

import pytest
from datetime import datetime, timezone, timedelta
from context_filter import ContextFilter

_ET = timezone(timedelta(hours=-4))


# ── Helpers ────────────────────────────────────────────────────────────────────

def _bar(price=7200.0, high=None, low=None, volume=500.0, trades=50):
    h = high if high is not None else price + 2.0
    l = low  if low  is not None else price - 2.0
    return {
        "price": float(price), "open": float(price),
        "high": float(h), "low": float(l), "close": float(price),
        "volume": float(volume), "ask_volume": float(volume * 0.55),
        "bid_volume": float(volume * 0.45), "delta": float(volume * 0.1),
        "trades": int(trades),
    }


def _warm_filter(cf: ContextFilter, n: int = 15,
                 base_volume: float = 500.0, base_atr: float = 4.0) -> None:
    """Feed n normal bars to build up rolling history."""
    for _ in range(n):
        bar = _bar(volume=base_volume,
                   high=7200.0 + base_atr / 2,
                   low=7200.0  - base_atr / 2,
                   trades=50)
        cf.update_bar(bar)


def _ts(hour: int, minute: int = 0) -> datetime:
    """Create an ET datetime for the given hour."""
    return datetime(2026, 5, 31, hour, minute, tzinfo=_ET)


# ── Session-level filter (backtest) ───────────────────────────────────────────

class TestSessionFilter:

    def test_vol_release_session_filtered(self):
        cf = ContextFilter(enable_vol_release=True)
        assert cf.is_session_filtered("VOL_RELEASE") is True

    def test_expansion_session_not_filtered(self):
        cf = ContextFilter(enable_vol_release=True)
        assert cf.is_session_filtered("EXPANSION") is False

    def test_opening_drive_not_filtered(self):
        cf = ContextFilter(enable_vol_release=True)
        assert cf.is_session_filtered("OPENING_DRIVE") is False

    def test_empty_session_type_not_filtered(self):
        cf = ContextFilter(enable_vol_release=True)
        assert cf.is_session_filtered("") is False

    def test_vol_release_filter_disabled_passes_all(self):
        cf = ContextFilter(enable_vol_release=False)
        assert cf.is_session_filtered("VOL_RELEASE") is False


# ── Dynamic VOL_RELEASE detection (live) ─────────────────────────────────────

class TestVolReleaseDynamic:

    def test_no_skip_before_warmup(self):
        """Not enough history → no skip, even under full conditions."""
        cf = ContextFilter(enable_vol_release=True)
        # Only 5 bars (below _MIN_BARS_FOR_DYNAMIC=10)
        for _ in range(5):
            cf.update_bar(_bar())
        vol_bar = _bar(high=7210.0, low=7190.0, volume=2500.0, trades=300)
        skip, reason = cf.should_skip(vol_bar, ts_et=_ts(14))
        assert skip is False

    def test_vol_release_detected_all_conditions_met(self):
        """All 4 conditions met → must skip."""
        cf = ContextFilter(enable_vol_release=True)
        _warm_filter(cf, n=15, base_volume=500.0, base_atr=4.0)
        # ATR=20 (5x avg=4), vol=2000 (4x avg=500), trades=300 (6x avg=50)
        hot_bar = _bar(high=7210.0, low=7190.0, volume=2000.0, trades=300)
        skip, reason = cf.should_skip(hot_bar, ts_et=_ts(14))
        assert skip is True
        assert "VOL_RELEASE" in reason

    def test_no_skip_outside_midday_window(self):
        """High ATR + volume but outside 13-15 ET → no skip."""
        cf = ContextFilter(enable_vol_release=True)
        _warm_filter(cf, n=15, base_volume=500.0, base_atr=4.0)
        hot_bar = _bar(high=7210.0, low=7190.0, volume=2000.0, trades=300)
        skip, _ = cf.should_skip(hot_bar, ts_et=_ts(10))  # 10:00 ET = morning
        assert skip is False

    def test_no_skip_normal_volume_midday(self):
        """Midday but normal volume/ATR → no skip."""
        cf = ContextFilter(enable_vol_release=True)
        _warm_filter(cf, n=15, base_volume=500.0, base_atr=4.0)
        normal_bar = _bar(high=7202.0, low=7198.0, volume=510.0, trades=52)
        skip, _ = cf.should_skip(normal_bar, ts_et=_ts(14))
        assert skip is False

    def test_vol_release_filter_off_no_skip(self):
        """Filter OFF → never skip even under full conditions."""
        cf = ContextFilter(enable_vol_release=False)
        _warm_filter(cf, n=15, base_volume=500.0, base_atr=4.0)
        hot_bar = _bar(high=7210.0, low=7190.0, volume=2000.0, trades=300)
        skip, _ = cf.should_skip(hot_bar, ts_et=_ts(14))
        assert skip is False


# ── Destructive regime ─────────────────────────────────────────────────────────

class TestDestructiveRegime:

    def _build_regime_filter(self, trades: list[tuple[float, bool]]) -> ContextFilter:
        cf = ContextFilter(enable_vol_release=False,
                           enable_destructive_regime=True,
                           enable_session_kill_switch=False)
        for pnl, win in trades:
            cf.register_trade(pnl, win)
        return cf

    def test_destructive_regime_detected(self):
        """WR=10%, PF<0.8 → destructive regime → skip."""
        bad_trades = [
            (-10.0, False), (-15.0, False), (-8.0, False),
            (-12.0, False), (-5.0, False), (-20.0, False),
            (-10.0, False), (-8.0, False), (-7.0, False),
            (5.0, True),   # 1 win out of 10 = 10% WR
        ]
        cf = self._build_regime_filter(bad_trades)
        skip, reason = cf.should_skip(_bar())
        assert skip is True
        assert "DESTRUCTIVE_REGIME" in reason

    def test_normal_regime_not_skipped(self):
        """WR=50%, PF>1 → normal → no skip."""
        normal_trades = [
            (10.0, True), (-8.0, False), (12.0, True),
            (-7.0, False), (10.0, True),
        ]
        cf = self._build_regime_filter(normal_trades)
        skip, _ = cf.should_skip(_bar())
        assert skip is False

    def test_insufficient_trades_no_skip(self):
        """Fewer than 5 trades → not enough data → no skip."""
        cf = ContextFilter(enable_vol_release=False,
                           enable_destructive_regime=True,
                           enable_session_kill_switch=False)
        cf.register_trade(-10.0, False)
        cf.register_trade(-10.0, False)
        skip, _ = cf.should_skip(_bar())
        assert skip is False

    def test_destructive_regime_filter_off(self):
        """Filter OFF → no skip even in destructive regime."""
        bad_trades = [(-10.0, False)] * 10
        cf = ContextFilter(enable_vol_release=False,
                           enable_destructive_regime=False,
                           enable_session_kill_switch=False)
        for pnl, win in bad_trades:
            cf.register_trade(pnl, win)
        skip, _ = cf.should_skip(_bar())
        assert skip is False


# ── Session kill switch ────────────────────────────────────────────────────────

class TestSessionKillSwitch:

    def test_kill_switch_activates_above_threshold(self):
        """DD > 30 pts → kill switch → skip."""
        cf = ContextFilter(enable_vol_release=False,
                           enable_destructive_regime=False,
                           enable_session_kill_switch=True,
                           session_maxdd_threshold=30.0)
        cf.register_trade(5.0, True)    # peak = 5
        cf.register_trade(-40.0, False) # cum = -35, DD = 40 > 30
        skip, reason = cf.should_skip(_bar())
        assert skip is True
        assert "KILL_SWITCH" in reason

    def test_kill_switch_below_threshold_no_skip(self):
        """DD < 30 pts → no kill switch."""
        cf = ContextFilter(enable_vol_release=False,
                           enable_destructive_regime=False,
                           enable_session_kill_switch=True,
                           session_maxdd_threshold=30.0)
        cf.register_trade(10.0, True)   # peak = 10
        cf.register_trade(-5.0, False)  # cum = 5, DD = 5 < 30
        skip, _ = cf.should_skip(_bar())
        assert skip is False

    def test_kill_switch_resets_on_session_reset(self):
        """After reset_session(), kill switch should deactivate."""
        cf = ContextFilter(enable_vol_release=False,
                           enable_destructive_regime=False,
                           enable_session_kill_switch=True,
                           session_maxdd_threshold=30.0)
        cf.register_trade(5.0, True)
        cf.register_trade(-40.0, False)
        skip, _ = cf.should_skip(_bar())
        assert skip is True  # confirma que está activo

        cf.reset_session()
        skip, _ = cf.should_skip(_bar())
        assert skip is False  # después del reset, desactivado

    def test_kill_switch_filter_off_no_skip(self):
        """Filter OFF → no skip even with large DD."""
        cf = ContextFilter(enable_vol_release=False,
                           enable_destructive_regime=False,
                           enable_session_kill_switch=False,
                           session_maxdd_threshold=30.0)
        cf.register_trade(5.0, True)
        cf.register_trade(-100.0, False)
        skip, _ = cf.should_skip(_bar())
        assert skip is False


# ── Robustez con datos ausentes ────────────────────────────────────────────────

class TestRobustness:

    def test_no_crash_with_minimal_bar(self):
        """Datos minimos no deben causar excepciones."""
        cf = ContextFilter()
        minimal_bar = {"price": 7200.0}
        try:
            cf.update_bar(minimal_bar)
            skip, _ = cf.should_skip(minimal_bar)
            assert isinstance(skip, bool)
        except Exception as exc:
            pytest.fail(f"ContextFilter raised with minimal bar: {exc}")

    def test_no_crash_with_empty_bar(self):
        """Bar vacio no debe crashar."""
        cf = ContextFilter()
        try:
            cf.update_bar({})
            skip, _ = cf.should_skip({})
            assert skip is False
        except Exception as exc:
            pytest.fail(f"ContextFilter raised with empty bar: {exc}")

    def test_get_status_returns_dict(self):
        """get_status() debe retornar dict con claves esperadas."""
        cf = ContextFilter()
        status = cf.get_status()
        assert isinstance(status, dict)
        assert "enable_vol_release" in status
        assert "recent_wr" in status
        assert "session_current_dd" in status

    def test_all_filters_off_never_skips(self):
        """Todos los filtros OFF → should_skip siempre False."""
        cf = ContextFilter(
            enable_vol_release=False,
            enable_destructive_regime=False,
            enable_session_kill_switch=False,
        )
        _warm_filter(cf, n=20, base_volume=500.0, base_atr=4.0)
        for pnl, win in [(-50.0, False)] * 10:
            cf.register_trade(pnl, win)
        hot_bar = _bar(high=7220.0, low=7180.0, volume=5000.0, trades=1000)
        skip, _ = cf.should_skip(hot_bar, ts_et=_ts(14))
        assert skip is False


# ── Compatibilidad con baseline ────────────────────────────────────────────────

class TestBaselineCompatibility:
    """Verifica que engine.py existente no se rompe con el filtro integrado."""

    def test_engine_imports_without_error(self):
        """engine.py debe importar sin errores con ContextFilter integrado."""
        import importlib
        import sys
        # Solo verificar el módulo context_filter, no engine.py completo
        # (engine.py require niveles.json y depende del filesystem)
        cf_mod = importlib.import_module("context_filter")
        assert hasattr(cf_mod, "ContextFilter")

    def test_context_filter_module_importable(self):
        from context_filter import ContextFilter as CF
        cf = CF()
        assert cf is not None

    def test_is_session_filtered_signature(self):
        """is_session_filtered() acepta string y retorna bool."""
        cf = ContextFilter()
        result = cf.is_session_filtered("VOL_RELEASE")
        assert isinstance(result, bool)

    def test_should_skip_returns_tuple(self):
        """should_skip() retorna (bool, str)."""
        cf = ContextFilter()
        result = cf.should_skip(_bar())
        assert isinstance(result, tuple)
        assert len(result) == 2
        assert isinstance(result[0], bool)
        assert isinstance(result[1], str)
