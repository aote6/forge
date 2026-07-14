"""校验器注册表"""
import subprocess
import json


def validate_python(path: str) -> tuple:
    try:
        r = subprocess.run(
            ["python", "-m", "py_compile", path],
            capture_output=True, text=True, timeout=10
        )
        if r.returncode == 0:
            return True, "OK"
        return False, r.stderr.strip()[:500]
    except Exception as e:
        return False, str(e)


def validate_json(path: str) -> tuple:
    try:
        with open(path, "r") as f:
            json.load(f)
        return True, "OK"
    except Exception as e:
        return False, str(e)[:500]


class ValidatorRegistry:
    _validators: dict = {}
    
    @classmethod
    def register(cls, extension: str, validator):
        cls._validators[extension.lower()] = validator
    
    @classmethod
    def validate(cls, path: str) -> tuple:
        import os
        ext = os.path.splitext(path)[1].lower()
        validator = cls._validators.get(ext)
        if validator is None:
            return True, f"无校验器({ext})，跳过"
        return validator(path)


ValidatorRegistry.register(".py", validate_python)
ValidatorRegistry.register(".json", validate_json)
