"""Daily digest email assembly + SMTP delivery for car_scout.

Reads the WorkflowState built up by scout_cycle, groups surfaced listings
into three sections (Top Picks, New Today, Price Drops), renders the
HTML + plain-text versions, and sends via SMTP.

Section rules (from plan):
- Top Picks: highest-scoring listings from the last 24h, max 3 (score >= 70)
- New Today: listings with first_seen in the last 24h (anything scored >= good)
- Price Drops: listings with a price decrease since the last digest (>= 5%)
"""

from __future__ import annotations

import os
import smtplib
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from typing import Iterable

from jinja2 import Environment, FileSystemLoader, StrictUndefined, select_autoescape

from lib.logger import get_logger

from .models import Listing, Score, ScoreBand, WorkflowState

logger = get_logger("car-scout.digest")

TOP_PICK_MAX = 3
TOP_PICK_MIN_SCORE = 70.0
NEW_TODAY_WINDOW_HOURS = 24
PRICE_DROP_THRESHOLD = 0.05

TEMPLATES_DIR = Path(__file__).parent / "templates"


@dataclass
class DigestCard:
    """Render-ready summary of one listing for the email template."""

    url: str
    photo: str | None
    year: int
    make: str
    model: str
    trim: str | None
    price: int
    old_price: int | None
    mileage: int
    deal_score: int
    deal_band: str
    cargurus_rating: str | None
    reasoning: str
    accident_count: int | None
    owner_count: int | None
    city: str | None
    state: str | None
    dealer_name: str | None


@dataclass
class DigestPayload:
    """Structured payload passed to the HTML renderer."""

    top_picks: list[DigestCard] = field(default_factory=list)
    new_today: list[DigestCard] = field(default_factory=list)
    price_drops: list[DigestCard] = field(default_factory=list)
    sources_checked: int = 0
    listings_in_state: int = 0
    last_scout_local: str = ""

    @property
    def empty(self) -> bool:
        return not (self.top_picks or self.new_today or self.price_drops)


def _to_card(
    listing: Listing,
    score: Score,
    *,
    old_price: int | None = None,
) -> DigestCard:
    return DigestCard(
        url=str(listing.url),
        photo=str(listing.photos[0]) if listing.photos else None,
        year=listing.year,
        make=listing.make,
        model=listing.model,
        trim=listing.trim,
        price=listing.price,
        old_price=old_price,
        mileage=listing.mileage,
        deal_score=int(round(score.total)),
        deal_band=score.band,
        cargurus_rating=listing.cargurus_rating,
        reasoning=score.reasoning,
        accident_count=listing.accident_count,
        owner_count=listing.owner_count,
        city=listing.city,
        state=listing.state,
        dealer_name=listing.dealer_name,
    )


def _recent_price_drop(listing: Listing, cutoff: datetime) -> int | None:
    """Return the old price if this listing dropped >= 5% since cutoff. Else None."""
    if len(listing.price_history) < 2:
        return None
    history = sorted(listing.price_history, key=lambda o: o.timestamp)
    # Most recent observation is listing.price; find the first obs before cutoff
    before_cutoff = [o for o in history if o.timestamp < cutoff]
    if not before_cutoff:
        return None
    # Price at the cutoff time (most recent observation before the cutoff)
    old = before_cutoff[-1].price
    if old <= 0:
        return None
    drop_pct = (old - listing.price) / old
    if drop_pct >= PRICE_DROP_THRESHOLD:
        return old
    return None


def assemble_digest(
    scored_listings: Iterable[tuple[Listing, Score]],
    state: WorkflowState,
    *,
    now: datetime | None = None,
    sources_checked: int = 0,
    timezone_label: str = "PT",
) -> DigestPayload:
    """Build sections from scored listings + state bookkeeping.

    `scored_listings` must be an iterable of (Listing, Score) pairs — usually
    produced by the scout cycle. Listings scored `pass` are dropped silently.
    Secondary-tier listings are included only if they scored `good` or better.
    """
    ts = now or datetime.now(timezone.utc)
    cutoff_new_today = ts - timedelta(hours=NEW_TODAY_WINDOW_HOURS)

    # Flatten + filter + dedupe by URL (same listing might show up multiple
    # times across scout cycles within 24h; we want the latest score)
    latest: dict[str, tuple[Listing, Score]] = {}
    for listing, score in scored_listings:
        if score.band == "pass":
            continue
        if listing.tier == "secondary" and score.band == "fair":
            continue
        latest[str(listing.url)] = (listing, score)

    # Top Picks: top-scoring (score >= 70), dedupe against top_picks_last_7_days
    eligible_top = [
        (l, s)
        for (l, s) in latest.values()
        if s.total >= TOP_PICK_MIN_SCORE
        and str(l.url) not in state.top_picks_last_7_days
    ]
    eligible_top.sort(key=lambda pair: pair[1].total, reverse=True)
    top_pick_cutoff = state.last_digest_sent or (ts - timedelta(hours=NEW_TODAY_WINDOW_HOURS))
    top_picks = [
        _to_card(l, s, old_price=_recent_price_drop(l, top_pick_cutoff))
        for (l, s) in eligible_top[:TOP_PICK_MAX]
    ]

    top_pick_urls = {card.url for card in top_picks}

    # New Today: first_seen within 24h, not already in top picks
    new_today_pairs = [
        (l, s)
        for (l, s) in latest.values()
        if l.first_seen >= cutoff_new_today and str(l.url) not in top_pick_urls
    ]
    new_today_pairs.sort(key=lambda pair: pair[1].total, reverse=True)
    new_today = [_to_card(l, s) for (l, s) in new_today_pairs]

    # Price Drops: had a >= 5% drop since last digest; exclude from top picks
    price_drop_cutoff = state.last_digest_sent or (ts - timedelta(hours=NEW_TODAY_WINDOW_HOURS))
    drop_pairs = []
    for (l, s) in latest.values():
        if str(l.url) in top_pick_urls:
            continue
        old = _recent_price_drop(l, price_drop_cutoff)
        if old is not None:
            drop_pairs.append((l, s, old))
    drop_pairs.sort(key=lambda tup: tup[1].total, reverse=True)
    price_drops = [_to_card(l, s, old_price=old) for (l, s, old) in drop_pairs]

    last_scout_local = (
        state.last_scout_run.astimezone(timezone.utc).strftime(f"%Y-%m-%d %H:%M UTC")
        if state.last_scout_run
        else "never"
    )

    return DigestPayload(
        top_picks=top_picks,
        new_today=new_today,
        price_drops=price_drops,
        sources_checked=sources_checked,
        listings_in_state=len(state.listings),
        last_scout_local=last_scout_local,
    )


# ─── Rendering ────────────────────────────────────────────────────────────────


def _build_jinja_env() -> Environment:
    env = Environment(
        loader=FileSystemLoader(str(TEMPLATES_DIR)),
        autoescape=select_autoescape(["html", "htm"]),
        undefined=StrictUndefined,
    )
    env.globals["render_card"] = _render_card_html
    return env


def _render_card_html(card: DigestCard, *, emphasize: bool = False, show_old_price: bool = False) -> str:
    """Inline-CSS card snippet for one listing. Jinja calls this via globals."""
    trim = f" {card.trim}" if card.trim else ""
    photo_block = (
        f'<img src="{card.photo}" alt="" width="120" height="90" '
        f'style="display:block;border-radius:6px;object-fit:cover;">'
        if card.photo
        else ""
    )

    history_parts: list[str] = []
    if card.accident_count is not None:
        history_parts.append(
            f"{card.accident_count} acc" if card.accident_count else "0 acc"
        )
    if card.owner_count is not None:
        history_parts.append(f"{card.owner_count} own")
    history_line = " · ".join(history_parts) if history_parts else "history: unknown"

    location = (
        f"{card.city}, {card.state}" if card.city and card.state
        else (card.dealer_name or "")
    )

    rating_badge = ""
    if card.cargurus_rating:
        color = {"Great": "#2e7d32", "Good": "#4caf50", "Fair": "#9e9e9e", "High": "#f57c00", "Overpriced": "#c62828"}.get(
            card.cargurus_rating, "#9e9e9e"
        )
        rating_badge = (
            f'<span style="display:inline-block;padding:2px 8px;border-radius:10px;'
            f'background:{color};color:#fff;font-size:11px;font-weight:600;">'
            f'CarGurus: {card.cargurus_rating}</span>'
        )

    score_badge = (
        f'<span style="display:inline-block;padding:2px 8px;border-radius:10px;'
        f'background:#1a1a1a;color:#fff;font-size:11px;font-weight:700;">'
        f'Score {card.deal_score}</span>'
    )

    price_line = f'<div style="font-size:18px;font-weight:700;">${card.price:,}</div>'
    if show_old_price and card.old_price:
        price_line = (
            f'<div style="font-size:18px;font-weight:700;">${card.price:,} '
            f'<span style="font-size:13px;color:#888;text-decoration:line-through;font-weight:400;">'
            f'${card.old_price:,}</span></div>'
        )

    border = "border:2px solid #b8860b;" if emphasize else "border:1px solid #e0e0e0;"

    return f"""
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"
       style="{border}border-radius:6px;overflow:hidden;">
<tr>
<td width="130" valign="top" style="padding:10px;">{photo_block}</td>
<td valign="top" style="padding:10px 10px 10px 0;">
<div style="font-size:15px;font-weight:600;">{card.year} {card.make} {card.model}{trim}</div>
<div style="font-size:12px;color:#666;margin-top:2px;">{card.mileage:,} miles · {history_line}{' · ' + location if location else ''}</div>
{price_line}
<div style="margin-top:6px;">{score_badge}{' ' + rating_badge if rating_badge else ''}</div>
<div style="font-size:12px;color:#555;margin-top:6px;font-style:italic;">{card.reasoning}</div>
<div style="margin-top:8px;"><a href="{card.url}" style="display:inline-block;padding:6px 12px;background:#1a73e8;color:#fff;text-decoration:none;border-radius:4px;font-size:12px;font-weight:600;">View listing</a></div>
</td>
</tr>
</table>
"""


def render_digest_html(payload: DigestPayload, *, now: datetime | None = None) -> str:
    """Render the HTML digest from a DigestPayload."""
    ts = now or datetime.now(timezone.utc)
    env = _build_jinja_env()
    tpl = env.get_template("digest.html.j2")

    summary_parts = []
    if payload.top_picks:
        summary_parts.append(f"{len(payload.top_picks)} top pick{'s' if len(payload.top_picks) != 1 else ''}")
    if payload.new_today:
        summary_parts.append(f"{len(payload.new_today)} new")
    if payload.price_drops:
        summary_parts.append(f"{len(payload.price_drops)} price drop{'s' if len(payload.price_drops) != 1 else ''}")
    if not summary_parts:
        summary_parts.append("no new matches")

    return tpl.render(
        date_long=ts.strftime("%A, %B %d, %Y"),
        date_short=ts.strftime("%a %m/%d"),
        summary_line=" · ".join(summary_parts),
        top_picks=payload.top_picks,
        new_today=payload.new_today,
        price_drops=payload.price_drops,
        empty=payload.empty,
        sources_checked=payload.sources_checked,
        listings_in_state=payload.listings_in_state,
        last_scout_local=payload.last_scout_local,
    )


def render_digest_plaintext(payload: DigestPayload) -> str:
    """Plain-text fallback for clients that strip HTML."""
    lines = ["Car Scout Digest", ""]

    def _add_card(card: DigestCard) -> None:
        trim = f" {card.trim}" if card.trim else ""
        old = f" (was ${card.old_price:,})" if card.old_price else ""
        lines.append(f"  {card.year} {card.make} {card.model}{trim} — ${card.price:,}{old}")
        lines.append(f"  {card.mileage:,} mi · score {card.deal_score} ({card.deal_band})")
        lines.append(f"  {card.reasoning}")
        lines.append(f"  {card.url}")
        lines.append("")

    if payload.top_picks:
        lines.append("## TOP PICKS")
        for c in payload.top_picks:
            _add_card(c)
    if payload.new_today:
        lines.append(f"## NEW TODAY ({len(payload.new_today)})")
        for c in payload.new_today:
            _add_card(c)
    if payload.price_drops:
        lines.append(f"## PRICE DROPS ({len(payload.price_drops)})")
        for c in payload.price_drops:
            _add_card(c)
    if payload.empty:
        lines.append("No new matches in the last 24h. Still watching.")

    return "\n".join(lines)


# ─── Delivery ────────────────────────────────────────────────────────────────


class DigestSendError(Exception):
    """Raised when SMTP send fails."""


def send_digest(
    html: str,
    plaintext: str,
    *,
    subject: str,
    sender: str | None = None,
    recipient: str | None = None,
    smtp_host: str | None = None,
    smtp_port: int | None = None,
    smtp_user: str | None = None,
    smtp_password: str | None = None,
) -> None:
    """Send the digest via SMTP. Defaults to env vars for all config."""
    sender = sender or os.environ.get("CAR_SCOUT_DIGEST_FROM", os.environ.get("APTOFLOW_SMTP_USERNAME", ""))
    recipient = recipient or os.environ.get("CAR_SCOUT_DIGEST_TO", "")
    smtp_host = smtp_host or os.environ.get("APTOFLOW_SMTP_HOST", "smtp.gmail.com")
    smtp_port = smtp_port or int(os.environ.get("APTOFLOW_SMTP_PORT", "587"))
    smtp_user = smtp_user or os.environ.get("APTOFLOW_SMTP_USERNAME", "")
    smtp_password = smtp_password or os.environ.get("APTOFLOW_SMTP_PASSWORD", "")

    missing = [
        name
        for name, val in (
            ("sender", sender),
            ("recipient", recipient),
            ("smtp_host", smtp_host),
            ("smtp_user", smtp_user),
            ("smtp_password", smtp_password),
        )
        if not val
    ]
    if missing:
        raise DigestSendError(f"Missing SMTP config: {', '.join(missing)}")

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = sender
    msg["To"] = recipient
    msg["Reply-To"] = recipient  # replies go to Nick, not alfred@

    msg.attach(MIMEText(plaintext, "plain", "utf-8"))
    msg.attach(MIMEText(html, "html", "utf-8"))

    try:
        with smtplib.SMTP(smtp_host, smtp_port, timeout=30) as server:
            server.starttls()
            server.login(smtp_user, smtp_password)
            server.sendmail(sender, [recipient], msg.as_string())
    except (smtplib.SMTPException, OSError) as exc:
        raise DigestSendError(f"SMTP send failed: {exc}") from exc

    logger.info(
        "digest_sent",
        extra={"recipient": recipient, "subject": subject, "html_len": len(html)},
    )


def compose_subject(payload: DigestPayload, now: datetime | None = None) -> str:
    ts = now or datetime.now(timezone.utc)
    date_short = ts.strftime("%a %m/%d")
    if payload.empty:
        return f"🚗 Car Scout — still watching (no new matches {date_short})"
    count = len(payload.new_today) + len(payload.price_drops) + len(payload.top_picks)
    tp = len(payload.top_picks)
    return f"🚗 Car Scout — {count} listing{'s' if count != 1 else ''}, {tp} top pick{'s' if tp != 1 else ''} ({date_short})"
