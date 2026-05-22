// flow_diagram — 2D process panel with nodes, edges, and animated tokens.
// Drawn as a single canvas-textured plane that floats in the scene.
// Read by spatail.js's applyMechanics() pass.
//
// Inputs:
//   - mechanic: { kind: "flow_diagram", params: { nodes, edges, title,
//                  highlightNodeId, tokenSpeed, orientation } }
//
// Output:
//   - visualEntity: a THREE.Object3D plane mesh whose texture redraws
//     on a `tick(t)` method to animate tokens along edges.

import * as THREE from "three";

export const flowDiagramMechanic = {
  kind: "flow_diagram",

  apply({ mechanic }) {
    const params = mechanic.params || {};
    const nodes = Array.isArray(params.nodes) ? params.nodes : [];
    const edges = Array.isArray(params.edges) ? params.edges : [];
    if (nodes.length === 0) return null;

    const widthMeters = 1.6;
    const heightMeters = 0.95;
    const pxPerMeter = 480;
    const cv = document.createElement("canvas");
    cv.width = Math.round(widthMeters * pxPerMeter);
    cv.height = Math.round(heightMeters * pxPerMeter);
    const ctx = cv.getContext("2d");

    const tex = new THREE.CanvasTexture(cv);
    tex.colorSpace = THREE.SRGBColorSpace;
    tex.anisotropy = 4;
    const geom = new THREE.PlaneGeometry(widthMeters, heightMeters);
    const mat = new THREE.MeshBasicMaterial({ map: tex, transparent: true,
                                              side: THREE.DoubleSide });
    const mesh = new THREE.Mesh(geom, mat);
    mesh.name = `mechanic.flow_diagram.${mechanic.id}`;

    // Layout nodes left → right (or top → bottom).
    const orientation = params.orientation === "tb" ? "tb" : "lr";
    const pad = 36;
    const titleH = params.title ? 64 : 0;
    const innerW = cv.width - pad * 2;
    const innerH = cv.height - pad * 2 - titleH;
    const positions = nodes.map((n, i) => {
      const t = nodes.length > 1 ? i / (nodes.length - 1) : 0.5;
      if (orientation === "lr") {
        return { node: n, x: pad + t * innerW, y: pad + titleH + innerH * 0.55 };
      }
      return { node: n, x: pad + innerW * 0.5, y: pad + titleH + t * innerH };
    });
    const byId = new Map(positions.map((p) => [p.node.id, p]));
    const highlightId = params.highlightNodeId || null;
    const tokenColor = params.tokenColor || "#6ea8ff";
    const tokenSpeed = Number.isFinite(params.tokenSpeed) ? params.tokenSpeed : 0.8;

    // Each edge gets one token whose progress 0..1 is driven from the
    // global clock; tokens stagger by edge index so the eye reads the
    // chain of motion.
    const tokens = edges.map((e, i) => ({
      edgeIndex: i,
      // initial offset so all tokens aren't co-located
      offset: i * 0.2,
    }));

    function draw(time) {
      // Background card
      const radius = 24;
      ctx.fillStyle = "rgba(20, 24, 32, 0.96)";
      roundRect(ctx, 0, 0, cv.width, cv.height, radius); ctx.fill();
      ctx.strokeStyle = "rgba(110,168,255,0.35)";
      ctx.lineWidth = 3;
      roundRect(ctx, 1.5, 1.5, cv.width - 3, cv.height - 3, radius); ctx.stroke();

      // Title
      if (params.title) {
        ctx.fillStyle = "#e6e8ee";
        ctx.font = "600 26px ui-sans-serif, system-ui, sans-serif";
        ctx.textBaseline = "top";
        ctx.fillText(params.title, pad, pad);
      }

      // Edges
      ctx.strokeStyle = "rgba(255,255,255,0.35)";
      ctx.lineWidth = 2;
      for (const e of edges) {
        const a = byId.get(e.from), b = byId.get(e.to);
        if (!a || !b) continue;
        ctx.beginPath();
        ctx.moveTo(a.x, a.y);
        ctx.bezierCurveTo(
          a.x + (b.x - a.x) * 0.5, a.y,
          a.x + (b.x - a.x) * 0.5, b.y,
          b.x, b.y,
        );
        ctx.stroke();
      }

      // Tokens
      for (const tok of tokens) {
        const e = edges[tok.edgeIndex];
        if (!e) continue;
        const a = byId.get(e.from), b = byId.get(e.to);
        if (!a || !b) continue;
        const t = ((time * tokenSpeed) + tok.offset) % 1.0;
        const x = bezierPoint(a.x, a.x + (b.x - a.x) * 0.5,
                              a.x + (b.x - a.x) * 0.5, b.x, t);
        const y = bezierPoint(a.y, a.y,                  b.y,                  b.y, t);
        ctx.fillStyle = tokenColor;
        ctx.beginPath();
        ctx.arc(x, y, 7, 0, Math.PI * 2);
        ctx.fill();
      }

      // Nodes
      for (const p of positions) {
        const isHighlight = p.node.id === highlightId;
        const label = p.node.label || p.node.id;
        const subtitle = p.node.subtitle;
        const w = Math.max(160, ctx.measureText(label).width + 56);
        const h = subtitle ? 84 : 56;
        const x = p.x - w / 2, y = p.y - h / 2;

        ctx.fillStyle = isHighlight ? "rgba(245,185,66,0.18)" : "rgba(110,168,255,0.10)";
        roundRect(ctx, x, y, w, h, 14); ctx.fill();
        ctx.strokeStyle = isHighlight ? "rgba(245,185,66,0.9)" : "rgba(110,168,255,0.65)";
        ctx.lineWidth = isHighlight ? 3 : 2;
        roundRect(ctx, x + 0.5, y + 0.5, w - 1, h - 1, 14); ctx.stroke();

        ctx.fillStyle = "#e6e8ee";
        ctx.font = "600 18px ui-sans-serif, system-ui, sans-serif";
        ctx.textBaseline = "top";
        ctx.textAlign = "center";
        ctx.fillText(label, p.x, y + 12);
        if (subtitle) {
          ctx.fillStyle = "rgba(255,255,255,0.65)";
          ctx.font = "500 13px ui-sans-serif, system-ui, sans-serif";
          wrapCentered(ctx, subtitle, p.x, y + 38, w - 16, 16);
        }
        ctx.textAlign = "left";
      }

      tex.needsUpdate = true;
    }

    draw(0);

    // Attach a tick callback the render loop can call.
    mesh.userData.tick = (t) => draw(t);
    return { visualEntity: mesh, kind: "flow_diagram" };
  },
};

function roundRect(ctx, x, y, w, h, r) {
  ctx.beginPath();
  ctx.moveTo(x + r, y);
  ctx.arcTo(x + w, y, x + w, y + h, r);
  ctx.arcTo(x + w, y + h, x, y + h, r);
  ctx.arcTo(x, y + h, x, y, r);
  ctx.arcTo(x, y, x + w, y, r);
  ctx.closePath();
}

function bezierPoint(p0, p1, p2, p3, t) {
  const it = 1 - t;
  return it * it * it * p0 + 3 * it * it * t * p1 + 3 * it * t * t * p2 + t * t * t * p3;
}

function wrapCentered(ctx, text, cx, y, maxWidth, lineHeight) {
  const words = String(text || "").split(/\s+/);
  let line = "", yy = y;
  for (const w of words) {
    const test = line ? line + " " + w : w;
    if (ctx.measureText(test).width > maxWidth && line) {
      ctx.fillText(line, cx, yy);
      yy += lineHeight;
      line = w;
    } else line = test;
  }
  if (line) ctx.fillText(line, cx, yy);
}
