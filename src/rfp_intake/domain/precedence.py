"""Load and validate config/precedence.yaml into typed Pydantic models.

Mirrors domain/registry.py's role for fields.yaml: this is the loader, not
the tie-break logic. The deterministic precedence application itself lives
in reconcile/precedence.py, per this policy's own stated ownership
("applied by RECONCILE after ADJUDICATE returns a 'conflict' verdict").
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field

from rfp_intake.config.settings import get_settings


class PrecedenceRule(BaseModel):
    id: str
    order: int
    description: str
    applies_to: str | None = None
    mapping: dict[str, str] | None = None


class PrecedencePolicy(BaseModel):
    rules: list[PrecedenceRule]
    severity: dict[str, str] = Field(default_factory=dict)

    def get_rule(self, rule_id: str) -> PrecedenceRule:
        for r in self.rules:
            if r.id == rule_id:
                return r
        raise KeyError(f"Unknown precedence rule: {rule_id}")


def load_precedence(path: Path | None = None) -> PrecedencePolicy:
    """Load precedence.yaml into a typed PrecedencePolicy."""
    if path is None:
        path = Path(get_settings().precedence_yaml_path)

    if not path.exists():
        raise FileNotFoundError(f"Precedence policy not found: {path}")

    data: dict[str, Any] = yaml.safe_load(path.read_text())
    rules = [PrecedenceRule(**r) for r in data.get("rules", [])]
    severity = data.get("severity", {})

    return PrecedencePolicy(rules=sorted(rules, key=lambda r: r.order), severity=severity)


@lru_cache(maxsize=1)
def get_precedence_policy() -> PrecedencePolicy:
    """Singleton access to the loaded precedence policy."""
    return load_precedence()
