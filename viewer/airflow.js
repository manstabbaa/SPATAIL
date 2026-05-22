// airflow_field renderer — animated streamlines around a hero mesh.
//
// The contract's airflow source provides `streams[]`, each with `from`,
// `to`, optional `via` waypoints, all in local hero coords. We sample
// the polyline, advect a flock of glowing dots along it, and re-emit
// them at the head when they reach the tail. Two regimes can coexist
// (DRS open / closed) toggled via a `select` interaction.
//
// Content-agnostic: any prompt that needs to show invisible-fluid flow
// (airflow over a car, water in a pipe, blood through a vessel) can
// emit `airflow` sources with the same shape.

import * as THREE from "three";

const DOT_COUNT_PER_STREAM = 14;
const DOT_SPEED = 0.6;          // unit-fractions per second
const DOT_RADIUS = 0.012;

export function renderAirflowField(el, { resolveHeroMesh }) {
  const group = new THREE.Group();
  group.name = `airflow.${el.id}`;

  const streams = el.sourceContent?.streams || [];
  if (streams.length === 0) {
    return group;
  }

  // Resolve hero so we can drive coordinates through its auto-fit ratio
  // and world position. Without a hero, the lines are still drawn but
  // anchored to the airflow element's own placement.
  const hero = resolveHeroMesh?.();
  const heroRatio = hero?.userData?._autoFitRatio ?? 1.0;

  const accent = el.sourceContent?.regime === "drs_open"
    ? 0xf5b942   // amber — high-speed straight-line flow
    : 0x6ea8ff;  // blue — high-downforce flow

  const lineMat = new THREE.LineBasicMaterial({
    color: accent, transparent: true, opacity: 0.45,
  });

  const dotMat = new THREE.MeshBasicMaterial({
    color: accent, transparent: true, opacity: 0.9,
  });
  const dotGeo = new THREE.SphereGeometry(DOT_RADIUS, 10, 10);

  // For each stream: a faint static line + a flock of moving dots.
  const flocks = [];
  for (const s of streams) {
    const pts = waypoints(s, heroRatio);
    if (pts.length < 2) continue;

    // Static guide line — faint, sets the silhouette of the airflow.
    const line = new THREE.Line(
      new THREE.BufferGeometry().setFromPoints(pts),
      lineMat,
    );
    group.add(line);

    // Per-stream curve we'll sample for dots.
    const curve = new THREE.CatmullRomCurve3(pts, false, "centripetal", 0.5);
    const flock = {
      curve,
      dots: [],
    };
    for (let i = 0; i < DOT_COUNT_PER_STREAM; i++) {
      const dot = new THREE.Mesh(dotGeo, dotMat);
      dot.userData.__t = i / DOT_COUNT_PER_STREAM;
      flock.dots.push(dot);
      group.add(dot);
    }
    flocks.push(flock);
  }

  // Per-frame tick — read by spatail.js' render loop via userData.tick.
  let last = performance.now() / 1000;
  group.userData.tick = (_t) => {
    const now = performance.now() / 1000;
    const dt = Math.min(0.1, now - last);
    last = now;
    for (const f of flocks) {
      for (const dot of f.dots) {
        dot.userData.__t = (dot.userData.__t + DOT_SPEED * dt) % 1.0;
        const p = f.curve.getPoint(dot.userData.__t);
        dot.position.copy(p);
      }
    }
  };

  group.position.set(0, 0, 0);  // streams are in world-space already
  return group;
}

function waypoints(stream, heroRatio) {
  const pts = [];
  const push = (v) => {
    if (!Array.isArray(v) || v.length < 3) return;
    pts.push(new THREE.Vector3(
      v[0] * heroRatio,
      v[1] * heroRatio,
      v[2] * heroRatio,
    ));
  };
  push(stream.from);
  if (Array.isArray(stream.via)) for (const v of stream.via) push(v);
  push(stream.to);
  return pts;
}
