"""Gherkin validation using the official Cucumber parser.

Provides full AST validation rather than keyword-only checks.
"""

from __future__ import annotations

from typing import Any


def validate_gherkin(text: str) -> dict[str, Any]:
    """Validate Gherkin syntax using the official Cucumber parser.

    Returns a dict with ``valid``, ``feature_name``, ``scenario_count``,
    ``scenarios``, and ``issues`` keys.
    """
    try:
        from gherkin.parser import Parser
        from gherkin.errors import ParserError
    except ImportError:
        # Fallback: basic keyword check when gherkin-official is not installed
        return _fallback_validate(text)

    parser = Parser()
    try:
        doc = parser.parse(text)
        feature = doc.get("feature", {})
        children = feature.get("children", [])
        scenarios = [c for c in children if "scenario" in c]
        return {
            "valid": True,
            "feature_name": feature.get("name", ""),
            "scenario_count": len(scenarios),
            "scenarios": [s["scenario"]["name"] for s in scenarios],
            "issues": [],
        }
    except ParserError as e:
        return {
            "valid": False,
            "feature_name": "",
            "scenario_count": 0,
            "scenarios": [],
            "issues": [str(e)],
        }


def _fallback_validate(text: str) -> dict[str, Any]:
    """Basic keyword-level validation when the parser is unavailable."""
    lines = text.strip().splitlines()
    issues: list[str] = []

    if not any(line.strip().startswith("Feature:") for line in lines):
        issues.append("Missing 'Feature:' keyword")
    if not any(
        line.strip().startswith("Scenario:")
        or line.strip().startswith("Scenario Outline:")
        for line in lines
    ):
        issues.append("Missing 'Scenario:' keyword")

    return {
        "valid": len(issues) == 0,
        "feature_name": "",
        "scenario_count": 0,
        "scenarios": [],
        "issues": issues,
    }
