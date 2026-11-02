"""Turn models into JSON-ready dicts. Sealed amounts and reserve prices are never exposed."""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from . import clock
from .models import LedgerEntry, Listing, Passport, ProxyBid, Squad, User, Watch
from .services import atlas, bids, ledger, passport, squads


def user_public(u: User) -> dict:
    return {"id": u.id, "name": u.display_name, "trust_score": u.trust_score, "ratings_count": u.ratings_count,
            "verified": u.seller_verified, "pro": u.is_pro}


def user_private(u: User) -> dict:
    return {**user_public(u), "email": u.email, "is_seller": u.is_seller, "is_admin": u.is_admin,
            "calm_mode": u.calm_mode, "daily_cap_cents": u.daily_cap_cents}


def media_url(name: str) -> str:
    return name if name.startswith(("http", "/")) else f"/media/{name}"


def card(l: Listing) -> dict:
    now = clock.now()
    return {
        "id": l.id, "title": l.title, "category": l.category, "format": l.format, "status": l.status,
        "image": media_url(l.images[0]) if l.images else None, "condition_grade": l.condition_grade,
        "price_cents": bids.display_price(l, now), "start_price_cents": l.start_price_cents,
        "min_next_cents": bids.min_next(l), "bid_count": l.bid_count, "shipping_cents": l.shipping_cents,
        "ends_at": l.ends_at, "starts_at": l.starts_at,
        "seconds_left": max(0, int((l.ends_at - now).total_seconds())) if l.ends_at and l.status == "live" else None,
        "reserve_met": (l.current_price_cents >= l.reserve_cents) if l.format == "english" and l.reserve_cents else None,
        "dutch": {"floor_cents": l.dutch_floor_cents, "drop_cents_per_min": l.dutch_drop_cents_per_min}
        if l.format == "dutch" else None,
        "sealed_pricing": l.sealed_pricing if l.format == "sealed" else None,
        "room_id": l.room_id, "seller": user_public(l.seller), "server_time": now,
    }


async def leader_alias(session: AsyncSession, l: Listing) -> str | None:
    if l.leader_squad_id:
        sq = await session.get(Squad, l.leader_squad_id)
        return f"Squad {sq.name}" if sq else None
    if l.leader_user_id:
        return (await session.get(User, l.leader_user_id)).display_name
    return None


async def detail(session: AsyncSession, l: Listing, viewer: User | None) -> dict:
    out = card(l)
    p = await session.get(Passport, l.id)
    closed = l.status in {"sold", "unsold", "closed"}
    entries = (await session.execute(select(LedgerEntry).where(LedgerEntry.listing_id == l.id)
                                     .order_by(LedgerEntry.seq.desc()).limit(30))).scalars().all()
    comps = await atlas.comps(session, l.category, l.title, exclude_listing=l.id)
    out.update({
        "description": l.description, "images": [media_url(i) for i in l.images],
        "leader_alias": await leader_alias(session, l), "extensions_used": l.extensions_used,
        "original_ends_at": l.original_ends_at, "winner_user_id": l.winner_user_id if closed else None,
        "final_price_cents": l.final_price_cents,
        "passport": passport.view(l, p, l.seller) if p else None,
        "atlas": comps,
        "ledger": [ledger.public_view(e, closed) for e in reversed(entries)],
        "settlement_hold": l.settlement_hold if viewer and (viewer.is_admin or viewer.id == l.seller_id) else None,
    })
    if viewer:
        proxy = (await session.execute(select(ProxyBid).where(ProxyBid.listing_id == l.id,
                                                              ProxyBid.user_id == viewer.id))).scalar_one_or_none()
        sealed = (await session.execute(select(LedgerEntry.id).where(
            LedgerEntry.listing_id == l.id, LedgerEntry.kind == "sealed", LedgerEntry.user_id == viewer.id))).first()
        w = (await session.execute(select(Watch).where(Watch.listing_id == l.id, Watch.user_id == viewer.id))).scalar_one_or_none()
        sq = (await session.execute(select(Squad.id).where(Squad.listing_id == l.id))).scalars().all()
        out["me"] = {"leading": l.leader_user_id == viewer.id, "my_max_cents": proxy.max_cents if proxy else None,
                     "sealed_placed": bool(sealed), "watching": bool(w),
                     "alert_price_cents": w.alert_price_cents if w else None, "is_seller": viewer.id == l.seller_id}
    out["squads"] = [await squads.view(session, i) for i in (await session.execute(
        select(Squad.id).where(Squad.listing_id == l.id, Squad.status.in_(["forming", "won"])))).scalars().all()]
    return out


def order(e, listing: Listing, buyer: User, seller: User) -> dict:
    return {"id": e.id, "listing_id": e.listing_id, "title": listing.title,
            "image": media_url(listing.images[0]) if listing.images else None,
            "amount_cents": e.amount_cents, "shipping_cents": e.shipping_cents, "fee_cents": e.fee_cents,
            "status": e.status, "tracking": e.tracking, "bundle_id": e.bundle_id, "squad_id": e.squad_id,
            "shipped_at": e.shipped_at, "release_at": e.release_at, "released_at": e.released_at,
            "created_at": e.created_at, "buyer": user_public(buyer), "seller": user_public(seller),
            "payout_paused": listing.settlement_hold}
