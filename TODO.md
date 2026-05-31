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
