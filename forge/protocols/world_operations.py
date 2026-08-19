"""World operation SSOT — single source of truth for all World (non-file) operations.

Any new World operation (e.g. unlink_objects, write, freeze) is added HERE only.
Other modules import from this file:
  - operation_contract.py: canonical set
  - plan_validator.py: target_files rules + parameter validation
  - constitution.py: runtime-only classification
  - context/planning.py: is_runtime_only_plan
"""

WORLD_OPERATIONS: dict[str, dict] = {
    "create_object": {
        "target_files": "empty",       # must be []
        "runtime_only": True,          # no source content required
        "params": {},                  # no extra required params
    },
    "link_objects": {
        "target_files": "empty",
        "runtime_only": True,
        "params": {
            "from_id": {"type": "int", "required": True},
            "to_id": {"type": "int", "required": True},
            "link_type": {
                "type": "enum",
                "required": True,
                "allowed": ["owns", "depends_on", "references"],
            },
        },
    },
}


def is_world_operation(op_type: str) -> bool:
    return op_type in WORLD_OPERATIONS


def world_operation_names() -> set[str]:
    return set(WORLD_OPERATIONS.keys())


def validate_world_operation_params(op_type: str, step: dict) -> list[str]:
    """Return list of validation errors. Empty list = valid."""
    spec = WORLD_OPERATIONS.get(op_type)
    if spec is None:
        return [f"unknown world operation: {op_type}"]
    errors = []
    for name, pspec in spec.get("params", {}).items():
        if pspec.get("required") and name not in step:
            errors.append(f"{op_type} 缺少 {name}")
            continue
        if name not in step:
            continue
        value = step[name]
        if pspec["type"] == "int" and not isinstance(value, int):
            errors.append(f"{op_type} 的 {name} 必须是 int，收到 {type(value).__name__}")
        elif pspec["type"] == "enum" and value not in pspec.get("allowed", []):
            errors.append(f"{op_type} 的 {name} 无效: {value!r}，必须是 {pspec.get('allowed')}")
    # link_objects: no self-loop
    if op_type == "link_objects":
        from_id = step.get("from_id")
        to_id = step.get("to_id")
        if from_id is not None and to_id is not None and from_id == to_id:
            errors.append(f"link_objects 不允许自环（from_id == to_id == {from_id}）")
    return errors
