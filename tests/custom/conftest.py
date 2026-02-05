# Conftest for custom SuperStrat tests
# This file isolates custom tests from the main nautilus_trader test suite

import sys
from pathlib import Path

# Ensure we can import from the project root
PROJECT_ROOT = Path(__file__).parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# Mark this directory as a separate test collection
collect_ignore_glob = []
