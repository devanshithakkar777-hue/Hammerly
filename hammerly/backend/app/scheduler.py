"""Background worker: opens scheduled auctions, closes ended ones, releases escrow, publishes Merkle roots."""
from __future__ import annotations

import asyncio
import logging
from datetime import timedelta

from sqlalchemy import select

from . import clock
from .config import get_settings
from .db import session_maker
from .models import Listing, MerkleRoot, Room, Watch
from .realtime import hub
from .services import bids, escrow, ledger, listings, rooms
from .services.notify import notify

log = logging.getLogger("hammerly.scheduler")


async def close_due(maker) -> int:
    async with maker() as s:
        ids = (await s.execute(select(Listing.id).where(Listing.status == "live", Listing.ends_at <= clock.now()))).scalars().all()
    n = 0
    for lid in ids:
        async with bids.listing_lock(lid):
            async with maker() as s:
                l = await bids.lock_listing(s, lid)
                if l.status != "live" or l.ends_at > clock.now():
                    continue  # a bid extended the auction in the meantime
                ev = await bids.close_listing(s, l)
                room_id = l.room_id
                await s.commit()
            await hub.publish(f"listing:{lid}", ev)
            if room_id:
                await hub.publish(f"room:{room_id}", ev)
            n += 1
    return n


async def open_scheduled(maker) -> int:
    async with maker() as s:
        rows = (await s.execute(select(Listing).where(Listing.status == "scheduled", Listing.starts_at <= clock.now()))).scalars().all()
        for l in rows:
            await listings.go_live(s, l)
        await s.commit()
        return len(rows)


async def last_hour_alerts(maker):
    async with maker() as s:
        soon = clock.now() + timedelta(hours=1)
        q = (select(Watch, Listing).join(Listing, Listing.id == Watch.listing_id)
             .where(Listing.status == "live", Listing.ends_at <= soon, Watch.notified_last_hour.is_(False)))
        for w, l in (await s.execute(q)).all():
            w.notified_last_hour = True
            await notify(s, w.user_id, "ending_soon", "Ending within the hour", l.title, f"/lots/{l.id}")
        await s.commit()


async def maybe_publish_root(maker):
    async with maker() as s:
        last = (await s.execute(select(MerkleRoot).order_by(MerkleRoot.id.desc()).limit(1))).scalar_one_or_none()
        if last is None or clock.now() - last.created_at >= timedelta(hours=1):
            if await ledger.publish_root(s):
                await s.commit()


async def advance_rooms(maker):
    async with maker() as s:
        live = (await s.execute(select(Room).where(Room.status == "live"))).scalars().all()
        for room in live:
            if not (room.stream or {}).get("auto_advance", True):
                continue
            cur = await s.get(Listing, room.current_listing_id) if room.current_listing_id else None
            if cur is None or cur.status not in {"live", "draft", "scheduled"}:
                nxt = await rooms.advance(s, None, room)
                await s.commit()
                await hub.publish(f"room:{room.id}", {"type": "lot_changed", "room_id": room.id,
                                                     "listing_id": nxt.id if nxt else None,
                                                     "room_status": room.status})


async def tick(maker=None):
    maker = maker or session_maker()
    await open_scheduled(maker)
    await close_due(maker)
    async with maker() as s:
        if await escrow.auto_release(s):
            await s.commit()
    await advance_rooms(maker)
    await last_hour_alerts(maker)
    await maybe_publish_root(maker)


async def run_forever():
    interval = get_settings().scheduler_tick_s
    while True:
        try:
            await tick()
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("scheduler tick failed")
        await asyncio.sleep(interval)
