"""Tests for domain/precedence.py — precedence.yaml loading."""

from __future__ import annotations

from pathlib import Path

import pytest

from rfp_intake.domain.precedence import PrecedencePolicy, load_precedence


def test_loads_valid_yaml(precedence_yaml_path: Path) -> None:
    policy = load_precedence(precedence_yaml_path)
    assert isinstance(policy, PrecedencePolicy)
    assert [r.id for r in policy.rules] == [
        "recency", "domain_authority", "specificity", "no_silent_resolution",
    ]


def test_rules_sorted_by_order(precedence_yaml_path: Path) -> None:
    policy = load_precedence(precedence_yaml_path)
    orders = [r.order for r in policy.rules]
    assert orders == sorted(orders)


def test_get_rule(precedence_yaml_path: Path) -> None:
    policy = load_precedence(precedence_yaml_path)
    rule = policy.get_rule("domain_authority")
    assert rule.mapping is not None
    assert set(rule.mapping) == {"protocol", "rfp", "any"}


def test_get_rule_unknown_raises(precedence_yaml_path: Path) -> None:
    policy = load_precedence(precedence_yaml_path)
    with pytest.raises(KeyError):
        policy.get_rule("nonexistent")


def test_severity_section_loaded(precedence_yaml_path: Path) -> None:
    policy = load_precedence(precedence_yaml_path)
    assert set(policy.severity) == {"high", "medium", "low"}


def test_missing_file_raises(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        load_precedence(tmp_path / "does_not_exist.yaml")
