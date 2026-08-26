FROM python:3.11-slim

WORKDIR /app

# install backend deps
COPY backend/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt


# System deps Beets commonly needs (and you likely want for your pipeline)
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    flac \
    ca-certificates \
    git \
    && rm -rf /var/lib/apt/lists/*

# Install beets (+ optional extras if you want)
RUN pip install --no-cache-dir beets

# backend code
COPY backend/app ./app
COPY backend/app/docker-entrypoint.sh /usr/local/bin/helix-entrypoint
RUN chmod +x /usr/local/bin/helix-entrypoint

# frontend static files (html + assets)
COPY frontend ./static

ENV MR_DB_PATH=/data/app.db
EXPOSE 8000

ENTRYPOINT ["/usr/local/bin/helix-entrypoint"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
