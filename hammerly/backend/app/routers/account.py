from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from .. import serialize
from ..config import get_settings
from ..db import get_session
from ..models import Notification, User, Wallet, WalletEntry
from ..schemas import LoginIn, MeUpdate, RegisterIn, TopupIn
from ..security import current_user, hash_password, make_token, record_device, verify_password
from ..services import payments, wallet

router = APIRouter(tags=["account"])


@router.post("/auth/register")
async def register(body: RegisterIn, request: Request, session: AsyncSession = Depends(get_session)):
    s = get_settings()
    admins = {e.strip().lower() for e in s.admin_emails.split(",") if e.strip()}
    u = User(email=body.email.lower(), password_hash=hash_password(body.password), display_name=body.display_name.strip(),
             daily_cap_cents=s.default_daily_cap_cents, is_admin=body.email.lower() in admins)
    session.add(u)
    try:
        await session.flush()
    except IntegrityError:
        await session.rollback()
        raise HTTPException(409, "That email or display name is already taken.")
    await wallet.get_wallet(session, u.id, lock=False)
    await record_device(session, u.id, request)
    await session.commit()
    return {"token": make_token(u.id), "user": serialize.user_private(u)}


@router.post("/auth/login")
async def login(body: LoginIn, request: Request, session: AsyncSession = Depends(get_session)):
    u = (await session.execute(select(User).where(User.email == body.email.lower()))).scalar_one_or_none()
    if not u or not verify_password(body.password, u.password_hash):
        raise HTTPException(401, "Email or password is incorrect.")
    await record_device(session, u.id, request)
    await session.commit()
    return {"token": make_token(u.id), "user": serialize.user_private(u)}


@router.get("/me")
async def me(user: User = Depends(current_user)):
    return serialize.user_private(user)


@router.patch("/me")
async def update_me(body: MeUpdate, user: User = Depends(current_user), session: AsyncSession = Depends(get_session)):
    if body.calm_mode is not None:
        user.calm_mode = body.calm_mode
    if body.daily_cap_cents is not None:
        user.daily_cap_cents = body.daily_cap_cents
    if body.display_name:
        user.display_name = body.display_name.strip()
    session.add(user)
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        raise HTTPException(409, "That display name is taken.")
    return serialize.user_private(user)


@router.post("/me/seller")
async def become_seller(user: User = Depends(current_user), session: AsyncSession = Depends(get_session)):
    """Test mode: seller verification is granted immediately. In production this would start real KYC."""
    user.is_seller = True
    user.seller_verified = True
    session.add(user)
    await session.commit()
    return serialize.user_private(user)


@router.get("/wallet")
async def get_wallet(user: User = Depends(current_user), session: AsyncSession = Depends(get_session)):
    w = await wallet.get_wallet(session, user.id, lock=False)
    await session.commit()
    rows = (await session.execute(select(WalletEntry).where(WalletEntry.user_id == user.id)
                                  .order_by(WalletEntry.id.desc()).limit(30))).scalars().all()
    return {"available_cents": w.available_cents, "held_cents": w.held_cents,
            "provider": get_settings().payment_provider,
            "entries": [{"id": e.id, "kind": e.kind, "amount_cents": e.amount_cents, "ref_type": e.ref_type,
                         "ref_id": e.ref_id, "at": e.created_at} for e in rows]}


@router.post("/wallet/topup")
async def topup(body: TopupIn, user: User = Depends(current_user), session: AsyncSession = Depends(get_session)):
    result = await payments.get_provider().create_topup(user.id, body.amount_cents)
    if result.status != "succeeded":
        raise HTTPException(402, "The payment did not go through.")
    await wallet.credit(session, user.id, body.amount_cents, "topup", "payment", 0)
    await session.commit()
    w = await wallet.get_wallet(session, user.id, lock=False)
    return {"available_cents": w.available_cents, "held_cents": w.held_cents, "test_mode": get_settings().payment_provider == "mock"}


@router.get("/notifications")
async def notifications(user: User = Depends(current_user), session: AsyncSession = Depends(get_session)):
    rows = (await session.execute(select(Notification).where(Notification.user_id == user.id)
                                  .order_by(Notification.id.desc()).limit(50))).scalars().all()
    return [{"id": n.id, "kind": n.kind, "title": n.title, "body": n.body, "link": n.link, "read": n.read,
             "at": n.created_at} for n in rows]


@router.post("/notifications/read-all")
async def read_all(user: User = Depends(current_user), session: AsyncSession = Depends(get_session)):
    for n in (await session.execute(select(Notification).where(Notification.user_id == user.id,
                                                               Notification.read.is_(False)))).scalars():
        n.read = True
    await session.commit()
    return {"ok": True}
