// Viewer — Three.js scene, GLB loader, camera, part registry, and
// the imperative API the contract player calls into.

import * as THREE from "three";
import { GLTFLoader } from "three/addons/loaders/GLTFLoader.js";
import { RGBELoader } from "three/addons/loaders/RGBELoader.js";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";

const TAU = Math.PI * 2;

// Curated camera presets keyed by the contract's CameraPose.preset field.
// World coordinates match the V8 GLB authoring: Y is up, the engine sits
// with its base on the y=0 ground plane. Measured bbox after load:
//   size  ≈ [0.75 (X width), 0.508 (Y height), 0.604 (Z depth)]
//   center≈ [0, 0.254, 0]
// All presets target the engine center and stand back ~1.5–1.7 units so a
// 30°-ish FOV frames the entire engine with a small margin on a ~1:1 canvas.
const CAMERA_PRESETS = {
  hero_threequarter: { from: [1.10, 0.70, 1.10], to: [0.0, 0.25, 0.0], fov: 32 },
  hero_front:        { from: [0.00, 0.35, 1.70], to: [0.0, 0.25, 0.0], fov: 32 },
  topdown:           { from: [0.00, 1.60, 0.01], to: [0.0, 0.25, 0.0], fov: 32 },
  section_side:      { from: [1.60, 0.30, 0.00], to: [0.0, 0.25, 0.0], fov: 32 },
  cylinder_close:    { from: [0.50, 0.70, 0.90], to: [0.0, 0.45, 0.0], fov: 30 },
};

export class Viewer {
  constructor(canvas) {
    this.canvas = canvas;

    // preserveDrawingBuffer:true so canvas.toDataURL() works for the visual
    // validator. ~5% perf cost; worth it.
    this.renderer = new THREE.WebGLRenderer({ canvas, antialias: true, alpha: false, preserveDrawingBuffer: true });
    this.renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    this.renderer.setSize(canvas.clientWidth, canvas.clientHeight, false);
    this.renderer.outputColorSpace = THREE.SRGBColorSpace;
    this.renderer.toneMapping = THREE.ACESFilmicToneMapping;
    this.renderer.toneMappingExposure = 1.1;

    this.scene = new THREE.Scene();
    // Cream paper to match the SPATAIL design system. The 3D scene sits in
    // a paper-toned card on the page, not over a dark void. Halo + highlight
    // colors are tuned (in the halo material) to read against this surface.
    this.scene.background = new THREE.Color(0xF5F4EF);

    this.camera = new THREE.PerspectiveCamera(30, canvas.clientWidth / canvas.clientHeight, 0.05, 60);
    this.applyCameraPose(CAMERA_PRESETS.hero_threequarter);

    this.controls = new OrbitControls(this.camera, this.renderer.domElement);
    this.controls.enableDamping = true;
    this.controls.dampingFactor = 0.08;
    // Aim controls at the engine center (Y-up). Must match the active preset.
    this.controls.target.set(...CAMERA_PRESETS.hero_threequarter.to);
    this.controls.update();

    this._buildLights();

    // Part registry — populated when a GLB is loaded.
    // Maps stable part id → { object3d, originalMaterial, originalVisible }
    this.parts = new Map();

    // Animation mixer + named actions keyed by animation id.
    this.mixer = null;
    this.actions = new Map();
    this.clock = new THREE.Clock();

    // Active camera preset table. Defaults to the engine-tuned CAMERA_PRESETS
    // at construction; loadAsset() can swap in a per-asset override.
    this._activePresets = CAMERA_PRESETS;
    this._loadedRoot = null;

    // Outline / highlight pass: simple per-material approach for the prototype.
    // (Postprocessing OutlinePass would be nicer; deferred to avoid extra deps.)
    this._highlighted = new Set();
    // Halo sprites — a 2D billboard placed at each highlighted part's world
    // position so the viewer always has a clear "lock-on" cue, even when the
    // underlying mesh fails to render (this V8 GLB has at least one piston
    // mesh that refuses to draw despite valid geometry / material settings).
    this._haloSprites = new Map();   // part_id (or "region:<id>") → THREE.Sprite
    this._haloTexture = null;        // lazily built radial-gradient texture
    // Sub-mesh regions (from a regions.json sidecar). id → record. A region's
    // baked overlay mesh travels inside the GLB, so it is already in the
    // viewer's coordinate frame — that's the authoritative highlight path.
    this._regions = new Map();
    this._shownRegionOverlays = new Set();  // overlay part ids made visible
    // Canvas-rendered label sprites — mirrors the HTML overlay labels so they
    // survive canvas.toDataURL() capture (the DOM layer is invisible to that).
    this._labelSprites = new Map();  // label key → THREE.Sprite

    window.addEventListener("resize", () => this._onResize());

    this._tick = this._tick.bind(this);
    this._tick();
  }

  _buildLights() {
    // Daylight setup tuned for the cream-paper background. We want soft,
    // even illumination so the part reads as a clean technical object on
    // paper — not a cinematic hero with deep shadows.
    const hemi = new THREE.HemisphereLight(0xFFFFFF, 0xD8D5C8, 1.05);
    this.scene.add(hemi);

    const key = new THREE.DirectionalLight(0xFFFFFF, 1.6);
    key.position.set(2.0, 3.0, 2.5);
    this.scene.add(key);

    const fill = new THREE.DirectionalLight(0xFFFFFF, 0.55);
    fill.position.set(-2.0, 1.5, 1.8);
    this.scene.add(fill);

    const rim = new THREE.DirectionalLight(0xFFE6B0, 0.35);
    rim.position.set(-0.8, 2.2, -1.5);
    this.scene.add(rim);
  }

  _onResize() {
    const w = this.canvas.clientWidth, h = this.canvas.clientHeight;
    this.renderer.setSize(w, h, false);
    this.camera.aspect = w / h;
    this.camera.updateProjectionMatrix();
  }

  _tick() {
    requestAnimationFrame(this._tick);
    const dt = this.clock.getDelta();
    if (this.mixer) this.mixer.update(dt);
    this.controls.update();
    this.renderer.render(this.scene, this.camera);
  }

  // -------------------------------------------------------------------
  // Loading
  // -------------------------------------------------------------------

  /** Drop the currently-loaded asset (root group + actions + parts +
   *  halos + labels) so the viewer is a clean slate for the next loadAsset.
   *  Called automatically by loadAsset(); safe to call when nothing is loaded. */
  _unloadAsset() {
    // Stop + clear any baked animations
    if (this.mixer) {
      try { this.mixer.stopAllAction(); } catch (_) {}
      this.mixer = null;
    }
    this.actions = new Map();
    // Drop halos + labels
    if (typeof this._clearHalos === "function") this._clearHalos();
    if (typeof this.clearCanvasLabels === "function") this.clearCanvasLabels();
    this._highlighted = new Set();
    // Force the next asset's halo size to recompute against the new bbox
    this._assetDiag = null;
    // Remove the GLB scene root from the THREE scene
    if (this._loadedRoot) {
      this.scene.remove(this._loadedRoot);
      // Dispose meshes + materials to free GPU memory
      this._loadedRoot.traverse((o) => {
        if (o.isMesh) {
          o.geometry?.dispose?.();
          const mats = Array.isArray(o.material) ? o.material : [o.material];
          for (const m of mats) m?.dispose?.();
        }
      });
      this._loadedRoot = null;
    }
    this.parts = new Map();
  }

  async loadAsset(url, opts = {}) {
    // Hot-swap support: drop any previously loaded asset (scene + actions +
    // parts + halos + labels) so consecutive loadAsset() calls don't pile up.
    this._unloadAsset();
    // Allow the caller to swap in per-asset camera presets (small assets
    // like a 60mm fan need much tighter framing than the 75cm engine).
    if (opts.cameraOverride) {
      this._activePresets = { ...CAMERA_PRESETS, ...opts.cameraOverride };
    } else {
      this._activePresets = CAMERA_PRESETS;
    }
    // Snap the camera to the active hero pose immediately so the load
    // doesn't reveal the OLD asset's framing for a frame.
    this.applyCameraPose(this._activePresets.hero_threequarter);

    const loader = new GLTFLoader();
    const gltf = await loader.loadAsync(url);
    this.scene.add(gltf.scene);
    this._loadedRoot = gltf.scene;

    // Build the part registry from object names. The Blender authoring
    // stage is responsible for naming things stably (piston_1A, rod_1A,
    // crank_throw_1, ...). We accept anything with a non-empty name.
    //
    // CRITICAL: clone material per-mesh at load time. Many V8 parts share
    // a material instance in this GLB (saves memory at author time). If
    // we let them share, then any per-part visual change (highlight,
    // dim_others except-set, etc.) bleeds across hundreds of unrelated
    // parts — we saw the entire engine going uniformly purple when only
    // piston_1A should have been highlighted. The clone costs ~3-5 MB
    // for 673 PBR materials but unlocks selective control.
    gltf.scene.traverse((o) => {
      if (!o.isMesh) return;
      const id = o.userData?.partId || o.name;
      if (!id) return;
      if (o.material && !o.material.userData?.__perPartClone) {
        const cloned = o.material.clone();
        cloned.userData = { ...(cloned.userData || {}), __perPartClone: true };
        o.material = cloned;
      }
      this.parts.set(id, {
        id,
        object: o,
        originalVisible: o.visible,
        originalMaterial: o.material,
        // Seated position as authored in the GLB. Captured ONCE at load so the
        // assembly rest pose is invariant — setAssembly() must never re-snapshot
        // from a live (possibly exploded) position, or rest drifts each scrub.
        originalPosition: o.position.clone(),
      });
    });

    // Animations: each gltf.animations[i] becomes a named action.
    if (gltf.animations?.length) {
      this.mixer = new THREE.AnimationMixer(gltf.scene);
      for (const clip of gltf.animations) {
        this.actions.set(clip.name, this.mixer.clipAction(clip));
      }
    }

    return gltf;
  }

  installPlaceholder() {
    // Friendly placeholder when the GLB isn't authored yet.
    const geo = new THREE.IcosahedronGeometry(0.35, 1);
    const mat = new THREE.MeshStandardMaterial({
      color: 0x5046E5, metalness: 0.4, roughness: 0.3,
      emissive: 0x5046E5, emissiveIntensity: 0.2, wireframe: true,
    });
    const m = new THREE.Mesh(geo, mat);
    m.name = "placeholder";
    m.position.set(0, 0, 0.3);
    this.scene.add(m);
    this.parts.set("placeholder", { id: "placeholder", object: m, originalVisible: true, originalMaterial: mat });
  }

  // -------------------------------------------------------------------
  // Part queries
  // -------------------------------------------------------------------

  /** Resolve a PartRef (string id OR glob like "piston_*") to an array of part records. */
  resolveTargets(ref) {
    if (Array.isArray(ref)) return ref.flatMap((r) => this.resolveTargets(r));
    if (typeof ref !== "string") return [];
    if (!ref.includes("*")) {
      const p = this.parts.get(ref);
      return p ? [p] : [];
    }
    const re = new RegExp("^" + ref.replace(/[.+^${}()|[\]\\]/g, "\\$&").replace(/\*/g, ".*") + "$");
    return [...this.parts.values()].filter((p) => re.test(p.id));
  }

  // -------------------------------------------------------------------
  // Contract actions — imperative API the player calls
  // -------------------------------------------------------------------

  setVisible(targets, visible) {
    for (const p of this.resolveTargets(targets)) p.object.visible = visible;
  }

  /** Hide EVERY part except the ones listed. The way to actually expose
   *  internals on a 673-part CAD where the "shell" list is incomplete. */
  showOnly(targets) {
    const keep = new Set(this.resolveTargets(targets).map((p) => p.id));
    for (const p of this.parts.values()) {
      p.object.visible = keep.has(p.id);
    }
  }

  resetVisibility() {
    for (const p of this.parts.values()) p.object.visible = p.originalVisible;
  }

  /** Build (once) a soft radial-gradient halo texture, white core fading to
   *  zero alpha at the edge. We tint per-sprite via Sprite material.color so
   *  one texture serves all highlight colors. */
  _ensureHaloTexture() {
    if (this._haloTexture) return this._haloTexture;
    const size = 128;
    const cv = document.createElement("canvas");
    cv.width = cv.height = size;
    const ctx = cv.getContext("2d");
    const g = ctx.createRadialGradient(size / 2, size / 2, 2, size / 2, size / 2, size / 2);
    g.addColorStop(0.00, "rgba(255,255,255,1.0)");
    g.addColorStop(0.30, "rgba(255,255,255,0.55)");
    g.addColorStop(0.65, "rgba(255,255,255,0.15)");
    g.addColorStop(1.00, "rgba(255,255,255,0.0)");
    ctx.fillStyle = g;
    ctx.fillRect(0, 0, size, size);
    const tex = new THREE.CanvasTexture(cv);
    tex.colorSpace = THREE.SRGBColorSpace;
    this._haloTexture = tex;
    return tex;
  }

  /** Add or refresh a halo sprite at `part`'s world position. */
  _addHalo(part, color, intensity = 1.0) {
    const box = new THREE.Box3().setFromObject(part.object);
    if (box.isEmpty()) return;
    const center = box.getCenter(new THREE.Vector3());
    const diag = box.getSize(new THREE.Vector3()).length();
    // Halo size is anchored to the LOADED ASSET'S scale so a 3cm part on a
    // 6cm fan doesn't get the same halo as a 3cm part on a 750mm engine
    // (which would overflow the fan entirely). Compute the asset's bbox
    // diagonal once per asset load and cache it; halo is clamped to a
    // fraction of that so it always reads as a focal mark, never floods
    // the whole asset.
    if (!this._assetDiag) {
      const assetBox = new THREE.Box3();
      if (this._loadedRoot) assetBox.setFromObject(this._loadedRoot);
      this._assetDiag = assetBox.isEmpty() ? 1.0 : assetBox.getSize(new THREE.Vector3()).length();
    }
    const aMin = this._assetDiag * 0.04;   // never smaller than ~4% of asset
    const aMax = this._assetDiag * 0.35;   // never bigger than ~35% of asset
    const haloSize = Math.min(aMax, Math.max(aMin, diag * 2.5));
    let sprite = this._haloSprites.get(part.id);
    if (!sprite) {
      // NORMAL blending (not additive) so the halo reads strongly against
      // the cream paper background — additive on light backgrounds just
      // washes out to invisible white.
      const mat = new THREE.SpriteMaterial({
        map: this._ensureHaloTexture(),
        color: new THREE.Color(color),
        transparent: true,
        opacity: Math.min(1.0, 0.92 * intensity),
        depthTest: false,
        depthWrite: false,
        blending: THREE.NormalBlending,
      });
      sprite = new THREE.Sprite(mat);
      sprite.renderOrder = 9999;
      this.scene.add(sprite);
      this._haloSprites.set(part.id, sprite);
    } else {
      sprite.material.color.set(color);
      sprite.material.opacity = Math.min(1.0, 0.92 * intensity);
    }
    sprite.position.copy(center);
    sprite.scale.set(haloSize, haloSize, 1);
  }

  /** Build a label-card texture (kicker + title) and return a fresh Sprite
   *  positioned above the given world point. The sprite lives in 3D space
   *  but always faces the camera, so the label "sticks" to its part and is
   *  visible in canvas.toDataURL() captures (DOM labels are not). */
  addCanvasLabel(key, worldPos, { text, kicker = null, offsetY = null } = {}) {
    // Label cards scale with the asset so a 6cm fan and a 75cm engine both
    // get a card sized to their geometry (a fixed 0.22 card is ~2.5× a fan).
    if (!this._assetDiag) {
      const assetBox = new THREE.Box3();
      if (this._loadedRoot) assetBox.setFromObject(this._loadedRoot);
      this._assetDiag = assetBox.isEmpty() ? 1.0 : assetBox.getSize(new THREE.Vector3()).length();
    }
    const padX = 22, padY = 14, w = 360, h = kicker ? 92 : 64;
    const cv = document.createElement("canvas");
    cv.width = w; cv.height = h;
    const ctx = cv.getContext("2d");
    // Cream card with hairline border + indigo left rule —
    // matches the SPATAIL design system .part-label / .explanation-card.
    const r = 14;
    ctx.fillStyle = "rgba(245,244,239,0.96)";
    ctx.beginPath();
    ctx.moveTo(r, 0);
    ctx.lineTo(w - r, 0); ctx.quadraticCurveTo(w, 0, w, r);
    ctx.lineTo(w, h - r); ctx.quadraticCurveTo(w, h, w - r, h);
    ctx.lineTo(r, h); ctx.quadraticCurveTo(0, h, 0, h - r);
    ctx.lineTo(0, r); ctx.quadraticCurveTo(0, 0, r, 0);
    ctx.closePath(); ctx.fill();
    // Hairline border
    ctx.strokeStyle = "rgba(10,10,15,0.18)";
    ctx.lineWidth = 1;
    ctx.stroke();
    // Indigo accent bar on left
    ctx.fillStyle = "#5046E5";
    ctx.fillRect(0, 0, 4, h);
    if (kicker) {
      ctx.fillStyle = "#5046E5";
      ctx.font = "500 20px 'Geist Mono', ui-monospace, Menlo, monospace";
      ctx.fillText(kicker.toUpperCase(), padX, padY + 18);
    }
    ctx.fillStyle = "#0A0A0F";
    ctx.font = "500 30px Geist, system-ui, -apple-system, sans-serif";
    ctx.fillText(text, padX, kicker ? padY + 56 : padY + 36);
    const tex = new THREE.CanvasTexture(cv);
    tex.colorSpace = THREE.SRGBColorSpace;
    tex.needsUpdate = true;
    const mat = new THREE.SpriteMaterial({
      map: tex, transparent: true, depthTest: false, depthWrite: false,
    });
    const sprite = new THREE.Sprite(mat);
    sprite.renderOrder = 10000;
    // 2D label width in world units, scaled to the asset (≈0.22 at engine
    // scale, ≈0.026 on the fan) so the card always reads next to its part.
    const labelW = this._assetDiag * 0.30;
    const dy = offsetY == null ? this._assetDiag * 0.10 : offsetY;
    sprite.scale.set(labelW, labelW * (h / w), 1);
    sprite.position.set(worldPos[0], worldPos[1] + dy, worldPos[2]);
    // Replace any prior label with the same key
    const prev = this._labelSprites.get(key);
    if (prev) { this.scene.remove(prev); prev.material.map?.dispose(); prev.material.dispose(); }
    this.scene.add(sprite);
    this._labelSprites.set(key, sprite);
    return sprite;
  }

  /** Remove all canvas-rendered labels. */
  clearCanvasLabels() {
    for (const sprite of this._labelSprites.values()) {
      this.scene.remove(sprite);
      sprite.material.map?.dispose();
      sprite.material.dispose();
    }
    this._labelSprites.clear();
  }

  /** Remove all halo sprites. */
  _clearHalos() {
    for (const sprite of this._haloSprites.values()) {
      this.scene.remove(sprite);
      sprite.material.dispose();
    }
    this._haloSprites.clear();
  }

  highlight(targets, { color = "#5046E5", intensity = 1.0 } = {}) {
    const c = new THREE.Color(color);
    for (const p of this.resolveTargets(targets)) {
      let m = p.object.material;
      if (!m) continue;
      // CRITICAL: many V8 parts share material instances in this GLB.
      // Tinting one material would tint hundreds of unrelated parts (we
      // saw the whole engine going uniformly purple). Clone the material
      // the first time we touch it so the changes are local to this part.
      if (!p._materialCloned) {
        const orig = m;
        m = m.clone();
        p.object.material = m;
        p._materialCloned = true;
        p._originalSharedMaterial = orig;  // keep a ref for full reset
      }
      // Snapshot the *cloned* material's starting values so reset restores
      // the look without leaking back to the shared one.
      if (!p._emissiveSnap) {
        p._emissiveSnap = {
          color: m.emissive ? m.emissive.clone() : null,
          intensity: m.emissiveIntensity ?? 0,
          diffuse: m.color ? m.color.clone() : null,
          opacity: m.opacity ?? 1.0,
        };
      }
      if (m.emissive) m.emissive.copy(c);
      // Strong emissive so a tiny part (e.g. a 3cm piston ring) reads
      // against a translucent-shell X-ray engine. 0.65 was too subtle —
      // the visual validator kept reporting "no specific piston stands out".
      m.emissiveIntensity = 2.2 * intensity;
      // Tint the diffuse toward the highlight color too, so the part looks
      // saturated even outside the emissive bloom.
      if (m.color) m.color.lerpColors(p._emissiveSnap.diffuse || c, c, 0.55);
      // Force the highlighted part to FULL opacity even if dim_others would
      // have dimmed it.
      m.opacity = 1.0;
      m.transparent = false;
      // CRITICAL for tiny internal parts: draw the highlight ON TOP of
      // everything else, ignoring depth. Without this, the engine block /
      // intake / valve covers occlude the highlighted piston ring even
      // when their opacity is 0.45 — alpha-blending lets you SEE through
      // them only if there's something behind to see, but the depth test
      // still discards the piston pixels because the shell wrote depth
      // first. depthTest:false + depthWrite:false + renderOrder:999
      // makes the highlight pop reliably.
      if (!p._depthSnap) {
        p._depthSnap = {
          depthTest: m.depthTest,
          depthWrite: m.depthWrite,
          renderOrder: p.object.renderOrder,
        };
      }
      m.depthTest = false;
      m.depthWrite = false;
      p.object.renderOrder = 999;
      m.needsUpdate = true;
      this._highlighted.add(p.id);
      // ALWAYS also drop a halo sprite at the part's center — even when the
      // mesh itself fails to draw (loader / depth bug), the halo guarantees
      // a visible "this is the part" marker the visual validator can pick up.
      this._addHalo(p, color, intensity);
    }
  }

  resetHighlights() {
    for (const id of this._highlighted) {
      const p = this.parts.get(id);
      if (!p || !p._emissiveSnap) continue;
      const m = p.object.material;
      if (!m) continue;
      if (m.emissive && p._emissiveSnap.color) m.emissive.copy(p._emissiveSnap.color);
      m.emissiveIntensity = p._emissiveSnap.intensity;
      if (m.color && p._emissiveSnap.diffuse) m.color.copy(p._emissiveSnap.diffuse);
      m.opacity = p._emissiveSnap.opacity ?? 1.0;
      m.transparent = m.opacity < 1.0;
      if (p._depthSnap) {
        m.depthTest = p._depthSnap.depthTest;
        m.depthWrite = p._depthSnap.depthWrite;
        p.object.renderOrder = p._depthSnap.renderOrder ?? 0;
      }
      m.needsUpdate = true;
    }
    this._highlighted.clear();
    this._clearHalos();
    // Re-hide any region overlay meshes we revealed for a highlight.
    for (const name of this._shownRegionOverlays) {
      const p = this.parts.get(name);
      if (p) p.object.visible = p.originalVisible;
    }
    this._shownRegionOverlays.clear();
  }

  // -------------------------------------------------------------------
  // Sub-mesh regions (regions.json sidecar)
  // -------------------------------------------------------------------

  /** Register sub-mesh regions. Accepts the sidecar payload ({regions:[…]})
   *  or a bare array. Overlay meshes baked into the GLB start hidden so they
   *  only appear when a region is highlighted. */
  setRegions(data) {
    this._regions = new Map();
    const list = Array.isArray(data) ? data : (data && data.regions) || [];
    for (const r of list) {
      if (!r || !r.id) continue;
      this._regions.set(r.id, r);
      if (r.overlayMesh && this.parts.has(r.overlayMesh)) {
        const p = this.parts.get(r.overlayMesh);
        p.object.visible = false;
        p.originalVisible = false;   // reset() keeps overlays hidden
      }
    }
    return this._regions.size;
  }

  regionIds() { return [...this._regions.keys()]; }

  /** Highlight a sub-mesh region by id. Resolution order:
   *   1) baked overlay mesh (exact surface, in-frame)  ← authoritative
   *   2) the parent mesh (coarse, still in-frame)
   *   3) a halo at the region's advisory world centroid (last resort) */
  highlightRegion(regionId, { color = "#5046E5", intensity = 1.0 } = {}) {
    const r = this._regions.get(regionId);
    if (!r) { console.warn("[viewer] unknown region", regionId); return false; }

    if (r.overlayMesh && this.parts.has(r.overlayMesh)) {
      const p = this.parts.get(r.overlayMesh);
      p.object.visible = true;
      this._shownRegionOverlays.add(r.overlayMesh);
      this.highlight(r.overlayMesh, { color, intensity });
      return true;
    }
    if (r.meshId && this.parts.has(r.meshId)) {
      this.highlight(r.meshId, { color, intensity });
      return true;
    }
    if (Array.isArray(r.centroidWorld)) {
      this._addRegionHalo(`region:${regionId}`, r.centroidWorld,
                          r.radiusWorld || 0, color, intensity);
      return true;
    }
    console.warn("[viewer] region has no resolvable target", regionId);
    return false;
  }

  /** Halo at an explicit world point sized to a world radius (region path). */
  _addRegionHalo(key, centerArr, radiusWorld, color, intensity = 1.0) {
    if (!this._assetDiag) {
      const assetBox = new THREE.Box3();
      if (this._loadedRoot) assetBox.setFromObject(this._loadedRoot);
      this._assetDiag = assetBox.isEmpty() ? 1.0 : assetBox.getSize(new THREE.Vector3()).length();
    }
    const center = new THREE.Vector3(centerArr[0], centerArr[1], centerArr[2]);
    const aMin = this._assetDiag * 0.04, aMax = this._assetDiag * 0.5;
    const want = (radiusWorld || this._assetDiag * 0.1) * 2.2;
    const size = Math.min(aMax, Math.max(aMin, want));
    let sprite = this._haloSprites.get(key);
    if (!sprite) {
      const mat = new THREE.SpriteMaterial({
        map: this._ensureHaloTexture(),
        color: new THREE.Color(color),
        transparent: true,
        opacity: Math.min(1.0, 0.92 * intensity),
        depthTest: false, depthWrite: false,
        blending: THREE.NormalBlending,
      });
      sprite = new THREE.Sprite(mat);
      sprite.renderOrder = 9999;
      this.scene.add(sprite);
      this._haloSprites.set(key, sprite);
    } else {
      sprite.material.color.set(color);
      sprite.material.opacity = Math.min(1.0, 0.92 * intensity);
    }
    sprite.position.copy(center);
    sprite.scale.set(size, size, 1);
  }

  dimOthers({ except = [], factor = 0.25 } = {}) {
    // Floor the factor — the director sometimes asks for 0.05–0.18 which against
    // a dark scene background makes the metallic parts effectively invisible.
    // Anything below 0.4 stops reading as "engine in the background" and starts
    // reading as "no engine at all". Keeping a baseline silhouette is more
    // important than perfect contrast on the highlighted part.
    factor = Math.max(0.40, Math.min(1.0, factor));

    const exceptSet = new Set(this.resolveTargets(except).map((p) => p.id));
    for (const p of this.parts.values()) {
      const m = p.object.material;
      if (!m || !("opacity" in m)) continue;
      if (!p._origOpacity) p._origOpacity = m.opacity ?? 1.0;
      if (exceptSet.has(p.id)) {
        m.opacity = p._origOpacity;
        m.transparent = m.transparent;
      } else {
        m.opacity = Math.max(0.35, p._origOpacity * factor);  // never fully invisible
        m.transparent = true;
      }
      m.needsUpdate = true;
    }
  }

  resetDim() {
    for (const p of this.parts.values()) {
      const m = p.object.material;
      if (!m || p._origOpacity === undefined) continue;
      m.opacity = p._origOpacity;
      m.transparent = p._origOpacity < 1;
      m.needsUpdate = true;
    }
  }

  // -------------------------------------------------------------------
  // Camera
  // -------------------------------------------------------------------

  applyCameraPose(pose) {
    // Resolve from the ACTIVE preset table (per-asset override aware), with
    // the engine-default table as a fallback.
    const presets = this._activePresets || CAMERA_PRESETS;
    if (pose.preset && presets[pose.preset]) {
      const p = presets[pose.preset];
      this.camera.position.set(...p.from);
      this.camera.fov = pose.fov ?? p.fov ?? 30;
      this.controls?.target.set(...p.to);
    } else if (pose.from && pose.to) {
      this.camera.position.set(...pose.from);
      this.camera.fov = pose.fov ?? 30;
      this.controls?.target.set(...pose.to);
    }
    this.camera.updateProjectionMatrix();
    this.controls?.update();
  }

  /** Compute a camera pose that frames a specific part (or group of parts)
   *  with a comfortable margin. Used by the frame_on action. */
  poseFor(targets, { margin = 1.8, dirHint = null } = {}) {
    const resolved = this.resolveTargets(targets);
    if (resolved.length === 0) return null;
    // World bbox of the union
    const box = new THREE.Box3();
    for (const p of resolved) {
      const partBox = new THREE.Box3().setFromObject(p.object);
      box.union(partBox);
    }
    if (box.isEmpty()) return null;
    const center = box.getCenter(new THREE.Vector3());
    const size = box.getSize(new THREE.Vector3());
    const maxDim = Math.max(size.x, size.y, size.z);
    const fov = 35;
    const distance = (maxDim * margin) / (2 * Math.tan(THREE.MathUtils.degToRad(fov / 2)));
    // View direction defaults to a Y-up 3/4 angle on the part
    // (right + above + in-front-of-engine).
    const dir = dirHint ? new THREE.Vector3(...dirHint).normalize()
                        : new THREE.Vector3(0.6, 0.5, 0.7).normalize();
    const from = center.clone().add(dir.multiplyScalar(distance));
    return { from: [from.x, from.y, from.z], to: [center.x, center.y, center.z], fov };
  }

  /** Smoothly tween to a target pose over `duration` seconds. */
  async tweenCamera(pose, duration = 0.8, ease = "easeInOut") {
    const presets = this._activePresets || CAMERA_PRESETS;
    const resolved = pose.preset && presets[pose.preset]
      ? { ...presets[pose.preset], fov: pose.fov ?? presets[pose.preset].fov }
      : pose;
    const from = this.camera.position.clone();
    const target0 = this.controls.target.clone();
    const fov0 = this.camera.fov;
    const to = new THREE.Vector3(...resolved.from);
    const target1 = new THREE.Vector3(...resolved.to);
    const fov1 = resolved.fov ?? fov0;

    const easeFn = ease === "linear" ? (t) => t
      : ease === "easeOut" ? (t) => 1 - Math.pow(1 - t, 3)
      : (t) => t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2;

    const start = performance.now();
    const durationMs = duration * 1000;
    await new Promise((resolve) => {
      let settled = false;
      const finish = () => {
        if (settled) return;
        settled = true;
        // Snap to final pose so the resting state is deterministic
        this.camera.position.copy(to);
        this.controls.target.copy(target1);
        this.camera.fov = fov1;
        this.camera.updateProjectionMatrix();
        this.controls.update();
        resolve();
      };
      const step = (now) => {
        if (settled) return;
        const t = Math.min(1, (now - start) / durationMs);
        const e = easeFn(t);
        this.camera.position.lerpVectors(from, to, e);
        this.controls.target.lerpVectors(target0, target1, e);
        this.camera.fov = fov0 + (fov1 - fov0) * e;
        this.camera.updateProjectionMatrix();
        this.controls.update();
        if (t < 1) requestAnimationFrame(step);
        else finish();
      };
      requestAnimationFrame(step);
      // Fallback: if RAF is throttled (headless previews / background tabs),
      // still resolve at the declared duration so the player can advance.
      setTimeout(finish, durationMs + 50);
    });
  }

  // -------------------------------------------------------------------
  // Animations (glTF actions)
  // -------------------------------------------------------------------

  playAnimation(name, { from = 0, to = 1, rate = 1.0, loop = false } = {}) {
    const action = this.actions.get(name);
    if (!action) {
      console.warn(`[viewer] No animation named "${name}" — skipping`);
      return Promise.resolve();
    }
    action.reset();
    action.setEffectiveTimeScale(rate);
    action.setLoop(loop ? THREE.LoopRepeat : THREE.LoopOnce, loop ? Infinity : 1);
    action.clampWhenFinished = true;

    const clipDuration = action.getClip().duration;
    action.time = from * clipDuration;
    action.play();

    if (loop) return Promise.resolve();

    const endTime = to * clipDuration;
    const stopAfter = Math.max(0, (endTime - action.time) / Math.max(rate, 0.0001));
    return new Promise((resolve) => setTimeout(() => {
      action.paused = true;
      resolve();
    }, stopAfter * 1000));
  }

  /** Scrub mode: deterministically pose an animation at a fractional
   *  position within [from, to] without playing forward in real time.
   *  Used by scrubToBeat / fast-forward so consecutive beats that share
   *  the same animation but different (from, to) ranges produce visibly
   *  distinct frames (otherwise rate:1e6 snaps every clip to its `to`
   *  endpoint, and the four-stroke beats all render identical pixels).
   *  position01 selects within [from..to] (default 1 = end of range). */
  scrubAnimation(name, { from = 0, to = 1, position01 = 1 } = {}) {
    const action = this.actions.get(name);
    if (!action) {
      console.warn(`[viewer] No animation named "${name}" — skipping (scrub)`);
      return;
    }
    if (!this.mixer) return;
    const clip = action.getClip();
    const dur  = clip.duration;
    const t01  = Math.min(1, Math.max(0, position01));
    const phase = from + (to - from) * t01;
    // Make sure the action is enabled and weighted in so its pose contributes.
    action.reset();
    action.enabled = true;
    action.setEffectiveWeight(1.0);
    action.clampWhenFinished = true;
    action.paused = false;
    action.time = phase * dur;
    action.play();
    // Tick the mixer by 0 to bake the pose into world matrices, then pause
    // so the per-frame _tick() update doesn't drift the time forward.
    this.mixer.update(0);
    action.paused = true;
  }

  stopAllAnimations() {
    for (const a of this.actions.values()) a.stop();
  }

  // -------------------------------------------------------------------
  // Assembly (runtime explode / assemble over per-part offsets)
  // -------------------------------------------------------------------
  // Generated flat-pack assets ship per-part "assembly offsets" (the exploded
  // entrance vector, in the loaded glTF frame) instead of baked motion clips.
  // We capture each part's seated rest position once, then explode/assemble by
  // translating the part between rest and rest+offset. This is the runtime that
  // makes a manual "build part by part" on screen.

  /** Register assembly metadata: { offsets: {partId:[x,y,z]}, order:[...] }.
   *  Snapshots each part's current (seated) position as its rest pose. */
  setAssembly(assembly) {
    this._assembly = assembly && assembly.offsets ? assembly : null;
    this._assemblyRest = new Map();
    if (!this._assembly) return;
    for (const id of Object.keys(this._assembly.offsets)) {
      const p = this.parts.get(id);
      // Rest = the part's authored seated position (captured at load), NOT its
      // live position — which may already be exploded from a prior beat. Using
      // the live position here is what caused rest to drift on every re-scrub.
      if (p) this._assemblyRest.set(id, (p.originalPosition ?? p.object.position).clone());
    }
  }

  _assemblyIds(ref) {
    if (ref === "all" || (ref && ref.scope === "all")) {
      return this._assemblyRest ? [...this._assemblyRest.keys()] : [];
    }
    return Array.isArray(ref) ? ref : (ref ? [ref] : []);
  }

  /** Seat every part at its rest pose (call on scene (re)apply). */
  resetAssembly() {
    if (!this._assemblyRest) return;
    for (const [id, rest] of this._assemblyRest) {
      const p = this.parts.get(id);
      if (p) p.object.position.copy(rest);
    }
  }

  /** Move the given parts (or "all") to their exploded position.
   *  fastForward (default) snaps instantly — used by scrub/seek and captures;
   *  otherwise tweens out over `duration` so the explode reads as motion. */
  async explodeParts(ref, { duration = 0.6, fastForward = true } = {}) {
    if (!this._assembly) return;
    const targets = [];
    for (const id of this._assemblyIds(ref)) {
      const p = this.parts.get(id);
      const rest = this._assemblyRest.get(id);
      const off = this._assembly.offsets[id];
      if (p && rest && off) {
        targets.push({ obj: p.object,
          to: { x: rest.x + off[0], y: rest.y + off[1], z: rest.z + off[2] } });
      }
    }
    if (fastForward) { for (const t of targets) t.obj.position.set(t.to.x, t.to.y, t.to.z); return; }
    await this._tweenPartsTo(targets, duration);
  }

  /** Tween the given parts (or "all") from their current position to seated rest.
   *  fastForward snaps instantly (scrub / seek). */
  async assembleParts(ref, { duration = 0.9, fastForward = false } = {}) {
    if (!this._assembly) return;
    const targets = [];
    for (const id of this._assemblyIds(ref)) {
      const p = this.parts.get(id);
      const rest = this._assemblyRest.get(id);
      if (p && rest) targets.push({ obj: p.object, to: rest });
    }
    if (fastForward) { for (const t of targets) t.obj.position.copy(t.to); return; }
    await this._tweenPartsTo(targets, duration);
  }

  /** Shared RAF position tween. items: [{obj, to:{x,y,z}}]. Resolves when the
   *  motion settles; a setTimeout fallback guarantees completion even when RAF
   *  is throttled (background tab / headless preview). */
  async _tweenPartsTo(items, duration = 0.9) {
    const tweens = items.map((it) => ({ obj: it.obj, from: it.obj.position.clone(), to: it.to }));
    if (!tweens.length) return;
    const durMs = Math.max(1, duration * 1000);
    const start = performance.now();
    const ease = (t) => (t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2);
    await new Promise((resolve) => {
      let settled = false;
      const finish = () => {
        if (settled) return;
        settled = true;
        for (const tw of tweens) tw.obj.position.set(tw.to.x, tw.to.y, tw.to.z);
        resolve();
      };
      const step = (now) => {
        if (settled) return;
        const t = Math.min(1, (now - start) / durMs);
        const e = ease(t);
        for (const tw of tweens) tw.obj.position.lerpVectors(tw.from, tw.to, e);
        if (t < 1) requestAnimationFrame(step);
        else finish();
      };
      requestAnimationFrame(step);
      setTimeout(finish, durMs + 60);  // RAF-throttle fallback (headless/bg tabs)
    });
  }

  // -------------------------------------------------------------------
  // World → screen projection (for HTML labels anchored to parts)
  // -------------------------------------------------------------------

  projectToScreen(worldPos) {
    const v = new THREE.Vector3(...worldPos).project(this.camera);
    const w = this.canvas.clientWidth, h = this.canvas.clientHeight;
    return {
      x: (v.x * 0.5 + 0.5) * w,
      y: (-v.y * 0.5 + 0.5) * h,
      z: v.z, // <0 = in front of camera, >1 = behind near plane (for visibility test)
      visible: v.z > -1 && v.z < 1,
    };
  }

  /** Force a fresh render and return the framebuffer as a base64 PNG.
   *  Used by the visual validator to capture each beat for Opus review. */
  captureFrame() {
    this.renderer.render(this.scene, this.camera);
    return this.canvas.toDataURL("image/png");
  }

  getPartWorldPosition(partId) {
    const p = this.parts.get(partId);
    if (!p) return null;
    // Use the world-space bbox center of the geometry, NOT obj.getWorldPosition()
    // which returns the mesh ORIGIN. Many CAD imports place every mesh's
    // origin at (0,0,0) and store the real position in the vertex data;
    // after parenting (e.g. blades → fan_hub) the origin sticks to the
    // parent's origin while the vertices remain at their authored coords.
    // Labels and halos need to track the visible geometry, not the origin.
    const box = new THREE.Box3().setFromObject(p.object);
    if (box.isEmpty()) {
      const v = new THREE.Vector3();
      p.object.getWorldPosition(v);
      return [v.x, v.y, v.z];
    }
    const c = box.getCenter(new THREE.Vector3());
    return [c.x, c.y, c.z];
  }
}

export { CAMERA_PRESETS };
