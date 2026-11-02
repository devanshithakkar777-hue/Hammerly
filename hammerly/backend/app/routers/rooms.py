import asyncio

from fastapi import APIRouter, Depends, HTTPException, WebSocket, WebSocketDisconnect
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .. import clock, serialize
from ..db import get_session, session_maker
from ..models import Listing, Notification, Room, User
from ..realtime import hub
from ..schemas import ChatIn, QueueIn, RoomIn
from ..security import current_user, decode_token, optional_user
from ..services import rooms as svc

router = APIRouter(tags=["rooms"])
REACTIONS = {"🔥", "👏", "😮", "❤️", "💎"}


async def _room(session, room_id: int) -> Room:
    r = await session.get(Room, room_id)
    if r is None:
        raise HTTPException(404, "Room not found.")
    return r


async def _view(session, r: Room) -> dict:
    host = await session.get(User, r.host_id)
    cur = await session.get(Listing, r.current_listing_id) if r.current_listing_id else None
    queue = []
    for lid in r.queue:
        l = await session.get(Listing, lid)
        if l:
            queue.append(serialize.card(l))
    return {"id": r.id, "title": r.title, "status": r.status, "starts_at": r.starts_at, "host": serialize.user_public(host),
            "playback_url": (r.stream or {}).get("playback_url", ""), "viewers": svc.viewers(r.id),
            "current": serialize.card(cur) if cur else None, "queue": queue, "server_time": clock.now()}


@router.get("/rooms")
async def list_rooms(session: AsyncSession = Depends(get_session)):
    rows = (await session.execute(select(Room).where(Room.status != "ended").order_by(Room.status.desc(), Room.id.desc()))).scalars().all()
    return {"items": [await _view(session, r) for r in rows]}


@router.post("/rooms", status_code=201)
async def create_room(body: RoomIn, user: User = Depends(current_user), session: AsyncSession = Depends(get_session)):
    r = await svc.create(session, user, body.title, body.listing_ids, body.starts_at, body.playback_url)
    await session.commit()
    return await _view(session, r)


@router.get("/rooms/{room_id}")
async def get_room(room_id: int, session: AsyncSession = Depends(get_session)):
    r = await _room(session, room_id)
    return {**await _view(session, r), "messages": await svc.recent_messages(session, r.id)}


@router.put("/rooms/{room_id}/queue")
async def set_queue(room_id: int, body: QueueIn, user: User = Depends(current_user), session: AsyncSession = Depends(get_session)):
    r = await _room(session, room_id)
    await svc.set_queue(session, user, r, body.listing_ids)
    await session.commit()
    return await _view(session, r)


async def _lot_event(r: Room, lot):
    await hub.publish(f"room:{r.id}", {"type": "lot_changed", "room_id": r.id, "listing_id": lot.id if lot else None,
                                       "room_status": r.status})


@router.post("/rooms/{room_id}/start")
async def start(room_id: int, user: User = Depends(current_user), session: AsyncSession = Depends(get_session)):
    r = await _room(session, room_id)
    await svc.start(session, user, r)
    lot = await svc.advance(session, user, r)
    await session.commit()
    await _lot_event(r, lot)
    return await _view(session, r)


@router.post("/rooms/{room_id}/next")
async def next_lot(room_id: int, user: User = Depends(current_user), session: AsyncSession = Depends(get_session)):
    r = await _room(session, room_id)
    lot = await svc.advance(session, user, r)
    await session.commit()
    await _lot_event(r, lot)
    return await _view(session, r)


@router.post("/rooms/{room_id}/end")
async def end(room_id: int, user: User = Depends(current_user), session: AsyncSession = Depends(get_session)):
    r = await _room(session, room_id)
    await svc.end(session, user, r)
    await session.commit()
    await _lot_event(r, None)
    return await _view(session, r)


@router.post("/rooms/{room_id}/messages")
async def chat(room_id: int, body: ChatIn, user: User = Depends(current_user), session: AsyncSession = Depends(get_session)):
    r = await _room(session, room_id)
    ev = await svc.post_message(session, user, r, body.body)
    await session.commit()
    await hub.publish(f"room:{r.id}", ev)
    return {"ok": True}


# ---- WebSockets ---------------------------------------------------------

async def _watch(ws: WebSocket, channel: str, on_join=None, on_message=None):
    await ws.accept()
    await hub.join(channel, ws)
    try:
        if on_join:
            await on_join()
        while True:
            msg = await ws.receive_json()
            if msg.get("type") == "ping":
                await ws.send_json({"type": "pong", "server_time": clock.now().isoformat()})
            elif on_message:
                await on_message(msg)
    except (WebSocketDisconnect, RuntimeError, ValueError):
        pass
    finally:
        hub.leave(channel, ws)
        if on_join:
            await hub.publish(channel, {"type": "viewers", "count": hub.count(channel)})


@router.websocket("/ws/listings/{listing_id}")
async def ws_listing(ws: WebSocket, listing_id: int):
    await _watch(ws, f"listing:{listing_id}")


@router.websocket("/ws/rooms/{room_id}")
async def ws_room(ws: WebSocket, room_id: int):
    channel = f"room:{room_id}"
    token = ws.query_params.get("token")
    uid = decode_token(token) if token else None

    async def joined():
        await hub.publish(channel, {"type": "viewers", "count": hub.count(channel)})

    async def message(msg):
        if msg.get("type") == "react" and msg.get("emoji") in REACTIONS:
            await hub.publish(channel, {"type": "reaction", "emoji": msg["emoji"]})
        elif msg.get("type") == "chat" and uid:
            async with session_maker()() as s:
                u, r = await s.get(User, uid), await s.get(Room, room_id)
                if u and r:
                    ev = await svc.post_message(s, u, r, str(msg.get("body", "")))
                    await s.commit()
                    await hub.publish(channel, ev)

    await _watch(ws, channel, joined, message)


@router.websocket("/ws/me")
async def ws_me(ws: WebSocket):
    token = ws.query_params.get("token")
    uid = decode_token(token) if token else None
    if not uid:
        await ws.close(code=4401)
        return
    await ws.accept()
    last = 0
    async with session_maker()() as s:
        last = (await s.execute(select(Notification.id).where(Notification.user_id == uid)
                                .order_by(Notification.id.desc()).limit(1))).scalar_one_or_none() or 0

    async def pump():
        nonlocal last
        while True:
            await asyncio.sleep(1.5)
            async with session_maker()() as s:
                rows = (await s.execute(select(Notification).where(Notification.user_id == uid, Notification.id > last)
                                        .order_by(Notification.id))).scalars().all()
            for n in rows:
                last = n.id
                await ws.send_json({"type": "notification", "id": n.id, "kind": n.kind, "title": n.title,
                                    "body": n.body, "link": n.link})

    task = asyncio.create_task(pump())
    try:
        while True:
            await ws.receive_text()
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        task.cancel()
