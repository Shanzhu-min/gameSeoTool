from __future__ import annotations

import gzip
import json
import socket
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any


USER_AGENT = "GameSEOToolsMVP/0.1 (+https://example.local)"


@dataclass(frozen=True)
class HttpResponse:
    url: str
    status: int
    body: bytes
    content_type: str = ""

    @property
    def text(self) -> str:
        return self.body.decode("utf-8", errors="replace")


def fetch(url: str, timeout: int = 30) -> HttpResponse:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read()
            headers = response.headers
            content_type = headers.get("Content-Type", "")
            if url.endswith(".gz") or headers.get("Content-Encoding") == "gzip":
                body = gzip.decompress(body)
            return HttpResponse(url=url, status=response.status, body=body, content_type=content_type)
    except urllib.error.HTTPError as exc:
        return HttpResponse(url=url, status=exc.code, body=exc.read())
    except (urllib.error.URLError, TimeoutError, socket.timeout, OSError) as exc:
        return HttpResponse(url=url, status=0, body=str(exc).encode("utf-8", errors="replace"))


def post_json(
    url: str,
    payload: Any,
    headers: dict[str, str] | None = None,
    timeout: int = 60,
) -> dict[str, Any]:
    body = json.dumps(payload).encode("utf-8")
    request_headers = {
        "User-Agent": USER_AGENT,
        "Content-Type": "application/json",
        **(headers or {}),
    }
    request = urllib.request.Request(url, data=body, headers=request_headers, method="POST")
    return open_json_request(request, timeout=timeout)


def get_json(
    url: str,
    headers: dict[str, str] | None = None,
    timeout: int = 60,
) -> dict[str, Any]:
    request_headers = {
        "User-Agent": USER_AGENT,
        "Content-Type": "application/json",
        **(headers or {}),
    }
    request = urllib.request.Request(url, headers=request_headers, method="GET")
    return open_json_request(request, timeout=timeout)


def open_json_request(request: urllib.request.Request, timeout: int) -> dict[str, Any]:
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8", errors="replace")
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        message = raw or exc.reason or f"HTTP {exc.code}"
        raise RuntimeError(f"HTTP {exc.code}: {message}") from exc
    except (urllib.error.URLError, TimeoutError, socket.timeout, OSError) as exc:
        raise RuntimeError(f"Network error: {exc}") from exc
