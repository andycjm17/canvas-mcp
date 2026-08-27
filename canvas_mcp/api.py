"""Canvas LMS REST API v1 客户端（只读，纯标准库）。

要点：
  * 认证只要 `Authorization: Bearer <token>`，没有额外签名。
  * 分页在 `Link` 响应头里，格式 `<url>; rel="next"`。不跟 next 只能拿到第一页，
    Canvas 默认 per_page=10，课程一多就会静默截断。
  * 同名参数用数组语法（`state[]=active`），要允许一个 key 出现多次。
  * token 只对签发它的那个 instance 有效。Fuqua 的 token 打 canvas.duke.edu
    会返回 401 Invalid access token——这不是 token 坏了，是 host 填错了。
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
MAX_PAGES = 50  # 防止分页链接成环时无限打请求

_NEXT = re.compile(r'<([^>]+)>\s*;\s*rel="next"')


class ApiError(RuntimeError):
    pass


def _encode(params: dict[str, Any] | None) -> str:
    """支持 Canvas 的数组参数：值是 list 时展开成多个同名 key。"""
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

    # ------------------------------------------------------------ 底层

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
            except Exception:  # noqa: BLE001 - 错误体不是 JSON 就算了
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

    # ------------------------------------------------------------ 对外

    def get(self, path: str, **params: Any) -> Any:
        """取单个资源，不翻页。"""
        query = _encode(params)
        url = f"{self.base}{path}" + (f"?{query}" if query else "")
        data, _ = self._open(url)
        return data

    def paginate(self, path: str, limit: int | None = None, **params: Any) -> list[dict]:
        """跟着 Link: rel="next" 把所有页取完。

        limit 是总条数上限，够了就不再请求下一页。
        """
        params.setdefault("per_page", 100)
        query = _encode(params)
        url = f"{self.base}{path}" + (f"?{query}" if query else "")

        out: list[dict] = []
        for _ in range(MAX_PAGES):
            data, link = self._open(url)
            if isinstance(data, dict):
                # 少数端点（如 /users/self）返回对象而非数组
                return [data]
            out.extend(data)
            if limit is not None and len(out) >= limit:
                return out[:limit]
            match = _NEXT.search(link or "")
            if not match:
                break
            url = match.group(1)
        return out[:limit] if limit is not None else out
