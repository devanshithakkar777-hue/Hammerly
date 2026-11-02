"""Squad Bids: friends pool payment for one lot. Each member funds a share in proportion to their pledge."""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import Escrow, EscrowContribution, Listing, Squad, SquadMember, User
from . import bids, wallet
from .bids import BidError


async def create(session: AsyncSession, owner: User, listing_id: int, name: str, pledge_cents: int) -> Squad:
    listing = await session.get(Listing, listing_id)
    if listing is None or listing.format != "english" or listing.status not in {"live", "scheduled"}:
        raise BidError("bad_listing", "Squads can form on live or upcoming English auctions.", 409)
    if owner.id == listing.seller_id:
        raise BidError("own_listing", "You cannot bid on your own listing.", 403)
    if pledge_cents < 100:
        raise BidError("bad_pledge", "Pledge at least $1.")
    sq = Squad(listing_id=listing_id, owner_id=owner.id, name=name.strip()[:40])
    session.add(sq)
    await session.flush()
    session.add(SquadMember(squad_id=sq.id, user_id=owner.id, pledge_cents=pledge_cents))
    await session.flush()
    return sq


async def join(session: AsyncSession, user: User, squad_id: int, pledge_cents: int) -> SquadMember:
    sq = await session.get(Squad, squad_id)
    if sq is None or sq.status != "forming":
        raise BidError("closed", "This squad is no longer open.", 409)
    listing = await session.get(Listing, sq.listing_id)
    if user.id == listing.seller_id:
        raise BidError("own_listing", "You cannot join a squad on your own listing.", 403)
    if listing.leader_squad_id == sq.id:
        raise BidError("leading", "The squad already leads, so its shares are locked.", 409)
    if pledge_cents < 100:
        raise BidError("bad_pledge", "Pledge at least $1.")
    existing = (await session.execute(select(SquadMember).where(SquadMember.squad_id == sq.id,
                                                                SquadMember.user_id == user.id))).scalar_one_or_none()
    if existing:
        existing.pledge_cents = pledge_cents
        return existing
    m = SquadMember(squad_id=sq.id, user_id=user.id, pledge_cents=pledge_cents)
    session.add(m)
    await session.flush()
    return m


async def bid(session: AsyncSession, owner: User, squad_id: int, amount_cents: int, confirm=None) -> dict:
    sq = await session.get(Squad, squad_id)
    if sq is None or sq.owner_id != owner.id:
        raise BidError("forbidden", "Only the squad owner places bids.", 403)
    if sq.status != "forming":
        raise BidError("closed", "This squad is no longer open.", 409)
    members = (await session.execute(select(SquadMember).where(SquadMember.squad_id == sq.id))).scalars().all()
    if sum(m.pledge_cents for m in members) < amount_cents + 0:
        raise BidError("over_pledged", "The squad's pledges do not cover this bid.", 409,
                       pledged_cents=sum(m.pledge_cents for m in members))
    try:
        return await bids.place_bid(session, owner, sq.listing_id, amount_cents, confirm=confirm, squad=sq)
    except wallet.InsufficientFunds as e:
        raise BidError("insufficient_funds", "A member does not have enough funds for their share.", 402,
                       needed_cents=e.needed, available_cents=e.available)


async def view(session: AsyncSession, squad_id: int) -> dict:
    sq = await session.get(Squad, squad_id)
    if sq is None:
        raise BidError("not_found", "Squad not found.", 404)
    rows = (await session.execute(select(SquadMember, User).join(User, User.id == SquadMember.user_id)
                                  .where(SquadMember.squad_id == sq.id))).all()
    total = sum(m.pledge_cents for m, _ in rows) or 1
    out = {"id": sq.id, "name": sq.name, "listing_id": sq.listing_id, "status": sq.status, "owner_id": sq.owner_id,
           "pledged_cents": total,
           "members": [{"user_id": u.id, "name": u.display_name, "pledge_cents": m.pledge_cents,
                        "share_bps": m.pledge_cents * 10_000 // total} for m, u in rows]}
    if sq.status == "won":
        esc = (await session.execute(select(Escrow).where(Escrow.squad_id == sq.id))).scalar_one_or_none()
        if esc:
            contribs = (await session.execute(select(EscrowContribution).where(
                EscrowContribution.escrow_id == esc.id))).scalars().all()
            paid = sum(c.amount_cents for c in contribs) or 1
            out["ownership"] = [{"user_id": c.user_id, "share_bps": c.amount_cents * 10_000 // paid} for c in contribs]
            out["order_id"] = esc.id
    return out
