"""Runtime — session shell for Forge.

Production path (唯一):
  Runtime.run(task) → _run_conversation() 工具循环
    → READ_ONLY + MUTATION schemas
    → ToolExecutor → IntentExecutor → Veritas commit/abort → Projection
"""
from __future__ import annotations

import json
import os
import re
import sys
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
    SUBMIT_PLAN_TOOL_NAME,
    SUBMIT_PLAN_DECLARATION,
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


# 规划/执行阶段注入到 system 的额外指令
_PLANNING_INSTRUCTION = """
## 当前阶段：规划（只读）
你现在只有只读/查询工具，无法修改代码。需要改动时：先只读探索定位，
然后调用 submit_plan 提交计划并停下等待确认。纯问答直接回答即可。
"""
_EXECUTION_INSTRUCTION = """
## 当前阶段：执行
用户已确认以下计划，请按计划执行修改：
{plan}

执行中若发现计划需要偏离或推翻，先停下说明，不要擅自大改。
"""
_PLAN_CONFIRM_PROMPT = (
    "\n\n── 以上是计划 ──\n"
    "回复「确认」开始执行；「取消」放弃；或直接说出你的修改意见。"
)

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


class Runtime:
    def __init__(self, adapter: BaseAdapter, workspace: Workspace, memory: MemoryStore):
        self.adapter = adapter
        self.workspace = workspace
        # Optional CLI presentation hooks (TerminalPresenter); not part of agent semantics.
        self._on_assistant_delta = None
        self._on_assistant_done = None
        self._assistant_streamed = False
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

        # Sync metadata 权威状态（决策 1：.forge/sync_state.json，不放入 Veritas）。
        self.sync_state = SyncState(project_root=workspace.project_root)
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
        def spawn_subagent(task: str, max_steps: int = 15) -> ToolResult:
            """Run an isolated subagent tool-loop; return conclusion text only."""
            from forge.subagent import run_subagent
            from forge.tools.schemas import MUTATION_TOOL_DECLARATIONS

            try:
                schemas = list(READ_ONLY_TOOL_DECLARATIONS) + list(MUTATION_TOOL_DECLARATIONS)
                conclusion = run_subagent(
                    self.adapter,
                    tools,
                    schemas,
                    task,
                    max_steps=int(max_steps) if max_steps else 15,
                )
                return ToolResult.ok(
                    display="RESULT: subagent_done\n" + (conclusion or ""),
                    payload={"conclusion": conclusion, "subagent": True},
                )
            except Exception as e:
                return ToolResult.fail(
                    display=(
                        "spawn_subagent failed: "
                        + str(e)
                        + "\n建议: 缩小子任务范围；确认模型与 veritasd 可用。"
                    )
                )

        tools["spawn_subagent"] = spawn_subagent
        self.executor = ToolExecutor(tools)

        self.conversation = Conversation()
        self.conversation.append(Message(role="system", content=SYSTEM_INSTRUCTION))
        self._handlers: dict = {t: [] for t in EventType}
        # 规划→确认→执行 状态：待用户确认的计划与原任务
        self._pending_plan: str | None = None
        self._pending_task: str | None = None
        self._submitted_plan: str | None = None

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
        """程序化同步状态查询（返回 SyncReport）。"""
        return self.sync_layer.detect()

    def sync(self):
        """显式执行 `forge sync`：检测 → 依状态安全推进 / 报告冲突。"""
        return self.sync_layer.sync()

    def _guard_external_change(self, tool_name: str):
        """运行期间外部变更守卫：变更工具执行前检测外部磁盘/Git 变化。

        契约 §7：持锁写入期间发现外部磁盘变化 → 立即停止继续写入，重新对账。
        World 不可达时：文件内容 mutation 走 P2-1 direct_disk 直写路径（放行，
        由工具自己标注 mode=direct_disk）；World object 操作没有磁盘等价物，
        继续 STOP，不得伪装成 direct_disk。
        """
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
            import sys
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

    def run(self, task: str, task_id: str | None = None) -> str:
        """Single path: 规划(只读) → 用户确认 → 执行(mutation)。

        默认先进入规划阶段：只读工具 + submit_plan。模型要改代码时必须
        先 submit_plan，运行时把计划交还用户确认；确认后下一轮才放行
        mutation 工具执行。纯问答直接返回，不需要确认。
        """
        self._last_tool_calls = 0
        self._last_assistant_replies = []

        # 有待确认的计划：先处理用户对计划的回复
        if self._pending_plan is not None:
            result = self._handle_plan_reply(task)
        else:
            result = self._run_planning(task)

        n = getattr(self, "_last_tool_calls", 0)
        print(f"[stats] tools={n}", file=sys.stderr)
        if result is None:
            return "(no response)"
        return result if isinstance(result, str) else str(result)

    def _handle_plan_reply(self, reply: str) -> str:
        """用户对计划回复：确认→执行；取消→放弃；其它→当作补充意见重新规划。"""
        if is_confirm(reply):
            plan = self._pending_plan
            task = self._pending_task
            self._pending_plan = None
            self._pending_task = None
            extra = _strip_confirm_prefix(reply)
            if extra:
                task = (task or "") + "\n（用户确认时的补充）" + extra
            return self._run_execution(task, plan)
        if is_cancel(reply):
            self._pending_plan = None
            self._pending_task = None
            return "已取消，未做任何改动。"
        # 其余内容：用户对计划有意见/新指令，并入原任务重新规划
        original = self._pending_task or ""
        self._pending_plan = None
        self._pending_task = None
        combined = original + "\n（用户对计划的补充/修正）" + reply
        return self._run_planning(combined)

    def _run_planning(self, task: str) -> str:
        """规划阶段：只读工具 + submit_plan。返回计划（待确认）或直接答案。"""
        self._submitted_plan = None
        result = self._run_conversation(
            task,
            schemas=list(READ_ONLY_TOOL_DECLARATIONS) + [SUBMIT_PLAN_DECLARATION],
            extra_system=_PLANNING_INSTRUCTION,
        )
        if self._submitted_plan:
            self._pending_plan = self._submitted_plan
            self._pending_task = task
            return result + _PLAN_CONFIRM_PROMPT
        return result

    def _run_execution(self, task: str, plan: str) -> str:
        """执行阶段：用户已确认计划，放行 mutation 工具按计划执行。"""
        self._submitted_plan = None
        return self._run_conversation(
            task,
            schemas=list(READ_ONLY_TOOL_DECLARATIONS)
            + list(MUTATION_TOOL_DECLARATIONS),
            extra_system=_EXECUTION_INSTRUCTION.format(plan=plan),
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

    def _run_conversation(self, task: str, schemas: list, extra_system: str = "") -> str:
        """Tool-calling loop；schemas 决定本轮可见工具（规划=只读，执行=只读+mutation）。"""
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
            import sys
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
        working_set.goal = (task or "").strip()[:800]
        self._working_set = working_set
        # P2-3: 上一轮有成功 mutation → 下一轮补一次 progress checkpoint
        mutation_pending = False
        for step_i in range(MAX_AGENT_STEPS):
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

            send_stream = getattr(self.adapter, "send_stream", None)
            if callable(send_stream):
                try:
                    resp = send_stream(messages, schemas, on_text_delta=_delta)
                except Exception:
                    resp = self.adapter.send(messages, schemas)
                    if resp.content and not getattr(self, "_assistant_streamed", False):
                        _delta(resp.content)
            else:
                resp = self.adapter.send(messages, schemas)
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
            for tc in resp.tool_calls:
                if tc.name == SUBMIT_PLAN_TOOL_NAME:
                    # 模型提交计划 → 中断本轮，交还用户确认，不放行 mutation。
                    plan = (tc.arguments or {}).get("plan") or (resp.content or "")
                    self._submitted_plan = plan
                    self.conversation.append(ForgeMessage(role="user", content=task))
                    self.conversation.append(ForgeMessage(role="assistant", content=plan))
                    _append_conversation_log(
                        self.workspace.project_root, "assistant", plan or ""
                    )
                    assistant_replies.append(plan)
                    self._last_tool_calls = tool_calls_n
                    self._last_assistant_replies = assistant_replies
                    return plan or "(no plan)"
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
                llm_tool_content = sanitize_and_redact(result.display or "")
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
