# Conftest for custom tests
# This file isolates custom tests from the main nautilus_trader test suite

import sys
from pathlib import Path

# Ensure we can import from the project root
PROJECT_ROOT = Path(__file__).parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# Ensure schwab_adapter package is importable
SCHWAB_ADAPTER_DIR = PROJECT_ROOT / "schwab_adapter"
if str(SCHWAB_ADAPTER_DIR) not in sys.path:
    sys.path.insert(0, str(SCHWAB_ADAPTER_DIR))

# Mark this directory as a separate test collection
collect_ignore_glob = []
