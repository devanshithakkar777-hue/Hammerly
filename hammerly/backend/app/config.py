from functools import lru_cache
from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "sqlite+aiosqlite:///./hammerly.db"
    jwt_secret: str = "change-me-in-production"
    jwt_minutes: int = 60 * 24 * 7
    upload_dir: str = "./uploads"
    cors_origins: str = "http://localhost:5173,http://localhost:8000"

    @field_validator("database_url")
    @classmethod
    def _driver(cls, v: str) -> str:
        # Render and Heroku hand out postgres:// URLs; SQLAlchemy's async driver needs postgresql+asyncpg://
        for old in ("postgres://", "postgresql://"):
            if v.startswith(old):
                return "postgresql+asyncpg://" + v[len(old):]
        return v

    # Fair-Close
    fair_close_window_s: int = 30
    fair_close_extension_s: int = 30
    fair_close_max_extensions: int = 10

    # Money
    seller_fee_bps: int = 800          # 8% standard sellers
    pro_seller_fee_bps: int = 500      # 5% verified pro sellers
    shipping_fee_cents: int = 800
    auto_release_days: int = 7
    default_daily_cap_cents: int = 50_000

    # Payments: "mock" credits wallets instantly (test mode)
    payment_provider: str = "mock"

    # AI Listing Studio: falls back to heuristics when no key is set
    anthropic_api_key: str = ""
    anthropic_model: str = "claude-sonnet-5-5"

    # Live video: "none" returns no stream; plug LiveKit/Daily/Mux here
    stream_provider: str = "none"

    admin_emails: str = ""             # comma-separated; these accounts become admins on register
    run_scheduler: bool = True
    scheduler_tick_s: float = 1.0
    room_lot_seconds: int = 90


@lru_cache
def get_settings() -> Settings:
    return Settings()
