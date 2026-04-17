"""
Streaming Chat Overlay
======================
Injects a floating chat interface into the browser for bidirectional
communication between the user and the JobPilot agent.
"""

from dataclasses import dataclass
import json
from rich.console import Console
from playwright.async_api import Page

console = Console()


@dataclass(frozen=True)
class ChatQuickAction:
    """Small, high-value commands exposed in the JobPilot chat UI."""

    command: str
    label: str


def build_chat_quick_actions() -> list[ChatQuickAction]:
    """Return the operator shortcuts shown in the chat control surface."""
    return [
        ChatQuickAction(
            command="Give me the current application status and the best next move.",
            label="Status",
        ),
        ChatQuickAction(
            command="Give me practical advice for this role right now.",
            label="Advice",
        ),
        ChatQuickAction(
            command="Summarize the strongest parts of my profile for this role.",
            label="Profile",
        ),
        ChatQuickAction(
            command="BLUF role-fit triage: should I pursue aggressively, pursue selectively, or archive this role?",
            label="Role Fit",
        ),
        ChatQuickAction(
            command="Pick the best resume variant for this role and explain why.",
            label="Resume",
        ),
        ChatQuickAction(
            command="Give me focused interview prep for this role, including likely themes and practice questions.",
            label="Interview",
        ),
        ChatQuickAction(
            command="Suggest the right automation mode for this application: suggest, semi-auto, or full-auto.",
            label="Suggest Mode",
        ),
    ]


def build_chat_welcome_message() -> str:
    """Return the default operator-facing welcome copy for the chat surface."""
    return (
        "Ask about this role, or tap Role Fit, Resume, or Interview to generate a targeted prompt. "
        "You can also check status, profile, or mode suggest / semi-auto / full-auto."
    )


_QUICK_ACTIONS_JSON = json.dumps(
    [{"command": action.command, "label": action.label} for action in build_chat_quick_actions()]
)

# The overlay HTML/CSS/JS that gets injected into the page
# Adapted from Cova Bot for JobPilot
CHAT_OVERLAY_TEMPLATE = """
(() => {
    // Prevent duplicate injection - but restore state if host exists
    const existingHost = document.getElementById('jobpilot-chat-host');
    if (existingHost) {
        // console.log('💬 Chat overlay already present, restoring state');
        if (window.jobpilotChat && window.jobpilotChat.restoreFromSession) {
            window.jobpilotChat.restoreFromSession();
        }
        return;
    }
    
    // === SESSION STORAGE KEYS ===
    const STORAGE_KEY = 'jobpilot_chat_history';
    const COLLAPSED_KEY = 'jobpilot_chat_collapsed';
    const QUICK_ACTIONS = __QUICK_ACTIONS_JSON__;
    
    // Create chat container with Shadow DOM
    const host = document.createElement('div');
    host.id = 'jobpilot-chat-host';
    document.body.appendChild(host);
    const shadow = host.attachShadow({mode: 'open'});
    
    // Styles
    const style = document.createElement('style');
    style.textContent = `
        :host {
            --primary: #64ffda;
            --primary-dim: rgba(100, 255, 218, 0.1);
            --bg: rgba(23, 33, 43, 0.95);
            --border: rgba(100, 255, 218, 0.3);
            --text: #e0e0e0;
        }
        
        * { box-sizing: border-box; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; }
        
        .chat-container {
            position: fixed;
            bottom: 20px;
            right: 20px;
            width: 320px;
            background: var(--bg);
            border-radius: 12px;
            border: 1px solid var(--border);
            box-shadow: 0 8px 32px rgba(0,0,0,0.4);
            display: flex;
            flex-direction: column;
            overflow: hidden;
            z-index: 2147483647; /* Max z-index */
            transition: height 0.3s ease;
            height: 400px;
        }
        
        .chat-container.collapsed {
            height: 48px;
        }
        
        .chat-header {
            padding: 12px 16px;
            background: var(--primary-dim);
            border-bottom: 1px solid var(--border);
            display: flex;
            align-items: center;
            justify-content: space-between;
            cursor: pointer;
            user-select: none;
            color: var(--primary);
            font-weight: 600;
            font-size: 14px;
        }

        .chat-title-block {
            display: flex;
            flex-direction: column;
            gap: 2px;
        }

        .chat-subtitle {
            font-size: 11px;
            color: #b8c2cc;
            font-weight: 500;
        }

        .chat-quick-actions {
            display: flex;
            flex-wrap: wrap;
            gap: 6px;
            padding: 10px 12px 0;
        }

        .quick-chip {
            background: rgba(100, 255, 218, 0.08);
            color: var(--primary);
            border: 1px solid rgba(100, 255, 218, 0.25);
            border-radius: 999px;
            padding: 4px 8px;
            font-size: 11px;
            cursor: pointer;
        }

        .quick-chip:hover {
            background: rgba(100, 255, 218, 0.18);
        }
        
        .chat-messages {
            flex: 1;
            overflow-y: auto;
            padding: 12px;
            display: flex;
            flex-direction: column;
            gap: 10px;
        }
        
        .message {
            padding: 8px 12px;
            border-radius: 8px;
            font-size: 13px;
            line-height: 1.4;
            max-width: 85%;
            word-wrap: break-word;
        }
        
        .message.user {
            background: rgba(100, 255, 218, 0.2);
            color: #fff;
            align-self: flex-end;
        }
        
        .message.ai {
            background: rgba(255, 255, 255, 0.05);
            color: #ccc;
            align-self: flex-start;
        }
        
        .chat-input-area {
            padding: 10px;
            border-top: 1px solid rgba(255, 255, 255, 0.1);
            display: flex;
            gap: 8px;
        }
        
        input {
            flex: 1;
            background: rgba(0, 0, 0, 0.2);
            border: 1px solid rgba(255, 255, 255, 0.2);
            border-radius: 6px;
            padding: 8px 12px;
            color: #fff;
            font-size: 13px;
            outline: none;
        }
        
        input:focus {
            border-color: var(--primary);
        }
        
        button {
            background: var(--primary-dim);
            border: 1px solid var(--border);
            color: var(--primary);
            border-radius: 6px;
            padding: 0 12px;
            cursor: pointer;
            font-weight: bold;
        }
        
        button:hover {
            background: rgba(100, 255, 218, 0.2);
        }
    `;
    shadow.appendChild(style);
    
    // UI Structure
    const container = document.createElement('div');
    container.className = 'chat-container';
    
    const header = document.createElement('div');
    header.className = 'chat-header';

    const titleBlock = document.createElement('div');
    titleBlock.className = 'chat-title-block';
    const title = document.createElement('span');
    title.textContent = '💬 JobPilot Control';
    const subtitle = document.createElement('span');
    subtitle.className = 'chat-subtitle';
    subtitle.textContent = 'role fit • resume • interview • mode';
    titleBlock.append(title, subtitle);

    const toggleSpan = document.createElement('span');
    toggleSpan.textContent = '−';
    header.append(titleBlock, toggleSpan);

    const quickActions = document.createElement('div');
    quickActions.className = 'chat-quick-actions';
    QUICK_ACTIONS.forEach(({ command, label }) => {
        const btn = document.createElement('button');
        btn.className = 'quick-chip';
        btn.type = 'button';
        btn.dataset.quick = command;
        btn.textContent = label;
        quickActions.appendChild(btn);
    });
    
    const messages = document.createElement('div');
    messages.className = 'chat-messages';
    
    const inputArea = document.createElement('div');
    inputArea.className = 'chat-input-area';
    const input = document.createElement('input');
    input.type = 'text';
    input.placeholder = 'Ask about fit, resume, or interview prep...';
    const sendBtn = document.createElement('button');
    sendBtn.type = 'button';
    sendBtn.textContent = '→';
    inputArea.append(input, sendBtn);
    
    container.append(header, quickActions, messages, inputArea);
    shadow.appendChild(container);
    
    // Collapse Toggle
    header.onclick = () => {
        container.classList.toggle('collapsed');
        const isCollapsed = container.classList.contains('collapsed');
        toggleSpan.textContent = isCollapsed ? '+' : '−';
        sessionStorage.setItem(COLLAPSED_KEY, isCollapsed);
    };
    
    if (sessionStorage.getItem(COLLAPSED_KEY) === 'true') {
        container.classList.add('collapsed');
        toggleSpan.textContent = '+';
    }
    
    // Messaging
    function addMessage(text, type, save = true) {
        const msg = document.createElement('div');
        msg.className = `message ${type}`;
        msg.textContent = text;
        messages.appendChild(msg);
        messages.scrollTop = messages.scrollHeight;
        
        if (save) {
            saveToSession();
        }
        return msg;
    }
    
    function saveToSession() {
        const history = [];
        messages.querySelectorAll('.message').forEach(el => {
            history.push({
                text: el.textContent,
                type: el.classList.contains('user') ? 'user' : 'ai'
            });
        });
        sessionStorage.setItem(STORAGE_KEY, JSON.stringify(history));
    }
    
    function restoreFromSession() {
        try {
            const saved = sessionStorage.getItem(STORAGE_KEY);
            if (saved) {
                messages.innerHTML = '';
                JSON.parse(saved).forEach(m => addMessage(m.text, m.type, false));
            }
        } catch(e) {}
    }
    
    // Send Logic
    function queueOutbound(text, echoUser = true) {
        if (!text) return;
        if (echoUser) addMessage(text, 'user');
        if (!window.__jobpilot_outbox) window.__jobpilot_outbox = [];
        window.__jobpilot_outbox.push(text);
    }

    function sendMessage() {
        const text = input.value.trim();
        if (!text) return;
        queueOutbound(text, true);
        input.value = '';
    }
    
    input.onkeydown = (e) => { if (e.key === 'Enter') sendMessage(); };
    sendBtn.onclick = sendMessage;
    quickActions.querySelectorAll('[data-quick]').forEach((btn) => {
        btn.addEventListener('click', (e) => {
            e.preventDefault();
            e.stopPropagation();
            queueOutbound(btn.dataset.quick || '', true);
        });
    });
    
    // Initial Restore
    restoreFromSession();
    if (messages.children.length === 0) {
        addMessage(__WELCOME_MESSAGE__, 'ai');
    }
    
    // Public API
    window.jobpilotChat = {
        addAIMessage: (text) => addMessage(text, 'ai'),
        restoreFromSession: restoreFromSession,
        getPendingMessages: () => {
            const msgs = window.__jobpilot_outbox || [];
            window.__jobpilot_outbox = [];
            return msgs;
        }
    };
    
    console.log('✓ JobPilot Chat injected');
})();
""".replace("__WELCOME_MESSAGE__", json.dumps(build_chat_welcome_message())).replace("__QUICK_ACTIONS_JSON__", _QUICK_ACTIONS_JSON)

class ChatOverlay:
    """
    Manages the persistent Chat UI.
    """

    def __init__(self, page: Page):
        self.page = page
        self._injected = False

    async def inject(self) -> bool:
        """Inject the fallback chat overlay, or reuse the unified surface if it already exists."""
        try:
            has_unified = await self.page.evaluate("typeof window.jobpilotChat !== 'undefined'")
            if has_unified:
                await self.page.evaluate(
                    "window.jobpilotChat.restoreFromSession && window.jobpilotChat.restoreFromSession()"
                )
                self._injected = True
                return True

            await self.page.evaluate(CHAT_OVERLAY_TEMPLATE)
            self._injected = True
            return True
        except Exception as e:
            self._injected = False
            console.print(f"[red]Failed to inject chat: {e}[/red]")
            return False

    async def ensure_injected(self) -> bool:
        """Ensure the chat control surface exists, injecting it when needed."""
        try:
            has_chat_api = await self.page.evaluate("typeof window.jobpilotChat !== 'undefined'")
        except Exception:
            has_chat_api = False

        self._injected = bool(has_chat_api)
        if self._injected:
            return True

        return await self.inject()
            
    async def get_messages(self) -> list[str]:
        """Poll for new messages from the user."""
        if not await self.ensure_injected():
            return []
        try:
            return await self.page.evaluate("window.jobpilotChat.getPendingMessages()")
        except Exception:
            return []

    async def send_message(self, text: str):
        """Send a message to the chat UI."""
        if not await self.ensure_injected():
            return
        safe_text = json.dumps(text)
        await self.page.evaluate(f"window.jobpilotChat.addAIMessage({safe_text})")
