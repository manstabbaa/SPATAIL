// cross_section — clips a target 3D object with a clipping plane and
// adds a tinted ring along the cut for emphasis. Read by spatail.js's
// applyMechanics() pass.
//
// Inputs:
//   - mechanic: { kind: "cross_section", target, params }
//   - ctx: { elementsById, renderer, scene, getMeshForElement(id) }
//
// Side effects:
//   - Sets renderer.localClippingEnabled = true (idempotent).
//   - For every Mesh under the target Object3D, adds the clipping plane
//     to material.clippingPlanes. Stores the original list on
//     mat.userData._spatail_clipBackup so reset is possible.
//   - Returns a `{ visualEntity }` that the caller adds to the scene.

import * as THREE from "three";

export const crossSectionMechanic = {
  kind: "cross_section",

  apply({ mechanic, ctx }) {
    if (!ctx?.renderer || !ctx?.scene) return null;
    ctx.renderer.localClippingEnabled = true;

    const targetMesh = ctx.getMeshForElement?.(mechanic.target);
    if (!targetMesh) {
      console.warn(`[cross_section] target '${mechanic.target}' not in scene`);
      return null;
    }

    const axis = (mechanic.params?.axis || "z").toLowerCase();
    const offset = Number.isFinite(mechanic.params?.offset) ? mechanic.params.offset : 0.0;
    const normal = axis === "x" ? new THREE.Vector3(1, 0, 0)
                  : axis === "y" ? new THREE.Vector3(0, 1, 0)
                  :                new THREE.Vector3(0, 0, 1);

    // Plane equation: dot(n, p) + constant = 0. Geometry on the +n side
    // is kept; the -n side is clipped. So `constant = +offset` clips
    // away geometry where (n · p) < -offset.
    const plane = new THREE.Plane(normal.clone(), offset);

    let materialsTouched = 0;
    targetMesh.traverse((obj) => {
      if (!obj.isMesh) return;
      const list = Array.isArray(obj.material) ? obj.material : [obj.material];
      for (const m of list) {
        if (!m) continue;
        if (!m.userData._spatail_clipBackup) {
          m.userData._spatail_clipBackup = m.clippingPlanes ? [...m.clippingPlanes] : [];
        }
        m.clippingPlanes = [plane];
        m.clipShadows = true;
        m.side = THREE.DoubleSide;
        m.needsUpdate = true;
        materialsTouched += 1;
      }
    });

    // Cut-line emphasis — a coloured ring placed exactly on the
    // clipping plane, sized to the target's bounding box.
    const tint = new THREE.Color(mechanic.params?.sectionTint || "#6ea8ff");
    const box = new THREE.Box3().setFromObject(targetMesh);
    const size = new THREE.Vector3();
    box.getSize(size);
    const center = new THREE.Vector3();
    box.getCenter(center);
    const w = axis === "x" ? size.z : size.x;
    const h = axis === "y" ? size.z : size.y;
    const ringGeom = new THREE.RingGeometry(0, Math.max(w, h, 0.05) * 0.55, 64);
    const ringMat = new THREE.MeshBasicMaterial({
      color: tint, transparent: true, opacity: 0.16,
      side: THREE.DoubleSide,
    });
    const ring = new THREE.Mesh(ringGeom, ringMat);
    // Align ring's +Z with the plane normal.
    const up = new THREE.Vector3(0, 0, 1);
    const q = new THREE.Quaternion().setFromUnitVectors(up, normal);
    ring.quaternion.copy(q);
    // Position the ring on the plane: plane is `normal · p + offset = 0`
    // so p = -offset * normal (in target-local space).
    ring.position.copy(center).addScaledVector(normal, -offset);

    // Outline along the section silhouette via a slightly larger ring
    // with thin opacity — reads as a glowing edge on the cut.
    const outlineGeom = new THREE.RingGeometry(
      Math.max(w, h, 0.05) * 0.55,
      Math.max(w, h, 0.05) * 0.58,
      64,
    );
    const outlineMat = new THREE.MeshBasicMaterial({
      color: tint, transparent: true, opacity: 0.85,
      side: THREE.DoubleSide,
    });
    const outline = new THREE.Mesh(outlineGeom, outlineMat);
    outline.quaternion.copy(q);
    outline.position.copy(ring.position);

    const group = new THREE.Group();
    group.name = `mechanic.cross_section.${mechanic.id}`;
    group.add(ring);
    group.add(outline);
    group.renderOrder = 999;
    // Match the hero's world transform so the ring sits exactly on the cut.
    targetMesh.getWorldPosition(new THREE.Vector3());
    group.position.copy(targetMesh.position);
    group.rotation.copy(targetMesh.rotation);

    console.log(`[cross_section] target=${mechanic.target} axis=${axis} offset=${offset} ` +
                `materials=${materialsTouched}`);

    return { visualEntity: group, kind: "cross_section" };
  },
};
