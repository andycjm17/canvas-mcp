"""凭证与实例配置。

**token 绝不进版本库**，只从环境变量或 ~/.config/canvas-mcp/config.json 读。
配置文件在写入时强制 0600。

解析顺序：环境变量 > 配置文件 > 默认值。
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

CONFIG_DIR = Path(os.environ.get("CANVAS_MCP_HOME") or (Path.home() / ".config" / "canvas-mcp"))
CONFIG_PATH = CONFIG_DIR / "config.json"

# Fuqua 用自己的 instance，不是 canvas.duke.edu——后者对同一个 token 直接 401。
DEFAULT_HOST = "fuqua.instructure.com"
DEFAULT_TZ = "America/New_York"


class ConfigError(RuntimeError):
    pass


def _file() -> dict[str, Any]:
    if not CONFIG_PATH.exists():
        return {}
    try:
        data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except ValueError as e:
        raise ConfigError(f"{CONFIG_PATH} 不是合法 JSON: {e}") from e
    if not isinstance(data, dict):
        raise ConfigError(f"{CONFIG_PATH} 顶层应该是一个对象")
    return data


def _get(key: str, env: str, default: str | None = None) -> str | None:
    val = os.environ.get(env) or _file().get(key) or default
    return val.strip() if isinstance(val, str) else val


def host() -> str:
    """Canvas 域名，不带协议。"""
    raw = _get("host", "CANVAS_MCP_HOST", DEFAULT_HOST) or DEFAULT_HOST
    return raw.replace("https://", "").replace("http://", "").rstrip("/")


def base_url() -> str:
    return f"https://{host()}/api/v1"


def timezone_name() -> str:
    """展示用时区。Canvas 返回的时间全是 UTC，不转会把 DDL 记晚一天。"""
    return _get("timezone", "CANVAS_MCP_TZ", DEFAULT_TZ) or DEFAULT_TZ


def token() -> str:
    tok = _get("token", "CANVAS_MCP_TOKEN")
    if not tok:
        raise ConfigError(
            "没找到 Canvas access token。二选一：\n"
            f"  1. 写进 {CONFIG_PATH}：{{\"token\": \"<token>\"}}（本模块会设成 0600）\n"
            "  2. 设环境变量 CANVAS_MCP_TOKEN\n"
            "token 在 Canvas → Account → Settings → Approved Integrations → "
            "+ New Access Token 生成。"
        )
    return tok


def save(token_value: str, host_value: str | None = None,
         timezone_value: str | None = None) -> Path:
    """把凭证写进配置文件，权限 0600。"""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    data = _file()
    data["token"] = token_value
    if host_value:
        data["host"] = host_value.replace("https://", "").replace("http://", "").rstrip("/")
    if timezone_value:
        data["timezone"] = timezone_value

    # 先建成 0600 再写，避免默认权限下有一瞬间是可读的。
    fd = os.open(CONFIG_PATH, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.chmod(CONFIG_PATH, 0o600)
    return CONFIG_PATH
