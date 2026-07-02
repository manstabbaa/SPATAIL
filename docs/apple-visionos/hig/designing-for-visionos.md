# Designing for visionOS (HIG)
Source: https://developer.apple.com/design/human-interface-guidelines/designing-for-visionos
Captured verbatim for SPATAIL spatial-education build.

When people wear Apple Vision Pro, they enter an infinite 3D space where they can engage with your app or game while staying connected to their surroundings.

As you begin designing, start by understanding the fundamental device characteristics and patterns that distinguish the platform. Use these to inform design decisions and create immersive, engaging experiences.

## Fundamental Characteristics

**Space.** Apple Vision Pro offers a limitless canvas for virtual content like windows, volumes, and 3D objects, and deeply immersive experiences that can transport people elsewhere.

**Immersion.** People fluidly transition between levels of immersion. By default an app launches in the *Shared Space* where multiple apps run side-by-side and people can open, close, and relocate windows. People can transition an app to a *Full Space*, where it's the only app running — viewing 3D content blended with surroundings, opening a portal to another place, or entering a different world.

**Passthrough.** Live video from external cameras lets people interact with virtual content while seeing their actual surroundings. The Digital Crown controls the amount of passthrough.

**Spatial Audio.** The device models the sonic characteristics of a person's surroundings to make audio sound natural. With permission to access surroundings, an app can fine-tune Spatial Audio.

**Eyes and hands.** People perform most actions by using their **eyes** to look at a virtual object and making an *indirect* **gesture** (like a tap) to activate it. They can also use a *direct* gesture, like touching it with a finger.

**Ergonomics.** People rely entirely on the device's cameras for everything they see, so maintaining visual comfort is paramount. The system automatically places content relative to the wearer's head, regardless of height or whether they're sitting, standing, or lying down. visionOS brings content to people instead of making people move to reach it — people can remain at rest.

**Accessibility.** Supports VoiceOver, Switch Control, Dwell Control, Guided Access, Head Pointer, and more.

> **Important / Safety:** Consider the device's spatial characteristics and user safety. Apple Vision Pro should not be used while operating a vehicle or heavy machinery, or while moving around unsafe environments (balconies, streets, stairs). For ages 13+.

## Best practices

Great visionOS apps are approachable and familiar, while offering extraordinary experiences.

**Embrace the unique features of Apple Vision Pro.** Take advantage of space, Spatial Audio, and immersion, while integrating passthrough and spatial input from eyes and hands.

**Consider different types of immersion.** Present experiences in a windowed UI-centric context, a fully immersive context, or something in between. **For each key moment, find the minimum level of immersion that suits it best — don't assume every moment needs to be fully immersive.**

**Use windows for contained, UI-centric experiences.** Prefer standard windows (planes in space) with familiar controls. People can relocate windows anywhere; dynamic scaling keeps content legible near or far.

**Prioritize comfort:**
- Display content within a person's **field of view**, positioned relative to their head. Avoid placing content where people must turn their head or move to interact.
- Avoid motion that's overwhelming, jarring, too fast, or missing a stationary frame of reference.
- Support **indirect gestures** that let people interact while hands rest in their lap or at their sides.
- If you support direct gestures, ensure interactive content isn't too far away and doesn't require long interaction.
- Avoid encouraging too much movement in a fully immersive experience.

**Help people share activities with others** via SharePlay + spatial Personas.

## Videos
- Principles of spatial design — wwdc2023/10072
- Design great visionOS apps — wwdc2024/10086
- Design interactive experiences for visionOS — wwdc2024/10096

## Sibling HIG pages
windows · immersive-experiences · digital-crown · eyes · gestures · accessibility · playing-audio · spatial-layout · motion · shareplay

---
### SPATAIL takeaways
- "Minimum viable immersion per moment" → our Director should pick window/volume/immersive per station, not max everything.
- Anchor content in the room, NOT to the head → world-anchored stations on an arc.
- Indirect gaze+tap is the default interaction → Tap-Reveal mechanic is the natural primitive.
- Comfort-first placement validates our existing SpatailAnalysis comfort-cone approach.
