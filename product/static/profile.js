import { $, api, busy, tell } from "./util.js";
import { watch, dirty, clean } from "./unsaved.js";

export const boardLink = (board) =>
  ({
    greenhouse: "https://boards.greenhouse.io/",
    lever: "https://jobs.lever.co/",
    ashby: "https://jobs.ashbyhq.com/",
  })[board.provider] + board.slug;
function parseBoards(value) {
  return value
    .split("\n")
    .map((s) => s.trim())
    .filter(Boolean)
    .map((link) => {
      let url;
      try {
        url = new URL(link);
      } catch {
        throw new Error(
          "Use a complete company hiring-board link on each line.",
        );
      }
      const providers = {
        "boards.greenhouse.io": "greenhouse",
        "job-boards.greenhouse.io": "greenhouse",
        "jobs.lever.co": "lever",
        "jobs.ashbyhq.com": "ashby",
      };
      const provider = providers[url.hostname];
      const slug = url.pathname.split("/").filter(Boolean)[0];
      if (
        !provider ||
        !slug ||
        !/^[-\w]{1,80}$/.test(slug) ||
        url.protocol !== "https:" ||
        url.username ||
        url.password
      )
        throw new Error(
          "Use a Greenhouse, Lever or Ashby company board link. Other postings can be added from Find work.",
        );
      return { provider, slug };
    });
}
export function showProfile(profile) {
  const form = $("profile-form");
  for (const [key, value] of Object.entries(profile)) {
    const field = form.elements.namedItem(key);
    if (!field) continue;
    if (key === "boards") field.value = value.map(boardLink).join("\n");
    else if (field.type === "checkbox") field.checked = value;
    else field.value = Array.isArray(value) ? value.join(", ") : value;
  }
}
export function setupProfile(saved) {
  watch($("profile-form"));
  $("example-boards").addEventListener("click", () => {
    $("profile-form").elements.boards.value = [
      "https://boards.greenhouse.io/cloudflare",
      "https://jobs.ashbyhq.com/notion",
      "https://jobs.lever.co/spotify",
    ].join("\n");
    dirty();
    tell(
      "Example boards added. Save your profile to use them; you can replace any of these.",
    );
  });
  $("profile-form").addEventListener("submit", (event) => {
    event.preventDefault();
    busy(event.submitter, "Saving your profile on this computer…", async () => {
      const form = $("profile-form");
      const profile = Object.fromEntries(new FormData(form));
      profile.remote_only = form.elements.remote_only.checked;
      profile.skills = profile.skills
        .split(",")
        .map((s) => s.trim())
        .filter(Boolean);
      profile.keywords = profile.keywords
        .split(",")
        .map((s) => s.trim())
        .filter(Boolean);
      profile.boards = parseBoards(profile.boards);
      const result = await api("profile", {
        method: "PUT",
        body: JSON.stringify(profile),
      });
      clean();
      await saved(result);
      tell(
        "Profile saved. You can now find openings and use your application helper.",
      );
    });
  });
  $("resume-file").addEventListener("change", async (event) => {
    const file = event.target.files[0];
    if (!file) return;
    await busy(
      event.target,
      "Reading the resume on this computer…",
      async () => {
        if (file.size > 2 * 1024 * 1024)
          throw new Error(
            "Choose a file smaller than 2 MiB or paste its text.",
          );
        const type = file.name.toLowerCase().endsWith(".pdf")
          ? "application/pdf"
          : "text/plain";
        const result = await api("resume-text", {
          method: "POST",
          headers: { "Content-Type": type },
          body: file,
        });
        $("profile-form").elements.resume_text.value = result.text;
        dirty();
        tell(
          "Resume text imported. Check it and save your profile; the file itself has not been stored.",
        );
      },
    );
    event.target.value = "";
  });
}
