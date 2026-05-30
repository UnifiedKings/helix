# frontend build
FROM node:20-slim AS frontend-build

WORKDIR /frontend

COPY frontend/package*.json ./
RUN npm install
RUN npm install -D @types/react @types/react-dom

COPY frontend ./
RUN npm run build

# backend/runtime
FROM python:3.11-slim

WORKDIR /app

# system deps
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    flac \
    ca-certificates \
    git \
    curl \
    unzip \
    && rm -rf /var/lib/apt/lists/*

# deno for yt-dlp
RUN curl -fsSL https://deno.land/install.sh | sh
ENV PATH="/root/.deno/bin:${PATH}"

# backend deps
COPY backend/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# beets
RUN pip install --no-cache-dir beets

# backend code
COPY backend/app ./app

# default configs copied into image, then copied into /data on first run
COPY backend/defaults/beets/config.yaml /app/defaults/beets/config.yaml
COPY docker-entrypoint.sh /docker-entrypoint.sh
RUN chmod +x /docker-entrypoint.sh

# built frontend only
COPY --from=frontend-build /frontend/dist ./static

ENV MR_DB_PATH=/data/app.db
EXPOSE 8000

ENTRYPOINT ["/docker-entrypoint.sh"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]