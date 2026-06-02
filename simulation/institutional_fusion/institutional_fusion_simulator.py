"""
INSTITUTIONAL_FUSION_SIMULATOR
Orchestrates all simulation layers against a single trade record.

Input:  trade dict (from real backtest CSV or live trade log)
Output: fusion decision + component breakdown + adjusted P&L
"""
from __future__ import annotations
import sys, os
sys.path.insert(0, os.path.dirname(__file__))

from modules.quality_engine_sim import QualityEngineSim
from modules.orderflow_sim import OrderFlowEngineSim
from modules.smc_sim import SMCEngineSim
from modules.ml_confidence_sim import MLConfidenceEngineSim
from modules.adaptive_risk_sim import AdaptiveRiskEngineSim
from typing import Dict


class InstitutionalFusionSimulator:

    def __init__(self, quality_threshold: int = 60):
        self.quality  = QualityEngineSim(quality_threshold=quality_threshold)
        self.orderflow = OrderFlowEngineSim()
        self.smc       = SMCEngineSim()
        self.ml        = MLConfidenceEngineSim(window=20)
        self.risk      = AdaptiveRiskEngineSim()

        self._equity_baseline = 0.0   # cumulative baseline PnL
        self._equity_fusion   = 0.0   # cumulative fusion PnL
        self._dd_baseline = 0.0
        self._dd_fusion   = 0.0
        self._peak_b = 0.0
        self._peak_f = 0.0
        self._session_trade_count = 0

    # ------------------------------------------------------------------
    def process(self, trade: dict) -> Dict:
        """
        Process one trade through all fusion layers.
        Returns result dict with baseline and fusion decisions + metrics.
        """
        pnl = float(trade.get("pnl_pts", 0) or 0)
        direction = trade.get("direction", "LONG")
        result_baseline = trade.get("result", "TIMEOUT")
        is_win = result_baseline == "WIN"

        # ── QUALITY ────────────────────────────────────────────────────
        q_score, q_breakdown = self.quality.score_from_trade_record(trade)
        quality_pass = self.quality.passes_filter(q_score)

        # ── ORDER FLOW ─────────────────────────────────────────────────
        of_metrics = self.orderflow.score_from_bar(trade)
        of_confluent = self.orderflow.is_confluent(of_metrics, direction)

        # ── SMC ────────────────────────────────────────────────────────
        smc_score, smc_detail = self.smc.analyse_trade(trade)

        # ── ML CONFIDENCE ──────────────────────────────────────────────
        # equity_slope: rolling window approximation
        eq_history_len = max(1, len(self.ml._pnls))
        equity_slope = 0.5 + (self._equity_fusion / max(abs(self._equity_fusion) + 0.01, 100)) * 0.5
        equity_slope = max(0.0, min(1.0, equity_slope))

        conf = self.ml.confidence(q_score, smc_score, equity_slope)

        # ── ADAPTIVE RISK ──────────────────────────────────────────────
        recent_wr = sum(self.ml._outcomes) / max(len(self.ml._outcomes), 1) if self.ml._outcomes else 0.387
        dd_pct = abs(self._dd_fusion) / max(abs(self._equity_fusion) + self._peak_f + 0.01, 1000)
        mult, risk_detail = self.risk.size_multiplier(conf, dd_pct, recent_wr)

        kill = self.risk.kill_switch(dd_pct, self._session_trade_count)

        # ── FUSION DECISION ────────────────────────────────────────────
        # Accept trade if quality passes AND (kill switch allows)
        fusion_accept = quality_pass and kill["allow"]

        # Adjusted P&L: multiply by size multiplier only on accepted trades
        if fusion_accept:
            fusion_pnl = pnl * mult
        else:
            fusion_pnl = 0.0   # Skipped trade

        # ── EQUITY / DRAWDOWN TRACKING ─────────────────────────────────
        self._equity_baseline += pnl
        self._peak_b = max(self._peak_b, self._equity_baseline)
        self._dd_baseline = min(0.0, self._equity_baseline - self._peak_b)

        self._equity_fusion += fusion_pnl
        self._peak_f = max(self._peak_f, self._equity_fusion)
        self._dd_fusion = min(0.0, self._equity_fusion - self._peak_f)

        if fusion_accept:
            self._session_trade_count += 1
            self.ml.record(fusion_pnl, fusion_pnl > 0)

        return {
            "trade_id":       trade.get("trade_id"),
            "direction":      direction,
            "baseline_pnl":   pnl,
            "baseline_result": result_baseline,
            "fusion_accepted": fusion_accept,
            "fusion_pnl":      round(fusion_pnl, 2),
            "quality_score":   q_score,
            "quality_pass":    quality_pass,
            "of_imbalance":    round(of_metrics["imbalance"], 3),
            "of_confluent":    of_confluent,
            "smc_score":       round(smc_score, 3),
            "ml_confidence":   round(conf, 3),
            "ml_label":        self.ml.label(conf),
            "size_multiplier": round(mult, 3),
            "kill_switch":     not kill["allow"],
            "kill_reason":     kill["reason"],
        }

    def reset_session(self) -> None:
        """Call between sessions to reset the daily trade counter."""
        self._session_trade_count = 0
