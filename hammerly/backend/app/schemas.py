from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

Cents = Field(gt=0, le=100_000_000)


class RegisterIn(BaseModel):
    email: str = Field(min_length=5, max_length=255, pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
    password: str = Field(min_length=8, max_length=128)
    display_name: str = Field(min_length=2, max_length=40)


class LoginIn(BaseModel):
    email: str
    password: str


class MeUpdate(BaseModel):
    calm_mode: bool | None = None
    daily_cap_cents: int | None = Field(default=None, ge=0, le=100_000_000)
    display_name: str | None = Field(default=None, min_length=2, max_length=40)


class TopupIn(BaseModel):
    amount_cents: int = Cents


class ListingIn(BaseModel):
    title: str = Field(min_length=3, max_length=160)
    description: str = Field(default="", max_length=5000)
    category: str = Field(min_length=2, max_length=40)
    format: Literal["english", "dutch", "sealed"]
    start_price_cents: int = Cents
    reserve_cents: int = Field(default=0, ge=0, le=100_000_000)
    shipping_cents: int | None = Field(default=None, ge=0, le=1_000_000)
    condition_grade: float | None = Field(default=None, ge=1, le=10)
    dutch_floor_cents: int = Field(default=0, ge=0)
    dutch_drop_cents_per_min: int = Field(default=0, ge=0)
    sealed_pricing: Literal["first", "second"] = "second"
    provenance: str = Field(default="", max_length=2000)
    images: list[str] = Field(default_factory=list, max_length=10)


class PublishIn(BaseModel):
    duration_seconds: int = Field(default=3600, ge=30, le=14 * 86400)
    starts_at: datetime | None = None


class BidIn(BaseModel):
    amount_cents: int = Cents
    max_cents: int | None = Field(default=None, gt=0, le=100_000_000)
    confirm: list[str] = Field(default_factory=list)


class ConfirmIn(BaseModel):
    confirm: list[str] = Field(default_factory=list)


class SquadCreateIn(BaseModel):
    name: str = Field(min_length=2, max_length=40)
    pledge_cents: int = Cents


class SquadJoinIn(BaseModel):
    pledge_cents: int = Cents


class SquadBidIn(BaseModel):
    amount_cents: int = Cents
    confirm: list[str] = Field(default_factory=list)


class WatchIn(BaseModel):
    alert_price_cents: int | None = Field(default=None, gt=0)


class ShipIn(BaseModel):
    tracking: str = Field(min_length=3, max_length=80)


class DisputeIn(BaseModel):
    reason: str = Field(min_length=10, max_length=2000)
    evidence: list[str] = Field(default_factory=list, max_length=10)


class BundleIn(BaseModel):
    order_ids: list[int] = Field(min_length=2, max_length=10)


class RateIn(BaseModel):
    stars: int = Field(ge=1, le=5)
    comment: str = Field(default="", max_length=300)


class ResolveIn(BaseModel):
    outcome: Literal["buyer", "seller"]
    note: str = Field(default="", max_length=2000)


class FlagReviewIn(BaseModel):
    status: Literal["cleared", "confirmed"]


class AuthenticateIn(BaseModel):
    status: Literal["none", "pending", "verified", "failed"]
    authenticator: str = Field(default="", max_length=80)
    cert_ref: str = Field(default="", max_length=80)


class RoomIn(BaseModel):
    title: str = Field(min_length=3, max_length=120)
    listing_ids: list[int] = Field(default_factory=list, max_length=50)
    starts_at: datetime | None = None
    playback_url: str = Field(default="", max_length=500)


class QueueIn(BaseModel):
    listing_ids: list[int] = Field(max_length=50)


class ChatIn(BaseModel):
    body: str = Field(min_length=1, max_length=300)
