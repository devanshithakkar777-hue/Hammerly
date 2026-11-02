from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_session
from ..models import Listing, User
from ..realtime import hub
from ..schemas import BidIn, ConfirmIn, SquadBidIn, SquadCreateIn, SquadJoinIn
from ..security import current_user, record_device
from ..services import bids, squads
from ..services.bids import BidError

router = APIRouter(tags=["auctions"])


async def _publish(session: AsyncSession, listing_id: int, result: dict):
    """Publish after commit so clients never see an event for a rolled-back bid."""
    room_id = (await session.get(Listing, listing_id)).room_id
    events = [result["event"]] + ([result["closed"]] if result.get("closed") else [])
    for ev in events:
        await hub.publish(f"listing:{listing_id}", ev)
        if room_id:
            await hub.publish(f"room:{room_id}", ev)


async def _run(session, request, user, listing_id, fn):
    await record_device(session, user.id, request)
    await session.commit()
    async with bids.listing_lock(listing_id):
        result = await fn()
        await session.commit()
    await _publish(session, listing_id, result)
    return result


def _out(result: dict) -> dict:
    ev = result["event"]
    return {"ok": True, "price_cents": result.get("price_cents", ev["price_cents"]), "ends_at": ev["ends_at"],
            "extended": ev["extended"], "leader_alias": ev["leader_alias"], "bid_count": ev["bid_count"],
            "closed": result.get("closed"), "commitment": result.get("commitment"),
            "outcome": result.get("outcome")}


@router.post("/listings/{listing_id}/bids")
async def bid(listing_id: int, body: BidIn, request: Request, user: User = Depends(current_user),
              session: AsyncSession = Depends(get_session)):
    r = await _run(session, request, user, listing_id, lambda: bids.place_bid(
        session, user, listing_id, body.amount_cents, body.max_cents, set(body.confirm)))
    return _out(r)


@router.post("/listings/{listing_id}/claim")
async def claim(listing_id: int, body: ConfirmIn, request: Request, user: User = Depends(current_user),
                session: AsyncSession = Depends(get_session)):
    r = await _run(session, request, user, listing_id, lambda: bids.claim_dutch(
        session, user, listing_id, set(body.confirm)))
    return _out(r)


@router.post("/listings/{listing_id}/sealed")
async def sealed(listing_id: int, body: BidIn, request: Request, user: User = Depends(current_user),
                 session: AsyncSession = Depends(get_session)):
    r = await _run(session, request, user, listing_id, lambda: bids.place_sealed(
        session, user, listing_id, body.amount_cents, set(body.confirm)))
    return _out(r)


# ---- Squad Bids ---------------------------------------------------------

@router.post("/listings/{listing_id}/squads", status_code=201)
async def squad_create(listing_id: int, body: SquadCreateIn, user: User = Depends(current_user),
                       session: AsyncSession = Depends(get_session)):
    sq = await squads.create(session, user, listing_id, body.name, body.pledge_cents)
    await session.commit()
    return await squads.view(session, sq.id)


@router.post("/squads/{squad_id}/join")
async def squad_join(squad_id: int, body: SquadJoinIn, user: User = Depends(current_user),
                     session: AsyncSession = Depends(get_session)):
    await squads.join(session, user, squad_id, body.pledge_cents)
    await session.commit()
    return await squads.view(session, squad_id)


@router.get("/squads/{squad_id}")
async def squad_view(squad_id: int, session: AsyncSession = Depends(get_session)):
    return await squads.view(session, squad_id)


@router.post("/squads/{squad_id}/bid")
async def squad_bid(squad_id: int, body: SquadBidIn, request: Request, user: User = Depends(current_user),
                    session: AsyncSession = Depends(get_session)):
    from ..models import Squad
    sq = await session.get(Squad, squad_id)
    if sq is None:
        raise BidError("not_found", "Squad not found.", 404)
    r = await _run(session, request, user, sq.listing_id,
                   lambda: squads.bid(session, user, squad_id, body.amount_cents, set(body.confirm)))
    return _out(r)
