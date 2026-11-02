# Hammerly

A fair, verifiable online auction platform. FastAPI + Postgres backend, React (Vite + TypeScript) frontend.

## What's in it

| Feature | Where |
|---|---|
| **Fair-Close** timer (bids in the last 30 s add 30 s, max 10 times, English only) | `backend/app/services/bids.py` |
| **Proof Ledger**: SHA-256 hash chain per auction, sealed-bid commit/reveal, hourly Merkle roots with inclusion proofs, public verifier page | `services/ledger.py`, `/verify/:id` |
| **Format Engine**: ascending, Dutch drop, sealed-bid Vickrey | `services/bids.py` |
| **Escrow at bid**: price + shipping held on bid, released on outbid, captured on win, paid out on delivery or after 7 days | `services/wallet.py`, `services/escrow.py` |
| **Authenticity Passport** (seller, authentication, provenance, photo-reuse check) | `services/passport.py` |
| **Live Rooms**: queued lots, chat, reactions, viewer count (you supply a playback URL) | `services/rooms.py`, `routers/rooms.py` |
| **Bid Cockpit / Calm Mode**: hold-to-bid; calm mode mutes pings and animation | `frontend/src/pages/Lot.tsx` |
| **Smart Proxy + Budget Guardian**: auto-bid to a max; daily cap and cool-off with explicit confirm | `services/bids.py` |
| **AI Listing Studio** (Claude when `ANTHROPIC_API_KEY` is set, heuristic draft otherwise) | `services/ai.py` |
| **Shield**: rule-based fraud signals (seller links, bid rings, shared devices, new-account high value); serious flags pause payout | `services/shield.py` |
| **Price Atlas**: comparable sales, suggested start and reserve, format recommendation | `services/atlas.py` |
| **Squad Bids** and **Win Bundle** | `services/squads.py`, `services/escrow.py` |

All money is integer cents. Each auction has a single writer (an in-process lock plus `SELECT ... FOR UPDATE`).

## Run it

### Docker (Postgres + app + built frontend)
```bash
cp .env.example .env      # set JWT_SECRET (and optionally ANTHROPIC_API_KEY)
docker compose up --build
docker compose exec app python seed.py      # optional demo data
```
Open http://localhost:8000.

### Local development
```bash
cd backend
python -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt
python seed.py                              # SQLite by default
uvicorn app.main:app --reload               # http://localhost:8000

cd ../frontend
npm install
npm run dev                                 # http://localhost:5173, proxies /api to :8000
```
Demo logins (password `password123`): `admin@hammerly.test`, `seller@hammerly.test`, `alice@hammerly.test`, `bob@hammerly.test`.

Tests: `cd backend && pytest` (SQLite). For Postgres: `TEST_DATABASE_URL=postgresql+asyncpg://... pytest`.

## Configuration (environment variables)
`DATABASE_URL`, `JWT_SECRET` (**change it**), `ADMIN_EMAILS` (comma separated; matching sign-ups become admins), `ANTHROPIC_API_KEY`, `ANTHROPIC_MODEL`, `UPLOAD_DIR`, `CORS_ORIGINS`, `SELLER_FEE_BPS` (800), `PRO_SELLER_FEE_BPS` (500), `SHIPPING_FEE_CENTS` (800), `AUTO_RELEASE_DAYS` (7), `DEFAULT_DAILY_CAP_CENTS`, `RUN_SCHEDULER`, `ROOM_LOT_SECONDS` (90).

## Honest limits: read before going live
- **Payments are a mock.** Top-ups credit instantly and no real money moves. A real provider (e.g. Stripe with manual capture) must be added behind `services/payments.py`, plus KYC and payouts. Seller verification is instant in this build.
- **No video stack.** Rooms play whatever HLS/MP4 URL the host pastes; there is no ingest or streaming server.
- **Single process.** The WebSocket hub and per-auction locks live in one process. To scale out, move the hub to Redis pub/sub; the Postgres row lock already protects bids across processes.
- **No migrations.** Tables are created at startup with `create_all`. Add Alembic before changing the schema in production.
- **Shield is rules, not ML.** It will miss things and can false-positive; admins review flags in `/admin`.
- **Photo check** detects reused photo bytes, not edited or stock images. The **ledger** proves history wasn't altered after the fact; it doesn't prove the server was honest at the time. Merkle roots are stored by us, not published to an external witness.
- Passwords use bcrypt; tokens are JWTs with no refresh or revocation. No email verification, rate limiting, or CAPTCHA yet.
- Uploads are stored on local disk.
