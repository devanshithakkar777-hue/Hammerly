from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_session
from ..models import Dispute, Escrow, Listing, ShieldFlag, User
from ..schemas import AuthenticateIn, FlagReviewIn, ResolveIn
from ..security import require_admin
from ..services import escrow, passport, shield

router = APIRouter(prefix="/admin", tags=["admin"])


@router.get("/overview")
async def overview(_: User = Depends(require_admin), session: AsyncSession = Depends(get_session)):
    async def n(model, *where):
        return (await session.execute(select(func.count()).select_from(model).where(*where))).scalar_one()
    return {"users": await n(User), "live_listings": await n(Listing, Listing.status == "live"),
            "open_flags": await n(ShieldFlag, ShieldFlag.status == "open"),
            "open_disputes": await n(Dispute, Dispute.status == "open"),
            "escrow_held": (await session.execute(select(func.coalesce(func.sum(Escrow.amount_cents), 0)).where(
                Escrow.status.in_(["held", "shipped", "disputed"])))).scalar_one()}


@router.get("/flags")
async def flags(status: str = "open", _: User = Depends(require_admin), session: AsyncSession = Depends(get_session)):
    rows = (await session.execute(select(ShieldFlag).where(ShieldFlag.status == status)
                                  .order_by(ShieldFlag.score.desc(), ShieldFlag.id.desc()).limit(100))).scalars().all()
    return {"items": [{"id": f.id, "listing_id": f.listing_id, "user_id": f.user_id, "kind": f.kind,
                       "score": f.score, "details": f.details, "status": f.status, "created_at": f.created_at}
                      for f in rows]}


@router.post("/flags/{flag_id}")
async def review_flag(flag_id: int, body: FlagReviewIn, _: User = Depends(require_admin),
                      session: AsyncSession = Depends(get_session)):
    f = await shield.review(session, flag_id, body.status)
    await session.commit()
    return {"id": f.id, "status": f.status}


@router.get("/disputes")
async def disputes(status: str = "open", _: User = Depends(require_admin), session: AsyncSession = Depends(get_session)):
    rows = (await session.execute(select(Dispute).where(Dispute.status == status).order_by(Dispute.id.desc()))).scalars().all()
    return {"items": [{"id": d.id, "escrow_id": d.escrow_id, "opened_by": d.opened_by, "reason": d.reason,
                       "evidence": d.evidence, "status": d.status, "created_at": d.created_at} for d in rows]}


@router.post("/disputes/{dispute_id}/resolve")
async def resolve(dispute_id: int, body: ResolveIn, _: User = Depends(require_admin),
                  session: AsyncSession = Depends(get_session)):
    d = await escrow.resolve_dispute(session, dispute_id, body.outcome, body.note)
    await session.commit()
    return {"id": d.id, "status": d.status}


@router.post("/listings/{listing_id}/authenticate")
async def authenticate(listing_id: int, body: AuthenticateIn, _: User = Depends(require_admin),
                       session: AsyncSession = Depends(get_session)):
    await passport.set_authentication(session, listing_id, body.status, body.authenticator, body.cert_ref)
    await session.commit()
    return {"ok": True}
