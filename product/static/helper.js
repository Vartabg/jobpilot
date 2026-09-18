import { $, api, node, button, copy, tell } from "./util.js";

const labels = {
  full_name: "Full name",
  first_name: "First name",
  last_name: "Last name",
  email: "Email",
  phone: "Phone",
  city: "City",
  region: "State or region",
  country: "Country",
  postal_code: "Postal code",
  linkedin: "LinkedIn",
  portfolio: "Portfolio",
  github: "GitHub",
  headline: "Headline",
};
export async function showHelper() {
  const result = await api("fill");
  $("fill-bookmark").href = result.url;
  $("copy-fields").replaceChildren();
  for (const [key, value] of Object.entries(result.fields)) {
    if (!value) continue;
    const row = node("div", undefined, "copy-item");
    const text = node("div");
    text.append(node("small", labels[key]), node("span", value));
    const control = button("Copy", () => copy(value));
    control.setAttribute("aria-label", "Copy " + labels[key]);
    row.append(text, control);
    $("copy-fields").append(row);
  }
  if (!$("copy-fields").children.length)
    $("copy-fields").append(
      node("p", "Add the details you want to reuse in Your profile."),
    );
}
$("fill-bookmark").addEventListener("click", (event) => {
  event.preventDefault();
  tell(
    "Drag this button to your bookmarks bar, then use the bookmark on an application page.",
  );
});
