"""Bid engine. One writer per auction: callers hold the listing lock and a DB row lock.

Money rules
- Funds for the leading bid (price + shipping) are held in the bidder's wallet at bid time.
- Being outbid releases the hold. Winning converts it into escrow.
"""
from __future__ import annotations

import asyncio
import secrets
from datetime import timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from .. import clock
from ..config import get_settings
from ..models import (Hold, LedgerEntry, Listing, Notification, ProxyBid, Squad, User, Watch)
from . import ledger, wallet
from .notify import notify

_locks: dict[int, asyncio.Lock] = {}


def listing_lock(listing_id: int) -> asyncio.Lock:
    return _locks.setdefault(listing_id, asyncio.Lock())


class BidError(Exception):
    def __init__(self, code: str, message: str, status: int = 400, **extra):
        super().__init__(message)
        self.code, self.message, self.status, self.extra = code, message, status, extra


def min_increment(price_cents: int) -> int:
    if price_cents < 10_000:
        return 500
    if price_cents < 50_000:
        return 1_000
    if price_cents < 200_000:
        return 2_500
    return 5_000


def held_for(listing: Listing, price_cents: int) -> int:
    return price_cents + listing.shipping_cents


def dutch_price(listing: Listing, at) -> int:
    elapsed = max(0, int((at - (listing.starts_at or at)).total_seconds()))
    return max(listing.dutch_floor_cents, listing.start_price_cents - listing.dutch_drop_cents_per_min * elapsed // 60)


def display_price(listing: Listing, at=None) -> int:
    if listing.format == "dutch" and listing.status == "live":
        return dutch_price(listing, at or clock.now())
    return listing.current_price_cents


def min_next(listing: Listing) -> int:
    if listing.format != "english":
        return listing.start_price_cents
    if listing.leader_user_id is None and listing.leader_squad_id is None:
        return listing.start_price_cents
    return listing.current_price_cents + min_increment(listing.current_price_cents)


async def lock_listing(session: AsyncSession, listing_id: int) -> Listing:
    q = select(Listing).where(Listing.id == listing_id).with_for_update(of=Listing)
    listing = (await session.execute(q)).scalar_one_or_none()
    if listing is None:
        raise BidError("not_found", "Listing not found.", 404)
    return listing


def _ensure_live(listing: Listing, fmt: str):
    now = clock.now()
    if listing.status != "live" or (listing.ends_at and now >= listing.ends_at):
        raise BidError("closed", "This auction is not open for bids.", 409)
    if listing.format != fmt:
        raise BidError("wrong_format", f"This is a {listing.format} auction.", 409)


async def _guard(session: AsyncSession, user: User, listing: Listing, new_hold: int, confirm: set[str]):
    """Budget Guardian: daily cap and cool-off. Both can be confirmed through."""
    own = await wallet.get_hold(session, listing.id, user.id, 0)
    held = (await session.execute(select(func.coalesce(func.sum(Hold.amount_cents), 0))
                                  .where(Hold.user_id == user.id))).scalar_one()
    exposure = int(held) - (own.amount_cents if own else 0) + new_hold
    if exposure > user.daily_cap_cents and "cap" not in confirm:
        raise BidError("cap_exceeded", "This would pass your daily spending cap.", 409,
                       exposure_cents=exposure, cap_cents=user.daily_cap_cents)
    since = clock.now() - timedelta(minutes=30)
    outbid = (await session.execute(select(func.count()).select_from(Notification).where(
        Notification.user_id == user.id, Notification.kind == "outbid", Notification.created_at > since))).scalar_one()
    if outbid >= 5 and "cooloff" not in confirm:
        raise BidError("cooloff", "You have been outbid several times recently. Take a breath, then confirm.", 409,
                       outbid_count=outbid)


async def _alias_for(session, user: User | None, squad: Squad | None) -> str:
    return f"Squad {squad.name}" if squad else user.display_name


async def _check_participant(session, listing: Listing, user: User):
    if user.id == listing.seller_id:
        raise BidError("own_listing", "You cannot bid on your own listing.", 403)
    from . import shield
    await shield.check_participant(session, listing, user)


def _event(listing: Listing, entries: list[LedgerEntry], extended: bool, leader_alias: str | None) -> dict:
    return {"type": "bid", "listing_id": listing.id, "price_cents": listing.current_price_cents,
            "leader_alias": leader_alias, "bid_count": listing.bid_count, "ends_at": listing.ends_at,
            "extended": extended, "extensions_used": listing.extensions_used,
            "entries": [ledger.public_view(e, False) for e in entries]}


async def _fair_close(session, listing: Listing) -> bool:
    s = get_settings()
    now = clock.now()
    if (listing.format == "english" and listing.ends_at and listing.extensions_used < s.fair_close_max_extensions
            and listing.ends_at - now <= timedelta(seconds=s.fair_close_window_s)):
        listing.ends_at = listing.ends_at + timedelta(seconds=s.fair_close_extension_s)
        listing.extensions_used += 1
        await ledger.append(session, listing.id, "extend", "system", repr_=f"extend:{listing.ends_at.isoformat()}")
        return True
    return False


async def _proxy_row(session, listing_id, user_id) -> ProxyBid | None:
    return (await session.execute(select(ProxyBid).where(ProxyBid.listing_id == listing_id,
                                                         ProxyBid.user_id == user_id))).scalar_one_or_none()


async def place_bid(session: AsyncSession, user: User, listing_id: int, amount_cents: int,
                    max_cents: int | None = None, confirm: set[str] | None = None,
                    squad: Squad | None = None) -> dict:
    """English auction bid with proxy resolution and Fair-Close. Returns the realtime event to publish."""
    confirm = confirm or set()
    listing = await lock_listing(session, listing_id)
    _ensure_live(listing, "english")
    await _check_participant(session, listing, user)

    squad_id = squad.id if squad else 0
    leader_key = (listing.leader_user_id, listing.leader_squad_id or 0)
    has_leader = listing.leader_user_id is not None
    my_key = (squad.owner_id if squad else user.id, squad_id)
    new_max = max(amount_cents, max_cents or 0) if not squad else amount_cents
    entries: list[LedgerEntry] = []
    outbid_user: tuple[int, int] | None = None
    prev_price = listing.current_price_cents

    # Raising your own limit while already leading changes nothing publicly.
    if has_leader and leader_key == my_key:
        if squad:
            raise BidError("already_leading", "Your squad already leads.", 409)
        row = await _proxy_row(session, listing.id, user.id)
        if row:
            row.max_cents = max(row.max_cents, new_max)
        return {"event": None, "changed": False, "price_cents": listing.current_price_cents}

    floor = min_next(listing)
    if amount_cents < floor:
        raise BidError("too_low", "Your bid is below the minimum.", 409, min_next_cents=floor)

    # Effective strength of the current leader: their limit, bounded by what they can still fund.
    leader_max = 0
    leader_user: User | None = None
    leader_squad_row: Squad | None = None
    if has_leader:
        if listing.leader_squad_id:
            leader_squad_row = await session.get(Squad, listing.leader_squad_id)
            leader_max = listing.current_price_cents
        else:
            leader_user = await session.get(User, listing.leader_user_id)
            row = await _proxy_row(session, listing.id, listing.leader_user_id)
            declared = row.max_cents if row else listing.current_price_cents
            w = await wallet.get_wallet(session, listing.leader_user_id)
            h = await wallet.get_hold(session, listing.id, listing.leader_user_id, 0)
            fundable = (h.amount_cents if h else 0) + w.available_cents - listing.shipping_cents
            leader_max = max(listing.current_price_cents, min(declared, fundable))

    inc = min_increment(max(prev_price, amount_cents))
    alias_new = await _alias_for(session, user, squad)

    if not has_leader:
        price = amount_cents
        new_leads = True
    elif new_max > leader_max:
        price = min(new_max, max(amount_cents, leader_max + inc))
        new_leads = True
    else:
        price = amount_cents
        new_leads = False

    if new_leads:
        try:
            if squad:
                await wallet.set_squad_hold(session, listing.id, squad, held_for(listing, price))
            else:
                await _guard(session, user, listing, held_for(listing, price), confirm)
                await wallet.set_hold(session, listing.id, user.id, held_for(listing, price), 0)
        except wallet.InsufficientFunds as e:
            raise BidError("insufficient_funds", "Add funds to place this bid.", 402,
                           needed_cents=e.needed, available_cents=e.available)
        if has_leader:
            outbid_user = leader_key
        entries.append(await ledger.append(session, listing.id, "bid", alias_new, repr_=str(price),
                                           user_id=my_key[0], squad_id=squad.id if squad else None,
                                           amount_cents=price))
        listing.leader_user_id, listing.leader_squad_id = my_key[0], (squad.id if squad else None)
        listing.current_price_cents = price
        listing.bid_count += 1
        if not squad:
            row = await _proxy_row(session, listing.id, user.id)
            if row:
                row.max_cents = new_max
            else:
                session.add(ProxyBid(listing_id=listing.id, user_id=user.id, max_cents=new_max))
    else:
        # The challenger bids, then the leader's proxy answers.
        entries.append(await ledger.append(session, listing.id, "bid", alias_new, repr_=str(amount_cents),
                                           user_id=my_key[0], squad_id=squad.id if squad else None,
                                           amount_cents=amount_cents))
        listing.bid_count += 1
        answer = min(leader_max, new_max + inc)  # always >= the challenger's best amount
        if leader_user:
            await wallet.set_hold(session, listing.id, leader_key[0], held_for(listing, answer), 0)
        if answer > amount_cents:
            leader_alias = leader_user.display_name if leader_user else f"Squad {leader_squad_row.name}"
            entries.append(await ledger.append(session, listing.id, "proxy", leader_alias, repr_=str(answer),
                                               user_id=leader_key[0], squad_id=listing.leader_squad_id,
                                               amount_cents=answer))
            listing.bid_count += 1
        listing.current_price_cents = answer
        await notify(session, user.id, "outbid", "You were outbid",
                     f"{listing.title}: a standing proxy bid covers your amount.", f"/lots/{listing.id}")

    # Release the previous leader's funds only after the new leader's hold succeeded.
    if outbid_user:
        await wallet.release_holds_for_bidder(session, listing.id, outbid_user[0], outbid_user[1])
        if outbid_user[0] != my_key[0]:
            await notify(session, outbid_user[0], "outbid", "You were outbid",
                         f"{listing.title} is now at ${listing.current_price_cents / 100:,.2f}.", f"/lots/{listing.id}")

    extended = await _fair_close(session, listing)
    leader_alias = None
    if listing.leader_squad_id:
        sq = await session.get(Squad, listing.leader_squad_id)
        leader_alias = f"Squad {sq.name}"
    elif listing.leader_user_id:
        leader_alias = (await session.get(User, listing.leader_user_id)).display_name

    from . import shield
    await shield.after_bid(session, listing, user)
    await _watch_alerts(session, listing)
    await session.flush()
    return {"event": _event(listing, entries, extended, leader_alias), "changed": True,
            "extended": extended, "price_cents": listing.current_price_cents,
            "leading": listing.leader_user_id == my_key[0] and (listing.leader_squad_id or 0) == squad_id}


async def _watch_alerts(session, listing: Listing):
    rows = (await session.execute(select(Watch).where(Watch.listing_id == listing.id,
                                                      Watch.alert_price_cents.is_not(None),
                                                      Watch.notified_price.is_(False)))).scalars().all()
    for w in rows:
        if listing.current_price_cents >= w.alert_price_cents:
            w.notified_price = True
            await notify(session, w.user_id, "price_alert", "Price alert",
                         f"{listing.title} reached ${listing.current_price_cents / 100:,.2f}.", f"/lots/{listing.id}")


async def claim_dutch(session: AsyncSession, user: User, listing_id: int, confirm: set[str] | None = None) -> dict:
    confirm = confirm or set()
    listing = await lock_listing(session, listing_id)
    _ensure_live(listing, "dutch")
    await _check_participant(session, listing, user)
    price = dutch_price(listing, clock.now())
    try:
        await _guard(session, user, listing, held_for(listing, price), confirm)
        await wallet.set_hold(session, listing.id, user.id, held_for(listing, price), 0)
    except wallet.InsufficientFunds as e:
        raise BidError("insufficient_funds", "Add funds to claim at this price.", 402,
                       needed_cents=e.needed, available_cents=e.available)
    entry = await ledger.append(session, listing.id, "claim", user.display_name, repr_=str(price),
                                user_id=user.id, amount_cents=price)
    listing.leader_user_id, listing.current_price_cents = user.id, price
    listing.bid_count += 1
    events = await close_listing(session, listing)
    return {"event": _event(listing, [entry], False, user.display_name), "closed": events, "price_cents": price}


async def place_sealed(session: AsyncSession, user: User, listing_id: int, amount_cents: int,
                       confirm: set[str] | None = None) -> dict:
    confirm = confirm or set()
    listing = await lock_listing(session, listing_id)
    _ensure_live(listing, "sealed")
    await _check_participant(session, listing, user)
    if amount_cents < listing.start_price_cents:
        raise BidError("too_low", "Your sealed bid is below the starting price.", 409,
                       min_next_cents=listing.start_price_cents)
    existing = (await session.execute(select(LedgerEntry.id).where(
        LedgerEntry.listing_id == listing.id, LedgerEntry.kind == "sealed", LedgerEntry.user_id == user.id))).first()
    if existing:
        raise BidError("already_bid", "You already placed your one sealed bid.", 409)
    try:
        await _guard(session, user, listing, held_for(listing, amount_cents), confirm)
        await wallet.set_hold(session, listing.id, user.id, held_for(listing, amount_cents), 0)
    except wallet.InsufficientFunds as e:
        raise BidError("insufficient_funds", "Add funds to place this sealed bid.", 402,
                       needed_cents=e.needed, available_cents=e.available)
    salt = secrets.token_hex(8)
    c = ledger.commit(salt, amount_cents)
    entry = await ledger.append(session, listing.id, "sealed", user.display_name, repr_=f"sealed:{c}",
                                user_id=user.id, amount_cents=amount_cents, commitment=c, salt=salt)
    listing.bid_count += 1
    await session.flush()
    return {"event": _event(listing, [entry], False, None), "price_cents": listing.current_price_cents,
            "commitment": c}


# ---- closing -------------------------------------------------------------

async def close_listing(session: AsyncSession, listing: Listing) -> dict:
    """Settle an auction. Caller holds the lock. Returns the realtime event."""
    from . import escrow
    now = clock.now()
    winner_user = winner_squad = None
    price = 0
    status = "unsold"

    if listing.format == "sealed":
        sealed = (await session.execute(select(LedgerEntry).where(
            LedgerEntry.listing_id == listing.id, LedgerEntry.kind == "sealed")
            .order_by(LedgerEntry.amount_cents.desc(), LedgerEntry.seq))).scalars().all()
        for e in sorted(sealed, key=lambda x: x.seq):  # publish reveals in the order they were placed
            await ledger.append(session, listing.id, "reveal", e.alias, repr_=f"reveal:{e.amount_cents}:{e.salt}",
                                user_id=e.user_id, amount_cents=e.amount_cents, commitment=e.commitment, salt=e.salt)
        if sealed and sealed[0].amount_cents >= max(listing.reserve_cents, listing.start_price_cents):
            top = sealed[0]
            second = sealed[1].amount_cents if len(sealed) > 1 else 0
            price = top.amount_cents if listing.sealed_pricing == "first" else min(
                top.amount_cents, max(second, listing.reserve_cents, listing.start_price_cents))
            winner_user, status = top.user_id, "sold"
            # Losers get their funds back; the winner keeps the hold, minus any Vickrey difference.
            for e in sealed[1:]:
                await wallet.release_holds_for_bidder(session, listing.id, e.user_id, 0)
            await wallet.set_hold(session, listing.id, winner_user, held_for(listing, price), 0)
        else:
            await wallet.release_all_holds(session, listing.id)
    elif listing.format == "dutch":
        if listing.leader_user_id:
            winner_user, price, status = listing.leader_user_id, listing.current_price_cents, "sold"
        else:
            await wallet.release_all_holds(session, listing.id)
    else:
        if listing.leader_user_id or listing.leader_squad_id:
            price = listing.current_price_cents
            if price >= listing.reserve_cents:
                winner_user, winner_squad, status = listing.leader_user_id, listing.leader_squad_id, "sold"
            else:
                await wallet.release_all_holds(session, listing.id)
        else:
            await wallet.release_all_holds(session, listing.id)

    alias = "no sale"
    if status == "sold":
        if winner_squad:
            alias = f"Squad {(await session.get(Squad, winner_squad)).name}"
        else:
            alias = (await session.get(User, winner_user)).display_name
    for sq in (await session.execute(select(Squad).where(Squad.listing_id == listing.id,
                                                          Squad.status == "forming"))).scalars():
        if sq.id != winner_squad:
            sq.status = "lost"
    await ledger.append(session, listing.id, "close", "system", repr_=f"close:{status}:{alias}:{price}",
                        user_id=winner_user, amount_cents=price if status == "sold" else None)
    listing.status = status
    listing.closed_at = now
    listing.winner_user_id, listing.winner_squad_id = winner_user, winner_squad
    listing.final_price_cents = price if status == "sold" else None
    if status == "sold":
        listing.current_price_cents = price
        await escrow.create_for_sale(session, listing, winner_user, winner_squad, price)
        await notify(session, listing.seller_id, "sold", "Your item sold",
                     f"{listing.title} sold for ${price / 100:,.2f}.", f"/orders")
    else:
        await notify(session, listing.seller_id, "unsold", "Your auction ended without a sale",
                     listing.title, f"/lots/{listing.id}")
    await session.flush()
    return {"type": "closed", "listing_id": listing.id, "status": status, "price_cents": listing.final_price_cents,
            "winner_alias": alias if status == "sold" else None, "winner_user_id": winner_user}
