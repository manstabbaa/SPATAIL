# Immersive Experiences (HIG)
Source: https://developer.apple.com/design/human-interface-guidelines/immersive-experiences
Captured verbatim for SPATAIL — defines the immersion levels our Director chooses between.

## Overview
An app launches in the **Shared Space** (runs alongside other apps, switch like on a Mac) or a **Full Space** (runs alone, hides others, deep immersion). Apps can transition fluidly between them at any time.

## Immersion and Passthrough
**Passthrough** = real-time external-camera video so people stay comfortable and connected to their physical context. Digital Crown: press-hold to recenter; double-click to briefly hide all content and show passthrough.

The system auto-changes content opacity for safety: in `mixed`, getting too close to a physical object dims content briefly. In `progressive`/`full`, the system defines a boundary **~1.5 meters from the wearer's initial head position** — as the head nears it, the experience fades and passthrough increases; beyond it, immersive visuals are replaced by the app's icon, restored on return or recenter.

## Immersion Styles
- **Dimmed passthrough** — subtly dim/tint passthrough to bring attention to your content (default tint black; custom tint allowed).
- **`mixed`** — unbounded 3D blended with passthrough. Can request info about nearby objects + room layout. No boundary; nearby content goes semi-opaque near real objects.
- **`progressive`** — custom environment partially replaces passthrough; Digital Crown adjusts immersion within default **120°–360°** (or custom). ~1.5 m boundary applies.
- **`full`** — 360° custom environment completely replaces passthrough. ~1.5 m boundary applies.

## Best Practices
- **Offer multiple ways to use your app**; support accessibility.
- **Prefer launching in the Shared Space or `mixed`** — lets people reference your app while using others; gives control over when to increase immersion.
- **Reserve immersion for meaningful moments.** Not every task benefits; not every immersive task needs full immersion. Design immersion for the *individual tasks/content* that make your experience unique. (Photos example: browse in a window, transition to Full Space to examine one photo.)
- **Help people engage with key moments regardless of immersion level** — cues like dimming, tinting, motion, scale, Spatial Audio. Start subtle, strengthen only with reason.
- **Prefer subtle tint colors for passthrough**; avoid bright/dramatic tints.

## Promoting Comfort
- **Be mindful of visual comfort.** Even in Full Space, prefer placing 3D content within the field of view. Display motion comfortably.
- **Choose an immersion style that supports the movements people make.** Minor movements OK (shift weight, turn, sit/stand); excessive movement can interrupt experiences. Avoid `progressive`/`full` if people might need to move beyond the 1.5 m boundary.
- **Avoid encouraging movement in progressive/full.** Some people can't/won't move. **Let people bring a virtual object closer instead of moving to it.** ← core comfort principle
- **In `mixed`, avoid obscuring passthrough too much.** If virtual objects block too much view, use `full`/`progressive` instead.
- **Adopt ARKit to blend custom content with surroundings / use hand positions** (requires permission).

## Transitioning Between Styles
- **Design smooth, predictable transitions**; avoid sudden jarring changes.
- **Let people choose when to enter/exit** more immersion; provide a clear action (e.g. Keynote's prominent Exit button). Don't force system controls.
- **Indicate the purpose of an exit control** (return to less-immersive vs quit).

## Creating an Environment (if we build custom worlds later)
- Minimize distracting content/movement, especially at FOV edges.
- Help distinguish interactive objects (proximity invites interaction — near = touch, far = look only).
- Keep animation subtle; create an expansive (not claustrophobic) environment.
- Use Spatial Audio for atmosphere; avoid repetition/looping.
- Avoid flat 360° images (no sense of scale); prefer object meshes with lighting + shaders.
- **Always provide a ground-plane mesh so people don't feel like they're floating.**

---
### SPATAIL takeaways (Director immersion logic)
- **Default = mixed immersion** (content blended into the real room) — this IS our selling point.
- Keep the whole experience inside the **~1.5 m comfort bubble**; don't make the learner walk.
- "Bring the object closer, don't make them move" → stations arc within reach, hero objects float in.
- Proximity = interactivity signal → place interactive (live-mechanic) objects near, display-only (baked) objects a bit farther.
- Reserve immersion per-moment → most stations mixed; only escalate for a climactic "enter the system" beat.
