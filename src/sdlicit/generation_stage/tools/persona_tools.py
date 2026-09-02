"""Generation stage tools — persona and Gherkin helpers.

Standalone utility functions. The unified MCP server in main.py
exposes the main tool endpoints.
"""

from __future__ import annotations

import json
import re


def parse_personas_json(raw_text: str) -> list[dict]:
    """Parse persona JSON from LLM output, handling common formatting issues.

    Extracts the first JSON array found in the text. Returns empty list
    on parse failure.
    """
    try:
        parsed = json.loads(raw_text)
        if isinstance(parsed, list):
            return parsed
        if isinstance(parsed, dict) and "personas" in parsed:
            return parsed["personas"]
    except (json.JSONDecodeError, TypeError):
        pass

    match = re.search(r"\[.*\]", raw_text, re.DOTALL)
    if match:
        try:
            parsed = json.loads(match.group())
            if isinstance(parsed, list):
                return parsed
        except (json.JSONDecodeError, TypeError):
            pass

    return []
