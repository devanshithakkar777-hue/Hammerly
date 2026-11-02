from datetime import timezone
from sqlalchemy import DateTime
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.types import TypeDecorator

from .config import get_settings


class UTCDateTime(TypeDecorator):
    """Always timezone-aware UTC, also on SQLite."""
    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is not None and value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value

    def process_result_value(self, value, dialect):
        if value is not None and value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value


class Base(DeclarativeBase):
    pass


_engine = None
_maker = None


def init_engine(url: str | None = None):
    global _engine, _maker
    url = url or get_settings().database_url
    kwargs = {}
    if url.startswith("sqlite"):
        kwargs["connect_args"] = {"timeout": 30}
    _engine = create_async_engine(url, **kwargs)
    _maker = async_sessionmaker(_engine, expire_on_commit=False)
    return _engine


def engine():
    if _engine is None:
        init_engine()
    return _engine


def session_maker() -> async_sessionmaker[AsyncSession]:
    if _maker is None:
        init_engine()
    return _maker


async def get_session():
    async with session_maker()() as s:
        yield s


async def create_all():
    from . import models  # noqa: F401
    async with engine().begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
