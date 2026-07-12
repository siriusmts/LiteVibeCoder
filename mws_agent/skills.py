"""Load attachable platform and work-style skills; the core loop owns no platform contract."""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class PlatformSkill:
    root: Path
    instructions: str
    spec: dict[str, Any]

    @classmethod
    def load(cls, root: Path) -> "PlatformSkill":
        skill_file = root / "SKILL.md"
        spec_file = root / "platform.json"
        if not skill_file.is_file() or not spec_file.is_file():
            raise RuntimeError(f"Platform skill must contain SKILL.md and platform.json: {root}")
        spec = json.loads(spec_file.read_text(encoding="utf-8"))
        for key in ("routes", "headers", "validation", "payload", "response", "frontend", "normalization"):
            if not isinstance(spec.get(key), dict):
                raise RuntimeError(f"Platform skill {root} has no object '{key}' in platform.json")
        return cls(root=root, instructions=skill_file.read_text(encoding="utf-8").strip(), spec=spec)

    def route(self, name: str, **values: Any) -> str:
        try:
            return str(self.spec["routes"][name]).format(**values)
        except KeyError as error:
            raise RuntimeError(f"Platform skill has no route '{name}'") from error


def read_skill(path: Path) -> str:
    if not path.is_file():
        raise RuntimeError(f"Work-style skill not found: {path}")
    return path.read_text(encoding="utf-8").strip()
