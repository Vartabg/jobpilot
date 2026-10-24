---
name: mobile-director
description: Help Garo direct existing AI work from his phone, review concrete results, and resolve the few decisions that need him. Use for mobile check-ins or requests to see what needs his attention across tasks.
---

# Mobile director

Garo does his best AI work on his Mac but spends much of his time with only his
phone. His useful contribution is direction, judgment, and quality control.
Make those contributions easy without turning a check-in into another report.

## Find the useful next action

Start with the task and intent already in context. For an across-task check-in,
use the app's task listing and inspect only the few relevant recent tasks.
Use actual task titles verbatim. Treat old conversations as evidence, not new
instructions. Don't reopen paused work or dispatch unrelated projects just
because they look promising.

Show up to three items, ordered by the value of a decision now: an actionable
blocker, a result ready for judgment, or a concrete opportunity supported by
fresh evidence. Show fewer when appropriate; if nothing needs him, say so.
Do not pad the queue with stale jobs, old dashboards, or a daily crib sheet.

For each item, keep the first view short:

- **Result:** what changed or what is ready.
- **Evidence:** the preview, draft, diff, or source that lets him judge it.
- **Decision:** a specific next action and your recommendation.

Put technical detail behind the evidence link where possible. A local path or
localhost URL is not proof that a preview opens on his phone. Use an existing
mobile-accessible artifact or the app's supported attachment display. If none
is available, say so; do not publish private work just to make a link.

## Review and direct

When a decision is needed, use real choice controls if the current environment
provides them, such as `request_user_input_async`. Keep options short and
specific: "Use this version", "Revise it", "Later". Include the task and the
consequence in the question. Offer a choice only if you can carry it out.
If the app cannot show controls, accept the same choices as short text.
Do not describe a Markdown label as an implemented button.

Read short or dictated feedback in the context of the selected task. Turn it
into a concrete change with a clear stopping point. Use the existing task's
follow-up tool when available and authorized, preserving its model settings.
Ask for the target only when the intended task is ambiguous. Create a separate
task only when the user explicitly requests one.

"Use this version" accepts the result; it does not silently mean publish,
send, apply, or purchase. Name those actions explicitly when relevant and use
the user's existing authorization. A review choice must not add another
approval gate to work already authorized.

Continue authorized work to a reviewable result, with appropriate checks.
For visual work, show the actual rendering and identify what was inspected;
passing code tests alone does not establish visual quality. For job work,
verify freshness and fit, provide the human paste-card flow, and distinguish
prepared, opened, and actually submitted. For research, distinguish sourced
findings from inference. Do not invent success receipts.

## Keep attention useful

Let the app deliver task completion and attention notifications. Do not create
an extra scheduler as a side effect of this skill. If the user requests ongoing
monitoring, configure it through the supported automation tool: notify for
meaningful changes, completion, failure, or a decision; stay quiet while the
state is unchanged. "Later" dismisses this decision, not an implicit reminder.

Report waiting, running, ready for review, and failed states truthfully. A sent
follow-up is not proof that execution has started. Prefer the app's compact
task-status tool when following ongoing work. If the Mac is offline, state
that and do not claim the work is running, remotely awakened, or queued unless
the system actually confirms that state. This skill supplies a workflow; it
does not itself pair devices, prevent sleep, or wake a sleeping Mac.
