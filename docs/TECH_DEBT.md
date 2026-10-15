# Verification follow-up

## Applicant privacy scanner manual release checks — 2026-10-15

Affected users: screen-reader, touch and browser-zoom users of the privacy scan
and its native capture dialog. Owner: JobPilot maintainer. Target: 2026-10-22,
before a public accessibility-conformance claim for this flow.

Keyboard, reverse tab order, dialog Escape/focus return, accessibility-tree
labels and announcements, axe AA checks, 320/640px reflow, forced system colors
and reduced motion were verified. Actual VoiceOver speech, physical-device
orientation/coarse-pointer use and browser-menu zoom at 200%/400% were not
performed. Mitigation: native controls/dialog, semantic reports, visible focus,
44px controls, metadata copy fallback, explicit partial coverage, and source
availability. Responsive-width checks do not replace those manual checks.

## Physical iPhone Safari and VoiceOver — 2026-09-05

Affected users: people using the phone application flow with Safari and VoiceOver.
Owner: JobPilot maintainer. Target: 2026-09-12, before a mobile release.

This task verified desktop system Chrome with popup blocking enabled, keyboard
and reverse-tab completion, accessibility names and live regions, axe-core AA
checks, narrow-width reflow, reduced motion, and forced colors. It did not
exercise a physical iPhone, actual Safari popup policy, or VoiceOver speech.
The automated tools provide browser accessibility-tree evidence, not speech
or touch-screen evidence. Physical-device orientation and pinch-zoom behavior
also need the manual release pass.

Mitigation: email uses a persistent native link with focus moved to it;
blocked web popups leave the card unchanged and explain how to retry. No live
ATS forms or email composers were exercised by the tests. Run the phone and
VoiceOver release checks before calling the mobile experience verified.

# Product preview limitations — 2026-09-07

- Accessibility: keyboard navigation, skip links, automated WCAG AA checks, forced-color interaction and responsive 320/390/640/844px checks passed. A real VoiceOver session, physical-device testing and browser UI zoom at 400% have not been completed. Affected users: screen-reader, touch and low-vision users. Owner: Garo / product accessibility review. Mitigation: semantic HTML, visible focus, 44px targets, reduced motion, quick-copy fallback and source availability. Target: before a public accessibility-conformance claim; review by 2026-09-21.
- Distribution: Apple Silicon macOS bundle is tested locally and includes dependency notices, but lacks Developer ID signing/notarization. Windows and Intel Mac installers are not verified. Owner: Garo / release packaging. Mitigation: explicit preview/platform labels and separate source download. Target: before promotion as a stable cross-platform product.
- Product limits: no full-workspace restore, OCR, automatic follow-up reminders, AI rewriting, universal ATS filling, eligibility guesses, consent or submission. Text matching is not validated career guidance. Owner: Garo / product. Mitigation: limits in the UI and product guide. Revisit using observed user friction rather than speculative features.
- Maintainability exceptions: the standalone bookmark function stays in one file so it can be directly embedded without a runtime dependency. The static HTML/CSS and browser acceptance script exceed 200 formatted lines as cohesive documents. `jobs.js` is 207 lines after extracting drafts. Owner: product maintainer; split further when feature additions introduce another responsibility. Reviewed 2026-09-07.

BridgeBot was not found in the bounded local project search. No integration with it is claimed. Reused public-board and native-input approaches came from existing JobPilot code, then were independently tested within the standalone product.
