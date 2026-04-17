"""
Tests for autonomy.py — mode switching, thresholds, and configuration.
"""

import pytest
from jobpilot.core.autonomy import (
    AutonomyMode,
    AutonomyConfig,
    get_autonomy_config,
    set_autonomy_mode,
)


class TestShouldAutoFill:

    def test_suggest_mode_never_autofills(self):
        cfg = AutonomyConfig(mode=AutonomyMode.SUGGEST)
        assert cfg.should_auto_fill(0.99) is False

    def test_semi_auto_above_threshold(self):
        cfg = AutonomyConfig(mode=AutonomyMode.SEMI_AUTO)
        assert cfg.should_auto_fill(0.90) is True

    def test_semi_auto_below_threshold(self):
        cfg = AutonomyConfig(mode=AutonomyMode.SEMI_AUTO)
        assert cfg.should_auto_fill(0.50) is False

    def test_full_auto_always_fills(self):
        cfg = AutonomyConfig(mode=AutonomyMode.FULL_AUTO)
        assert cfg.should_auto_fill(0.01) is True

    def test_default_threshold_is_085(self):
        cfg = AutonomyConfig()
        assert cfg.auto_fill_threshold == 0.85


class TestShouldAutoAdvance:

    def test_suggest_never_advances(self):
        cfg = AutonomyConfig(mode=AutonomyMode.SUGGEST)
        assert cfg.should_auto_advance(is_final_step=False) is False

    def test_semi_auto_advances_non_final(self):
        cfg = AutonomyConfig(mode=AutonomyMode.SEMI_AUTO)
        assert cfg.should_auto_advance(is_final_step=False) is True

    def test_semi_auto_blocks_final_if_never_submit(self):
        cfg = AutonomyConfig(mode=AutonomyMode.SEMI_AUTO, never_auto_submit=True)
        assert cfg.should_auto_advance(is_final_step=True) is False

    def test_full_auto_advances_non_final(self):
        cfg = AutonomyConfig(mode=AutonomyMode.FULL_AUTO)
        assert cfg.should_auto_advance(is_final_step=False) is True

    def test_full_auto_blocks_final_if_never_submit(self):
        cfg = AutonomyConfig(mode=AutonomyMode.FULL_AUTO, never_auto_submit=True)
        assert cfg.should_auto_advance(is_final_step=True) is False


class TestGlobalConfig:

    def test_set_and_get_mode(self):
        original = get_autonomy_config()
        original_mode = original.mode
        set_autonomy_mode(AutonomyMode.FULL_AUTO)
        cfg = get_autonomy_config()
        assert cfg.mode == AutonomyMode.FULL_AUTO
        # Restore
        set_autonomy_mode(original_mode)

    def test_get_returns_config(self):
        cfg = get_autonomy_config()
        assert isinstance(cfg, AutonomyConfig)


class TestAutonomyModeEnum:

    def test_values(self):
        assert AutonomyMode.SUGGEST.value == "suggest"
        assert AutonomyMode.SEMI_AUTO.value == "semi-auto"
        assert AutonomyMode.FULL_AUTO.value == "full-auto"
