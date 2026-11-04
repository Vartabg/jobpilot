import { executeModelOperation, initializeLocalData } from "./model.js";

// One broker owns read/modify/write operations across every open side panel.
// Returning true keeps this worker alive until the queued response is sent.
let operations = Promise.resolve();

async function configure() {
  await chrome.storage.local.setAccessLevel({ accessLevel: "TRUSTED_CONTEXTS" });
  // Reset the persisted automatic toggle so the action invokes our listener.
  await chrome.sidePanel.setPanelBehavior({ openPanelOnActionClick: false });
}

chrome.action.onClicked.addListener((tab) => {
  // Open directly in the action gesture: this invocation grants activeTab on
  // the clicked tab, including employers without persistent host permission.
  try {
    chrome.sidePanel.open({ windowId: tab.windowId })
      .catch(() => console.error("JobPilot could not open the panel. Click its toolbar icon again."));
  } catch {
    console.error("JobPilot could not open the panel. Click its toolbar icon again.");
  }
});

function queue(operation) {
  const result = operations.catch(() => {}).then(operation);
  operations = result.catch(() => {});
  return result;
}
async function initialize() {
  try { await initializeLocalData(); }
  catch (error) {
    // An optional seed must never stop the empty-profile editor from opening.
    console.error("JobPilot could not initialize optional local data:", error.message);
  }
}
queue(async () => { await configure(); await initialize(); })
  .catch((error) => console.error("JobPilot setup failed:", error.message));
chrome.runtime.onInstalled.addListener(() => queue(async () => { await configure(); await initialize(); }).catch(console.error));
chrome.runtime.onStartup.addListener(() => queue(async () => { await configure(); await initialize(); }).catch(console.error));

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  if (message?.type !== "jobpilot:model") return false;
  // Chrome supplies sender.url: content scripts have the employer's URL,
  // whereas our workspace can legitimately live in a tab (sender.tab exists).
  // Permit only our two bundled UI documents, including their hash/query.
  const extensionRoot = chrome.runtime.getURL("");
  const document = sender.url?.startsWith(extensionRoot)
    ? sender.url.slice(extensionRoot.length).split(/[?#]/)[0] : '';
  if (sender.id !== chrome.runtime.id || !['sidepanel.html', 'workspace.html'].includes(document)) {
    sendResponse({ ok: false, error: "JobPilot storage is available only inside the extension." });
    return false;
  }
  queue(async () => {
    try {
      await initialize();
      const result = await executeModelOperation(message.operation, message.args || []);
      sendResponse({ ok: true, result });
    } catch (error) {
      sendResponse({ ok: false, error: error.message || "JobPilot could not save this change." });
    }
  });
  return true;
});
