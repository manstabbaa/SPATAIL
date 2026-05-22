// Direct manipulation — Step 4 of v0.4.
//
// Users push and drag elements in the scene. Every successful gesture
// becomes a constraint that the next call to /api/inquire respects:
//
//   { type: "fix_position", elementId, position }
//   { type: "fix_scale",    elementId, scaleFactor }
//   { type: "fix_rotation", elementId, rotationDeg }
//
// Constraints live in window.__spatail__.constraints[]. The prompt form
// includes them in its POST body so the planner can honor them. The
// constraint store is intentionally per-tab (no server persistence yet);
// the iOS app will persist its own to Documents/constraints/.
//
// This module wires:
//   - long-press → grab (visual: ring under the element brightens)
//   - drag       → translate the element along the plane below it
//   - release    → emit constraint
//
// Camera is never moved; OrbitControls is suspended while a grab is in
// flight so dragging a panel doesn't double up as a camera orbit.

import * as THREE from "three";

const GRAB_DELAY_MS = 220;   // long-press threshold
const GRAB_THRESHOLD_PX = 4;  // pixels of movement that count as "moved"
const RAYCAST_PLANE_Y = 0.001;

export function attachDirectManipulation({ renderer, camera, scene, controls,
                                            getElementsGroup, store }) {
  const ray = new THREE.Raycaster();
  const pointer = new THREE.Vector2();
  const dragPlane = new THREE.Plane(new THREE.Vector3(0, 1, 0), 0);

  let grab = null;          // { node, element, downXY, timer, controlsState }
  let didDrag = false;

  function pickElementUnder(ev) {
    const rect = renderer.domElement.getBoundingClientRect();
    pointer.x = ((ev.clientX - rect.left) / rect.width) * 2 - 1;
    pointer.y = -((ev.clientY - rect.top) / rect.height) * 2 + 1;
    ray.setFromCamera(pointer, camera);
    const group = getElementsGroup();
    if (!group) return null;
    const hits = ray.intersectObject(group, true);
    for (const h of hits) {
      let node = h.object;
      while (node && !node.userData?.elementId) node = node.parent;
      if (node?.userData?.elementId) return { node, hit: h };
    }
    return null;
  }

  function rayPlaneAtY(ev, y) {
    const rect = renderer.domElement.getBoundingClientRect();
    pointer.x = ((ev.clientX - rect.left) / rect.width) * 2 - 1;
    pointer.y = -((ev.clientY - rect.top) / rect.height) * 2 + 1;
    ray.setFromCamera(pointer, camera);
    dragPlane.constant = -y;
    const hit = new THREE.Vector3();
    ray.ray.intersectPlane(dragPlane, hit);
    return hit;
  }

  function onPointerDown(ev) {
    if (grab) cancelGrab();
    const pick = pickElementUnder(ev);
    if (!pick) return;
    grab = {
      node: pick.node,
      element: pick.node.userData.element,
      downXY: [ev.clientX, ev.clientY],
      armed: false,
      controlsState: null,
    };
    didDrag = false;
    grab.timer = setTimeout(armGrab, GRAB_DELAY_MS);
  }

  function armGrab() {
    if (!grab) return;
    grab.armed = true;
    // Lock the camera while we drag, so pinch-orbit doesn't fight us.
    grab.controlsState = controls.enabled;
    controls.enabled = false;
    // Visual cue: glow under the grabbed node.
    addGrabRing(grab.node);
  }

  function onPointerMove(ev) {
    if (!grab) return;
    const dx = ev.clientX - grab.downXY[0];
    const dy = ev.clientY - grab.downXY[1];
    if (!grab.armed) {
      if (Math.abs(dx) + Math.abs(dy) > GRAB_THRESHOLD_PX) {
        // Moved before the long-press fired — not a drag, cancel.
        cancelGrab();
      }
      return;
    }
    didDrag = true;
    const baseY = grab.node.position.y;
    const target = rayPlaneAtY(ev, baseY);
    if (!target) return;
    grab.node.position.x = target.x;
    grab.node.position.z = target.z;
  }

  function onPointerUp() {
    if (!grab) return;
    if (grab.timer) clearTimeout(grab.timer);

    if (grab.armed && didDrag) {
      const elementId = grab.element?.id;
      const p = grab.node.position;
      const constraint = {
        type: "fix_position",
        elementId,
        position: [+p.x.toFixed(3), +p.y.toFixed(3), +p.z.toFixed(3)],
        emittedAt: new Date().toISOString(),
      };
      store.push(constraint);
      // Loud-but-quiet feedback so we know it landed.
      if (typeof window !== "undefined") {
        window.dispatchEvent(new CustomEvent("spatail:constraint", { detail: constraint }));
      }
      console.log("[direct] fix_position", constraint);
    }
    cleanupAfterGrab();
  }

  function cancelGrab() { cleanupAfterGrab(); }

  function cleanupAfterGrab() {
    if (!grab) return;
    if (grab.timer) clearTimeout(grab.timer);
    if (grab.armed && grab.controlsState != null) {
      controls.enabled = grab.controlsState;
    }
    removeGrabRing(grab.node);
    grab = null;
  }

  renderer.domElement.addEventListener("pointerdown", onPointerDown);
  renderer.domElement.addEventListener("pointermove", onPointerMove);
  window.addEventListener("pointerup", onPointerUp);
}

function addGrabRing(node) {
  if (node.userData.__grabRing) return;
  const ring = new THREE.Mesh(
    new THREE.RingGeometry(0.16, 0.18, 48),
    new THREE.MeshBasicMaterial({
      color: 0x4e8aff, transparent: true, opacity: 0.85,
      side: THREE.DoubleSide,
    }),
  );
  ring.rotation.x = -Math.PI / 2;
  ring.position.y = -0.001;
  ring.renderOrder = 12;
  ring.name = "__grabRing";
  node.userData.__grabRing = ring;
  node.add(ring);
}

function removeGrabRing(node) {
  const ring = node.userData?.__grabRing;
  if (!ring) return;
  node.remove(ring);
  delete node.userData.__grabRing;
}
