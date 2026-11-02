# Stage 1: build the React app
FROM node:22-alpine AS web
WORKDIR /web
COPY frontend/package*.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

# Stage 2: API + built frontend in one image
FROM python:3.13-slim
WORKDIR /app
COPY backend/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY backend/app ./app
COPY backend/seed.py ./
COPY --from=web /web/dist /app/frontend_dist
ENV FRONTEND_DIST=/app/frontend_dist UPLOAD_DIR=/data/uploads
EXPOSE 8000
# SEED_DEMO=true loads demo data on first start (skipped if users already exist)
CMD ["sh", "-c", "if [ \"$SEED_DEMO\" = true ]; then python seed.py; fi; exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000} --proxy-headers --forwarded-allow-ips=*"]
