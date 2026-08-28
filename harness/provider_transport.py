"""Bounded direct HTTP transport worker for the MARB cohort executor.

This helper is launched as a short-lived subprocess so the parent can enforce
one hard wall-clock timeout across connect, request, headers, and response
reads. It never uses ambient proxy configuration and never emits credentials.
"""
from __future__ import annotations

import base64
import json
import sys
import urllib.error
import urllib.parse
import urllib.request
from typing import Any


MAX_INPUT_BYTES = 12_000_000
MAX_RESPONSE_BYTES = 16_000_000


class _RejectRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _origin(value: str) -> tuple[str, str]:
    parsed = urllib.parse.urlsplit(value)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("unsafe endpoint")
    host = parsed.hostname.lower()
    if parsed.scheme == "http" and host not in {"127.0.0.1", "::1", "localhost"}:
        raise ValueError("plaintext endpoint is not loopback")
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    port = parsed.port
    default = 443 if parsed.scheme == "https" else 80
    authority = host if port in {None, default} else f"{host}:{port}"
    return value, f"{parsed.scheme}://{authority}"


def _load_request() -> dict[str, Any]:
    raw = sys.stdin.buffer.read(MAX_INPUT_BYTES + 1)
    if len(raw) > MAX_INPUT_BYTES:
        raise ValueError("request too large")
    value = json.loads(raw.decode("ascii"))
    if not isinstance(value, dict) or set(value) != {
        "endpoint",
        "approved_origin",
        "headers",
        "request_body_b64",
    }:
        raise ValueError("malformed request")
    if not isinstance(value["headers"], dict) or not all(
        isinstance(key, str) and isinstance(item, str)
        for key, item in value["headers"].items()
    ):
        raise ValueError("malformed headers")
    return value


def main() -> int:
    try:
        value = _load_request()
        endpoint, origin = _origin(value["endpoint"])
        if origin != value["approved_origin"]:
            raise ValueError("origin mismatch")
        if "Authorization" in value["headers"] and not endpoint.startswith("https://"):
            raise ValueError("credentialed plaintext endpoint")
        body = base64.b64decode(value["request_body_b64"], validate=True)
        opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({}), _RejectRedirects()
        )
        request = urllib.request.Request(
            endpoint,
            data=body,
            headers=value["headers"],
            method="POST",
        )
        with opener.open(request) as response:
            response_url, response_origin = _origin(response.geturl())
            if response_origin != origin:
                raise ValueError("response origin mismatch")
            response_raw = response.read(MAX_RESPONSE_BYTES + 1)
            if len(response_raw) > MAX_RESPONSE_BYTES:
                raise ValueError("response too large")
            request_id = response.headers.get("x-request-id")
        output = {
            "status": "ok",
            "response_url": response_url,
            "request_id": request_id if isinstance(request_id, str) else None,
            "response_body_b64": base64.b64encode(response_raw).decode("ascii"),
        }
        sys.stdout.buffer.write(
            json.dumps(
                output,
                ensure_ascii=True,
                allow_nan=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("ascii")
        )
        return 0
    except (
        ValueError,
        TypeError,
        KeyError,
        json.JSONDecodeError,
        UnicodeError,
        urllib.error.URLError,
        urllib.error.HTTPError,
        OSError,
    ):
        sys.stdout.write('{"status":"error"}')
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
