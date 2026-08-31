// Front end controller. Plain modules, no framework, no build step: the whole
// thing is served as written, which for an internal tool is one less thing that
// can be out of date relative to the source.

import { lineChart, eng } from '/chart.js';
import { smithChart } from '/smith.js';

const state = {
  query: '',
  kind: null,
  family: null,
  part: null,
  offset: 0,
  limit: 12,
  selectedDoc: null,
  selectedDataset: null,
  traceKind: 'db',
  parameter: [2, 1],
};

const $ = (id) => document.getElementById(id);

// Small cache keyed on url. The API sends ETags; this avoids even asking again
// for something already on screen.
const cache = new Map();
async function api(path) {
  if (cache.has(path)) return cache.get(path);
  const response = await fetch(path);
  if (!response.ok) throw new Error(`${path} responded ${response.status}`);
  const body = await response.json();
  cache.set(path, body);
  return body;
}

function qs(params) {
  return Object.entries(params)
    .filter(([, v]) => v !== null && v !== undefined && v !== '')
    .map(([k, v]) => `${encodeURIComponent(k)}=${encodeURIComponent(v)}`)
    .join('&');
}

// ---- rendering ---------------------------------------------------------

function renderCounts(counts) {
  $('counts').textContent =
    `${counts.parts} parts  ${counts.documents} docs  ${counts.datasets} datasets  ${counts.terms} terms`;
}

function renderFacets(facets) {
  const build = (mount, title, key, rows) => {
    mount.textContent = '';
    const heading = document.createElement('h3');
    heading.textContent = title;
    mount.append(heading);
    for (const row of rows) {
      const value = row[key];
      const button = document.createElement('button');
      button.className = 'facet';
      button.type = 'button';
      button.setAttribute('aria-pressed', String(state[key] === value));
      const name = document.createElement('span');
      name.textContent = value;
      const n = document.createElement('span');
      n.className = 'n';
      n.textContent = row.n;
      button.append(name, n);
      button.addEventListener('click', () => {
        state[key] = state[key] === value ? null : value;
        state.offset = 0;
        refresh();
      });
      mount.append(button);
    }
  };
  build($('facet-kind'), 'Document kind', 'kind', facets.kind);
  build($('facet-family'), 'Part family', 'family', facets.family);
}

function renderParts(parts) {
  const list = $('partlist');
  list.textContent = '';
  for (const part of parts) {
    const li = document.createElement('li');
    const button = document.createElement('button');
    button.type = 'button';
    const label = document.createElement('span');
    label.textContent = part.part_number;
    const fam = document.createElement('span');
    fam.className = 'fam';
    fam.textContent = part.family;
    button.append(label, fam);
    button.addEventListener('click', () => selectPart(part.part_number));
    li.append(button);
    list.append(li);
  }
}

function highlight(text, terms) {
  const fragment = document.createDocumentFragment();
  if (!terms.length) { fragment.append(text); return fragment; }
  const pattern = new RegExp(`(${terms.map(escapeRe).join('|')})`, 'ig');
  let last = 0;
  for (const match of text.matchAll(pattern)) {
    fragment.append(text.slice(last, match.index));
    const mark = document.createElement('mark');
    mark.textContent = match[0];
    fragment.append(mark);
    last = match.index + match[0].length;
  }
  fragment.append(text.slice(last));
  return fragment;
}

function escapeRe(s) { return s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'); }

function renderResults(payload, terms) {
  const list = $('doclist');
  list.textContent = '';
  const rows = payload.results || payload.documents || [];
  $('results-title').textContent = state.query ? `Results for "${state.query}"` : 'Documents';
  $('results-count').textContent = `${payload.total} match${payload.total === 1 ? '' : 'es'}`;

  for (const row of rows) {
    const li = document.createElement('li');
    li.setAttribute('aria-current', String(state.selectedDoc === row.id));
    const button = document.createElement('button');
    button.type = 'button';

    const title = document.createElement('div');
    title.className = 'doc-title';
    title.append(highlight(row.title, terms));

    const meta = document.createElement('div');
    meta.className = 'doc-meta';
    const part = document.createElement('span');
    part.className = 'pill part';
    part.textContent = row.part_number;
    const kind = document.createElement('span');
    kind.className = 'pill';
    kind.textContent = row.kind;
    meta.append(part, kind);
    if (row.score !== undefined) {
      const score = document.createElement('span');
      score.className = 'pill';
      score.textContent = `score ${row.score.toFixed(2)}`;
      meta.append(score);
    }

    button.append(title, meta);
    if (row.snippet) {
      const snippet = document.createElement('div');
      snippet.className = 'snippet';
      snippet.append(highlight(row.snippet, terms));
      button.append(snippet);
    }
    button.addEventListener('click', () => selectDocument(row.id));
    li.append(button);
    list.append(li);
  }

  const pager = $('pager');
  pager.textContent = '';
  const prev = document.createElement('button');
  prev.type = 'button';
  prev.textContent = 'Previous';
  prev.disabled = state.offset === 0;
  prev.addEventListener('click', () => { state.offset = Math.max(0, state.offset - state.limit); refresh(); });
  const next = document.createElement('button');
  next.type = 'button';
  next.textContent = 'Next';
  next.disabled = state.offset + state.limit >= payload.total;
  next.addEventListener('click', () => { state.offset += state.limit; refresh(); });
  const status = document.createElement('span');
  status.className = 'muted';
  const shown = Math.min(payload.total, state.offset + rows.length);
  status.textContent = payload.total ? `${state.offset + 1}-${shown} of ${payload.total}` : 'nothing to show';
  pager.append(prev, next, status);
}

function fmtHz(v) { return `${eng(v)}Hz`; }

const SUMMARY_ROWS = [
  ['points', 'Points', (v) => v],
  ['f_start_hz', 'Start', fmtHz],
  ['f_stop_hz', 'Stop', fmtHz],
  ['reference_impedance', 'Reference', (v) => `${v} ohm`],
  ['worst_return_loss_db', 'Worst return loss', (v) => `${v.toFixed(2)} dB`],
  ['worst_vswr', 'Worst VSWR', (v) => `${v.toFixed(2)}:1`],
  ['max_gain_db', 'Peak gain', (v) => `${v.toFixed(2)} dB`],
  ['peak_gain_hz', 'Peak at', fmtHz],
  ['max_insertion_loss_db', 'Max insertion loss', (v) => `${v.toFixed(2)} dB`],
  ['group_delay_ripple_s', 'Group delay ripple', (v) => `${eng(v)}s`],
  ['min_stability_k', 'Min K', (v) => v.toFixed(3)],
  ['min_mu', 'Min mu', (v) => v.toFixed(3)],
];

function renderSummary(summary) {
  const table = $('summary');
  table.textContent = '';
  for (const [key, label, format] of SUMMARY_ROWS) {
    const value = summary[key];
    if (value === undefined || value === null) continue;
    const tr = document.createElement('tr');
    const th = document.createElement('th');
    th.textContent = label;
    const td = document.createElement('td');
    td.textContent = format(value);
    tr.append(th, td);
    table.append(tr);
  }
  if (summary.unconditionally_stable !== undefined) {
    const tr = document.createElement('tr');
    const th = document.createElement('th');
    th.textContent = 'Stability';
    const td = document.createElement('td');
    td.textContent = summary.unconditionally_stable
      ? 'unconditionally stable' : 'conditionally stable';
    td.className = summary.unconditionally_stable ? 'verdict-good' : 'verdict-warn';
    tr.append(th, td);
    table.append(tr);
  }
}

// VSWR and return loss are reflection quantities. The backend refuses them for
// an off diagonal parameter, so the buttons are disabled rather than letting the
// user click something that can only come back as a 400.
const TRACE_KINDS = [
  ['db', 'Magnitude dB', false],
  ['phase', 'Phase', false],
  ['vswr', 'VSWR', true],
  ['return_loss', 'Return loss', true],
  ['group_delay', 'Group delay', false],
];

const Y_LABEL = {
  db: 'Magnitude (dB)', phase: 'Phase (deg)', vswr: 'VSWR',
  return_loss: 'Return loss (dB)', group_delay: 'Group delay (s)',
};

function renderControls(dataset) {
  const mount = $('plot-controls');
  mount.textContent = '';
  const params = [];
  for (let i = 1; i <= dataset.ports; i++) {
    for (let j = 1; j <= dataset.ports; j++) params.push([i, j]);
  }
  for (const [i, j] of params) {
    const button = document.createElement('button');
    button.type = 'button';
    button.textContent = `S${i}${j}`;
    const active = state.parameter[0] === i && state.parameter[1] === j;
    button.setAttribute('aria-pressed', String(active));
    button.addEventListener('click', () => { state.parameter = [i, j]; drawPlots(dataset); });
    mount.append(button);
  }
  const spacer = document.createElement('span');
  spacer.style.width = '12px';
  mount.append(spacer);
  const [pi, pj] = state.parameter;
  for (const [kind, label, reflectionOnly] of TRACE_KINDS) {
    const button = document.createElement('button');
    button.type = 'button';
    button.textContent = label;
    const allowed = !reflectionOnly || pi === pj;
    button.disabled = !allowed;
    if (!allowed) button.title = `${label} is only defined for a reflection parameter`;
    button.setAttribute('aria-pressed', String(state.traceKind === kind && allowed));
    button.addEventListener('click', () => { state.traceKind = kind; drawPlots(dataset); });
    mount.append(button);
  }
}

async function drawPlots(dataset) {
  const [i, j] = state.parameter;
  const kindSpec = TRACE_KINDS.find(([k]) => k === state.traceKind);
  if (kindSpec && kindSpec[2] && i !== j) state.traceKind = 'db';
  renderControls(dataset);
  $('plot-title').textContent =
    `${dataset.part_number || ''} S${i}${j} ${dataset.revision ? 'rev ' + dataset.revision : ''}`.trim();

  const trace = await api(`/api/datasets/${dataset.id}/trace?${qs({ i, j, kind: state.traceKind })}`);
  lineChart($('chart'), {
    series: [{ x: trace.frequencies_hz, y: trace.values, label: trace.parameter }],
    xLabel: 'Frequency', yLabel: Y_LABEL[state.traceKind] || '',
    logX: true,
    formatY: (v) => (Math.abs(v) >= 1000 || (v !== 0 && Math.abs(v) < 0.01) ? eng(v) : v.toFixed(2)),
  });
  $('chart-caption').textContent =
    `${trace.values.length} measured points, ${fmtHz(trace.frequencies_hz[0])} to ` +
    `${fmtHz(trace.frequencies_hz[trace.frequencies_hz.length - 1])}, ` +
    `reference ${trace.reference_impedance} ohm.`;

  const re = await api(`/api/datasets/${dataset.id}/trace?${qs({ i: 1, j: 1, kind: 'real' })}`);
  const im = await api(`/api/datasets/${dataset.id}/trace?${qs({ i: 1, j: 1, kind: 'imag' })}`);
  smithChart($('smith'), {
    points: re.values.map((v, k) => ({ re: v, im: im.values[k] })),
    label: 'S11 on the impedance plane',
  });

  renderSummary(dataset.summary || {});
}

// ---- interactions ------------------------------------------------------

async function selectDocument(id) {
  state.selectedDoc = id;
  const doc = await api(`/api/documents/${id}`);
  $('empty').hidden = true;
  $('plots').hidden = true;
  const article = $('doc');
  article.hidden = false;
  article.textContent = '';

  const h3 = document.createElement('h3');
  h3.textContent = doc.title;
  const meta = document.createElement('div');
  meta.className = 'doc-meta';
  for (const [cls, text] of [['pill part', doc.part_number], ['pill', doc.kind],
                             ['pill', `${doc.byte_size} bytes`]]) {
    const pill = document.createElement('span');
    pill.className = cls;
    pill.textContent = text;
    meta.append(pill);
  }
  const body = document.createElement('div');
  body.className = 'body';
  body.textContent = doc.body;

  const open = document.createElement('button');
  open.className = 'clear';
  open.type = 'button';
  open.style.marginTop = '16px';
  open.textContent = `Open measurements for ${doc.part_number}`;
  open.addEventListener('click', () => selectPart(doc.part_number));

  article.append(h3, meta, body, open);
  refreshSelection();
}

async function selectPart(partNumber) {
  const payload = await api(`/api/parts/${partNumber}`);
  if (!payload.datasets.length) return;
  const dataset = payload.datasets[0];
  dataset.part_number = payload.part.part_number;
  state.selectedDataset = dataset.id;
  state.parameter = dataset.ports >= 2 ? [2, 1] : [1, 1];
  $('empty').hidden = true;
  $('doc').hidden = true;
  $('plots').hidden = false;
  await drawPlots(dataset);
}

function refreshSelection() {
  for (const li of $('doclist').children) {
    li.setAttribute('aria-current', 'false');
  }
}

async function refresh() {
  const terms = state.query.toLowerCase().split(/\s+/).filter((t) => t.length > 1);
  let payload;
  if (state.query) {
    payload = await api(`/api/search?${qs({
      q: state.query, kind: state.kind, family: state.family,
      limit: state.limit, offset: state.offset,
    })}`);
  } else {
    payload = await api(`/api/documents?${qs({
      kind: state.kind, family: state.family, part: state.part,
      limit: state.limit, offset: state.offset,
    })}`);
  }
  renderResults(payload, terms);
  const facets = await api('/api/facets');
  renderFacets(facets);
}

async function boot() {
  const health = await api('/api/health');
  renderCounts(health.counts);
  const parts = await api('/api/parts');
  renderParts(parts.parts);
  await refresh();
}

$('searchform').addEventListener('submit', (event) => {
  event.preventDefault();
  state.query = $('q').value.trim();
  state.offset = 0;
  refresh();
});

$('clear').addEventListener('click', () => {
  state.kind = null; state.family = null; state.part = null;
  state.query = ''; $('q').value = ''; state.offset = 0;
  refresh();
});

boot().catch((error) => {
  $('results-title').textContent = 'Could not reach the API';
  $('results-count').textContent = String(error.message || error);
});
