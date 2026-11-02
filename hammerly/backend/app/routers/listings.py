import os
import re
import secrets
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from .. import clock, serialize
from ..config import get_settings
from ..db import get_session
from ..models import Listing, Passport, User, Watch
from ..schemas import ListingIn, PublishIn, WatchIn
from ..security import optional_user, require_seller
from ..services import ai, atlas, bids, ledger, listings as lsvc, passport
from ..services.bids import BidError

router = APIRouter(tags=["listings"])
MIME_EXT = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp"}
NAME_RE = re.compile(r"^[a-f0-9]{32}\.(jpg|png|webp)$")
MAX_BYTES = 5 * 1024 * 1024


def upload_dir() -> Path:
    p = Path(get_settings().upload_dir)
    p.mkdir(parents=True, exist_ok=True)
    return p


async def _save(files: list[UploadFile]) -> list[tuple[str, bytes, str]]:
    out = []
    for f in files[:6]:
        ext = MIME_EXT.get(f.content_type or "")
        if not ext:
            raise HTTPException(415, "Photos must be JPEG, PNG, or WebP.")
        data = await f.read()
        if len(data) > MAX_BYTES:
            raise HTTPException(413, "Each photo must be under 5 MB.")
        name = f"{secrets.token_hex(16)}.{ext}"
        (upload_dir() / name).write_bytes(data)
        out.append((name, data, f.content_type))
    return out


def _hashes(names: list[str]) -> list[str]:
    out = []
    for n in names:
        if not NAME_RE.match(n):
            raise HTTPException(422, f"Unknown image '{n}'. Upload photos first.")
        path = upload_dir() / n
        if not path.exists():
            raise HTTPException(422, f"Image '{n}' was not found. Upload it again.")
        out.append(passport.sha256_bytes(path.read_bytes()))
    return out


@router.get("/listings")
async def browse(category: str | None = None, q: str | None = None, format: str | None = None,
                 status: str = "live", sort: str = "ending", limit: int = 30, offset: int = 0,
                 session: AsyncSession = Depends(get_session)):
    stmt = select(Listing).where(Listing.status == status, Listing.room_id.is_(None) | (Listing.status == "live"))
    if category:
        stmt = stmt.where(Listing.category == category)
    if format:
        stmt = stmt.where(Listing.format == format)
    if q:
        stmt = stmt.where(func.lower(Listing.title).contains(q.lower()))
    order = {"ending": Listing.ends_at.asc(), "newest": Listing.created_at.desc(),
             "price_low": Listing.current_price_cents.asc(), "price_high": Listing.current_price_cents.desc()}.get(sort, Listing.ends_at.asc())
    rows = (await session.execute(stmt.order_by(order).limit(min(limit, 60)).offset(offset))).scalars().unique().all()
    return {"items": [serialize.card(l) for l in rows], "server_time": clock.now()}


@router.get("/listings/mine")
async def mine(user: User = Depends(require_seller), session: AsyncSession = Depends(get_session)):
    rows = (await session.execute(select(Listing).where(Listing.seller_id == user.id)
                                  .order_by(Listing.id.desc()))).scalars().unique().all()
    return {"items": [serialize.card(l) for l in rows]}


@router.get("/listings/{listing_id}")
async def get_listing(listing_id: int, user: User | None = Depends(optional_user),
                      session: AsyncSession = Depends(get_session)):
    l = await session.get(Listing, listing_id)
    if l is None or (l.status == "draft" and (not user or user.id != l.seller_id)):
        raise HTTPException(404, "Listing not found.")
    return await serialize.detail(session, l, user)


@router.post("/listings", status_code=201)
async def create(body: ListingIn, user: User = Depends(require_seller), session: AsyncSession = Depends(get_session)):
    data = body.model_dump(exclude_none=True)
    hashes = _hashes(body.images)
    try:
        l = await lsvc.create_draft(session, user, data)
        if hashes:
            await passport.record_photos(session, l, hashes)
        await session.commit()
    except BidError as e:
        raise HTTPException(e.status, e.message)
    return await serialize.detail(session, l, user)


@router.put("/listings/{listing_id}")
async def update(listing_id: int, body: ListingIn, user: User = Depends(require_seller),
                 session: AsyncSession = Depends(get_session)):
    l = await session.get(Listing, listing_id)
    if l is None or l.seller_id != user.id:
        raise HTTPException(404, "Listing not found.")
    if l.status != "draft":
        raise HTTPException(409, "Only drafts can be edited.")
    for k, v in body.model_dump(exclude_none=True, exclude={"provenance", "images"}).items():
        setattr(l, k, v)
    l.current_price_cents = l.start_price_cents
    p = await session.get(Passport, l.id)
    p.provenance = body.provenance
    l.images = body.images
    p.photo_hashes = []
    if body.images:
        await passport.record_photos(session, l, _hashes(body.images))
    await session.commit()
    return await serialize.detail(session, l, user)


@router.post("/listings/{listing_id}/publish")
async def publish(listing_id: int, body: PublishIn, user: User = Depends(require_seller),
                  session: AsyncSession = Depends(get_session)):
    l = await session.get(Listing, listing_id)
    if l is None or l.seller_id != user.id:
        raise HTTPException(404, "Listing not found.")
    try:
        await lsvc.publish(session, user, l, body.duration_seconds, body.starts_at)
        await session.commit()
    except BidError as e:
        await session.rollback()
        raise HTTPException(e.status, e.message)
    return await serialize.detail(session, l, user)


@router.delete("/listings/{listing_id}")
async def cancel(listing_id: int, user: User = Depends(require_seller), session: AsyncSession = Depends(get_session)):
    async with bids.listing_lock(listing_id):
        l = await bids.lock_listing(session, listing_id)
        if l.seller_id != user.id:
            raise HTTPException(404, "Listing not found.")
        if l.status not in {"draft", "scheduled"} and not (l.status == "live" and l.bid_count == 0):
            raise HTTPException(409, "Auctions with bids cannot be cancelled.")
        l.status = "cancelled"
        await session.commit()
    return {"ok": True}


@router.post("/listings/images")
async def upload_images(files: list[UploadFile] = File(...), user: User = Depends(require_seller)):
    saved = await _save(files)
    return {"images": [n for n, _, _ in saved]}


@router.get("/listings/{listing_id}/ledger")
async def get_ledger(listing_id: int, session: AsyncSession = Depends(get_session)):
    l = await session.get(Listing, listing_id)
    if l is None or l.status == "draft":
        raise HTTPException(404, "Listing not found.")
    closed = l.status in {"sold", "unsold"}
    from ..models import LedgerEntry
    rows = (await session.execute(select(LedgerEntry).where(LedgerEntry.listing_id == l.id)
                                  .order_by(LedgerEntry.seq))).scalars().all()
    return {"entries": [ledger.public_view(e, closed) for e in rows]}


@router.get("/listings/{listing_id}/verify")
async def verify(listing_id: int, session: AsyncSession = Depends(get_session)):
    l = await session.get(Listing, listing_id)
    if l is None or l.status == "draft":
        raise HTTPException(404, "Listing not found.")
    return {**await ledger.verify(session, listing_id), "anchors": await ledger.anchors_for(session, listing_id)}


@router.post("/listings/{listing_id}/watch")
async def watch(listing_id: int, body: WatchIn, user: User | None = Depends(optional_user),
                session: AsyncSession = Depends(get_session)):
    if not user:
        raise HTTPException(401, "Sign in to watch lots.")
    w = (await session.execute(select(Watch).where(Watch.user_id == user.id, Watch.listing_id == listing_id))).scalar_one_or_none()
    if w is None:
        w = Watch(user_id=user.id, listing_id=listing_id)
        session.add(w)
    w.alert_price_cents, w.notified_price = body.alert_price_cents, False
    await session.commit()
    return {"watching": True}


@router.delete("/listings/{listing_id}/watch")
async def unwatch(listing_id: int, user: User | None = Depends(optional_user), session: AsyncSession = Depends(get_session)):
    if not user:
        raise HTTPException(401, "Sign in first.")
    w = (await session.execute(select(Watch).where(Watch.user_id == user.id, Watch.listing_id == listing_id))).scalar_one_or_none()
    if w:
        await session.delete(w)
        await session.commit()
    return {"watching": False}


@router.get("/atlas")
async def atlas_lookup(category: str, q: str = "", session: AsyncSession = Depends(get_session)):
    c = await atlas.comps(session, category, q)
    return {**c, "suggested": atlas.suggest(c)}


@router.post("/ai/listing-draft")
async def listing_draft(files: list[UploadFile] = File(default=[]), hint: str = Form(""), quick_sale: bool = Form(False),
                        user: User = Depends(require_seller), session: AsyncSession = Depends(get_session)):
    saved = await _save(files)
    draft = await ai.draft_from_images([(d, m) for _, d, m in saved], hint)
    c = await atlas.comps(session, draft["category"], draft["title"])
    sug = atlas.suggest(c)
    rec = ai.recommend_format(c["count"] if c["matched_title"] else 0, c["median_cents"], quick_sale)
    pricing = {"start_price_cents": sug["start_price_cents"], "reserve_cents": sug["reserve_cents"]}
    if rec["format"] == "dutch" and c["median_cents"]:
        pricing = {"start_price_cents": int(c["median_cents"] * 1.3) // 500 * 500,
                   "dutch_floor_cents": int(c["median_cents"] * 0.7) // 500 * 500,
                   "dutch_drop_cents_per_min": max(500, int(c["median_cents"] * 0.02) // 100 * 100), "reserve_cents": 0}
    return {"draft": draft, "images": [n for n, _, _ in saved], "comps": c, "pricing": pricing, "format": rec}
