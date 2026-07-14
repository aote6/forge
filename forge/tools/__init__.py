"""工具函数"""
from forge.adapters.base import ToolResult


def make_tools(workspace) -> dict:
    
    def read_file(path: str, start: int = 1, end: int = 0) -> ToolResult:
        try:
            content = workspace.read_file(path, start, end)
            return ToolResult.ok(display=content, payload={"path": path})
        except FileNotFoundError:
            return ToolResult.fail(display=f"文件不存在: {path}")
        except Exception as e:
            return ToolResult.fail(display=f"读取失败: {e}")
    
    def prepare_write(path: str, operations: list) -> ToolResult:
        ok, msg, tx = workspace.prepare_write(path, operations)
        if not ok:
            return ToolResult.fail(display=msg)
        display = (
            f"⏸️ 事务 {tx.id} 已准备，待确认。\n"
            f"📄 文件: {path}\n"
            f"📊 Diff:\n{tx.patch}\n"
            f"---\n💡 '确认 {tx.id}' 提交 | '取消 {tx.id}' 放弃"
        )
        return ToolResult.ok(display=display, payload={
            "transaction_id": tx.id, "path": path, "patch": tx.patch,
            "operations": tx.operations
        })
    
    def commit_write(transaction_id: str) -> ToolResult:
        ok, msg = workspace.commit_write(transaction_id)
        return ToolResult.ok(display=msg) if ok else ToolResult.fail(display=msg)
    
    def search_code(pattern: str, path: str = ".") -> ToolResult:
        result = workspace.search_code(pattern, path)
        return ToolResult.ok(display=result, payload={"pattern": pattern})
    
    return {
        "read_file": read_file,
        "prepare_write": prepare_write,
        "commit_write": commit_write,
        "search_code": search_code,
    }
