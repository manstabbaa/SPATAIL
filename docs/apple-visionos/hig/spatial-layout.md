# Spatial Layout (HIG)
Source: https://developer.apple.com/design/human-interface-guidelines/spatial-layout
Captured verbatim for SPATAIL. THE key page for placement/comfort numbers.

## Field of view
A person's **field of view** is the space they can see without moving their head. Dimensions vary per individual (Light Seal fit, peripheral acuity). **The system does not provide info about a person's field of view.**

- **Center important content within the field of view.** visionOS launches an app directly in front of people. In immersive experiences, keep important content centered; avoid distracting motion or bright, high-contrast objects in the periphery.
- **Avoid anchoring content to the wearer's head.** Statically head-locked content makes people feel stuck, confined, uncomfortable — especially if it obscures passthrough and reduces apparent stability of surroundings. **Instead, anchor content in people's space**, giving freedom to look around naturally.

## Depth
People rely on distance, occlusion, and shadow to perceive depth. The system uses color temperature, reflections, and shadow to convey depth; effects change as people move objects or themselves.

- Incorporate small amounts of depth throughout the interface — even standard windows — to look natural.
- For more depth, use RealityKit to make a 3D object; display it anywhere or in a **volume** (3D content component, like a window without a visible frame).
- **Provide visual cues that accurately communicate depth** — missing/conflicting cues cause discomfort.
- **Use depth to communicate hierarchy** (e.g. a sheet comes forward, the window recedes along z).
- **Avoid adding depth to text** — hovering text is hard to read and can cause discomfort.
- **Make sure depth adds value** — refocusing eyes for every depth difference too often is tiring. Use depth to separate large important elements, not small objects.

## Scale
visionOS defines two scale types:

- **Dynamic scale** — content stays comfortably legible/interactive regardless of proximity. visionOS automatically increases a window's scale as it moves away and decreases it as it nears, so it appears the same size at all distances. (A point is defined as an *angle*, not pixels.)
- **Fixed scale** — object maintains the same real scale regardless of proximity; appears smaller when farther (like a real object). Use when a virtual object should look exactly like a physical object (e.g. life-size product). Apply sparingly; reserve for non-interactive objects.

## Best practices (with NUMBERS)
- **Avoid displaying too many windows** — obscures surroundings, feels overwhelming, cumbersome to relocate.
- **Prioritize standard, indirect gestures** — indirect = no need to move hand into view; works on any object you look at, any distance. Reserve direct gestures for nearby objects, short manipulation.
- **Digital Crown recenters** content — your app needs to do nothing to support it.
- **Include enough space around interactive components.** Looking at an element shows a hover effect.
  - **Place regular-size buttons so their centers are at least 60 points apart.**
  - **Leave 16 points or more of space between them.**
  - Don't let controls overlap other interactive elements/views.
- **Let people use your app with minimal or no physical movement** unless movement is essential.
- **Use the floor to place a large immersive experience.** Content extending up from the floor should be placed on a flat horizontal plane aligned with the floor, to blend seamlessly.

## Platform
Not supported in iOS, iPadOS, macOS, tvOS, watchOS (visionOS-only page — but the comfort principles inform our iOS AR placement too).

---
### SPATAIL takeaways (placement engine)
- 60pt-centers / 16pt-gap → minimum spacing rule for our spatial UI panels & station controls.
- World-anchored, floor-aligned, centered-in-FOV → exactly the arc+comfort-cone model in SpatailAnalysis.
- Dynamic vs fixed scale → our Director's "is this a real-size object or a UI element?" decision.
- "Don't add depth to text" → keep panel text flat; use depth only to separate stations/hero objects.
