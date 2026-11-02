import pytest
from sqlalchemy import select, update

from app import clock
from app.models import Escrow, LedgerEntry, Listing, ShieldFlag, Wallet
from app.services import bids, escrow, ledger, shield, wallet
from app.services.bids import BidError

from .conftest import make_listing, make_user

D = 100  # dollars to cents
RICH = 1_000_000 * 1


async def bid(session, user, listing, amount, **kw):
    try:
        r = await bids.place_bid(session, user, listing.id, amount, **kw)
        await session.commit()
        return r
    except BidError:
        raise


async def wal(session, user):
    q = select(Wallet).where(Wallet.user_id == user.id).execution_options(populate_existing=True)
    w = (await session.execute(q)).scalar_one_or_none()
    return w or Wallet(user_id=user.id, available_cents=0, held_cents=0)


async def fresh(session, listing):
    return await session.get(Listing, listing.id, populate_existing=True)


async def end_and_close(session, listing):
    l = await fresh(session, listing)
    clock.advance((l.ends_at - clock.now()).total_seconds() + 1)
    async with bids.listing_lock(l.id):
        l = await bids.lock_listing(session, l.id)
        ev = await bids.close_listing(session, l)
        await session.commit()
    return ev


@pytest.fixture
async def world(session):
    s = await make_user(session, "seller", seller=True)
    a = await make_user(session, "alice", RICH)
    b = await make_user(session, "bob", RICH)
    c = await make_user(session, "cara", RICH)
    return s, a, b, c


async def test_ledger_chain_detects_tampering(session, world):
    s, a, b, c = world
    l = await make_listing(session, s)
    await bid(session, a, l, 10_000)
    await bid(session, b, l, 11_000)
    v = await ledger.verify(session, l.id)
    assert v["ok"] and v["count"] == 3
    await session.execute(update(LedgerEntry).where(LedgerEntry.listing_id == l.id, LedgerEntry.seq == 2)
                          .values(amount_cents=1))
    await session.commit()
    v = await ledger.verify(session, l.id)
    assert not v["ok"] and v["broken_at"] == 2
    await session.execute(update(LedgerEntry).where(LedgerEntry.listing_id == l.id, LedgerEntry.seq == 2)
                          .values(amount_cents=10_000, alias="mallory"))
    await session.commit()
    v = await ledger.verify(session, l.id)
    assert not v["ok"] and v["broken_at"] == 2


async def test_outbid_releases_hold(session, world):
    s, a, b, c = world
    l = await make_listing(session, s)
    await bid(session, a, l, 10_000)
    w = await wal(session, a)
    assert w.held_cents == 10_000 and w.available_cents == RICH - 10_000
    await bid(session, b, l, 11_000)
    w = await wal(session, a)
    assert w.held_cents == 0 and w.available_cents == RICH
    assert (await wal(session, b)).held_cents == 11_000


async def test_bid_rules(session, world):
    s, a, b, c = world
    l = await make_listing(session, s)
    await bid(session, a, l, 10_000)
    with pytest.raises(BidError) as e:
        await bid(session, b, l, 10_500)
    assert e.value.code == "too_low" and e.value.extra["min_next_cents"] == 11_000
    with pytest.raises(BidError) as e:
        await bid(session, s, l, 20_000)
    assert e.value.code == "own_listing"
    poor = await make_user(session, "poor", 5_000)
    with pytest.raises(BidError) as e:
        await bid(session, poor, l, 12_000)
    assert e.value.code == "insufficient_funds" and e.value.status == 402


async def test_proxy_bidding(session, world):
    s, a, b, c = world
    l = await make_listing(session, s)
    await bid(session, a, l, 10_000, max_cents=30_000)
    r = await bid(session, b, l, 11_000)
    l = await fresh(session, l)
    assert l.leader_user_id == a.id and l.current_price_cents == 12_000
    assert (await wal(session, a)).held_cents == 12_000 and (await wal(session, b)).held_cents == 0
    assert [e["kind"] for e in r["event"]["entries"]] == ["bid", "proxy"]
    await bid(session, c, l, 20_000, max_cents=50_000)
    l = await fresh(session, l)
    assert l.leader_user_id == c.id and l.current_price_cents == 31_000
    assert (await wal(session, a)).held_cents == 0
    assert (await ledger.verify(session, l.id))["ok"]


async def test_proxy_tie_goes_to_earlier_bidder(session, world):
    s, a, b, c = world
    l = await make_listing(session, s)
    await bid(session, a, l, 10_000, max_cents=20_000)
    await bid(session, b, l, 20_000)
    l = await fresh(session, l)
    assert l.leader_user_id == a.id and l.current_price_cents == 20_000


async def test_fair_close_extends_and_caps(session, world):
    s, a, b, c = world
    l = await make_listing(session, s, duration=600)
    original_end = l.ends_at
    users = [a, b]
    for i in range(12):
        l = await fresh(session, l)
        clock.advance((l.ends_at - clock.now()).total_seconds() - 10)
        await bid(session, users[i % 2], l, bids.min_next(l), confirm={"cooloff"})
    l = await fresh(session, l)
    assert l.extensions_used == 10
    assert (l.ends_at - original_end).total_seconds() == 300
    kinds = [e.kind for e in (await session.execute(select(LedgerEntry).where(LedgerEntry.listing_id == l.id))).scalars()]
    assert kinds.count("extend") == 10


async def test_no_bids_after_end(session, world):
    s, a, b, c = world
    l = await make_listing(session, s, duration=60)
    clock.advance(61)
    with pytest.raises(BidError) as e:
        await bid(session, a, l, 10_000)
    assert e.value.code == "closed"


async def test_reserve_not_met_releases_funds(session, world):
    s, a, b, c = world
    l = await make_listing(session, s, reserve=50_000)
    await bid(session, a, l, 11_000)
    ev = await end_and_close(session, l)
    assert ev["status"] == "unsold"
    w = await wal(session, a)
    assert w.held_cents == 0 and w.available_cents == RICH


async def test_dutch_claim(session, world):
    s, a, b, c = world
    l = await make_listing(session, s, fmt="dutch", start=100_000, dutch_floor_cents=40_000,
                           dutch_drop_cents_per_min=6_000, duration=3600)
    clock.advance(60)
    assert bids.display_price(await fresh(session, l)) == 94_000
    r = await bids.claim_dutch(session, a, l.id)
    await session.commit()
    assert r["price_cents"] == 94_000 and r["closed"]["status"] == "sold"
    esc = (await session.execute(select(Escrow).where(Escrow.listing_id == l.id))).scalar_one()
    assert esc.amount_cents == 94_000 and esc.buyer_id == a.id
    w = await wal(session, a)
    assert w.held_cents == 0 and w.available_cents == RICH - 94_000
    with pytest.raises(BidError):
        await bids.claim_dutch(session, b, l.id)


async def test_dutch_price_never_below_floor(session, world):
    s, a, b, c = world
    l = await make_listing(session, s, fmt="dutch", start=100_000, dutch_floor_cents=40_000,
                           dutch_drop_cents_per_min=6_000, duration=36_000)
    clock.advance(3_000)
    assert bids.display_price(await fresh(session, l)) == 40_000


async def test_sealed_vickrey(session, world):
    s, a, b, c = world
    l = await make_listing(session, s, fmt="sealed", start=10_000, duration=600)
    for u, amt in ((a, 30_000), (b, 25_000), (c, 12_000)):
        await bids.place_sealed(session, u, l.id, amt)
        await session.commit()
    with pytest.raises(BidError) as e:
        await bids.place_sealed(session, a, l.id, 40_000)
    assert e.value.code == "already_bid"
    rows = (await session.execute(select(LedgerEntry).where(LedgerEntry.kind == "sealed"))).scalars().all()
    assert all(ledger.public_view(r, False)["amount_cents"] is None for r in rows)
    ev = await end_and_close(session, l)
    assert ev["status"] == "sold" and ev["price_cents"] == 25_000 and ev["winner_alias"] == "alice"
    assert (await wal(session, a)).available_cents == RICH - 25_000
    assert (await wal(session, b)).available_cents == RICH and (await wal(session, c)).held_cents == 0
    kinds = [e.kind for e in (await session.execute(select(LedgerEntry).where(LedgerEntry.listing_id == l.id)
                                                    .order_by(LedgerEntry.seq))).scalars()]
    assert kinds == ["open", "sealed", "sealed", "sealed", "reveal", "reveal", "reveal", "close"]
    assert (await ledger.verify(session, l.id))["ok"]


async def test_budget_guardian_requires_confirmation(session, world):
    s, a, b, c = world
    a.daily_cap_cents = 20_000
    await session.commit()
    l = await make_listing(session, s)
    with pytest.raises(BidError) as e:
        await bid(session, a, l, 25_000)
    assert e.value.code == "cap_exceeded" and e.value.extra["cap_cents"] == 20_000
    await bid(session, a, l, 25_000, confirm={"cap"})
    assert (await fresh(session, l)).leader_user_id == a.id


async def test_escrow_release_pays_seller_minus_fee(session, world):
    s, a, b, c = world
    l = await make_listing(session, s)
    await bid(session, a, l, 11_000)
    await end_and_close(session, l)
    esc = (await session.execute(select(Escrow).where(Escrow.listing_id == l.id))).scalar_one()
    assert esc.fee_cents == 880 and esc.status == "held"
    with pytest.raises(escrow.EscrowError):
        await escrow.confirm_delivery(session, a, esc.id)  # not shipped yet
    await escrow.ship(session, s, esc.id, "TRK123")
    await session.commit()
    await escrow.confirm_delivery(session, a, esc.id)
    await session.commit()
    assert (await wal(session, s)).available_cents == 11_000 - 880
    with pytest.raises(escrow.EscrowError):
        await escrow.confirm_delivery(session, a, esc.id)


async def test_dispute_refunds_buyer(session, world):
    s, a, b, c = world
    admin = await make_user(session, "admin", admin=True)
    l = await make_listing(session, s)
    await bid(session, a, l, 11_000)
    await end_and_close(session, l)
    esc = (await session.execute(select(Escrow).where(Escrow.listing_id == l.id))).scalar_one()
    await escrow.ship(session, s, esc.id, "TRK")
    d = await escrow.open_dispute(session, a, esc.id, "Not as described", [])
    await session.commit()
    await escrow.resolve_dispute(session, d.id, "buyer", "Photos show damage")
    await session.commit()
    assert (await wal(session, a)).available_cents == RICH
    assert (await wal(session, s)).available_cents == 0


async def test_auto_release_after_timeout(session, world):
    s, a, b, c = world
    l = await make_listing(session, s)
    await bid(session, a, l, 11_000)
    await end_and_close(session, l)
    esc = (await session.execute(select(Escrow).where(Escrow.listing_id == l.id))).scalar_one()
    await escrow.ship(session, s, esc.id, "TRK")
    await session.commit()
    assert await escrow.auto_release(session) == 0
    clock.advance(8 * 86400)
    assert await escrow.auto_release(session) == 1
    await session.commit()
    assert (await wal(session, s)).available_cents == 11_000 - 880


async def test_shield_blocks_seller_linked_bidder(session):
    s = await make_user(session, "seller", seller=True, device="dev-1")
    shill = await make_user(session, "shill", RICH, device="dev-1")
    l = await make_listing(session, s)
    with pytest.raises(BidError) as e:
        await bids.place_bid(session, shill, l.id, 10_000)
    assert e.value.code == "linked_to_seller"
    await session.commit()
    flags = (await session.execute(select(ShieldFlag))).scalars().all()
    assert flags and flags[0].kind == "seller_device_link"


async def test_bid_ring_pauses_payout_until_reviewed(session, world):
    s, a, b, c = world
    l = await make_listing(session, s)
    for i in range(8):
        l = await fresh(session, l)
        await bid(session, (a, b)[i % 2], l, bids.min_next(l))
    l = await fresh(session, l)
    assert l.settlement_hold is True
    flag = (await session.execute(select(ShieldFlag).where(ShieldFlag.kind == "bid_ring"))).scalars().first()
    assert flag is not None and flag.score >= 0.8
    await end_and_close(session, l)
    esc = (await session.execute(select(Escrow).where(Escrow.listing_id == l.id))).scalar_one()
    await escrow.ship(session, s, esc.id, "TRK")
    await session.commit()
    buyer = a if esc.buyer_id == a.id else b
    with pytest.raises(escrow.EscrowError) as e:
        await escrow.confirm_delivery(session, buyer, esc.id)
    assert e.value.code == "under_review"
    await shield.review(session, flag.id, "cleared")
    await session.commit()
    await escrow.confirm_delivery(session, buyer, esc.id)
    await session.commit()
    assert (await wal(session, s)).available_cents > 0


async def test_win_bundle_refunds_extra_shipping(session, world):
    s, a, b, c = world
    ids = []
    for _ in range(2):
        l = await make_listing(session, s, shipping_cents=800)
        await bid(session, a, l, 11_000)
        await end_and_close(session, l)
        ids.append((await session.execute(select(Escrow).where(Escrow.listing_id == l.id))).scalar_one().id)
    before = (await wal(session, a)).available_cents
    escs = await escrow.bundle(session, a, ids)
    await session.commit()
    assert sorted(e.shipping_cents for e in escs) == [0, 800]
    assert (await wal(session, a)).available_cents == before + 800


async def test_cooloff_after_repeated_outbids(session, world):
    s, a, b, c = world
    l = await make_listing(session, s)
    for i in range(10):
        l = await fresh(session, l)
        await bid(session, (a, b)[i % 2], l, bids.min_next(l), confirm={"cooloff"})
    l = await fresh(session, l)
    with pytest.raises(BidError) as e:
        await bid(session, a, l, bids.min_next(l))
    assert e.value.code == "cooloff"


@pytest.mark.parametrize("use_lock", [True, False])
async def test_concurrent_bids_keep_one_order(session, world, use_lock):
    """Racing bidders: exactly one gets each price and the chain stays valid.
    Without the in-process lock this relies on the database row lock (Postgres only)."""
    import asyncio
    from app.db import session_maker
    if not use_lock and "sqlite" in str(session.bind.url):
        pytest.skip("row locks need Postgres")
    s, a, b, c = world
    l = await make_listing(session, s)
    users = [await make_user(session, f"racer{i}", RICH) for i in range(8)]

    async def go(u):
        async with session_maker()() as ses:
            try:
                if use_lock:
                    async with bids.listing_lock(l.id):
                        await bids.place_bid(ses, u, l.id, 10_000)
                else:
                    await bids.place_bid(ses, u, l.id, 10_000)
                await ses.commit()
                return "ok"
            except BidError as e:
                await ses.rollback()
                return e.code

    results = await asyncio.gather(*[go(u) for u in users])
    assert results.count("ok") == 1 and results.count("too_low") == 7
    assert (await ledger.verify(session, l.id))["ok"]
    total_held = 0
    for u in users:
        total_held += (await wal(session, u)).held_cents
    assert total_held == 10_000
