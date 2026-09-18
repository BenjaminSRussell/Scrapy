/**
 * Regression tests for #154:
 * - Activity log messages with HTML render as text (no markup children)
 * - Tab activation calls chart.resize() for charts in that panel
 *
 * Run: APP_JS=/path/to/app.js node tests/unit/dashboard/test_activity_log_and_charts.mjs
 */
import fs from 'fs';
import path from 'path';
import vm from 'vm';
import { fileURLToPath } from 'url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const appJsPath = process.env.APP_JS || path.resolve(__dirname, '../../../../dashboard/app.js');

class FakeElement {
  constructor(tag = 'div', id = '') {
    this.tagName = tag.toUpperCase();
    this.id = id;
    this.className = '';
    this.children = [];
    this.attrs = {};
    this._text = '';
    this._html = '';
    this.listeners = {};
    const self = this;
    this.classList = {
      add(...names) {
        const set = new Set((self.className || '').split(/\s+/).filter(Boolean));
        names.forEach((n) => set.add(n));
        self.className = [...set].join(' ');
      },
      remove(...names) {
        const set = new Set((self.className || '').split(/\s+/).filter(Boolean));
        names.forEach((n) => set.delete(n));
        self.className = [...set].join(' ');
      },
      contains(name) {
        return (self.className || '').split(/\s+/).includes(name);
      },
    };
  }
  setAttribute(k, v) { this.attrs[k] = v; }
  getAttribute(k) { return this.attrs[k] ?? null; }
  addEventListener(type, fn) {
    (this.listeners[type] ||= []).push(fn);
  }
  click() {
    for (const fn of this.listeners.click || []) fn();
  }
  append(...nodes) {
    for (const n of nodes) this.appendChild(n);
  }
  appendChild(child) {
    this.children.push(child);
    return child;
  }
  replaceChildren(...nodes) {
    this.children = [...nodes];
  }
  get textContent() { return this._text; }
  set textContent(v) {
    this._text = String(v);
    this._html = this._text
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;');
  }
  get innerHTML() { return this._html; }
  set innerHTML(v) {
    this._html = String(v);
    this._text = String(v).replace(/<[^>]+>/g, '');
    this.children = [];
  }
}

function makeDocument() {
  const byId = new Map();
  const ensure = (id, tag = 'div') => {
    if (!byId.has(id)) byId.set(id, new FakeElement(tag, id));
    return byId.get(id);
  };
  [
    'throughput-chart', 'stage-progression-chart', 'routing-chart',
    'urls-chart', 'pages-chart', 'summaries-chart',
    'overview-activity', 'activity-log',
    'tab-overview', 'tab-pipeline', 'tab-performance', 'tab-system', 'tab-activity',
    'topbar-status', 'topbar-urls', 'topbar-summaries',
    'refresh-countdown', 'last-refresh-time', 'last-update', 'uptime',
    'redis-keys', 'redis-memory',
    'perf-s1-rate', 'perf-s2-rate', 'perf-s3-rate', 'perf-s4-rate',
  ].forEach((id) => ensure(id, id.includes('chart') ? 'canvas' : 'div'));

  const tabButtons = ['overview', 'pipeline', 'performance', 'system', 'activity'].map((name) => {
    const btn = new FakeElement('button');
    btn.setAttribute('data-tab', name);
    btn.className = name === 'overview' ? 'tab-button active' : 'tab-button';
    return btn;
  });
  const tabContents = ['overview', 'pipeline', 'performance', 'system', 'activity'].map((name) => {
    const el = ensure(`tab-${name}`);
    el.className = name === 'overview' ? 'tab-content active' : 'tab-content';
    return el;
  });

  return {
    readyState: 'complete',
    getElementById: (id) => ensure(id),
    querySelectorAll: (sel) => {
      if (sel === '.tab-button') return tabButtons;
      if (sel === '.tab-content') return tabContents;
      return [];
    },
    createElement: (tag) => new FakeElement(tag),
    addEventListener() {},
    _tabButtons: tabButtons,
  };
}

class FakeChart {
  constructor(canvas, config) {
    this.canvas = canvas;
    this.config = config;
    this.data = config.data;
    this.resizeCalls = 0;
    this.updateCalls = 0;
  }
  resize() { this.resizeCalls += 1; }
  update() { this.updateCalls += 1; }
}

function loadApp(doc) {
  const code = fs.readFileSync(appJsPath, 'utf8');
  // Wrap so we can export let/const bindings for tests without changing production app.js
  const wrapped = `
(function (global) {
${code}
global.__dashboardTest = {
  addActivityLogItem,
  activityLog,
  charts,
  updateActivityLog,
  resizeChartsForTab,
  sanitizeActivityType,
};
})(this);
`;
  const Chart = function (...args) {
    return new FakeChart(...args);
  };
  const context = {
    console: {
      log() {},
      error() {},
    },
    document: doc,
    Chart,
    Date,
    Math,
    Set,
    Map,
    requestAnimationFrame: (fn) => { fn(); return 0; },
    setInterval: () => 0,
    fetch: async () => { throw new Error('offline'); },
  };
  vm.runInNewContext(wrapped, context, { filename: 'app.js' });
  return context.__dashboardTest;
}

function assert(cond, msg) {
  if (!cond) throw new Error(msg);
}

function testXssRendersAsText() {
  const doc = makeDocument();
  const api = loadApp(doc);
  api.activityLog.length = 0;
  const payload = `<img src=x onerror=alert(1)><script>alert(1)</script>`;
  api.addActivityLogItem('danger', payload);

  for (const id of ['overview-activity', 'activity-log']) {
    const container = doc.getElementById(id);
    assert(container.children.length === 1, `${id} should have one item`);
    const msg = container.children[0].children.find((c) => c.className === 'activity-message');
    assert(msg, `${id} missing activity-message`);
    assert(msg.textContent === payload, `${id} textContent mismatch: ${JSON.stringify(msg.textContent)}`);
    assert(!String(msg.innerHTML).includes('<script>'), `${id} raw <script> leaked into innerHTML`);
    assert(msg.children.length === 0, `${id} message should have no element children from HTML parse`);
  }
  // Hostile type must not become an arbitrary class
  assert(api.sanitizeActivityType('"><img src=x>') === 'info', 'sanitizeActivityType should whitelist');
  console.log('ok: activity log XSS renders as text');
}

function testChartResizeOnTab() {
  const doc = makeDocument();
  const api = loadApp(doc);
  const charts = api.charts;
  assert(charts.stageProgression && charts.routing && charts.urls, 'charts initialized');

  const beforePipe = charts.stageProgression.resizeCalls + charts.routing.resizeCalls;
  doc._tabButtons.find((b) => b.getAttribute('data-tab') === 'pipeline').click();
  const afterPipe = charts.stageProgression.resizeCalls + charts.routing.resizeCalls;
  assert(afterPipe > beforePipe, 'pipeline tab should resize stage/routing charts');

  const beforePerf = charts.urls.resizeCalls + charts.pages.resizeCalls + charts.summaries.resizeCalls;
  doc._tabButtons.find((b) => b.getAttribute('data-tab') === 'performance').click();
  const afterPerf = charts.urls.resizeCalls + charts.pages.resizeCalls + charts.summaries.resizeCalls;
  assert(afterPerf > beforePerf, 'performance tab should resize urls/pages/summaries charts');

  // Direct API also resizes
  const before = charts.throughput.resizeCalls;
  api.resizeChartsForTab('overview');
  assert(charts.throughput.resizeCalls > before, 'resizeChartsForTab(overview) should resize throughput');

  console.log('ok: chart.resize on tab switch');
}

testXssRendersAsText();
testChartResizeOnTab();
console.log('all #154 regression tests passed');
