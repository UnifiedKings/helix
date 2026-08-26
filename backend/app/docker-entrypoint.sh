#!/bin/sh
set -u

log() {
    printf '%s\n' "[helix-entrypoint] $*"
}

bool_enabled() {
    case "$(printf '%s' "$1" | tr '[:upper:]' '[:lower:]')" in
        1|true|yes|on) return 0 ;;
        *) return 1 ;;
    esac
}

ytdlp_version() {
    python -c 'import yt_dlp; print(yt_dlp.version.__version__)' 2>/dev/null || printf '%s' 'unknown'
}

AUTO_UPDATE="${HELIX_YTDLP_AUTO_UPDATE:-true}"
CHANNEL="$(printf '%s' "${HELIX_YTDLP_CHANNEL:-stable}" | tr '[:upper:]' '[:lower:]')"

if bool_enabled "$AUTO_UPDATE"; then
    BEFORE="$(ytdlp_version)"
    log "yt-dlp auto-update enabled (channel=${CHANNEL}, bundled/current=${BEFORE})"

    case "$CHANNEL" in
        stable)
            if python -m pip install --disable-pip-version-check --no-cache-dir --upgrade 'yt-dlp[default]'; then
                log "yt-dlp ready: $(ytdlp_version)"
            else
                log "WARNING: yt-dlp update failed; continuing with bundled/current version ${BEFORE}. YouTube playback or downloads may fail if this version is stale."
            fi
            ;;
        nightly)
            if python -m pip install --disable-pip-version-check --no-cache-dir --upgrade --pre 'yt-dlp[default]'; then
                log "yt-dlp ready: $(ytdlp_version) (nightly channel)"
            else
                log "WARNING: yt-dlp nightly update failed; continuing with bundled/current version ${BEFORE}. YouTube playback or downloads may fail if this version is stale."
            fi
            ;;
        *)
            log "WARNING: invalid HELIX_YTDLP_CHANNEL='${CHANNEL}'; expected 'stable' or 'nightly'. Skipping update and continuing with ${BEFORE}."
            ;;
    esac
else
    log "yt-dlp auto-update disabled; using bundled/current version $(ytdlp_version)"
fi

exec "$@"
