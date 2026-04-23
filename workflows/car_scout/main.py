"""car_scout — Modal entrypoints + local CLI.

Two scheduled functions:
- scout_cycle: runs every 2h — scrape sources, score, fire unicorn SMS, save state
- assemble_and_send_digest: runs daily at 13:25 UTC (06:25 PT during PDT) —
  builds the morning digest from state and emails it

Local usage (dry-run, no email, no SMS):
    python -m workflows.car_scout.main scout --dry-run
    python -m workflows.car_scout.main digest --dry-run

Deploy:
    modal deploy workflows/car_scout/main.py
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

# Load .env sitting next to this file for local runs. In Modal, env vars come
# from modal.Secret.
_local_env = Path(__file__).parent / ".env"
if _local_env.exists():
    load_dotenv(_local_env)

from lib.logger import get_logger
from lib.marketcheck import MarketCheckClient, MarketCheckConfigError
from lib.scraping import BrightDataClient, BrightDataConfigError

from .digest import (
    DigestSendError,
    assemble_digest,
    compose_subject,
    render_digest_html,
    render_digest_plaintext,
    send_digest,
)
from .models import Listing, Score, WorkflowState
from .notify import PennyworthNotifyError, format_unicorn_sms, notify_unicorn
from .scoring import score_listing
from .sources.base import build_default_scrapers, build_dealer_direct_scraper
from .sources.marketcheck import fetch_all_targets as mc_fetch
from .state import (
    load_state,
    merge_listing,
    prune_old,
    record_sms,
    save_state,
)
from .title_filter import evaluate_title
from .title_vdp import verify_titles_parallel
from .unicorn import evaluate_unicorn

logger = get_logger("car-scout.main")


def _config_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except ValueError:
        return default


def _run_scout_cycle(*, dry_run: bool = False, now: datetime | None = None) -> dict[str, Any]:
    """One scout cycle: fetch, dedupe, score, match unicorns, fire SMS, save."""
    ts = now or datetime.now(timezone.utc)
    state = load_state()
    state.runs_total += 1
    state.last_scout_run = ts

    # Configuration
    zip_code = os.environ.get("SCOUT_ZIP", "98225")
    radius_mi = _config_int("SCOUT_RADIUS_MI", 100)
    budget = _config_int("BUDGET_CEILING_USD", 22000)
    year_floor = _config_int("YEAR_FLOOR", 2015)

    summary: dict[str, Any] = {
        "started": ts.isoformat(),
        "dry_run": dry_run,
        "sources": {},
        "new_listings": 0,
        "price_drops": 0,
        "unicorns_fired": 0,
        "unicorns_skipped_dedupe": 0,
        "unicorns_rate_limited": 0,
        "errors": [],
    }

    # 1. Scrape each source
    try:
        client = BrightDataClient()
    except BrightDataConfigError as exc:
        logger.error("brightdata_config_missing", extra={"error": str(exc)})
        if dry_run:
            # In dry-run we tolerate missing creds; just skip scraping.
            summary["errors"].append(f"bright_data_config: {exc}")
            return summary
        raise

    scrapers = build_default_scrapers(
        client,
        zip_code=zip_code,
        radius_mi=radius_mi,
        budget_ceiling=budget,
        year_floor=year_floor,
    )

    all_new_listings: list[Listing] = []
    all_price_drops: list[Listing] = []

    for scraper in scrapers:
        logger.info("source_starting", extra={"source": scraper.name})
        try:
            result = scraper.scrape()
        except Exception as exc:  # noqa: BLE001 — don't kill the cycle on one source
            logger.error("source_crashed", extra={"source": scraper.name, "error": str(exc)})
            summary["sources"][scraper.name] = {"error": str(exc)}
            summary["errors"].append(f"{scraper.name}: {exc}")
            continue

        summary["sources"][scraper.name] = {
            "listings": len(result.listings),
            "pages": result.pages_fetched,
            "errors": len(result.errors),
        }
        summary["errors"].extend(f"{scraper.name}: {e}" for e in result.errors)

        # 2. Merge into state, tracking new + price-drop vehicles
        for listing in result.listings:
            existing = state.listings.get(listing.dedup_key())
            old_price = existing.price if existing else None
            merged = merge_listing(state, listing, now=ts)
            if existing is None:
                all_new_listings.append(merged)
            elif old_price is not None and merged.price < old_price:
                all_price_drops.append(merged)

    client.close()

    summary["new_listings"] = len(all_new_listings)
    summary["price_drops"] = len(all_price_drops)

    # 3. Hard filter then score every tracked listing (not just newly merged — a
    # stale listing's score can change as comps move)
    scored: list[tuple[Listing, Score]] = []
    for listing in state.listings.values():
        if not _passes_hard_filters(listing):
            continue
        score = score_listing(listing, state, now=ts)
        scored.append((listing, score))

    # 4. Unicorn matching — fire SMS for new-or-just-dropped primary deals
    unicorn_candidates = [
        (l, s) for (l, s) in scored
        if l in all_new_listings or l in all_price_drops
    ]
    for listing, score in unicorn_candidates:
        decision = evaluate_unicorn(listing, score, state, now=ts)
        if decision.rate_limited:
            summary["unicorns_rate_limited"] += 1
            continue
        if decision.already_notified:
            summary["unicorns_skipped_dedupe"] += 1
            continue
        if not decision.is_unicorn:
            continue

        title, body = format_unicorn_sms(
            year=listing.year,
            make=listing.make,
            model=listing.model,
            trim=listing.trim,
            mileage=listing.mileage,
            price=listing.price,
            delta_pct=_delta_pct_from_score(score),
            dealer_or_city=(listing.dealer_name or listing.city or "local"),
            short_url=str(listing.url),
        )

        if dry_run:
            logger.info(
                "unicorn_dry_run",
                extra={"title": title, "body": body, "vin": listing.vin, "url": str(listing.url)},
            )
            summary["unicorns_fired"] += 1
            continue

        try:
            notify_unicorn(
                title=title,
                body=body,
                url=str(listing.url),
                data={
                    "workflow": "car_scout",
                    "vin": listing.vin,
                    "score": score.total,
                    "band": score.band,
                },
            )
            record_sms(state, now=ts)
            state.unicorn_notified.add(listing.dedup_key())
            summary["unicorns_fired"] += 1
        except PennyworthNotifyError as exc:
            logger.error(
                "unicorn_sms_failed",
                extra={"error": str(exc), "url": str(listing.url)},
            )
            summary["errors"].append(f"unicorn-sms: {exc}")

    # 5. Prune + save
    prune_counts = prune_old(state, now=ts)
    summary["pruned"] = prune_counts

    if not dry_run:
        save_state(state)

    logger.info("scout_cycle_complete", extra=summary)
    return summary


# ─── Hard filters ─────────────────────────────────────────────────────────────


# Exterior-color allowlist — Nick 2026-04-17: Owen's family prefers darker
# colors only. Values match what MarketCheck typically returns in
# `exterior_color` / `base_ext_color`. Matching is case-insensitive substring.
ALLOWED_EXTERIOR_COLORS: tuple[str, ...] = (
    "black",
    "gray", "grey",
    "charcoal",
    "graphite",
    "navy", "blue",      # includes "dark blue", "navy blue", "cosmic blue"
    "green",              # Subaru dark greens ("autumn green", "cypress green")
    "brown", "espresso",
    "burgundy", "maroon",
    "crimson",
    "gunmetal",
)

BLOCKED_EXTERIOR_COLORS: tuple[str, ...] = (
    "white",
    "silver",
    "ivory",
    "beige",
    "tan",
    "gold",
    "yellow",
    "orange",
    "red",     # bright red — Nick: "not bright colors"; dark reds like burgundy/crimson handled in allowlist
    "pink",
)
# "pearl" intentionally omitted — Subaru uses names like "Cosmic Blue Pearl"
# and "Crystal Black Silica" where pearl describes a finish, not a base color.
# We trust the allowlist to catch base-color intent.


def _color_ok(listing: Listing) -> bool:
    """Return True if the listing's exterior color passes Nick's dark-color filter.

    Rules:
    - Unknown/missing color → admit (we can't judge — let human verify)
    - Blocklist hit on any color field → reject (white, silver, red, etc.)
    - Allowlist hit on any color field → admit
    - Neither → admit (unusual names default to admit, logged for later tuning)
    """
    fields = [listing.exterior_color]
    # Only exterior_color is currently mapped; base_ext_color can be added if
    # MarketCheck's raw field is surfaced in the future.

    any_known = False
    for raw in fields:
        if not raw:
            continue
        low = raw.lower()
        any_known = True
        for blocked in BLOCKED_EXTERIOR_COLORS:
            if blocked in low:
                return False
        for allowed in ALLOWED_EXTERIOR_COLORS:
            if allowed in low:
                return True

    if not any_known:
        return True  # unknown color is admitted — user verifies

    # Color data present but didn't match allow or block — admit + let user
    # judge. Log so we can tune the lists over time.
    logger.info(
        "color_unmatched_admitted",
        extra={"url": str(listing.url), "exterior_color": listing.exterior_color},
    )
    return True


def _passes_hard_filters(listing: Listing) -> bool:
    """Apply the car_scout hard-filter rules (plan §Filters + title heuristics)."""
    # Title quality — explicit branded rejection + dealer/text blocklist
    title_decision = evaluate_title(listing)
    if not title_decision.passes:
        logger.info(
            "filtered_on_title",
            extra={
                "url": str(listing.url),
                "dealer": listing.dealer_name,
                "reasons": title_decision.reasons,
            },
        )
        return False

    # Color: darker colors only, no white / silver / red / etc.
    if not _color_ok(listing):
        logger.info(
            "filtered_on_color",
            extra={"url": str(listing.url), "exterior_color": listing.exterior_color},
        )
        return False

    # Budget cap
    budget = _config_int("BUDGET_CEILING_USD", 22000)
    if listing.price > budget:
        return False

    # Year floor
    year_floor = _config_int("YEAR_FLOOR", 2015)
    if listing.year < year_floor:
        return False

    # Mileage by tier
    if listing.tier == "primary":
        mileage_cap = _config_int("PRIMARY_MILEAGE_CEILING", 80000)
    else:
        mileage_cap = _config_int("SECONDARY_MILEAGE_CEILING", 110000)
    if listing.mileage > mileage_cap:
        return False

    # Transmission default: auto only (manual override handled via scoring
    # upgrade at a higher layer — here we admit manuals if they're already
    # flagged with a strong deal score, but our hard filter admits both and
    # lets the unicorn matcher / digest decide).
    return True


def _verify_pending_titles(state: WorkflowState, *, dry_run: bool) -> dict[str, Any]:
    """VDP-fetch every unverified listing via parallel Bright Data requests.

    - Only fetches listings that survived the cheap blocklist + color filters
    - Branded-verdict listings are REMOVED from state (permanent rejection)
    - Clean + unknown verdicts cached forever per dedup_key
    - Returns summary counts for digest run metadata
    """
    summary = {"checked": 0, "branded": 0, "clean": 0, "unknown": 0, "errors": 0}

    # Narrow the candidate set before incurring fetch costs. Only verify
    # listings that would otherwise make it to scoring.
    candidates = [
        l for l in state.listings.values()
        if evaluate_title(l).passes
        and _color_ok(l)
        and l.dedup_key() not in state.title_verifications
    ]

    if not candidates:
        return summary

    # Map URL → listing for lookup after parallel fetch
    url_to_listing = {str(l.url): l for l in candidates}

    try:
        results = verify_titles_parallel(url_to_listing.keys())
    except BrightDataConfigError as exc:
        logger.warning(
            "vdp_verify_skipped_no_brightdata",
            extra={"error": str(exc), "candidates": len(candidates)},
        )
        return summary

    for url, result in results.items():
        listing = url_to_listing[url]
        summary["checked"] += 1

        if result.fetch_error:
            summary["errors"] += 1
            # 404 → dead listing (sold/delisted); evict from state entirely.
            # Other errors (timeouts, 5xx) → transient; leave listing but
            # DON'T cache a verdict so the next run retries the VDP fetch.
            if "404" in result.fetch_error:
                del state.listings[listing.dedup_key()]
                logger.info(
                    "vdp_dead_link_evicted",
                    extra={"url": url, "dealer": listing.dealer_name},
                )
            # else: no cache entry → next digest run re-tries this URL
            continue

        state.title_verifications[listing.dedup_key()] = result.verdict

        if result.verdict == "branded":
            summary["branded"] += 1
            del state.listings[listing.dedup_key()]
            logger.info(
                "vdp_branded_rejected",
                extra={
                    "url": url,
                    "dealer": listing.dealer_name,
                    "phrase": result.matched_branded_phrase,
                },
            )
        elif result.verdict == "clean":
            summary["clean"] += 1
            if listing.title_status == "unknown":
                listing.title_status = "clean"
        else:
            summary["unknown"] += 1

    logger.info("vdp_verify_batch_complete", extra=summary)
    return summary


def _delta_pct_from_score(score: Score) -> float:
    """Reverse-engineer approx % below market from the market-delta component.

    Not exact (bands are coarse), but fine for human-readable SMS copy.
    Maps the 0-100 component back to a reasonable mid-range percentage.
    """
    mapping = {
        100.0: 20.0,
        85.0: 14.0,
        65.0: 7.0,
        50.0: 0.0,
        25.0: -10.0,
        5.0: -20.0,
    }
    return mapping.get(score.market_delta_component, 0.0)


# ─── Digest orchestration ────────────────────────────────────────────────────


def _run_digest(*, dry_run: bool = False, now: datetime | None = None) -> dict[str, Any]:
    """Fetch MarketCheck, merge into state, assemble digest, send email.

    Called by both AM and PM cron functions — the function itself is stateless,
    the Modal volume handles persistence.
    """
    ts = now or datetime.now(timezone.utc)
    state = load_state()

    zip_code = os.environ.get("SCOUT_ZIP", "98225")
    radius_mi = _config_int("SCOUT_RADIUS_MI", 100)
    year_floor = _config_int("YEAR_FLOOR", 2015)
    budget = _config_int("BUDGET_CEILING_USD", 22000)

    mc_fetch_summary: dict[str, Any] = {"listings": 0, "errors": []}

    # 1. Fetch fresh inventory from MarketCheck and merge into state
    try:
        with MarketCheckClient() as mc:
            result = mc_fetch(
                mc,
                zip_code=zip_code,
                radius_mi=radius_mi,
                year_floor=year_floor,
                budget_ceiling=budget,
            )
    except MarketCheckConfigError as exc:
        logger.error("marketcheck_config_missing", extra={"error": str(exc)})
        if not dry_run:
            raise
        mc_fetch_summary["errors"].append(f"config: {exc}")
        result = None
    else:
        mc_fetch_summary["listings"] = len(result.listings)
        mc_fetch_summary["errors"] = list(result.errors)
        for listing in result.listings:
            merge_listing(state, listing, now=ts)

    # 1b. Pull Subaru trade-ins from local non-Subaru dealers that MarketCheck
    #     routinely misses (Bellingham Ford, Toyota of Bellingham, Audi
    #     Bellingham). Dedupes against MarketCheck listings by VIN via merge_listing.
    dealer_direct_summary: dict[str, Any] = {"listings": 0, "errors": []}
    try:
        with BrightDataClient() as bd_client:
            try:
                dd_result = build_dealer_direct_scraper(bd_client).scrape()
            except Exception as exc:  # noqa: BLE001 — never kill the digest cycle
                logger.error("dealer_direct_scrape_crashed", extra={"error": str(exc)})
                dealer_direct_summary["errors"].append(f"scrape: {exc}")
            else:
                dealer_direct_summary["listings"] = len(dd_result.listings)
                dealer_direct_summary["errors"] = list(dd_result.errors)
                dealer_direct_summary["pages_fetched"] = dd_result.pages_fetched
                for listing in dd_result.listings:
                    merge_listing(state, listing, now=ts)
    except BrightDataConfigError as exc:
        logger.warning("dealer_direct_brightdata_missing", extra={"error": str(exc)})
        dealer_direct_summary["errors"].append(f"brightdata_config: {exc}")

    # 2. VDP title verification for listings that passed cheap filters but
    #    aren't yet cached. One HTTP call per unverified VIN, result persists
    #    forever. Branded listings get removed from state entirely.
    vdp_summary = _verify_pending_titles(state, dry_run=dry_run)

    # 3. Re-score every tracked listing that passes hard filters
    scored: list[tuple[Listing, Score]] = []
    for listing in state.listings.values():
        if not _passes_hard_filters(listing):
            continue
        scored.append((listing, score_listing(listing, state, now=ts)))

    payload = assemble_digest(
        scored,
        state,
        now=ts,
        sources_checked=len({l.source for l, _ in scored}),
    )

    html = render_digest_html(payload, now=ts)
    plaintext = render_digest_plaintext(payload)
    subject = compose_subject(payload, now=ts)

    summary: dict[str, Any] = {
        "started": ts.isoformat(),
        "dry_run": dry_run,
        "marketcheck": mc_fetch_summary,
        "dealer_direct": dealer_direct_summary,
        "vdp_title_verification": vdp_summary,
        "top_picks": len(payload.top_picks),
        "new_today": len(payload.new_today),
        "price_drops": len(payload.price_drops),
        "empty": payload.empty,
        "subject": subject,
    }
    result = summary

    if dry_run:
        result["html_length"] = len(html)
        result["plaintext_preview"] = plaintext[:400]
        logger.info("digest_dry_run", extra=result)
        return result

    try:
        send_digest(html=html, plaintext=plaintext, subject=subject)
    except DigestSendError as exc:
        logger.error("digest_send_failed", extra={"error": str(exc)})
        result["error"] = str(exc)
        return result

    # Record top picks in state for 7-day dedupe
    for card in payload.top_picks:
        state.top_picks_last_7_days[card.url] = ts
    state.last_digest_sent = ts
    save_state(state)

    logger.info("digest_sent", extra=result)
    return result


# ─── Modal wiring ────────────────────────────────────────────────────────────
#
# Modal's @app.function decorator requires module-scope definitions. The app
# object is built at import time; this is safe because modal is a required
# dependency in requirements.txt.

import modal  # noqa: E402

app = modal.App("aptoflow-car-scout")

_image = (
    modal.Image.debian_slim(python_version="3.12")
    .pip_install_from_requirements(
        str(Path(__file__).parent.parent.parent / "requirements.txt")
    )
    .add_local_python_source("lib", "workflows")
)

_secrets = [modal.Secret.from_name("car-scout-secrets")]
_volume = modal.Volume.from_name("car-scout-state", create_if_missing=True)


# Morning digest — 13:30 UTC ≈ 06:30 PT (PDT) / 05:30 PT (PST)
# Timeout 900s: first cold-start run does VDP-verify on ~200 new URLs (~5-6 min
# with 10 parallel workers). Steady state after day 1 is mostly cache hits <30s.
@app.function(
    image=_image,
    secrets=_secrets,
    volumes={"/data": _volume},
    schedule=modal.Cron("30 13 * * *"),
    timeout=900,
)
def digest_cron_am():
    result = _run_digest()
    _volume.commit()
    return result


# Evening digest — 01:30 UTC ≈ 18:30 PT (PDT) / 17:30 PT (PST)
@app.function(
    image=_image,
    secrets=_secrets,
    volumes={"/data": _volume},
    schedule=modal.Cron("30 1 * * *"),
    timeout=900,
)
def digest_cron_pm():
    result = _run_digest()
    _volume.commit()
    return result


# V1.1 — Bright Data scout every 2h for real-time unicorn SMS. Commented out
# until Autotrader + Cars.com scrapers land (CarGurus blocked via robots.txt).
# The _run_scout_cycle function + underlying modules remain fully wired and
# tested; only the Modal schedule is absent.
#
# @app.function(
#     image=_image,
#     secrets=_secrets,
#     volumes={"/data": _volume},
#     schedule=modal.Cron("0 */2 * * *"),
#     timeout=900,
# )
# def scout_cron():
#     result = _run_scout_cycle()
#     _volume.commit()
#     return result


# ─── Local CLI ───────────────────────────────────────────────────────────────


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="car_scout local runner")
    parser.add_argument(
        "mode",
        choices=("scout", "digest"),
        help="Which cycle to run locally",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Skip side effects (no SMS, no email, no state write)",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv or sys.argv[1:])
    if args.mode == "scout":
        result = _run_scout_cycle(dry_run=args.dry_run)
    else:
        result = _run_digest(dry_run=args.dry_run)

    import json
    print(json.dumps(result, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
