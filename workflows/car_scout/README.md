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
- `MARKETCHECK_API_KEY` — MarketCheck free tier (500 calls/mo — fits twice-daily cadence)
- `RESEND_API_KEY` — Resend transactional email (aptoworks.com DNS-verified)
- `BRIGHTDATA_ZONE`, `BRIGHTDATA_USERNAME`, `BRIGHTDATA_PASSWORD` — Web Unlocker zone for per-VIN VDP title verification
- `CAR_SCOUT_DIGEST_FROM=alfred@aptoworks.com`, `CAR_SCOUT_DIGEST_TO=nick@aptoworks.com`

Optional:
- `AOL_API_TOKEN` — Pennyworth API token, only needed when V1.1 unicorn SMS ships
- `OPENROUTER_API_KEY` — reserved for V1.1 LLM-based red-flag scanning
- `VINAUDIT_API_KEY` — V1.1 vehicle-history Tier B, unicorn candidates only

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
  MARKETCHECK_API_KEY=... \
  RESEND_API_KEY=re_... \
  BRIGHTDATA_ZONE=aptoflow_unblocker \
  BRIGHTDATA_USERNAME=brd-customer-hl_... \
  BRIGHTDATA_PASSWORD=... \
  CAR_SCOUT_DIGEST_FROM=alfred@aptoworks.com \
  CAR_SCOUT_DIGEST_TO=nick@aptoworks.com \
  SCOUT_ZIP=98225 \
  SCOUT_RADIUS_MI=100 \
  BUDGET_CEILING_USD=22000 \
  YEAR_FLOOR=2015 \
  PRIMARY_MILEAGE_CEILING=80000 \
  SECONDARY_MILEAGE_CEILING=110000
```

Deploy:

```bash
modal deploy workflows/car_scout/main.py
```

Two scheduled functions will appear in the Modal dashboard:
- `digest_cron_am` — daily at 13:30 UTC (~06:30 PT morning digest)
- `digest_cron_pm` — daily at 01:30 UTC (~18:30 PT evening refresh)

V1.1 will re-enable `scout_cron` every 2h for real-time unicorn SMS.

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
