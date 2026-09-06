import json
from pathlib import Path

from forge.tools.search_tools import make_search_tools
from forge.workspace import Workspace


def test_list_recent_subtasks_returns_last_n_reversed(tmp_path: Path):
    d = tmp_path / ".forge"
    d.mkdir()
    p = d / "subagent_results.jsonl"
    records = [
        {"subtask_id": f"sub_{i}", "status": "done", "status_reason": "ok",
         "conclusion": f"c{i}"}
        for i in range(7)
    ]
    p.write_text("\n".join(json.dumps(r) for r in records), encoding="utf-8")

    ws = Workspace(project_root=str(tmp_path))
    tools = make_search_tools(ws)
    result = tools["list_recent_subtasks"](n=3)

    assert "sub_6" in result.display
    assert "sub_4" in result.display
    assert "sub_3" not in result.display
    assert result.display.index("sub_6") < result.display.index("sub_4")
