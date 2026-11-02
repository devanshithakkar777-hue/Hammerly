"""Wallet balances, bid-time holds, and escrow money movement. Everything is in integer cents."""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import Hold, SquadMember, Wallet, WalletEntry


class InsufficientFunds(Exception):
    def __init__(self, needed: int, available: int, user_id: int | None = None):
        super().__init__(f"Needs {needed} cents, {available} available")
        self.needed, self.available, self.user_id = needed, available, user_id


async def get_wallet(session: AsyncSession, user_id: int, lock: bool = True) -> Wallet:
    q = select(Wallet).where(Wallet.user_id == user_id)
    if lock:
        q = q.with_for_update()
    w = (await session.execute(q)).scalar_one_or_none()
    if w is None:
        w = Wallet(user_id=user_id, available_cents=0, held_cents=0)
        session.add(w)
        await session.flush()
    return w


async def _entry(session, user_id, kind, amount, ref_type="", ref_id=0):
    session.add(WalletEntry(user_id=user_id, kind=kind, amount_cents=amount, ref_type=ref_type, ref_id=ref_id))


async def credit(session, user_id, amount, kind="topup", ref_type="", ref_id=0):
    w = await get_wallet(session, user_id)
    w.available_cents += amount
    await _entry(session, user_id, kind, amount, ref_type, ref_id)


async def hold_funds(session, user_id, amount, ref_type="listing", ref_id=0):
    w = await get_wallet(session, user_id)
    if w.available_cents < amount:
        raise InsufficientFunds(amount, w.available_cents, user_id)
    w.available_cents -= amount
    w.held_cents += amount
    await _entry(session, user_id, "hold", amount, ref_type, ref_id)


async def unhold_funds(session, user_id, amount, ref_type="listing", ref_id=0):
    w = await get_wallet(session, user_id)
    w.held_cents -= amount
    w.available_cents += amount
    await _entry(session, user_id, "unhold", amount, ref_type, ref_id)


async def capture_held(session, user_id, amount, ref_type="listing", ref_id=0):
    """Money leaves the wallet's held balance and moves into escrow."""
    w = await get_wallet(session, user_id)
    w.held_cents -= amount
    await _entry(session, user_id, "capture", amount, ref_type, ref_id)


# ---- per-listing holds (individual or squad) ---------------------------

async def get_hold(session, listing_id, user_id, squad_id=0) -> Hold | None:
    q = select(Hold).where(Hold.listing_id == listing_id, Hold.user_id == user_id, Hold.squad_id == squad_id)
    return (await session.execute(q)).scalar_one_or_none()


async def set_hold(session, listing_id, user_id, amount, squad_id=0):
    """Make the hold for this bidder on this listing equal `amount`."""
    h = await get_hold(session, listing_id, user_id, squad_id)
    cur = h.amount_cents if h else 0
    delta = amount - cur
    if delta > 0:
        await hold_funds(session, user_id, delta, "listing", listing_id)
    elif delta < 0:
        await unhold_funds(session, user_id, -delta, "listing", listing_id)
    if h:
        h.amount_cents = amount
    elif amount > 0:
        session.add(Hold(listing_id=listing_id, user_id=user_id, squad_id=squad_id, amount_cents=amount))
    await session.flush()


def split_by_pledge(amount: int, pledges: dict[int, int], owner_id: int) -> dict[int, int]:
    """Split an amount across squad members in proportion to pledges. The owner absorbs rounding."""
    total = sum(pledges.values())
    parts = {uid: amount * p // total for uid, p in pledges.items()}
    parts[owner_id] += amount - sum(parts.values())
    return parts


async def squad_pledges(session, squad_id) -> dict[int, int]:
    rows = (await session.execute(select(SquadMember).where(SquadMember.squad_id == squad_id))).scalars().all()
    return {m.user_id: m.pledge_cents for m in rows}


async def set_squad_hold(session, listing_id, squad, amount):
    """Hold each member's share of the squad's leading bid."""
    pledges = await squad_pledges(session, squad.id)
    if amount > sum(pledges.values()):
        raise InsufficientFunds(amount, sum(pledges.values()))
    for uid, share in split_by_pledge(amount, pledges, squad.owner_id).items():
        await set_hold(session, listing_id, uid, share, squad.id)


async def release_all_holds(session, listing_id, user_id=None, squad_id=None):
    q = select(Hold).where(Hold.listing_id == listing_id)
    if user_id is not None:
        q = q.where(Hold.user_id == user_id)
    if squad_id is not None:
        q = q.where(Hold.squad_id == squad_id)
    for h in (await session.execute(q)).scalars().all():
        if h.amount_cents:
            await unhold_funds(session, h.user_id, h.amount_cents, "listing", listing_id)
        await session.delete(h)
    await session.flush()


async def release_holds_for_bidder(session, listing_id, bidder_user_id, bidder_squad_id):
    if bidder_squad_id:
        await release_all_holds(session, listing_id, squad_id=bidder_squad_id)
    elif bidder_user_id:
        await release_all_holds(session, listing_id, user_id=bidder_user_id, squad_id=0)
