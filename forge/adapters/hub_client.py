"""Hub capability runtime client.

Forge adapters call Hub; Hub discovers nodes. Forge must not hardcode
node filesystem paths or subprocess node binaries directly.
"""
from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass, field
from typing import Any, Dict, Optional


@dataclass
class HubConfig:
    """Loaded from env / .forge/hub.json — never hardcode Termux paths."""
    hub_bin: str = ""
    timeout_sec: int = 60
    nodes: Dict[str, str] = field(default_factory=dict)  # capability -> node id
    env: Dict[str, str] = field(default_factory=dict)

    @classmethod
    def load(cls, project_root: str = ".") -> "HubConfig":
        cfg = cls()
        cfg.hub_bin = os.environ.get("FORGE_HUB_BIN", "hub")
        cfg.timeout_sec = int(os.environ.get("FORGE_HUB_TIMEOUT", "60"))
        # Optional explicit node map via env JSON
        raw = os.environ.get("FORGE_HUB_NODES")
        if raw:
            try:
                cfg.nodes = json.loads(raw)
            except json.JSONDecodeError:
                pass
        path = os.path.join(project_root, ".forge", "hub.json")
        if os.path.isfile(path):
            try:
                with open(path, encoding="utf-8") as f:
                    data = json.load(f)
                cfg.hub_bin = data.get("hub_bin", cfg.hub_bin)
                cfg.timeout_sec = int(data.get("timeout_sec", cfg.timeout_sec))
                cfg.nodes.update(data.get("nodes") or {})
                cfg.env.update(data.get("env") or {})
            except (OSError, json.JSONDecodeError, ValueError):
                pass
        return cfg


@dataclass
class HubResponse:
    ok: bool
    data: Dict[str, Any] = field(default_factory=dict)
    error: str = ""
    exit_code: int = 0


class HubClient:
    """Thin runtime: invoke hub for a capability node."""

    def __init__(self, config: Optional[HubConfig] = None, project_root: str = "."):
        self.config = config or HubConfig.load(project_root)
        self.project_root = project_root

    def invoke(
        self,
        capability: str,
        action: str,
        payload: Optional[Dict[str, Any]] = None,
        timeout: Optional[int] = None,
    ) -> HubResponse:
        """Call: hub run <node> <action> --json <payload>

        On hub missing: returns structured error (no silent success).
        """
        node = self.config.nodes.get(capability, capability)
        timeout = timeout if timeout is not None else self.config.timeout_sec
        payload = payload or {}
        base_cmd = self.config.hub_bin.split()
        cmd = base_cmd + [
            "run",
            node,
            action,
            "--json",
            json.dumps(payload, ensure_ascii=False),
        ]
        env = os.environ.copy()
        env.update(self.config.env)
        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout,
                cwd=self.project_root,
                env=env,
            )
        except FileNotFoundError:
            return HubResponse(
                ok=False,
                error=f"hub binary not found: {self.config.hub_bin}",
                exit_code=127,
            )
        except subprocess.TimeoutExpired:
            return HubResponse(ok=False, error=f"hub timeout after {timeout}s", exit_code=124)
        except OSError as e:
            return HubResponse(ok=False, error=str(e), exit_code=1)

        data: Dict[str, Any] = {}
        stdout = (proc.stdout or "").strip()
        if stdout:
            try:
                data = json.loads(stdout)
            except json.JSONDecodeError:
                data = {"raw": stdout}

        if proc.returncode != 0:
            err = (proc.stderr or "").strip() or data.get("error") or f"exit {proc.returncode}"
            return HubResponse(ok=False, data=data, error=str(err), exit_code=proc.returncode)

        return HubResponse(ok=True, data=data if isinstance(data, dict) else {"value": data}, exit_code=0)
