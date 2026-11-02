"""Authenticity Passport: what Hammerly can actually verify about a seller and an item."""
from __future__ import annotations

import hashlib

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .. import clock
from ..models import Listing, Passport, User


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


async def record_photos(session: AsyncSession, listing: Listing, new_hashes: list[str]) -> Passport:
    """Photo check: the exact same image file listed by another seller is a strong reuse signal.
    This catches copied listing photos, not clever edits. Perceptual matching is a later upgrade."""
    p = await session.get(Passport, listing.id)
    p.photo_hashes = list(dict.fromkeys(list(p.photo_hashes or []) + new_hashes))
    reused_from = None
    others = (await session.execute(select(Passport, Listing).join(Listing, Listing.id == Passport.listing_id)
                                    .where(Passport.listing_id != listing.id, Listing.seller_id != listing.seller_id))).all()
    mine = set(p.photo_hashes)
    for op, ol in others:
        if mine & set(op.photo_hashes or []):
            reused_from = ol.id
            break
    if reused_from:
        p.photo_check, p.photo_check_note = "flagged", f"A photo also appears on listing {reused_from} from another seller."
    elif p.photo_hashes:
        p.photo_check, p.photo_check_note = "clean", "No photo reuse found. Edit detection is not run."
    p.updated_at = clock.now()
    await session.flush()
    return p


async def set_authentication(session: AsyncSession, listing_id: int, status: str, authenticator: str, cert_ref: str):
    if status not in {"none", "pending", "verified", "failed"}:
        raise ValueError("bad status")
    p = await session.get(Passport, listing_id)
    p.authentication_status, p.authenticator, p.cert_ref = status, authenticator[:80], cert_ref[:80]
    p.updated_at = clock.now()
    await session.flush()
    return p


def view(listing: Listing, p: Passport, seller: User) -> dict:
    auth_detail = {"none": "Not authenticated.", "pending": "Authentication requested.",
                   "failed": "Authentication failed.",
                   "verified": f"Verified by {p.authenticator or 'an authenticator'}"
                               + (f", certificate {p.cert_ref}." if p.cert_ref else ".")}[p.authentication_status]
    checks = [
        {"key": "seller", "label": "Seller", "status": "verified" if seller.seller_verified else "none",
         "detail": "Identity and payout account verified." if seller.seller_verified else "Seller is not verified."},
        {"key": "authentication", "label": "Authentication", "status": p.authentication_status, "detail": auth_detail},
        {"key": "provenance", "label": "Provenance", "status": "verified" if p.provenance else "none",
         "detail": p.provenance or "The seller gave no provenance."},
        {"key": "photos", "label": "Photo check", "status": {"clean": "verified", "flagged": "failed"}.get(p.photo_check, "pending"),
         "detail": p.photo_check_note or "Waiting for photos."},
    ]
    ok = sum(1 for c in checks if c["status"] == "verified")
    return {"checks": checks, "verified_count": ok, "total": len(checks)}
