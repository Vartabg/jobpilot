"""
Ghost Overlay - Injected UI for suggestions and controls

Injects a Shadow DOM overlay into LinkedIn pages that shows:
- Field suggestions with confidence indicators
- Approve/Edit/Skip controls
- Keyboard shortcut hints
- Visual feedback before actions (no audio)
"""

import json
from dataclasses import dataclass
from typing import Optional

from playwright.async_api import Page
from rich.console import Console

from jobpilot.ui.chat_overlay import (
    build_chat_quick_actions,
    build_chat_welcome_message,
)

console = Console()

_CHAT_QUICK_ACTIONS_JSON = json.dumps(
    [{"command": action.command, "label": action.label} for action in build_chat_quick_actions()]
)
_CHAT_WELCOME_MESSAGE_JSON = json.dumps(build_chat_welcome_message())


@dataclass(frozen=True)
class ControlSurfaceSection:
    """User-facing sections shown in the unified JobPilot control surface."""

    key: str
    title: str
    hint: str


def build_control_surface_sections() -> list[ControlSurfaceSection]:
    """Return the ordered sections for the operator control surface."""
    return [
        ControlSurfaceSection(
            key="review",
            title="Review Queue",
            hint="Approve, edit, or skip suggested answers.",
        ),
        ControlSurfaceSection(
            key="assistant",
            title="Assistant",
            hint="Ask for status, advice, profile, or mode changes.",
        ),
    ]


def build_density_mode_label(is_compact: bool) -> str:
    """Return the concise density label shown in the panel header."""
    return "Compact" if is_compact else "Expanded"


_CONTROL_SURFACE_SECTIONS_JSON = json.dumps(
    [section.__dict__ for section in build_control_surface_sections()]
)
_DENSITY_LABELS_JSON = json.dumps(
    {
        "compact": build_density_mode_label(True),
        "expanded": build_density_mode_label(False),
    }
)


@dataclass(frozen=True)
class StatusPresentation:
    """User-facing status model for the injected JobPilot overlay."""

    tone: str
    label: str
    guidance: str


def build_status_presentation(status: str) -> StatusPresentation:
    """Translate raw engine status strings into clearer operator guidance."""
    normalized = (status or "").strip()
    lowered = normalized.lower()

    if "already applied" in lowered:
        return StatusPresentation(
            tone="warn",
            label="Already applied",
            guidance="Skip this posting or reopen it only if you want to review your previous attempt.",
        )
    if normalized.startswith("Step "):
        return StatusPresentation(
            tone="active",
            label=normalized,
            guidance="Review the highlighted suggestion, then approve, edit, or skip.",
        )
    if "auto-advancing" in lowered:
        return StatusPresentation(
            tone="active",
            label=normalized,
            guidance="JobPilot is moving to the next step. Stay ready for the next review.",
        )
    if "submitted" in lowered or "success" in lowered:
        return StatusPresentation(
            tone="success",
            label="Submitted",
            guidance="This application is done. Move to the next strong-fit role.",
        )
    if "navigate" in lowered:
        return StatusPresentation(
            tone="info",
            label="Navigate to a job",
            guidance="Open LinkedIn Jobs and start an Easy Apply flow to begin.",
        )
    if lowered == "ready":
        return StatusPresentation(
            tone="ready",
            label="Ready",
            guidance="Open an Easy Apply form and JobPilot will stage suggestions.",
        )
    return StatusPresentation(
        tone="info",
        label=normalized or "Standing by",
        guidance="Use Enter to approve, E to edit, or Esc to skip.",
    )

# The overlay HTML/CSS/JS that gets injected into the page
OVERLAY_TEMPLATE = """
(function() {
    // Prevent double-injection
    if (window.__jobpilot_overlay) return;

    // Bypass TrustedHTML CSP for overlay injection
    if (window.trustedTypes && window.trustedTypes.createPolicy) {
        try {
            if (!window.__jp_tt_policy) {
                window.__jp_tt_policy = window.trustedTypes.createPolicy("default", {
                    createHTML: s => s,
                    createScript: s => s,
                    createScriptURL: s => s
                });
            }
        } catch (e) {
            console.warn("JobPilot: Could not create default TrustedTypes policy", e);
        }
    }

    const CHAT_HISTORY_KEY = 'jobpilot_chat_history';
    const DENSITY_KEY = 'jobpilot_overlay_density';
    const CHAT_ACTIONS = __JP_CHAT_ACTIONS__;
    const SECTION_CONFIG = __JP_SURFACE_SECTIONS__;
    const DENSITY_LABELS = __JP_DENSITY_LABELS__;
    
    // Create container with Shadow DOM for style isolation
    const container = document.createElement('div');
    container.id = 'jobpilot-overlay-container';
    container.style.cssText = `
        position: fixed;
        top: 0;
        right: 0;
        z-index: 999999;
        pointer-events: none;
    `;
    
    const shadow = container.attachShadow({ mode: 'open' });
    
    // Inject styles
    const styles = document.createElement('style');
    styles.textContent = `
        :host {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
        }
        
        .jp-panel {
            position: fixed;
            top: 80px;
            right: 20px;
            width: 320px;
            background: linear-gradient(135deg, #1a1a2e 0%, #16213e 100%);
            border: 1px solid rgba(100, 255, 218, 0.3);
            border-radius: 12px;
            box-shadow: 0 8px 32px rgba(0, 0, 0, 0.4);
            color: #e0e0e0;
            pointer-events: auto;
            overflow: hidden;
        }
        
        .jp-header {
            background: rgba(100, 255, 218, 0.1);
            padding: 12px 16px;
            display: flex;
            align-items: center;
            justify-content: space-between;
            border-bottom: 1px solid rgba(100, 255, 218, 0.2);
        }
        
        .jp-title {
            font-size: 14px;
            font-weight: 600;
            color: #64ffda;
            display: flex;
            align-items: center;
            gap: 8px;
        }

        .jp-density-btn {
            border: 1px solid rgba(100, 255, 218, 0.24);
            background: rgba(100, 255, 218, 0.08);
            color: #9ef7e4;
            border-radius: 999px;
            padding: 3px 8px;
            font-size: 10px;
            cursor: pointer;
        }
        
        .jp-state-card {
            margin: 10px 12px 0;
            padding: 10px 12px;
            background: rgba(255, 255, 255, 0.04);
            border: 1px solid rgba(100, 255, 218, 0.14);
            border-radius: 10px;
        }

        .jp-state-row {
            display: flex;
            align-items: center;
            justify-content: space-between;
            gap: 8px;
            margin-bottom: 6px;
        }

        .jp-status-chip {
            display: inline-flex;
            align-items: center;
            border-radius: 999px;
            padding: 4px 10px;
            font-size: 11px;
            font-weight: 700;
            letter-spacing: 0.02em;
        }

        .jp-status-chip.tone-ready,
        .jp-status-chip.tone-active {
            background: rgba(100, 255, 218, 0.15);
            color: #64ffda;
        }

        .jp-status-chip.tone-info {
            background: rgba(255, 255, 255, 0.08);
            color: #d2d7de;
        }

        .jp-status-chip.tone-warn {
            background: rgba(255, 167, 38, 0.18);
            color: #ffa726;
        }

        .jp-status-chip.tone-success {
            background: rgba(76, 175, 80, 0.18);
            color: #81c784;
        }

        .jp-guidance {
            font-size: 12px;
            line-height: 1.4;
            color: #b8c2cc;
        }

        .jp-section-heading {
            display: flex;
            flex-direction: column;
            gap: 2px;
            margin-bottom: 8px;
            font-size: 11px;
            font-weight: 700;
            letter-spacing: 0.02em;
            color: #dce4eb;
            text-transform: uppercase;
        }

        .jp-section-heading span {
            font-size: 10px;
            font-weight: 500;
            color: #7e8a98;
            text-transform: none;
        }
        
        .jp-body {
            padding: 12px;
            max-height: 400px;
            overflow-y: auto;
        }
        
        .jp-field {
            background: rgba(255, 255, 255, 0.05);
            border-radius: 8px;
            padding: 10px;
            margin-bottom: 8px;
            border-left: 3px solid #64ffda;
        }
        
        .jp-field.low-confidence {
            border-left-color: #ffa726;
        }
        
        .jp-field.no-match {
            border-left-color: #ef5350;
        }
        
        .jp-field-label {
            font-size: 11px;
            color: #888;
            margin-bottom: 4px;
            display: flex;
            align-items: center;
            gap: 6px;
        }
        
        .jp-field-value {
            font-size: 13px;
            color: #fff;
            padding: 6px 8px;
            background: rgba(0, 0, 0, 0.3);
            border-radius: 4px;
            margin-bottom: 8px;
            word-break: break-word;
        }
        
        .jp-actions {
            display: flex;
            gap: 6px;
        }
        
        .jp-btn {
            flex: 1;
            padding: 6px 10px;
            border: none;
            border-radius: 4px;
            font-size: 11px;
            cursor: pointer;
            transition: all 0.2s;
        }
        
        .jp-btn-approve {
            background: rgba(100, 255, 218, 0.2);
            color: #64ffda;
        }
        
        .jp-btn-approve:hover {
            background: rgba(100, 255, 218, 0.4);
        }
        
        .jp-btn-edit {
            background: rgba(255, 167, 38, 0.2);
            color: #ffa726;
        }
        
        .jp-btn-edit:hover {
            background: rgba(255, 167, 38, 0.4);
        }
        
        .jp-btn-skip {
            background: rgba(239, 83, 80, 0.2);
            color: #ef5350;
        }
        
        .jp-btn-skip:hover {
            background: rgba(239, 83, 80, 0.4);
        }
        
        .jp-footer {
            padding: 10px 16px;
            background: rgba(0, 0, 0, 0.2);
            font-size: 11px;
            color: #666;
        }
        
        .jp-shortcut {
            background: rgba(255, 255, 255, 0.1);
            padding: 2px 6px;
            border-radius: 3px;
            font-family: monospace;
        }
        
        .jp-confidence {
            font-size: 10px;
            padding: 2px 6px;
            border-radius: 10px;
        }
        
        .jp-confidence.high { background: rgba(100, 255, 218, 0.2); color: #64ffda; }
        .jp-confidence.medium { background: rgba(255, 167, 38, 0.2); color: #ffa726; }
        .jp-confidence.low { background: rgba(239, 83, 80, 0.2); color: #ef5350; }
        
        .jp-empty {
            text-align: center;
            padding: 20px;
            color: #666;
        }

        .jp-assistant {
            padding: 10px 12px 12px;
            border-top: 1px solid rgba(100, 255, 218, 0.1);
            background: rgba(255, 255, 255, 0.02);
        }

        .jp-assistant-header {
            display: flex;
            align-items: center;
            justify-content: space-between;
            gap: 8px;
            margin-bottom: 8px;
            font-size: 12px;
            color: #dfe7ee;
            font-weight: 600;
        }

        .jp-assistant-hint {
            color: #7e8a98;
            font-size: 10px;
            font-weight: 500;
        }

        .jp-quick-actions {
            display: flex;
            flex-wrap: wrap;
            gap: 6px;
            margin-bottom: 8px;
        }

        .jp-quick-chip {
            background: rgba(100, 255, 218, 0.08);
            color: #64ffda;
            border: 1px solid rgba(100, 255, 218, 0.2);
            border-radius: 999px;
            padding: 4px 8px;
            font-size: 11px;
            cursor: pointer;
        }

        .jp-quick-chip:hover {
            background: rgba(100, 255, 218, 0.16);
        }

        .jp-chat-log {
            display: flex;
            flex-direction: column;
            gap: 6px;
            max-height: 120px;
            overflow-y: auto;
            margin-bottom: 8px;
        }

        .jp-chat-message {
            max-width: 92%;
            padding: 7px 9px;
            border-radius: 8px;
            font-size: 12px;
            line-height: 1.35;
            word-break: break-word;
        }

        .jp-chat-message.ai {
            align-self: flex-start;
            background: rgba(255, 255, 255, 0.05);
            color: #d6dce3;
        }

        .jp-chat-message.user {
            align-self: flex-end;
            background: rgba(100, 255, 218, 0.18);
            color: #ffffff;
        }

        .jp-chat-input-row {
            display: flex;
            gap: 6px;
        }

        .jp-chat-input-row input {
            flex: 1;
            background: rgba(0, 0, 0, 0.2);
            border: 1px solid rgba(255, 255, 255, 0.16);
            border-radius: 6px;
            padding: 7px 10px;
            color: #fff;
            font-size: 12px;
            outline: none;
        }

        .jp-chat-input-row input:focus {
            border-color: rgba(100, 255, 218, 0.65);
        }

        .jp-chat-input-row button {
            flex: 0 0 auto;
        }
        
        .jp-flash {
            animation: jp-flash-anim 0.3s ease-out;
        }
        
        @keyframes jp-flash-anim {
            0% { background: rgba(100, 255, 218, 0.4); }
            100% { background: transparent; }
        }

        .jp-success-pop {
            animation: jp-success-anim 0.8s cubic-bezier(0.175, 0.885, 0.32, 1.275);
        }

        .jp-toast {
            position: fixed;
            bottom: 20px;
            right: 20px;
            background: #64ffda;
            color: #0a0a12;
            padding: 8px 16px;
            border-radius: 8px;
            font-size: 12px;
            font-weight: 600;
            box-shadow: 0 4px 12px rgba(0,0,0,0.3);
            z-index: 1000001;
            animation: jp-toast-anim 3s forwards;
            pointer-events: auto;
        }

        @keyframes jp-toast-anim {
            0% { transform: translateY(100px); opacity: 0; }
            10% { transform: translateY(0); opacity: 1; }
            90% { transform: translateY(0); opacity: 1; }
            100% { transform: translateY(-20px); opacity: 0; }
        }

        @keyframes jp-success-anim {
            0% { transform: scale(0.5); opacity: 0; }
            50% { transform: scale(1.1); opacity: 1; }
            100% { transform: scale(1); opacity: 1; }
        }
        
        .jp-minimize-btn {
            background: none;
            border: none;
            color: #64ffda;
            cursor: pointer;
            font-size: 16px;
            padding: 0 4px;
        }
        
        .jp-minimized .jp-state-card,
        .jp-minimized .jp-body,
        .jp-minimized .jp-assistant,
        .jp-minimized .jp-footer,
        .jp-minimized .jp-progress-bar {
            display: none;
        }

        .jp-panel.jp-density-compact {
            width: 296px;
        }

        .jp-panel.jp-density-compact .jp-guidance,
        .jp-panel.jp-density-compact .jp-assistant-hint,
        .jp-panel.jp-density-compact .jp-footer {
            display: none;
        }

        .jp-panel.jp-density-compact .jp-body {
            max-height: 260px;
            padding-top: 10px;
            padding-bottom: 8px;
        }

        .jp-panel.jp-density-compact .jp-chat-log {
            max-height: 72px;
        }

        .jp-panel.jp-density-compact .jp-field {
            padding: 8px;
            margin-bottom: 6px;
        }

        .jp-thinking {
            animation: jp-thinking-pulse 1.5s infinite;
        }

        @keyframes jp-thinking-pulse {
            0% { border-left-color: rgba(100, 255, 218, 0.4); }
            50% { border-left-color: rgba(100, 255, 218, 1); }
            100% { border-left-color: rgba(100, 255, 218, 0.4); }
        }

        .jp-fit-badge {
            font-size: 10px;
            padding: 2px 8px;
            border-radius: 4px;
            background: rgba(100, 255, 218, 0.15);
            color: #64ffda;
            border: 1px solid rgba(100, 255, 218, 0.3);
        }

        /* --- Ghost Previews --- */
        .jp-ghost-active {
            color: #888 !important;
            font-style: italic !important;
            opacity: 0.7;
        }
        
        /* --- Progress Dashboard --- */
        .jp-progress-bar {
            display: flex;
            align-items: center;
            gap: 8px;
            padding: 8px 16px;
            background: rgba(0, 0, 0, 0.3);
            border-top: 1px solid rgba(100, 255, 218, 0.1);
            font-size: 11px;
            color: #999;
        }
        
        .jp-progress-ring {
            width: 28px;
            height: 28px;
            flex-shrink: 0;
        }
        
        .jp-progress-ring circle {
            fill: none;
            stroke-width: 3;
        }
        
        .jp-progress-ring .bg { stroke: rgba(255,255,255,0.1); }
        .jp-progress-ring .fg {
            stroke: #64ffda;
            stroke-linecap: round;
            transition: stroke-dashoffset 0.5s ease;
        }
        
        .jp-progress-text {
            display: flex;
            flex-direction: column;
            gap: 2px;
            line-height: 1.2;
        }
        
        .jp-progress-text .main { color: #ccc; font-weight: 500; }
        .jp-progress-text .sub { color: #666; font-size: 10px; }
        
        /* --- Field Highlight Beam --- */
        .jp-field:hover {
            background: rgba(255, 255, 255, 0.08);
        }
        
        /* --- Pre-Submit Review --- */
        .jp-review-overlay {
            position: fixed;
            top: 0; left: 0; right: 0; bottom: 0;
            background: rgba(0, 0, 0, 0.7);
            display: flex;
            align-items: center;
            justify-content: center;
            z-index: 1000000;
            pointer-events: auto;
        }
        
        .jp-review-card {
            background: linear-gradient(135deg, #1a1a2e 0%, #16213e 100%);
            border: 1px solid rgba(100, 255, 218, 0.4);
            border-radius: 16px;
            width: 420px;
            max-height: 80vh;
            overflow-y: auto;
            box-shadow: 0 20px 60px rgba(0, 0, 0, 0.6);
        }
        
        .jp-review-title {
            font-size: 16px;
            font-weight: 700;
            color: #64ffda;
            padding: 16px 20px;
            border-bottom: 1px solid rgba(100, 255, 218, 0.2);
        }
        
        .jp-review-body { padding: 16px 20px; }
        
        .jp-review-row {
            display: flex;
            justify-content: space-between;
            padding: 6px 0;
            border-bottom: 1px solid rgba(255,255,255,0.05);
        }
        
        .jp-review-label { color: #888; font-size: 12px; }
        .jp-review-value { color: #fff; font-size: 12px; text-align: right; max-width: 60%; word-break: break-word; }
        
        .jp-review-actions {
            display: flex;
            gap: 10px;
            padding: 16px 20px;
            justify-content: flex-end;
        }
        
        .jp-review-btn {
            padding: 8px 18px;
            border: none;
            border-radius: 6px;
            font-size: 13px;
            font-weight: 600;
            cursor: pointer;
        }
        
        .jp-review-btn.submit {
            background: #64ffda;
            color: #0a0a12;
        }
        
        .jp-review-btn.cancel {
            background: rgba(239, 83, 80, 0.3);
            color: #ef5350;
        }
    `;
    shadow.appendChild(styles);
    
    // Icons Helper — plain glyphs keep the injected overlay reliable on LinkedIn pages.
    const ICONS = {
        fill: '✓',
        edit: '✎',
        skip: '✕',
        rocket: '🚀'
    };

    // Create Pill (Floating Bubble)
    const pill = document.createElement('div');
    pill.className = 'jp-pill hidden';
    pill.innerHTML = `${ICONS.rocket} <span>JobPilot</span>`;
    shadow.appendChild(pill);

    const reviewSection = SECTION_CONFIG.find((section) => section.key === 'review') || {
        title: 'Review Queue',
        hint: 'Approve, edit, or skip suggested answers.',
    };
    const assistantSection = SECTION_CONFIG.find((section) => section.key === 'assistant') || {
        title: 'Assistant',
        hint: 'Ask for status, advice, profile, or mode changes.',
    };

    // Create panel
    const panel = document.createElement('div');
    panel.className = 'jp-panel';

    const header = document.createElement('div');
    header.className = 'jp-header';
    header.innerHTML = `
        <div class="jp-title">
            <span>${ICONS.rocket}</span>
            <span>JobPilot Control</span>
        </div>
        <div style="display: flex; align-items: center; gap: 8px;">
            <button class="jp-density-btn" id="jp-density-toggle">${DENSITY_LABELS.expanded}</button>
            <button class="jp-minimize-btn" id="jp-minimize" style="color: #64ffda; font-weight: bold; background: none; border: none; cursor: pointer; padding: 4px;">−</button>
        </div>
    `;

    const stateCard = document.createElement('div');
    stateCard.className = 'jp-state-card';
    stateCard.innerHTML = `
        <div class="jp-state-row">
            <div id="jp-status-chip" class="jp-status-chip tone-ready">Ready</div>
            <div id="jp-fit-score" class="jp-fit-badge" style="display:none">—% Match</div>
        </div>
        <div id="jp-guidance" class="jp-guidance">Open an Easy Apply form and JobPilot will stage suggestions.</div>
    `;

    const body = document.createElement('div');
    body.className = 'jp-body';
    body.innerHTML = `
        <div class="jp-section-heading">${reviewSection.title}<span>${reviewSection.hint}</span></div>
        <div class="jp-empty">Waiting for Easy Apply form...</div>
    `;

    const progress = document.createElement('div');
    progress.className = 'jp-progress-bar';
    progress.id = 'jp-progress';
    progress.style.display = 'none';
    progress.innerHTML = `
        <svg class="jp-progress-ring" viewBox="0 0 36 36">
            <circle class="bg" cx="18" cy="18" r="15"/>
            <circle class="fg" id="jp-ring-fg" cx="18" cy="18" r="15"
                stroke-dasharray="94.2" stroke-dashoffset="94.2"
                transform="rotate(-90 18 18)"/>
        </svg>
        <div class="jp-progress-text">
            <span class="main" id="jp-prog-main">0/0 fields</span>
            <span class="sub" id="jp-prog-sub">Waiting for scan...</span>
        </div>
    `;

    const assistant = document.createElement('div');
    assistant.className = 'jp-assistant';

    const assistantHeader = document.createElement('div');
    assistantHeader.className = 'jp-assistant-header';
    const assistantTitle = document.createElement('span');
    assistantTitle.textContent = assistantSection.title;
    const assistantHint = document.createElement('span');
    assistantHint.className = 'jp-assistant-hint';
    assistantHint.textContent = assistantSection.hint;
    assistantHeader.append(assistantTitle, assistantHint);

    const quickActions = document.createElement('div');
    quickActions.className = 'jp-quick-actions';
    CHAT_ACTIONS.forEach(({ command, label }) => {
        const btn = document.createElement('button');
        btn.className = 'jp-quick-chip';
        btn.type = 'button';
        btn.dataset.quick = command;
        btn.textContent = label;
        quickActions.appendChild(btn);
    });

    const chatLog = document.createElement('div');
    chatLog.className = 'jp-chat-log';
    chatLog.id = 'jp-chat-log';

    const chatInputRow = document.createElement('div');
    chatInputRow.className = 'jp-chat-input-row';
    const chatInput = document.createElement('input');
    chatInput.type = 'text';
    chatInput.placeholder = 'Ask about this role or type help...';
    const chatSend = document.createElement('button');
    chatSend.type = 'button';
    chatSend.textContent = '→';
    chatInputRow.append(chatInput, chatSend);

    assistant.append(assistantHeader, quickActions, chatLog, chatInputRow);

    const footer = document.createElement('div');
    footer.className = 'jp-footer';
    footer.style.display = 'flex';
    footer.style.justifyContent = 'space-between';
    footer.style.gap = '8px';
    footer.style.flexWrap = 'wrap';
    footer.innerHTML = `
        <div><span class="jp-shortcut">Enter</span> Approve</div>
        <div><span class="jp-shortcut">E</span> Edit</div>
        <div><span class="jp-shortcut">Esc</span> Skip</div>
    `;

    panel.append(header, stateCard, body, assistant, progress, footer);
    shadow.appendChild(panel);
    
    // --- State & Minimized logic ---
    const setMinimized = (min) => {
        if (min) {
            panel.classList.add('minimized');
            pill.classList.remove('hidden');
        } else {
            panel.classList.remove('minimized');
            pill.classList.add('hidden');
        }
    };
    const minimizeBtn = panel.querySelector('#jp-minimize');
    const densityBtn = panel.querySelector('#jp-density-toggle');
    const setDensityMode = (compact) => {
        panel.classList.toggle('jp-density-compact', compact);
        if (densityBtn) {
            densityBtn.textContent = compact ? DENSITY_LABELS.compact : DENSITY_LABELS.expanded;
            densityBtn.setAttribute('aria-pressed', compact ? 'true' : 'false');
            densityBtn.title = compact ? 'Compact density enabled' : 'Expanded density enabled';
        }
        sessionStorage.setItem(DENSITY_KEY, compact ? 'compact' : 'expanded');
    };
    if (minimizeBtn) minimizeBtn.onclick = () => setMinimized(true);
    if (densityBtn) {
        densityBtn.onclick = (e) => {
            e.preventDefault();
            e.stopPropagation();
            setDensityMode(!panel.classList.contains('jp-density-compact'));
        };
    }
    setDensityMode(sessionStorage.getItem(DENSITY_KEY) === 'compact');
    pill.onclick = () => setMinimized(false);

    // --- Adaptive Docking Logic ---
    const updatePosition = () => {
        const modal = document.querySelector(
            '[role="dialog"][aria-label*="Easy Apply"], '
            + '[data-test-modal-id="easy-apply-modal"], '
            + '.jobs-easy-apply-modal'
        );
        
        if (modal) {
            const rect = modal.getBoundingClientRect();
            const panelWidth = panel.classList.contains('jp-density-compact') ? 316 : 340;
            const margin = 20;
            
            // Dock to the right of the modal
            let targetLeft = rect.right + margin;
            let targetTop = rect.top;
            
            // Check if we hit the right boundary
            if (targetLeft + panelWidth > window.innerWidth - margin) {
                // If not enough room on the right, overlay on the modal (right side)
                targetLeft = rect.right - panelWidth - margin;
            }
            
            panel.style.left = `${targetLeft}px`;
            panel.style.top = `${targetTop}px`;
            panel.style.right = 'auto';
            
            // Auto-expand if minimized and modal just appeared
            if (panel.classList.contains('minimized') && !window.__jp_auto_expanded) {
                setMinimized(false);
                window.__jp_auto_expanded = true;
            }
        } else {
            // Default position if no modal
            panel.style.right = '20px';
            panel.style.top = '100px';
            panel.style.left = 'auto';
            window.__jp_auto_expanded = false;
        }
        
        requestAnimationFrame(updatePosition);
    };
    updatePosition();
    
    document.body.appendChild(container);

    function buildReviewShell(content) {
        return `
            <div class="jp-section-heading">${reviewSection.title}<span>${reviewSection.hint}</span></div>
            ${content}
        `;
    }

    function addChatMessage(text, type = 'ai', save = true) {
        const msg = document.createElement('div');
        msg.className = `jp-chat-message ${type}`;
        msg.textContent = text;
        chatLog.appendChild(msg);
        chatLog.scrollTop = chatLog.scrollHeight;
        if (save) saveChatHistory();
        return msg;
    }

    function saveChatHistory() {
        const history = [];
        chatLog.querySelectorAll('.jp-chat-message').forEach((el) => {
            history.push({
                text: el.textContent,
                type: el.classList.contains('user') ? 'user' : 'ai',
            });
        });
        sessionStorage.setItem(CHAT_HISTORY_KEY, JSON.stringify(history));
    }

    function restoreChatHistory() {
        try {
            const saved = sessionStorage.getItem(CHAT_HISTORY_KEY);
            if (saved) {
                chatLog.innerHTML = '';
                JSON.parse(saved).forEach((entry) => addChatMessage(entry.text, entry.type, false));
            }
        } catch (e) {
            // ignore session restore issues
        }
    }

    function queueChatOutbound(text, echoUser = true) {
        if (!text) return;
        if (echoUser) addChatMessage(text, 'user');
        if (!window.__jobpilot_outbox) window.__jobpilot_outbox = [];
        window.__jobpilot_outbox.push(text);
    }

    function sendChatMessage() {
        const text = chatInput.value.trim();
        if (!text) return;
        queueChatOutbound(text, true);
        chatInput.value = '';
    }

    chatInput.onkeydown = (e) => {
        if (e.key === 'Enter') sendChatMessage();
    };
    chatSend.onclick = sendChatMessage;
    quickActions.querySelectorAll('.jp-quick-chip').forEach((btn) => {
        btn.addEventListener('click', (e) => {
            e.preventDefault();
            e.stopPropagation();
            queueChatOutbound(btn.dataset.quick || '', true);
        });
    });

    restoreChatHistory();
    if (chatLog.children.length === 0) {
        addChatMessage(__JP_CHAT_WELCOME__, 'ai');
    }
    
    // Expose API for Python to communicate with
    window.__jobpilot = {
        updateStatus: function(status) {
            const payload = typeof status === 'string'
                ? {label: status, tone: 'info', guidance: 'Use Enter to approve, E to edit, or Esc to skip.'}
                : (status || {});
            const chip = shadow.querySelector('#jp-status-chip');
            const guidance = shadow.querySelector('#jp-guidance');
            if (chip) {
                chip.textContent = payload.label || 'Standing by';
                chip.className = `jp-status-chip tone-${payload.tone || 'info'}`;
            }
            if (guidance) {
                guidance.textContent = payload.guidance || 'Use Enter to approve, E to edit, or Esc to skip.';
            }
        },
        
        showSuggestions: function(fields, fitScore = null) {
            const body = shadow.querySelector('.jp-body');
            
            // Update fit score if provided
            const fitEl = shadow.querySelector('#jp-fit-score');
            if (fitScore !== null && fitEl) {
                fitEl.textContent = `Match: ${fitScore}%`;
                fitEl.style.display = 'block';
            }

            if (!fields || fields.length === 0) {
                body.innerHTML = buildReviewShell('<div class="jp-empty">No fields detected yet. Open or advance the Easy Apply form.</div>');
                this.updateStatus({
                    label: 'Waiting for fields',
                    tone: 'info',
                    guidance: 'Open the next Easy Apply step and JobPilot will stage suggestions here.'
                });
                return;
            }

            this.updateStatus({
                label: `Reviewing ${fields.length} field${fields.length === 1 ? '' : 's'}`,
                tone: 'active',
                guidance: 'Approve, edit, or skip the highlighted suggestions below.'
            });
            
            body.innerHTML = buildReviewShell(fields.map((f, i) => `
                <div class="jp-field ${f.status === 'thinking' ? 'jp-thinking' : ''} ${f.confidence < 0.65 ? 'low-confidence' : ''} ${f.confidence < 0.45 ? 'no-match' : ''}" data-field-id="${i}">
                    <div class="jp-field-label">
                        <span>${f.label || 'Field'}</span>
                        <span class="jp-confidence ${f.confidence >= 0.85 ? 'high' : f.confidence >= 0.65 ? 'medium' : 'low'}">
                            ${f.status === 'thinking' ? '...' : Math.round(f.confidence * 100) + '%'}
                        </span>
                    </div>
                    <div class="jp-field-value">${f.suggestion || (f.status === 'thinking' ? 'Analyzing...' : '(no suggestion)')}</div>
                    <div class="jp-actions">
                        <button class="jp-btn jp-btn-approve" data-action="approve" data-id="${i}" title="Approve & Fill" ${f.status === 'thinking' ? 'disabled' : ''}>
                            ${ICONS.fill}
                        </button>
                        <button class="jp-btn jp-btn-edit" data-action="edit" data-id="${i}" title="Manual Edit">
                            ${ICONS.edit}
                        </button>
                        <button class="jp-btn jp-btn-skip" data-action="skip" data-id="${i}" title="Skip Field">
                            ${ICONS.skip}
                        </button>
                    </div>
                </div>
            `).join(''));
            
            // --- Ghost Previews Logic ---
            const inputs = document.querySelectorAll(
                '.jobs-easy-apply-modal input, .jobs-easy-apply-modal textarea, '
                + '.jobs-easy-apply-modal select'
            );
            
            fields.forEach((f, i) => {
                const target = inputs[i];
                if (!target || !f.suggestion || f.status === 'thinking') return;
                
                // If the field is empty, show the suggestion as a "ghost" placeholder
                if (!target.value) {
                    target.setAttribute('placeholder', `[JobPilot]: ${f.suggestion}`);
                    target.classList.add('jp-ghost-input');
                }
            });

            // Add click handlers
            body.querySelectorAll('.jp-btn').forEach(btn => {
                btn.addEventListener('click', (e) => {
                    const source = e.currentTarget;
                    const action = source.dataset.action;
                    const id = parseInt(source.dataset.id, 10);
                    window.__jobpilot_action = { action, id };
                    
                    // Visual feedback
                    const fieldEl = shadow.querySelector(`[data-field-id="${id}"]`);
                    if (fieldEl) {
                        fieldEl.classList.add('jp-flash');
                        if (action === 'approve' || action === 'skip') {
                            setTimeout(() => fieldEl.remove(), 300);
                        }
                    }
                });
            });
        },
        
        flashField: function(fieldId) {
            const fieldEl = shadow.querySelector(`[data-field-id="${fieldId}"]`);
            if (fieldEl) {
                fieldEl.classList.add('jp-flash');
                setTimeout(() => fieldEl.classList.remove('jp-flash'), 300);
            }
        },
        
        hide: function() {
            panel.style.display = 'none';
        },
        
        show: function() {
            panel.style.display = 'block';
        },
        
        updateProgress: function({filled, total, step, totalSteps, appsToday}) {
            const bar = shadow.querySelector('#jp-progress');
            if (!bar) return;
            bar.style.display = 'flex';
            
            const pct = total > 0 ? filled / total : 0;
            const circumference = 94.2;
            const offset = circumference * (1 - pct);
            const ring = shadow.querySelector('#jp-ring-fg');
            if (ring) ring.setAttribute('stroke-dashoffset', offset);
            
            const main = shadow.querySelector('#jp-prog-main');
            if (main) main.textContent = `${filled}/${total} fields`;
            
            const sub = shadow.querySelector('#jp-prog-sub');
            if (sub) sub.textContent = `Step ${step}/${totalSteps}` + (appsToday ? ` · ${appsToday} apps today` : '');
        },
        
        showReview: function(fields) {
            // Pre-submit review dialog
            const existing = shadow.querySelector('.jp-review-overlay');
            if (existing) existing.remove();
            
            const overlay = document.createElement('div');
            overlay.className = 'jp-review-overlay';
            
            const rows = fields.map(f => `
                <div class="jp-review-row">
                    <span class="jp-review-label">${f.label}</span>
                    <span class="jp-review-value">${f.value || '(empty)'}</span>
                </div>
            `).join('');
            
            overlay.innerHTML = `
                <div class="jp-review-card">
                    <div class="jp-review-title">📋 Ready to Submit</div>
                    <div class="jp-review-body">${rows}</div>
                    <div class="jp-review-actions">
                        <button class="jp-review-btn cancel" id="jp-review-cancel">✕ Cancel</button>
                        <button class="jp-review-btn submit" id="jp-review-submit">Submit ✓</button>
                    </div>
                </div>
            `;
            
            shadow.appendChild(overlay);
            
            const submitBtn = overlay.querySelector('#jp-review-submit');
            const cancelBtn = overlay.querySelector('#jp-review-cancel');
            if (submitBtn) {
                submitBtn.addEventListener('click', () => {
                    window.__jobpilot_review_decision = 'submit';
                    overlay.remove();
                });
            }
            if (cancelBtn) {
                cancelBtn.addEventListener('click', () => {
                    window.__jobpilot_review_decision = 'cancel';
                    overlay.remove();
                });
            }
        },
        
        showSuccess: function() {
            const body = shadow.querySelector('.jp-body');
            body.innerHTML = `
                <div class="jp-empty jp-success-pop">
                    <div style="font-size: 48px; margin-bottom: 12px;">🎉</div>
                    <div style="color: #64ffda; font-weight: 700; font-size: 18px;">Application Sent!</div>
                    <div style="font-size: 12px; margin-top: 8px; color: #888;">JobPilot successfully assisted with this role.</div>
                </div>
            `;
            this.updateStatus({
                label: 'Submitted',
                tone: 'success',
                guidance: 'This application is done. Move to the next strong-fit role.'
            });
            setTimeout(() => {
                this.updateStatus({
                    label: 'Ready',
                    tone: 'ready',
                    guidance: 'Open an Easy Apply form and JobPilot will stage suggestions.'
                });
                body.innerHTML = buildReviewShell('<div class="jp-empty">Waiting for Easy Apply form...</div>');
            }, 5000);
        },

        showLearningToast: function(label) {
            const toast = document.createElement('div');
            toast.className = 'jp-toast';
            toast.innerHTML = `🧠 Learned: "${label}"`;
            shadow.appendChild(toast);
            setTimeout(() => toast.remove(), 3000);
        },

        addAIMessage: function(text) {
            addChatMessage(text, 'ai');
        },

        restoreChatHistory: function() {
            restoreChatHistory();
        },

        getPendingMessages: function() {
            const msgs = window.__jobpilot_outbox || [];
            window.__jobpilot_outbox = [];
            return msgs;
        }
    };

    window.jobpilotChat = {
        addAIMessage: (text) => window.__jobpilot.addAIMessage(text),
        restoreFromSession: () => window.__jobpilot.restoreChatHistory(),
        getPendingMessages: () => window.__jobpilot.getPendingMessages(),
    };
    
    // --- Keyboard Shortcuts ---
    // Only active when Easy Apply modal is open and user isn't typing in a field
    document.addEventListener('keydown', (e) => {
        const modal = document.querySelector('[data-test-modal-id="easy-apply-modal"]');
        if (!modal) return;
        
        const tag = document.activeElement?.tagName?.toLowerCase();
        const isTyping = tag === 'input' || tag === 'textarea' || tag === 'select';
        
        // Find the first visible suggestion field
        const firstField = shadow.querySelector('.jp-field');
        if (!firstField) return;
        const fieldId = parseInt(firstField.dataset.fieldId);
        
        if (e.key === 'Enter' && !isTyping) {
            e.preventDefault();
            window.__jobpilot_action = { action: 'approve', id: fieldId };
            firstField.classList.add('jp-flash');
            setTimeout(() => firstField.remove(), 300);
        } else if (e.key === 'e' && !isTyping) {
            e.preventDefault();
            window.__jobpilot_action = { action: 'edit', id: fieldId };
            firstField.classList.add('jp-flash');
        } else if (e.key === 'Escape') {
            e.preventDefault();
            window.__jobpilot_action = { action: 'skip', id: fieldId };
            firstField.classList.add('jp-flash');
            setTimeout(() => firstField.remove(), 300);
        }
    });
    
    // --- MutationObserver on Easy Apply modal ---
    window.__jobpilot_mutations = [];
    const _startObserver = () => {
        const modal = document.querySelector(
            '[role="dialog"][aria-label*="Easy Apply"], '
            + '[data-test-modal-id="easy-apply-modal"], '
            + '.jobs-easy-apply-modal'
        );
        if (!modal || window.__jobpilot_observer_active) return;
        
        const obs = new MutationObserver((mutations) => {
            const dominated = mutations.some(m =>
                m.type === 'childList' && (m.addedNodes.length > 0 || m.removedNodes.length > 0)
            );
            if (dominated) {
                window.__jobpilot_mutations.push({
                    type: 'dom_change',
                    ts: Date.now(),
                    count: mutations.length
                });
            }
        });
        obs.observe(modal, { childList: true, subtree: true });
        window.__jobpilot_observer_active = true;
    };
    // Try immediately and then poll for the modal to appear
    _startObserver();
    setInterval(_startObserver, 2000);
    
    // --- Field Highlight Beams ---
    shadow.addEventListener('mouseenter', (e) => {
        const fieldEl = e.target.closest?.('.jp-field');
        if (!fieldEl) return;
        const fieldId = fieldEl.dataset.fieldId;
        // Highlight the corresponding form element on the page
        const inputs = document.querySelectorAll(
            '.jobs-easy-apply-modal input, .jobs-easy-apply-modal textarea, '
            + '.jobs-easy-apply-modal select, .jobs-easy-apply-modal fieldset'
        );
        const target = inputs[fieldId];
        if (target) {
            target.style.outline = '2px solid #64ffda';
            target.style.outlineOffset = '2px';
            target.style.boxShadow = '0 0 12px rgba(100, 255, 218, 0.4)';
            target.style.transition = 'all 0.2s ease';
            window.__jobpilot_highlighted = target;
        }
    }, true);
    
    shadow.addEventListener('mouseleave', (e) => {
        const fieldEl = e.target.closest?.('.jp-field');
        if (!fieldEl) return;
        if (window.__jobpilot_highlighted) {
            window.__jobpilot_highlighted.style.outline = '';
            window.__jobpilot_highlighted.style.outlineOffset = '';
            window.__jobpilot_highlighted.style.boxShadow = '';
            window.__jobpilot_highlighted = null;
        }
    }, true);
    
    window.__jobpilot_overlay = true;
    console.log('✓ JobPilot overlay injected (v2 — MutationObserver + Progress + Highlights)');
})();
""".replace("__JP_CHAT_ACTIONS__", _CHAT_QUICK_ACTIONS_JSON).replace("__JP_CHAT_WELCOME__", _CHAT_WELCOME_MESSAGE_JSON).replace("__JP_SURFACE_SECTIONS__", _CONTROL_SURFACE_SECTIONS_JSON).replace("__JP_DENSITY_LABELS__", _DENSITY_LABELS_JSON)


class GhostOverlay:
    """
    Manages the overlay UI injected into LinkedIn pages.
    
    The overlay:
    - Shows suggested values with confidence levels
    - Provides Approve/Edit/Skip buttons per field
    - Gives visual feedback before any action
    - Doesn't interfere with LinkedIn's UI
    """
    
    def __init__(self, page: Page):
        self.page = page
        self._injected = False
    
    async def inject(self) -> bool:
        """Inject the overlay into the page"""
        try:
            await self.page.evaluate(OVERLAY_TEMPLATE)
            self._injected = True
            console.print("[green]✓ Overlay injected[/green]")
            return True
        except Exception as e:
            console.print(f"[red]✗ Failed to inject overlay: {e}[/red]")
            return False
    
    async def is_injected(self) -> bool:
        """Check if overlay is already injected"""
        result = await self.page.evaluate("window.__jobpilot_overlay === true")
        return bool(result)
    
    async def ensure_injected(self) -> bool:
        """Inject overlay if not already present"""
        if not await self.is_injected():
            return await self.inject()
        return True
    
    async def update_status(self, status: str):
        """Update the operator-facing status in the overlay."""
        if not await self.ensure_injected():
            return
        import json

        presentation = build_status_presentation(status)
        payload = json.dumps({
            "label": presentation.label,
            "tone": presentation.tone,
            "guidance": presentation.guidance,
        })
        await self.page.evaluate(f"window.__jobpilot.updateStatus({payload})")
    
    async def show_suggestions(self, suggestions: list[dict], fit_score: Optional[int] = None):
        """
        Show field suggestions in the overlay.
        
        Each suggestion should have:
        - label: Field label/name
        - suggestion: Suggested value
        - confidence: 0.0 to 1.0
        - status: 'thinking' (optional)
        """
        if not await self.ensure_injected():
            return
        # Convert to JSON-safe format
        import json
        suggestions_json = json.dumps(suggestions)
        fit_json = json.dumps(fit_score)
        await self.page.evaluate(f"window.__jobpilot.showSuggestions({suggestions_json}, {fit_json})")
    
    async def get_pending_action(self) -> Optional[dict]:
        """Check if user clicked any action button"""
        result = await self.page.evaluate("""
            (() => {
                const action = window.__jobpilot_action;
                window.__jobpilot_action = null;
                return action;
            })()
        """)
        return result
    
    async def flash_field(self, field_id: int):
        """Flash a field to show it's being acted on"""
        await self.page.evaluate(f"window.__jobpilot.flashField({field_id})")
    
    async def hide(self):
        """Hide the overlay"""
        if await self.is_injected():
            await self.page.evaluate("window.__jobpilot.hide()")
    
    async def show(self):
        """Show the overlay"""
        if await self.is_injected():
            await self.page.evaluate("window.__jobpilot.show()")

    async def update_progress(self, filled: int, total: int, step: int,
                               total_steps: int, apps_today: int = 0):
        """Update the progress dashboard in the overlay footer."""
        if not await self.ensure_injected():
            return
        import json
        data = json.dumps({
            "filled": filled, "total": total,
            "step": step, "totalSteps": total_steps,
            "appsToday": apps_today,
        })
        await self.page.evaluate(f"window.__jobpilot.updateProgress({data})")

    async def show_review(self, fields: list[dict]) -> str:
        """Show pre-submit review dialog and wait for decision.

        Returns 'submit' or 'cancel'.
        """
        if not await self.ensure_injected():
            return 'cancel'
        import json
        import asyncio
        fields_json = json.dumps(fields)
        await self.page.evaluate(f"window.__jobpilot.showReview({fields_json})")

        # Poll for the user's decision
        for _ in range(120):  # 60-second timeout
            decision = await self.page.evaluate("""
                (() => {
                    const d = window.__jobpilot_review_decision;
                    window.__jobpilot_review_decision = null;
                    return d;
                })()
            """)
            if decision:
                return decision
            await asyncio.sleep(0.5)
        return 'cancel'  # timeout = cancel

    async def show_success(self):
        """Show success animation in the overlay."""
        if not await self.ensure_injected():
            return
        await self.page.evaluate("window.__jobpilot.showSuccess()")

    async def show_learning_toast(self, label: str):
        """Show a 'Learned!' toast in the overlay."""
        if not await self.ensure_injected():
            return
        await self.page.evaluate(f"window.__jobpilot.showLearningToast({repr(label)})")

    async def get_mutations(self) -> list:
        """Drain the MutationObserver event queue."""
        result = await self.page.evaluate("""
            (() => {
                const q = window.__jobpilot_mutations || [];
                window.__jobpilot_mutations = [];
                return q;
            })()
        """)
        return result or []
