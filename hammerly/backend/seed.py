"""Seed demo data: python seed.py   (safe to re-run; skips if users exist)."""
import asyncio
import hashlib
import random
from datetime import timedelta
from pathlib import Path

from sqlalchemy import func, select

from app import clock, db
from app.config import get_settings
from app.models import PriceSample, User
from app.security import hash_password
from app.services import bids, listings, passport, wallet

PALETTES = [("#3347F0", "#9AA6FF"), ("#E5484D", "#FFB4B6"), ("#1B9A6C", "#9FE3C8"), ("#C77B12", "#F6D08C"), ("#7A3FE0", "#CBB2FF")]
LOTS = [
    ("1968 Leica M4 rangefinder", "cameras", "english", 94000, 110000, 8.5, "Bought new by the seller's grandfather in Frankfurt; serviced 2022."),
    ("Mechanical keyboard, hand-lubed 75%", "electronics", "english", 18000, 0, 9.0, ""),
    ("Signed first edition: The God of Small Things", "books", "english", 42000, 50000, 8.0, "Signed at a 1997 reading; receipt included."),
    ("Mid-century teak sideboard", "furniture", "dutch", 90000, 0, 7.5, ""),
    ("Omega Seamaster 300, 2005", "watches", "sealed", 150000, 200000, 8.8, "Full set with papers."),
    ("Vintage Pokédex tin collection", "collectibles", "english", 6000, 0, 8.0, ""),
    ("Fender Jazzmaster, 1996 Japan", "music", "english", 85000, 95000, 8.2, "Original case, new strings."),
    ("Hand-knotted Kashan rug 6x9", "home", "dutch", 120000, 0, 9.0, ""),
]
COMPS = {"cameras": 90000, "electronics": 16000, "books": 38000, "furniture": 70000, "watches": 190000,
         "collectibles": 5500, "music": 82000, "home": 95000}


def image(name: str, i: int) -> str:
    a, b = PALETTES[i % len(PALETTES)]
    fn = hashlib.sha256(name.encode()).hexdigest()[:16] + ".svg"
    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 800 600"><defs><linearGradient id="g" x1="0" y1="0" x2="1" y2="1">'
           f'<stop offset="0" stop-color="{a}"/><stop offset="1" stop-color="{b}"/></linearGradient></defs>'
           f'<rect width="800" height="600" fill="url(#g)"/><text x="400" y="320" font-family="sans-serif" font-size="34" '
           f'fill="#fff" text-anchor="middle">{name[:28]}</text></svg>')
    d = Path(get_settings().upload_dir)
    d.mkdir(parents=True, exist_ok=True)
    (d / fn).write_text(svg)
    return fn


async def main():
    s = get_settings()
    db.init_engine(s.database_url)
    await db.create_all()
    async with db.session_maker()() as session:
        if (await session.execute(select(func.count()).select_from(User))).scalar_one():
            print("Users exist; skipping seed.")
            return
        def mk(email, name, **kw):
            u = User(email=email, password_hash=hash_password("password123"), display_name=name,
                     daily_cap_cents=50_000_00, **kw)
            session.add(u)
            return u
        admin = mk("admin@hammerly.test", "Admin", is_admin=True)
        seller = mk("seller@hammerly.test", "Mira's Vintage", is_seller=True, seller_verified=True, trust_score=4.8, ratings_count=41)
        alice, bob = mk("alice@hammerly.test", "alice"), mk("bob@hammerly.test", "bob")
        await session.flush()
        for u in (alice, bob):
            await wallet.credit(session, u.id, 5_000_000)
        for cat, med in COMPS.items():
            for k in range(6):
                session.add(PriceSample(category=cat, title=f"{cat} comp {k}", price_cents=int(med * random.uniform(0.8, 1.2)),
                                        sold_at=clock.now() - timedelta(days=random.randint(2, 90)), source="seed"))
        for i, (title, cat, fmt, start, reserve, grade, prov) in enumerate(LOTS):
            data = {"title": title, "description": f"{title}. Seeded demo lot.", "category": cat, "format": fmt,
                    "start_price_cents": start, "reserve_cents": reserve, "condition_grade": grade,
                    "provenance": prov, "images": [image(title, i)]}
            if fmt == "dutch":
                data.update(start_price_cents=start, dutch_floor_cents=int(start * 0.6),
                            dutch_drop_cents_per_min=max(100, start // 400))
            l = await listings.create_draft(session, seller, data)
            await passport.record_photos(session, l, [hashlib.sha256(title.encode()).hexdigest()])
            await listings.publish(session, seller, l, random.choice([1800, 7200, 3 * 3600, 86400]))
        await session.commit()
    print("Seeded. Logins (password: password123): admin@, seller@, alice@, bob@hammerly.test")


if __name__ == "__main__":
    asyncio.run(main())
