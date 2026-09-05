# Verification follow-up

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
