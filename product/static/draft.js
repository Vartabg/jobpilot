import { $, api, node, button, busy, tell, download, copy } from "./util.js";
import { watch, clean } from "./unsaved.js";
import { showJob } from "./jobs.js";
function field(label, input) {
  const wrapper = node("label", label);
  wrapper.append(input);
  return wrapper;
}
export function showDraft(draft, job, state) {
  const section = node("section", undefined, "panel");
  const title = node("h3", "Your application draft");
  title.tabIndex = -1;
  const text = node("textarea");
  text.value = draft.text;
  text.rows = 18;
  text.maxLength = 100000;
  section.append(
    title,
    node(
      "p",
      "A factual message starter, relevant resume excerpts, and questions to check. Edit it in your own voice.",
    ),
    field("Application draft", text),
  );
  const actions = node("div", undefined, "actions");
  const save = button("Save draft", () =>
    busy(save, "Saving draft…", async () => {
      await api(`jobs/${job.id}/draft`, {
        method: "PUT",
        body: JSON.stringify({ text: text.value }),
      });
      clean();
      tell("Draft saved on this computer.");
    }),
  );
  const take = button(
    "Download draft",
    () =>
      busy(take, "Saving and preparing download…", async () => {
        const saved = await api(`jobs/${job.id}/draft`, {
          method: "PUT",
          body: JSON.stringify({ text: text.value }),
        });
        clean();
        download(saved.text, "jobpilot-application.txt");
        tell("Draft saved and download requested. Review it before using it.");
      }),
    "primary",
  );
  actions.append(
    save,
    take,
    button("Copy draft", () => copy(text.value)),
    button("Back to job", () =>
      showJob(state.jobs.find((j) => j.id === job.id) || job, state),
    ),
  );
  watch(text);
  section.append(actions);
  $("job-detail").replaceChildren(section);
  title.focus();
}
