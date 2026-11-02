"""Shield: rule-based detection of self-dealing, linked accounts, and bid rings.

These are transparent heuristics, not a trained model. Every flag keeps its evidence in `details`
so a reviewer can see why it fired. Flags with score >= 0.8 pause the seller payout until cleared.
"""
from __future__ import annotations

from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .. import clock
from ..models import LedgerEntry, Listing, ShieldFlag, User, UserDevice
from .bids import BidError
from .notify import notify

HIGH = 0.8


async def _devices(session, user_id: int) -> tuple[set[str], set[str]]:
    rows = (await session.execute(select(UserDevice).where(UserDevice.user_id == user_id))).scalars().all()
    return {r.device_id for r in rows if r.device_id}, {r.ip for r in rows if r.ip}


async def check_participant(session: AsyncSession, listing: Listing, user: User):
    """Hard block: a bidder who shares a device with the seller is bidding against themselves."""
    bd, _ = await _devices(session, user.id)
    sd, _ = await _devices(session, listing.seller_id)
    if bd & sd:
        await _flag(session, listing, user.id, "seller_device_link", 0.95,
                    {"shared_devices": len(bd & sd)})
        raise BidError("linked_to_seller", "This account appears linked to the seller, so it cannot bid here.", 403)


async def _flag(session, listing, user_id, kind, score, details) -> ShieldFlag | None:
    exists = (await session.execute(select(ShieldFlag.id).where(
        ShieldFlag.listing_id == listing.id, ShieldFlag.user_id == user_id, ShieldFlag.kind == kind))).first()
    if exists:
        return None
    f = ShieldFlag(listing_id=listing.id, user_id=user_id, kind=kind, score=score, details=details,
                   created_at=clock.now())
    session.add(f)
    if score >= HIGH:
        listing.settlement_hold = True
        for a in (await session.execute(select(User).where(User.is_admin.is_(True)))).scalars():
            await notify(session, a.id, "shield", "Shield flagged a listing", f"{kind} on listing {listing.id}", "/admin")
    await session.flush()
    return f


async def after_bid(session: AsyncSession, listing: Listing, user: User):
    entries = (await session.execute(select(LedgerEntry).where(
        LedgerEntry.listing_id == listing.id, LedgerEntry.kind.in_(["bid", "proxy"]))
        .order_by(LedgerEntry.seq))).scalars().all()
    bidder_ids = {e.user_id for e in entries if e.user_id}

    # 1. Bid ring: two accounts raise each other repeatedly and nobody else bids.
    if len(entries) >= 6 and len(bidder_ids) == 2:
        tail = [e.user_id for e in entries[-6:]]
        switches = sum(1 for a, b in zip(tail, tail[1:]) if a != b)
        if switches >= 4:
            await _flag(session, listing, None, "bid_ring", 0.85,
                        {"bidders": sorted(bidder_ids), "recent_switches": switches, "bids": len(entries)})

    # 2. Linked bidders: different accounts on the same device.
    mine, my_ips = await _devices(session, user.id)
    for other in bidder_ids - {user.id}:
        od, oips = await _devices(session, other)
        if mine & od:
            await _flag(session, listing, user.id, "linked_bidders", 0.8,
                        {"other_user": other, "shared_devices": len(mine & od)})
        elif my_ips & oips:
            await _flag(session, listing, user.id, "shared_network", 0.4,
                        {"other_user": other, "shared_networks": len(my_ips & oips)})

    # 3. Same network as the seller (can be a household, so it never blocks).
    _, seller_ips = await _devices(session, listing.seller_id)
    if my_ips & seller_ips:
        await _flag(session, listing, user.id, "seller_network_link", 0.5, {"shared_networks": len(my_ips & seller_ips)})

    # 4. Brand-new account placing a large bid.
    if clock.now() - user.created_at < timedelta(hours=1) and listing.current_price_cents >= 50_000:
        await _flag(session, listing, user.id, "new_account_high_value", 0.4,
                    {"account_age_minutes": int((clock.now() - user.created_at).total_seconds() // 60)})


async def review(session: AsyncSession, flag_id: int, status: str) -> ShieldFlag:
    f = await session.get(ShieldFlag, flag_id)
    if f is None or status not in {"cleared", "confirmed"}:
        raise BidError("not_found", "Flag not found.", 404)
    f.status = status
    listing = await session.get(Listing, f.listing_id)
    open_high = (await session.execute(select(ShieldFlag.id).where(
        ShieldFlag.listing_id == f.listing_id, ShieldFlag.status == "open", ShieldFlag.score >= HIGH))).first()
    listing.settlement_hold = bool(open_high)
    await session.flush()
    return f
