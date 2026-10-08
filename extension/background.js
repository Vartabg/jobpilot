import { executeModelOperation, initializeLocalData } from "./model.js";

// One broker owns read/modify/write operations across every open side panel.
// Returning true keeps this worker alive until the queued response is sent.
let operations = Promise.resolve();

async function configure() {
  await chrome.storage.local.setAccessLevel({ accessLevel: "TRUSTED_CONTEXTS" });
  await chrome.sidePanel.setPanelBehavior({ openPanelOnActionClick: true });
}

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
  // Only extension pages may access the profile and résumé library. Content
  // scripts receive the specific user-approved payload through executeScript.
  if (sender.id !== chrome.runtime.id || sender.tab ||
      !sender.url?.startsWith(chrome.runtime.getURL(""))) {
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
