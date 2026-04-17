"""
Autonomy — graduated autonomy levels for JobPilot.

Three modes from conservative to aggressive:
  SUGGEST    — show suggestions only, user fills manually
  SEMI_AUTO  — auto-fill fields with confidence ≥ threshold, prompt on the rest
  FULL_AUTO  — auto-fill everything, auto-advance pages (never auto-submit final step)
"""

import json
from enum import Enum
from pathlib import Path
from typing import Optional
from dataclasses import dataclass, asdict

from jobpilot.core.config import DATA_DIR, SETTINGS_FILE


class AutonomyMode(str, Enum):
    """Graduated autonomy levels."""
    SUGGEST = "suggest"
    SEMI_AUTO = "semi-auto"
    FULL_AUTO = "full-auto"


@dataclass
class AutonomyConfig:
    """Runtime configuration for autonomy behavior."""
    mode: AutonomyMode = AutonomyMode.SEMI_AUTO
    auto_fill_threshold: float = 0.85      # Minimum confidence to auto-fill
    auto_advance_delay_ms: int = 1500      # Delay before clicking Next (ms)
    never_auto_submit: bool = True         # Safety: never auto-submit final step

    def should_auto_fill(self, confidence: float) -> bool:
        """Should this field be auto-filled based on confidence?"""
        if self.mode == AutonomyMode.SUGGEST:
            return False
        if self.mode == AutonomyMode.FULL_AUTO:
            return True
        # SEMI_AUTO — only if above threshold
        return confidence >= self.auto_fill_threshold

    def should_auto_advance(self, is_final_step: bool) -> bool:
        """Should we auto-click Next after all fields are filled?"""
        if self.mode == AutonomyMode.SUGGEST:
            return False
        if is_final_step and self.never_auto_submit:
            return False
        return True  # SEMI_AUTO and FULL_AUTO both auto-advance non-final steps

    def save(self) -> None:
        """Persist settings to disk."""
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        data = {
            "mode": self.mode.value,
            "auto_fill_threshold": self.auto_fill_threshold,
            "auto_advance_delay_ms": self.auto_advance_delay_ms,
            "never_auto_submit": self.never_auto_submit,
        }
        SETTINGS_FILE.write_text(json.dumps(data, indent=2))

    @classmethod
    def load(cls) -> "AutonomyConfig":
        """Load settings from disk, or return defaults."""
        if SETTINGS_FILE.exists():
            try:
                data = json.loads(SETTINGS_FILE.read_text())
                return cls(
                    mode=AutonomyMode(data.get("mode", "semi-auto")),
                    auto_fill_threshold=data.get("auto_fill_threshold", 0.85),
                    auto_advance_delay_ms=data.get("auto_advance_delay_ms", 1500),
                    never_auto_submit=data.get("never_auto_submit", True),
                )
            except Exception:
                pass
        return cls()


# ---------------------------------------------------------------------------
# Global access
# ---------------------------------------------------------------------------
_config: Optional[AutonomyConfig] = None


def get_autonomy_config() -> AutonomyConfig:
    """Get or load the global autonomy config."""
    global _config
    if _config is None:
        _config = AutonomyConfig.load()
    return _config


def set_autonomy_mode(mode: AutonomyMode) -> AutonomyConfig:
    """Set the autonomy mode and persist."""
    config = get_autonomy_config()
    config.mode = mode
    config.save()
    return config
