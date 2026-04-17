# car_scout

Daily used-car digest + rare SMS unicorn alerts for Owen's first car search.

## What it does

Runs every 2 hours on Modal:
1. Scrapes dealer inventory (CarGurus in V1; Autotrader/Cars.com/dealer-direct queued for V1.5) via Bright Data Web Unlocker.
2. Normalizes listings into a canonical `Listing` model.
3. Scores each listing against rolling market-median comps (30-day window) + mileage-percentile + CarGurus deal rating.
4. Fires a **unicorn SMS** via Pennyworth when a primary-tier vehicle hits all 5 strict criteria (new listing OR ≥5% price drop, CarGurus Great OR ≥10% below market, bottom-25 miles for its year, confirmed clean title, primary-tier vehicle).
5. Saves state to a Modal volume for cross-cycle dedup + price-history tracking.

Once a day at 6:30 AM PT:
- Assembles an HTML digest (Top Picks / New Today / Price Drops) from the last 24h of tracked listings.
- Emails it to `nick@aptoworks.com` via Gmail SMTP.

## Setup

### 1. Environment

Copy `.env.example` → `.env` and fill in every variable:

```bash
cp .env.example .env
# Edit .env
```

Required:
- `OPENROUTER_API_KEY` — for future LLM-based red-flag scanning (V1.1)
- `BRIGHTDATA_ZONE`, `BRIGHTDATA_USERNAME`, `BRIGHTDATA_PASSWORD` — Bright Data Web Unlocker creds
- `APTOFLOW_SMTP_*` + `CAR_SCOUT_DIGEST_*` — Gmail SMTP for the digest email
- `AOL_API_TOKEN` — Pennyworth API token (same one used elsewhere)

Optional:
- `VINAUDIT_API_KEY` — vehicle history (Tier B, unicorn candidates only) — deferred to V1.1

### 2. Local dry-run

Validate wiring without firing SMS, sending email, or writing state:

```bash
python -m workflows.car_scout.main scout --dry-run
python -m workflows.car_scout.main digest --dry-run
```

### 3. Modal deploy

Ensure `car-scout-secrets` exists as a Modal secret with all env vars:

```bash
modal secret create car-scout-secrets \
  OPENROUTER_API_KEY=... \
  BRIGHTDATA_ZONE=... \
  BRIGHTDATA_USERNAME=... \
  BRIGHTDATA_PASSWORD=... \
  APTOFLOW_SMTP_HOST=smtp.gmail.com \
  APTOFLOW_SMTP_PORT=587 \
  APTOFLOW_SMTP_USERNAME=alfred@aptoworks.com \
  APTOFLOW_SMTP_PASSWORD=... \
  CAR_SCOUT_DIGEST_FROM=alfred@aptoworks.com \
  CAR_SCOUT_DIGEST_TO=nick@aptoworks.com \
  PENNYWORTH_BASE_URL=https://pw.aptoworks.cloud \
  AOL_API_TOKEN=... \
  SCOUT_ZIP=98225 \
  SCOUT_RADIUS_MI=100
```

Deploy:

```bash
modal deploy workflows/car_scout/main.py
```

Two cron functions will appear in the Modal dashboard:
- `scout_cron` — every 2h
- `digest_cron` — daily at 13:30 UTC (~06:30 PT)

## Retirement

When Owen has a car:

```bash
modal app stop aptoflow-car-scout
```

Update `CATALOG.md` status to `Archived`.

## Files

| Module | Purpose |
|--------|---------|
| `main.py` | Modal app + cron entrypoints + local CLI |
| `models.py` | `Listing`, `Score`, `WorkflowState` (Pydantic) |
| `state.py` | Load/save JSON, dedup merge, rolling 30d comps, SMS rate-limit window |
| `scoring.py` | 4-component weighted scoring (0-100) + band classification |
| `unicorn.py` | 5-gate unicorn matcher with rate-limit + dedupe safety valves |
| `notify.py` | Pennyworth `/api/events/external` POST + SMS formatter |
| `digest.py` | Section assembly + Jinja2 HTML render + SMTP send |
| `templates/digest.html.j2` | Inline-CSS email template |
| `sources/base.py` | `AbstractSourceScraper` + model tier registry |
| `sources/cargurus.py` | CarGurus inventory scraper (multi-strategy JSON extraction) |

## Notes

- **All 7 planned sources block direct HTTP.** V1 uses Bright Data Unlocker for CarGurus only; Autotrader/Cars.com/local dealers are queued for V1.5 once CarGurus is battle-tested.
- **CarGurus selectors are best-effort.** The `entitySelectingHelper.selectedEntity` codes in `sources/cargurus.py` are ballpark — verify with one real fetch and update if pagination returns empty.
- **LLM red-flag scan is deferred to V1.1.** Scoring currently uses severity=0 for all listings (neutral contribution).
