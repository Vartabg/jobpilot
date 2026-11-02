"""
Autonomy — graduated autonomy levels for JobPilot.

Three modes from conservative to aggressive:
  SUGGEST    — show suggestions only, user fills manually
  SEMI_AUTO  — auto-fill fields with confidence ≥ threshold, prompt on the rest
  FULL_AUTO  — auto-fill everything, auto-advance pages (never auto-submit final step)
"""

import json
from dataclasses import dataclass
from enum import StrEnum

from jobpilot.core.atomic_io import atomic_write_text
from jobpilot.core.config import DATA_DIR, SETTINGS_FILE


class AutonomyMode(StrEnum):
    """Graduated autonomy levels."""

    SUGGEST = "suggest"
    SEMI_AUTO = "semi-auto"
    FULL_AUTO = "full-auto"


@dataclass
class AutonomyConfig:
    """Runtime configuration for autonomy behavior."""

    mode: AutonomyMode = AutonomyMode.SEMI_AUTO
    auto_fill_threshold: float = 0.85  # Minimum confidence to auto-fill
    auto_advance_delay_ms: int = 1500  # Delay before clicking Next (ms)
    never_auto_submit: bool = True  # Safety: never auto-submit final step

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
        # SEMI_AUTO and FULL_AUTO both auto-advance; never auto-submit the final step.
        return not (is_final_step and self.never_auto_submit)

    def save(self) -> None:
        """Persist the autonomy keys without disturbing the rest of settings.json.

        settings.json is shared (resume lanes live there too), so this merges
        into the existing object instead of replacing it. An unreadable file
        is copied to settings.json.bak before being rewritten, never discarded.
        """
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        data: dict = {}
        if SETTINGS_FILE.exists():
            raw = SETTINGS_FILE.read_text()
            try:
                loaded = json.loads(raw)
            except ValueError:
                loaded = None
            if isinstance(loaded, dict):
                data = loaded
            else:
                atomic_write_text(SETTINGS_FILE.with_name("settings.json.bak"), raw)
        data.update(
            {
                "mode": self.mode.value,
                "auto_fill_threshold": self.auto_fill_threshold,
                "auto_advance_delay_ms": self.auto_advance_delay_ms,
                "never_auto_submit": self.never_auto_submit,
            }
        )
        atomic_write_text(SETTINGS_FILE, json.dumps(data, indent=2))

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
_config: AutonomyConfig | None = None


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
