import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {test} from 'node:test';
import vm from 'node:vm';

const html = readFileSync(new URL('../public/index.html', import.meta.url), 'utf8');
const fullScript = [...html.matchAll(/<script>([\s\S]*?)<\/script>/g)].at(-1)[1];
const script = fullScript.slice(fullScript.indexOf('// ---- Claude usage:')).split('// ---- Account and Claude status ----')[0];

function harness() {
  const nodes = new Map();
  class Node {
    constructor() { this.children = []; this.attrs = {}; this.dataset = {}; this.events = {}; this.open = false; this.value = ''; }
    get textContent() { return this.value + this.children.map(c => typeof c === 'string' ? c : c.textContent).join(' '); }
    set textContent(v) { this.value = v; this.children = []; }
    setAttribute(k, v) { this.attrs[k] = v; }
    append(...children) { this.children.push(...children); }
    replaceChildren(...children) { this.value = ''; this.children = children; }
    addEventListener(k, fn) { this.events[k] = fn; }
    showModal() { this.open = true; }
    close() { this.open = false; }
  }
  const $ = id => nodes.get(id) || null;
  for (const id of ['usageButton', 'usageArc', 'usageNumber', 'sheetBody', 'sheet', 'app']) nodes.set(id, new Node());
  let reading = {state: 'ready', checkedAt: Math.floor(Date.now() / 1000), sessionPercent: 49.75, weeklyPercent: 83.125,
    sessionResetsAt: '2026-11-14T18:00:00Z', weeklyResetsAt: '2026-11-20T18:00:00Z', stale: false};
  const requests = [];
  const context = {Date, Number, String, Math, console, $, document: {hidden: false, addEventListener() {}},
    setInterval() {}, setTimeout() { return 1; }, clearTimeout() {}, toast() {},
    api: async (path, options) => { requests.push([path, options]); return reading; },
    h(tag, attrs, ...children) {
      const node = new Node(); node.tag = tag; node.attrs = attrs;
      node.children = children.flat().filter(c => c !== false && c !== null && c !== undefined);
      if (attrs.id) nodes.set(attrs.id, node);
      return node;
    }};
  vm.createContext(context); vm.runInContext(script, context);
  return {nodes, $, requests, run: code => vm.runInContext(code, context), setReading: value => { reading = value; }};
}

test('the complete page script parses', () => { new vm.Script(fullScript); });

test('usage circle changes color at exact thresholds and stale/error data stays neutral', () => {
  const ui = harness();
  for (const [percent, tone] of [[0, 'green'], [69.99, 'green'], [70, 'yellow'], [79.99, 'yellow'], [80, 'orange'], [89.99, 'orange'], [90, 'red'], [100, 'red']]) {
    ui.run(`claudeUsage = {state:'ready',checkedAt:Date.now()/1000,sessionPercent:0,weeklyPercent:${percent},stale:false}; renderUsage();`);
    assert.equal(ui.$('usageButton').dataset.tone, tone);
    assert.equal(ui.$('usageNumber').textContent, Math.floor(percent) + '%');
  }
  ui.run("claudeUsage = {state:'ready',checkedAt:Date.now()/1000,sessionPercent:91.5,weeklyPercent:20,stale:false}; renderUsage();");
  assert.equal(ui.$('usageButton').dataset.tone, 'red', 'uses the higher reading');
  for (const state of ["{state:'error',stale:true}", "{state:'ready',checkedAt:Date.now()/1000-600,sessionPercent:20,weeklyPercent:30,stale:false}"]) {
    ui.run(`claudeUsage = ${state}; renderUsage();`);
    assert.equal(ui.$('usageNumber').textContent, '—');
    assert.equal(ui.$('usageButton').dataset.tone, 'unknown');
  }
});

test('click opens exact readings, reset times, last check and Refresh without starting a film', async () => {
  const ui = harness();
  await ui.run('loadUsage()');
  ui.$('usageButton').events.click();
  await Promise.resolve(); await Promise.resolve();
  assert.equal(ui.$('sheet').open, true);
  const text = ui.$('sheetBody').textContent;
  for (const expected of ['49.75%', '83.125%', 'Resets', 'Last checked', 'Refresh', 'Close']) assert.ok(text.includes(expected), expected);
  assert.match(ui.$('usageButton').attrs['aria-label'], /49.75%.*83.125%/);
  assert.equal(ui.requests.some(([path]) => path.includes('/films')), false);
  await ui.run('refreshUsage()');
  assert.equal(ui.requests.at(-1)[0], '/api/claude-usage/refresh');
  assert.equal(ui.requests.at(-1)[1].method, 'POST');
});
