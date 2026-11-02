"""Price Atlas: comparable sales for an item. Real Hammerly sales plus optional seeded history."""
from __future__ import annotations

import re
from statistics import median

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import PriceSample

_STOP = {"the", "a", "an", "of", "and", "in", "with", "edition", "grade", "size"}


def tokens(text: str) -> set[str]:
    return {t for t in re.findall(r"[a-z0-9]+", text.lower()) if t not in _STOP and len(t) > 1}


async def comps(session: AsyncSession, category: str, title: str, limit: int = 8, exclude_listing: int | None = None) -> dict:
    rows = (await session.execute(select(PriceSample).where(PriceSample.category == category)
                                  .order_by(PriceSample.sold_at.desc()).limit(200))).scalars().all()
    if exclude_listing:
        rows = [r for r in rows if r.listing_id != exclude_listing]
    want = tokens(title)
    scored = []
    for r in rows:
        have = tokens(r.title)
        j = len(want & have) / (len(want | have) or 1)
        scored.append((j, r))
    close = [r for j, r in sorted(scored, key=lambda x: (-x[0], -x[1].sold_at.timestamp())) if j >= 0.25]
    picked = (close or [r for _, r in scored])[:limit]
    picked.sort(key=lambda r: r.sold_at)
    prices = [r.price_cents for r in picked]
    return {"count": len(picked), "matched_title": bool(close),
            "median_cents": int(median(prices)) if prices else None,
            "low_cents": min(prices) if prices else None, "high_cents": max(prices) if prices else None,
            "samples": [{"title": r.title, "price_cents": r.price_cents, "sold_at": r.sold_at, "source": r.source}
                        for r in picked]}


def suggest(comps_result: dict) -> dict:
    """Start below the typical price so bidding can begin, reserve near the median."""
    m = comps_result["median_cents"]
    if not m:
        return {"start_price_cents": None, "reserve_cents": None}
    rnd = lambda v: max(500, int(round(v / 500.0)) * 500)
    return {"start_price_cents": rnd(m * 0.6), "reserve_cents": rnd(m * 0.9)}
