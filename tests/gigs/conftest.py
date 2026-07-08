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

import json
import os
import tempfile
from pathlib import Path

_TMP = Path(tempfile.mkdtemp(prefix="jobpilot-gigs-tests-"))

os.environ["GIGPILOT_DATA_DIR"] = str(_TMP / "data")
os.environ["GIGPILOT_ICLOUD_ROOT"] = str(_TMP / "icloud")
os.environ["GIGPILOT_PIPELINE_DIR"] = str(_TMP / "icloud" / "GigPilot")
os.environ["GIGPILOT_DIGESTS_DIR"] = str(_TMP / "icloud" / "Gigpilot_Digests")
os.environ["GIGPILOT_AWAY_DIR"] = str(_TMP / "icloud" / "Gigpilot_Away")

# Seed a *resolved* identity into the isolated data dir. Draft-building tests
# (swipe cards, crib mailto, draft conversion) go through the placeholder guard
# in proposals.py, which refuses any draft still holding a neutral placeholder
# (example.com / your-handle / ...). With an empty data dir and no jobpilot
# profile — the normal CI / fresh-clone state — identity falls back to those
# placeholders and every draft test fails. A test identity here makes those
# tests hermetic without depending on a machine-local data/profile.json.
# (test_preferences.py resolves via explicit load(path=...) calls and is
# unaffected; this only backs the global preferences.load() default path.)
_DATA_DIR = _TMP / "data"
_DATA_DIR.mkdir(parents=True, exist_ok=True)
(_DATA_DIR / "preferences.json").write_text(json.dumps({
    "identity": {
        "first_name": "Test",
        "last_name": "Applicant",
        "email": "test.applicant@resolved.test",
        "phone": "555-000-1234",
        "phone_note": "(text preferred)",
        "linkedin": "https://www.linkedin.com/in/test-applicant",
        "github": "https://github.com/test-applicant",
        "portfolio": "https://resolved.test",
        "city": "Austin, TX",
        "tagline": "Builder who ships",
    },
    "links": {
        "service_page": "https://resolved.test/services",
        "work_page": "https://resolved.test/work",
    },
}))
