"""PlanValidator — 独立于 Planner 的 Plan 结构校验器"""
import os
from forge.protocols.models import Plan, PlanStep, RepoContext


class PlanValidationError(Exception):
    pass


class PlanValidator:
    """校验 Plan 结构合法性 + old_text 精确匹配"""

    def __init__(self, project_root: str = "."):
        self.project_root = project_root

    def validate(self, plan_dict: dict, repo: RepoContext, repair_constraints=None, obligations=None) -> tuple[Plan, dict]:
        """校验并补充 old_text。返回 (Plan, enriched_dict)"""
        if not isinstance(plan_dict, dict):
            raise PlanValidationError("Plan 必须是 dict")

        goal = plan_dict.get("goal", "")
        if not goal:
            raise PlanValidationError("Plan 缺少 goal")

        assumptions = plan_dict.get("assumptions", [])
        if not isinstance(assumptions, list):
            raise PlanValidationError("assumptions 必须是 list")

        steps_raw = plan_dict.get("steps", [])
        if not steps_raw:
            raise PlanValidationError("Plan 缺少 steps")
        if not isinstance(steps_raw, list):
            raise PlanValidationError("steps 必须是 list")

        existing_files = set(repo.file_tree)
        valid_ops = {"modify", "create_file", "delete_file", "create_object"}
        step_ids = set()
        steps = []
        enriched_steps = []

        for i, s in enumerate(steps_raw):
            sid = s.get("step_id", f"step_{i+1}")
            if sid in step_ids:
                raise PlanValidationError(f"重复的 step_id: {sid}")
            step_ids.add(sid)

            desc = s.get("description", "")
            if not desc:
                raise PlanValidationError(f"{sid}: 缺少 description")

            op = s.get("operation_type", "modify")
            targets = s.get("target_files", [])
            if not isinstance(targets, list):
                raise PlanValidationError(f"{sid}: target_files 必须是 list")
            if op not in valid_ops:
                raise PlanValidationError(f"{sid}: 无效 operation_type '{op}'")

            # Fail-closed: no auto-correction of operation_type or target_files.
            # create_object must have empty target_files; modify/create_file/delete_file must not.
            if op == "create_object":
                if targets:
                    raise PlanValidationError(
                        f"{sid}: create_object 的 target_files 必须为空数组 []，"
                        f"收到 {targets}（Validator 不会自动清空或改写 operation_type）"
                    )
            else:
                # modify / create_file / delete_file require non-empty target_files
                if not targets:
                    raise PlanValidationError(
                        f"{sid}: {op} 缺少 target_files（Validator 不会自动补全或改写 operation_type）"
                    )

            enriched = dict(s)

            for tf in targets:
                if op in ("modify", "delete_file"):
                    if tf not in existing_files:
                        raise PlanValidationError(f"{sid}: {op} 的目标 '{tf}' 不在仓库中")
                elif op == "create_file":
                    if tf in existing_files:
                        raise PlanValidationError(f"{sid}: create_file 的目标 '{tf}' 已存在")

            if op == "create_file":
                if "content" not in s:
                    raise PlanValidationError(f"{sid}: create_file 缺少 content")
                if s.get("content") is None:
                    raise PlanValidationError(f"{sid}: create_file 的 content 不能为 null")
                if not isinstance(s.get("content"), str):
                    raise PlanValidationError(f"{sid}: create_file 的 content 必须是 string")

            elif op == "modify":
                start_line = s.get("start_line")
                end_line = s.get("end_line")
                if start_line is None or end_line is None:
                    raise PlanValidationError(f"{sid}: modify 缺少 start_line 或 end_line")
                if start_line < 1 or end_line < start_line:
                    raise PlanValidationError(f"{sid}: 无效行号范围 {start_line}-{end_line}")

                # new_text is required for modify (key must be present).
                # Empty string is allowed (delete the line range).
                if "new_text" not in s:
                    raise PlanValidationError(
                        f"{sid}: modify 缺少 new_text（替换 start_line..end_line 的新内容；"
                        f"删除整行时可为空字符串）"
                    )
                new_text = s.get("new_text")
                if new_text is None:
                    raise PlanValidationError(f"{sid}: modify 的 new_text 不能为 null")
                if not isinstance(new_text, str):
                    raise PlanValidationError(f"{sid}: modify 的 new_text 必须是 string")

                for tf in targets:
                    filepath = os.path.join(self.project_root, tf)
                    if not os.path.exists(filepath):
                        raise PlanValidationError(f"{sid}: 文件不存在: {tf}")
                    with open(filepath, "r", encoding="utf-8") as f:
                        lines = f.readlines()
                    if end_line > len(lines):
                        raise PlanValidationError(f"{sid}: end_line={end_line} 超出文件行数 {len(lines)}")
                    old_lines = lines[start_line-1:end_line]
                    enriched["old_text"] = "".join(old_lines)

            elif op == "delete_file":
                for tf in targets:
                    filepath = os.path.join(self.project_root, tf)
                    if not os.path.exists(filepath):
                        raise PlanValidationError(f"{sid}: 要删除的文件不存在: {tf}")

            deps = s.get("dependencies", [])
            if not isinstance(deps, list):
                raise PlanValidationError(f"{sid}: dependencies 必须是 list")

            # content is only required for create_file; for modify use new_text.
            # Do not treat absent content as an error or print a misleading MISSING.
            step_content = s.get("content", "") if op == "create_file" else s.get("content", "")
            if step_content is None:
                step_content = ""
            steps.append(PlanStep(
                step_id=sid, description=desc, target_files=targets,
                operation_type=op, dependencies=deps,
                content=step_content if isinstance(step_content, str) else str(step_content),
                old_text=s.get("old_text", enriched.get("old_text", "")) or "",
                new_text=s.get("new_text", "") if s.get("new_text") is not None else "",
                start_line=s.get("start_line"),
                end_line=s.get("end_line"),
                expected_symbols=list(s.get("expected_symbols") or []),
            ))
            enriched_steps.append(enriched)

        for step in steps:
            for dep in step.dependencies:
                if dep not in step_ids:
                    raise PlanValidationError(f"{step.step_id}: 依赖的 {dep} 不存在")

        impact_files = list(plan_dict.get("impact_files") or [])
        impact_symbols = list(plan_dict.get("impact_symbols") or [])
        # Priority 2: when impact_files is declared non-empty, modify/delete
        # targets must be a subset. create_file is exempt (new paths OK).
        if impact_files:
            allowed = set(impact_files)
            for step in steps:
                if step.operation_type in ("modify", "delete_file", "delete"):
                    for tf in step.target_files:
                        if tf not in allowed:
                            raise PlanValidationError(
                                f"{step.step_id}: target '{tf}' outside impact_files "
                                f"boundary {sorted(allowed)}"
                            )

        # Priority 3: machine repair constraints from classified failure
        if repair_constraints is not None:
            from forge.failures import RepairConstraints, FailureClass
            rc = repair_constraints
            if isinstance(rc, dict):
                rc = RepairConstraints.from_dict(rc)
            if not getattr(rc, "allow_mutation", True):
                raise PlanValidationError(
                    f"repair blocked: failure {rc.failure_code} does not allow mutation"
                )
            force_create = set(getattr(rc, "force_create_files", None) or [])
            must_touch = set(getattr(rc, "must_touch_files", None) or [])
            required_impact = set(getattr(rc, "required_impact_files", None) or [])
            forbidden = set(getattr(rc, "forbidden_ops", None) or [])

            for step in steps:
                op = step.operation_type
                if op in forbidden:
                    raise PlanValidationError(
                        f"{step.step_id}: operation '{op}' forbidden by repair constraints"
                    )
                for tf in step.target_files:
                    if tf in force_create and op in ("modify", "delete_file", "delete"):
                        raise PlanValidationError(
                            f"{step.step_id}: missing file '{tf}' requires create_file, not {op}"
                        )
                    if required_impact and op in ("modify", "delete_file", "delete"):
                        if tf not in required_impact:
                            raise PlanValidationError(
                                f"{step.step_id}: target '{tf}' outside repair "
                                f"required_impact_files {sorted(required_impact)}"
                            )

            if must_touch:
                touched = set()
                for step in steps:
                    touched.update(step.target_files)
                if not (touched & must_touch):
                    raise PlanValidationError(
                        f"repair plan must touch at least one of {sorted(must_touch)}; "
                        f"got {sorted(touched)}"
                    )

        # Priority 7: required obligation coverage (machine lower bound)
        if obligations:
            from forge.context.planning import missing_required_obligations
            from forge.protocols.models import Plan as _Plan, PlanStep as _PS
            # temporary plan-like for coverage check
            tmp = _Plan(goal=goal, steps=steps)
            missing = missing_required_obligations(tmp, obligations)
            if missing:
                desc = ", ".join(
                    f"{m.get('role')}:{m.get('symbol')}@{m.get('file')}" for m in missing
                )
                raise PlanValidationError(
                    f"plan missing required obligations: {desc}"
                )

        import time
        plan = Plan(
            plan_id=f"plan_{int(time.time())}",
            goal=goal, steps=steps, assumptions=assumptions,
            impact_files=impact_files,
            impact_symbols=impact_symbols,
        )
        enriched_plan = dict(plan_dict)
        enriched_plan["steps"] = enriched_steps
        enriched_plan["impact_files"] = impact_files
        enriched_plan["impact_symbols"] = impact_symbols
        return plan, enriched_plan
