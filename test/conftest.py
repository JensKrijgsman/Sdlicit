"""Test collection setup.

The CLI (``cli/``) is not an installed package — it is run as
``cd cli && python cli_client.py``, with every module inside it importing
its siblings as top level names (``import api_client``, ``from shared.files
import ...``, ``from stages.composing import ...``). Tests replicate that by
putting ``cli/`` itself on ``sys.path``, the same way running the CLI does.
"""

from __future__ import annotations

import sys
from pathlib import Path

_CLI_DIR = Path(__file__).resolve().parent.parent / "cli"
if str(_CLI_DIR) not in sys.path:
    sys.path.insert(0, str(_CLI_DIR))
