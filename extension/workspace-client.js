// Uses the existing trusted, serialized worker broker; never available to sites.
export async function workspaceCall(operation, ...args) {
  const response = await chrome.runtime.sendMessage({type: 'jobpilot:model', operation: `workspace:${operation}`, args});
  if (!response?.ok) throw new Error(response?.error || 'The workspace could not save this change. Please retry.');
  return response.result;
}
