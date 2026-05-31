# GIBBZ — Institutional SMC Trading Engine

> **Git status:** Local repository only — no remote configured.  
> Do not push or add a remote without explicit confirmation.

Real-time algorithmic trading engine for ES/NQ futures using Smart Money Concepts (SMC). Processes live tick data from ATAS via a C# UDP bridge through a 15-engine Python analysis pipeline, emitting trade signals with voice alerts and structured logs.

---

## Prerequisites

- **Python 3.12+** (tested on 3.14)
- **ATAS** charting platform (Windows) with `GibbzBridge.cs` loaded as an indicator
- Google Chrome (for SpotGamma scraper, optional)

Install dependencies:

```powershell
pip install -r requirements.txt
```

---

## Setup

### 1. Configure levels

Edit `levels.json` with today's volume profile and SpotGamma levels, or run the scraper:

```powershell
python spotgamma_scraper.py
```

Credentials are stored in the OS keyring (Windows Credential Manager) — no plaintext files.

### 2. Set API key (optional — for AI context extraction)

```powershell
$env:ANTHROPIC_API_KEY = "sk-ant-..."
```

Or add `ANTHROPIC_API_KEY=<key>` to a `.env` file in the project root.

### 3. Run the engine

```powershell
# Live mode (requires ATAS + GibbzBridge.cs)
python engine.py

# Replay a recorded session
python run_replay.py recordings/2026-01-15_0900.jsonl

# Record a session from ATAS replay
python gibbz_launcher.py 2026-01-15 --mine
```

---

## Architecture

```
ATAS Chart
  │ UDP :9999 (CSV, 13 fields)
GibbzBridge.cs  (C# ATAS indicator)
  │
market_feed.py      → thread-safe UDP receiver
bar_aggregator.py   → 5-second TIME bars
  │
event_engine.py     → INTENTO / FALLO / AGOTAMIENTO / ACUMULACIÓN
levels.py           → zone context (AT_VAH, ABOVE_VAH …)
confluence_engine.py→ multi-factor score 0–100
validator.py        → gate filters + dynamic penalties
intent_engine.py    → narrative (INDUCTION / SQUEEZE / REBALANCE …)
risk_engine.py      → sizing / stop / target / R:R
  │
voice_engine.py     → priority-queue TTS alerts
logger.py           → CSV session log
engine_view.py      → terminal dashboard
```

**Pipeline call order** (invariant — do not reorder):

```
event → levels → confluence → validator → intent → risk
```

---

## Configuration

Runtime feature flags and connection settings are in `config.py` and can be overridden with environment variables:

| Variable | Default | Description |
|---|---|---|
| `GIBBZ_ENABLE_LOGGING` | `1` | Write CSV session log |
| `GIBBZ_OVERRIDE_SESSION` | `1` | Skip session-time filter |
| `GIBBZ_USE_REAL_FEED` | `1` | Use live UDP feed vs. simulation |
| `GIBBZ_ENABLE_VOICE` | `1` | Enable TTS voice alerts |
| `GIBBZ_UDP_HOST` | `127.0.0.1` | UDP bind host |
| `GIBBZ_UDP_PORT` | `9999` | UDP bind port |
| `GIBBZ_LOG_LEVEL` | `INFO` | Python logger level (`DEBUG`/`INFO`/`WARNING`) |

---

## Testing

```powershell
# All tests (140 tests)
pytest

# With coverage
pytest --cov=. --cov-report=term-missing

# Specific suite
pytest tests/unit/
pytest tests/integration/
pytest tests/e2e/
```

---

## Key Thresholds

| Constant | Value | Location |
|---|---|---|
| `THRESHOLD_INTENTO` | 2.0 pts | `event_engine.py` |
| `WARMUP_BARS` | 3 bars | `event_engine.py` |
| `MIN_SCORE_TO_TRADE` | 45 | `validator.py` |
| `MIN_RR` | 1.5 | `risk_engine.py` |
| `MAX_RISK_PTS` | 20.0 pts | `risk_engine.py` |
| Sizing 86–100 | 2.0 contracts | `risk_engine.py` |
| Sizing 70–85 | 1.0 contracts | `risk_engine.py` |
| Sizing 55–69 | 0.5 contracts | `risk_engine.py` |
| Sizing 42–54 | 0.25 contracts | `risk_engine.py` |

---

## Logs and Outputs

| Path | Contents |
|---|---|
| `logs/gibbz.log` | Structured rotating log (5 MB × 3) |
| `outcomes/` | Per-trade JSON outcome files |
| `expansion_outcomes/` | Session EP-score analysis |
| `recordings/` | Raw JSONL tick recordings |
| `historical_context/` | Per-date JSON context files |

---

## Dependency Graph

```powershell
codegraph $(Get-ChildItem *.py -Name)
# Opens codegraph.html in browser
```
