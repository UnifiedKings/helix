#!/usr/bin/env bash
set -e

mkdir -p /data/helix/beets

if [ ! -f /data/helix/beets/config.yaml ]; then
  echo "[helix] No Beets config found. Installing default config."
  cp /app/defaults/beets/config.yaml /data/helix/beets/config.yaml
fi

exec "$@"