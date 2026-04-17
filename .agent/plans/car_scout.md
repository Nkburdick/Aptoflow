# Plan: car_scout

## Metadata

- **Complexity**: Complex
- **Deploy Target**: modal-scheduled
- **Modal Mode**: scheduled (two crons — scout every 2h, digest daily at 13:25 UTC / 06:25 PT)
- **Workflow Type**: pipeline (deterministic scrape + LLM-scored ranking + email/SMS delivery)
- **Recommended Model**: `google/gemini-2.0-flash-001` for extraction/red-flag scan, `anthropic/claude-sonnet-4` for digest narrative
- **Status**: Planned

## Overview

A serverless agent that scouts the Pacific-Northwest used-car market every two hours for vehicles matching Owen's (Nick's son) criteria, scores each listing against market comps, and delivers:

1. A **daily HTML email digest** at 6:30 AM PT to `nick@aptoworks.com` summarizing the last 24h of findings (Top Picks / New Today / Price Drops).
2. A **rare, real-time SMS ping** via Pennyworth the moment a "unicorn" (top 5% deal + primary-tier vehicle + clean title + low miles) appears — tuned to fire ~0-2 times per week.

The workflow runs for ~60 days (through ~mid-June 2026) while Owen shops for his first car, then gets retired.

## Motivation

Owen just turned 16. Nick wants to get him a decent first car at a decent price without the drawn-out pain of manually watching listings. Constraints:

- Budget: payment target ~$325/mo × 72mo @ 6.5% APR + ~$4-5k down → sticker ceiling ~$22k
- Vehicle: Subaru Crosstrek is the anchor; several similar models are acceptable
- Nick hates negotiating and haggling — automation should surface good deals *before* the market reacts
- Geography: Bellingham → Seattle metro (~100 mi radius)
- Timeline: car in hand by ~2026-06-17

This is a classic "agent does the boring watching, human does the decisive acting" use case.

## User Experience

### Morning Digest (daily, 6:30 AM PT)

- Single HTML email to `nick@aptoworks.com`
- Three sections, in order:
  1. 🏆 **Top Picks** — up to 3 highest-scoring listings from the last 24h
  2. 🆕 **New Today** — all listings first seen in the last 24h, sorted by score
  3. 📉 **Price Drops** — listings seen before that dropped price ≥5%
- Each card: hero photo, price, year/make/model/trim, mileage, deal score badge, history badges (accidents/owners/title per listing page), distance from Bellingham, "View listing" button
- Empty-state friendly: if nothing new, send a brief "no new matches — still watching" email

### Unicorn SMS (real-time, rare)

- Fires the moment a unicorn is detected mid-scout (outside the digest window)
- Delivered as SMS via Pennyworth (green bubble on iPhone, functionally identical to iMessage)
- Body < 160 chars: `🏆 Unicorn: 2020 Crosstrek Premium, 42k mi, $19,900 @ Roger Jobs (Bellingham) — 14% below market. <short.link>`
- Hard rate limit: max 3 SMS per 24h (safety valve against a bad scraper run)
- Dedupe: same VIN never triggers SMS twice

## Scope

### In scope (V1)
- Deterministic scraping of dealer inventory from 3 aggregator sites + 4-5 named local dealer sites
- Listing normalization into a common Pydantic schema
- Deal scoring algorithm (market-median delta + mileage percentile + CarGurus rating + LLM red-flag scan)
- Hard filtering (clean title, budget cap, tier-based mileage floor, etc.)
- Unicorn detection + SMS via Pennyworth
- Daily HTML email digest
- State persistence on Modal volume (VIN/URL dedup + price history)
- Third-party VIN history API (paid, on-demand for unicorns only)

### Deferred to V1.5 (~2 weeks after V1 ships)
- Private-party sources (Craigslist Seattle, Facebook Marketplace) — higher scraping complexity, bigger inventory pool
- "Dismiss"/"Save"/"Shortlist" action links in the email

### Out of scope entirely
- Automated dealer outreach (no auto-sending emails or filling contact forms)
- Price negotiation suggestions (manual talking points only, if at all)
- Financing automation (Nick handles financing directly)
- Cross-border (BC) sources

## Inputs

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| trigger | Literal["scout", "digest"] | Yes | Which cron entrypoint is invoking the run |
| now | datetime | No | Override current time (for testing); defaults to UTC now |

Config lives in env vars (see Configuration section) — not in the input payload, because triggers are cron-driven and carry no meaningful parameters.

## Outputs

| Field | Type | Description |
|-------|------|-------------|
| trigger | str | Which cron ran (for logs) |
| sources_polled | dict[str, int] | Per-source listing counts (for monitoring) |
| new_listings | int | Count of listings first seen this run |
| price_drops | int | Count of listings with price decreases |
| unicorns_fired | int | SMS unicorn pings fired this run (0 for digest runs) |
| digest_sent | bool | Whether a digest email was sent (true only on digest trigger) |
| cost_usd | float | Aggregate LLM cost for this run |
| duration_sec | float | Total wall-clock time |
| errors | list[str] | Per-source errors (non-fatal; run continues) |

## Pydantic Models

```python
from datetime import datetime
from typing import Literal, Optional
from pydantic import BaseModel, Field, HttpUrl

Transmission = Literal["auto", "manual", "unknown"]
TitleStatus = Literal["clean", "salvage", "rebuilt", "unknown"]
Source = Literal["cargurus", "autotrader", "cars_com", "dealer_direct"]
Tier = Literal["primary", "secondary"]
ScoreBand = Literal["unicorn", "great", "good", "fair", "pass"]


class PriceObservation(BaseModel):
    """One snapshot of a listing's price at a point in time."""
    timestamp: datetime
    price: int  # USD


class Listing(BaseModel):
    """Canonical shape of a single vehicle listing, normalized across sources."""
    # Identity
    url: HttpUrl                       # primary dedup key when VIN missing
    vin: Optional[str] = None          # dedup key when present (preferred)
    source: Source

    # Core vehicle
    year: int
    make: str                          # "Subaru"
    model: str                         # "Crosstrek"
    trim: Optional[str] = None         # "Premium", "Sport", etc.
    transmission: Transmission = "unknown"
    mileage: int
    exterior_color: Optional[str] = None

    # Listing metadata
    price: int                         # USD, current asking
    price_history: list[PriceObservation] = Field(default_factory=list)
    title_status: TitleStatus = "unknown"
    photos: list[HttpUrl] = Field(default_factory=list)
    description: str = ""

    # Seller
    dealer_name: Optional[str] = None
    seller_type: Literal["dealer", "private", "unknown"] = "dealer"
    city: Optional[str] = None
    state: Optional[str] = None
    distance_mi: Optional[int] = None   # from Bellingham center

    # History badges (scraped from listing page when available)
    accident_count: Optional[int] = None
    owner_count: Optional[int] = None
    use_type: Optional[str] = None      # "Personal", "Fleet", etc.

    # CarGurus-supplied deal rating (verbatim)
    cargurus_rating: Optional[Literal["Great", "Good", "Fair", "High", "Overpriced"]] = None

    # Bookkeeping
    first_seen: datetime
    last_seen: datetime
    tier: Tier                          # assigned from model


class Score(BaseModel):
    """Computed score for a listing against current market."""
    listing_url: HttpUrl
    computed_at: datetime

    # Components (each 0-100)
    cargurus_component: float
    market_delta_component: float       # how far below market median (higher = better deal)
    mileage_component: float            # lower miles for the model/year = higher score
    redflag_penalty: float              # 0 = clean description, up to -30 for severe flags

    total: float                        # weighted sum, 0-100
    band: ScoreBand
    reasoning: str                      # LLM-generated 1-sentence "why this is a deal"

    # Unicorn gate fields (computed once; used by matcher)
    is_new_listing: bool
    had_recent_price_drop: bool
    mileage_percentile_for_model_year: float  # 0-100
    passes_unicorn: bool
```

## Data Sources

Each source has: base URL template, scrape approach, rate limit, extraction notes, anti-bot risk, legal note.

### S1. CarGurus
- **Base query URL**: `https://www.cargurus.com/Cars/inventorylisting/viewDetailsFilterViewInventoryListing.action?zip=98225&distance=100&sourceContext=carGurusHomePageModel`
- **Approach**: direct HTTP GET with realistic User-Agent + Accept-Language headers. CarGurus serves listing data in embedded JSON within the HTML (`__INITIAL_STATE__` or similar). Extract via regex → parse JSON → iterate listings.
- **Rate limit**: 1 request every 8 seconds, jittered ±2s. Stop after 100 listings per query.
- **Per-listing detail**: follow each listing URL to pull description, full photos, CarGurus deal rating, accident/owner/title badges.
- **Deal rating**: CarGurus publishes a "Great Deal" / "Good Deal" / "Fair Deal" / "High" / "Overpriced" badge on each listing. This is the single most valuable free signal.
- **Anti-bot risk**: Moderate. CarGurus uses Cloudflare but serves normal HTML to realistic browsers. If direct HTTP starts returning challenge pages, escalate to Bright Data proxy (already configured for Alfred's scraping skill).
- **Legal**: robots.txt permits crawling of listing pages. No login required. Respect rate limit.

### S2. Autotrader
- **Base query URL**: `https://www.autotrader.com/cars-for-sale/all-cars/subaru/crosstrek/bellingham-wa-98225?searchRadius=100`
- **Approach**: HTTP GET, extract embedded JSON from `<script id="__NEXT_DATA__">`.
- **Rate limit**: 1 req / 10s jittered.
- **Per-listing detail**: second HTTP GET per listing for full description + photos.
- **Anti-bot risk**: Moderate-high. Autotrader is known to challenge headless requests. Start with direct HTTP, fall back to Bright Data Unblocker proxy.
- **Legal**: robots.txt permits listing-page crawl.

### S3. Cars.com
- **Base query URL**: `https://www.cars.com/shopping/results/?dealer_id=&include_shippable=false&list_price_max=22000&makes[]=subaru&models[]=subaru-crosstrek&maximum_distance=100&zip=98225`
- **Approach**: HTTP GET, parse listing cards from HTML.
- **Rate limit**: 1 req / 8s.
- **Anti-bot risk**: Low-moderate. Direct HTTP typically works.

### S4–S8. Local dealer sites (direct)
- **Roger Jobs Subaru** (Bellingham): `https://www.rogerjobssubaru.com/used-inventory/index.htm`
- **Dewey Griffin Subaru** (Bellingham): `https://www.deweygriffinsubaru.com/used-vehicles/`
- **Wilson Toyota** (Bellingham): `https://www.wilsontoyota.com/used-vehicles-bellingham-wa/`
- **Honda of Bellingham**: `https://www.hondaofbellingham.com/used-vehicles/`
- **Northwest Honda** (Bellingham): `https://www.northwesthonda.com/used-vehicles/`

Dealer sites are typically Dealer.com or Dealerinspire-powered and serve JSON inventory APIs. Approach: check each site's `/apis/search/inventory` or similar JSON endpoint first; fall back to HTML scrape.

Rate limit: 1 req / 15s per dealer (they're small sites — don't hammer).

### Source registry

Encode the list in `sources.py` as a registry so adding a dealer is a single-line change:

```python
SOURCES: list[SourceConfig] = [
    SourceConfig(name="cargurus", scraper=CarGurusScraper, enabled=True, priority=1),
    SourceConfig(name="autotrader", scraper=AutotraderScraper, enabled=True, priority=1),
    SourceConfig(name="cars_com", scraper=CarsComScraper, enabled=True, priority=1),
    SourceConfig(name="roger_jobs", scraper=DealerScraper, url=..., enabled=True, priority=2),
    # ...
]
```

## Filters (applied before scoring)

### Hard filters — listings violating these are dropped silently

1. **Title status**: `clean` or `unknown` only. `salvage` and `rebuilt` are hard-rejected.
   - "Unknown" is allowed with a badge "⚠️ History unknown — verify Carfax"; it's not a rejection.
2. **Budget**: `price ≤ 22000`.
3. **Year**: `year ≥ 2015`.
4. **Model**: must be in the tier registry below.
5. **Transmission**:
   - Default filter: `auto`.
   - **Override**: allow `manual` IFF the listing would otherwise score `great` or better (≥85). Rationale: a manual Crosstrek at 15% under market is worth surfacing even though Owen can't drive stick yet — it's a teaching opportunity at a good price.
6. **Mileage ceiling by tier**:
   - Primary tier (Subarus): `mileage < 80000`.
   - Secondary tier (Toyota/Honda/Mazda): `mileage < 110000`.

### Model tier registry

```python
PRIMARY_MODELS = {
    ("Subaru", "Crosstrek"),
    ("Subaru", "Forester"),
    ("Subaru", "Outback"),
    ("Subaru", "Impreza"),  # AWD hatchback only — filter body_style
}

SECONDARY_MODELS = {
    ("Toyota", "RAV4"),
    ("Honda", "CR-V"),
    ("Mazda", "CX-5"),
}
```

Secondary tier listings are surfaced **only** if they score `good` or better (≥70). Primary tier listings surface at any score (including `fair`), because Nick anchors on Subaru.

## Scoring Algorithm

Every listing that survives hard filters gets scored 0–100 with four components, weighted-summed into a total.

### Component A: CarGurus rating (weight 0.30)

| CarGurus rating | Component A |
|---|---|
| Great Deal | 100 |
| Good Deal | 80 |
| Fair Deal | 60 |
| High | 30 |
| Overpriced | 10 |
| (missing) | 50 (neutral) |

### Component B: Market-median delta (weight 0.40)

For each `(year, make, model)` bucket, compute the 30-day rolling median of all listings seen across all sources. Let `median` be this number.

`delta_pct = (median - listing.price) / median`

| delta_pct | Component B |
|---|---|
| ≥ +20% (listing is 20%+ below median) | 100 |
| +10% to +20% | 85 |
| +5% to +10% | 65 |
| -5% to +5% | 50 |
| -5% to -15% | 25 |
| ≤ -15% (way above median) | 5 |

Edge case: if the bucket has <3 comps, fall back to KBB/Edmunds published fair-market values loaded from a reference table refreshed monthly. Flag `low_comp_confidence=True` in the Score model.

### Component C: Mileage percentile (weight 0.20)

For the same `(year, make, model)` bucket, compute the percentile rank of this listing's mileage. Lower mileage = higher percentile rank flipped → higher component score.

| Percentile rank (0 = lowest miles, 100 = highest) | Component C |
|---|---|
| 0–25 (bottom quartile for miles) | 100 |
| 25–50 | 75 |
| 50–75 | 50 |
| 75–90 | 25 |
| 90–100 | 10 |

### Component D: Red-flag LLM scan (weight 0.10, applied as penalty)

Pass the listing description to Gemini Flash with this prompt:

> Analyze this used-car listing description. Return JSON with two fields:
> - `flags`: list of strings, each a specific concern ("needs head gasket", "previous accident not disclosed", "sold as-is with no warranty", "rebuilt title admitted", "high-revving use", "salvage auction purchase", etc.). Empty list if clean.
> - `severity`: integer 0-3 (0 = clean, 1 = minor concern, 2 = significant concern, 3 = deal-breaker)

Component D = `(3 - severity) * 33`, capped 0–100. Severity-3 listings get downgraded aggressively; severity-0 listings are unaffected.

### Total score

```python
total = (
    0.30 * component_a
    + 0.40 * component_b
    + 0.20 * component_c
    + 0.10 * component_d
)
```

Band assignment:

| Total | Band |
|---|---|
| ≥ 95 | `unicorn` (eligible for SMS — see below) |
| 85–94.9 | `great` |
| 70–84.9 | `good` |
| 50–69.9 | `fair` |
| < 50 | `pass` (not included in digest at all) |

### Reasoning blurb

For every listing scoring `good` or better, run a second Gemini Flash call to generate a 1-sentence human-readable "why this is a deal":

> Given this listing (year, model, price, mileage) and score breakdown (CarGurus: X, market delta: Y%, mileage percentile: Z), write ONE sentence under 25 words explaining why this is or isn't a good deal. Plain English, no jargon.

Example output: "2020 Crosstrek Premium at $19,900 with 42k miles — 14% below market median and in the lowest-mile quartile for its year."

## Unicorn Criteria (SMS trigger)

A listing triggers an SMS ping iff **all five** of these evaluate true:

1. `listing.tier == "primary"` (Crosstrek / Forester / Outback / Impreza)
2. `(listing.cargurus_rating == "Great") OR (component_b >= 85)` — i.e. CarGurus flagged it Great, OR it's at least 10% below the market median by our own math
3. `mileage_percentile_for_model_year <= 25` — in the lowest-mileage quartile for its year
4. `listing.title_status == "clean"` — NOT "unknown" — unicorn requires *confirmed* clean
5. `(listing.first_seen > now - 2 hours) OR (had_price_drop_this_run AND drop_pct >= 5)` — i.e. either brand-new to us this run, or just dropped ≥5%

Additional SMS-only safety gates:

- `unicorn_sms_fired_in_last_24h < 3` (rate-limit safety valve)
- `vin not in already_notified_set` (dedupe per VIN — even across days)

Tuning note: if these criteria fire more than ~2/week, tighten thresholds. If they fire less than ~1/month, loosen condition 2 to `component_b >= 75`.

## Vehicle History Layer

Three tiers of history data:

### Tier A — Free, on every listing (listing-page scrape)

Most listings on CarGurus, Autotrader, and Cars.com display history badges pulled from AutoCheck or Carfax (accident count, owner count, personal-use flag, clean-title confirmation). Extract these during per-listing detail scrape.

Populate `listing.accident_count`, `listing.owner_count`, `listing.use_type`, `listing.title_status`.

### Tier B — Paid API, on-demand (unicorn candidates only)

When a listing passes the unicorn matcher but Tier A left gaps (e.g., title_status still "unknown"), call a third-party VIN history API via one of:

- **VinAudit** (https://www.vinaudit.com/autocheck-api) — ~$2-4 per AutoCheck pull
- **One Auto API** (https://www.oneautoapi.com/service/experian-autocheck/) — pricing similar
- **Vini.az** (https://vini.az/en/api) — cheaper, lower reliability

Implementation: pick one provider, store API key in Modal secret. Retry once on failure. Cost-tracked like any LLM call.

Budget envelope: ~$5/week × 8 weeks = $40 total for the full search.

### Tier C — Manual, pre-purchase (Nick-driven)

When Nick decides to visit a vehicle, he runs a full Carfax manually ($40). Out of scope for the workflow.

## Digest Email

### Delivery

- **From**: `alfred@aptoworks.com` (existing Google Workspace address — uses Gmail SMTP via Modal secret)
- **To**: `nick@aptoworks.com`
- **Subject**: `🚗 Car Scout — {N} new today, {M} top picks ({date})` where date is short format e.g. "Fri 4/17"
- **Reply-to**: `nick@aptoworks.com` so replies don't dead-end
- **Schedule**: daily at 13:30 UTC (06:30 PT when Pacific is on PDT; 14:30 UTC for PST) — use `pytz`/`zoneinfo` to compute the right cron based on DST state at deploy time

### HTML Template

```
┌─────────────────────────────────────────┐
│ 🚗 Car Scout Digest — Fri, Apr 17      │
│ Since yesterday: 12 new, 3 price drops │
└─────────────────────────────────────────┘

🏆 TOP PICKS
┌────────────────┐
│ [hero photo]   │
│ 2020 Crosstrek │
│ Premium        │
│ $19,900 • 42k  │
│ ⭐ Great Deal  │
│ 🧾 0 acc • 1 own │
│ 📍 Bellingham  │
│ "14% below mkt"│
│ [View listing] │
└────────────────┘
(up to 3 cards)

🆕 NEW TODAY (12)
[compact 2-column card grid]

📉 PRICE DROPS (3)
[compact 2-column card grid, showing old → new price]

─────────────────────────────
Scout run at 06:25 PT • 6 sources checked • 127 listings in state
Unsubscribe (kills the Modal cron)
```

### Empty-state email

If the run produced zero listings worth surfacing (all scored `pass`, no new listings, no price drops), send a terse "no new matches" email so Nick knows the system is still running.

Subject: `🚗 Car Scout — still watching (no new matches Fri 4/17)`

### Tech

- Use `jinja2` for the template (install in `requirements.txt`)
- Render to HTML + plain-text fallback
- Send via `smtplib` using Gmail app password in `APTOFLOW_SMTP_PASSWORD` Modal secret
- Inline CSS (no external stylesheets — most clients strip `<link>` tags)

## SMS Notification Path

### V1 (recommended): add external-event endpoint to Pennyworth

Pennyworth today has a closed union of notification event types (`src/types/notification-event.ts`) — chat.completion, forge.complete, etc. None are external-workflow events.

**Proposal**: add a new event type to Pennyworth and an external-auth endpoint:

- New event type: `"external.workflow_alert"`
- New endpoint: `POST /api/events/external`
- Auth: bearer token stored in `APTOFLOW_EXTERNAL_TOKEN` env var
- Payload:
  ```json
  {
    "type": "external.workflow_alert",
    "title": "🏆 Car Scout: Unicorn match",
    "body": "2020 Crosstrek Premium, 42k mi, $19,900",
    "url": "https://www.cargurus.com/Cars/link/...",
    "data": { "workflow": "car_scout", "vin": "JF2GTAPC9LH232411" },
    "channels": ["sms", "push", "feed"]
  }
  ```
- Pennyworth routes it through `notification-routing.ts` → SMS/push/feed as requested.

**Cost to ship**: small Pennyworth PR (~30 lines of new code + test). Does not block V1 if sequenced first.

### V1 fallback (if Pennyworth extension delayed): direct Twilio

- Car scout has its own `TWILIO_ACCOUNT_SID` / `TWILIO_AUTH_TOKEN` / `TWILIO_FROM_NUMBER` Modal secrets
- Sends via Twilio Programmable SMS directly
- Duplicates the Twilio credential surface (sub-ideal long-term)

**Recommendation**: ship the Pennyworth extension first (1-day task), then car_scout V1 uses it. Don't build direct Twilio — it'll ossify.

## State Management

### Persistence

Modal `Volume` mounted at `/data` — survives function cold starts.

Single state file: `/data/car_scout_state.json`

### State schema

```python
class WorkflowState(BaseModel):
    # Listings keyed by URL (VIN preferred as dedup when present, but URL is primary key
    # because VIN isn't always extractable from listing page)
    listings: dict[str, Listing]

    # Bucket comps used for market-median computation, rolling 30-day window
    comps: dict[str, list[PriceObservation]]  # key: "year_make_model"

    # Unicorn SMS dedupe — VINs we've already pinged about
    unicorn_vins_notified: set[str]

    # Rolling SMS count for rate limiting (last 24h)
    sms_timestamps: list[datetime]

    # Digest dedupe — URLs already featured in Top Picks this week (avoid repeating)
    top_picks_last_7_days: dict[str, datetime]  # url -> timestamp featured

    # Bookkeeping
    last_scout_run: Optional[datetime]
    last_digest_sent: Optional[datetime]
    runs_total: int = 0
```

### Dedup logic (per run)

On each scout run, for every scraped raw listing:

1. Compute dedup key: `vin if vin else hash(url)`
2. If key exists in state: update `last_seen`, append to `price_history` if price changed.
3. If key is new: insert with `first_seen = now`, `last_seen = now`, empty `price_history`.

### Pruning

At the end of each run:
- Remove listings where `last_seen < now - 7 days` (treated as delisted)
- Remove comps observations older than 30 days
- Remove `sms_timestamps` older than 24h
- Remove `top_picks_last_7_days` entries older than 7 days
- Remove `unicorn_vins_notified` entries older than 30 days (rare edge case: same VIN relists)

## Architecture

```
┌──────────────────────────────────────────────────────┐
│ Modal App: aptoflow-car-scout                        │
│                                                      │
│  ┌────────────────────────┐                         │
│  │ scout_cycle()          │  cron: every 2h         │
│  │  - poll all sources    │                         │
│  │  - normalize & dedup   │                         │
│  │  - score each listing  │                         │
│  │  - match unicorns      │                         │
│  │  - fire SMS if unicorn │◀───┐                    │
│  │  - persist state       │    │                    │
│  └────────────────────────┘    │                    │
│             │                   │                    │
│  ┌──────────▼─────────────┐    │                    │
│  │ assemble_and_send_     │    │                    │
│  │ digest()               │    │                    │
│  │  cron: daily 13:30 UTC │    │                    │
│  │  - read state          │    │                    │
│  │  - assemble sections   │    │                    │
│  │  - render HTML         │    │                    │
│  │  - SMTP send           │    │                    │
│  └────────────────────────┘    │                    │
│                                 │                    │
│  Modal Volume: /data           │                    │
│    car_scout_state.json        │                    │
│                                 │                    │
└─────────────────────────────────┼────────────────────┘
                                  │
                     POST /api/events/external
                                  │
                                  ▼
                         ┌────────────────┐
                         │ Pennyworth     │
                         │ KVM 2          │
                         │ → Twilio SMS   │
                         │ → Push PWA     │
                         │ → In-app feed  │
                         └────────────────┘
                                  │
                                  ▼
                             Nick's phone
```

### File layout (matches linkedin_reposter pattern)

```
workflows/car_scout/
├── __init__.py
├── main.py           # Modal app + cron entrypoints
├── models.py         # Pydantic schemas (Listing, Score, WorkflowState)
├── sources/
│   ├── __init__.py
│   ├── base.py       # AbstractScraper
│   ├── cargurus.py
│   ├── autotrader.py
│   ├── cars_com.py
│   └── dealer.py     # Generic Dealer.com scraper for local dealers
├── scoring.py        # Market median + percentile + total score + band
├── unicorn.py        # Unicorn matcher
├── history.py        # VIN history API (Tier B)
├── digest.py         # HTML email assembly + SMTP send
├── notify.py         # Pennyworth external-event POST
├── state.py          # load/save state, dedup, pruning
├── templates/
│   └── digest.html.j2
├── tests/
│   ├── test_scoring.py
│   ├── test_unicorn.py
│   ├── test_state.py
│   └── fixtures/
│       ├── cargurus_sample.html
│       └── autotrader_sample.html
├── README.md
└── .env.example
```

## Dependencies

New to `requirements.txt`:
- `jinja2>=3.1.0` — email template rendering
- `beautifulsoup4>=4.12.0` — HTML parsing
- `httpx[http2]>=0.27.0` — async HTTP client (faster than requests for concurrent scrape)
- `geopy>=2.4.0` — distance-from-Bellingham calc
- `pydantic[email]>=2.0.0` — already in repo; ensure the email validator extra is installed

External services:
- Modal — compute + cron + volume + secrets
- OpenRouter — LLM access
- Gmail SMTP — email delivery (alfred@aptoworks.com app password)
- Pennyworth `/api/events/external` — SMS routing (requires Pennyworth PR first)
- VinAudit or One Auto API — VIN history (Tier B, optional for V1)

Optional (fallback):
- Bright Data Unblocker proxy — if direct HTTP starts hitting bot challenges

## Configuration

`.env.example`:

```bash
# LLM
OPENROUTER_API_KEY=sk-or-v1-xxxxx

# Email delivery
APTOFLOW_SMTP_HOST=smtp.gmail.com
APTOFLOW_SMTP_PORT=587
APTOFLOW_SMTP_USERNAME=alfred@aptoworks.com
APTOFLOW_SMTP_PASSWORD=<gmail-app-password>
CAR_SCOUT_DIGEST_TO=nick@aptoworks.com

# Pennyworth notifications
PENNYWORTH_BASE_URL=https://pennyworth.aptoworks.com
APTOFLOW_EXTERNAL_TOKEN=<bearer-token-issued-by-pennyworth>

# VIN history (Tier B, optional)
VIN_HISTORY_PROVIDER=vinaudit
VINAUDIT_API_KEY=<api-key>

# Bright Data fallback (optional)
BRIGHTDATA_ZONE=aptoflow_unblocker
BRIGHTDATA_PASSWORD=<password>

# Tuning
SCOUT_RADIUS_MI=100
SCOUT_ZIP=98225
BUDGET_CEILING_USD=22000
YEAR_FLOOR=2015
PRIMARY_MILEAGE_CEILING=80000
SECONDARY_MILEAGE_CEILING=110000
UNICORN_SMS_DAILY_CAP=3
STATE_FILE_PATH=/data/car_scout_state.json
```

All secret values live in `modal.Secret.from_name("car-scout-secrets")`, not in the repo.

## Implementation Tasks

### Phase 0 — Prerequisites (Pennyworth)
- [ ] PW-1: Add `"external.workflow_alert"` to `NotificationEventType` union
- [ ] PW-2: Add `EVENT_DEFAULTS` entry routing to `["sms", "push", "feed"]`
- [ ] PW-3: Add `POST /api/events/external` endpoint with bearer auth
- [ ] PW-4: Add rate limiter (max 10/hr per token) to external endpoint
- [ ] PW-5: Write integration test for external endpoint
- [ ] PW-6: Issue bearer token and store in Pennyworth + Modal secret

### Phase 1 — AptoFlow scaffold
- [ ] T1: Run `/new-workflow car_scout` to scaffold directory structure
- [ ] T2: Add new deps to `requirements.txt` + rerun `python bootstrap.py`
- [ ] T3: Create `modal.Secret.from_name("car-scout-secrets")` with all env vars
- [ ] T4: Add `workflows/car_scout` row to `CATALOG.md`

### Phase 2 — Models & state
- [ ] T5: Implement `models.py` with Listing, Score, WorkflowState Pydantic classes
- [ ] T6: Implement `state.py` — load_state, save_state, dedup_and_merge, prune_old
- [ ] T7: Unit tests: roundtrip state serialization, dedup by VIN vs URL, pruning correctness

### Phase 3 — Sources
- [ ] T8: Implement `sources/base.py` AbstractScraper (interface + rate limiter)
- [ ] T9: Implement `sources/cargurus.py` — query + detail scrape, extract CarGurus rating
- [ ] T10: Implement `sources/autotrader.py`
- [ ] T11: Implement `sources/cars_com.py`
- [ ] T12: Implement `sources/dealer.py` — generic Dealer.com JSON endpoint scraper
- [ ] T13: Per-source integration tests against recorded HTML fixtures
- [ ] T14: Add Bright Data fallback logic (retry on 403/challenge)

### Phase 4 — Scoring & unicorn
- [ ] T15: Implement `scoring.py` — market median, percentile, red-flag LLM call, total score
- [ ] T16: Implement `unicorn.py` — matcher with all 5 criteria + safety gates
- [ ] T17: Unit tests: scoring with synthetic comps, unicorn edge cases (just-misses, rate-limited)
- [ ] T18: Golden test — hand-curated 20-listing input → expected score + band output

### Phase 5 — Vehicle history
- [ ] T19: Implement `history.py` — Tier A (listing-page scrape done in source layer) + Tier B API client
- [ ] T20: Implement Tier B fallback: if provider A fails, try provider B
- [ ] T21: Cost tracking — log every Tier B pull with price

### Phase 6 — Delivery
- [ ] T22: Implement `digest.py` — section assembly from state, Jinja2 render, SMTP send
- [ ] T23: Design + write `templates/digest.html.j2` with inline CSS
- [ ] T24: Implement empty-state digest email
- [ ] T25: Implement `notify.py` — POST to Pennyworth external endpoint with bearer auth
- [ ] T26: Unit test: HTML digest rendering with fixture state
- [ ] T27: Unit test: notify.py with mocked Pennyworth endpoint

### Phase 7 — Orchestration & Modal
- [ ] T28: Implement `main.py` with two Modal functions (`scout_cycle`, `assemble_and_send_digest`) wired to cron
- [ ] T29: Add `lib_mount` + image config matching linkedin_reposter pattern
- [ ] T30: Write `README.md` (setup, config, run-local instructions)
- [ ] T31: Write `.env.example`

### Phase 8 — Ship
- [ ] T32: Local dry-run: `python -m workflows.car_scout.main scout --dry-run`
- [ ] T33: Local dry-run: `python -m workflows.car_scout.main digest --dry-run`
- [ ] T34: `modal deploy workflows/car_scout/main.py`
- [ ] T35: Verify first scout run executed successfully (Modal logs)
- [ ] T36: Verify first digest email received at `nick@aptoworks.com`
- [ ] T37: Induce a synthetic unicorn (fixture listing → manual insertion) → verify SMS fires
- [ ] T38: Mark status "Deployed" in `CATALOG.md`

## Safety Configuration

| Setting | Value | Rationale |
|---------|-------|-----------|
| Max iterations per LLM agent loop | N/A | This workflow is pipeline, not agentic |
| Timeout per scout run | 600 sec | 2 hours = 7200s, 10 min ceiling is generous |
| Timeout per digest run | 120 sec | Rendering + SMTP only |
| Rate limit per source | 1 req / 8-15 sec | Respects source terms |
| Cost budget per scout run | $0.20 | ~20 red-flag calls × $0.0001 + occasional VIN history |
| Cost budget per digest run | $0.10 | 1 Sonnet 4 summary call max |
| Max SMS per 24h | 3 | Hard safety valve against runaway scraper |

## Testing Plan

### Unit tests (pytest)

- `test_models.py` — Pydantic validation roundtrips
- `test_scoring.py` — each component in isolation + total calculation
- `test_unicorn.py` — every combination of the 5 criteria, including near-miss cases
- `test_state.py` — dedup, merge, prune, rate-limit window
- `test_digest.py` — HTML rendering with fixture state (assert key substrings present)
- `test_notify.py` — Pennyworth POST with mocked response (success + 401 + 500 cases)

### Integration tests

- `test_cargurus_integration.py` — parse recorded HTML fixture, assert expected listings extracted
- `test_autotrader_integration.py` — same
- `test_cars_com_integration.py` — same
- `test_dealer_integration.py` — same for one representative dealer fixture

### Smoke test

- `python -m workflows.car_scout.main scout --dry-run` runs against live sources but:
  - Limits to 5 listings per source (fast)
  - Does not send email
  - Does not fire SMS
  - Prints full scored output to stdout
- Expected runtime: <45 sec

### Pre-deploy gates

- All unit tests pass
- All integration tests pass against fixtures
- Smoke test runs clean against live sources
- `modal deploy --dry-run` reports no config errors
- Manual review: one real scout output inspected for data-quality sanity

## Modal Deployment Checklist

- [ ] `modal.Secret.from_name("car-scout-secrets")` configured with all env vars
- [ ] `lib_mount` included in app
- [ ] Image has all deps (check `requirements.txt` against `image.pip_install(...)`)
- [ ] No auth/rate-limiting needed (no external endpoints — cron-only)
- [ ] Tested locally with `python -m workflows.car_scout.main scout --dry-run`
- [ ] `modal deploy` succeeds
- [ ] First scheduled scout run appears in Modal logs within 2h of deploy
- [ ] First scheduled digest appears in Nick's inbox the next morning

## Open Questions

1. **DST handling for digest cron**: Modal cron is UTC-only. Pacific Time shifts between PST (UTC-8) and PDT (UTC-7). Options:
   - (A) Hardcode for the active DST season and update twice a year manually
   - (B) Run two crons (one for each DST state) and self-suppress the wrong one at runtime based on current DST
   - (C) Pick a UTC time that's "close enough" year-round (e.g. 14:00 UTC = 06:00 PDT / 07:00 PST) and accept ±1h drift
   - **Recommendation**: (C). 7am in PST is still fine for a morning digest; not worth the complexity of (B).

2. **VIN extraction coverage**: Not every listing page exposes VIN publicly. Falling back to URL-hash dedup is safe but creates a risk of counting the same vehicle twice if a dealer relists with a new URL. Mitigation: fuzzy match on `(year, make, model, trim, mileage, dealer)` as a secondary dedup check.

3. **KBB/Edmunds reference prices**: These are NOT free APIs. V1 alternative: manually curate a small YAML table of fair-market values for the 7 target models × 5 year-buckets, refreshed monthly. Good enough for `low_comp_confidence` fallback.

4. **CarGurus deal rating extraction**: CarGurus renders the badge in client-side JS; may not appear in initial HTML. If scrape misses it, the workflow silently loses 30% of scoring weight on that listing. Need to verify during T9 implementation whether the rating is in `__INITIAL_STATE__` or requires JS rendering (Playwright escalation).

5. **Legal/ToS**: Dealer sites, CarGurus, Autotrader, and Cars.com all have ToS that technically prohibit scraping. Risk is low for low-volume personal use (15-min intervals, <500 req/day), but acknowledge and accept this risk explicitly. Do not distribute or commercialize the workflow.

## V1 → V1.5 → V2 Roadmap

### V1 (ship this weekend, 2026-04-19)
- Scope per this plan
- Dealer sources only (CarGurus + Autotrader + Cars.com + 5 local dealers)
- Pennyworth external endpoint wired
- Runs for ~60 days

### V1.5 (~2 weeks later, 2026-05-03)
- Add Craigslist Seattle source (private party)
- Add Facebook Marketplace source via Bright Data (private party, harder anti-bot)
- Add "seller type" explicit flag in digest cards
- Dashboard check: unicorn false-positive rate after 2 weeks of data → retune thresholds

### V2 (only if workflow outlives Owen's search, unlikely)
- Email action links: Dismiss / Save / Shortlist (requires Modal webhook endpoint)
- Persistent "dismissed VINs" list to drop permanently
- Per-model tuning of unicorn thresholds based on real data

## Retirement

Once Owen has a car, Nick issues `modal app stop aptoflow-car-scout`. Status in `CATALOG.md` changes to `Archived`. State file archived to Drive for reference.
