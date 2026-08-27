"""Canvas LMS REST API v1 client (read-only, standard library only).

Notes:
  * Auth is just `Authorization: Bearer <token>`; there is no extra signing.
  * Pagination lives in the `Link` response header, formatted `<url>; rel="next"`.
    Without following `next` you only ever get the first page — Canvas defaults to
    per_page=10, so anything sizeable is silently truncated.
  * Array parameters repeat the same key (`state[]=active`), so a key must be
    allowed to appear more than once.
  * A token is only valid for the instance that issued it. Fuqua's token against
    canvas.duke.edu returns 401 Invalid access token — that is a wrong host, not a
    bad token.
"""
from __future__ import annotations

import json
import re
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from . import config

TIMEOUT = 30
MAX_PAGES = 50  # Guard against a cyclic pagination chain looping forever

_NEXT = re.compile(r'<([^>]+)>\s*;\s*rel="next"')


class ApiError(RuntimeError):
    pass


def _encode(params: dict[str, Any] | None) -> str:
    """Support Canvas array parameters: a list value expands to repeated keys."""
    if not params:
        return ""
    pairs: list[tuple[str, str]] = []
    for key, val in params.items():
        if val is None:
            continue
        if isinstance(val, (list, tuple)):
            pairs.extend((key, str(v)) for v in val if v is not None)
        elif isinstance(val, bool):
            pairs.append((key, "true" if val else "false"))
        else:
            pairs.append((key, str(val)))
    return urllib.parse.urlencode(pairs)


class Client:
    def __init__(self, token: str | None = None, host: str | None = None) -> None:
        self.token = token or config.token()
        self.host = (host or config.host()).replace("https://", "").rstrip("/")
        self.base = f"https://{self.host}/api/v1"

    # ------------------------------------------------------------ transport

    def _open(self, url: str) -> tuple[Any, str | None]:
        req = urllib.request.Request(url, headers={
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/json",
            "User-Agent": "canvas-mcp/0.1",
        })
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
                body = resp.read().decode("utf-8")
                link = resp.headers.get("Link")
        except urllib.error.HTTPError as e:
            detail = ""
            try:
                payload = json.loads(e.read().decode("utf-8"))
                errs = payload.get("errors") or payload.get("message")
                if isinstance(errs, list):
                    detail = "；".join(str(x.get("message", x)) for x in errs)
                elif errs:
                    detail = str(errs)
            except Exception:  # noqa: BLE001 - non-JSON error body, nothing to extract
                pass

            if e.code == 401:
                raise ApiError(
                    f"401 {detail or '认证失败'}。检查 token 是不是 {self.host} "
                    f"这个实例签发的——Canvas 的 token 不跨实例。"
                ) from e
            if e.code == 403:
                raise ApiError(f"403 没权限访问该资源。{detail}") from e
            if e.code == 404:
                raise ApiError(f"404 资源不存在或你没有访问权。{detail}") from e
            raise ApiError(f"HTTP {e.code} {detail or e.reason}") from e
        except urllib.error.URLError as e:
            raise ApiError(f"连不上 {self.host}：{e.reason}") from e

        try:
            return json.loads(body), link
        except ValueError as e:
            raise ApiError(f"响应不是合法 JSON（前 200 字符）：{body[:200]}") from e

    # ------------------------------------------------------------ public

    def get(self, path: str, **params: Any) -> Any:
        """Fetch a single resource. Does not paginate."""
        query = _encode(params)
        url = f"{self.base}{path}" + (f"?{query}" if query else "")
        data, _ = self._open(url)
        return data

    def paginate(self, path: str, limit: int | None = None, **params: Any) -> list[dict]:
        """Follow `Link: rel="next"` until every page is consumed.

        `limit` caps the total number of items; once reached, no further request
        is made.
        """
        params.setdefault("per_page", 100)
        query = _encode(params)
        url = f"{self.base}{path}" + (f"?{query}" if query else "")

        out: list[dict] = []
        for _ in range(MAX_PAGES):
            data, link = self._open(url)
            if isinstance(data, dict):
                # A few endpoints (e.g. /users/self) return an object, not an array
                return [data]
            out.extend(data)
            if limit is not None and len(out) >= limit:
                return out[:limit]
            match = _NEXT.search(link or "")
            if not match:
                break
            url = match.group(1)
        return out[:limit] if limit is not None else out
