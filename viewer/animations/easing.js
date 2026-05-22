// Easing functions used by the animation handlers. Plain math, no deps.
// Names match the strings used in the contract's animations[].easing field.

const easings = {
  "linear":            (t) => t,
  "ease-in-cubic":     (t) => t * t * t,
  "ease-out-cubic":    (t) => 1 - Math.pow(1 - t, 3),
  "ease-in-out-cubic": (t) => t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2,
  "sine-in-out":       (t) => -(Math.cos(Math.PI * t) - 1) / 2,
};

export function ease(name, t) {
  const fn = easings[name] || easings["linear"];
  return fn(Math.max(0, Math.min(1, t)));
}
