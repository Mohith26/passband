// Smith chart. The grid is the standard conformal map of the impedance plane
// onto the unit reflection circle:
//
//   a constant resistance r maps to a circle centred at (r/(1+r), 0)
//   with radius 1/(1+r)
//
//   a constant reactance x maps to a circle centred at (1, 1/x)
//   with radius |1/x|, clipped to the unit circle
//
// Drawing it by hand rather than shipping an image means the grid stays crisp at
// any size and the trace sits in exactly the same coordinate system as the grid.

const NS = 'http://www.w3.org/2000/svg';

function el(name, attrs = {}, text) {
  const node = document.createElementNS(NS, name);
  for (const [k, v] of Object.entries(attrs)) node.setAttribute(k, v);
  if (text !== undefined) node.textContent = text;
  return node;
}

const R_CIRCLES = [0, 0.2, 0.5, 1, 2, 5];
const X_ARCS = [0.2, 0.5, 1, 2, 5];

export function smithChart(mount, spec) {
  const { points = [], size = 380, label = '' } = spec;
  mount.textContent = '';

  const pad = 16;
  const radius = (size - pad * 2) / 2;
  const cx = size / 2;
  const cy = size / 2;
  const toX = (re) => cx + re * radius;
  const toY = (im) => cy - im * radius;

  const svg = el('svg', {
    viewBox: `0 0 ${size} ${size}`, width: '100%', height: 'auto',
    role: 'img', 'aria-label': label || 'Smith chart of input reflection',
  });

  const grid = el('g', { class: 'smith-grid' });

  for (const r of R_CIRCLES) {
    const rc = 1 / (1 + r);
    const centre = r / (1 + r);
    grid.append(el('circle', { cx: toX(centre), cy: toY(0), r: rc * radius }));
  }

  // Reactance arcs are circles centred outside the chart, so they are drawn as
  // full circles and clipped to the unit circle rather than solved analytically.
  const clipId = 'smith-clip-' + Math.random().toString(36).slice(2, 8);
  const clip = el('clipPath', { id: clipId });
  clip.append(el('circle', { cx, cy, r: radius }));
  svg.append(clip);

  const arcs = el('g', { class: 'smith-grid', 'clip-path': `url(#${clipId})` });
  for (const x of X_ARCS) {
    for (const sign of [1, -1]) {
      const r = radius / x;
      arcs.append(el('circle', { cx: toX(1), cy: toY(sign / x), r }));
    }
  }
  grid.append(arcs);

  const axis = el('g', { class: 'smith-axis' });
  axis.append(el('circle', { cx, cy, r: radius }));
  axis.append(el('line', { x1: toX(-1), x2: toX(1), y1: toY(0), y2: toY(0) }));
  svg.append(grid, axis);

  if (points.length) {
    let d = '';
    let pen = false;
    for (const p of points) {
      if (!isFinite(p.re) || !isFinite(p.im)) { pen = false; continue; }
      d += (pen ? 'L' : 'M') + toX(p.re).toFixed(2) + ' ' + toY(p.im).toFixed(2) + ' ';
      pen = true;
    }
    svg.append(el('path', { class: 'smith-trace', d }));

    const first = points[0];
    const last = points[points.length - 1];
    if (isFinite(first.re)) {
      svg.append(el('circle', { cx: toX(first.re), cy: toY(first.im), r: 3.2, fill: '#46c08a' }));
    }
    if (isFinite(last.re)) {
      svg.append(el('circle', { cx: toX(last.re), cy: toY(last.im), r: 3.2, fill: '#ef6a72' }));
    }
  }

  svg.append(el('text', {
    class: 'readout', x: toX(-1) + 2, y: toY(0) - 6, 'text-anchor': 'start',
  }, 'short'));
  svg.append(el('text', {
    class: 'readout', x: toX(1) - 2, y: toY(0) - 6, 'text-anchor': 'end',
  }, 'open'));
  svg.append(el('text', {
    class: 'readout', x: cx, y: toY(0) - 6, 'text-anchor': 'middle',
  }, 'match'));

  mount.append(svg);
  return svg;
}
