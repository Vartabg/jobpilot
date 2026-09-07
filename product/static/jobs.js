import { showDraft } from "./draft.js";
import { $, api, node, button, busy, tell, download, copy } from "./util.js";
import { allowLeave, watch, clean } from "./unsaved.js";

const statuses = [
  "saved",
  "prepared",
  "applied",
  "interview",
  "closed",
  "skipped",
];
export function renderJobs(state) {
  const query = $("filter").value.toLowerCase();
  const jobs = state.jobs
    .filter((j) =>
      state.view === "applications"
        ? ["prepared", "applied", "interview", "closed"].includes(j.status)
        : !["closed", "skipped"].includes(j.status),
    )
    .filter((j) =>
      `${j.company} ${j.title} ${j.location}`.toLowerCase().includes(query),
    );
  jobs.sort(
    (a, b) =>
      Number(b.available) - Number(a.available) ||
      b.fit.signals - a.fit.signals,
  );
  $("job-count").textContent = `${jobs.length} in this view`;
  $("list-title").textContent =
    state.view === "applications" ? "Your progress" : "Openings to review";
  $("job-list").replaceChildren();
  for (const job of jobs) {
    const item = button("", () => showJob(job, state), "job");
    item.setAttribute("aria-pressed", String(job.id === state.selected));
    item.append(
      node("span", job.company, "company"),
      node("strong", job.title),
      node("span", job.location || "Location not supplied", "meta"),
      node(
        "span",
        `${job.status} · ${job.available ? `${job.fit.matched.length} matching skill terms` : "No longer on this board"}`,
        "tag",
      ),
    );
    $("job-list").append(item);
  }
  if (!jobs.length)
    $("job-list").append(
      node(
        "p",
        state.view === "applications"
          ? "Prepared drafts and applications you mark will appear here."
          : "No openings in this view yet. Save your search preferences, find openings, or add a posting from another site.",
        "empty",
      ),
    );
}

function field(label, input) {
  const wrapper = node("label", label);
  wrapper.append(input);
  return wrapper;
}
function bulletList(title, items) {
  const section = node("section");
  section.append(node("h3", title));
  const list = node("ul");
  for (const text of items) list.append(node("li", text));
  section.append(list);
  return section;
}

export function showJob(job, state) {
  if (!allowLeave()) return;
  state.selected = job.id;
  renderJobs(state);
  const detail = $("job-detail");
  detail.replaceChildren();
  const heading = node("h2", job.title);
  heading.tabIndex = -1;
  detail.append(
    node("p", job.company, "eyebrow"),
    heading,
    node("p", job.location || "Location not supplied"),
    node(
      "p",
      `${job.provider === "manual" ? "Added by you" : "Board checked"} · ${new Date(job.checked_at).toLocaleString()}`,
      "meta",
    ),
  );
  if (!job.available)
    detail.append(
      node(
        "p",
        "This role was absent from the last successful board check. Open the original listing to confirm its status.",
      ),
    );
  if (job.fit.matched.length)
    detail.append(
      bulletList("Terms connected to your profile", job.fit.matched),
    );
  detail.append(
    node(
      "p",
      "These are text matches, not a qualification decision or a hiring probability. Check the requirements and evidence.",
      "hint",
    ),
  );
  if (job.fit.questions.length)
    detail.append(bulletList("Requirements to check", job.fit.questions));
  if (job.fit.excerpts.length)
    detail.append(
      bulletList("Relevant lines from your resume", job.fit.excerpts),
    );
  const actions = node("div", undefined, "actions");
  if (job.url) {
    const link = node("a", "Open original posting", "button");
    link.href = job.url;
    link.target = "_blank";
    link.rel = "noopener noreferrer";
    actions.append(link);
  }
  const draftButton = button(
    "Prepare application",
    () =>
      busy(
        draftButton,
        "Checking the posting and preparing your draft…",
        async () => {
          if (!allowLeave()) return;
          const draft = await api(`jobs/${job.id}/draft`, { method: "POST" });
          await state.reload();
          showDraft(draft, job, state);
          tell(
            "Draft ready. It uses your resume text; review and edit before sharing. Nothing has been submitted.",
          );
        },
      ),
    "primary",
  );
  actions.append(draftButton);
  detail.append(actions);
  const description = node("details");
  description.append(
    node("summary", "Full description supplied by the board"),
    node(
      "pre",
      job.description || "Open the original posting for its description.",
    ),
  );
  detail.append(description);
  const form = node("form", undefined, "field-stack");
  form.append(node("h3", "Keep track"));
  const select = node("select");
  select.name = "status";
  statuses.forEach((status) => {
    const option = node("option", status);
    option.value = status;
    select.append(option);
  });
  select.value = job.status;
  const note = node("textarea");
  note.name = "note";
  note.rows = 3;
  note.maxLength = 4000;
  note.value = job.note;
  const follow = node("input");
  follow.name = "follow_up";
  follow.type = "date";
  follow.value = job.follow_up;
  const save = node("button", "Save progress", "primary");
  save.type = "submit";
  form.append(
    field("Application status", select),
    field("Your notes", note),
    field("Follow-up date", follow),
    save,
  );
  if (job.follow_up)
    detail.append(
      node(
        "p",
        `Follow up: ${job.follow_up}${job.follow_up <= new Date().toLocaleDateString("en-CA") ? " · due" : ""}`,
        "tag",
      ),
    );
  form.addEventListener("submit", (event) => {
    event.preventDefault();
    busy(save, "Saving progress…", async () => {
      await api(`jobs/${job.id}`, {
        method: "PATCH",
        body: JSON.stringify({
          status: select.value,
          note: note.value,
          follow_up: follow.value,
        }),
      });
      clean();
      await state.reload();
      tell("Progress saved. “Applied” records that you submitted it yourself.");
    });
  });
  watch(form);
  detail.append(form);
  heading.focus();
}
