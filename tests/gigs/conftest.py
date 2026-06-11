"""Isolate gig tests from real state.

Several jobpilot.gigs modules resolve their paths at import time (e.g.
preferences.PREFS_PATH derives from GIGPILOT_DATA_DIR), so the environment
must be pointed at a throwaway directory *before* any jobpilot.gigs import.
pytest imports this conftest before collecting the test modules in this
directory, which is early enough — no other test package imports gigs code.

This means the gig tests can never read or write the real machine-local
state (data/gigs/, iCloud pipeline/digest/away folders), regardless of what
is configured in the developer's shell.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

_TMP = Path(tempfile.mkdtemp(prefix="jobpilot-gigs-tests-"))

os.environ["GIGPILOT_DATA_DIR"] = str(_TMP / "data")
os.environ["GIGPILOT_ICLOUD_ROOT"] = str(_TMP / "icloud")
os.environ["GIGPILOT_PIPELINE_DIR"] = str(_TMP / "icloud" / "GigPilot")
os.environ["GIGPILOT_DIGESTS_DIR"] = str(_TMP / "icloud" / "Gigpilot_Digests")
os.environ["GIGPILOT_AWAY_DIR"] = str(_TMP / "icloud" / "Gigpilot_Away")
