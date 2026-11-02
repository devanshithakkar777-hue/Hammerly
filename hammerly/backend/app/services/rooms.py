"""Live Rooms: a host runs a queue of lots; viewers bid, chat and react in real time."""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .. import clock
from ..config import get_settings
from ..models import Listing, Room, RoomMessage, User
from ..realtime import hub
from . import listings
from .bids import BidError


async def create(session: AsyncSession, host: User, title: str, listing_ids: list[int], starts_at=None,
                 playback_url: str = "") -> Room:
    if not host.is_seller:
        raise BidError("forbidden", "Only sellers can host rooms.", 403)
    room = Room(host_id=host.id, title=title.strip()[:120], starts_at=starts_at, queue=[],
                stream={"provider": get_settings().stream_provider, "playback_url": playback_url, "auto_advance": True})
    session.add(room)
    await session.flush()
    await set_queue(session, host, room, listing_ids)
    return room


async def set_queue(session: AsyncSession, host: User, room: Room, listing_ids: list[int]):
    if room.host_id != host.id:
        raise BidError("forbidden", "This is not your room.", 403)
    ids = []
    for lid in listing_ids:
        l = await session.get(Listing, lid)
        if l is None or l.seller_id != host.id or l.status != "draft":
            raise BidError("bad_listing", f"Listing {lid} must be one of your drafts.", 409)
        l.room_id = room.id
        ids.append(lid)
    room.queue = ids
    await session.flush()


async def start(session: AsyncSession, host: User, room: Room) -> Room:
    if room.host_id != host.id:
        raise BidError("forbidden", "This is not your room.", 403)
    if room.status != "scheduled":
        raise BidError("bad_state", "This room already started.", 409)
    room.status = "live"
    return room


async def advance(session: AsyncSession, host: User | None, room: Room) -> Listing | None:
    """Put the next queued lot on the block. The host or the scheduler may call this."""
    if host is not None and room.host_id != host.id:
        raise BidError("forbidden", "This is not your room.", 403)
    if room.status != "live":
        raise BidError("bad_state", "Start the room first.", 409)
    if room.current_listing_id:
        cur = await session.get(Listing, room.current_listing_id)
        if cur and cur.status == "live":
            raise BidError("lot_open", "The current lot is still open.", 409)
    for lid in list(room.queue):
        l = await session.get(Listing, lid)
        if l and l.status == "draft":
            seller = await session.get(User, l.seller_id)
            await listings.publish(session, seller, l, get_settings().room_lot_seconds)
            room.current_listing_id = l.id
            return l
    room.status = "ended"
    room.current_listing_id = None
    return None


async def end(session: AsyncSession, host: User, room: Room):
    if room.host_id != host.id:
        raise BidError("forbidden", "This is not your room.", 403)
    room.status = "ended"


async def post_message(session: AsyncSession, user: User, room: Room, body: str) -> dict:
    body = body.strip()[:300]
    if not body:
        raise BidError("empty", "Say something first.")
    m = RoomMessage(room_id=room.id, user_id=user.id, alias=user.display_name, body=body, created_at=clock.now())
    session.add(m)
    await session.flush()
    return {"type": "chat", "room_id": room.id, "alias": user.display_name, "body": body, "at": m.created_at}


async def recent_messages(session: AsyncSession, room_id: int, limit: int = 50) -> list[dict]:
    rows = (await session.execute(select(RoomMessage).where(RoomMessage.room_id == room_id)
                                  .order_by(RoomMessage.id.desc()).limit(limit))).scalars().all()
    return [{"alias": r.alias, "body": r.body, "at": r.created_at} for r in reversed(rows)]


def viewers(room_id: int) -> int:
    return hub.count(f"room:{room_id}")
