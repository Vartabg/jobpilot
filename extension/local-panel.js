export function setupLocalPanel() {
  const connect = document.getElementById('local-connect');
  const disconnect = document.getElementById('local-disconnect');
  const output = document.getElementById('local-status');
  const extensionId = document.getElementById('local-extension-id');
  if (!connect || !disconnect || !output || !extensionId) return;
  let busy = false, timer;
  extensionId.textContent = chrome.runtime.id;

  async function update(op = 'status') {
    if (busy) return;
    busy = true; connect.disabled = true; disconnect.disabled = true;
    clearTimeout(timer);
    try {
      const response = await chrome.runtime.sendMessage({type: 'jobpilot:local', op});
      if (!response?.ok) throw new Error(response?.error || 'The local controller is unavailable.');
      const state = response.result;
      connect.disabled = state.connected;
      disconnect.disabled = !state.connected;
      output.textContent = state.connected ? `Connected to this Mac${state.pending ? ` · ${state.pending} pending` : ''}.` : state.error || 'Disconnected. Connect when you want to use the local controller.';
      extensionId.textContent = state.extensionId || chrome.runtime.id;
      if (state.connected) timer = setTimeout(() => update(), 1500);
    } catch (error) {
      output.textContent = error.message || 'The local controller is unavailable.';
      connect.disabled = false; disconnect.disabled = true;
    } finally { busy = false; }
  }
  connect.addEventListener('click', () => update('connect'));
  disconnect.addEventListener('click', () => update('disconnect'));
  window.addEventListener('pagehide', () => clearTimeout(timer), {once: true});
  update();
}
