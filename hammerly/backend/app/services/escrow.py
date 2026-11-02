"""Escrow: funds captured from winners are held until delivery is confirmed or the timer runs out."""
from __future__ import annotations

from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .. import clock
from ..config import get_settings
from ..models import (Dispute, Escrow, EscrowContribution, Hold, Listing, PriceSample, Rating, ShieldFlag,
                      Squad, User)
from . import wallet
from .notify import notify


class EscrowError(Exception):
    def __init__(self, code: str, message: str, status: int = 400):
        super().__init__(message)
        self.code, self.message, self.status = code, message, status


async def create_for_sale(session: AsyncSession, listing: Listing, buyer_id: int, squad_id: int | None, price: int):
    s = get_settings()
    seller = await session.get(User, listing.seller_id)
    bps = s.pro_seller_fee_bps if seller.is_pro else s.seller_fee_bps
    esc = Escrow(listing_id=listing.id, buyer_id=buyer_id, squad_id=squad_id, seller_id=listing.seller_id,
                 amount_cents=price, shipping_cents=listing.shipping_cents, fee_cents=price * bps // 10_000,
                 status="held", created_at=clock.now())
    session.add(esc)
    await session.flush()
    holds = (await session.execute(select(Hold).where(Hold.listing_id == listing.id))).scalars().all()
    for h in holds:
        if h.amount_cents:
            await wallet.capture_held(session, h.user_id, h.amount_cents, "escrow", esc.id)
            session.add(EscrowContribution(escrow_id=esc.id, user_id=h.user_id, amount_cents=h.amount_cents))
        await session.delete(h)
    if squad_id:
        sq = await session.get(Squad, squad_id)
        sq.status = "won"
    session.add(PriceSample(category=listing.category, title=listing.title, price_cents=price,
                            sold_at=clock.now(), source="hammerly", listing_id=listing.id))
    await notify(session, buyer_id, "won", "You won", f"{listing.title} for ${price / 100:,.2f}. Funds are in escrow.",
                 f"/orders/{esc.id}")
    await session.flush()
    return esc


async def _get(session, escrow_id: int) -> Escrow:
    esc = (await session.execute(select(Escrow).where(Escrow.id == escrow_id).with_for_update())).scalar_one_or_none()
    if esc is None:
        raise EscrowError("not_found", "Order not found.", 404)
    return esc


async def _has_open_flags(session, listing_id: int) -> bool:
    q = select(ShieldFlag.id).where(ShieldFlag.listing_id == listing_id, ShieldFlag.status == "open",
                                    ShieldFlag.score >= 0.8)
    return (await session.execute(q)).first() is not None


async def ship(session: AsyncSession, seller: User, escrow_id: int, tracking: str) -> Escrow:
    esc = await _get(session, escrow_id)
    if esc.seller_id != seller.id:
        raise EscrowError("forbidden", "This is not your order.", 403)
    if esc.status != "held":
        raise EscrowError("bad_state", "This order cannot be marked shipped.", 409)
    esc.status, esc.tracking = "shipped", tracking[:80]
    esc.shipped_at = clock.now()
    esc.release_at = esc.shipped_at + timedelta(days=get_settings().auto_release_days)
    await notify(session, esc.buyer_id, "shipped", "Your order shipped", f"Tracking: {esc.tracking}", f"/orders/{esc.id}")
    return esc


async def release(session: AsyncSession, esc: Escrow, reason: str = "delivered") -> Escrow:
    if esc.status not in {"held", "shipped", "disputed"}:
        raise EscrowError("bad_state", "Funds were already settled.", 409)
    if await _has_open_flags(session, esc.listing_id):
        raise EscrowError("under_review", "Payout is paused while Shield reviews this sale.", 409)
    net = esc.amount_cents - esc.fee_cents + esc.shipping_cents
    await wallet.credit(session, esc.seller_id, net, "payout", "escrow", esc.id)
    esc.status, esc.released_at = "released", clock.now()
    await notify(session, esc.seller_id, "payout", "Payout released",
                 f"${net / 100:,.2f} added to your wallet.", f"/orders/{esc.id}")
    return esc


async def confirm_delivery(session: AsyncSession, user: User, escrow_id: int) -> Escrow:
    esc = await _get(session, escrow_id)
    if user.id != esc.buyer_id and not user.is_admin:
        raise EscrowError("forbidden", "Only the buyer can confirm delivery.", 403)
    if esc.status != "shipped":
        raise EscrowError("bad_state", "The seller has not shipped this order yet.", 409)
    return await release(session, esc)


async def open_dispute(session: AsyncSession, user: User, escrow_id: int, reason: str, evidence: list[str]) -> Dispute:
    esc = await _get(session, escrow_id)
    if user.id not in (esc.buyer_id, esc.seller_id):
        raise EscrowError("forbidden", "This is not your order.", 403)
    if esc.status not in {"held", "shipped"}:
        raise EscrowError("bad_state", "This order can no longer be disputed.", 409)
    esc.status = "disputed"
    d = Dispute(escrow_id=esc.id, opened_by=user.id, reason=reason[:2000], evidence=evidence[:10])
    session.add(d)
    other = esc.seller_id if user.id == esc.buyer_id else esc.buyer_id
    await notify(session, other, "dispute", "A dispute was opened", reason[:120], f"/orders/{esc.id}")
    for a in (await session.execute(select(User).where(User.is_admin.is_(True)))).scalars():
        await notify(session, a.id, "dispute", "Dispute needs review", f"Order {esc.id}", f"/admin")
    await session.flush()
    return d


async def resolve_dispute(session: AsyncSession, dispute_id: int, outcome: str, note: str) -> Dispute:
    d = await session.get(Dispute, dispute_id)
    if d is None or d.status != "open":
        raise EscrowError("not_found", "Open dispute not found.", 404)
    esc = await _get(session, d.escrow_id)
    if outcome == "buyer":
        contribs = (await session.execute(select(EscrowContribution).where(EscrowContribution.escrow_id == esc.id))).scalars().all()
        for c in contribs:
            await wallet.credit(session, c.user_id, c.amount_cents, "refund", "escrow", esc.id)
        esc.status = "refunded"
        seller = await session.get(User, esc.seller_id)
        seller.trust_score = max(0.0, seller.trust_score - 0.3)
        d.status = "resolved_buyer"
    elif outcome == "seller":
        esc.status = "shipped"  # restore a releasable state, then release
        await release(session, esc, "dispute_resolved")
        d.status = "resolved_seller"
    else:
        raise EscrowError("bad_outcome", "Outcome must be 'buyer' or 'seller'.")
    d.resolution_note, d.resolved_at = note[:2000], clock.now()
    for uid in (esc.buyer_id, esc.seller_id):
        await notify(session, uid, "dispute_resolved", "Dispute resolved", note[:120], f"/orders/{esc.id}")
    return d


async def bundle(session: AsyncSession, buyer: User, escrow_ids: list[int]) -> list[Escrow]:
    """Win Bundle: orders from one seller within 7 days share a single shipping charge."""
    if len(set(escrow_ids)) < 2:
        raise EscrowError("too_few", "Choose at least two orders to bundle.")
    escs = [await _get(session, i) for i in sorted(set(escrow_ids))]
    if any(e.buyer_id != buyer.id or e.squad_id for e in escs):
        raise EscrowError("forbidden", "Only your own non-squad orders can be bundled.", 403)
    if len({e.seller_id for e in escs}) != 1:
        raise EscrowError("mixed_sellers", "Orders must be from the same seller.", 409)
    if any(e.status != "held" or e.bundle_id for e in escs):
        raise EscrowError("bad_state", "Only unshipped, unbundled orders can be bundled.", 409)
    if max(e.created_at for e in escs) - min(e.created_at for e in escs) > timedelta(days=7):
        raise EscrowError("too_far_apart", "Orders must be within 7 days of each other.", 409)
    first = escs[0]
    for e in escs[1:]:
        if e.shipping_cents:
            await wallet.credit(session, buyer.id, e.shipping_cents, "refund", "escrow", e.id)
            contrib = (await session.execute(select(EscrowContribution).where(
                EscrowContribution.escrow_id == e.id, EscrowContribution.user_id == buyer.id))).scalar_one()
            contrib.amount_cents -= e.shipping_cents
            e.shipping_cents = 0
        e.bundle_id = first.id
    first.bundle_id = first.id
    await notify(session, first.seller_id, "bundle", "Orders bundled",
                 f"{len(escs)} orders ship together. Please send them in one parcel.", f"/orders/{first.id}")
    return escs


async def auto_release(session: AsyncSession) -> int:
    now = clock.now()
    rows = (await session.execute(select(Escrow).where(Escrow.status == "shipped", Escrow.release_at <= now))).scalars().all()
    n = 0
    for e in rows:
        try:
            await release(session, e, "timeout")
            n += 1
        except EscrowError:
            pass
    return n


async def rate(session: AsyncSession, buyer: User, escrow_id: int, stars: int, comment: str) -> Rating:
    esc = await _get(session, escrow_id)
    if esc.buyer_id != buyer.id or esc.status != "released":
        raise EscrowError("bad_state", "You can rate a seller after the order is complete.", 409)
    if not 1 <= stars <= 5:
        raise EscrowError("bad_rating", "Rating must be 1 to 5.")
    if (await session.execute(select(Rating.id).where(Rating.escrow_id == esc.id))).first():
        raise EscrowError("duplicate", "You already rated this order.", 409)
    r = Rating(escrow_id=esc.id, rater_id=buyer.id, ratee_id=esc.seller_id, stars=stars, comment=comment[:300])
    session.add(r)
    seller = await session.get(User, esc.seller_id)
    seller.trust_score = round((seller.trust_score * seller.ratings_count + stars) / (seller.ratings_count + 1), 2)
    seller.ratings_count += 1
    await session.flush()
    return r
