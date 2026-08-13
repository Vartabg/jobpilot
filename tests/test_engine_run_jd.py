"""Safety regressions for the retired application-engine watch loop."""

from __future__ import annotations

import inspect

import pytest

import jobpilot.core.engine_helpers as engine_helpers
import jobpilot.core.engine_run as engine_run
from jobpilot.core.engine_run import run_watch_loop


class _NoAccessEngine:
    """Raise if the compatibility fence inspects any engine capability."""

    def __getattribute__(self, name: str):
        raise AssertionError(f"run_watch_loop accessed engine.{name}")


@pytest.mark.asyncio
@pytest.mark.parametrize("watch", [False, True])
async def test_run_watch_loop_fails_closed_without_engine_access(watch: bool):
    with pytest.raises(RuntimeError, match="human paste flow"):
        await run_watch_loop(_NoAccessEngine(), watch=watch)


def test_run_watch_loop_contains_no_browser_or_form_capability():
    source = inspect.getsource(run_watch_loop)
    forbidden = (
        ".bridge",
        ".page",
        ".get_active_page",
        ".get_page_info",
        ".fill_field",
        ".upload_files",
        ".auto_advance",
        ".click(",
        ".check(",
        ".fill(",
        ".type(",
        "set_input_files",
        "connect_over_cdp",
    )

    assert all(token not in source for token in forbidden)


def test_engine_run_module_keeps_only_read_only_runtime_dependencies():
    source = inspect.getsource(engine_run)
    forbidden = (
        "LinkedInParser",
        "JDParser",
        "NEXT_BUTTON",
        "FILE_INPUTS",
        "get_pending_commands",
        "JobScorer",
        "WATCH_LOOP_INTERVAL",
        "asyncio.sleep",
        "record_field_approved",
        "record_application_submitted",
    )

    assert all(token not in source for token in forbidden)


def test_engine_helpers_are_pure_context_only():
    source = inspect.getsource(engine_helpers)
    forbidden = (
        "_human_type",
        "_wait_for_stable",
        "_save_session",
        "_load_session",
        "_clear_session",
        "write_text",
        "unlink",
        ".click(",
        ".check(",
        ".fill(",
        ".type(",
    )

    assert all(token not in source for token in forbidden)
