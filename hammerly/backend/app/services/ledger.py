"""Proof Ledger: an append-only SHA-256 hash chain per auction, with hourly Merkle roots."""
from __future__ import annotations

import hashlib

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from .. import clock
from ..models import LedgerEntry, MerkleRoot

GENESIS = "genesis"
VISIBLE_AMOUNT_KINDS = {"bid", "proxy", "claim"}


def sha256(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def entry_hash(prev: str, seq: int, kind: str, alias: str, repr_: str) -> str:
    return sha256(f"{prev}|{seq}|{kind}|{alias}|{repr_}")


def commit(salt: str, amount_cents: int) -> str:
    return sha256(f"{salt}|{amount_cents}")


async def head(session: AsyncSession, listing_id: int) -> LedgerEntry | None:
    q = select(LedgerEntry).where(LedgerEntry.listing_id == listing_id).order_by(LedgerEntry.seq.desc()).limit(1)
    return (await session.execute(q)).scalar_one_or_none()


async def append(session: AsyncSession, listing_id: int, kind: str, alias: str, *, repr_: str,
                 user_id: int | None = None, squad_id: int | None = None,
                 amount_cents: int | None = None, commitment: str = "", salt: str = "") -> LedgerEntry:
    last = await head(session, listing_id)
    prev = last.hash if last else GENESIS
    seq = (last.seq + 1) if last else 1
    entry = LedgerEntry(listing_id=listing_id, seq=seq, kind=kind, user_id=user_id, squad_id=squad_id,
                        alias=alias, amount_cents=amount_cents, commitment=commitment, salt=salt,
                        repr=repr_, prev_hash=prev, hash=entry_hash(prev, seq, kind, alias, repr_),
                        created_at=clock.now())
    session.add(entry)
    await session.flush()
    return entry


def public_view(e: LedgerEntry, revealed: bool) -> dict:
    """Sealed amounts and salts stay hidden until the auction closes."""
    hide = e.kind == "sealed" and not revealed
    return {"seq": e.seq, "kind": e.kind, "alias": e.alias,
            "amount_cents": None if hide else e.amount_cents,
            "commitment": e.commitment or None, "salt": None if hide else (e.salt or None),
            "hash": e.hash, "prev_hash": e.prev_hash, "at": e.created_at}


async def verify(session: AsyncSession, listing_id: int) -> dict:
    """Recompute every hash from stored fields. Detects edits, deletions, and reordering."""
    rows = (await session.execute(select(LedgerEntry).where(LedgerEntry.listing_id == listing_id)
                                  .order_by(LedgerEntry.seq))).scalars().all()
    prev = GENESIS
    for i, e in enumerate(rows, start=1):
        reason = None
        if e.seq != i:
            reason = "an entry is missing or out of order"
        elif e.prev_hash != prev:
            reason = "the link to the previous entry does not match"
        elif e.kind in VISIBLE_AMOUNT_KINDS and e.repr != str(e.amount_cents):
            reason = "the recorded amount differs from the hashed amount"
        elif e.kind == "sealed" and e.repr != f"sealed:{e.commitment}":
            reason = "the sealed commitment was changed"
        elif e.kind == "reveal" and e.commitment and commit(e.salt, e.amount_cents or 0) != e.commitment:
            reason = "the revealed bid does not match its commitment"
        elif entry_hash(prev, e.seq, e.kind, e.alias, e.repr) != e.hash:
            reason = "the entry content does not match its hash"
        if reason:
            return {"ok": False, "count": len(rows), "broken_at": e.seq, "reason": reason, "head": None}
        prev = e.hash
    return {"ok": True, "count": len(rows), "broken_at": None, "reason": None,
            "head": rows[-1].hash if rows else None}


# ---- Merkle roots -------------------------------------------------------

def leaf(listing_id: int, seq: int, hash_: str) -> str:
    return sha256(f"{listing_id}|{seq}|{hash_}")


def merkle_root(leaves: list[str]) -> str:
    if not leaves:
        return sha256("empty")
    level = list(leaves)
    while len(level) > 1:
        if len(level) % 2:
            level.append(level[-1])
        level = [sha256(level[i] + level[i + 1]) for i in range(0, len(level), 2)]
    return level[0]


def merkle_proof(leaves: list[str], index: int) -> list[dict]:
    proof, level, i = [], list(leaves), index
    while len(level) > 1:
        if len(level) % 2:
            level.append(level[-1])
        sib = i ^ 1
        proof.append({"hash": level[sib], "side": "right" if sib > i else "left"})
        level = [sha256(level[j] + level[j + 1]) for j in range(0, len(level), 2)]
        i //= 2
    return proof


def check_proof(leaf_hash: str, proof: list[dict], root: str) -> bool:
    cur = leaf_hash
    for step in proof:
        cur = sha256(cur + step["hash"]) if step["side"] == "right" else sha256(step["hash"] + cur)
    return cur == root


async def publish_root(session: AsyncSession) -> MerkleRoot | None:
    """Anchor the current head of every auction whose chain changed since the last root."""
    last = (await session.execute(select(MerkleRoot).order_by(MerkleRoot.id.desc()).limit(1))).scalar_one_or_none()
    since = last.created_at if last else None
    q = select(LedgerEntry.listing_id, func.max(LedgerEntry.seq)).group_by(LedgerEntry.listing_id)
    if since:
        q = q.where(LedgerEntry.created_at > since)
    heads = (await session.execute(q.order_by(LedgerEntry.listing_id))).all()
    if not heads:
        return None
    leaves = []
    for lid, seq in heads:
        h = (await session.execute(select(LedgerEntry.hash).where(LedgerEntry.listing_id == lid,
                                                                  LedgerEntry.seq == seq))).scalar_one()
        leaves.append({"listing_id": lid, "seq": seq, "hash": h})
    root = merkle_root([leaf(x["listing_id"], x["seq"], x["hash"]) for x in leaves])
    row = MerkleRoot(root=root, leaf_count=len(leaves), leaves=leaves, created_at=clock.now())
    session.add(row)
    await session.flush()
    return row


async def anchors_for(session: AsyncSession, listing_id: int) -> list[dict]:
    """Merkle roots that include this auction, each with an inclusion proof for its anchored head."""
    out = []
    for r in (await session.execute(select(MerkleRoot).order_by(MerkleRoot.id.desc()).limit(50))).scalars():
        leaf_hashes = [leaf(x["listing_id"], x["seq"], x["hash"]) for x in r.leaves]
        for idx, x in enumerate(r.leaves):
            if x["listing_id"] == listing_id:
                proof = merkle_proof(leaf_hashes, idx)
                out.append({"root_id": r.id, "root": r.root, "published_at": r.created_at, "seq": x["seq"],
                            "head": x["hash"], "proof": proof,
                            "valid": check_proof(leaf_hashes[idx], proof, r.root)})
    return out
