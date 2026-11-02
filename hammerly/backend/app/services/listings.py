from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .. import clock
from ..config import get_settings
from ..models import Listing, Passport, User
from . import ledger
from .bids import BidError


async def create_draft(session: AsyncSession, seller: User, data: dict) -> Listing:
    fmt = data["format"]
    listing = Listing(
        seller_id=seller.id, title=data["title"].strip(), description=data.get("description", ""),
        category=data["category"], condition_grade=data.get("condition_grade"), format=fmt,
        start_price_cents=data["start_price_cents"], reserve_cents=data.get("reserve_cents", 0),
        shipping_cents=data.get("shipping_cents", get_settings().shipping_fee_cents),
        dutch_floor_cents=data.get("dutch_floor_cents", 0),
        dutch_drop_cents_per_min=data.get("dutch_drop_cents_per_min", 0),
        sealed_pricing=data.get("sealed_pricing", "second"), images=data.get("images", []),
        status="draft", current_price_cents=data["start_price_cents"], created_at=clock.now())
    session.add(listing)
    await session.flush()
    session.add(Passport(listing_id=listing.id, provenance=data.get("provenance", ""),
                         authentication_status="none", photo_check="pending"))
    await session.flush()
    return listing


def _validate(listing: Listing, duration_s: int):
    if listing.start_price_cents <= 0:
        raise BidError("bad_price", "Start price must be above zero.")
    if duration_s < 30:
        raise BidError("bad_duration", "An auction must run at least 30 seconds.")
    if listing.format == "dutch":
        if not (0 < listing.dutch_floor_cents < listing.start_price_cents):
            raise BidError("bad_dutch", "A Dutch auction needs a floor below the start price.")
        if listing.dutch_drop_cents_per_min <= 0:
            raise BidError("bad_dutch", "A Dutch auction needs a price drop per minute.")
    if listing.reserve_cents and listing.reserve_cents < listing.start_price_cents and listing.format != "dutch":
        raise BidError("bad_reserve", "Reserve cannot be below the start price.")


async def publish(session: AsyncSession, seller: User, listing: Listing, duration_s: int,
                  starts_at: datetime | None = None) -> Listing:
    if listing.seller_id != seller.id:
        raise BidError("forbidden", "This is not your listing.", 403)
    if listing.status != "draft":
        raise BidError("bad_state", "Only drafts can be published.", 409)
    if not seller.is_seller:
        raise BidError("forbidden", "Become a seller first.", 403)
    _validate(listing, duration_s)
    now = clock.now()
    start = starts_at if starts_at and starts_at > now else now
    listing.starts_at = start
    listing.ends_at = start + timedelta(seconds=duration_s)
    listing.original_ends_at = listing.ends_at
    if start > now:
        listing.status = "scheduled"
    else:
        await go_live(session, listing)
    await session.flush()
    return listing


async def go_live(session: AsyncSession, listing: Listing):
    listing.status = "live"
    seller = await session.get(User, listing.seller_id)
    await ledger.append(session, listing.id, "open", seller.display_name,
                        repr_=f"open:{listing.format}:{listing.start_price_cents}:{listing.ends_at.isoformat()}",
                        user_id=seller.id)
