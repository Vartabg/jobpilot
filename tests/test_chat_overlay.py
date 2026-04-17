"""Tests for the unified JobPilot chat control surface."""

from unittest.mock import AsyncMock

import pytest

from jobpilot.ui.chat_overlay import ChatOverlay, build_chat_quick_actions, build_chat_welcome_message


def test_quick_actions_cover_operator_basics():
    actions = build_chat_quick_actions()
    labels = {action.label for action in actions}
    assert {"Status", "Advice", "Profile", "Role Fit", "Resume", "Interview"}.issubset(labels)

    prompts = {action.label: action.command for action in actions}
    assert "status" in prompts["Status"].lower()
    assert "profile" in prompts["Profile"].lower()
    assert "pursue aggressively" in prompts["Role Fit"].lower()
    assert "resume variant" in prompts["Resume"].lower()
    assert "interview prep" in prompts["Interview"].lower()


def test_welcome_message_sets_clear_operator_expectations():
    message = build_chat_welcome_message()
    assert "status" in message.lower()
    assert "role fit" in message.lower()
    assert "resume" in message.lower()
    assert "interview" in message.lower()
    assert "mode" in message.lower()


@pytest.mark.asyncio
async def test_chat_overlay_reuses_unified_surface_when_available():
    page = AsyncMock()

    async def eval_side_effect(script: str):
        if "typeof window.jobpilotChat !== 'undefined'" in script:
            return True
        return None

    page.evaluate.side_effect = eval_side_effect

    overlay = ChatOverlay(page)
    await overlay.ensure_injected()

    evaluated = [call.args[0] for call in page.evaluate.call_args_list]
    assert any("typeof window.jobpilotChat !== 'undefined'" in script for script in evaluated)
    assert not any("jobpilot-chat-host" in script for script in evaluated)


@pytest.mark.asyncio
async def test_chat_overlay_send_message_injects_surface_when_missing():
    page = AsyncMock()
    state = {"chat_available": False}

    async def eval_side_effect(script: str):
        if "typeof window.jobpilotChat !== 'undefined'" in script:
            return state["chat_available"]
        if "jobpilot-chat-host" in script:
            state["chat_available"] = True
            return None
        if "window.jobpilotChat.addAIMessage" in script:
            return None
        return None

    page.evaluate.side_effect = eval_side_effect

    overlay = ChatOverlay(page)
    await overlay.send_message("JobPilot control surface ready.")

    evaluated = [call.args[0] for call in page.evaluate.call_args_list]
    assert any("jobpilot-chat-host" in script for script in evaluated)
    assert any("window.jobpilotChat.addAIMessage" in script for script in evaluated)
