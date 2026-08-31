// A line chart drawn straight into SVG. No charting library, partly because the
// bundle would dwarf the rest of the front end and partly because the axis rules
// an RF plot needs are specific enough that I would end up fighting a general
// purpose library anyway: a log frequency axis with engineering suffixes, ticks
// that land on 1/2/5 rather than wherever, and a shared cursor readout.

const NS = 'http://www.w3.org/2000/svg';

export function el(name, attrs = {}, text) {
  const node = document.createElementNS(NS, name);
  for (const [k, v] of Object.entries(attrs)) node.setAttribute(k, v);
  if (text !== undefined) node.textContent = text;
  return node;
}

// 1, 2, 5, 10, 20, 50 ... which is what every instrument front panel uses.
export function niceTicks(min, max, target = 6) {
  if (!isFinite(min) || !isFinite(max) || min === max) return [min];
  const span = max - min;
  const rough = span / target;
  const mag = Math.pow(10, Math.floor(Math.log10(rough)));
  const norm = rough / mag;
  const step = (norm < 1.5 ? 1 : norm < 3 ? 2 : norm < 7 ? 5 : 10) * mag;
  const first = Math.ceil(min / step) * step;
  const out = [];
  for (let v = first; v <= max + step * 1e-9; v += step) {
    out.push(Math.abs(v) < step * 1e-9 ? 0 : v);
  }
  return out;
}

export function decadeTicks(min, max) {
  const out = [];
  const lo = Math.floor(Math.log10(min));
  const hi = Math.ceil(Math.log10(max));
  for (let d = lo; d <= hi; d++) {
    for (const m of [1, 2, 5]) {
      const v = m * Math.pow(10, d);
      if (v >= min && v <= max) out.push(v);
    }
  }
  return out;
}

const SUFFIX = [
  [1e9, 'G'], [1e6, 'M'], [1e3, 'k'], [1, ''],
  [1e-3, 'm'], [1e-6, 'u'], [1e-9, 'n'], [1e-12, 'p'],
];

export function eng(value, digits = 3) {
  if (value === 0) return '0';
  const abs = Math.abs(value);
  for (const [scale, suffix] of SUFFIX) {
    if (abs >= scale) {
      const scaled = value / scale;
      const decimals = Math.abs(scaled) >= 100 ? 0 : Math.abs(scaled) >= 10 ? 1 : digits - 1;
      return scaled.toFixed(decimals).replace(/\.0+$/, '') + suffix;
    }
  }
  return value.toExponential(2);
}

const PALETTE = ['#4f9cf9', '#f2a23c', '#46c08a', '#c47ff5', '#ef6a72'];

export function lineChart(mount, spec) {
  const {
    series, xLabel = '', yLabel = '', logX = true,
    width = 620, height = 300, formatY = (v) => v.toFixed(2),
  } = spec;

  mount.textContent = '';
  const usable = series.filter((s) => s.x.length > 0);
  if (usable.length === 0) {
    mount.append(Object.assign(document.createElement('p'), {
      className: 'muted', textContent: 'No points to plot.',
    }));
    return;
  }

  const pad = { l: 62, r: 14, t: 12, b: 38 };
  const iw = width - pad.l - pad.r;
  const ih = height - pad.t - pad.b;

  let xmin = Infinity, xmax = -Infinity, ymin = Infinity, ymax = -Infinity;
  for (const s of usable) {
    for (let i = 0; i < s.x.length; i++) {
      const xv = s.x[i], yv = s.y[i];
      if (typeof xv !== 'number' || typeof yv !== 'number' || !isFinite(xv) || !isFinite(yv)) continue;
      if (logX && xv <= 0) continue;
      if (xv < xmin) xmin = xv;
      if (xv > xmax) xmax = xv;
      if (yv < ymin) ymin = yv;
      if (yv > ymax) ymax = yv;
    }
  }
  if (!isFinite(ymin) || !isFinite(ymax)) { ymin = 0; ymax = 1; }
  if (ymin === ymax) { ymin -= 1; ymax += 1; }
  const headroom = (ymax - ymin) * 0.08;
  ymin -= headroom; ymax += headroom;

  const sx = logX
    ? (v) => pad.l + (Math.log10(v) - Math.log10(xmin)) / (Math.log10(xmax) - Math.log10(xmin)) * iw
    : (v) => pad.l + (v - xmin) / (xmax - xmin) * iw;
  const sy = (v) => pad.t + ih - (v - ymin) / (ymax - ymin) * ih;

  const svg = el('svg', {
    viewBox: `0 0 ${width} ${height}`, width: '100%', height: 'auto',
    role: 'img', 'aria-label': `${yLabel} against ${xLabel}`,
  });

  const grid = el('g', { class: 'grid' });
  const yticks = niceTicks(ymin, ymax, 6);
  for (const t of yticks) grid.append(el('line', { x1: pad.l, x2: pad.l + iw, y1: sy(t), y2: sy(t) }));
  const xticks = logX ? decadeTicks(xmin, xmax) : niceTicks(xmin, xmax, 6);
  for (const t of xticks) grid.append(el('line', { x1: sx(t), x2: sx(t), y1: pad.t, y2: pad.t + ih }));
  svg.append(grid);

  const axis = el('g', { class: 'axis' });
  axis.append(el('line', { x1: pad.l, x2: pad.l, y1: pad.t, y2: pad.t + ih }));
  axis.append(el('line', { x1: pad.l, x2: pad.l + iw, y1: pad.t + ih, y2: pad.t + ih }));
  for (const t of yticks) {
    axis.append(el('text', { x: pad.l - 8, y: sy(t) + 3.5, 'text-anchor': 'end' }, formatY(t)));
  }
  for (const t of xticks) {
    axis.append(el('text', { x: sx(t), y: pad.t + ih + 15, 'text-anchor': 'middle' }, eng(t)));
  }
  axis.append(el('text', {
    class: 'axis-label', x: pad.l + iw / 2, y: height - 4, 'text-anchor': 'middle',
  }, xLabel));
  axis.append(el('text', {
    class: 'axis-label', x: 12, y: pad.t + ih / 2, 'text-anchor': 'middle',
    transform: `rotate(-90 12 ${pad.t + ih / 2})`,
  }, yLabel));
  svg.append(axis);

  usable.forEach((s, index) => {
    const colour = s.colour || PALETTE[index % PALETTE.length];
    let d = '';
    let pen = false;
    for (let i = 0; i < s.x.length; i++) {
      const xv = s.x[i], yv = s.y[i];
      // A null arrives where the backend had a real infinity. Note that
      // isFinite(null) is true in JavaScript, so the type check has to come
      // first or the gap silently plots as zero.
      if (typeof xv !== 'number' || typeof yv !== 'number' || !isFinite(xv) || !isFinite(yv) || (logX && xv <= 0)) { pen = false; continue; }
      d += (pen ? 'L' : 'M') + sx(xv).toFixed(2) + ' ' + sy(yv).toFixed(2) + ' ';
      pen = true;
    }
    svg.append(el('path', { class: 'trace', d, stroke: colour }));
  });

  if (usable.length > 1) {
    const legend = el('g');
    usable.forEach((s, index) => {
      const colour = s.colour || PALETTE[index % PALETTE.length];
      const y = pad.t + 12 + index * 15;
      legend.append(el('line', { x1: pad.l + iw - 92, x2: pad.l + iw - 74, y1: y, y2: y, stroke: colour, 'stroke-width': 2 }));
      legend.append(el('text', { class: 'readout', x: pad.l + iw - 68, y: y + 3.5 }, s.label || ''));
    });
    svg.append(legend);
  }

  // Shared cursor. Reading a value off a plot by eye is how wrong numbers end up
  // in a report, so the exact sample is printed instead.
  const cursor = el('g', { visibility: 'hidden' });
  const vline = el('line', { class: 'cursor-line', y1: pad.t, y2: pad.t + ih });
  const box = el('rect', { class: 'readout-box', rx: 4, width: 150, height: 16 + usable.length * 14 });
  const labels = usable.map(() => el('text', { class: 'readout' }));
  const xlabel = el('text', { class: 'readout' });
  cursor.append(vline, box, xlabel, ...labels);
  svg.append(cursor);

  svg.addEventListener('mouseleave', () => cursor.setAttribute('visibility', 'hidden'));
  svg.addEventListener('mousemove', (event) => {
    const rect = svg.getBoundingClientRect();
    const px = (event.clientX - rect.left) / rect.width * width;
    if (px < pad.l || px > pad.l + iw) { cursor.setAttribute('visibility', 'hidden'); return; }
    const ref = usable[0];
    let best = 0, bestDistance = Infinity;
    for (let i = 0; i < ref.x.length; i++) {
      const d = Math.abs(sx(ref.x[i]) - px);
      if (d < bestDistance) { bestDistance = d; best = i; }
    }
    const cx = sx(ref.x[best]);
    vline.setAttribute('x1', cx); vline.setAttribute('x2', cx);
    const flip = cx > pad.l + iw - 165;
    const bx = flip ? cx - 158 : cx + 8;
    box.setAttribute('x', bx); box.setAttribute('y', pad.t + 6);
    xlabel.setAttribute('x', bx + 8); xlabel.setAttribute('y', pad.t + 20);
    xlabel.textContent = eng(ref.x[best]) + 'Hz';
    usable.forEach((s, index) => {
      const node = labels[index];
      node.setAttribute('x', bx + 8);
      node.setAttribute('y', pad.t + 34 + index * 14);
      node.setAttribute('fill', s.colour || PALETTE[index % PALETTE.length]);
      const value = s.y[best];
      const printable = typeof value === 'number' && isFinite(value);
      node.textContent = `${s.label || 'y'}  ${printable ? formatY(value) : '--'}`;
    });
    cursor.setAttribute('visibility', 'visible');
  });

  mount.append(svg);
  return svg;
}
