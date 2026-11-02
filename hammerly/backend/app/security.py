from datetime import timedelta

import bcrypt
import jwt
from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from . import clock
from .config import get_settings
from .db import get_session
from .models import User, UserDevice

bearer = HTTPBearer(auto_error=False)


def hash_password(pw: str) -> str:
    return bcrypt.hashpw(pw.encode(), bcrypt.gensalt()).decode()


def verify_password(pw: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(pw.encode(), hashed.encode())
    except ValueError:
        return False


def make_token(user_id: int) -> str:
    s = get_settings()
    exp = clock.now() + timedelta(minutes=s.jwt_minutes)
    return jwt.encode({"sub": str(user_id), "exp": exp}, s.jwt_secret, algorithm="HS256")


def decode_token(token: str) -> int | None:
    try:
        data = jwt.decode(token, get_settings().jwt_secret, algorithms=["HS256"])
        return int(data["sub"])
    except Exception:
        return None


async def record_device(session: AsyncSession, user_id: int, request: Request | None):
    """Remember device and network per user so Shield can link accounts."""
    if request is None:
        return
    device = (request.headers.get("x-device-id") or "")[:80]
    ip = (request.client.host if request.client else "")[:64]
    if not device and not ip:
        return
    from sqlalchemy import select
    q = select(UserDevice).where(UserDevice.user_id == user_id, UserDevice.device_id == device, UserDevice.ip == ip)
    if (await session.execute(q)).scalar_one_or_none() is None:
        session.add(UserDevice(user_id=user_id, device_id=device, ip=ip))
        await session.flush()


async def optional_user(creds: HTTPAuthorizationCredentials | None = Depends(bearer),
                        session: AsyncSession = Depends(get_session)) -> User | None:
    if not creds:
        return None
    uid = decode_token(creds.credentials)
    return await session.get(User, uid) if uid else None


async def current_user(user: User | None = Depends(optional_user)) -> User:
    if user is None:
        raise HTTPException(401, "Sign in to continue.")
    return user


async def require_seller(user: User = Depends(current_user)) -> User:
    if not user.is_seller:
        raise HTTPException(403, "Become a seller to list items.")
    return user


async def require_admin(user: User = Depends(current_user)) -> User:
    if not user.is_admin:
        raise HTTPException(403, "Admins only.")
    return user
