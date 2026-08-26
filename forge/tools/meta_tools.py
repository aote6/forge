"""会话/杂项类工具：todo、web_fetch、命令执行、Mastodon、状态查询。"""

from __future__ import annotations

import subprocess
import sys

from forge.adapters.base import ToolResult
from forge.core.security import is_dangerous_command
from forge.tools._common import _truncate
from forge.tools.display import format_block, error_slices
from forge.tools.errors import decorate_fail_message
from forge.tools.project_memory import load_memory, update_memory
from forge.tools.project_review import make_project_review_tools
from forge.tools.session_changes import list_changes, format_list as format_session_changes


def make_meta_tools(workspace) -> dict:
    # Per-Runtime todo list (make_meta_tools closure)
    _todo_items: list = []

    def todo_write(items: list) -> ToolResult:
        """Replace or update in-memory task list for the current agent session."""
        nonlocal _todo_items
        try:
            if not isinstance(items, list):
                return ToolResult.fail(display="todo_write: items 必须是列表 [{id?, content, status}]")
            allowed = {"pending", "in_progress", "done", "completed", "cancelled"}
            normalized = []
            for i, it in enumerate(items):
                if not isinstance(it, dict):
                    return ToolResult.fail(display=f"todo_write: items[{i}] 必须是 dict")
                content = it.get("content") or it.get("title") or ""
                if not content:
                    return ToolResult.fail(display=f"todo_write: items[{i}] 需要 content")
                status = (it.get("status") or "pending").lower()
                if status == "completed":
                    status = "done"
                if status not in allowed:
                    return ToolResult.fail(
                        display=f"todo_write: status 必须是 pending|in_progress|done，收到 {status}"
                    )
                tid = it.get("id") or str(i + 1)
                normalized.append({"id": str(tid), "content": str(content), "status": status})
            _todo_items = normalized
            lines = ["RESULT: todo updated"]
            for it in _todo_items:
                mark = {"pending": "[ ]", "in_progress": "[~]", "done": "[x]", "cancelled": "[-]"}.get(
                    it["status"], "[ ]"
                )
                lines.append(f"  {mark} {it['id']}. {it['content']} ({it['status']})")
            return ToolResult.ok(
                display="\n".join(lines),
                payload={"todos": list(_todo_items)},
            )
        except Exception as e:
            return ToolResult.fail(display=f"todo_write failed: {e}")

    def todo_list() -> ToolResult:
        """Show current in-memory todos."""
        if not _todo_items:
            return ToolResult.ok(display="RESULT: todo empty\n(无任务)", payload={"todos": []})
        lines = ["RESULT: todo list"]
        for it in _todo_items:
            mark = {"pending": "[ ]", "in_progress": "[~]", "done": "[x]", "cancelled": "[-]"}.get(
                it["status"], "[ ]"
            )
            lines.append(f"  {mark} {it['id']}. {it['content']} ({it['status']})")
        return ToolResult.ok(display="\n".join(lines), payload={"todos": list(_todo_items)})

    def web_fetch(url: str, max_chars: int = 5000) -> ToolResult:
        """Fetch http(s) URL text (no JS). urllib only."""
        import re
        import urllib.error
        import urllib.request
        from html.parser import HTMLParser

        class _TextExtractor(HTMLParser):
            def __init__(self):
                super().__init__()
                self.parts: list[str] = []
                self._skip = 0

            def handle_starttag(self, tag, attrs):
                if tag in ("script", "style", "noscript"):
                    self._skip += 1

            def handle_endtag(self, tag):
                if tag in ("script", "style", "noscript") and self._skip:
                    self._skip -= 1

            def handle_data(self, data):
                if self._skip:
                    return
                t = data.strip()
                if t:
                    self.parts.append(t)

        try:
            u = (url or "").strip()
            if not (u.startswith("http://") or u.startswith("https://")):
                return ToolResult.fail(display="web_fetch: 仅支持 http/https URL")
            req = urllib.request.Request(
                u,
                headers={"User-Agent": "ForgeAgent/1.0"},
                method="GET",
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                raw = resp.read(max(1000, int(max_chars) * 4))
                ctype = (resp.headers.get("Content-Type") or "").lower()
            try:
                text = raw.decode("utf-8", errors="replace")
            except Exception:
                text = raw.decode("latin-1", errors="replace")
            if "html" in ctype or text.lstrip().lower().startswith("<!doctype") or "<html" in text[:200].lower():
                parser = _TextExtractor()
                try:
                    parser.feed(text)
                    text = "\n".join(parser.parts)
                except Exception:
                    text = re.sub(r"<[^>]+>", " ", text)
            text = re.sub(r"\n{3,}", "\n\n", text).strip()
            truncated = text[: int(max_chars)]
            if len(text) > int(max_chars):
                truncated += "\n...(truncated)"
            return ToolResult.ok(
                display=f"RESULT: url={u} chars={len(truncated)}\n{truncated}",
                payload={"url": u, "chars": len(truncated), "text": truncated},
            )
        except urllib.error.HTTPError as e:
            return ToolResult.fail(display=f"web_fetch HTTP {e.code}: {url}\n建议: 检查 URL 或稍后重试。")
        except Exception as e:
            return ToolResult.fail(display=f"web_fetch failed: {e}\n建议: 确认网络与 URL。")

    def project_memory() -> ToolResult:
        """启发式项目记忆（可能过期）。不得作为项目当前事实或历史权威；提交历史以 Git / project_review 为准。"""
        data = load_memory(workspace.project_root)
        if not data:
            return ToolResult.ok(
                display=format_block("project_memory", "OK", {"empty": True}, "(empty)"),
                payload={},
            )
        body = "\n".join(f"{k}: {v}" for k, v in data.items())
        return ToolResult.ok(
            display=format_block("project_memory", "OK", {"keys": len(data)}, body),
            payload=data,
        )

    def session_changes() -> ToolResult:
        """当前 session 的 mutation evidence。session ≠ calendar day；不能替代 Git 日历工作历史。"""
        body = format_session_changes()
        items = list_changes()
        return ToolResult.ok(
            display=format_block(
                "session_changes",
                "OK",
                {"count": len(items)},
                body,
                hint="用户问改了哪些文件时优先用本工具",
            ),
            payload={"changes": items},
        )

    def run_command(cmd: str, timeout: int = 60) -> ToolResult:
        """执行 shell；保留尾部输出，并提取 Error/Traceback 切片。"""
        try:
            danger = is_dangerous_command(cmd)
            if danger:
                return ToolResult.fail(
                    display=format_block(
                        "run_command", "FAIL", {"cmd": cmd, "reason": danger},
                        hint="命令被安全策略拦截",
                    )
                )
            r = subprocess.run(
                cmd,
                shell=True,
                cwd=workspace.project_root,
                capture_output=True,
                text=True,
                timeout=int(timeout) if timeout else 60,
            )
            out = (r.stdout or "") + (r.stderr or "")
            tail = _truncate(out)
            slices = error_slices(out)
            body = tail
            if slices:
                body = tail + "\n--- ERROR_SLICES ---\n" + slices
            ok = r.returncode == 0
            # learn test command
            if "pytest" in cmd or "npm test" in cmd or "cargo test" in cmd:
                try:
                    update_memory(workspace.project_root, test_command=cmd.strip())
                except Exception as e:
                    print(f"[local_tools] update_memory failed: {e}", file=sys.stderr)
            return ToolResult.ok(
                display=format_block(
                    "run_command",
                    "OK" if ok else "FAIL",
                    {"cmd": cmd, "exit": r.returncode},
                    body,
                    hint="" if ok else "看 ERROR_SLICES / 尾部输出",
                ),
                payload={"returncode": r.returncode, "cmd": cmd},
            ) if ok else ToolResult.fail(
                display=format_block(
                    "run_command",
                    "FAIL",
                    {"cmd": cmd, "exit": r.returncode},
                    body,
                    hint="看 ERROR_SLICES；或 run_test_structured",
                ),
                payload={"returncode": r.returncode, "cmd": cmd},
            )
        except Exception as e:
            return ToolResult.fail(
                display=decorate_fail_message(format_block("run_command", "FAIL", {"cmd": cmd, "reason": str(e)}), e)
            )

    def post_toot(text: str, visibility: str = "unlisted") -> ToolResult:
        """发一条 Mastodon 嘟文。非强制：想发就调。"""
        try:
            from forge.adapters.mastodon import MastodonClient, is_configured
            if not is_configured():
                return ToolResult.fail(
                    display=format_block("post_toot", "FAIL", {"reason": "未配置 MASTODON_BASE_URL / MASTODON_ACCESS_TOKEN"}),
                    hint="export 后重试；Token 勿写入源码",
                )
            client = MastodonClient()
            data = client.post_status(text, visibility=visibility)
            url = data.get("url") or data.get("uri") or ""
            return ToolResult.ok(
                display=format_block("post_toot", "OK", {"url": url, "id": data.get("id"), "visibility": visibility}),
                payload={"mutation": True, "url": url, "id": data.get("id")},
            )
        except Exception as e:
            return ToolResult.fail(display=format_block("post_toot", "FAIL", {"reason": str(e)}))

    def delete_toot(status_id: str) -> ToolResult:
        """删除指定 Mastodon 嘟文。"""
        try:
            from forge.adapters.mastodon import MastodonClient, is_configured
            if not is_configured():
                return ToolResult.fail(
                    display=format_block("delete_toot", "FAIL", {"reason": "未配置 MASTODON_BASE_URL / MASTODON_ACCESS_TOKEN"}),
                )
            client = MastodonClient()
            client.delete_status(status_id)
            return ToolResult.ok(
                display=format_block("delete_toot", "OK", {"id": status_id}),
                payload={"mutation": True, "id": status_id},
            )
        except Exception as e:
            return ToolResult.fail(display=format_block("delete_toot", "FAIL", {"reason": str(e)}))

    def verify_tool_call(tool_call_id: str) -> ToolResult:
        """Independently look up a ToolCallRecord by tool_call_id.

        Returns only ground-truth fields from the append-only log.
        Does NOT return sub-agent claim, conclusion, or any AgentResult narrative.
        """
        from forge.tool_call_record import get_record

        tc_id = (tool_call_id or "").strip()
        if not tc_id:
            return ToolResult.fail(
                display=format_block(
                    "verify_tool_call",
                    "FAIL",
                    {"reason": "tool_call_id required"},
                )
            )
        rec = get_record(workspace.project_root, tc_id)
        if not rec:
            return ToolResult.fail(
                display=format_block(
                    "verify_tool_call",
                    "FAIL",
                    {"tool_call_id": tc_id, "reason": "no ToolCallRecord found"},
                ),
                payload={"tool_call_id": tc_id, "found": False},
            )
        payload = {
            "tool_call_id": tc_id,
            "found": True,
            "tool_name": rec.get("tool_name"),
            "input": rec.get("input"),
            "output": rec.get("output"),
            "status": rec.get("status"),
            "error": rec.get("error"),
            "subtask_id": rec.get("subtask_id"),
            "timestamp": rec.get("timestamp"),
        }
        body_lines = [
            f"tool_call_id: {tc_id}",
            f"tool_name: {payload['tool_name']}",
            f"status: {payload['status']}",
            f"subtask_id: {payload['subtask_id']}",
            f"error: {payload['error']}",
            f"input: {payload['input']!r}",
            f"output: {payload['output']!r}",
        ]
        return ToolResult.ok(
            display=format_block(
                "verify_tool_call",
                "OK",
                {"tool_call_id": tc_id, "tool_name": payload["tool_name"]},
                "\n".join(body_lines),
            ),
            payload=payload,
        )

    tools = {
        "todo_write": todo_write,
        "todo_list": todo_list,
        "web_fetch": web_fetch,
        "project_memory": project_memory,
        "session_changes": session_changes,
        "verify_tool_call": verify_tool_call,
        "run_command": run_command,
        "post_toot": post_toot,
        "delete_toot": delete_toot,
    }
    tools.update(make_project_review_tools(workspace))
    return tools
