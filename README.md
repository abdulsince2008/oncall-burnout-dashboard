# On-Call Burnout Early-Warning Dashboard

**Problem:** Engineering teams lose people to burnout from unsustainable on-call loads. Existing tools (PagerDuty, Opsgenie, Squadcast, Keep) focus on alert deduplication and routing — they don't tell you *which engineers are burning out* from slow ack times, rising trends, or too many consecutive on-call days.

**Why this is different:** Most observability tools treat on-call as a routing problem. This tool treats it as a *human-factors* problem. It correlates per-engineer acknowledgment-time trends with consecutive on-call days to produce a rolling **burnout risk score** — a metric no mainstream tool exposes.

## How it Works

1. **Input**: PagerDuty or OpsGenie JSON exports (alerts + optional schedules)
2. **Parse**: Normalize alerts and on-call shifts into a unified schema
3. **Compute per engineer**:
   - Median / P90 acknowledgment time (minutes)
   - Linear trend slope of ack time over the window (min/alert)
   - Maximum consecutive on-call days in the window
4. **Score**: Weighted combination → `burnout_risk_score` (0–2+)
   - `ack_time_weight` × normalized median ack time
   - `trend_weight` × normalized positive trend slope
   - `consecutive_days_weight` × normalized max consecutive days
5. **Output**: Terminal dashboard + JSON report with risk levels (critical/high/medium/low)

## Quick Start

```bash
# Install
pip install -e .

# Generate sample data (PagerDuty format)
oncall-burnout generate-sample -o data/sample -e 8 -d 30

# Run analysis
oncall-burnout analyze data/sample/alerts.json data/sample/schedules.json

# Or with options
oncall-burnout analyze data/sample/alerts.json data/sample/schedules.json \
  --window 30 --top 10 --output report.json
```

## Example Output (Actual Run)

```
╭──────────────────────────────────────────────────────────────────────────────╮
│                  🔥 On-Call Burnout Early-Warning Dashboard                  │
╰─────── Window: 2026-07-30 → 2026-08-29 | Generated: 2026-08-29 23:53 ────────╯

╭────────────────────────────────── Summary ───────────────────────────────────╮
│   Total Engineers     8                                                      │
│   Total Alerts        135                                                    │
│   🔴 Critical Risk    3                                                      │
│   🟠 High Risk        2                                                      │
│   🟡 Medium Risk      3                                                      │
│   🟢 Low Risk         0                                                      │
╰──────────────────────────────────────────────────────────────────────────────╯

                        Top 8 Engineers by Burnout Risk                         
┏━┳━━━━━━━━━━━━━━━━━━┳━━━━━━━━┳━━━━┳━━━┳━━━┳━━━━━━┳━━━━━━━┳━━━━━━━━━━┳━━━━━━━━━┓
┃ ┃                  ┃        ┃    ┃   ┃   ┃ Med… ┃       ┃      Max ┃ On-Call ┃
┃ ┃ Engineer         ┃  Risk  ┃ S… ┃ … ┃ … ┃  Ack ┃ Trend ┃ Consecu… ┃    Days ┃
┡━╇━━━━━━━━━━━━━━━━━━╇━━━━━━━━╇━━━━╇━━━╇━━━╇━━━━━━╇━━━━━━━╇━━━━━━━━━━╇━━━━━━━━━┩
│ │ Bob Martinez     │   🔴   │ 1… │ … │ … │ 36.… │ +4.1… │       10 │      17 │
│ │                  │ CRITI… │    │   │   │      │       │          │         │
│ │ Grace Lee        │   🔴   │ 1… │ … │ … │ 49.… │ +3.0… │        7 │      16 │
│ │                  │ CRITI… │    │   │   │      │       │          │         │
│ │ Eva Rodriguez    │   🔴   │ 1… │ … │ … │ 66.… │ +1.6… │        8 │      14 │
│ │                  │ CRITI… │    │   │   │      │       │          │         │
│ │ Frank Thompson   │   🟠   │ 1… │ … │ … │ 24.… │ +1.7… │        9 │      15 │
│ │                  │  HIGH  │    │   │   │      │       │          │         │
│ │ David Kim        │   🟠   │ 1… │ … │ … │ 43.… │ +1.3… │        4 │      10 │
│ │                  │  HIGH  │    │   │   │      │       │          │         │
│ │ Carol Singh      │   🟡   │ 0… │ … │ … │ 22.… │ +1.6… │        6 │      14 │
│ │                  │ MEDIUM │    │   │   │      │       │          │         │
│ │ Henry Patel      │   🟡   │ 0… │ 7 │ 6 │ 37.… │ -0.1… │        6 │      14 │
│ │                  │ MEDIUM │    │   │   │      │       │          │         │
│ │ Alice Chen       │   🟡   │ 0… │ … │ … │ 28.… │ -0.1… │        7 │      14 │
│ │                  │ MEDIUM │    │   │   │      │       │          │         │
└─┴──────────────────┴────────┴────┴───┴───┴──────┴───────┴──────────┴─────────┘

╭──────────────────────────────────────────────────────────────────────────────╮
│ ⚠ ACTION REQUIRED: 3 critical + 2 high-risk engineers detected.              │
│ Consider: redistributing on-call load, adding coverage, or scheduling        │
│ recovery time.                                                               │
╰──────────────────────────────────────────────────────────────────────────────╯
```

## Tech Stack & Libraries Reused

| Library | Purpose | Why |
|---------|---------|-----|
| **pandas** | Data manipulation | Industry standard, battle-tested |
| **numpy** | Numerical computations | Fast percentiles, polyfit for trends |
| **pydantic** | Data validation & parsing | Type-safe models, automatic datetime parsing |
| **typer** | CLI framework | Modern, type-hint based, auto-help |
| **rich** | Terminal UI | Beautiful tables, panels, progress bars |

The **genuinely new piece** is the burnout risk formula: `weighted(median_ack, ack_trend_slope, max_consecutive_days)` — a human-factors metric no existing tool computes.

## Known Limitations / What's Next

- **No live API integration** — works on exported JSON only (by design, zero-credential)
- **Single-window analysis** — no historical comparison across windows yet
- **No team-level aggregation** — only per-engineer scores
- **OpsGenie parser** covers common fields but may need tuning for custom exports
- **No web UI** — terminal-only for now (could add FastAPI + React dashboard)
- **Alert grouping** — doesn't deduplicate correlated alerts (assumes input is pre-deduped)

---

## License

MIT License — see [LICENSE](LICENSE)