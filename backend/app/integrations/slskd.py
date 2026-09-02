from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass
from pathlib import PurePath
from typing import Any

import httpx

LOG = logging.getLogger(__name__)


@dataclass
class SlskdCandidate:
    username: str
    filename: str
    size: int
    bitrate: int = 0
    sample_rate: int = 0
    bit_depth: int = 0
    duration_ms: int = 0
    free_upload_slots: bool = False
    queue_length: int = 0

    @property
    def extension(self) -> str:
        name = self.filename.replace("\\", "/")
        return PurePath(name).suffix.lower()


def _int(value: Any) -> int:
    try:
        if value is None or value == "":
            return 0
        return int(float(value))
    except (TypeError, ValueError):
        return 0


# Soulseek file attributes can be serialized by slskd either with a readable
# name ("BitRate", "Length", etc.) or with the numeric Soulseek attribute ID.
# Numeric IDs used by the Soulseek protocol:
#   0 = bitrate (kbps)
#   1 = length (seconds)
#   2 = variable bitrate flag
#   4 = sample rate (Hz)
#   5 = bit depth
_ATTRIBUTE_NAMES = {
    "0": "bitrate",
    "bitrate": "bitrate",
    "bit_rate": "bitrate",
    "1": "length",
    "length": "length",
    "duration": "length",
    "durationseconds": "length",
    "duration_seconds": "length",
    "4": "sample_rate",
    "samplerate": "sample_rate",
    "sample_rate": "sample_rate",
    "5": "bit_depth",
    "bitdepth": "bit_depth",
    "bit_depth": "bit_depth",
}


def _normalized_attributes(raw: Any) -> dict[str, int]:
    out: dict[str, int] = {}

    if isinstance(raw, dict):
        items = raw.items()
    elif isinstance(raw, list):
        items = []
        for entry in raw:
            if not isinstance(entry, dict):
                continue
            key = (
                entry.get("type")
                if entry.get("type") is not None
                else entry.get("name")
                if entry.get("name") is not None
                else entry.get("id")
            )
            value = entry.get("value")
            items.append((key, value))
    else:
        return out

    for key, value in items:
        normalized_key = str(key or "").strip().lower().replace(" ", "").replace("-", "_")
        canonical = _ATTRIBUTE_NAMES.get(normalized_key)
        if not canonical:
            # Some JSON serializers produce enum names such as "BitRate".
            collapsed = normalized_key.replace("_", "")
            canonical = _ATTRIBUTE_NAMES.get(collapsed)
        if canonical:
            out[canonical] = _int(value)

    return out


def _candidate_from_raw(response: dict[str, Any], item: dict[str, Any]) -> SlskdCandidate | None:
    filename = str(item.get("filename") or item.get("fileName") or "").strip()
    if not filename:
        return None

    attrs = _normalized_attributes(item.get("attributes") or {})

    bitrate = _int(
        item.get("bitRate")
        or item.get("bitrate")
        or attrs.get("bitrate")
    )
    # Soulseek's bitrate attribute is commonly reported in kbps while some API
    # fields may already be bps. Store bps internally for consistency with
    # mutagen/Helix quality metadata.
    if 0 < bitrate < 10000:
        bitrate *= 1000

    sample_rate = _int(
        item.get("sampleRate")
        or item.get("sample_rate")
        or attrs.get("sample_rate")
    )
    bit_depth = _int(
        item.get("bitDepth")
        or item.get("bit_depth")
        or attrs.get("bit_depth")
    )

    duration_seconds = _int(
        item.get("length")
        or item.get("duration")
        or item.get("durationSeconds")
        or item.get("duration_seconds")
        or attrs.get("length")
    )

    return SlskdCandidate(
        username=str(response.get("username") or response.get("user") or "").strip(),
        filename=filename,
        size=_int(item.get("size")),
        bitrate=bitrate,
        sample_rate=sample_rate,
        bit_depth=bit_depth,
        duration_ms=duration_seconds * 1000 if duration_seconds > 0 else 0,
        free_upload_slots=bool(
            response.get("hasFreeUploadSlot")
            if response.get("hasFreeUploadSlot") is not None
            else response.get("freeUploadSlots")
        ),
        queue_length=_int(response.get("queueLength") or response.get("uploadQueueLength")),
    )


class SlskdClient:
    def __init__(self, base_url: str, api_key: str, timeout_s: float = 20.0):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self._http = httpx.AsyncClient(
            base_url=self.base_url,
            timeout=timeout_s,
            follow_redirects=True,
            headers={"X-API-Key": api_key},
        )

    async def close(self) -> None:
        await self._http.aclose()

    async def test_connection(self) -> dict[str, Any]:
        for path in ("/api/v0/session", "/api/v0/searches"):
            try:
                r = await self._http.get(path)
                if r.status_code < 400:
                    return {"ok": True, "status_code": r.status_code}
                if r.status_code in (401, 403):
                    return {"ok": False, "status_code": r.status_code, "error": "Authentication failed"}
            except Exception as exc:
                last = str(exc)
        return {"ok": False, "error": locals().get("last", "Could not connect to slskd")}

    async def search(self, query: str, *, timeout_s: float = 35.0, max_results: int = 200) -> list[SlskdCandidate]:
        # Generate the search ID client-side and send it explicitly.
        search_id = str(uuid.uuid4())

        # This is the lifetime slskd itself should use for the Soulseek search.
        # Keep it within a sane range, but do not use it as Helix's "no match"
        # deadline. slskd is authoritative about whether the search is complete.
        search_timeout_ms = max(5000, min(120000, int(float(timeout_s) * 1000)))
        create_payload = {
            "id": search_id,
            "searchText": query,
            "searchTimeout": search_timeout_ms,
            "fileLimit": max(1000, int(max_results) * 10),
            "responseLimit": max(100, int(max_results) * 2),
        }

        r = await self._http.post("/api/v0/searches", json=create_payload)
        r.raise_for_status()

        LOG.info(
            "[slskd] search started id=%s query=%r timeout_ms=%s",
            search_id,
            query,
            search_timeout_ms,
        )

        loop = asyncio.get_running_loop()

        # Hard safety timeout only. This is deliberately much longer than the
        # normal slskd search window and exists solely to prevent a permanently
        # stuck InProgress search from hanging a worker forever.
        #
        # IMPORTANT: hitting this deadline is an error, not "no match".
        hard_timeout_s = max(180.0, float(timeout_s) + 120.0)
        hard_deadline = loop.time() + hard_timeout_s

        state: dict[str, Any] = {}
        last_response_count = -1
        last_file_count = -1

        while True:
            state_resp = await self._http.get(
                f"/api/v0/searches/{search_id}",
                params={"includeResponses": "true"},
            )
            state_resp.raise_for_status()
            state = state_resp.json() or {}

            raw_responses = state.get("responses") or state.get("results") or []
            if not isinstance(raw_responses, list):
                raw_responses = []

            file_count = 0
            for response in raw_responses:
                if isinstance(response, dict):
                    files = response.get("files") or response.get("results") or []
                    if isinstance(files, list):
                        file_count += len(files)

            response_count = len(raw_responses)
            if response_count != last_response_count or file_count != last_file_count:
                last_response_count = response_count
                last_file_count = file_count
                LOG.info(
                    "[slskd] search id=%s state=%r complete=%r responses=%s files=%s",
                    search_id,
                    state.get("state"),
                    state.get("isComplete"),
                    response_count,
                    file_count,
                )

            is_complete = state.get("isComplete") is True
            state_name = str(
                state.get("state") or state.get("searchState") or ""
            ).strip().lower()

            # slskd is authoritative. If it says the search is still in
            # progress, keep waiting regardless of Helix's normal search timeout.
            if is_complete:
                break

            if state_name in {
                "completed",
                "complete",
                "finished",
                "cancelled",
                "canceled",
                "failed",
                "errored",
                "error",
            }:
                break

            if loop.time() >= hard_deadline:
                LOG.error(
                    "[slskd] search hard-timeout id=%s state=%r complete=%r responses=%s files=%s",
                    search_id,
                    state.get("state"),
                    state.get("isComplete"),
                    response_count,
                    file_count,
                )
                raise TimeoutError(
                    f"slskd search {search_id} remained "
                    f"{state.get('state') or 'in progress'} for more than "
                    f"{int(hard_timeout_s)} seconds"
                )

            await asyncio.sleep(1.0)

        # Search is now complete (or terminal). Fetch once more to capture any
        # responses that landed at the completion boundary.
        final_resp = await self._http.get(
            f"/api/v0/searches/{search_id}",
            params={"includeResponses": "true"},
        )
        final_resp.raise_for_status()
        final_state = final_resp.json() or {}
        if final_state:
            state = final_state

        raw_responses = state.get("responses") or state.get("results") or []
        if not isinstance(raw_responses, list):
            raw_responses = []

        out: list[SlskdCandidate] = []
        raw_file_count = 0

        for response in raw_responses:
            if not isinstance(response, dict):
                continue

            files = response.get("files") or response.get("results") or []
            if not isinstance(files, list):
                continue

            raw_file_count += len(files)
            for item in files:
                if not isinstance(item, dict):
                    continue

                candidate = _candidate_from_raw(response, item)
                if candidate is None:
                    continue

                out.append(candidate)
                if len(out) >= max_results:
                    LOG.info(
                        "[slskd] search id=%s parsed_candidates=%s raw_files=%s (capped)",
                        search_id,
                        len(out),
                        raw_file_count,
                    )
                    return out

        LOG.info(
            "[slskd] search id=%s parsed_candidates=%s raw_files=%s responses=%s state=%r complete=%r",
            search_id,
            len(out),
            raw_file_count,
            len(raw_responses),
            state.get("state"),
            state.get("isComplete"),
        )

        if not out:
            LOG.warning(
                "[slskd] completed zero-result search id=%s final_keys=%s state=%r "
                "search_state=%r is_complete=%r response_count=%r file_count=%r",
                search_id,
                sorted(state.keys()) if isinstance(state, dict) else [],
                state.get("state") if isinstance(state, dict) else None,
                state.get("searchState") if isinstance(state, dict) else None,
                state.get("isComplete") if isinstance(state, dict) else None,
                state.get("responseCount") if isinstance(state, dict) else None,
                state.get("fileCount") if isinstance(state, dict) else None,
            )

        # Deliberately do not delete searches. slskd handles retention and keeping
        # them visible is useful for debugging/auditing.
        return out

    async def enqueue_download(self, candidate: SlskdCandidate) -> None:
        payload = [
            {
                "filename": candidate.filename,
                "size": int(candidate.size or 0),
            }
        ]
        r = await self._http.post(
            f"/api/v0/transfers/downloads/{candidate.username}",
            json=payload,
        )
        if r.is_error:
            body = ""
            try:
                body = r.text[:2000]
            except Exception:
                pass
            LOG.error(
                "[slskd] download enqueue failed user=%r file=%r size=%s status=%s body=%r",
                candidate.username,
                candidate.filename,
                candidate.size,
                r.status_code,
                body,
            )
        r.raise_for_status()

    async def find_download_transfer(
        self,
        candidate: SlskdCandidate,
    ) -> dict | None:
        """Return the queued/download transfer for this exact Soulseek result."""
        try:
            r = await self._http.get(
                f"/api/v0/transfers/downloads/{candidate.username}"
            )
            r.raise_for_status()
            payload = r.json()
        except Exception:
            LOG.exception(
                "[slskd] could not inspect downloads for user=%r",
                candidate.username,
            )
            return None

        wanted_name = str(candidate.filename or "").replace("\\", "/").rsplit("/", 1)[-1]
        wanted_size = int(candidate.size or 0)

        def walk(value):
            if isinstance(value, dict):
                yield value
                for child in value.values():
                    yield from walk(child)
            elif isinstance(value, list):
                for child in value:
                    yield from walk(child)

        best = None
        for row in walk(payload):
            raw_name = str(
                row.get("filename")
                or row.get("fileName")
                or row.get("remoteFilename")
                or ""
            )
            name = raw_name.replace("\\", "/").rsplit("/", 1)[-1]
            try:
                size = int(row.get("size") or row.get("fileSize") or 0)
            except Exception:
                size = 0

            if name != wanted_name:
                continue
            if wanted_size and size and size != wanted_size:
                continue
            best = row
            break

        if best is None:
            LOG.warning(
                "[slskd] enqueue returned success but transfer not found user=%r file=%r size=%s",
                candidate.username,
                candidate.filename,
                candidate.size,
            )
            return None

        state = str(best.get("state") or best.get("status") or "")
        LOG.info(
            "[slskd] transfer verified user=%r file=%r state=%r",
            candidate.username,
            candidate.filename,
            state,
        )
        return best

    async def wait_for_download_transfer(
        self,
        candidate: SlskdCandidate,
        timeout_s: float = 8.0,
    ) -> dict | None:
        """Give slskd a few seconds to materialize the queued transfer."""
        deadline = asyncio.get_running_loop().time() + max(1.0, float(timeout_s))
        while asyncio.get_running_loop().time() < deadline:
            transfer = await self.find_download_transfer(candidate)
            if transfer is not None:
                return transfer
            await asyncio.sleep(0.75)
        return None


    async def downloads(self) -> Any:
        r = await self._http.get("/api/v0/transfers/downloads")
        r.raise_for_status()
        return r.json()
