"""Database models. All money is stored as integer cents."""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import (JSON, BigInteger, Boolean, Float, ForeignKey, Index, Integer, String,
                        Text, UniqueConstraint)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from . import clock
from .db import Base, UTCDateTime


def _now() -> datetime:
    return clock.now()


class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    display_name: Mapped[str] = mapped_column(String(60), unique=True)
    is_seller: Mapped[bool] = mapped_column(Boolean, default=False)
    seller_verified: Mapped[bool] = mapped_column(Boolean, default=False)
    is_pro: Mapped[bool] = mapped_column(Boolean, default=False)
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False)
    trust_score: Mapped[float] = mapped_column(Float, default=0.0)
    ratings_count: Mapped[int] = mapped_column(Integer, default=0)
    daily_cap_cents: Mapped[int] = mapped_column(BigInteger, default=50_000)
    calm_mode: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=_now)


class UserDevice(Base):
    """Used by Shield to link accounts that share a device or network."""
    __tablename__ = "user_devices"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    device_id: Mapped[str] = mapped_column(String(80), index=True)
    ip: Mapped[str] = mapped_column(String(64), default="", index=True)
    first_seen: Mapped[datetime] = mapped_column(UTCDateTime, default=_now)
    __table_args__ = (UniqueConstraint("user_id", "device_id", "ip"),)


class Wallet(Base):
    __tablename__ = "wallets"
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), primary_key=True)
    available_cents: Mapped[int] = mapped_column(BigInteger, default=0)
    held_cents: Mapped[int] = mapped_column(BigInteger, default=0)


class WalletEntry(Base):
    __tablename__ = "wallet_entries"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    kind: Mapped[str] = mapped_column(String(30))  # topup hold unhold capture payout refund fee
    amount_cents: Mapped[int] = mapped_column(BigInteger)
    ref_type: Mapped[str] = mapped_column(String(30), default="")
    ref_id: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=_now)


class Hold(Base):
    """Funds reserved for the current leading bid of one bidder on one listing."""
    __tablename__ = "holds"
    id: Mapped[int] = mapped_column(primary_key=True)
    listing_id: Mapped[int] = mapped_column(ForeignKey("listings.id"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    squad_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    amount_cents: Mapped[int] = mapped_column(BigInteger)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=_now)
    __table_args__ = (UniqueConstraint("listing_id", "user_id", "squad_id"),)


class Listing(Base):
    __tablename__ = "listings"
    id: Mapped[int] = mapped_column(primary_key=True)
    seller_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    title: Mapped[str] = mapped_column(String(160))
    description: Mapped[str] = mapped_column(Text, default="")
    category: Mapped[str] = mapped_column(String(40), index=True)
    condition_grade: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    images: Mapped[list] = mapped_column(JSON, default=list)
    format: Mapped[str] = mapped_column(String(10))  # english dutch sealed
    start_price_cents: Mapped[int] = mapped_column(BigInteger)
    reserve_cents: Mapped[int] = mapped_column(BigInteger, default=0)
    shipping_cents: Mapped[int] = mapped_column(BigInteger, default=800)
    # Dutch
    dutch_floor_cents: Mapped[int] = mapped_column(BigInteger, default=0)
    dutch_drop_cents_per_min: Mapped[int] = mapped_column(BigInteger, default=0)
    # Sealed: "second" = Vickrey (winner pays second-highest), "first" = pays own bid
    sealed_pricing: Mapped[str] = mapped_column(String(8), default="second")
    status: Mapped[str] = mapped_column(String(12), default="draft", index=True)
    # draft scheduled live closed sold unsold cancelled
    starts_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime, nullable=True)
    ends_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime, nullable=True, index=True)
    original_ends_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime, nullable=True)
    current_price_cents: Mapped[int] = mapped_column(BigInteger, default=0)
    leader_user_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    leader_squad_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    bid_count: Mapped[int] = mapped_column(Integer, default=0)
    extensions_used: Mapped[int] = mapped_column(Integer, default=0)
    winner_user_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    winner_squad_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    final_price_cents: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    closed_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime, nullable=True)
    room_id: Mapped[Optional[int]] = mapped_column(ForeignKey("rooms.id"), nullable=True)
    settlement_hold: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=_now)

    seller: Mapped[User] = relationship(lazy="joined", foreign_keys=[seller_id])


class Passport(Base):
    """Authenticity Passport."""
    __tablename__ = "passports"
    listing_id: Mapped[int] = mapped_column(ForeignKey("listings.id"), primary_key=True)
    authentication_status: Mapped[str] = mapped_column(String(10), default="none")  # none pending verified failed
    authenticator: Mapped[str] = mapped_column(String(80), default="")
    cert_ref: Mapped[str] = mapped_column(String(80), default="")
    provenance: Mapped[str] = mapped_column(Text, default="")
    photo_check: Mapped[str] = mapped_column(String(10), default="pending")  # pending clean flagged
    photo_check_note: Mapped[str] = mapped_column(String(200), default="")
    photo_hashes: Mapped[list] = mapped_column(JSON, default=list)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=_now)


class LedgerEntry(Base):
    """Proof Ledger: one hash-chained row per auction event."""
    __tablename__ = "ledger_entries"
    id: Mapped[int] = mapped_column(primary_key=True)
    listing_id: Mapped[int] = mapped_column(ForeignKey("listings.id"), index=True)
    seq: Mapped[int] = mapped_column(Integer)
    kind: Mapped[str] = mapped_column(String(10))  # open bid proxy claim sealed extend close reveal
    user_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    squad_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    alias: Mapped[str] = mapped_column(String(80))
    amount_cents: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)  # sealed: stored but hidden until reveal
    commitment: Mapped[str] = mapped_column(String(64), default="")
    salt: Mapped[str] = mapped_column(String(64), default="")
    repr: Mapped[str] = mapped_column(String(120))   # what goes into the hash
    prev_hash: Mapped[str] = mapped_column(String(64))
    hash: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=_now)
    __table_args__ = (UniqueConstraint("listing_id", "seq"),)


class ProxyBid(Base):
    __tablename__ = "proxy_bids"
    id: Mapped[int] = mapped_column(primary_key=True)
    listing_id: Mapped[int] = mapped_column(ForeignKey("listings.id"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    max_cents: Mapped[int] = mapped_column(BigInteger)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=_now)
    __table_args__ = (UniqueConstraint("listing_id", "user_id"),)


class MerkleRoot(Base):
    __tablename__ = "merkle_roots"
    id: Mapped[int] = mapped_column(primary_key=True)
    root: Mapped[str] = mapped_column(String(64))
    leaf_count: Mapped[int] = mapped_column(Integer)
    leaves: Mapped[list] = mapped_column(JSON, default=list)  # [{listing_id, seq, hash}]
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=_now)


class Escrow(Base):
    __tablename__ = "escrows"
    id: Mapped[int] = mapped_column(primary_key=True)
    listing_id: Mapped[int] = mapped_column(ForeignKey("listings.id"), unique=True)
    buyer_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    squad_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    seller_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    amount_cents: Mapped[int] = mapped_column(BigInteger)
    shipping_cents: Mapped[int] = mapped_column(BigInteger, default=0)
    fee_cents: Mapped[int] = mapped_column(BigInteger, default=0)
    status: Mapped[str] = mapped_column(String(12), default="held")  # held shipped released disputed refunded
    tracking: Mapped[str] = mapped_column(String(80), default="")
    bundle_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    shipped_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime, nullable=True)
    release_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime, nullable=True)
    released_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=_now)


class EscrowContribution(Base):
    __tablename__ = "escrow_contributions"
    id: Mapped[int] = mapped_column(primary_key=True)
    escrow_id: Mapped[int] = mapped_column(ForeignKey("escrows.id"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    amount_cents: Mapped[int] = mapped_column(BigInteger)


class Dispute(Base):
    __tablename__ = "disputes"
    id: Mapped[int] = mapped_column(primary_key=True)
    escrow_id: Mapped[int] = mapped_column(ForeignKey("escrows.id"), index=True)
    opened_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    reason: Mapped[str] = mapped_column(Text)
    evidence: Mapped[list] = mapped_column(JSON, default=list)
    status: Mapped[str] = mapped_column(String(16), default="open")  # open resolved_buyer resolved_seller
    resolution_note: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=_now)
    resolved_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime, nullable=True)


class Rating(Base):
    __tablename__ = "ratings"
    id: Mapped[int] = mapped_column(primary_key=True)
    escrow_id: Mapped[int] = mapped_column(ForeignKey("escrows.id"), unique=True)
    rater_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    ratee_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    stars: Mapped[int] = mapped_column(Integer)
    comment: Mapped[str] = mapped_column(String(300), default="")


class Squad(Base):
    __tablename__ = "squads"
    id: Mapped[int] = mapped_column(primary_key=True)
    listing_id: Mapped[int] = mapped_column(ForeignKey("listings.id"), index=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    name: Mapped[str] = mapped_column(String(60))
    status: Mapped[str] = mapped_column(String(10), default="forming")  # forming won lost cancelled
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=_now)


class SquadMember(Base):
    __tablename__ = "squad_members"
    id: Mapped[int] = mapped_column(primary_key=True)
    squad_id: Mapped[int] = mapped_column(ForeignKey("squads.id"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    pledge_cents: Mapped[int] = mapped_column(BigInteger)  # the most this member will fund
    __table_args__ = (UniqueConstraint("squad_id", "user_id"),)


class ShieldFlag(Base):
    __tablename__ = "shield_flags"
    id: Mapped[int] = mapped_column(primary_key=True)
    listing_id: Mapped[int] = mapped_column(ForeignKey("listings.id"), index=True)
    user_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    kind: Mapped[str] = mapped_column(String(30))
    score: Mapped[float] = mapped_column(Float)
    details: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(10), default="open")  # open cleared confirmed
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=_now)


class Watch(Base):
    __tablename__ = "watches"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    listing_id: Mapped[int] = mapped_column(ForeignKey("listings.id"), index=True)
    alert_price_cents: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    notified_last_hour: Mapped[bool] = mapped_column(Boolean, default=False)
    notified_price: Mapped[bool] = mapped_column(Boolean, default=False)
    __table_args__ = (UniqueConstraint("user_id", "listing_id"),)


class Notification(Base):
    __tablename__ = "notifications"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    kind: Mapped[str] = mapped_column(String(30))
    title: Mapped[str] = mapped_column(String(160))
    body: Mapped[str] = mapped_column(String(300), default="")
    link: Mapped[str] = mapped_column(String(120), default="")
    read: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=_now)


class Room(Base):
    __tablename__ = "rooms"
    id: Mapped[int] = mapped_column(primary_key=True)
    host_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    title: Mapped[str] = mapped_column(String(120))
    status: Mapped[str] = mapped_column(String(10), default="scheduled")  # scheduled live ended
    starts_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime, nullable=True)
    queue: Mapped[list] = mapped_column(JSON, default=list)  # listing ids in order
    current_listing_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    stream: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=_now)


class RoomMessage(Base):
    __tablename__ = "room_messages"
    id: Mapped[int] = mapped_column(primary_key=True)
    room_id: Mapped[int] = mapped_column(ForeignKey("rooms.id"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    alias: Mapped[str] = mapped_column(String(60))
    body: Mapped[str] = mapped_column(String(300))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=_now)


class PriceSample(Base):
    """Comparable sales for Price Atlas. Real sales are added when auctions sell."""
    __tablename__ = "price_samples"
    id: Mapped[int] = mapped_column(primary_key=True)
    category: Mapped[str] = mapped_column(String(40), index=True)
    title: Mapped[str] = mapped_column(String(160))
    price_cents: Mapped[int] = mapped_column(BigInteger)
    sold_at: Mapped[datetime] = mapped_column(UTCDateTime, default=_now)
    source: Mapped[str] = mapped_column(String(12), default="hammerly")  # hammerly seed
    listing_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)


Index("ix_ledger_listing_seq", LedgerEntry.listing_id, LedgerEntry.seq)
