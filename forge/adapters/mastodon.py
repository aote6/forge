"""Mastodon 发嘟适配器：Token 仅从环境变量读取，禁止硬编码。"""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

# 可选限流状态（每用户目录）
_STATE_PATH = Path.home() / ".forge" / "mastodon_state.json"


def _env(name: str, default: str = "") -> str:
    return (os.environ.get(name) or default).strip()


class MastodonClient:
    def __init__(
        self,
        base_url: str | None = None,
        access_token: str | None = None,
    ):
        self.base_url = (base_url or _env("MASTODON_BASE_URL")).rstrip("/")
        self.token = access_token or _env("MASTODON_ACCESS_TOKEN")
        if not self.base_url or not self.token:
            raise RuntimeError(
                "缺少 MASTODON_BASE_URL 或 MASTODON_ACCESS_TOKEN。"
                "请 export 或写入 .env（勿提交 Git）。"
            )

    def _request(self, method: str, path: str, payload: dict | None = None) -> dict[str, Any]:
        data = None
        headers = {
            "Authorization": f"Bearer {self.token}",
            "User-Agent": "ForgeAgent/1.0",
        }
        if payload is not None:
            data = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(
            f"{self.base_url}{path}",
            data=data,
            headers=headers,
            method=method,
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                body = resp.read().decode("utf-8")
                return json.loads(body) if body else {}
        except urllib.error.HTTPError as e:
            err = e.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"Mastodon API {e.code}: {err[:500]}") from e

    def verify(self) -> dict[str, Any]:
        return self._request("GET", "/api/v1/accounts/verify_credentials")

    def post_status(
        self,
        text: str,
        visibility: str | None = None,
        spoiler_text: str | None = None,
    ) -> dict[str, Any]:
        if not text or not text.strip():
            raise ValueError("status 不能为空")
        text = text.strip()
        max_len = int(_env("MASTODON_MAX_CHARS", "500") or "500")
        if len(text) > max_len:
            text = text[: max_len - 3] + "..."

        vis = visibility or _env("MASTODON_VISIBILITY", "unlisted") or "unlisted"
        if vis not in ("public", "unlisted", "private", "direct"):
            vis = "unlisted"

        payload: dict[str, Any] = {"status": text, "visibility": vis}
        if spoiler_text:
            payload["spoiler_text"] = spoiler_text
        return self._request("POST", "/api/v1/statuses", payload)


def is_configured() -> bool:
    return bool(_env("MASTODON_BASE_URL") and _env("MASTODON_ACCESS_TOKEN"))


def auto_toot_enabled() -> bool:
    """环境变量 MASTODON_AUTO_TOOT=1 时，git commit/push 成功后自动发嘟。"""
    return _env("MASTODON_AUTO_TOOT", "0") in ("1", "true", "yes", "on")


def rate_limit_ok(min_interval_sec: int = 300, max_per_day: int = 12) -> bool:
    """简单本地限流，避免刷屏。返回 True 表示可以发。"""
    try:
        min_interval_sec = int(_env("MASTODON_MIN_INTERVAL_SEC", str(min_interval_sec)))
        max_per_day = int(_env("MASTODON_MAX_PER_DAY", str(max_per_day)))
    except ValueError:
        pass
    now = time.time()
    day = time.strftime("%Y-%m-%d")
    st = {"last": 0.0, "day": day, "count": 0}
    if _STATE_PATH.exists():
        try:
            st = json.loads(_STATE_PATH.read_text(encoding="utf-8"))
        except Exception:
            pass
    if st.get("day") != day:
        st = {"last": 0.0, "day": day, "count": 0}
    if now - float(st.get("last") or 0) < min_interval_sec:
        return False
    if int(st.get("count") or 0) >= max_per_day:
        return False
    st["last"] = now
    st["count"] = int(st.get("count") or 0) + 1
    st["day"] = day
    try:
        _STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        _STATE_PATH.write_text(json.dumps(st), encoding="utf-8")
    except Exception:
        pass
    return True


def maybe_toot_git_event(cmd: str, ok: bool, cwd: str = ".") -> str | None:
    """若开启自动发嘟且命令为成功的 git commit/push，则发一条简短嘟文。

    Returns:
        成功时返回嘟文 URL；跳过或失败返回 None（不抛到主流程）。
    """
    if not ok or not auto_toot_enabled() or not is_configured():
        return None
    import re
    c = " ".join(cmd.split())
    kind = None
    if re.search(r"\bgit\s+commit\b", c):
        kind = "commit"
    elif re.search(r"\bgit\s+push\b", c):
        kind = "push"
    if not kind:
        return None
    if not rate_limit_ok():
        return None
    # 取简短说明
    msg = None
    m = re.search(r'-m\s+["\']([^"\']+)["\']', c)
    if m:
        msg = m.group(1).strip()
    repo = Path(cwd).name
    if kind == "commit":
        text = f"🔧 [{repo}] git commit"
        if msg:
            text += f": {msg[:120]}"
    else:
        text = f"🚀 [{repo}] git push"
    try:
        client = MastodonClient()
        data = client.post_status(text)
        return data.get("url") or data.get("uri")
    except Exception:
        return None
