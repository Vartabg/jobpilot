# iOS Shortcut — 3-tap apply with the resume pre-attached

The gigs digest pushes an **Apply** button to your phone. For `mailto:` leads it
opens Mail with the subject and body already typed — but you still attach your
resume by hand. This optional one-time Shortcut cuts that out: tap **Apply** →
review → send, with the resume PDF already attached.

## How it works

When the `GIGPILOT_IOS_SHORTCUT` environment variable is set to the name of a
Shortcut, the digest wraps each `mailto:` target as:

```
shortcuts://run-shortcut?name=<YourShortcutName>&input=<the mailto URL>
```

Tapping **Apply** runs your Shortcut and hands it the `mailto:` URL. The
Shortcut opens the mail draft and attaches your resume.

## One-time setup

1. Put your resume PDF in iCloud Drive (e.g. `iCloud Drive/Resume/resume.pdf`)
   so the Shortcut can reach it on any device.
2. Open the **Shortcuts** app → **+** to create a new Shortcut. Name it, e.g.,
   `GigPilotApply`.
3. Add these actions, in order:
   - **Get File** → point it at your resume PDF in iCloud Drive.
   - **URLs** → set to `Shortcut Input` (this is the `mailto:` URL passed in).
   - **Send Email** (or **Open URLs**):
     - For the richest result use **Send Email** with the **Get File** output
       as the attachment, and parse the recipient/subject/body from the input
       URL. The simplest reliable build: **Open URLs** with the input to pre-fill
       Mail, then the attachment step — test both on your device and keep
       whichever opens a composer you can review before sending.
4. In the Shortcut settings, enable **Show in Share Sheet** and allow it to run
   without confirmation if you want the fewest taps.
5. Tell GigPilot the name. Add to `~/.secrets/api-keys.env` (sourced by the
   digest scripts):

   ```bash
   export GIGPILOT_IOS_SHORTCUT="GigPilotApply"
   ```

## Tap counts

| Apply target | Taps from notification → sent |
|---|---|
| `mailto:` (no Shortcut) | 4: Apply → attach resume → review → Send |
| `mailto:` + this Shortcut | **3**: Apply → review → Send (resume auto-attached) |
| Greenhouse / Lever / Ashby form | 6–10: form fill (iOS autofill + the crib sheet help) → resume upload → screening Qs → submit |

## Safety

The Shortcut only opens a pre-filled draft — **it never sends automatically**.
You review and tap Send yourself. Resume upload on web ATS forms stays manual by
design (see `docs/GIGS.md`).
