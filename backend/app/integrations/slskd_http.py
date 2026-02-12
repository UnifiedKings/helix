# app/integrations/slskd_http.py
from __future__ import annotations
from app.config import SLSKD_BASE_URL, SLSKD_API_KEY

import time
from typing import Any, Callable

import httpx


class SlskdClient:
    """
    Minimal raw-HTTP slskd client.

    - base_url should be like: http://slskd:5030  (no /api/v0)
    - we automatically append /api/v0
    - auth header: X-API-KEY
    """

    def __init__(self, base_url: str=SLSKD_BASE_URL, api_key: str=SLSKD_API_KEY, timeout: float = 20.0):
        self.base = base_url.rstrip("/") + "/api/v0"
        self.api_key = api_key
        self.timeout = timeout

        self._http = httpx.Client(
            headers={"X-API-KEY": self.api_key},
            timeout=self.timeout,
        )

    # --- lifecycle ---------------------------------------------------------

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> "SlskdClient":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    # --- core request helper ----------------------------------------------

    def _req(self, method: str, path: str, **kwargs) -> Any:
        url = f"{self.base}{path}"
        r = self._http.request(method, url, **kwargs)
        r.raise_for_status()
        if not r.content:
            return None
        ct = r.headers.get("content-type", "")
        return r.json() if "application/json" in ct else r.text

    # --- health / info -----------------------------------------------------

    def ping(self) -> dict[str, Any]:
        """
        slskd versions can differ. Try a few lightweight endpoints.
        """
        for path in ("/application", "/server", "/session", "/sessions"):
            try:
                data = self._req("GET", path)
                if isinstance(data, dict):
                    return data
                return {"ok": True, "path": path, "data": data}
            except httpx.HTTPStatusError as e:
                if e.response.status_code == 404:
                    continue
                raise
        raise RuntimeError("No known ping endpoint found. Check slskd Swagger for a basic GET endpoint.")

    # --- searches ----------------------------------------------------------

    def create_search(self, payload: dict[str, Any]) -> dict[str, Any]:
        """
        Lowest-level search creator. Pass the exact JSON your slskd expects.
        Returns the created search object (should include an 'id').
        """
        return self._req("POST", "/searches", json=payload)

    def create_text_search(self, query: str) -> str:
        """
        Convenience wrapper.
        If your slskd expects different field names, adjust here.
        """
        data = self.create_search({"searchText": query})
        if "id" not in data:
            raise RuntimeError(f"Search response missing 'id': {data}")
        return data["id"]

    def get_search(self, search_id: str) -> dict[str, Any]:
        return self._req("GET", f"/searches/{search_id}")

    def get_search_responses(self, search_id: str) -> list[dict[str, Any]]:
        return self._req("GET", f"/searches/{search_id}/responses")

    def wait_for_search_responses(
        self,
        search_id: str,
        *,
        min_results: int = 1,
        timeout_s: float = 15.0,
        poll_s: float = 1.0,
    ) -> list[dict[str, Any]]:
        """
        Poll until at least min_results responses exist (or timeout).
        """
        deadline = time.time() + timeout_s
        last: list[dict[str, Any]] = []
        while time.time() < deadline:
            try:
                last = self.get_search_responses(search_id) or []
            except httpx.HTTPStatusError:
                last = []
            if len(last) >= min_results:
                return last
            time.sleep(poll_s)
        return last

    # --- downloads / transfers --------------------------------------------

    def enqueue_download(self, username: str, payload: dict[str, Any]) -> dict[str, Any]:
        """
        Lowest-level enqueue call. slskd expects a JSON body describing files/directories.
        Use exactly what your slskd instance expects.
        """
        return self._req("POST", f"/transfers/downloads/{username}", json=payload)

    def list_downloads(self) -> list[dict[str, Any]]:
        return self._req("GET", "/transfers/downloads")

    def wait_for_download(
        self,
        *,
        predicate: Callable[[dict[str, Any]], bool],
        timeout_s: float = 600.0,
        poll_s: float = 2.0,
    ) -> dict[str, Any]:
        """
        Poll downloads until predicate(download) returns True.
        """
        deadline = time.time() + timeout_s
        while time.time() < deadline:
            for d in self.list_downloads() or []:
                if predicate(d):
                    return d
            time.sleep(poll_s)
        raise TimeoutError("Timed out waiting for download to match predicate")
