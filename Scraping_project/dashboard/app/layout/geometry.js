// Where flow-diagram edges run between two measured cards. Pure.
const r = (n) => +n.toFixed(2);

// Vertical when the target starts below the source's bottom edge, else side to side.
export const edgeOrientation = (a, b) => (b.y >= a.y + a.h - 1 ? 'vertical' : 'horizontal');

export function edgePath(a, b) {
  if (edgeOrientation(a, b) === 'vertical') {
    const x1 = a.x + a.w / 2;
    const y1 = a.y + a.h;
    const x2 = b.x + b.w / 2;
    const y2 = b.y;
    const my = (y1 + y2) / 2;
    return `M${r(x1)},${r(y1)}C${r(x1)},${r(my)} ${r(x2)},${r(my)} ${r(x2)},${r(y2)}`;
  }
  const x1 = a.x + a.w;
  const y1 = a.y + a.h / 2;
  const x2 = b.x;
  const y2 = b.y + b.h / 2;
  const mx = (x1 + x2) / 2;
  return `M${r(x1)},${r(y1)}C${r(mx)},${r(y1)} ${r(mx)},${r(y2)} ${r(x2)},${r(y2)}`;
}
