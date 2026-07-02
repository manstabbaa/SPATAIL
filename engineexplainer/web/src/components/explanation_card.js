// ExplanationCard — a screen-anchored glass panel. Single component, no framework.
// Props (passed via the contract action): { kicker?, title, body, metrics? }

export const ExplanationCard = {
  create(props = {}) {
    const el = document.createElement("article");
    el.className = "explanation-card";

    const { kicker, title, body, metrics } = props;

    if (kicker) {
      const k = document.createElement("div");
      k.className = "kicker";
      k.textContent = kicker;
      el.appendChild(k);
    }

    const accent = document.createElement("div");
    accent.className = "accent";
    el.appendChild(accent);

    if (title) {
      const t = document.createElement("h2");
      t.className = "title";
      t.textContent = title;
      el.appendChild(t);
    }

    if (body) {
      const b = document.createElement("p");
      b.className = "body";
      b.textContent = body;
      el.appendChild(b);
    }

    if (Array.isArray(metrics) && metrics.length) {
      const grid = document.createElement("div");
      grid.className = "metrics";
      grid.style.cssText = "display:grid;grid-template-columns:repeat(auto-fit,minmax(80px,1fr));gap:12px;margin-top:14px;padding-top:14px;border-top:1px solid var(--hairline);";
      for (const m of metrics) {
        const cell = document.createElement("div");
        const lbl = document.createElement("div");
        lbl.style.cssText = "font-family:var(--font-mono);font-size:10px;letter-spacing:.12em;color:var(--indigo-soft);text-transform:uppercase;margin-bottom:2px;";
        lbl.textContent = m.label;
        const val = document.createElement("div");
        val.style.cssText = "font-size:18px;font-weight:600;color:var(--cream);";
        val.textContent = m.value;
        cell.appendChild(lbl);
        cell.appendChild(val);
        grid.appendChild(cell);
      }
      el.appendChild(grid);
    }

    return el;
  },
};
