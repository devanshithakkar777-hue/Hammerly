import os
import pytest

from app import clock
from app.db import Base, init_engine, engine, session_maker
from app.models import User, UserDevice
from app.security import hash_password
from app.services import wallet, listings

DB_URL = os.environ.get("TEST_DATABASE_URL", "sqlite+aiosqlite:///./test.db")


@pytest.fixture(autouse=True)
async def db():
    clock.reset()
    init_engine(DB_URL)
    async with engine().begin() as c:
        await c.run_sync(Base.metadata.drop_all)
        await c.run_sync(Base.metadata.create_all)
    yield
    await engine().dispose()


@pytest.fixture
async def session():
    async with session_maker()() as s:
        yield s


async def make_user(session, name, balance=0, seller=False, admin=False, device=None, ip=""):
    u = User(email=f"{name}@x.test", password_hash=hash_password("pw"), display_name=name,
             is_seller=seller, seller_verified=seller, is_admin=admin, daily_cap_cents=10_000_000)
    session.add(u)
    await session.flush()
    if balance:
        await wallet.credit(session, u.id, balance)
    if device or ip:
        session.add(UserDevice(user_id=u.id, device_id=device or "", ip=ip))
    await session.commit()
    return u


async def make_listing(session, seller, fmt="english", start=10_000, reserve=0, duration=600, **kw):
    data = dict(title="Holo Dragon Card", category="Cards", format=fmt, start_price_cents=start,
                reserve_cents=reserve, shipping_cents=kw.pop("shipping_cents", 0))
    data.update(kw)
    l = await listings.create_draft(session, seller, data)
    await listings.publish(session, seller, l, duration)
    await session.commit()
    return l
