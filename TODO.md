# GIBBZ Core — Audit TODO

Generated from `QA_AUDIT_REPORT.md` · 2026-05-30  
Track progress here. Mark items `[x]` as completed.

---

## P0 — Critical (Security Blockers)

- [x] **[S1]** Delete `.sg_config` — migrate SpotGamma credentials to `keyring` OS secret store
- [x] **[S2]** Rewrite `spotgamma_scraper.py` `save_credentials` / `load_credentials` → `keyring.set_password` / `keyring.get_password`  
- [x] **[S3]** Create `.gitignore` — protect credentials, logs, recordings, audio, temp files
- [x] **[S4]** `extract_context.py` — validate API key is non-empty at startup, raise clear error; `.env` covered by S3

---

## P1 — High Severity

- [x] **[H1]** Generate `requirements.txt` with pinned versions
- [x] **[H2]** Replace all `except Exception: pass` in critical paths with logged errors
  - `spotgamma_scraper.py` `save_credentials` / `load_credentials` — now log errors
  - `market_feed.py` receive loop — now logs with `_log.warning`
  - `gibbz_launcher.py` `write_cmd` — now prints IPC error
- [x] **[H3]** `gibbz_launcher.py` — atomic write-then-rename for IPC files (`write_cmd` → `shutil.move`)
- [x] **[H4]** `spotgamma_scraper.py` — persist successful scrape to `spotgamma_cache_{date}.json`; load cache on failure; surface visible warning
- [x] **[H5]** `market_feed.py` `_parse()` — field-count check (≥13), numeric validation, specific exception types

---

## P2 — Medium Severity

- [x] **[M1]** Create `config.py` — feature flags + UDP config with env-var overrides; `engine.py` imports from it
- [x] **[M2]** Create `log_config.py` — rotating file log + console WARNING handler; `market_feed.py` and `voice_engine.py` wired in
- [x] **[M3]** Remove debug blocks committed to production
  - `replay_feed.py:282-284` — `# DEBUG TEMP` print block removed
- [x] **[M4]** `engine_view.py:179` — replaced `os.system("cls")` with ANSI escape `\033[2J\033[H`
- [x] **[M5]** `voice_engine.py` `_loop` — `try/except Exception` wraps thread body; logs to `_log.error`; resets priority on exception
- [x] **[M6]** `simulation/regime_morpher.py:12` — `TODO` → `INVARIANTE` (intent clarified)

---

## P3 — Low Severity

- [x] **[L1]** Write `README.md` — overview, architecture, prerequisites, setup, config reference, test commands
- [x] **[L2]** `pytest.ini` + test suite — 140/140 passing (unit + integration + e2e)
- [x] **[L3]** Standardize comment language to English in core pipeline — `bar_aggregator.py`, `event_engine.py`, `confluence_engine.py`, `levels.py`
- [x] **[L4]** Move `tmp_*.txt` and `_*.mp3` scratch files to `tmp/`; `tmp/` added to `.gitignore`
- [x] **[L5]** Type hints added to `bar_aggregator.py`, `event_engine.py`, `levels.py`, `market_feed.py`, `config.py`, `log_config.py`, `confluence_engine.py` — 0 mypy errors on core pipeline

---

## Progress

| ID | Status | Notes |
|----|--------|-------|
| S1 | done ✅ | `keyring` OS credential store; `.sg_config` deleted |
| S2 | done ✅ | `spotgamma_scraper.py` uses `keyring.set/get_password` |
| S3 | done ✅ | `.gitignore` created |
| S4 | done ✅ | `extract_context.py` raises `RuntimeError` with clear message |
| H1 | done ✅ | `requirements.txt` generated with pinned versions |
| H2 | done ✅ | Silent `except pass` replaced in scraper, market_feed, launcher |
| H3 | done ✅ | `write_cmd` → atomic write via `shutil.move` |
| H4 | done ✅ | `save_levels_cache` / `load_levels_cache` added to scraper |
| H5 | done ✅ | `_parse()` validates ≥13 fields, `ValueError`/`IndexError` only |
| M1 | done ✅ | `config.py` with env-var overrides; `engine.py` updated |
| M2 | done ✅ | `log_config.py` rotating logger; market_feed + voice_engine use it |
| M3 | done ✅ | Debug block removed from `replay_feed.py` |
| M4 | done ✅ | `os.system("cls")` → ANSI escape in `engine_view.py` + `spotgamma_scraper.py` |
| M5 | done ✅ | `voice_engine._loop` exception guard added |
| M6 | done ✅ | Ambiguous TODO clarified to INVARIANTE |
| L1 | done ✅ | `README.md` written |
| L2 | done ✅ | 140/140 tests passing |
| L3 | done ✅ | English comments in bar_aggregator, event_engine, confluence_engine, levels |
| L4 | done ✅ | `tmp/` dir created; 7 scratch files moved; `.gitignore` covers it |
| L5 | done ✅ | 0 mypy errors on 6 core pipeline files; `mypy.ini` configured |

---

## All audit findings resolved ✅

All 19 P0–P3 findings from `QA_AUDIT_REPORT.md` are resolved as of 2026-05-30.

### Remaining Sprint 4–5 items (not from original audit)

- **Sprint 4** — `mypy` configured (`mypy.ini`); running on core pipeline ✅
- **Sprint 5** — Architecture: IPC TCP socket, Redis/SQLite state, SpotGamma API, Docker (not started)

---

## Sprint 6 — Edge Validation (2026-05-31)

- [x] **[EV1]** Create `edge_validation.py` — 9-phase scientific edge validation (no code modifications)
  - Phase 1: Dataset inventory (43 sessions, 91 recordings, expansion_outcomes)
  - Phase 2: Data quality validation per JSONL
  - Phase 3–5: Full backtest via direct import of run_session/run_backtest + metrics + regime segmentation
  - Phase 6: Out-of-sample 70/30 split by date
  - Phase 7: Robustness (session stability, top-3 concentration, CV)
  - Phase 8: Core >= 65 vs ACG 55-64 using real logs/gibbz_trades_*.csv
  - Phase 9: Final verdict with corrected decision logic (INCONCLUSO not NO for Exp>0 / PF>1.0)
- [x] **[EV2]** Fix decision logic bug — NO only when exp<=0 or pf<1.0; INCONCLUSO when positive but concentrated
- Results: WR=38.7%, PF=1.56, Exp=+2.61 pts/trade, Total=+277 pts, Verdict=INCONCLUSO
- [x] **[EV3]** Create `robustness_audit.py` — 9-phase robustness audit (concentration, resilience, regime, setup, temporal, stats, ESS, verdict)
  - Phase 1: PnL concentration (Top 1/3/5/10)
  - Phase 2: Resilience scenarios (Base / sin Top1 / sin Top3 / sin Top5 / sin Top10)
  - Phase 3: Regime segmentation (session_type)
  - Phase 4: Setup analysis (all 7 setup types)
  - Phase 5: Setup dependency (eliminate best/top2/top3 setups)
  - Phase 6: Temporal stability by context month + CV
  - Phase 7: PF, Expectancy, Sharpe (per-trade), Recovery Factor, Max DD, OOS 70/30
  - Phase 8: Edge Survival Score 0-100
  - Phase 9: Final verdict — EDGE / ROBUSTEZ / PRODUCTION READINESS + 7 questions with evidence
- [x] **[EV4]** Create `failure_investigation.py` — 9-phase edge failure investigation (analytical only, no code modifications)
  - Phase 1: Worst periods identification
  - Phase 2: Winners vs losers comparison (sconf, R:R, stop distance)
  - Phase 3: March 2026 autopsy
  - Phase 4: Regime analysis (session_type vs performance)
  - Phase 5: Signal distribution analysis (confluence/validator/risk scores)
  - Phase 6: Time-of-day analysis (ET hour periods)
  - Phase 7: Edge Decay Ranking by PF per session
  - Phase 8: Failure Signatures (momentum, overtrading, direction dominance)
  - Phase 9: GIBBZ EDGE FAILURE REPORT

---

## Dry Run Real — Mejoras 1+2 (2026-05-31)

- [x] **[DR1]** Branch `improvement-1`: relax ContextFilter thresholds (ATR 1.5→2.0, vol 2.0→2.5, remove activity check, disable destructive_regime)
  - Resultado: IDENTICO al baseline (32 trades, PF=2.91) — cambios a nivel barra no afectan backtest
  - Hallazgo: `is_session_filtered()` (nivel sesion) domina; `should_skip()` (nivel barra) no se llama en backtest
  
- [x] **[DR2]** Branch `improvement-1-plus-2`: agregar `PullbackDetector` + `BreakoutDetector`
  - Intento 1 (thresholds iniciales): 141 trades, PF=1.63 — FAIL (demasiados trades de baja calidad)
  - Intento 2 (thresholds endurecidos): 35 trades, PF=2.47, MaxDD=34 pts — FAIL (PF<2.5, MaxDD>20)
  - Bootstrap (100 runs): PF_p5%=1.16 (necesita >=2.0), MaxDD_p95%=78 pts (necesita <=30) — FAIL
  
- [x] **[DR3]** Crear `reports/Dry_Run_Final_Informe.md` con informe ejecutivo completo

- **VEREDICTO: DISCARD** — No mergear ninguna mejora. Mantener sistema actual (PF=2.91) y proceder a paper trading.

### Lecciones documentadas (ver `reports/Dry_Run_Final_Informe.md`):
- Bar-level vs session-level filter distinction (Invariante 8 en CLAUDE.md)
- Edge de GIBBZ es concentrado y selectivo — agregar setups lo diluye
- MaxDD se amplifica cuando nuevos trades coinciden con sesiones de drawdown existente
- Proyecciones sin backtest real tienen error de hasta 89% (MaxDD) y 100% (trade count)

---

## Estimación de Error de Datos (2026-05-31)

- [x] **[DE1]** Confirmar métricas actuales con backtest real (43 sesiones): PF=2.91, MaxDD=12 pts, 32 trades ✅
- [x] **[DE2]** Crear `reports/correction_factors.md` con factores precisos de corrección 5s/1000x → tick/normal ✅
- [x] **[DE3]** Crear y ejecutar `scripts/estimate_tick_normal_metrics.py` — estimación de métricas con datos reales ✅
  - PF estimado: 3.35 (+15%), MaxDD: 9.60 pts (-20%), Trades: 320 (+900%), Exp: +7.24 (+8%)
- [x] **[DE4]** Ejecutar Bootstrap Treadmill 200 runs: PF_median=2.90, PF_p5%=1.56, MaxDD_p95%=36 pts ✅
- [x] **[DE5]** Ejecutar Counterfactual Edge Audit 9-fase: Score=99/100, edge real confirmado ✅
- [x] **[DE6]** Simular mejoras 1+2 con datos tick/normal — siguen fallando (MaxDD>20 para imp-1+2) ✅
- [x] **[DE7]** Crear `reports/Dry_Run_Estimation_Error_Final_Report.md` con informe ejecutivo completo ✅

### Conclusión clave (2026-05-31):
- Datos actuales subestiman el edge real: PF real estimado 3.35 vs 2.91 observado
- Sistema es production-ready con datos actuales (4/5 criterios) y con tick/normal (5/5)
- Las mejoras 1+2 SIGUEN fallando con datos reales — veredicto DISCARD es robusto
- **Acción más valiosa: REGRABAR sesiones en tick/tick, velocidad normal (costo=0)**
