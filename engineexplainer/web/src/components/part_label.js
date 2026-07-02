// PartLabel — a small HTML callout that follows a world-space point.
// Lifetime: created by the contract player, repositioned every RAF,
// destroyed when the player clears overlays between contracts.

export class PartLabel {
  constructor({ text, kicker, anchor = "auto" }) {
    this.text = text;
    this.kicker = kicker;
    this.anchor = anchor;
    this.worldPosition = null;
    this.el = null;
  }

  mount(parent) {
    const el = document.createElement("div");
    el.className = "part-label";
    if (this.kicker) {
      const k = document.createElement("span");
      k.className = "kicker";
      k.textContent = this.kicker;
      el.appendChild(k);
    }
    const t = document.createElement("span");
    t.className = "text";
    t.textContent = this.text;
    el.appendChild(t);
    parent.appendChild(el);
    this.el = el;
  }

  setWorldPosition(p) { this.worldPosition = p; }

  setScreenPosition(x, y, visible) {
    if (!this.el) return;
    if (!visible) {
      this.el.style.opacity = "0";
      return;
    }
    this.el.style.left = `${Math.round(x)}px`;
    this.el.style.top  = `${Math.round(y)}px`;
    // Keep transform from CSS but override the X/Y if anchor demands it.
    // (Default CSS uses translate(-50%, -100%) translateY(-12px) to put the
    // label above the part with a tail. anchor='below' flips it.)
    if (this.anchor === "below") {
      this.el.style.transform = "translate(-50%, 12px)";
    } else if (this.anchor === "left") {
      this.el.style.transform = "translate(-100%, -50%) translateX(-12px)";
    } else if (this.anchor === "right") {
      this.el.style.transform = "translate(0, -50%) translateX(12px)";
    }
    // 'above' and 'auto' use the CSS default.
  }

  show() { this.el?.classList.add("is-visible"); }
  hide() { this.el?.classList.remove("is-visible"); }

  destroy() {
    if (this.el?.parentNode) this.el.parentNode.removeChild(this.el);
    this.el = null;
    this.worldPosition = null;
  }
}
