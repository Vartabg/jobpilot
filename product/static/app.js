import { $, api, node, busy, tell, download } from "./util.js";
import { allowLeave } from "./unsaved.js";
import { setupProfile, showProfile } from "./profile.js";
import { renderJobs } from "./jobs.js";
import { showHelper } from "./helper.js";

const state = {
  profile: null,
  jobs: [],
  selected: null,
  view: "jobs",
  reload: async () => {
    state.jobs = await api("jobs");
    renderJobs(state);
  },
};
const titles = {
  jobs: [
    "Your next move.",
    "Find openings, prepare your application, and keep the details in one place.",
  ],
  applications: [
    "Keep things moving.",
    "Your saved drafts, applications, notes, and follow-up dates.",
  ],
  profile: [
    "Your experience belongs here.",
    "Set this up once. Change it as your direction changes.",
  ],
  helper: [
    "Type it once.",
    "Reuse your details while keeping control of the application.",
  ],
};
async function view(name, focus = true) {
  if (!allowLeave()) return;
  state.view = name;
  $("jobs-view").hidden = !["jobs", "applications"].includes(name);
  $("profile-view").hidden = name !== "profile";
  $("helper-view").hidden = name !== "helper";
  $("heading").textContent = titles[name][0];
  $("intro").textContent = titles[name][1];
  document
    .querySelectorAll("[data-view]")
    .forEach((button) =>
      button.setAttribute("aria-pressed", String(button.dataset.view === name)),
    );
  if (name === "helper") await showHelper();
  if (name === "profile") showProfile(state.profile);
  renderJobs(state);
  if (focus) $("heading").focus();
}
document
  .querySelectorAll("[data-view]")
  .forEach((button) =>
    button.addEventListener("click", () =>
      view(button.dataset.view).catch((e) => tell(e.message, true)),
    ),
  );
$("profile-shortcut").addEventListener("click", () => view("profile"));
$("filter").addEventListener("input", () => renderJobs(state));
$("search").addEventListener("click", (event) =>
  busy(event.target, "Searching your company hiring boards…", async () => {
    const result = await api("search", { method: "POST" });
    $("source-status").replaceChildren();
    for (const source of result.sources)
      $("source-status").append(
        node(
          "div",
          source.error
            ? `${source.slug}: ${source.error}`
            : `${source.slug}: ${source.count} listings checked`,
          "source" + (source.error ? " error" : ""),
        ),
      );
    await state.reload();
    tell(
      `${result.found} matching openings returned. ${result.sources.filter((s) => s.error).length ? "Some boards could not be checked; their previous results remain saved." : "Select a role to review its fit."}`,
    );
  }),
);
$("manual-form").addEventListener("submit", (event) => {
  event.preventDefault();
  busy(event.submitter, "Adding the posting…", async () => {
    await api("jobs", {
      method: "POST",
      body: JSON.stringify(Object.fromEntries(new FormData(event.target))),
    });
    event.target.reset();
    await state.reload();
    tell("Posting added. Select it to prepare a draft or track progress.");
  });
});
$("export").addEventListener("click", (event) =>
  busy(event.target, "Preparing your portable data…", async () => {
    const result = await api("export");
    download(
      JSON.stringify(result, null, 2),
      "jobpilot-data.json",
      "application/json",
    );
    tell(
      "Export requested. It contains your profile and saved work; share it only with a person or tool you choose.",
    );
  }),
);
$("quit").addEventListener("click", (event) =>
  busy(event.target, "Closing JobPilot…", async () => {
    if (!allowLeave()) return;
    await api("quit", { method: "POST" });
    tell(
      "JobPilot is closed. You can close this tab. Open the app to return to your saved work.",
    );
  }),
);
setupProfile(async (profile) => {
  state.profile = profile;
  await state.reload();
  await view("jobs");
});
try {
  [state.profile, state.jobs] = await Promise.all([
    api("profile"),
    api("jobs"),
  ]);
  await view(
    state.profile.resume_text || state.jobs.length ? "jobs" : "profile",
    false,
  );
  if (!state.profile.resume_text)
    tell(
      "Start with your existing resume and a few preferences. Everything is saved on this computer.",
    );
} catch (error) {
  tell(error.message || "JobPilot could not start. Reload the app.", true);
}
