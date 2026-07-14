"""Patch Engine - 内部使用 EditScript，不解析 unified diff"""
from dataclasses import dataclass
import difflib


@dataclass
class EditOp:
    type: str
    start_line: int
    end_line: int
    new_lines: list


class PatchEngine:
    @staticmethod
    def compute_edits(original: str, modified: str) -> list:
        orig_lines = original.splitlines(keepends=True)
        mod_lines = modified.splitlines(keepends=True)
        matcher = difflib.SequenceMatcher(None, orig_lines, mod_lines)
        edits = []
        for tag, i1, i2, j1, j2 in matcher.get_opcodes():
            if tag == "equal":
                continue
            elif tag == "replace":
                edits.append(EditOp("replace", i1, i2, mod_lines[j1:j2]))
            elif tag == "delete":
                edits.append(EditOp("delete", i1, i2, []))
            elif tag == "insert":
                edits.append(EditOp("insert", i1, i2, mod_lines[j1:j2]))
        return edits
    
    @staticmethod
    def apply_edits(original: str, edits: list) -> str:
        lines = original.splitlines(keepends=True)
        result = []
        pos = 0
        for edit in sorted(edits, key=lambda e: e.start_line):
            result.extend(lines[pos:edit.start_line])
            if edit.type in ("replace", "insert"):
                result.extend(edit.new_lines)
            pos = edit.end_line
        result.extend(lines[pos:])
        return "".join(result)
    
    @staticmethod
    def to_unified_diff(original: str, modified: str, path: str = "file") -> str:
        old_lines = original.splitlines(keepends=True)
        new_lines = modified.splitlines(keepends=True)
        d = difflib.unified_diff(
            old_lines, new_lines,
            fromfile=f"a/{path}", tofile=f"b/{path}"
        )
        return "".join(d)
    
    @staticmethod
    def diff(original: str, modified: str, path: str = "file") -> str:
        return PatchEngine.to_unified_diff(original, modified, path)
