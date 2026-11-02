from fastapi import APIRouter, Depends
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from .. import serialize
from ..db import get_session
from ..models import Escrow, EscrowContribution, Listing, Rating, User
from ..schemas import BundleIn, DisputeIn, RateIn, ShipIn
from ..security import current_user
from ..services import escrow
from ..services.escrow import EscrowError

router = APIRouter(tags=["orders"])


async def _out(session, e: Escrow, viewer: User) -> dict:
    listing = await session.get(Listing, e.listing_id)
    d = serialize.order(e, listing, await session.get(User, e.buyer_id), await session.get(User, e.seller_id))
    d["role"] = "seller" if viewer.id == e.seller_id else "buyer"
    c = (await session.execute(select(EscrowContribution).where(
        EscrowContribution.escrow_id == e.id, EscrowContribution.user_id == viewer.id))).scalar_one_or_none()
    d["my_share_cents"] = c.amount_cents if c else None
    d["rated"] = (await session.execute(select(Rating.id).where(Rating.escrow_id == e.id))).first() is not None
    return d


async def _visible(session, escrow_id: int, user: User) -> Escrow:
    e = await session.get(Escrow, escrow_id)
    mine = e and (user.id in (e.buyer_id, e.seller_id) or user.is_admin or (await session.execute(
        select(EscrowContribution.id).where(EscrowContribution.escrow_id == e.id,
                                            EscrowContribution.user_id == user.id))).first())
    if not mine:
        raise EscrowError("not_found", "Order not found.", 404)
    return e


@router.get("/orders")
async def orders(user: User = Depends(current_user), session: AsyncSession = Depends(get_session)):
    contrib = select(EscrowContribution.escrow_id).where(EscrowContribution.user_id == user.id)
    rows = (await session.execute(select(Escrow).where(or_(
        Escrow.buyer_id == user.id, Escrow.seller_id == user.id, Escrow.id.in_(contrib))).order_by(Escrow.id.desc()))).scalars().all()
    return {"items": [await _out(session, e, user) for e in rows]}


@router.get("/orders/{order_id}")
async def order(order_id: int, user: User = Depends(current_user), session: AsyncSession = Depends(get_session)):
    return await _out(session, await _visible(session, order_id, user), user)


@router.post("/orders/{order_id}/ship")
async def ship(order_id: int, body: ShipIn, user: User = Depends(current_user), session: AsyncSession = Depends(get_session)):
    e = await escrow.ship(session, user, order_id, body.tracking)
    await session.commit()
    return await _out(session, e, user)


@router.post("/orders/{order_id}/confirm")
async def confirm(order_id: int, user: User = Depends(current_user), session: AsyncSession = Depends(get_session)):
    e = await escrow.confirm_delivery(session, user, order_id)
    await session.commit()
    return await _out(session, e, user)


@router.post("/orders/{order_id}/dispute", status_code=201)
async def dispute(order_id: int, body: DisputeIn, user: User = Depends(current_user),
                  session: AsyncSession = Depends(get_session)):
    d = await escrow.open_dispute(session, user, order_id, body.reason, body.evidence)
    await session.commit()
    return {"id": d.id, "status": d.status}


@router.post("/orders/{order_id}/rate")
async def rate(order_id: int, body: RateIn, user: User = Depends(current_user), session: AsyncSession = Depends(get_session)):
    await escrow.rate(session, user, order_id, body.stars, body.comment)
    await session.commit()
    return {"ok": True}


@router.post("/orders/bundle")
async def bundle(body: BundleIn, user: User = Depends(current_user), session: AsyncSession = Depends(get_session)):
    escs = await escrow.bundle(session, user, body.order_ids)
    await session.commit()
    return {"items": [await _out(session, e, user) for e in escs]}
