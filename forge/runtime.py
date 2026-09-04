"""Runtime — session shell for Forge.

Production path (唯一):
  Runtime.run(task) → Continuous Conversation + Pending Action Gate
    → 完整工具 schema 始终可见
    → READ 直接执行；WRITE_CONFIRM 冻结 PendingAction，用户确认后 Runtime 执行快照
    → ToolExecutor → IntentExecutor → Veritas commit/abort → Projection
"""
from __future__ import annotations
import sys

import json
import os
import re
from dataclasses import dataclass, field
from typing import Optional

from forge.adapters.base import BaseAdapter, Message, ToolResult
from forge.confirmation import is_cancel, is_confirm
from forge.conversation import Conversation
from forge.events import Event, EventType
from forge.memory import MemoryStore
from forge.projections.base import ProjectionManager
from forge.projections.file_projection import FileProjection
from forge.projections.git_projection import GitProjection
from forge.projections.index_projection import IndexProjection
from forge.system_prompt import SYSTEM_INSTRUCTION
from forge.core.sanitizer import sanitize_and_redact
from forge.tools import make_tools
from forge.tools.direct_disk import DIRECT_DISK_TOOLS
from forge.tools.schemas import (
    READ_ONLY_TOOL_DECLARATIONS,
    MUTATION_TOOL_DECLARATIONS,
    MUTATION_TOOL_NAMES,
    RECONCILIATION_TOOL_DECLARATIONS,
    RECONCILIATION_TOOL_NAMES,
    SUBMIT_PLAN_TOOL_NAME,
    SUBMIT_PLAN_DECLARATION,
    CONTROL_PLANE_TOOL_DECLARATIONS,
    CONTROL_PLANE_TOOLS,
    EXECUTION_PLANE_TOOL_DECLARATIONS,
    EXECUTION_PLANE_TOOLS,
    MAIN_READ_ONLY_TOOL_NAMES,
    MAIN_READ_ONLY_TOOL_DECLARATIONS,
    MAIN_AUDITED_TOOL_NAMES,
)
from forge.workspace import Workspace
from forge.world import WorldRuntime

MAX_AGENT_STEPS = 40
MAX_CONSECUTIVE_FAILURES = 3

# P2-3：弱模型长任务骨架。每 N 个工具循环步骤注入一次 progress checkpoint；
# 距离 MAX_AGENT_STEPS 只剩这些步时改注入 final checkpoint 收束。
PROGRESS_CHECKPOINT_EVERY = 5
FINAL_CHECKPOINT_TAIL_STEPS = 3

# Tools that primarily read code/content into context
_READ_TOOLS = {
    "read_file",
    "read_file_with_lines",
    "read_files",
    "search_code",
    "glob_files",
    "list_files",
    "summarize_file",
    "read_function",
}

# Mutation tools that edit files on success
_EDIT_TOOLS = {
    "str_replace",
    "write_file",
    "modify_file",
    "create_file",
    "delete_file",
    "apply_patch",
    "edit_files_batch",
}

# P1-6: 待验证状态下需要硬拦截的"文件内容 mutation"工具（最小集合）。
# 不含 undo_last_tx / forge_sync（修正/对账，必须放行），也不含 create_object/
# link_objects/unlink_objects/post_toot/delete_toot（非文件编辑，不在此 guard 范畴）。
_VERIFY_GUARDED_MUTATIONS = {
    "str_replace",
    "write_file",
    "create_file",
    "modify_file",
    "edit_files_batch",
    "apply_patch",
    "delete_file",
}

# Mastodon 工具（post_toot / delete_toot）走环境变量 + HTTP，既不依赖 World
# 也不写磁盘，外部变更守卫（World 可达性 / 磁盘变化）对它们无意义，直接放行。
_MASTODON_TOOLS = {
    "post_toot",
    "delete_toot",
}

# ---------------------------------------------------------------------------
# Write strategy buckets (权限轴只有 READ / WRITE；桶是策略不是 phase)
# ---------------------------------------------------------------------------
# WRITE_CONFIRM: 普通副作用 → 冻结 PendingAction，用户确认后 Runtime 执行快照
# WRITE_RECOVERY: 恢复一致性 → 按现有安全规则直接执行（仍受 Guard）
# WRITE_BLOCKED: 由 _guard_* 判定，与确认正交
# ---------------------------------------------------------------------------
# undo_last_tx: 恢复类，直接执行（仍受 Guard）
# forge_sync: 不在此自动执行；循环内先 detect，仅 FAST_FORWARD 才进 PendingAction
_WRITE_RECOVERY_TOOLS = frozenset({"undo_last_tx"})
_WRITE_CONFIRM_TOOLS = frozenset(
    (MUTATION_TOOL_NAMES | RECONCILIATION_TOOL_NAMES) - _WRITE_RECOVERY_TOOLS - {"forge_sync"}
)
# NOTE(2026-09-02): Phase 1 隔离后，_main_tool_policy_denied 会在此策略判定之前
# 拦截所有 MUTATION_TOOL_NAMES | RECONCILIATION_TOOL_NAMES 工具并 continue（见主循环
# for tc in resp.tool_calls 里 denied is not None 分支）。因此下面 WRITE_CONFIRM 分支
# （strategy == "WRITE_CONFIRM" 那段）在当前工具面下不可达，仅当未来把某个
# mutation/reconciliation 工具重新加入 CONTROL_PLANE_TOOL_DECLARATIONS 时才会复活。
# 保留该分支作为该场景下的安全网，不要删除；不变量由
# tests/test_tool_plane_isolation.py::test_write_confirm_tools_unreachable_from_main_loop 锁定。


@dataclass
class PendingAction:
    """一次待用户确认的精确写操作快照。确认后 Runtime 直接执行，不重问模型。"""

    tool: str
    args: dict
    tool_call_id: str
    summary: str = ""
    # 确认前模型可能已输出的 assistant 文本 / 同批已处理的 tool 上下文，用于续跑
    assistant_content: str | None = None


def _default_tool_schemas() -> list:
    """主 AI 可见：控制面 + 最小 READ_ONLY 子集（无 mutation）。"""
    return list(CONTROL_PLANE_TOOL_DECLARATIONS) + list(MAIN_READ_ONLY_TOOL_DECLARATIONS)


def _main_tool_policy_denied(tool_name: str) -> str | None:
    """Layer-2 hard policy for main AI (beyond schema isolation).

    Main may only run control-plane tools and MAIN_READ_ONLY.
    """
    name = (tool_name or "").strip()
    if not name:
        return "empty tool name"
    if name in CONTROL_PLANE_TOOLS:
        return None
    if name in MAIN_READ_ONLY_TOOL_NAMES:
        return None
    if name in MUTATION_TOOL_NAMES or name in RECONCILIATION_TOOL_NAMES:
        return (
            f"main AI cannot execute mutation/reconciliation tool {name!r}; "
            "delegate via spawn_subagent"
        )
    return (
        f"main AI cannot execute tool {name!r}; "
        "only control-plane and MAIN_READ_ONLY tools are allowed"
    )


def _record_main_tool_call(
    project_root: str,
    *,
    tool_name: str,
    arguments: dict,
    result,
) -> tuple[str | None, bool]:
    """Write ToolCallRecord(actor=main). Returns (tool_call_id, record_written)."""
    from forge.tool_call_record import (
        ToolCallRecord,
        current_timestamp,
        new_tool_call_id,
        write_record,
    )

    tool_call_id = new_tool_call_id()
    status = "success" if getattr(result, "success", False) else "error"
    error = None if status == "success" else (getattr(result, "display", None) or "error")
    output = getattr(result, "payload", None)
    rec = ToolCallRecord(
        tool_call_id=tool_call_id,
        subtask_id="",
        tool_name=tool_name,
        input=dict(arguments or {}),
        output=output,
        status=status,
        error=error,
        timestamp=current_timestamp(),
        actor="main",
    )
    ok = bool(write_record(project_root, rec))
    return tool_call_id, ok


def _write_strategy(tool_name: str) -> str:
    """返回 READ | WRITE_CONFIRM | WRITE_RECOVERY | FORGE_SYNC。

    FORGE_SYNC 单独处理：先 detect（只读观察）；仅需 FAST_FORWARD 推进时进 PendingAction。
    """
    if tool_name == "forge_sync":
        return "FORGE_SYNC"
    if tool_name in _WRITE_RECOVERY_TOOLS:
        return "WRITE_RECOVERY"
    if tool_name in _WRITE_CONFIRM_TOOLS:
        return "WRITE_CONFIRM"
    if tool_name == SUBMIT_PLAN_TOOL_NAME:
        return "READ"  # 可选方案输出，不授予写权限
    return "READ"


def _pending_action_summary(tool: str, args: dict | None) -> str:
    args = args or {}
    if tool == "str_replace":
        path = args.get("path", "?")
        old = str(args.get("old_string") or "")
        new = str(args.get("new_string") or "")
        return (
            f"str_replace path={path}\n"
            f"  old_string ({len(old)} chars): {old[:120]!r}{'…' if len(old) > 120 else ''}\n"
            f"  new_string ({len(new)} chars): {new[:120]!r}{'…' if len(new) > 120 else ''}"
        )
    if tool == "write_file":
        path = args.get("path", "?")
        content = str(args.get("content") or "")
        return f"write_file path={path} content_len={len(content)}"
    if tool == "post_toot":
        text = str(args.get("text") or "")
        vis = args.get("visibility") or "unlisted"
        return f"post_toot visibility={vis} text={text[:200]!r}{'…' if len(text) > 200 else ''}"
    if tool == "delete_toot":
        return f"delete_toot status_id={args.get('status_id')!r}"
    if tool == "forge_sync":
        detail = str(args.get("_detect_summary") or args.get("status") or "FAST_FORWARD")
        return f"forge_sync 将推进同步：\n{detail}"
    try:
        import json as _json
        blob = _json.dumps(args, ensure_ascii=False)
    except Exception:
        blob = str(args)
    if len(blob) > 400:
        blob = blob[:400] + "…"
    return f"{tool} {blob}"


_ACTION_CONFIRM_PROMPT = (
    "\n\n── 待确认的写操作 ──\n"
    "回复「确认」执行上述精确动作；「取消」放弃；或直接说明你的修改意见（将取消本次动作并继续对话）。"
)



def _norm_path(p: str) -> str:
    """归一化相对路径：统一分隔符、去 ./、去尾部 /，便于比较。"""
    p = (p or "").strip().replace("\\", "/")
    while p.startswith("./"):
        p = p[2:]
    return p.rstrip("/")


def _mutation_target_paths(tool_name: str, arguments: dict | None) -> list[str]:
    """从 mutation 工具参数里提取其将要改动的文件路径集合。"""
    args = arguments or {}
    paths: list[str] = []
    for key in ("path", "file", "target"):
        v = args.get(key)
        if isinstance(v, str) and v.strip():
            paths.append(v.strip())
    edits = args.get("edits")
    if isinstance(edits, list):
        for e in edits:
            if isinstance(e, dict):
                p = e.get("path") or e.get("file")
                if isinstance(p, str) and p.strip():
                    paths.append(p.strip())
    patch = args.get("patch")
    if isinstance(patch, str):
        for line in patch.splitlines():
            if line.startswith("+++ "):
                p = line[4:].strip()
                if p.startswith("b/"):
                    p = p[2:]
                if p and p != "/dev/null":
                    paths.append(p)
    return paths


@dataclass
class WorkingSet:
    """Lightweight task-level state held in Runtime memory (P1-1).

    Not a second todo system — just enough so long tool loops do not lose
    the current goal, which files were touched, and what still needs verify.
    """

    goal: str = ""
    constraints: list[str] = field(default_factory=list)
    files_read: list[str] = field(default_factory=list)
    files_edited: list[str] = field(default_factory=list)
    open_hypotheses: list[str] = field(default_factory=list)
    pending_verify: list[str] = field(default_factory=list)
    failure_context: list = field(default_factory=list)
    verify_targets: list[str] = field(default_factory=list)
    # path -> set(verify_target)：某次编辑的待验证 target 关联。
    # 供 P1-5 精确清账（只清与本次测试相关的项）与 P1-6 待验证 guard（哪些文件在验证中）使用。
    verify_map: dict = field(default_factory=dict)
    # 最近一次"产生失败上下文"的测试目标，用于成功时精确清除 failure_context。
    failure_target: Optional[str] = None

    def _add_unique(self, bucket: list[str], item: str, max_keep: int = 24) -> None:
        item = (item or "").strip()
        if not item:
            return
        if item in bucket:
            return
        bucket.append(item)
        if len(bucket) > max_keep:
            del bucket[: len(bucket) - max_keep]

    def _path_from_args_or_payload(
        self, arguments: dict | None, result: ToolResult
    ) -> str | None:
        args = arguments or {}
        for key in ("path", "file", "target"):
            v = args.get(key)
            if isinstance(v, str) and v.strip():
                return v.strip()
        payload = result.payload or {}
        for key in ("path", "file"):
            v = payload.get(key)
            if isinstance(v, str) and v.strip():
                return v.strip()
        display = (result.display or "").strip()
        if display:
            m = re.search(r"path[=:]\s*(\S+)", display.splitlines()[0])
            if m:
                return m.group(1).strip().rstrip(",")
        return None

    _PENDING_PREFIX = "verify edit on "

    def _pending_entry_path(self, entry: str) -> str:
        """从 pending_verify 条目里取回 source path（条目由本类生成，格式固定）。"""
        e = (entry or "").strip()
        if e.startswith(self._PENDING_PREFIX):
            return e[len(self._PENDING_PREFIX):].strip()
        return e

    def _is_broad_target(self, target: str) -> bool:
        """全量/近全量 pytest target：`.` / `tests` / `test` / 空 视为覆盖一切。"""
        t = (target or "").strip().rstrip("/")
        return t in ("", ".", "./", "tests", "test", "all")

    def _target_covers(self, ran: str, required: str) -> bool:
        """一次 pytest 运行 target=ran 是否覆盖某个待验证 target=required。"""
        ran = (ran or "").strip()
        required = (required or "").strip()
        if self._is_broad_target(ran):
            return True
        if not required:
            # required 未知：只有 broad 运行才算覆盖
            return False
        if ran == required:
            return True
        # ran 是 required 的父目录：跑 tests/ 覆盖 tests/test_x.py
        return required.startswith(ran.rstrip("/") + "/")

    def _verified_paths(self, ran_target: str) -> set[str]:
        """本次成功测试运行真正覆盖到的 source path 集合。"""
        cleared: set[str] = set()
        for entry in self.pending_verify:
            path = self._pending_entry_path(entry)
            targets = self.verify_map.get(path) or set()
            if not targets:
                # 无关联测试的编辑：只有 broad 运行才清
                if self._is_broad_target(ran_target):
                    cleared.add(path)
                continue
            if any(self._target_covers(ran_target, t) for t in targets):
                cleared.add(path)
        return cleared

    def update_from_tool(
        self, name: str, arguments: dict | None, result: ToolResult
    ) -> None:
        """Incrementally update from a real tool outcome."""
        path = self._path_from_args_or_payload(arguments, result)
        display = (result.display or "") or ""
        disp_l = display.lower()

        if name in _READ_TOOLS and result.success and path:
            self._add_unique(self.files_read, path)

        if name in _EDIT_TOOLS and result.success and path:
            self._add_unique(self.files_edited, path)
            self._add_unique(
                self.pending_verify,
                f"verify edit on {path}",
            )
            # Capture VERIFY_REQUIRED target(s) from display / payload，并建立
            # path -> target 关联（P1-5 精确清账 / P1-6 待验证 guard 共用）。
            targets: set[str] = set()
            pl = result.payload or {}
            if pl.get("verify_target"):
                targets.add(str(pl["verify_target"]))
            for line in display.splitlines():
                if "VERIFY_REQUIRED" in line and "run_test_structured" in line:
                    m = re.search(r"target=([^\s)]+)", line)
                    if m:
                        targets.add(m.group(1).strip().strip("'\""))
            for t in targets:
                self._add_unique(self.verify_targets, t, max_keep=8)
                self.verify_map.setdefault(path, set()).add(t)

        if not result.success:
            if "near_miss" in disp_l or "NEAR_MISS" in display:
                note = f"NEAR_MISS on {name}" + (f" ({path})" if path else "")
                self._add_unique(self.open_hypotheses, note, max_keep=12)
            elif path and name not in ("run_test_structured", "run_tests"):
                self._add_unique(
                    self.open_hypotheses,
                    f"{name} failed on {path}",
                    max_keep=12,
                )

        if name in ("run_test_structured", "run_tests"):
            payload = result.payload or {}
            ran_target = (arguments or {}).get("target") or "tests/"
            if result.success:
                # 只清与本次测试运行相关的验证状态（多文件编辑不误清其它项）。
                cleared = self._verified_paths(ran_target)
                self.pending_verify = [
                    p for p in self.pending_verify
                    if self._pending_entry_path(p) not in cleared
                ]
                self.verify_targets = [
                    v for v in self.verify_targets
                    if not self._target_covers(ran_target, v)
                ]
                # failure_context 只在本次运行覆盖了上次失败目标时才清，保持三者一致。
                # failure_target 为 None（旧数据/手工构造）时按"已解决"对待，兼容旧行为。
                if self.failure_target is None or self._target_covers(
                    ran_target, self.failure_target
                ):
                    self.failure_context = []
                    self.failure_target = None
                for p in cleared:
                    self.verify_map.pop(p, None)
            else:
                # 失败：保留 pending_verify / verify_targets，记录失败上下文与目标。
                fc = payload.get("failure_context") or payload.get("contexts") or []
                if fc:
                    self.failure_context = list(fc)[:8]
                    self.failure_target = ran_target
                fails = payload.get("failed_tests") or payload.get("failures") or []
                for f in fails[:5]:
                    self._add_unique(
                        self.open_hypotheses,
                        f"test failed: {f}"[:140],
                        max_keep=12,
                    )

    def summary(self, max_lines: int = 28) -> str:
        """Compact injection text so the model always sees current task state."""
        lines: list[str] = ["[Working Set]"]
        lines.append(f"goal: {self.goal[:400] if self.goal else '(none)'}")
        if self.constraints:
            lines.append("constraints:")
            for c in self.constraints[:5]:
                lines.append(f"  - {c[:120]}")
        if self.files_read:
            shown = self.files_read[-10:]
            lines.append("files_read: " + ", ".join(shown))
        if self.files_edited:
            shown = self.files_edited[-8:]
            lines.append("files_edited: " + ", ".join(shown))
        if self.open_hypotheses:
            lines.append("open_hypotheses:")
            for h in self.open_hypotheses[-5:]:
                lines.append(f"  - {h[:140]}")
        if self.pending_verify:
            lines.append("pending_verify:")
            for p in self.pending_verify[-5:]:
                lines.append(f"  - {p[:140]}")
        if self.verify_targets:
            lines.append(
                "VERIFY_REQUIRED: run_test_structured(target="
                + repr(self.verify_targets[0])
                + ") — 验证完成前不要开始无关重构"
            )
        if self.failure_context:
            lines.append("failure_context:")
            for c in self.failure_context[:3]:
                if isinstance(c, dict):
                    loc = f"{c.get('file', '?')}:{c.get('line', '?')}"
                    src = (c.get("source") or c.get("snippet") or "")[:80]
                    lines.append(f"  - {loc} {src}")
                else:
                    lines.append(f"  - {str(c)[:120]}")
        if len(lines) > max_lines:
            lines = lines[: max_lines - 1] + ["  ...(truncated)"]
        return "\n".join(lines)

    @staticmethod
    def _normalize_verify_map(raw) -> dict:
        """JSON/dict → path -> set(str)；非法条目丢弃，空 target 集不保留。"""
        out: dict = {}
        if not isinstance(raw, dict):
            return out
        for path, targets in raw.items():
            p = str(path or "").strip()
            if not p:
                continue
            if isinstance(targets, (set, list, tuple)):
                cleaned = {str(t).strip() for t in targets if str(t).strip()}
            elif targets is None:
                cleaned = set()
            else:
                s = str(targets).strip()
                cleaned = {s} if s else set()
            if cleaned:
                out[p] = cleaned
        return out


    def _sync_verify_views_from_map(self) -> None:
        """以 verify_map 为权威行为事实，同步 pending_verify / verify_targets。

        - guard 与精确清账只读 verify_map（及由 map 派生的视图）
        - 表达层字段由此集中生成，避免三字段各自漂移
        - map 为空时不清空已有 pending/targets（兼容无 map 的旧 task_state）
        """
        if not self.verify_map:
            return
        # Drop empty sets if any leaked in-memory
        self.verify_map = {
            p: set(ts) for p, ts in self.verify_map.items() if p and ts
        }
        paths = list(self.verify_map.keys())
        # pending_verify: 保留已有合法条目顺序，补齐 map 中缺失 path
        existing_paths = {
            self._pending_entry_path(e) for e in self.pending_verify
        }
        new_pending = [
            e
            for e in self.pending_verify
            if self._pending_entry_path(e) in self.verify_map
        ]
        for p in paths:
            if p not in existing_paths:
                new_pending.append(f"{self._PENDING_PREFIX}{p}")
        self.pending_verify = new_pending
        # verify_targets: 合并 map 中全部 target，保序去重
        seen: set[str] = set()
        targets: list[str] = []
        for p in paths:
            for t in sorted(self.verify_map.get(p) or ()):
                if t not in seen:
                    seen.add(t)
                    targets.append(t)
        self.verify_targets = targets[:8]

    def to_dict(self) -> dict:
        """把持久化字段序列化为 JSON-safe dict（P2-4 WorkingSet 持久化）。

        验证关联以 verify_map 为权威行为事实（path → sorted target list），
        跨 Runtime 恢复后保持 guard / 精确清账语义。failure_target 与
        failure_context 一并恢复，以便成功测试时精确清除失败上下文。
        """
        vmap = {}
        for path, targets in (self.verify_map or {}).items():
            p = str(path or "").strip()
            if not p:
                continue
            ts = sorted({str(t).strip() for t in (targets or ()) if str(t).strip()})
            if ts:
                vmap[p] = ts
        return {
            "goal": self.goal,
            "constraints": list(self.constraints),
            "files_read": list(self.files_read),
            "files_edited": list(self.files_edited),
            "open_hypotheses": list(self.open_hypotheses),
            "pending_verify": list(self.pending_verify),
            "verify_targets": list(self.verify_targets),
            "failure_context": list(self.failure_context),
            "verify_map": vmap,
            "failure_target": self.failure_target,
        }

    @classmethod
    def from_dict(cls, data: dict | None) -> "WorkingSet":
        """从 JSON dict 重建；缺失/损坏/非 dict 一律按空处理，绝不抛异常。"""
        d = data if isinstance(data, dict) else {}

        def _str_list(v) -> list[str]:
            out: list[str] = []
            for item in (v if isinstance(v, list) else []):
                s = str(item).strip()
                if s:
                    out.append(s)
            return out

        ft = d.get("failure_target")
        if ft is not None:
            ft = str(ft).strip() or None

        ws = cls(
            goal=str(d.get("goal") or ""),
            constraints=_str_list(d.get("constraints")),
            files_read=_str_list(d.get("files_read")),
            files_edited=_str_list(d.get("files_edited")),
            open_hypotheses=_str_list(d.get("open_hypotheses")),
            pending_verify=_str_list(d.get("pending_verify")),
            verify_targets=_str_list(d.get("verify_targets")),
            failure_context=list(d.get("failure_context") or []),
            verify_map=cls._normalize_verify_map(d.get("verify_map")),
            failure_target=ft,
        )
        # 有 map 时以其为权威同步表达字段；无 map 的旧快照保持原 pending/targets
        ws._sync_verify_views_from_map()
        return ws



# --------------------------------------------------------------------------- #
# P2-3：长任务骨架 checkpoint
#
# 只做一件事：把已有 WorkingSet 压成几行，周期性地作为**瞬时** system 注入交给
# 下一轮模型，让弱模型不丢「目标/已完成/下一步/风险」。不是第二套状态系统：
# 所有内容都从 WorkingSet 读，字段为空写「无」，绝不编造。
# --------------------------------------------------------------------------- #

PROGRESS_MARKER = "[PROGRESS]"
FINAL_CHECKPOINT_MARKER = "[FINAL CHECKPOINT]"
_CHECKPOINT_MARKERS = (PROGRESS_MARKER, FINAL_CHECKPOINT_MARKER)
_EMPTY = "无"


def _one_line(text, limit: int = 160) -> str:
    """压成单行并截断；空值返回空串。"""
    t = " ".join(str(text or "").split())
    return t[:limit]


def _ck_done(ws: "WorkingSet") -> str:
    """done：已完成的编辑，最多 3 项。只来自 files_edited（成功 mutation 才入账）。"""
    items = [_one_line(p, 80) for p in (ws.files_edited or [])[-3:]]
    items = [i for i in items if i]
    return ", ".join(items) if items else _EMPTY


def _ck_next(ws: "WorkingSet") -> str:
    """next：优先待验证 target（最可执行），否则 pending_verify。"""
    if ws.verify_targets:
        return _one_line(
            f"run_test_structured(target={ws.verify_targets[0]!r}) 验证已改动文件"
        )
    if ws.pending_verify:
        return _one_line(ws.pending_verify[-1], 120)
    return _EMPTY


def _ck_risk(ws: "WorkingSet") -> str:
    """risk：已有阻塞信息，来自 open_hypotheses / failure_context。"""
    if ws.open_hypotheses:
        return _one_line(ws.open_hypotheses[-1], 120)
    if ws.failure_context:
        c = ws.failure_context[0]
        if isinstance(c, dict):
            return _one_line(f"测试失败于 {c.get('file', '?')}:{c.get('line', '?')}", 120)
        return _one_line(c, 120)
    return _EMPTY


def _ck_unfinished(ws: "WorkingSet") -> str:
    items = [_one_line(p, 80) for p in (ws.pending_verify or [])[-3:]]
    items = [i for i in items if i]
    return ", ".join(items) if items else _EMPTY


def _progress_checkpoint_text(ws: "WorkingSet") -> str:
    """周期性强制 checkpoint：要求模型先复述状态，再继续工具调用。"""
    return (
        f"{PROGRESS_MARKER}\n"
        f"goal: {_one_line(ws.goal, 200) or _EMPTY}\n"
        f"done: {_ck_done(ws)}\n"
        f"next: {_ck_next(ws)}\n"
        f"risk: {_ck_risk(ws)}\n"
        "在继续任何工具调用之前，先按上面 4 行原样输出当前状态（这是强制 checkpoint，"
        "不是建议）。信息以上面给出的为准，不要重新猜测 goal；字段为空就写「无」。"
    )


def _final_checkpoint_text(ws: "WorkingSet") -> str:
    """接近最大步数时的收束 checkpoint：停止探索，立即总结。"""
    return (
        f"{FINAL_CHECKPOINT_MARKER}\n\n"
        "你已接近本轮最大工具调用次数。\n"
        "停止新的无关探索。\n\n"
        "请立即总结：\n\n"
        f"goal: {_one_line(ws.goal, 200) or _EMPTY}\n"
        f"done: {_ck_done(ws)}\n"
        f"unfinished: {_ck_unfinished(ws)}\n"
        f"next: {_ck_next(ws)}\n\n"
        "如果任务已经完成，不要继续调用工具。\n"
        "如果仍未完成，只指出最必要的下一步。"
    )


def _is_checkpoint_message(m) -> bool:
    """是否为本机制注入的瞬时 checkpoint system 消息（每轮重建，不进历史）。"""
    if getattr(m, "role", None) != "system":
        return False
    content = getattr(m, "content", None)
    return isinstance(content, str) and content.startswith(_CHECKPOINT_MARKERS)


def _checkpoint_for_step(
    ws: "WorkingSet",
    step_i: int,
    mutation_pending: bool,
    max_steps: int,
) -> str:
    """本轮该注入哪种 checkpoint（空串表示不注入）。

    - 最后 FINAL_CHECKPOINT_TAIL_STEPS 步：收束，取代周期 progress。
    - 每 PROGRESS_CHECKPOINT_EVERY 步，或上一轮有成功 mutation：progress。
    """
    if step_i >= max_steps - FINAL_CHECKPOINT_TAIL_STEPS:
        return _final_checkpoint_text(ws)
    if mutation_pending or (step_i > 0 and step_i % PROGRESS_CHECKPOINT_EVERY == 0):
        return _progress_checkpoint_text(ws)
    return ""


# 连续对话工作模型（无 Planning/Execution 硬阶段）
_CONTINUOUS_INSTRUCTION = """
## 工作方式
你可以自由读取、分析、验证；需要改变外部状态（改文件、发嘟文等）时直接调用对应工具。
Runtime 会在真正执行写操作前要求用户确认（确认的是这一次精确动作，不是「进入执行模式」）。
forge_sync：先观察同步状态；仅需安全推进时再确认后执行。undo_last_tx 按恢复规则直接执行（仍受 Guard）。
可选：复杂任务可用 submit_plan 先给出方案供讨论，但这不是写操作的必经前门。
"""

# 确认词 + 后续分隔符：用于把「确认，另外改下 b.py」拆成「确认」+「补充意见」，
# 避免把用户确认时顺带说的补充意见丢掉。
_CONFIRM_PREFIX_RE = re.compile(
    r"^(?:确认|confirm|commit|ok|yes|y|执行|go)\b\s*[，,。.、:：\s]*",
    re.IGNORECASE,
)


def _strip_confirm_prefix(text: str) -> str:
    """去掉开头的确认词，返回剩余补充意见（裸确认返回空串）。"""
    return _CONFIRM_PREFIX_RE.sub("", (text or "").strip(), count=1).strip()



def _append_conversation_log(project_root: str, role: str, content: str, **extra) -> None:
    """Append one JSONL line to .forge/conversation_log.jsonl for search_history."""
    import json
    import time
    from pathlib import Path as _P
    try:
        log_dir = _P(project_root) / ".forge"
        log_dir.mkdir(parents=True, exist_ok=True)
        path = log_dir / "conversation_log.jsonl"
        rec = {"ts": time.time(), "role": role, "content": (content or "")[:4000], **extra}
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception as e:
        print(f"[forge] _append_conversation_log failed: {e}", file=sys.stderr)



def _load_session_summary(project_root: str) -> str:
    """Load prior session summary / conversation history for system prompt."""
    from pathlib import Path as _P
    root = _P(project_root) / ".forge"
    notes = []
    tasks = []
    try:
        hist = root / "conversation_history.json"
        if hist.is_file():
            data = json.loads(hist.read_text(encoding="utf-8"))
            notes = list(data.get("notes") or [])
            summary = data.get("summary") or {}
            tasks = list(summary.get("last_tasks") or [])
            if not notes:
                notes = list(summary.get("last_conclusions") or [])
        path = root / "session_summary.json"
        if path.is_file():
            data = json.loads(path.read_text(encoding="utf-8"))
            notes = notes or list(data.get("notes") or data.get("summaries") or [])
    except Exception as e:
        print(f"[forge] _load_session_summary failed: {e}", file=sys.stderr)
        return ""
    if not notes and not tasks:
        return ""
    parts = ["\n\n## 上次会话摘要"]
    if tasks:
        parts.append("任务:")
        parts.extend(f"- {t}" for t in tasks[-3:])
    if notes:
        parts.append("结论:")
        parts.extend(f"- {n[:300]}" for n in notes[-5:])
    return "\n".join(parts)
def _save_session_summary(project_root: str, assistant_replies: list[str]) -> None:
    from pathlib import Path as _P
    try:
        log_dir = _P(project_root) / ".forge"
        log_dir.mkdir(parents=True, exist_ok=True)
        path = log_dir / "session_summary.json"
        notes = [r.strip()[:500] for r in assistant_replies if r and r.strip()][-5:]
        path.write_text(
            json.dumps({"notes": notes}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception as e:
        print(f"[forge] _save_session_summary failed: {e}", file=sys.stderr)


# P2-4: WorkingSet 跨 Runtime 持久化。只做 JSON 全量存取，不做增量同步/版本迁移。
_TASK_STATE_FILENAME = "task_state.json"


def _save_task_state(project_root: str, ws: "WorkingSet") -> None:
    """把 WorkingSet 写到 .forge/task_state.json（best-effort，不阻塞会话）。

    失败语义：按产品裁定 task_state 本应 DEGRADED，但当前无机器消费端
    （下次启动仅 best-effort 加载，失败则静默重建 WorkingSet），因此先按
    WARN 处理——只打日志，不挂 runtime.degraded_components。若日后 Guard
    或启动路径读取 is_degraded("task_state")，再升级为 DEGRADED。
    """
    from pathlib import Path as _P
    try:
        log_dir = _P(project_root) / ".forge"
        log_dir.mkdir(parents=True, exist_ok=True)
        path = log_dir / _TASK_STATE_FILENAME
        path.write_text(
            json.dumps(ws.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception as e:
        # WARN only — no degraded_components consumer yet (see docstring).
        print(f"[forge] _save_task_state failed (WARN): {e}", file=sys.stderr)


def _load_task_state(project_root: str) -> dict | None:
    """读 .forge/task_state.json；缺失/非对象返回 None，损坏则记日志后返回 None（不阻塞启动）。"""
    from pathlib import Path as _P
    try:
        path = _P(project_root) / ".forge" / _TASK_STATE_FILENAME
        if not path.is_file():
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except Exception as e:
        print(f"[forge] _load_task_state failed: {e}", file=sys.stderr)
        return None


def _sync_status_system_hint(report) -> str:
    """把一次同步判定（SyncReport）转成首轮 system 注入的一等提示。

    P2-2：`_startup_sync_check` 发现同步状态后，不再只写 stderr，而是把状态
    作为首轮 system 上下文交给 Agent，让 Agent 明确下一步该做什么。

    纯文本格式化，不改变任何同步/权限逻辑；返回空串表示无需注入。
    """
    if report is None:
        return ""
    from forge.sync.sync_layer import (
        CONFLICT,
        FAST_FORWARD_DISK_TO_WORLD,
        FAST_FORWARD_WORLD_TO_DISK,
        IN_SYNC,
        NOT_A_GIT_REPO,
        WORLD_UNAVAILABLE,
    )

    status = getattr(report, "status", "") or ""
    detail = getattr(report, "detail", "") or ""

    if status == IN_SYNC:
        return (
            "\n\n## 同步状态\n"
            "sync=IN_SYNC：World 与 Disk/Git 处于同一已知状态，可直接继续，不阻塞。"
        )

    if status == CONFLICT:
        kind = getattr(report, "conflict_kind", None) or ""
        lines = [
            "## 同步状态（一等上下文）",
            "sync=CONFLICT：工作区可能与 World 分叉，当前禁止 mutation。",
        ]
        if kind:
            lines.append(f"conflict_kind={kind}")
        lines += [
            "- 在 forge_sync 完成对账、确认恢复到可继续状态之前，禁止任何 mutation"
            "（str_replace/write_file/undo_last_tx 等）。",
            "- 必须优先调用 forge_sync 查看 diff 并显式决策同步方向。",
            "- 你首次面向用户的回复应解释当前冲突，以及下一步如何同步。",
        ]
        if detail:
            lines.append(f"详情：{detail}")
        return "\n\n" + "\n".join(lines)

    if status in (FAST_FORWARD_DISK_TO_WORLD, FAST_FORWARD_WORLD_TO_DISK):
        direction = (
            "World → Disk" if status == FAST_FORWARD_WORLD_TO_DISK else "Disk → World"
        )
        lines = [
            "## 同步状态（一等上下文）",
            f"sync={status}（方向：{direction}）",
            "- 同步完成前禁止任何 mutation。",
            f"- 必须先调用 forge_sync 完成同步，方向为 {direction}（以 SyncReport 实际结果为准）。",
        ]
        if detail:
            lines.append(f"详情：{detail}")
        return "\n\n" + "\n".join(lines)

    if status == WORLD_UNAVAILABLE:
        lines = [
            "## 同步状态（一等上下文）",
            "sync=WORLD_UNAVAILABLE：World（veritasd）不可达。",
            "- 允许使用 direct_disk 文件工具（str_replace/write_file/undo_last_tx）直写磁盘。",
            "- 纯 World 操作（create_object/link_objects/unlink_objects）不可用。",
            "- 恢复 veritasd 后运行 forge_sync 重新对账。",
        ]
        if detail:
            lines.append(f"详情：{detail}")
        return "\n\n" + "\n".join(lines)

    if status == NOT_A_GIT_REPO:
        return (
            "\n\n## 同步状态\n"
            "sync=NOT_A_GIT_REPO：当前工作区不是 Git 仓库，同步能力不可用。"
        )

    return ""


def _direct_disk_reconcile_hint(project_root: str, world_available: bool) -> str:
    """P2-1c：World 已恢复但存在 direct_disk 待对账文件时，返回提示文本（否则空串）。

    direct_disk 写入不产生 World receipt，恢复 veritasd 后需要 forge_sync 把磁盘
    变更 FAST_FORWARD 回 World。这里只读持久化的 session_changes 标记，只提示，
    不自动对账（保持「不自动 merge」原则）。World 仍不可达时返回空串——此时
    对账无从谈起，`_sync_system_hint` 的 WORLD_UNAVAILABLE 分支已给出指引。
    """
    if not world_available:
        return ""
    from forge.tools.session_changes import pending_direct_disk

    pending = pending_direct_disk(project_root)
    if not pending:
        return ""
    paths: list[str] = []
    seen: set[str] = set()
    for e in pending:
        p = str(e.get("path") or "").strip()
        if p and p not in seen:
            seen.add(p)
            paths.append(p)
    if not paths:
        return ""
    lines = [
        "\n\n## direct_disk 待对账（World 已恢复）",
        "以下文件曾在 veritasd 不可达时以 direct_disk 模式修改，World 未记录：",
    ]
    for p in paths[:20]:
        lines.append(f"- {p}")
    if len(paths) > 20:
        lines.append(f"  ... 共 {len(paths)} 个文件")
    lines.append(
        "请运行 forge_sync 把磁盘变更 FAST_FORWARD 回 World；"
        "对账前这些文件的 World 状态与磁盘不一致。"
    )
    return "\n".join(lines)


# 结果确认型工具：第一行就是精华（RESULT: path=... tx=... 之类），压成一行安全。
# 其余(默认)按"内容承载型"处理：read_file/str_replace/near_miss/diff 等，
# 精华常常不在第一行，压缩过头是长会话后期质量下滑的直接原因之一。
# NOTE: 这份名单是从代码里认出的工具名猜的，请对照 forge/tools/schemas.py
# 里的实际工具名核对一遍，缺漏的往"内容型"（默认分支）方向偏，不要往
# "确认型"偏——宁可少压缩，不要错压缩。
_CONFIRMATION_TOOLS = {
    "write_file", "modify_file", "undo_last_tx", "create_object",
    "delete_file", "create_file", "unlink_objects", "link_objects",
    "run_test_structured", "apply_patch", "edit_files_batch", "todo_write",
}


def _is_near_miss_or_fail_content(content: str) -> bool:
    c = content or ""
    cl = c.lower()
    return (
        "near_miss" in cl
        or "NEAR_MISS" in c
        or cl.startswith("fail")
        or "status: fail" in cl
        or "failed:" in cl
    )


def _path_mentioned(content: str, paths: set[str]) -> bool:
    if not paths or not content:
        return False
    for p in paths:
        if p and p in content:
            return True
    return False


def _summarize_tool_message(name: str, content: str) -> str:
    """Build a compressed summary for one tool message."""
    stripped = (content or "").strip()
    if not stripped:
        return f"[compressed FACT {name}] "
    if name in _CONFIRMATION_TOOLS:
        # Keep first line (usually RESULT: path=... tx=...)
        first = stripped.splitlines()[0][:160]
        return f"[compressed FACT {name}] {first}"
    # content-bearing: keep a modest head
    lines = stripped.splitlines()
    kept = lines[:8]
    body = "\n".join(kept)[:800]
    truncated = len(lines) > 8 or len(body) < len(stripped)
    more = (
        f"\n...[截断，原始长度 {len(stripped)} 字符/{len(lines)} 行]"
        if truncated
        else ""
    )
    return f"[compressed {name}]\n{body}{more}"


def _compress_messages(
    messages: list,
    keep_recent_tools: int = 6,
    working_set: "WorkingSet | None" = None,
) -> list:
    """Replace older tool results with summaries to curb context rot.

    P1-2 rules (when working_set provided):
    - Prefer retaining tool results related to goal / files_read / files_edited.
    - read_file / search_code / str_replace failures especially NEAR_MISS:
      keep the most recent 2 uncompressed.
    - Confirmation tools may collapse to one line but must keep path + tx.
    - Unrelated old tool output is compressed first.
    - [Working Set] system messages are never dropped or altered.
    """
    if len(messages) < 24:
        return messages

    tool_idxs = [i for i, m in enumerate(messages) if getattr(m, "role", None) == "tool"]
    if len(tool_idxs) <= keep_recent_tools:
        return messages

    relevant_paths: set[str] = set()
    if working_set is not None:
        relevant_paths.update(working_set.files_read or [])
        relevant_paths.update(working_set.files_edited or [])
        if working_set.goal:
            # crude: any path-like tokens already tracked; goal text alone not a path
            pass

    # Protect recent NEAR_MISS / fail results for key tools (last 2)
    protect_names = {"read_file", "search_code", "str_replace", "read_file_with_lines"}
    near_miss_idxs = [
        i
        for i in tool_idxs
        if (getattr(messages[i], "name", None) in protect_names)
        and _is_near_miss_or_fail_content(getattr(messages[i], "content", None) or "")
    ]
    protected = set(near_miss_idxs[-2:])

    # Always keep the most recent keep_recent_tools tool messages
    recent = set(tool_idxs[-keep_recent_tools:])

    # Prefer not compressing relevant-path tool results when possible
    relevant_idxs = []
    if relevant_paths:
        for i in tool_idxs:
            if i in recent or i in protected:
                continue
            content = getattr(messages[i], "content", None) or ""
            if _path_mentioned(content, relevant_paths):
                relevant_idxs.append(i)
    # Keep up to 4 older relevant results uncompressed
    protected.update(relevant_idxs[-4:])

    drop = set(tool_idxs) - recent - protected

    out = []
    for i, m in enumerate(messages):
        # Never alter Working Set injection
        if (
            getattr(m, "role", None) == "system"
            and isinstance(getattr(m, "content", None), str)
            and getattr(m, "content", "").startswith("[Working Set]")
        ):
            out.append(m)
            continue
        if i in drop:
            name = getattr(m, "name", None) or "tool"
            content = getattr(m, "content", None) or ""
            summary = _summarize_tool_message(name, content)
            try:
                from forge.adapters.base import Message as ForgeMessage

                out.append(
                    ForgeMessage(
                        role="tool",
                        content=summary,
                        tool_call_id=getattr(m, "tool_call_id", None),
                        name=name,
                    )
                )
            except Exception:
                out.append(m)
        else:
            out.append(m)
    return out


def _todo_nudge_from_tools(tools: dict) -> str:
    """If todo_list exists and has pending items, return a short reminder."""
    fn = tools.get("todo_list")
    if not fn:
        return ""
    try:
        r = fn()
        items = (r.payload or {}).get("todos") or []
        pending = [it for it in items if it.get("status") in ("pending", "in_progress")]
        if not pending:
            return ""
        lines = [f"- [{it.get('status')}] {it.get('content')}" for it in pending[:7]]
        return "\n[system reminder] 未完成 todo（以用户最新消息为准）:\n" + "\n".join(lines)
    except Exception as e:
        print(f"[forge] _todo_nudge_from_tools failed: {e}", file=sys.stderr)
        return ""


class ToolExecutor:
    def __init__(self, tools: dict):
        self.tools = tools
        self.call_history: dict[str, list[str]] = {}

    def _args_signature(self, tool_name: str, arguments: dict) -> str:
        """Signature for retry circuit-breaker.

        str_replace: tool+path+hash(old_string) so changing old_string is a fresh attempt.
        Other tools: full args JSON.
        """
        args = arguments or {}
        if tool_name == "str_replace":
            import hashlib
            path = str(args.get("path") or "")
            old = str(args.get("old_string") or "")
            h = hashlib.sha1(old.encode("utf-8", errors="replace")).hexdigest()[:12]
            return f"str_replace:{path}:{h}"
        if tool_name == "write_file":
            import hashlib
            path = str(args.get("path") or "")
            content = str(args.get("content") or "")
            h = hashlib.sha1(content.encode("utf-8", errors="replace")).hexdigest()[:12]
            return f"write_file:{path}:{h}"
        if tool_name == "modify_file":
            import hashlib
            path = str(args.get("path") or "")
            ops = args.get("operations") or []
            ops_json = json.dumps(ops, sort_keys=True, ensure_ascii=False, default=str)
            h = hashlib.sha1(ops_json.encode("utf-8", errors="replace")).hexdigest()[:12]
            return f"modify_file:{path}:{h}"
        return tool_name + ":" + json.dumps(
            args, sort_keys=True, ensure_ascii=False, default=str
        )

    def reset(self):
        self.call_history.clear()

    def execute(self, tool_call) -> ToolResult:
        # All tools executable directly. Mutation tools internally handle
        # transaction begin/commit/abort via IntentExecutor + WorldSession.
        if tool_call.name in MUTATION_TOOL_NAMES:
            pass  # mutation tools allowed — they handle commit/abort internally
        fn = self.tools.get(tool_call.name)
        if not fn:
            return ToolResult.fail(display=f"未知工具: {tool_call.name}")

        sig = self._args_signature(tool_call.name, tool_call.arguments)
        history = self.call_history.get(sig, [])

        consecutive_failures = 0
        last_kind = ""
        for s in reversed(history):
            if s.startswith("fail"):
                consecutive_failures += 1
                if not last_kind and ":" in s:
                    last_kind = s.split(":", 1)[1]
            else:
                break

        _KIND_ADVICE = {
            "type_mismatch": "参数结构反复不对，重新读一遍工具schema再改参数，不要靠猜。",
            "exception": "运行时异常反复出现，问题可能不在参数上，检查前置状态(文件是否存在/veritasd是否在线)。",
            "logic": "工具正常执行但业务上判定失败(如old_string未找到)，仔细核对返回里的HINT/NEAR_MISS。",
        }

        if consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
            advice = _KIND_ADVICE.get(last_kind, "请换策略、read 核对，或直接问用户。")
            return ToolResult.fail(
                display=(
                    f"STOP_HINT: 同一调用已连续失败 {consecutive_failures} 次(原因: {last_kind or '未知'})，已禁止再试。\n"
                    f"  {tool_call.name}({json.dumps(tool_call.arguments, ensure_ascii=False)})\n"
                    f"{advice} 不要继续微调同一参数。"
                )
            )

        try:
            result = fn(**tool_call.arguments)
            status = "success" if result.success else "fail:logic"
            self.call_history.setdefault(sig, []).append(status)
            if not result.success and consecutive_failures >= 1:
                # after 1 prior fail, this is 2nd+ failure in a row for same sig
                prefix = (
                    f"STOP_HINT: 该调用已连续失败 {consecutive_failures + 1} 次(原因: logic)。"
                    f"请换方向或问用户，勿重复同一操作。\n"
                )
                if result.display and "STOP_HINT" not in result.display:
                    result.display = prefix + result.display
            return result
        except TypeError as e:
            self.call_history.setdefault(sig, []).append("fail:type_mismatch")
            return ToolResult.fail(
                display=f"参数不匹配: {e}\n收到的参数: {tool_call.arguments}"
            )
        except Exception as e:
            self.call_history.setdefault(sig, []).append("fail:exception")
            return ToolResult.fail(display=f"工具执行异常: {type(e).__name__}: {e}")


def _build_agent_task_from_spawn_args(
    goal: str = "",
    done_when: str = "",
    stop_when: str = "",
    not_allowed=None,
    scope=None,
    max_steps: int = 15,
    task: str = "",
    subtask_id: str | None = None,
):
    """R3: 把 spawn_subagent 入口参数折叠成完整 AgentTask。

    task 仅作 goal 的 legacy alias。
    not_allowed / scope 折叠进 constraints，由 constraint_enforcer 在子循环内强制。
    """
    from forge.agent_abi import AgentTask

    goal_text = (goal or task or "").strip()
    constraints: dict = {}
    if not_allowed is not None and not_allowed != "":
        constraints["not_allowed"] = not_allowed
    if scope is not None and scope != "":
        if isinstance(scope, str):
            # 主 AI 可能传逗号分隔字符串："forge/a.py, forge"
            # 拆成多个路径前缀，避免被当成一个带逗号的完整前缀。
            parts = [s.strip() for s in scope.split(",") if s.strip()]
            constraints["scope"] = {"paths": parts or [scope.strip()]}
        elif isinstance(scope, (list, tuple)):
            constraints["scope"] = {
                "paths": [str(p).strip() for p in scope if str(p).strip()]
            }
        elif isinstance(scope, dict):
            constraints["scope"] = scope
        else:
            constraints["scope"] = {"paths": [str(scope)]}

    return AgentTask(
        goal=goal_text,
        subtask_id=subtask_id,
        done_when=str(done_when or ""),
        stop_when=str(stop_when or ""),
        constraints=constraints,
        max_steps=int(max_steps) if max_steps else 15,
    )


class Runtime:
    def __init__(
        self,
        adapter: BaseAdapter,
        workspace: Workspace,
        memory: MemoryStore,
        confirm_provider=None,
    ):
        self.adapter = adapter
        self.workspace = workspace
        self._confirm_provider = confirm_provider
        # Cooperative soft-stop (Ctrl+C during run). Not a state-machine phase.
        self._stop_requested = False
        # Optional CLI presentation hooks (TerminalPresenter); not part of agent semantics.
        self._on_assistant_delta = None
        self._on_assistant_done = None
        self._assistant_streamed = False
        self._last_response_needs_display = False
        self.memory = memory
        self.world = WorldRuntime(project_root=workspace.project_root)
        # P2-1a 冷启动降级：Identity 建立失败不再使 Runtime 无法启动，
        # 而是置 world_available=False，进入 direct_disk 降级模式。
        # 文件内容 mutation 会在工具层探测到 World 不可达并走直写；
        # 纯 World 操作（create_object/link_objects 等）保持硬失败。
        self.world_available = True
        try:
            self.world.ensure_identity()
        except Exception as e:
            self.world_available = False
            print(
                f"[forge] World (veritasd) 不可达，Runtime 以降级模式启动：{e}\n"
                f"        文件内容 mutation 走 direct_disk；纯 World 操作不可用。",
                file=sys.stderr,
            )

        from forge.sync.state import SyncState
        from forge.sync.sync_layer import SyncLayer
        from forge.runtime_state import RuntimeStateStore

        # Sync metadata 权威状态（决策 1：.forge/sync_state.json，不放入 Veritas）。
        self.sync_state = SyncState(project_root=workspace.project_root)

        # R1: RuntimeState 生命周期真相（.forge/runtime_state.json）。
        # 启动只加载 + 推导 recovery；不自动续跑子循环、不消费 Gate。
        self._runtime_state_store = RuntimeStateStore(
            project_root=workspace.project_root
        )
        self.runtime_state = self._runtime_state_store.load()
        self.recovery = self.runtime_state.recovery

        # Durable Pause: load SubtaskCheckpoint + derive SubtaskRecovery (§7).
        from forge.subtask_checkpoint import (
            SubtaskCheckpointStore,
            derive_subtask_recovery,
            validate_checkpoint_facts,
            SUBTASK_RECOVERY_INCONSISTENT,
        )
        self._subtask_checkpoint_store = SubtaskCheckpointStore(
            project_root=workspace.project_root
        )
        _cp = self._subtask_checkpoint_store.load()
        self.subtask_recovery = derive_subtask_recovery(
            _cp,
            self.runtime_state.phase,
            self.runtime_state.active_subtask_id,
        )
        if (
            self.subtask_recovery.mode == SUBTASK_RECOVERY_INCONSISTENT
            and self.subtask_recovery.checkpoint is not None
        ):
            fact_ok = validate_checkpoint_facts(
                workspace.project_root, self.subtask_recovery.checkpoint
            )
            self.subtask_recovery = type(self.subtask_recovery)(
                mode=self.subtask_recovery.mode,
                checkpoint=self.subtask_recovery.checkpoint,
                reason=self.subtask_recovery.reason,
                fact_valid=fact_ok,
            )

        # R2: SyncDecision 持久化（.forge/sync_decision.json），与 RuntimeState.pending 对齐。
        from forge.sync.decision import SyncDecisionStore

        self._sync_decision_store = SyncDecisionStore(
            project_root=workspace.project_root
        )
        self.sync_decision = self._sync_decision_store.load()
        # P1-01: align RuntimeState.pending with SyncDecision before any tools run.
        self._reconcile_sync_decision_pending()
        path_map = getattr(self.world, "_path_map", None)
        file_projection = FileProjection(
            project_root=workspace.project_root,
            object_path_map=path_map,
            sync_state=self.sync_state,
        )
        self.projections = ProjectionManager(
            checkpoint_dir=os.path.join(workspace.project_root, ".forge")
        )
        self.projections.register(file_projection)
        self.projections.register(GitProjection(project_root=workspace.project_root))
        self.projections.register(IndexProjection(project_root=workspace.project_root))

        self.sync_layer = SyncLayer(
            project_root=workspace.project_root,
            world_runtime=self.world,
            sync_state=self.sync_state,
            file_projection=file_projection,
        )

        try:
            self._startup_sync_check()
        except Exception as e:
            print(f"[sync] startup check error: {e}", file=sys.stderr)

        # Single tool-loop: all tools available (read + mutation).
        # Mutations go through IntentExecutor → WorldSession → Veritas,
        # with commit/abort handled inside the tool call.
        tools = make_tools(
            workspace=workspace,
            world_runtime=self.world,
            projections=self.projections,
            allow_mutation=True,
            sync_layer=self.sync_layer,
        )
        def spawn_subagent(
            goal: str = "",
            done_when: str = "",
            stop_when: str = "",
            not_allowed=None,
            scope=None,
            max_steps: int = 15,
            task: str = "",
        ) -> ToolResult:
            """Run an isolated subagent tool-loop; return AgentResult summary text.

            AgentTask contract entry (R3):
              goal / done_when / stop_when / not_allowed / scope / max_steps
            ``task`` is a legacy alias for ``goal`` only (not a parallel contract).
            not_allowed / scope fold into AgentTask.constraints and are enforced
            by constraint_enforcer inside the subagent loop before tool execution.
            """
            from forge.agent_abi import (
                AgentTask,
                format_agent_result_for_parent,
                precheck_agent_result,
            )
            from forge.subagent import run_subagent
            import uuid
            try:
                schemas = list(EXECUTION_PLANE_TOOL_DECLARATIONS)
                sub_tools = {k: v for k, v in tools.items() if k in EXECUTION_PLANE_TOOLS}

                # R1 phase lifecycle: DISPATCHING before spawning
                from forge.runtime_state import (
                    PHASE_DISPATCHING,
                    PHASE_IDLE,
                    PHASE_RUNNING_SUBTASK,
                )
                subtask_id = f"sub_{uuid.uuid4().hex[:12]}"
                # Gate: human_intervention pending blocks spawn
                _hi = self._guard_human_intervention_pending("spawn_subagent")
                if _hi is not None:
                    return _hi
                if getattr(self, "_runtime_state_store", None) is not None:
                    self.runtime_state.phase = PHASE_DISPATCHING
                    self.runtime_state.active_subtask_id = subtask_id
                    self.runtime_state.refresh_recovery()
                    self.recovery = self.runtime_state.recovery
                    self._runtime_state_store.save(self.runtime_state)

                agent_task = _build_agent_task_from_spawn_args(
                    goal=goal,
                    done_when=done_when,
                    stop_when=stop_when,
                    not_allowed=not_allowed,
                    scope=scope,
                    max_steps=max_steps,
                    task=task,
                    subtask_id=subtask_id,
                )

                # R1 phase lifecycle: RUNNING_SUBTASK before entering sub-loop
                if getattr(self, "_runtime_state_store", None) is not None:
                    self.runtime_state.phase = PHASE_RUNNING_SUBTASK
                    self.runtime_state.refresh_recovery()
                    self.recovery = self.runtime_state.recovery
                    self._runtime_state_store.save(self.runtime_state)
                def _subagent_confirm(summary: str) -> bool:
                    """Host-side confirmation; delegates to CLI input provider."""
                    if self._confirm_provider is None:
                        return False
                    try:
                        return bool(self._confirm_provider(summary))
                    except KeyboardInterrupt:
                        self._stop_requested = True
                        raise
                    except Exception:
                        return False

                result = run_subagent(
                    self.adapter,
                    sub_tools,
                    schemas,
                    agent_task,
                    project_root=workspace.project_root,
                    confirm_fn=_subagent_confirm,
                    emit=self.emit,
                    should_stop=self.stop_requested,
                )
                # Machine precheck: re-verify evidence against on-disk ToolCallRecord
                # before the main agent sees the result.
                result = precheck_agent_result(workspace.project_root, result)
                if "user_stop" in str(getattr(result, "status_reason", "") or ""):
                    self._stop_requested = True
                # Persist structured AgentResult by subtask_id (memory + JSONL)
                # so main agent can call verify_subtask_evidence(subtask_id)
                # without re-emitting machine identity through LLM text.
                if result.subtask_id:
                    ar_dict = result.to_dict()
                    self._subagent_results[str(result.subtask_id)] = ar_dict
                    from forge.subagent_results_store import append_subagent_result
                    append_ok = append_subagent_result(workspace.project_root, ar_dict)

                    # Durable Pause §6.2: clear checkpoint ONLY after append success.
                    if append_ok:
                        try:
                            if getattr(self, "_subtask_checkpoint_store", None) is not None:
                                self._subtask_checkpoint_store.clear()
                        except Exception:
                            pass
                        # R1 phase lifecycle: reset to IDLE after result persisted
                        if getattr(self, "_runtime_state_store", None) is not None:
                            self.runtime_state.phase = PHASE_IDLE
                            self.runtime_state.active_subtask_id = None
                            self.runtime_state.refresh_recovery()
                            self.recovery = self.runtime_state.recovery
                            self._runtime_state_store.save(self.runtime_state)
                display = format_agent_result_for_parent(result)
                return ToolResult.ok(
                    display=display,
                    payload={
                        "subagent": True,
                        "agent_result": result.to_dict(),
                    },
                )
            except Exception as e:
                # R1 phase lifecycle: reset on spawn failure
                if getattr(self, "_runtime_state_store", None) is not None:
                    try:
                        self.runtime_state.phase = PHASE_IDLE
                        self.runtime_state.active_subtask_id = None
                        self.runtime_state.refresh_recovery()
                        self.recovery = self.runtime_state.recovery
                        self._runtime_state_store.save(self.runtime_state)
                    except Exception:
                        pass
                return ToolResult.fail(
                    display=(
                        "spawn_subagent failed: "
                        + str(e)
                        + "\n建议: 缩小子任务范围；确认模型与 veritasd 可用。"
                    )
                )

        def verify_subtask_evidence(subtask_id: str) -> ToolResult:
            """Machine verification of all Evidence for a subtask.

            Input is only subtask_id. Runtime resolves the structured AgentResult
            stored at spawn_subagent return, reads full tool_call_id values from
            AgentResult.evidence, and performs exact ToolCallRecord lookups.
            Does NOT require the main AI to re-emit UUIDs. Does NOT perform
            semantic task-completion judgment — that remains the main agent's job.
            """
            from forge.tool_call_record import get_record
            from forge.tools.display import format_block

            sid = (subtask_id or "").strip()
            if not sid:
                return ToolResult.fail(
                    display=format_block(
                        "verify_subtask_evidence",
                        "FAIL",
                        {"reason": "subtask_id required"},
                    ),
                    payload={"subtask_id": sid, "found": False},
                )
            stored = self._subagent_results.get(sid)
            if stored is None:
                return ToolResult.fail(
                    display=format_block(
                        "verify_subtask_evidence",
                        "FAIL",
                        {
                            "subtask_id": sid,
                            "reason": "no structured AgentResult stored for subtask_id",
                        },
                    ),
                    payload={"subtask_id": sid, "found": False, "evidence_results": []},
                )

            raw_evidence = stored.get("evidence") or []
            evidence_results: list[dict] = []
            all_ok = True
            for item in raw_evidence:
                if not isinstance(item, dict):
                    continue
                tc_id = str(item.get("tool_call_id") or "").strip()
                if not tc_id:
                    evidence_results.append(
                        {
                            "tool_call_id": "",
                            "ok": False,
                            "reason": "empty tool_call_id in evidence",
                            "record": None,
                            "evidence": item,
                        }
                    )
                    all_ok = False
                    continue
                rec = get_record(workspace.project_root, tc_id)
                ok = rec is not None
                if ok:
                    if str(rec.get("actor") or "") == "main":
                        ok = False
                    else:
                        rec_sid = str(rec.get("subtask_id") or "").strip()
                        if not rec_sid or rec_sid != sid:
                            ok = False
                if not ok:
                    all_ok = False
                evidence_results.append(
                    {
                        "tool_call_id": tc_id,
                        "ok": ok,
                        "reason": None if ok else "no ToolCallRecord found (exact id)",
                        "record": rec if ok else None,
                        "evidence": item,
                    }
                )

            payload = {
                "subtask_id": sid,
                "found": True,
                "agent_status": stored.get("status"),
                "evidence_count": len(evidence_results),
                "all_ok": all_ok and len(evidence_results) > 0,
                "evidence_results": evidence_results,
            }
            body_lines = [
                f"subtask_id: {sid}",
                f"agent_status: {payload['agent_status']}",
                f"evidence_count: {payload['evidence_count']}",
                f"all_ok: {payload['all_ok']}",
            ]
            for er in evidence_results:
                body_lines.append(
                    f"- tool_call_id={er['tool_call_id']} ok={er['ok']}"
                    + (f" reason={er['reason']}" if er.get("reason") else "")
                )
                if er.get("ok") and er.get("record"):
                    rec = er["record"]
                    body_lines.append(
                        f"  tool_name={rec.get('tool_name')} status={rec.get('status')}"
                    )
                    out = rec.get("output")
                    if isinstance(out, dict):
                        if "returncode" in out:
                            body_lines.append(f"  returncode={out.get('returncode')}")
                        if "stdout" in out:
                            stdout_preview = str(out.get("stdout") or "")
                            if len(stdout_preview) > 500:
                                stdout_preview = stdout_preview[:500] + "...(preview)"
                            body_lines.append(f"  stdout={stdout_preview!r}")
                        if out.get("stdout_truncated"):
                            body_lines.append("  stdout_truncated=True")
                        if "stderr" in out and out.get("stderr"):
                            body_lines.append(
                                f"  stderr={str(out.get('stderr'))[:200]!r}"
                            )

            if not evidence_results:
                body_lines.append("(no evidence items in stored AgentResult)")

            status_label = "OK" if payload["all_ok"] else "FAIL"
            display = format_block(
                "verify_subtask_evidence",
                status_label,
                {"subtask_id": sid, "all_ok": payload["all_ok"]},
                "\n".join(body_lines),
            )
            if payload["all_ok"]:
                return ToolResult.ok(display=display, payload=payload)
            return ToolResult.fail(display=display, payload=payload)

        def resolve_sync_decision_tool(direction: str) -> ToolResult:
            """控制面工具：完成 SyncDecision 决议。

            主 AI 必须先得到用户明确方向，不得自行决定。
            决议成功后只清除 pending，不自动执行 forge_sync。
            """
            from forge.adapters.base import ToolResult as TR

            try:
                decision = self.resolve_sync_decision(direction)
            except ValueError as e:
                return TR.fail(display=str(e))
            if decision is None:
                return TR.fail(display="没有待决议的 SyncDecision。")
            return TR.ok(
                display=(
                    f"SyncDecision 已决议：direction={decision.direction} "
                    f"status={decision.status}\n"
                    "pending 已清除；现在可以调用 forge_sync 推进同步。"
                ),
                payload=decision.to_dict(),
            )


        def resume_subtask(subtask_id: str = "") -> ToolResult:
            """Resume an interrupted subtask from SubtaskCheckpoint (Durable Pause).

            Requires explicit user intent via main AI. Never auto-resumes.
            """
            from forge.agent_abi import (
                AgentTask,
                AgentResult,
                format_agent_result_for_parent,
                precheck_agent_result,
                STATUS_BLOCKED,
            )
            from forge.subagent import run_subagent
            from forge.subagent_results_store import (
                load_subagent_results,
                append_subagent_result,
            )
            from forge.subtask_checkpoint import (
                MAX_RESUME_ATTEMPTS,
                SUBTASK_RECOVERY_DECISION_REQUIRED,
                SUBTASK_RECOVERY_INCONSISTENT,
                SUBTASK_RECOVERY_NONE,
                build_prior_facts_summary,
                derive_subtask_recovery,
                validate_checkpoint_facts,
            )
            from forge.runtime_state import PHASE_IDLE, PHASE_RUNNING_SUBTASK

            sid = str(subtask_id or "").strip()
            if not sid:
                return ToolResult.fail(display="resume_subtask: subtask_id required")

            _hi = self._guard_human_intervention_pending("resume_subtask")
            if _hi is not None:
                return _hi

            store = getattr(self, "_subtask_checkpoint_store", None)
            if store is None:
                return ToolResult.fail(display="resume_subtask: checkpoint store unavailable")

            # 0. Terminal AgentResult guard (design §7.2) — both paths.
            existing = load_subagent_results(workspace.project_root).get(sid)
            if existing is not None:
                store.clear()
                if getattr(self, "_runtime_state_store", None) is not None:
                    self.runtime_state.phase = PHASE_IDLE
                    self.runtime_state.active_subtask_id = None
                    self.runtime_state.refresh_recovery()
                    self.recovery = self.runtime_state.recovery
                    self._runtime_state_store.save(self.runtime_state)
                return ToolResult.ok(
                    display=(
                        f"resume_subtask: subtask_id={sid} already has terminal "
                        f"AgentResult (status={existing.get('status')}); "
                        "checkpoint cleared, no second result written."
                    ),
                    payload={"resumed": False, "reason": "already_terminal"},
                )

            cp = store.load()
            if cp is None:
                return ToolResult.fail(
                    display="resume_subtask: no SubtaskCheckpoint present"
                )
            if cp.subtask_id != sid:
                return ToolResult.fail(
                    display=(
                        f"resume_subtask: checkpoint subtask_id={cp.subtask_id} "
                        f"!= requested {sid}"
                    )
                )

            # Refresh recovery classification
            recovery = derive_subtask_recovery(
                cp, self.runtime_state.phase, self.runtime_state.active_subtask_id
            )
            if recovery.mode == SUBTASK_RECOVERY_INCONSISTENT:
                if not validate_checkpoint_facts(workspace.project_root, cp):
                    return ToolResult.fail(
                        display=(
                            "resume_subtask: INCONSISTENT and fact check failed; "
                            "only abort_subtask is allowed"
                        )
                    )
            elif recovery.mode == SUBTASK_RECOVERY_NONE:
                return ToolResult.fail(
                    display="resume_subtask: no recoverable checkpoint"
                )

            if cp.attempt_count >= MAX_RESUME_ATTEMPTS:
                return ToolResult.fail(
                    display=(
                        f"resume_subtask: attempt_count={cp.attempt_count} "
                        f">= {MAX_RESUME_ATTEMPTS}; abort instead"
                    )
                )

            # Increment attempt_count and persist before re-run
            cp.attempt_count = int(cp.attempt_count) + 1
            store.save(cp)

            task = AgentTask.from_dict(cp.task)
            # Keep original subtask_id identity
            task.subtask_id = sid
            facts = build_prior_facts_summary(workspace.project_root, sid)
            if facts:
                task.goal = (task.goal or "").rstrip() + "\n\n" + facts

            if getattr(self, "_runtime_state_store", None) is not None:
                self.runtime_state.phase = PHASE_RUNNING_SUBTASK
                self.runtime_state.active_subtask_id = sid
                self.runtime_state.refresh_recovery()
                self.recovery = self.runtime_state.recovery
                self._runtime_state_store.save(self.runtime_state)

            schemas = list(EXECUTION_PLANE_TOOL_DECLARATIONS)
            sub_tools = {k: v for k, v in tools.items() if k in EXECUTION_PLANE_TOOLS}

            def _subagent_confirm(summary: str) -> bool:
                if self._confirm_provider is None:
                    return False
                try:
                    return bool(self._confirm_provider(summary))
                except KeyboardInterrupt:
                    self._stop_requested = True
                    raise
                except Exception:
                    return False

            try:
                result = run_subagent(
                    self.adapter,
                    sub_tools,
                    schemas,
                    task,
                    project_root=workspace.project_root,
                    confirm_fn=_subagent_confirm,
                    emit=self.emit,
                    should_stop=self.stop_requested,
                )
                result = precheck_agent_result(workspace.project_root, result)
                if "user_stop" in str(getattr(result, "status_reason", "") or ""):
                    self._stop_requested = True
                if result.subtask_id:
                    ar_dict = result.to_dict()
                    self._subagent_results[str(result.subtask_id)] = ar_dict
                    append_ok = append_subagent_result(
                        workspace.project_root, ar_dict
                    )
                    if append_ok:
                        store.clear()
                        if getattr(self, "_runtime_state_store", None) is not None:
                            self.runtime_state.phase = PHASE_IDLE
                            self.runtime_state.active_subtask_id = None
                            self.runtime_state.refresh_recovery()
                            self.recovery = self.runtime_state.recovery
                            self._runtime_state_store.save(self.runtime_state)
                display = format_agent_result_for_parent(result)
                return ToolResult.ok(
                    display=display,
                    payload={
                        "resumed": True,
                        "agent_result": result.to_dict(),
                    },
                )
            except Exception as e:
                if getattr(self, "_runtime_state_store", None) is not None:
                    try:
                        self.runtime_state.phase = PHASE_IDLE
                        self.runtime_state.active_subtask_id = None
                        self.runtime_state.refresh_recovery()
                        self.recovery = self.runtime_state.recovery
                        self._runtime_state_store.save(self.runtime_state)
                    except Exception:
                        pass
                return ToolResult.fail(
                    display=f"resume_subtask failed: {e}"
                )

        def abort_subtask(subtask_id: str = "") -> ToolResult:
            """Abort an interrupted subtask; clear checkpoint (Durable Pause).

            If terminal AgentResult already exists: clean only, no second result.
            Else: synthesize blocked / abandoned_after_process_interrupt.
            """
            from forge.agent_abi import AgentResult, STATUS_BLOCKED
            from forge.subagent_results_store import (
                load_subagent_results,
                append_subagent_result,
            )
            from forge.runtime_state import PHASE_IDLE

            sid = str(subtask_id or "").strip()
            if not sid:
                return ToolResult.fail(display="abort_subtask: subtask_id required")

            store = getattr(self, "_subtask_checkpoint_store", None)
            existing = load_subagent_results(workspace.project_root).get(sid)

            def _clean_phase_and_checkpoint():
                if store is not None:
                    store.clear()
                if getattr(self, "_runtime_state_store", None) is not None:
                    self.runtime_state.phase = PHASE_IDLE
                    self.runtime_state.active_subtask_id = None
                    self.runtime_state.refresh_recovery()
                    self.recovery = self.runtime_state.recovery
                    self._runtime_state_store.save(self.runtime_state)

            if existing is not None:
                _clean_phase_and_checkpoint()
                return ToolResult.ok(
                    display=(
                        f"abort_subtask: subtask_id={sid} already has terminal "
                        f"AgentResult (status={existing.get('status')}); "
                        "checkpoint cleared, no second result written."
                    ),
                    payload={"aborted": True, "synthesized": False},
                )

            # Synthesize blocked result
            ar = AgentResult(
                subtask_id=sid,
                status=STATUS_BLOCKED,
                conclusion="subtask abandoned after process interrupt",
                evidence=(),
                uncertain="",
                next="",
                stop_when_met=False,
                status_reason="abandoned_after_process_interrupt",
                raw_conclusion="",
            )
            ar_dict = ar.to_dict()
            append_ok = append_subagent_result(workspace.project_root, ar_dict)
            if not append_ok:
                return ToolResult.fail(
                    display=(
                        f"abort_subtask: failed to persist blocked result for "
                        f"{sid}; checkpoint retained, abort can be retried."
                    )
                )
            self._subagent_results[sid] = ar_dict
            _clean_phase_and_checkpoint()
            return ToolResult.ok(
                display=(
                    f"abort_subtask: synthesized blocked result for {sid} "
                    f"(status_reason=abandoned_after_process_interrupt); "
                    "checkpoint cleared."
                ),
                payload={
                    "aborted": True,
                    "synthesized": True,
                    "agent_result": ar_dict,
                },
            )


        def request_human_intervention_tool(
            reason: str = "",
            options_context: str = "",
            proposed_next: str = "",
        ):
            from forge.adapters.base import ToolResult as TR
            return self.request_human_intervention(
                reason=reason,
                options_context=options_context or None,
                proposed_next=proposed_next or None,
            )

        def resolve_human_intervention_tool(
            decision: str = "",
            user_note: str = "",
        ):
            from forge.adapters.base import ToolResult as TR
            return self.resolve_human_intervention(
                decision=decision,
                user_note=user_note or None,
            )

        tools["spawn_subagent"] = spawn_subagent
        tools["verify_subtask_evidence"] = verify_subtask_evidence
        tools["resolve_sync_decision"] = resolve_sync_decision_tool
        tools["resume_subtask"] = resume_subtask
        tools["abort_subtask"] = abort_subtask
        tools["request_human_intervention"] = request_human_intervention_tool
        tools["resolve_human_intervention"] = resolve_human_intervention_tool
        tools["get_runtime_state"] = self.get_runtime_state
        self.executor = ToolExecutor(tools)

        self.conversation = Conversation()
        self.conversation.append(Message(role="system", content=SYSTEM_INSTRUCTION))
        self._handlers: dict = {t: [] for t in EventType}
        # Continuous Conversation + Pending Action Gate（唯一等待确认状态）
        self._pending_action: PendingAction | None = None
        # Structured AgentResult by subtask_id (machine store; not LLM-reparsed).
        # Load append-only JSONL so prior subtasks remain verifiable after restart.
        from forge.subagent_results_store import load_subagent_results
        self._subagent_results: dict[str, dict] = load_subagent_results(
            workspace.project_root
        )

    def _startup_sync_check(self):
        """启动时只做同步状态检测，不自动 replay receipt 写磁盘（决策 3/8）。

        契约 §4：发现分叉则 STOP；发现 FAST_FORWARD 也不自动推进，
        仅提示显式 forge_sync。
        """
        from forge.recovery.check import RecoveryCheck
        from forge.sync.sync_layer import (
            CONFLICT,
            FAST_FORWARD_DISK_TO_WORLD,
            FAST_FORWARD_WORLD_TO_DISK,
            IN_SYNC,
            NOT_A_GIT_REPO,
            WORLD_UNAVAILABLE,
        )

        report = RecoveryCheck(self.sync_layer).check()
        status = report.status
        if status == IN_SYNC:
            return
        if status == NOT_A_GIT_REPO:
            print("[sync] 工作区不是 Git 仓库；跳过同步状态检测。", file=sys.stderr)
            return
        if status == WORLD_UNAVAILABLE:
            print(
                "[sync] WORLD_UNAVAILABLE：veritasd 不可达，进入降级模式。\n"
                "       文件内容 mutation 走 direct_disk；纯 World 操作不可用。\n"
                "       恢复 veritasd 后运行 forge_sync 对账。",
                file=sys.stderr,
            )
            return
        if status == CONFLICT:
            print(
                "[sync] CONFLICT：World 与 Disk/Git 在共同已知状态之后都发生了独立变化。\n"
                "       已停止自动同步；不覆盖磁盘、不覆盖 World、不推进水位。\n"
                "       请运行 forge_sync 查看 diff 并显式决策。",
                file=sys.stderr,
            )
            return
        direction = (
            "World → Disk"
            if status == FAST_FORWARD_WORLD_TO_DISK
            else "Disk → World"
        )
        print(
            f"[sync] 检测到 FAST_FORWARD({direction})；启动时不自动推进。\n"
            f"       请运行 forge_sync 执行显式同步。",
            file=sys.stderr,
        )

    def sync_status(self):
        """程序化同步状态查询（返回 SyncReport）。

        R2: CONFLICT / FAST_FORWARD 时打开 SyncDecision + RuntimeState.pending。
        """
        report = self.sync_layer.detect()
        self._maybe_open_sync_decision(report)
        return report

    def _reconcile_sync_decision_pending(self) -> None:
        """Startup align RuntimeState.pending with SyncDecisionStore (P1-01).

        Runs after both stores are loaded and before tools are exposed.
        Does not choose direction and does not run forge_sync.
        HI pending is never overwritten by a PENDING SyncDecision artifact.
        """
        if getattr(self, "_sync_decision_store", None) is None:
            return
        if getattr(self, "_runtime_state_store", None) is None:
            return
        if getattr(self, "runtime_state", None) is None:
            return

        from forge.runtime_state import (
            PHASE_AWAITING_USER,
            PHASE_IDLE,
            PENDING_KIND_HUMAN_INTERVENTION,
            PENDING_KIND_SYNC_DECISION,
            Pending,
        )
        from forge.sync.decision import STATUS_PENDING

        rs = self.runtime_state
        decision = self._sync_decision_store.load()
        self.sync_decision = decision

        if decision is not None and decision.status == STATUS_PENDING:
            if rs.pending is not None and rs.pending.kind == PENDING_KIND_HUMAN_INTERVENTION:
                # HI owns the slot; leave orphan SD=PENDING for later sync_status.
                return
            if rs.pending is None or rs.pending.kind == PENDING_KIND_SYNC_DECISION:
                summary = f"sync_decision required: basis={decision.basis}"
                payload = {
                    "decision_id": decision.decision_id,
                    "basis": decision.basis,
                }
                if rs.pending is not None and isinstance(rs.pending.payload, dict):
                    # Preserve any extra payload keys; refresh id/basis.
                    payload = {**dict(rs.pending.payload), **payload}
                    if rs.pending.summary:
                        summary = rs.pending.summary
                rs.phase = PHASE_AWAITING_USER
                rs.pending = Pending(
                    kind=PENDING_KIND_SYNC_DECISION,
                    summary=summary,
                    payload=payload,
                )
                rs.refresh_recovery()
                self.recovery = rs.recovery
                try:
                    self._runtime_state_store.save(rs)
                except Exception as e:
                    print(
                        f"[runtime] reconcile_sync_decision save failed: {e}",
                        file=sys.stderr,
                    )
                return

        # RS claims sync_decision but no PENDING body → clear index only.
        if rs.pending is not None and rs.pending.kind == PENDING_KIND_SYNC_DECISION:
            if decision is None or decision.status != STATUS_PENDING:
                rs.pending = None
                if rs.phase == PHASE_AWAITING_USER:
                    rs.phase = PHASE_IDLE
                rs.refresh_recovery()
                self.recovery = rs.recovery
                try:
                    self._runtime_state_store.save(rs)
                except Exception as e:
                    print(
                        f"[runtime] reconcile clear pending save failed: {e}",
                        file=sys.stderr,
                    )

    def _maybe_open_sync_decision(self, report) -> None:
        """detect 结果需要策略点时，写入 SyncDecision 与 RuntimeState.pending。

        若同 basis 已有 decided/aborted 决议，不重复打开 pending（允许一次 forge_sync 推进）。
        IN_SYNC 时清掉已完成的 decision 文件。

        P1-01: check HI before creating/saving a new PENDING decision so a crash
        cannot leave SD=PENDING under an active human_intervention.
        """
        if getattr(self, "_sync_decision_store", None) is None:
            return
        if getattr(self, "_runtime_state_store", None) is None:
            return
        if getattr(self, "runtime_state", None) is None:
            return
        from forge.runtime_state import (
            PHASE_AWAITING_USER,
            PENDING_KIND_HUMAN_INTERVENTION,
            PENDING_KIND_SYNC_DECISION,
            Pending,
        )
        from forge.sync.decision import (
            STATUS_ABORTED,
            STATUS_DECIDED,
            STATUS_PENDING,
            SyncDecision,
            build_sync_decision_generation,
            needs_sync_decision,
        )
        from forge.sync.sync_layer import IN_SYNC

        status = getattr(report, "status", None) or ""
        if status == IN_SYNC:
            existing = self._sync_decision_store.load()
            if existing is not None:
                self._sync_decision_store.clear()
                self.sync_decision = None
            rs = getattr(self, "runtime_state", None)
            if (
                rs is not None
                and rs.pending is not None
                and rs.pending.kind == PENDING_KIND_SYNC_DECISION
            ):
                self.runtime_state.pending = None
                if self.runtime_state.phase == PHASE_AWAITING_USER:
                    from forge.runtime_state import PHASE_IDLE
                    self.runtime_state.phase = PHASE_IDLE
                self.runtime_state.refresh_recovery()
                self.recovery = self.runtime_state.recovery
                self._runtime_state_store.save(self.runtime_state)
            return

        if not needs_sync_decision(status):
            return

        # HI first: never create a new PENDING decision artifact under HI.
        if (
            self.runtime_state.pending is not None
            and self.runtime_state.pending.kind == PENDING_KIND_HUMAN_INTERVENTION
        ):
            existing = self._sync_decision_store.load()
            if existing is not None:
                self.sync_decision = existing
            return

        existing = self._sync_decision_store.load()
        if (
            existing is not None
            and existing.status == STATUS_PENDING
            and existing.basis == status
        ):
            decision = existing
            # keep RuntimeState.pending aligned
        elif (
            existing is not None
            and existing.status == STATUS_DECIDED
            and existing.basis == status
        ):
            # Already decided for this basis — do not re-open Gate.
            # ABORTED is intentionally NOT included here: abort is a terminal
            # state for that specific decision, not a permanent veto for the
            # same basis. A later detect with the same basis must open a new
            # PENDING decision so the user can choose a fresh direction.
            self.sync_decision = existing
            return
        else:
            if existing is not None and existing.status == STATUS_PENDING:
                # Stale PENDING for a basis that no longer applies — the
                # user never resolved it, but detect() has since moved on.
                # Single-slot store, no history log by design: at minimum
                # log the supersession so it isn't silently lost, and note
                # the old decision_id is now invalid for resolve calls.
                print(
                    f"[sync] superseding unresolved decision "
                    f"{existing.decision_id} (basis={existing.basis}) "
                    f"with new basis={status}; old decision_id now invalid",
                    file=sys.stderr,
                )
            sync_state = None
            if getattr(self, "sync_layer", None) is not None:
                sync_state = getattr(self.sync_layer, "state", None)
            generation = build_sync_decision_generation(report, sync_state)
            decision = SyncDecision.new_pending(basis=status, generation=generation)
            self._sync_decision_store.save(decision)

        self.sync_decision = decision
        detail = getattr(report, "detail", "") or ""
        summary = f"sync_decision required: basis={decision.basis}"
        if detail:
            summary = f"{summary} detail={detail}"
        self.runtime_state.phase = PHASE_AWAITING_USER
        self.runtime_state.pending = Pending(
            kind=PENDING_KIND_SYNC_DECISION,
            summary=summary.strip(),
            payload={
                "decision_id": decision.decision_id,
                "basis": decision.basis,
            },
        )
        self.runtime_state.refresh_recovery()
        self.recovery = self.runtime_state.recovery
        self._runtime_state_store.save(self.runtime_state)

    def resolve_sync_decision(self, direction: str):
        """完成同步策略决议：更新 SyncDecision，清除 RuntimeState.pending。

        direction: disk_to_world | world_to_disk | abort
        不自动执行 forge_sync；决议后 Gate 放行，由调用方再推进同步。
        不重算、不修改 generation（授权边界在 open 时冻结）。

        Persist order (P1-01): apply_direction → save SD terminal → clear RS.pending.
        Never clear the index before the decision body reaches a terminal status.
        """
        from forge.runtime_state import PHASE_IDLE, PENDING_KIND_SYNC_DECISION
        from forge.sync.decision import (
            STATUS_PENDING,
            SyncDecision,
            VALID_DIRECTIONS,
        )

        direction = str(direction or "").strip()
        if direction not in VALID_DIRECTIONS:
            raise ValueError(
                f"direction must be one of {sorted(VALID_DIRECTIONS)}, got {direction!r}"
            )

        decision = self._sync_decision_store.load()
        if decision is None or decision.status != STATUS_PENDING:
            if decision is not None and decision.direction != direction:
                # Already resolved to a *different* direction than what's
                # being requested now — this is not a safe no-op. Silently
                # returning the stale decision here is exactly the bug
                # class that caused abort to be swallowed as world_to_disk.
                raise ValueError(
                    f"SyncDecision {decision.decision_id} already resolved "
                    f"as direction={decision.direction!r} (status="
                    f"{decision.status!r}); refusing to silently return it "
                    f"for a new direction={direction!r} request. If this "
                    "call is intentional, resolve against a fresh "
                    "SyncDecision instead."
                )
            # Idempotent: clear residual sync_decision index only (not HI).
            if (
                self.runtime_state.pending is not None
                and self.runtime_state.pending.kind == PENDING_KIND_SYNC_DECISION
            ):
                self.runtime_state.pending = None
                if self.runtime_state.phase == "AWAITING_USER":
                    self.runtime_state.phase = PHASE_IDLE
                self.runtime_state.refresh_recovery()
                self.recovery = self.runtime_state.recovery
                self._runtime_state_store.save(self.runtime_state)
            self.sync_decision = decision
            return decision

        decision.apply_direction(direction)
        self._sync_decision_store.save(decision)
        self.sync_decision = decision

        self.runtime_state.pending = None
        if self.runtime_state.phase == "AWAITING_USER":
            self.runtime_state.phase = PHASE_IDLE
        self.runtime_state.refresh_recovery()
        self.recovery = self.runtime_state.recovery
        self._runtime_state_store.save(self.runtime_state)
        return decision


    def get_runtime_state(self):
        """控制面只读：返回 RuntimeState 摘要，不写盘、不改 phase/pending。"""
        import json
        from forge.adapters.base import ToolResult as TR

        rs = getattr(self, "runtime_state", None)
        if rs is None:
            return TR.fail(display="get_runtime_state: RuntimeState unavailable")

        # Recovery is already derived at load / after phase+pending changes.

        pending_out = None
        if rs.pending is not None:
            raw_payload = rs.pending.payload if isinstance(rs.pending.payload, dict) else {}
            small: dict = {}
            for key in ("basis", "decision_id", "reason"):
                if key not in raw_payload:
                    continue
                val = raw_payload[key]
                if val is None:
                    continue
                if isinstance(val, (str, int, float, bool)):
                    small[key] = val if not isinstance(val, str) else val[:200]
                else:
                    small[key] = str(val)[:200]
            next_action = None
            from forge.runtime_state import PENDING_KIND_SYNC_DECISION
            basis_val = small.get("basis")
            if (
                rs.pending.kind == PENDING_KIND_SYNC_DECISION
                and isinstance(basis_val, str)
                and basis_val.startswith("FAST_FORWARD")
            ):
                next_action = {
                    "tool": "resolve_sync_decision",
                    "reason": "basis 唯一，无需侦查",
                }
            pending_out = {
                "kind": rs.pending.kind,
                "summary": rs.pending.summary or "",
                "payload": small,
                "next_action": next_action,
            }

        rec = getattr(rs, "recovery", None)
        recovery_out = {
            "mode": getattr(rec, "mode", None) or "none",
            "reason": getattr(rec, "reason", None),
        }
        data = {
            "phase": rs.phase,
            "active_subtask_id": rs.active_subtask_id,
            "pending": pending_out,
            "recovery": recovery_out,
        }
        display = json.dumps(data, ensure_ascii=False, indent=2)
        return TR.ok(
            display=display,
            payload={"mutation": False, **data},
        )

    def request_human_intervention(
        self,
        reason: str = "",
        options_context: str | None = None,
        proposed_next: str | None = None,
    ):
        """Open durable human_intervention pending and end the current AI turn.

        Preconditions (HUMAN_INTERVENTION_CONTRACT):
          - phase == IDLE
          - no durable pending
          - active_subtask_id is None
          - _pending_action is None
        """
        import time
        from forge.adapters.base import ToolResult as TR
        from forge.runtime_state import (
            PHASE_AWAITING_USER,
            PHASE_IDLE,
            PENDING_KIND_HUMAN_INTERVENTION,
            PENDING_KIND_SYNC_DECISION,
            Pending,
        )

        reason = str(reason or "").strip()
        if not reason:
            return TR.fail(display="request_human_intervention: reason is required")

        if getattr(self, "_pending_action", None) is not None:
            return TR.fail(
                display=(
                    "request_human_intervention: refused — in-process PendingAction "
                    "exists; finish write confirmation first."
                )
            )

        rs = getattr(self, "runtime_state", None)
        store = getattr(self, "_runtime_state_store", None)
        if rs is None or store is None:
            return TR.fail(display="request_human_intervention: RuntimeState unavailable")

        if rs.phase != PHASE_IDLE:
            return TR.fail(
                display=(
                    f"request_human_intervention: refused — phase is {rs.phase!r}, "
                    "must be IDLE"
                )
            )
        if rs.active_subtask_id is not None:
            return TR.fail(
                display=(
                    "request_human_intervention: refused — active_subtask_id is set; "
                    "finish or abort the subtask first."
                )
            )
        if rs.pending is not None:
            return TR.fail(
                display=(
                    f"request_human_intervention: refused — durable pending already "
                    f"open (kind={rs.pending.kind})"
                )
            )

        opts = str(options_context or "").strip() or None
        prop = str(proposed_next or "").strip() or None
        payload = {
            "reason": reason,
            "requested_at": time.time(),
        }
        if opts:
            payload["options_context"] = opts
        if prop:
            payload["proposed_next"] = prop

        # Phase 2: task/evidence anchors (no API expansion — payload only)
        original_goal, source_subtask_ids, evidence_digest = (
            self._human_intervention_task_anchors()
        )
        if original_goal:
            payload["original_goal"] = original_goal
        if source_subtask_ids:
            payload["source_subtask_ids"] = list(source_subtask_ids)
        if evidence_digest:
            payload["evidence_digest"] = evidence_digest

        summary = f"human_intervention: {reason}"
        if len(summary) > 200:
            summary = summary[:197] + "..."

        rs.phase = PHASE_AWAITING_USER
        rs.pending = Pending(
            kind=PENDING_KIND_HUMAN_INTERVENTION,
            summary=summary,
            payload=payload,
        )
        rs.refresh_recovery()
        self.recovery = rs.recovery
        try:
            store.save(rs)
        except Exception as e:
            # roll back in-memory on save failure — no false success
            rs.phase = PHASE_IDLE
            rs.pending = None
            rs.refresh_recovery()
            self.recovery = rs.recovery
            return TR.fail(
                display=f"request_human_intervention: persist failed: {e}"
            )

        display = self._format_human_intervention_prompt(payload)
        return TR.ok(
            display=display,
            payload={
                "human_intervention": True,
                "turn_boundary": True,
                "pending": rs.pending.to_dict(),
            },
        )

    def resolve_human_intervention(
        self,
        decision: str = "",
        user_note: str | None = None,
    ):
        """Resolve durable human_intervention pending."""
        from forge.adapters.base import ToolResult as TR
        from forge.runtime_state import (
            PHASE_ABORTED,
            PHASE_IDLE,
            PENDING_KIND_HUMAN_INTERVENTION,
        )

        decision = str(decision or "").strip().lower()
        if decision not in ("continue", "modify", "abort"):
            return TR.fail(
                display=(
                    "resolve_human_intervention: decision must be "
                    "continue | modify | abort"
                )
            )
        note = str(user_note or "").strip()
        if decision == "modify" and not note:
            return TR.fail(
                display=(
                    "resolve_human_intervention: user_note is required and "
                    "must be non-empty when decision=modify"
                )
            )

        rs = getattr(self, "runtime_state", None)
        store = getattr(self, "_runtime_state_store", None)
        if rs is None or store is None:
            return TR.fail(display="resolve_human_intervention: RuntimeState unavailable")
        if rs.pending is None or rs.pending.kind != PENDING_KIND_HUMAN_INTERVENTION:
            return TR.fail(
                display="resolve_human_intervention: no human_intervention pending"
            )

        if decision in ("continue", "modify"):
            rs.phase = PHASE_IDLE
        else:
            rs.phase = PHASE_ABORTED
        rs.pending = None
        rs.refresh_recovery()
        self.recovery = rs.recovery
        try:
            store.save(rs)
        except Exception as e:
            return TR.fail(
                display=f"resolve_human_intervention: persist failed: {e}"
            )

        return TR.ok(
            display=(
                f"human_intervention resolved: decision={decision}"
                + (f" user_note={note!r}" if note else "")
            ),
            payload={
                "decision": decision,
                "user_note": note or None,
                "phase": rs.phase,
            },
        )

    @staticmethod
    def _format_human_intervention_prompt(payload: dict) -> str:
        reason = payload.get("reason") or ""
        opts = payload.get("options_context")
        prop = payload.get("proposed_next")
        lines = [
            "── Needs human decision (main AI escalation) ──",
            f"reason: {reason}",
        ]
        if opts:
            lines.append(f"options_context: {opts}")
        if prop:
            lines.append(f"proposed_next: {prop}")
        lines.extend(
            [
                "",
                "Reply with exactly one of:",
                "  continue",
                "  modify <new instruction>",
                "  abort",
            ]
        )
        return "\n".join(lines)

    def _handle_human_intervention_reply(self, reply: str) -> str:
        """Machine-parse user arbitration for human_intervention (no AI)."""
        from forge.runtime_state import PENDING_KIND_HUMAN_INTERVENTION

        text = (reply or "").strip()
        lower = text.lower()
        decision = None
        note = ""

        if lower == "continue":
            decision = "continue"
        elif lower == "abort" or lower.startswith("abort ") or lower.startswith("abort\n"):
            decision = "abort"
            note = text[5:].strip() if len(text) > 5 else ""
        elif lower.startswith("modify"):
            decision = "modify"
            # strip leading 'modify' and optional separator
            rest = text[6:].lstrip(" \t:：-")
            note = rest.strip()
            if not note:
                rs = self.runtime_state
                payload = (rs.pending.payload if rs.pending else {}) or {}
                return (
                    self._format_human_intervention_prompt(payload)
                    + "\n\n(modify requires a non-empty instruction; try again)"
                )

        if decision is None:
            rs = self.runtime_state
            payload = (rs.pending.payload if rs.pending else {}) or {}
            return (
                self._format_human_intervention_prompt(payload)
                + "\n\n(unrecognized input; reply continue / modify <instruction> / abort)"
            )

        # Snapshot payload before resolve clears pending (Phase 2 anchors).
        rs_pending = getattr(self, "runtime_state", None)
        snap = {}
        if (
            rs_pending is not None
            and rs_pending.pending is not None
            and rs_pending.pending.kind == PENDING_KIND_HUMAN_INTERVENTION
        ):
            snap = dict(rs_pending.pending.payload or {})

        result = self.resolve_human_intervention(decision=decision, user_note=note or None)
        if not result.success:
            return result.display or "resolve failed"

        if decision == "abort":
            return (
                (result.display or "")
                + "\n当前任务已终止（phase=ABORTED）。下一条新任务输入将进入 IDLE。"
                "\n不要再 spawn_subagent 或继续原任务执行。"
            )

        original_goal = str(snap.get("original_goal") or "").strip()
        proposed_next = str(snap.get("proposed_next") or "").strip()
        evidence_digest = str(snap.get("evidence_digest") or "").strip()
        source_ids = snap.get("source_subtask_ids") or []
        if not isinstance(source_ids, list):
            source_ids = []
        source_ids = [str(x).strip() for x in source_ids if str(x).strip()]

        # Restore WorkingSet.goal to original task anchor (no goal drift).
        if original_goal:
            try:
                root = self.workspace.project_root
                ws = WorkingSet.from_dict(_load_task_state(root))
                ws.goal = original_goal[:800]
                _save_task_state(root, ws)
                self._working_set = ws
            except Exception as e:
                print(f"[forge] restore original_goal failed: {e}", file=sys.stderr)

        if decision == "continue":
            follow = self._build_human_continue_followup(
                original_goal=original_goal,
                proposed_next=proposed_next,
                evidence_digest=evidence_digest,
                source_subtask_ids=source_ids,
                options_context=str(snap.get("options_context") or ""),
            )
            return self._run_conversation(
                follow, working_set_goal=original_goal or None
            )

        # modify: replan under original_goal + user_note; do not reuse old path auth
        follow = self._build_human_modify_followup(
            original_goal=original_goal,
            user_note=note,
            evidence_digest=evidence_digest,
            source_subtask_ids=source_ids,
        )
        # Goal stays original; user_note is constraint for replan, not a replacement goal.
        return self._run_conversation(
            follow, working_set_goal=original_goal or None
        )

    def _human_intervention_task_anchors(
        self,
    ) -> tuple[str, list[str], str]:
        """Derive original_goal + source subtask ids + evidence digest for HI payload.

        Sources (best-effort, no second state machine):
          - WorkingSet.goal / task_state.json
          - in-memory _subagent_results with status need_decision|blocked
        """
        original_goal = ""
        ws = getattr(self, "_working_set", None)
        if ws is not None and str(getattr(ws, "goal", "") or "").strip():
            original_goal = str(ws.goal).strip()[:800]
        if not original_goal:
            try:
                root = getattr(getattr(self, "workspace", None), "project_root", None)
                if root:
                    data = _load_task_state(root)
                    if isinstance(data, dict) and str(data.get("goal") or "").strip():
                        original_goal = str(data.get("goal")).strip()[:800]
            except Exception:
                pass

        source_ids: list[str] = []
        digest_parts: list[str] = []
        results = getattr(self, "_subagent_results", None) or {}
        # Prefer decision-bearing statuses; fall back to any recent results.
        ranked: list[tuple[str, dict]] = []
        for sid, ar in results.items():
            if not isinstance(ar, dict):
                continue
            st = str(ar.get("status") or "").strip()
            ranked.append((str(sid), ar))
        # need_decision / blocked first
        def _rank(item: tuple[str, dict]) -> int:
            st = str(item[1].get("status") or "")
            if st == "need_decision":
                return 0
            if st == "blocked":
                return 1
            return 2

        ranked.sort(key=_rank)
        for sid, ar in ranked[:5]:
            st = str(ar.get("status") or "")
            if st in ("need_decision", "blocked"):
                source_ids.append(sid)
            # evidence anchors
            ev_ids: list[str] = []
            for ev in ar.get("evidence") or []:
                if isinstance(ev, dict):
                    tc = str(ev.get("tool_call_id") or "").strip()
                    if tc:
                        ev_ids.append(tc)
            reason = str(ar.get("status_reason") or ar.get("conclusion") or "")[:160]
            line = f"subtask_id={sid} status={st}"
            if ev_ids:
                line += " evidence_tool_call_ids=" + ",".join(ev_ids[:8])
            if reason:
                line += f" reason={reason}"
            digest_parts.append(line)

        # de-dupe ids preserving order
        seen: set[str] = set()
        uniq_ids: list[str] = []
        for s in source_ids:
            if s not in seen:
                seen.add(s)
                uniq_ids.append(s)

        return original_goal, uniq_ids, "\n".join(digest_parts)

    @staticmethod
    def _build_human_continue_followup(
        *,
        original_goal: str,
        proposed_next: str,
        evidence_digest: str,
        source_subtask_ids: list[str],
        options_context: str,
    ) -> str:
        lines = [
            "（用户裁决：continue）在原任务上继续；不要恢复被 "
            "request_human_intervention 中断的旧工具回合。",
            "必须重新规划并在需要时 spawn_subagent；执行后用 verify_subtask_evidence 验收。",
        ]
        if original_goal:
            lines.append(f"original_goal: {original_goal}")
        if proposed_next:
            lines.append(
                f"proposed_next（用户接受主 AI 建议时优先按此路径）: {proposed_next}"
            )
        if source_subtask_ids:
            lines.append(
                "source_subtask_ids: " + ", ".join(source_subtask_ids)
            )
        if evidence_digest:
            lines.append("verified/observed evidence anchors:")
            lines.append(evidence_digest)
        if options_context:
            lines.append(f"options_context: {options_context}")
        lines.append(
            "不得把无 Evidence/tool_call_id 支撑的路径当作已授权执行方向。"
        )
        return "\n".join(lines)

    @staticmethod
    def _build_human_modify_followup(
        *,
        original_goal: str,
        user_note: str,
        evidence_digest: str,
        source_subtask_ids: list[str],
    ) -> str:
        lines = [
            "（用户裁决：modify）按用户新指示重新规划并执行。",
            "旧路径授权作废；必须重新构造 AgentTask 并 spawn_subagent，"
            "不得静默沿用中断前未获用户确认的路径结论。",
            f"user_note: {user_note}",
        ]
        if original_goal:
            lines.append(f"original_goal: {original_goal}")
        if source_subtask_ids:
            lines.append(
                "prior_source_subtask_ids（仅作证据参考，非继续授权）: "
                + ", ".join(source_subtask_ids)
            )
        if evidence_digest:
            lines.append("prior evidence anchors (reference only):")
            lines.append(evidence_digest)
        lines.append("执行后用 verify_subtask_evidence 做机器验收。")
        return "\n".join(lines)

    def _guard_human_intervention_pending(self, tool_name: str):
        """Gate: human_intervention pending blocks progression tools."""
        from forge.adapters.base import ToolResult
        from forge.runtime_state import human_intervention_pending_blocks
        from forge.tools.schemas import MUTATION_TOOL_NAMES, RECONCILIATION_TOOL_NAMES

        name = (tool_name or "").strip()
        if name in ("resolve_human_intervention", "todo_list"):
            return None

        ws = getattr(self, "workspace", None)
        if ws is None or not getattr(ws, "project_root", None):
            return None
        try:
            blocked, summary = human_intervention_pending_blocks(ws.project_root)
        except Exception:
            return None
        if not blocked:
            return None

        blocked_names = set(MUTATION_TOOL_NAMES) | set(RECONCILIATION_TOOL_NAMES) | {
            "forge_sync",
            "spawn_subagent",
            "resume_subtask",
            "todo_write",
        }
        if name not in blocked_names:
            return None
        return ToolResult.fail(
            display=(
                "⛔ human_intervention pending：人类升级尚未裁决，已拒绝任务推进。\n"
                f"pending: {summary}\n"
                "请用户直接输入 continue / modify <instruction> / abort。"
            )
        )

    def _guard_sync_decision_pending(self, tool_name: str):
        """R2 Gate: pending.kind==sync_decision 时拒绝写类工具与 forge_sync。"""
        from forge.adapters.base import ToolResult
        from forge.runtime_state import sync_decision_pending_blocks
        from forge.tools.schemas import MUTATION_TOOL_NAMES, RECONCILIATION_TOOL_NAMES

        ws = getattr(self, "workspace", None)
        if ws is None or not getattr(ws, "project_root", None):
            return None
        try:
            blocked, summary = sync_decision_pending_blocks(ws.project_root)
        except Exception:
            return None
        if not blocked:
            return None
        # 只拦写/对账推进；只读工具放行
        if tool_name not in MUTATION_TOOL_NAMES and tool_name not in RECONCILIATION_TOOL_NAMES:
            if tool_name != "forge_sync":
                return None
        return ToolResult.fail(
            display=(
                "⛔ SyncDecision pending：同步策略点尚未决议，已拒绝写入与 forge_sync 推进。\n"
                f"pending: {summary}\n"
                "请先 resolve_sync_decision(direction=disk_to_world|world_to_disk|abort)。"
            )
        )


    def _guard_external_change(self, tool_name: str):
        """运行期间外部变更守卫：变更工具执行前检测外部磁盘/Git 变化。

        契约 §7：持锁写入期间发现外部磁盘变化 → 立即停止继续写入，重新对账。
        World 不可达时：文件内容 mutation 走 P2-1 direct_disk 直写路径（放行，
        由工具自己标注 mode=direct_disk）；World object 操作没有磁盘等价物，
        继续 STOP，不得伪装成 direct_disk。
        """
        # Human intervention pending blocks progression (before sync / external)
        blocked = self._guard_human_intervention_pending(tool_name)
        if blocked is not None:
            return blocked
        # R2: SyncDecision pending 优先于外部变更守卫
        blocked = self._guard_sync_decision_pending(tool_name)
        if blocked is not None:
            return blocked
        if tool_name not in MUTATION_TOOL_NAMES:
            return None
        # forge_sync 是对账入口本身，不得被外部变更守卫拦截（否则无法解决冲突）。
        if tool_name == "forge_sync":
            return None
        # Mastodon 走环境变量 + HTTP，不依赖 World，也不写磁盘；豁免 World 可达性检查。
        if tool_name in _MASTODON_TOOLS:
            return None
        try:
            if self.sync_layer is not None:
                if not self.sync_layer.world_available():
                    from forge.adapters.base import ToolResult
                    if tool_name in DIRECT_DISK_TOOLS:
                        # P2-1: 放行到 direct_disk，但磁盘侧外部变更 guard 不放弃。
                        if self.sync_layer.disk_change_detected():
                            return ToolResult.fail(
                                display=(
                                    "⛔ veritasd 不可达，且检测到外部磁盘/Git 变化：已停止继续写入。\n"
                                    "direct_disk 直写不解决磁盘分叉；请先运行 forge_sync 重新对账。"
                                )
                            )
                        return None
                    return ToolResult.fail(
                        display=(
                            "⛔ 无法访问 World（veritasd）：已停止继续写入。\n"
                            "Forge 写盘会产生 World 无法记录的变化，禁止在不可达时继续。\n"
                            f"该操作只存在于 World，没有磁盘等价物（可用: "
                            f"{', '.join(sorted(DIRECT_DISK_TOOLS))}）。\n"
                            "请先恢复 veritasd 后重试。"
                        )
                    )
                if self.sync_layer.external_change_detected():
                    from forge.adapters.base import ToolResult
                    return ToolResult.fail(
                        display=(
                            "⛔ 检测到外部磁盘/Git 变化：已停止继续写入。\n"
                            "请先运行 forge_sync 重新对账，确认同步状态后再继续编辑。"
                        )
                    )
        except Exception as e:
            print(f"[sync] external-change guard failed: {e}", file=sys.stderr)
        return None

    def _guard_path_map_degraded(self, tool_name: str):
        """Intercept file mutations when path_map is degraded.

        path_map 不可信时禁止文件内容 mutation，要求先恢复/重建映射。
        forge_sync / undo_last_tx 放行以便对账与修复。
        """
        if tool_name not in _VERIFY_GUARDED_MUTATIONS:
            return None
        world = getattr(self, "world", None) or getattr(
            getattr(self, "executor", None), "_world", None
        )
        if world is None:
            return None
        is_deg = False
        if hasattr(world, "is_degraded"):
            is_deg = bool(world.is_degraded("path_map"))
        elif getattr(world, "_path_map_degraded", False):
            is_deg = True
        elif "path_map" in getattr(world, "degraded_components", ()):
            is_deg = True
        if not is_deg:
            return None
        return ToolResult.fail(
            display=(
                "⛔ path_map 处于 DEGRADED 状态：路径↔对象映射不可信，禁止文件 mutation。\n"
                "请先恢复 veritasd / 重建 path_map（重启 WorldRuntime 或 forge_sync 对账），"
                "确认 is_degraded(\"path_map\") 为 False 后再编辑。"
            ),
            payload={"degraded": ["path_map"], "blocked_by": "path_map_degraded"},
        )

    def _guard_pending_verify(self, tool_name: str, arguments: dict | None):
        """P1-6 最小硬拦截：待验证状态下阻止无关文件编辑。

        触发条件：WorkingSet.verify_targets 非空（存在 VERIFY_REQUIRED 待验证 target）。
        放行：read/diagnostic/test 工具、undo_last_tx、forge_sync、以及编辑
        verify_map 中仍在验证的文件（修复失败测试）。验证通过(verify_targets 清空)后自动恢复。

        不做权限系统/状态机：只对文件内容 mutation 做单一拦截。
        """
        ws = getattr(self, "_working_set", None)
        if ws is None or not ws.verify_targets:
            return None
        if tool_name not in _VERIFY_GUARDED_MUTATIONS:
            return None
        pending_paths = {_norm_path(p) for p in ws.verify_map.keys()}
        targets = _mutation_target_paths(tool_name, arguments)
        if not targets:
            # 无法识别目标文件的 mutation → 视为无关，保守拦截
            return ToolResult.fail(
                display=(
                    "⛔ 有待验证的改动尚未通过验证，禁止开始无关的新编辑。\n"
                    "请先 run_test_structured(target=...) 验证；验证通过后自动恢复。"
                )
            )
        unrelated = [t for t in targets if _norm_path(t) not in pending_paths]
        if unrelated:
            required = ", ".join(sorted(str(v) for v in ws.verify_targets))
            return ToolResult.fail(
                display=(
                    f"⛔ 有待验证的改动尚未通过验证，禁止编辑无关文件: {', '.join(unrelated)}。\n"
                    f"待验证 target: {required or '(见 pending_verify)'}\n"
                    "请先 run_test_structured(target=...) 验证；若需修复当前改动，"
                    "编辑 pending 中的文件仍被允许；验证通过后自动恢复。"
                )
            )
        return None

    def on(self, event_type: EventType, handler):
        self._handlers[event_type].append(handler)

    def emit(self, event: Event) -> Event:
        for handler in self._handlers.get(event.type, []):
            handler(event)
            if event.cancelled:
                break
        return event


    def request_stop(self) -> None:
        """Request cooperative soft-stop of the current turn (Ctrl+C during run)."""
        self._stop_requested = True

    def stop_requested(self) -> bool:
        return bool(getattr(self, "_stop_requested", False))

    def run(self, task: str, task_id: str | None = None) -> str:
        """Continuous Conversation + Pending Action Gate.

        工具始终可见。写操作需确认时冻结 PendingAction；用户确认后 Runtime
        执行快照（不重问模型），然后继续普通对话。不存在 Execution 整表放行。

        Human intervention: durable pending is machine-arbitrated at this entry
        (continue / modify / abort) without main-AI semantic interpretation.
        """
        from forge.runtime_state import (
            PHASE_ABORTED,
            PHASE_IDLE,
            PENDING_KIND_HUMAN_INTERVENTION,
        )

        self._last_tool_calls = 0
        self._last_assistant_replies = []
        self._last_response_needs_display = False

        # ABORTED → IDLE on new user task input (do not restore old pending/task)
        rs = getattr(self, "runtime_state", None)
        store = getattr(self, "_runtime_state_store", None)
        if rs is not None and rs.phase == PHASE_ABORTED:
            rs.pending = None
            rs.active_subtask_id = None
            rs.phase = PHASE_IDLE
            rs.refresh_recovery()
            self.recovery = rs.recovery
            if store is not None:
                try:
                    store.save(rs)
                except Exception as e:
                    print(f"[forge] ABORTED→IDLE save failed: {e}", file=sys.stderr)

        self._stop_requested = False
        try:
            if self._pending_action is not None:
                result = self._handle_pending_reply(task)
            elif (
                rs is not None
                and rs.pending is not None
                and rs.pending.kind == PENDING_KIND_HUMAN_INTERVENTION
            ):
                result = self._handle_human_intervention_reply(task)
            else:
                result = self._run_conversation(task)
        except KeyboardInterrupt:
            # Soft-stop path: never exit the REPL from inside run().
            self._stop_requested = True
            result = (
                "已停止当前任务（Ctrl+C）。"
                "可继续输入新任务；在 forge> 空闲时再按 Ctrl+C 退出。"
            )

        if self._stop_requested and not (
            isinstance(result, str)
            and result.startswith("已停止当前任务")
        ):
            # Subagent or loop set the flag without bubbling KI
            result = (
                "已停止当前任务（Ctrl+C）。"
                "可继续输入新任务；在 forge> 空闲时再按 Ctrl+C 退出。"
            )

        n = getattr(self, "_last_tool_calls", 0)
        print(f"[stats] tools={n}", file=sys.stderr)
        if result is None:
            return "(no response)"
        return result if isinstance(result, str) else str(result)

    def _handle_pending_reply(self, reply: str) -> str:
        """用户对 PendingAction 的回复：确认→执行快照；取消→放弃；其它→取消 pending 并当新任务。"""
        if is_confirm(reply):
            extra = _strip_confirm_prefix(reply)
            result_text = self._execute_pending_action()
            if extra:
                # 确认时的补充意见：执行完再作为新用户输入继续对话
                follow = self._run_conversation(
                    "（用户确认时的补充）" + extra,
                    extra_system=_CONTINUOUS_INSTRUCTION,
                )
                return (result_text or "") + ("\n" + follow if follow else "")
            return result_text
        if is_cancel(reply):
            self._pending_action = None
            return "已取消，未执行该写操作。"
        # 非确认：放弃冻结动作，把输入当普通任务继续
        self._pending_action = None
        return self._run_conversation(reply, extra_system=_CONTINUOUS_INSTRUCTION)

    def _execute_pending_action(self) -> str:
        """执行已冻结的 PendingAction 快照：先 Guard，再 ToolExecutor，再清空 pending。"""
        from forge.adapters.base import Message as ForgeMessage, ToolCall

        pa = self._pending_action
        if pa is None:
            return "没有待确认的写操作。"
        # 执行参数：去掉仅用于展示/门禁的内部键（如 forge_sync 的 _detect_*）
        raw_args = dict(pa.args or {})
        exec_args = {k: v for k, v in raw_args.items() if not str(k).startswith("_")}
        tc = ToolCall(id=pa.tool_call_id or "pending", name=pa.tool, arguments=exec_args)
        # Guard 与确认正交：已确认也不能绕过
        guard = self._guard_path_map_degraded(tc.name)
        if guard is None:
            guard = self._guard_pending_verify(tc.name, tc.arguments)
        if guard is None:
            guard = self._guard_external_change(tc.name)
        if guard is not None:
            self._pending_action = None
            display = guard.display or "被安全守卫拦截"
            self.conversation.append(
                ForgeMessage(role="assistant", content=f"待确认动作未执行：{display}")
            )
            return display

        result = self.executor.execute(tc)
        self._pending_action = None
        self._last_tool_display = result.display or ""
        self._last_tool_name = tc.name
        self._last_tool_calls = getattr(self, "_last_tool_calls", 0) + 1
        try:
            ws = getattr(self, "_working_set", None)
            if ws is not None:
                ws.update_from_tool(tc.name, tc.arguments, result)
                _save_task_state(self.workspace.project_root, ws)
        except Exception as e:
            print(f"[forge] WorkingSet update after pending failed: {e}", file=sys.stderr)

        llm_tool_content = sanitize_and_redact(result.display or "")
        # 写入对话：assistant 曾请求的 tool_call + tool result
        self.conversation.append(
            ForgeMessage(
                role="assistant",
                content=pa.assistant_content or "",
                tool_calls=[tc],
            )
        )
        self.conversation.append(
            ForgeMessage(
                role="tool",
                content=llm_tool_content,
                tool_call_id=tc.id,
                name=tc.name,
            )
        )
        _append_conversation_log(
            self.workspace.project_root,
            "tool",
            result.display or "",
            name=tc.name,
            success=bool(result.success),
        )
        self.emit(
            Event(
                EventType.TOOL_CALL_END,
                {"name": tc.name, "success": result.success, "display": result.display},
            )
        )
        # 执行后让模型根据结果继续（完整工具表，不再放行整表无确认写）
        cont = self._run_conversation(
            (
                f"[系统] 用户已确认并执行 {tc.name}（tool_call_id={tc.id}）。"
                f"结果：\n{result.display or '(empty)'}\n"
                "请根据结果继续：验证、再读、或提出下一步写操作（写操作仍需再次确认）。"
            ),
            extra_system=_CONTINUOUS_INSTRUCTION,
        )
        header = f"已执行 {tc.name}：\n{result.display or ''}"
        if cont and cont not in (result.display or ""):
            return header + "\n" + cont
        return header

    def _forge_sync_observe_or_pending(
        self,
        tc,
        *,
        resp_content: str | None,
        task: str,
        messages: list,
        assistant_replies: list,
        tool_calls_n: int,
        working_set,
    ):
        """forge_sync 边界：detect 只读观察；仅 FAST_FORWARD 冻结 PendingAction。

        返回:
          ("result", ToolResult) — 无需推进，已可作为 tool 结果继续循环
          ("pending", str) — 需要用户确认推进，调用方应 return 该展示文本
          ("execute", None) — 无 sync_layer 等，走普通 executor
        """
        from forge.adapters.base import ToolResult
        from forge.sync.sync_layer import (
            CONFLICT,
            FAST_FORWARD_DISK_TO_WORLD,
            FAST_FORWARD_WORLD_TO_DISK,
            IN_SYNC,
            NOT_A_GIT_REPO,
            WORLD_UNAVAILABLE,
        )

        if self.sync_layer is None:
            return ("execute", None)

        blocked = self._guard_human_intervention_pending("forge_sync")
        if blocked is not None:
            return ("result", blocked)
        # R2 Gate: SyncDecision pending blocks forge_sync advancement
        blocked = self._guard_sync_decision_pending("forge_sync")
        if blocked is not None:
            return ("result", blocked)

        try:
            report = self.sync_layer.detect()
        except Exception as e:
            return (
                "result",
                ToolResult.fail(display=f"forge_sync detect failed: {e}"),
            )

        # R2: CONFLICT / FAST_FORWARD 打开 SyncDecision
        self._maybe_open_sync_decision(report)

        status = report.status
        display = report.format() if hasattr(report, "format") else str(report)
        payload = {"mutation": False, **(report.to_dict() if hasattr(report, "to_dict") else {"status": status})}

        # 不推进水位 / 不写盘：只读观察或 STOP
        if status in (
            IN_SYNC,
            CONFLICT,
            WORLD_UNAVAILABLE,
            NOT_A_GIT_REPO,
        ):
            if status == CONFLICT:
                display = (
                    display
                    + "\n建议: CONFLICT 时请 resolve_sync_decision("
                    "direction=disk_to_world|world_to_disk|abort)，"
                    "再执行 forge_sync；勿自动覆盖。"
                )
                return ("result", ToolResult.fail(display=display, payload=payload))
            if status == IN_SYNC:
                return ("result", ToolResult.ok(display=display, payload=payload))
            # WORLD_UNAVAILABLE / NOT_A_GIT_REPO：不推进
            return ("result", ToolResult.fail(display=display, payload=payload))

        if status in (FAST_FORWARD_DISK_TO_WORLD, FAST_FORWARD_WORLD_TO_DISK):
            # R2: if SyncDecision just opened (pending), stop — do not mix with Mutation Confirmation
            from forge.runtime_state import PENDING_KIND_SYNC_DECISION

            rs = getattr(self, "runtime_state", None)
            if (
                rs is not None
                and rs.pending is not None
                and rs.pending.kind == PENDING_KIND_SYNC_DECISION
            ):
                display = (
                    display
                    + "\nSyncDecision pending：请先 resolve_sync_decision("
                    "direction=disk_to_world|world_to_disk|abort)，"
                    "再调用 forge_sync 推进。"
                )
                return ("result", ToolResult.fail(display=display, payload=payload))
            summary = (
                f"forge_sync 需要推进同步（{status}）\n"
                f"{display}\n"
                f"确认后将执行安全 fast-forward；CONFLICT 仍会 STOP。"
            )
            # 冻结快照：确认后 executor 调用完整 forge_sync→sync()（内部再 detect）
            self._pending_action = PendingAction(
                tool="forge_sync",
                args={
                    "_detect_status": status,
                    "_detect_summary": display,
                },
                tool_call_id=getattr(tc, "id", None) or "pending",
                summary=summary,
                assistant_content=resp_content,
            )
            self._last_response_needs_display = True
            from forge.adapters.base import Message as ForgeMessage

            self.conversation.append(ForgeMessage(role="user", content=task))
            self.conversation.append(
                ForgeMessage(role="assistant", content=(resp_content or "") + "\n" + summary)
            )
            _append_conversation_log(self.workspace.project_root, "assistant", summary)
            assistant_replies.append(summary)
            self._last_tool_calls = tool_calls_n
            self._last_assistant_replies = assistant_replies
            return ("pending", summary + _ACTION_CONFIRM_PROMPT)

        # 未知状态：保守不自动推进，仅报告
        return (
            "result",
            ToolResult.fail(
                display=display + f"\n未识别的 sync 状态 {status!r}，未推进。",
                payload=payload,
            ),
        )

    def save_session_summary(self) -> None:

        """Persist last assistant replies for next process start."""
        replies = getattr(self, "_last_assistant_replies", None) or []
        # also pull from conversation
        for m in self.conversation.get_messages():
            if getattr(m, "role", None) == "assistant" and getattr(m, "content", None):
                replies.append(m.content)
        _save_session_summary(self.workspace.project_root, replies)

    def _sync_system_hint(self) -> str:
        """首轮 system 注入：把当前同步状态作为一等上下文交给 Agent。

        复用 `sync_status()` / `SyncReport`，不重做同步模型；任何探测失败都
        退回空串（不阻塞正常会话）。
        """
        try:
            report = self.sync_status()
        except Exception as e:
            print(f"[forge] _sync_system_hint failed: {e}", file=sys.stderr)
            return ""
        return _sync_status_system_hint(report)

    def _initial_system(self, extra_system: str = "") -> str:
        """构建首轮 system 消息文本（base 指令 + sync 状态 + 阶段指令 + 摘要 + 记忆）。

        sync 状态只在首轮构建时注入一次；工具循环后续轮次只追加 Working Set /
        todo 提醒，不再重复注入同步状态。
        """
        prior = _load_session_summary(self.workspace.project_root)
        try:
            from forge.tools.project_memory import format_for_prompt
            mem = format_for_prompt(self.workspace.project_root)
        except Exception:
            mem = ""
        sync_hint = self._sync_system_hint()
        reconcile_hint = _direct_disk_reconcile_hint(
            self.workspace.project_root,
            getattr(self, "world_available", False),
        )
        return (
            SYSTEM_INSTRUCTION
            + sync_hint
            + reconcile_hint
            + (extra_system or "")
            + (prior or "")
            + (mem or "")
        )

    def _run_conversation(
        self,
        task: str,
        schemas: list | None = None,
        extra_system: str = "",
        require_plan: bool = False,
        working_set_goal: str | None = None,
    ) -> str:
        """Tool-calling loop；完整工具表默认可见；WRITE_CONFIRM 冻结 PendingAction。"""
        if schemas is None:
            schemas = _default_tool_schemas()
        from forge.adapters.base import Message as ForgeMessage

        system = self._initial_system(extra_system)
        messages = [ForgeMessage(role="system", content=system)]
        history = self.conversation.get_messages()
        if history:
            recent = [m for m in history if m.role != "system"][-20:]
            messages.extend(recent)
        messages.append(ForgeMessage(role="user", content=task))
        _append_conversation_log(self.workspace.project_root, "user", task)
        try:
            from forge.tools.goal_clarify import (
                needs_clarify,
                clarification_message,
                user_looks_like_clarification,
                mark_clarified,
            )
            if user_looks_like_clarification(task):
                mark_clarified()
            elif needs_clarify(task):
                messages.append(ForgeMessage(role="user", content=clarification_message()))
        except Exception as e:
            print(f"[forge] goal_clarify unavailable: {e}", file=sys.stderr)

        tool_calls_n = 0
        assistant_replies: list[str] = []
        half = max(5, MAX_AGENT_STEPS // 2)
        nudged = False
        # P1-1: task-level Working Set；P2-4 从 .forge/task_state.json 恢复上次
        # 未完成任务的上下文（goal 始终以本次 task 为准，其余字段恢复）。
        working_set = WorkingSet.from_dict(
            _load_task_state(self.workspace.project_root)
        )
        # Phase 2: human continue/modify may pin goal to original_goal (no drift).
        _goal_src = (working_set_goal if working_set_goal is not None else task) or ""
        working_set.goal = str(_goal_src).strip()[:800]
        self._working_set = working_set
        # P2-3: 上一轮有成功 mutation → 下一轮补一次 progress checkpoint
        mutation_pending = False
        for step_i in range(MAX_AGENT_STEPS):
            if self.stop_requested():
                self._last_tool_calls = tool_calls_n
                self._last_assistant_replies = assistant_replies
                return (
                    "已停止当前任务（Ctrl+C）。"
                    "可继续输入新任务；在 forge> 空闲时再按 Ctrl+C 退出。"
                )
            messages = _compress_messages(messages, working_set=working_set)
            # P2-3: checkpoint 是瞬时注入，每轮先丢弃上一轮那条再重建
            messages = [m for m in messages if not _is_checkpoint_message(m)]
            # Inject compact Working Set so goal/files/hypotheses stay visible
            ws_text = working_set.summary()
            if ws_text:
                # drop previous Working Set system injection if any
                messages = [
                    m
                    for m in messages
                    if not (
                        getattr(m, "role", None) == "system"
                        and isinstance(getattr(m, "content", None), str)
                        and getattr(m, "content", "").startswith("[Working Set]")
                    )
                ]
                messages.append(ForgeMessage(role="system", content=ws_text))
            if step_i >= half and not nudged:
                nudge = _todo_nudge_from_tools(self.executor.tools)
                if nudge:
                    messages.append(ForgeMessage(role="user", content=nudge))
                    nudged = True
            checkpoint = _checkpoint_for_step(
                working_set, step_i, mutation_pending, MAX_AGENT_STEPS
            )
            if checkpoint:
                messages.append(ForgeMessage(role="system", content=checkpoint))
            mutation_pending = False
            self._assistant_streamed = False  # presentation only

            def _delta(piece: str) -> None:
                if not piece:
                    return
                self._assistant_streamed = True
                cb = getattr(self, "_on_assistant_delta", None)
                if cb is not None:
                    try:
                        cb(piece)
                    except Exception:
                        pass

            try:
                send_stream = getattr(self.adapter, "send_stream", None)
                if callable(send_stream):
                    try:
                        resp = send_stream(messages, schemas, on_text_delta=_delta)
                    except KeyboardInterrupt:
                        raise
                    except Exception:
                        resp = self.adapter.send(messages, schemas)
                        if resp.content and not getattr(self, "_assistant_streamed", False):
                            _delta(resp.content)
                else:
                    resp = self.adapter.send(messages, schemas)
            except KeyboardInterrupt:
                self._stop_requested = True
                self._last_tool_calls = tool_calls_n
                self._last_assistant_replies = assistant_replies
                return (
                    "已停止当前任务（Ctrl+C）。"
                    "可继续输入新任务；在 forge> 空闲时再按 Ctrl+C 退出。"
                )
            done_cb = getattr(self, "_on_assistant_done", None)
            if done_cb is not None:
                try:
                    done_cb()
                except Exception:
                    pass
            if not resp.tool_calls:
                if resp.content:
                    self.conversation.append(ForgeMessage(role="user", content=task))
                    self.conversation.append(ForgeMessage(role="assistant", content=resp.content))
                    _append_conversation_log(
                        self.workspace.project_root, "assistant", resp.content or ""
                    )
                    assistant_replies.append(resp.content)
                self._last_tool_calls = tool_calls_n
                self._last_assistant_replies = assistant_replies
                return resp.content or "(no response)"
            messages.append(ForgeMessage(
                role="assistant",
                content=resp.content,
                tool_calls=resp.tool_calls
            ))
            if resp.content:
                assistant_replies.append(resp.content)

            # HUMAN_INTERVENTION turn boundary: if any tool_call requests escalation,
            # only run that tool, discard the rest of the batch, persist, return.
            _hi_tc = None
            for _cand in resp.tool_calls:
                if getattr(_cand, "name", None) == "request_human_intervention":
                    _hi_tc = _cand
                    break
            if _hi_tc is not None:
                args = getattr(_hi_tc, "arguments", None) or {}
                if not isinstance(args, dict):
                    args = {}
                self.emit(
                    Event(
                        EventType.TOOL_CALL_START,
                        {"name": _hi_tc.name, "args": args},
                    )
                )
                result = self.request_human_intervention(
                    reason=str(args.get("reason") or ""),
                    options_context=args.get("options_context"),
                    proposed_next=args.get("proposed_next"),
                )
                tool_calls_n += 1
                self._last_tool_display = result.display or ""
                self._last_tool_name = _hi_tc.name
                self.emit(
                    Event(
                        EventType.TOOL_CALL_END,
                        {
                            "name": _hi_tc.name,
                            "success": bool(result.success),
                            "display": result.display,
                        },
                    )
                )
                self._last_response_needs_display = True
                self.conversation.append(ForgeMessage(role="user", content=task))
                self.conversation.append(
                    ForgeMessage(
                        role="assistant",
                        content=((resp.content or "") + ("\n" if resp.content else "") + (result.display or "")),
                    )
                )
                _append_conversation_log(
                    self.workspace.project_root,
                    "assistant",
                    result.display or "",
                )
                if result.success:
                    assistant_replies.append(result.display or "")
                    self._last_tool_calls = tool_calls_n
                    self._last_assistant_replies = assistant_replies
                    return result.display or ""
                # failed open: surface error and end turn (still no other tools)
                assistant_replies.append(result.display or "")
                self._last_tool_calls = tool_calls_n
                self._last_assistant_replies = assistant_replies
                return result.display or "request_human_intervention failed"

            for tc in resp.tool_calls:
                if self.stop_requested():
                    self._last_tool_calls = tool_calls_n
                    self._last_assistant_replies = assistant_replies
                    return (
                        "已停止当前任务（Ctrl+C）。"
                        "可继续输入新任务；在 forge> 空闲时再按 Ctrl+C 退出。"
                    )
                denied = _main_tool_policy_denied(tc.name)
                if denied is not None:
                    from forge.adapters.base import ToolResult as _TR
                    result = _TR.fail(
                        display=(
                            f"⛔ main tool policy refused: {denied}\n"
                            "主 AI 仅可使用控制面工具与 MAIN_READ_ONLY；"
                            "工程变更请 spawn_subagent。"
                        )
                    )
                    tool_calls_n += 1
                    self._last_tool_display = result.display or ""
                    self._last_tool_name = tc.name
                    self.emit(
                        Event(
                            EventType.TOOL_CALL_END,
                            {
                                "name": tc.name,
                                "success": False,
                                "display": result.display,
                            },
                        )
                    )
                    llm_tool_content = sanitize_and_redact(result.display or "")
                    messages.append(ForgeMessage(
                        role="tool",
                        content=llm_tool_content,
                        tool_call_id=getattr(tc, "id", None),
                        name=tc.name,
                    ))
                    self.conversation.append(
                        ForgeMessage(
                            role="assistant",
                            content=resp.content or "",
                            tool_calls=[tc],
                        )
                    )
                    self.conversation.append(
                        ForgeMessage(
                            role="tool",
                            content=llm_tool_content,
                            tool_call_id=getattr(tc, "id", None),
                            name=tc.name,
                        )
                    )
                    _append_conversation_log(
                        self.workspace.project_root,
                        "tool",
                        result.display or "",
                        name=tc.name,
                        success=False,
                    )
                    continue
                strategy = _write_strategy(tc.name)
                if strategy == "FORGE_SYNC":
                    kind, payload = self._forge_sync_observe_or_pending(
                        tc,
                        resp_content=resp.content,
                        task=task,
                        messages=messages,
                        assistant_replies=assistant_replies,
                        tool_calls_n=tool_calls_n,
                        working_set=working_set,
                    )
                    if kind == "pending":
                        return payload
                    if kind == "result":
                        result = payload
                        tool_calls_n += 1
                        self._last_tool_display = result.display or ""
                        self._last_tool_name = tc.name
                        try:
                            working_set.update_from_tool(
                                tc.name, getattr(tc, "arguments", None) or {}, result
                            )
                            _save_task_state(self.workspace.project_root, working_set)
                        except Exception as e:
                            print(f"[forge] WorkingSet update failed: {e}", file=sys.stderr)
                        self.emit(
                            Event(
                                EventType.TOOL_CALL_END,
                                {
                                    "name": tc.name,
                                    "success": result.success,
                                    "display": result.display,
                                },
                            )
                        )
                        llm_tool_content = sanitize_and_redact(result.display or "")
                        messages.append(ForgeMessage(
                            role="tool",
                            content=llm_tool_content,
                            tool_call_id=tc.id,
                            name=tc.name,
                        ))
                        _append_conversation_log(
                            self.workspace.project_root,
                            "tool",
                            result.display or "",
                            name=tc.name,
                            success=bool(result.success),
                        )
                        continue
                    # kind == "execute": fall through to normal executor
                if tc.name == SUBMIT_PLAN_TOOL_NAME:
                    # 兼容：可选方案输出，不打开写权限、不建立 phase
                    raw_args = tc.arguments or {}
                    plan = raw_args.get("plan") or (resp.content or "")
                    self._last_response_needs_display = True
                    self.conversation.append(ForgeMessage(role="user", content=task))
                    self.conversation.append(ForgeMessage(role="assistant", content=plan))
                    _append_conversation_log(
                        self.workspace.project_root, "assistant", plan or ""
                    )
                    assistant_replies.append(plan)
                    self._last_tool_calls = tool_calls_n
                    self._last_assistant_replies = assistant_replies
                    return (plan or "(no plan)") + (
                        "\n\n── 以上是可选方案 ──\n"
                        "可直接回复意见继续讨论；需要改文件/发嘟时请调用对应工具，"
                        "Runtime 会在执行前要求确认。"
                    )
                if strategy == "WRITE_CONFIRM":
                    # 冻结精确快照，本轮停止；不执行、不打开其它 mutation 权限
                    summary = _pending_action_summary(tc.name, getattr(tc, "arguments", None) or {})
                    self._pending_action = PendingAction(
                        tool=tc.name,
                        args=dict(getattr(tc, "arguments", None) or {}),
                        tool_call_id=getattr(tc, "id", None) or "pending",
                        summary=summary,
                        assistant_content=resp.content,
                    )
                    self._last_response_needs_display = True
                    self.conversation.append(ForgeMessage(role="user", content=task))
                    self.conversation.append(
                        ForgeMessage(role="assistant", content=(resp.content or "") + "\n" + summary)
                    )
                    _append_conversation_log(
                        self.workspace.project_root, "assistant", summary
                    )
                    assistant_replies.append(summary)
                    self._last_tool_calls = tool_calls_n
                    self._last_assistant_replies = assistant_replies
                    return summary + _ACTION_CONFIRM_PROMPT
                self.emit(
                    Event(EventType.TOOL_CALL_START, {"name": tc.name, "args": tc.arguments})
                )
                guard = self._guard_path_map_degraded(tc.name)
                if guard is None:
                    guard = self._guard_pending_verify(
                        tc.name, getattr(tc, "arguments", None) or {}
                    )
                if guard is None:
                    guard = self._guard_external_change(tc.name)
                result = guard if guard is not None else self.executor.execute(tc)
                tool_calls_n += 1
                self._last_tool_display = result.display or ""
                self._last_tool_name = tc.name
                # Main READ_ONLY: durable ToolCallRecord (actor=main), same ToolResult
                record_id = None
                record_ok = False
                if tc.name in MAIN_AUDITED_TOOL_NAMES and guard is None:
                    try:
                        record_id, record_ok = _record_main_tool_call(
                            self.workspace.project_root,
                            tool_name=tc.name,
                            arguments=getattr(tc, "arguments", None) or {},
                            result=result,
                        )
                    except Exception as e:
                        print(
                            f"[forge] main ToolCallRecord write failed: {e}",
                            file=sys.stderr,
                        )
                        record_id, record_ok = None, False
                    if not record_ok:
                        note = (
                            "\n[durable evidence unavailable: "
                            "ToolCallRecord write failed for this read]"
                        )
                        result.display = (result.display or "") + note
                # P1-1: update Working Set from real tool outcome
                try:
                    working_set.update_from_tool(
                        tc.name, getattr(tc, "arguments", None) or {}, result
                    )
                    # P2-4: 每次工具调用后持久化（best-effort，损坏下次静默重建）
                    _save_task_state(self.workspace.project_root, working_set)
                except Exception as e:
                    print(f"[forge] WorkingSet update failed: {e}", file=sys.stderr)
                # P2-3: 复用文件编辑成功判定（_EDIT_TOOLS + result.success），
                # Working Set 刷新之后再排队 checkpoint；失败的编辑不触发、不入 done。
                # 收窄：forge_sync / undo_last_tx 不是文件编辑，不再触发 checkpoint。
                if tc.name in _EDIT_TOOLS and result.success:
                    mutation_pending = True
                self.emit(
                    Event(
                        EventType.TOOL_CALL_END,
                        {
                            "name": tc.name,
                            "success": result.success,
                            "display": result.display,
                        },
                    )
                )

                # Untrusted tool text → LLM context: redact secrets + mark
                # injection-like phrasing. Soft mitigation only; not a hard boundary.
                display_for_llm = result.display or ""
                if record_id and record_ok:
                    display_for_llm = f"tool_call_id={record_id}\n{display_for_llm}"
                elif record_id and not record_ok:
                    display_for_llm = (
                        f"tool_call_id={record_id} (record_write_failed)\n"
                        + display_for_llm
                    )
                llm_tool_content = sanitize_and_redact(display_for_llm)
                messages.append(ForgeMessage(
                    role="tool",
                    content=llm_tool_content,
                    tool_call_id=tc.id,
                    name=tc.name
                ))
                _append_conversation_log(
                    self.workspace.project_root,
                    "tool",
                    result.display or "",
                    name=tc.name,
                    success=bool(result.success),
                )
        self._last_tool_calls = tool_calls_n
        self._last_assistant_replies = assistant_replies
        return "(达到最大工具调用次数)"

    # Backward-compat alias
    run_v2 = run
