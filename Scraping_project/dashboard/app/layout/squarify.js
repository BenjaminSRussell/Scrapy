// Squarified treemap layout (Bruls, Huizing, van Wijk). Pure.
// Returns a copy of each item with x, y, w, h. Items with value <= 0 are dropped.

function worst(row, side, scale) {
  const sum = row.reduce((a, r) => a + r.value * scale, 0);
  let max = -Infinity;
  let min = Infinity;
  for (const r of row) {
    const a = r.value * scale;
    if (a > max) max = a;
    if (a < min) min = a;
  }
  const s2 = side * side;
  const sum2 = sum * sum;
  return Math.max((s2 * max) / sum2, sum2 / (s2 * min));
}

export function squarify(items, x, y, w, h) {
  const list = items.filter((i) => i.value > 0).sort((a, b) => b.value - a.value);
  if (!list.length || w <= 0 || h <= 0) return [];
  const total = list.reduce((a, i) => a + i.value, 0);
  const scale = (w * h) / total;
  const out = [];
  let rect = { x, y, w, h };
  let row = [];
  let i = 0;

  function layoutRow(r) {
    const area = r.reduce((a, it) => a + it.value * scale, 0);
    if (rect.w >= rect.h) {
      const colW = area / rect.h;
      let cy = rect.y;
      for (const it of r) {
        const ih = (it.value * scale) / colW;
        out.push({ ...it, x: rect.x, y: cy, w: colW, h: ih });
        cy += ih;
      }
      rect = { x: rect.x + colW, y: rect.y, w: rect.w - colW, h: rect.h };
    } else {
      const rowH = area / rect.w;
      let cx = rect.x;
      for (const it of r) {
        const iw = (it.value * scale) / rowH;
        out.push({ ...it, x: cx, y: rect.y, w: iw, h: rowH });
        cx += iw;
      }
      rect = { x: rect.x, y: rect.y + rowH, w: rect.w, h: rect.h - rowH };
    }
  }

  while (i < list.length) {
    const side = Math.min(rect.w, rect.h);
    const next = [...row, list[i]];
    if (!row.length || worst(next, side, scale) <= worst(row, side, scale)) {
      row = next;
      i += 1;
    } else {
      layoutRow(row);
      row = [];
    }
  }
  if (row.length) layoutRow(row);
  return out;
}
