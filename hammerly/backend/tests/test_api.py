import io

import httpx
import pytest

from app import clock
from app.main import create_app

pytestmark = pytest.mark.asyncio


@pytest.fixture
async def client(db, tmp_path, monkeypatch):
    monkeypatch.setenv("UPLOAD_DIR", str(tmp_path))
    from app.config import get_settings
    get_settings().upload_dir = str(tmp_path)
    app = create_app()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        yield c


async def reg(c, name):
    r = await c.post("/api/auth/register", json={"email": f"{name}@x.com", "password": "password1", "display_name": name})
    assert r.status_code in (200, 201), r.text
    return {"Authorization": "Bearer " + r.json()["token"]}


async def test_full_flow(client):
    c = client
    seller, a, b = await reg(c, "seller"), await reg(c, "alice"), await reg(c, "bobby")
    assert (await c.post("/api/me/seller", headers=seller)).status_code == 200
    png = b"\x89PNG\r\n\x1a\n" + b"0" * 64
    up = await c.post("/api/listings/images", headers=seller, files=[("files", ("a.png", io.BytesIO(png), "image/png"))])
    assert up.status_code == 200, up.text
    body = {"title": "Vintage camera", "description": "Works", "category": "cameras", "format": "english",
            "start_price_cents": 5000, "images": up.json()["images"]}
    l = await c.post("/api/listings", headers=seller, json=body)
    assert l.status_code == 201, l.text
    lid = l.json()["id"]
    p = await c.post(f"/api/listings/{lid}/publish", headers=seller, json={"duration_seconds": 3600})
    assert p.status_code == 200, p.text
    for h in (a, b):
        assert (await c.post("/api/wallet/topup", headers=h, json={"amount_cents": 100000})).status_code == 200
    assert (await c.post(f"/api/listings/{lid}/bids", headers=a, json={"amount_cents": 5000})).status_code == 200
    r = await c.post(f"/api/listings/{lid}/bids", headers=b, json={"amount_cents": 5000})
    assert r.status_code == 409 and r.json()["code"]
    assert (await c.post(f"/api/listings/{lid}/bids", headers=b, json={"amount_cents": 6000})).status_code == 200
    v = (await c.get(f"/api/listings/{lid}/verify")).json()
    assert v["ok"] and v["count"] >= 2
    clock.advance(3601)
    from app.scheduler import tick
    from app.db import session_maker
    await tick(session_maker())
    d = (await c.get(f"/api/listings/{lid}")).json()
    assert d["status"] == "sold" and d["final_price_cents"] == 6000
    orders = (await c.get("/api/orders", headers=b)).json()["items"]
    assert orders[0]["status"] == "held"
    oid = orders[0]["id"]
    assert (await c.post(f"/api/orders/{oid}/ship", headers=seller, json={"tracking": "TRK123"})).status_code == 200
    assert (await c.post(f"/api/orders/{oid}/confirm", headers=b)).status_code == 200
    assert (await c.post(f"/api/orders/{oid}/rate", headers=b, json={"stars": 5})).status_code == 200
    w = (await c.get("/api/wallet", headers=seller)).json()
    assert w["available_cents"] == 6000 - 480 + 800
