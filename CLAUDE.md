# GIBBZ Core — Project Intelligence

> Single source of truth for Claude Code, new developers, and the QA/product pipeline.  
> Maintained by: Senior SWE · Senior QA · Product Owner  
> Last updated: 2026-05-30

---

## 0. Non-Negotiable Rules (MANDATORY — every session)

### Rule 1 — Always commit and push
Every session that produces any code change MUST finish with:
```powershell
git add <changed files>
git commit -m "..."
git push
```
No exceptions. A session that modifies code without committing and pushing has left the project in an inconsistent state.

### Rule 2 — Never use mock or seed data
**No `Mock`, `MagicMock`, fake seeds, hardcoded dummy prices, or synthetic fixture data anywhere in production code paths.**
- Tests may use test fixtures (conftest.py `make_bar()`, etc.) but must never substitute fake data for real engine logic.
- Simulations must derive entirely from real recorded data — see `simulation/regime_morpher.py` INVARIANTE.
- If a module needs real data to run and it is not available, it must raise an explicit error — never silently substitute fabricated values.

### Rule 3 — Always update MD files
**Every session that modifies code MUST update the relevant MD files before committing.**

| File | Update when |
|------|-------------|
| `CLAUDE.md` | Architecture changes, new modules, new invariants, completed roadmap items |
| `TODO.md` | Any finding resolved, any new finding discovered |
| `QA_AUDIT_REPORT.md` | Any P0–P3 finding resolved — mark status, add resolution date |
| `README.md` | Setup steps change, new entry points, env-var overrides added |

> **Git:** `https://github.com/luxorink20-png/grey-mouse` · branch `master` · initial commit pushed 2026-05-30

---

## 1. What This Is

**GIBBZ** is an institutional-grade algorithmic trading engine for ES/NQ futures built around Smart Money Concepts (SMC). It processes real-time ATAS tick data through a 15-engine analysis pipeline and emits trade signals with voice alerts, CSV logs, and JSON outcome tracking.

**Not** a web app. **Not** a SaaS. A local Python desktop system tightly coupled to the ATAS charting platform via a C# UDP bridge.

---

## 2. Architecture

```
ATAS Chart (Windows)
    │
    ▼ UDP :9999 (CSV payload, 13 fields)
GibbzBridge.cs  ──────────────────────────────  C# ATAS indicator
    │
    ▼
market_feed.py        MarketFeed — thread-safe UDP receiver
    │
bar_aggregator.py     BarAggregator — 5-second TIME bars
    │
    ▼ dict{price, high, low, volume, delta, ask_volume, bid_volume, ...}
    │
event_engine.py       EventEngine — INTENTO/FALLO/AGOTAMIENTO/ACUMULACIÓN
    │
levels.py             InstitutionalLevels — zone context (AT_VAH, ABOVE_VAH, etc.)
    │
confirmation_engine   breakout quality (FAKE/WEAK/MODERATE/REAL/EXPLOSIVE)
    │
session_regime_engine market regime (TREND_DAY/ROTATIONAL/BALANCED_DAY...)
    │
continuation_engine   post-breakout momentum probability
    │
confluence_engine.py  ConfluenceEngine — multi-factor score 0–100
    │
validator.py          Validator v9.1 — gate filters + dynamic penalties
    │
intent_engine.py      IntentEngine — narrative (INDUCTION/SQUEEZE/REBALANCE...)
    │
risk_engine.py        RiskEngine — sizing, stop, target, R:R
    │
feedback_engine       outcome tracking
learning_engine       edge adaptation
    │
voice_engine.py       VoiceEngine — priority-queue TTS (edge_tts + playsound)
logger.py             GibbzLogger — CSV session log
engine_view.py        EngineView — terminal dashboard
```

### Key Data Schemas

| Object | File | Key Fields |
|--------|------|------------|
| Raw bar (dict) | `tick_schema.py` | price, high, low, volume, delta, ask_volume, bid_volume |
| EventResult (dict) | `event_engine.py` | event, confidence, reason, context{delta,momentum,...} |
| LevelContext | `levels.py` | zone, nearest_level, high_prob_zone, reaction_bias |
| ConfluenceResult | `confluence_engine.py` | score, bias, classification, event |
| ValidationResult | `validator.py` | validated, adjusted_score, filters_passed, filters_failed |
| RiskResult | `risk_engine.py` | approved, position_size, stop, target_1, target_2, risk_reward |

---

## 3. Entry Points

| Script | Purpose | How to run |
|--------|---------|------------|
| `engine.py` | Live trading engine — connects to ATAS via UDP | `python engine.py` |
| `gibbz_launcher.py` | Session recording automation | `python gibbz_launcher.py 2026-05-30 [--mine]` |
| `run_replay.py` | Replay a recorded session | `python run_replay.py` |
| `expansion_session_miner.py` | Post-session analysis | `python expansion_session_miner.py 2026-05-30` |
| `extract_context.py` | Claude Vision → historical_context JSON | `python extract_context.py --watch` |
| `full_backtest.py` | Full backtest harness | `python full_backtest.py` |
| `edge_validation.py` | 9-phase scientific edge validation | `python edge_validation.py` |
| `simulation/replay_treadmill.py` | GO-LIVE readiness score | `python simulation/replay_treadmill.py` |

---

## 4. Configuration

Feature flags and connection config live in `config.py` with environment-variable overrides. Engine-internal thresholds remain in their respective modules.

| Constant | Source | Default | Override via |
|----------|--------|---------|--------------|
| `ENABLE_LOGGING` | `config.py` | `True` | `$env:GIBBZ_ENABLE_LOGGING=0` |
| `OVERRIDE_SESSION` | `config.py` | `True` | `$env:GIBBZ_OVERRIDE_SESSION=0` |
| `USE_REAL_FEED` | `config.py` | `True` | `$env:GIBBZ_USE_REAL_FEED=0` |
| `ENABLE_VOICE` | `config.py` | `True` | `$env:GIBBZ_ENABLE_VOICE=0` |
| `UDP_HOST` | `config.py` | `127.0.0.1` | `$env:GIBBZ_UDP_HOST` |
| `UDP_PORT` | `config.py` | `9999` | `$env:GIBBZ_UDP_PORT` |
| `MIN_SCORE_TO_TRADE` | `validator.py` | `45` | Edit in-class |
| `MIN_RR` | `risk_engine.py` | `1.5` | Edit in-class |
| `MAX_RISK_PTS` | `risk_engine.py` | `20.0` | Edit in-class |

---

## 5. Dependencies

Install via:

```powershell
pip install -r requirements.txt
```

`requirements.txt` exists with pinned versions (generated 2026-05-30). Key packages:

| Package | Used In |
|---------|---------|
| `anthropic` | `extract_context.py` |
| `selenium` + `webdriver-manager` | `spotgamma_scraper.py` |
| `edge-tts` + `playsound` | `voice_engine.py` |
| `keyring` | `spotgamma_scraper.py` — OS credential store |
| `pywinauto` | `gibbz_launcher.py` |
| `pytest` + `pytest-cov` | Test suite |
| `codegraph` | Dependency graph (`codegraph.html`) |

---

## 6. Logging

Structured rotating log via `log_config.py`:

```
logs/gibbz.log   — 5 MB × 3 backups, UTF-8
```

- **Console**: WARNING and above only (keeps terminal clean)
- **File**: DEBUG and above (full trace for post-session review)
- **Level override**: `$env:GIBBZ_LOG_LEVEL=DEBUG`

Use in any module:
```python
from log_config import get_logger
_log = get_logger(__name__)
_log.error("something failed: %s", e)
```

---

## 7. Test Suite

```
tests/
├── conftest.py            — shared fixtures (make_bar, warmed_engine, mock engines)
├── unit/
│   ├── test_event_engine.py   (22 tests)
│   ├── test_risk_engine.py    (21 tests)
│   ├── test_levels.py         (21 tests)
│   ├── test_validator.py      (23 tests)
│   ├── test_bar_aggregator.py (15 tests)
│   └── test_state.py          (7 tests)
├── integration/
│   └── test_pipeline.py       (15 tests — full event→risk chain)
└── e2e/
    └── test_replay_pipeline.py (15 tests — UDP parse + session replay)
```

**Status: 140/140 passing** (2026-05-30)

```powershell
pytest                               # all 140
pytest --cov=. --cov-report=term-missing   # with coverage
pytest tests/unit/                   # unit only
```

---

## 8. QA Audit Status — ALL RESOLVED ✅

Full report: `QA_AUDIT_REPORT.md`

### P0 — Critical ✅ (all resolved 2026-05-30)
- **[S1]** ✅ `.sg_config` deleted — credentials migrated to Windows Credential Manager (`keyring`)
- **[S2]** ✅ `spotgamma_scraper.py` — `save/load_credentials` now use `keyring.set/get_password`
- **[S3]** ✅ `.gitignore` created — covers credentials, logs, recordings, audio, temp files
- **[S4]** ✅ `extract_context.py` — raises `RuntimeError` with clear message if API key missing

### P1 — High ✅ (all resolved 2026-05-30)
- **[H1]** ✅ `requirements.txt` generated with pinned versions
- **[H2]** ✅ Silent `except Exception: pass` replaced with logged errors in critical paths
- **[H3]** ✅ `gibbz_launcher.py` `write_cmd` — atomic write via `shutil.move`
- **[H4]** ✅ `spotgamma_scraper.py` — `save/load_levels_cache` added; cache loaded on scrape failure
- **[H5]** ✅ `market_feed._parse()` — ≥13 field check, `ValueError`/`IndexError` only

### P2 — Medium ✅ (all resolved 2026-05-30)
- **[M1]** ✅ `config.py` created — feature flags + UDP config, env-var overrides; `engine.py` imports it
- **[M2]** ✅ `log_config.py` created — rotating logger; `market_feed` + `voice_engine` use it
- **[M3]** ✅ Debug block removed from `replay_feed.py`
- **[M4]** ✅ `os.system("cls")` → ANSI escape in `engine_view.py` and `spotgamma_scraper.py`
- **[M5]** ✅ `voice_engine._loop` — `try/except` guard + `_log.error` + state reset
- **[M6]** ✅ `regime_morpher.py:12` — `TODO` → `INVARIANTE`

### P3 — Low ✅ (all resolved 2026-05-30)
- **[L1]** ✅ `README.md` written
- **[L2]** ✅ `pytest.ini` + 140/140 tests passing
- **[L3]** ✅ English comments in `bar_aggregator`, `event_engine`, `confluence_engine`, `levels`
- **[L4]** ✅ `tmp/` created; 7 scratch files moved; covered by `.gitignore`
- **[L5]** ✅ Type hints + `mypy.ini` — 0 errors on 6 core pipeline files

---

## 9. Coding Conventions

- **Language:** Python 3.10+ (uses `str | None` union syntax)
- **Style:** No formatter enforced — roughly PEP 8 with aligned assignments
- **Comments:** Spanish domain terms (INTENTO, AGOTAMIENTO) are intentional trading vocabulary, not style inconsistency. All new comments should be in English.
- **Dataclasses:** Use `@dataclass` for all result objects — no plain dicts for return types on new code
- **No globals** for mutable state — use class instances
- **Thread safety:** Shared mutable state must use `threading.Lock()` — see `MarketFeed` as canonical reference
- **Error handling:** Never use `except Exception: pass`. Minimum: `except Exception as e: _log.error(...)`. For critical paths: re-raise
- **Logging:** Use `get_logger(__name__)` from `log_config.py` — no bare `print("[ERROR]")`
- **Config:** Feature flags go in `config.py`; engine thresholds stay in their class constants
- **Tests:** One test file per module. Fixtures in `conftest.py`. No voice/audio in unit tests — mock `VoiceEngine`. Never substitute mock/seed data for real engine logic in production paths (see Rule 2 in Section 0)
- **Type hints:** All new code in the core pipeline must be fully typed. Run `python -m mypy <file>` before committing — `mypy.ini` enforces strict checking on `bar_aggregator`, `event_engine`, `levels`, `market_feed`, `config`, `log_config`
- **MD files:** Update all relevant `.md` files every session that modifies code (see Section 0)

---

## 10. Product Roadmap

### Sprint 1 — Security & Stability (P0 + P1) — COMPLETE ✅
| ID | Task | Status |
|----|------|--------|
| S1/S2 | `keyring` credential storage | ✅ done |
| S3 | `.gitignore` | ✅ done |
| S4 | API key validation at startup | ✅ done |
| H1 | `requirements.txt` pinned | ✅ done |
| H2 | Replace silent `except pass` | ✅ done |
| H3 | Atomic IPC writes | ✅ done |
| H4 | SpotGamma cache fallback | ✅ done |
| H5 | UDP data validation | ✅ done |

### Sprint 2 — Observability & Quality (P2) — COMPLETE ✅
| ID | Task | Status |
|----|------|--------|
| M1 | `config.py` with env-var overrides | ✅ done |
| M2 | `log_config.py` rotating logger | ✅ done |
| M3 | Remove debug blocks | ✅ done |
| M4 | ANSI terminal clear | ✅ done |
| M5 | Voice loop exception guard | ✅ done |
| M6 | Clarify regime_morpher TODO | ✅ done |

### Sprint 3 — Test Coverage (L2) — COMPLETE ✅
| Task | Status |
|------|--------|
| Unit: EventEngine, RiskEngine, Levels, Validator, BarAggregator, State | ✅ 109 tests |
| Integration: full pipeline chain | ✅ 15 tests |
| E2E: replay from JSONL + UDP parse | ✅ 15 tests |
| `pytest.ini` + `pytest-cov` | ✅ done |

### Sprint 4 — Documentation & Developer Experience — COMPLETE ✅
| Task | Status |
|------|--------|
| `README.md` | ✅ done |
| Standardize comments to English (core pipeline) | ✅ done — 4 files |
| Move temp files to `tmp/` | ✅ done |
| `mypy.ini` — strict checking on core pipeline | ✅ done — 0 errors |

### Sprint 5 — Architecture Improvements — NOT STARTED
| Task | Benefit |
|------|---------|
| Consolidate IPC to local TCP socket | Eliminates race conditions permanently |
| Add Redis/SQLite for session state | Replaces 6+ JSON file formats |
| Move from Selenium to SpotGamma API | Eliminate HTML fragility |
| Containerise with Docker (optional) | Any-machine deployment |

### Sprint 6 — Edge Validation — COMPLETE ✅ (2026-05-31)
| Task | Status |
|------|--------|
| `edge_validation.py` — 9-phase scientific edge validation | ✅ done |
| Direct import of `run_session`/`run_backtest` (no subprocess) | ✅ done |
| UTF-8 stdout reconfiguration for Windows cp1252 terminals | ✅ done |
| Decision logic fix: NO only when exp<=0 or PF<1.0 | ✅ done |
| **Result**: WR=38.7%, PF=1.56, Exp=+2.61 pts/trade, Verdict=INCONCLUSO | ✅ done |

---

## 11. Key Invariants (Never Break These)

1. `VAL < POC < VAH` — enforced in `levels.py:54`. Violating crashes `InstitutionalLevels.__init__`.
2. `MIN_SCORE_TO_TRADE = 45` — changing this shifts all backtest results. Update `QA_AUDIT_REPORT.md` if changed.
3. `MIN_RR = 1.5` — minimum risk-reward. All position approval logic depends on this.
4. `MarketFeed._latest` must only be read/written inside `self._lock`.
5. `VoiceEngine.say()` is non-blocking — never call `say_blocking()` from the main engine loop.
6. Engine pipeline order in `engine.py` is load-bearing: `event → levels → confluence → validator → intent → risk`. Do not reorder without running the full test suite.
7. `config.py` is the single source of truth for feature flags and connection config — do not re-declare these constants in other modules.

---

## 12. Git & Version Control

> **Repository:** `https://github.com/luxorink20-png/grey-mouse`  
> **Branch:** `master` → tracking `origin/master`. Initial commit pushed 2026-05-30.

```powershell
git log --oneline          # view commits
git status                 # check working tree
```

Files excluded from tracking: see `.gitignore` (credentials, logs, recordings, data dirs, temp files).
