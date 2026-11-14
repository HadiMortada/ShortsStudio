import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {test} from 'node:test';
import vm from 'node:vm';

const html = readFileSync(new URL('../public/index.html', import.meta.url), 'utf8');
const fullScript = [...html.matchAll(/<script>([\s\S]*?)<\/script>/g)].at(-1)[1];
const script = fullScript.slice(fullScript.indexOf('// ---- Claude usage:')).split('// ---- Account and Claude status ----')[0];

function harness(cached = null) {
  const nodes = new Map(), values = new Map([['claudeUsage', cached]]), timers = new Map(), events = new Map();
  let timerId = 0;
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
  for (const id of ['usageButton', 'usageArc', 'usageNumber', 'sheetBody', 'sheet', 'app', 'claudeLineText', 'claudeLine', 'claudeLineAction', 'avatarFlag', 'claudePill', 'claudeDetail', 'claudeButton']) nodes.set(id, new Node());
  let reading = {state: 'ready', checkedAt: Math.floor(Date.now() / 1000), sessionPercent: 49.75, weeklyPercent: 83.125,
    sessionResetsAt: '2026-11-14T18:00:00Z', weeklyResetsAt: '2026-11-20T18:00:00Z', stale: false};
  const requests = [];
  let transport = async () => reading;
  const context = {Date, Number, String, Math, console, AbortController, $, state: {account: null}, startLogin() {},
    store: {get: (key, fallback) => values.get(key) ?? fallback, set: (key, value) => values.set(key, value)},
    document: {hidden: false, addEventListener(type, handler) { events.set(type, handler); }},
    addEventListener(type, handler) { events.set(type, handler); },
    setInterval() {}, setTimeout(fn, ms) { timers.set(++timerId, {fn, ms}); return timerId; }, clearTimeout(id) { timers.delete(id); }, toast() {},
    api: async (path, options) => { requests.push([path, options]); return transport(path, options); },
    h(tag, attrs = {}, ...children) {
      const node = new Node(); node.tag = tag; node.attrs = attrs;
      node.children = children.flat().filter(c => c !== false && c !== null && c !== undefined);
      if (attrs.id) nodes.set(attrs.id, node);
      return node;
    }};
  vm.createContext(context); vm.runInContext(script, context);
  return {nodes, $, requests, timers, events, values, context, run: code => vm.runInContext(code, context),
    setReading: value => { reading = value; }, setTransport: fn => { transport = fn; }};
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
    assert.equal(ui.$('usageNumber').textContent, state.includes("state:'error'") ? '—' : '30%');
    assert.equal(ui.$('usageButton').dataset.tone, 'unknown');
  }
});

test('click opens compact usage bars with exact readings and refresh', async () => {
  const ui = harness();
  await ui.run('loadUsage()');
  ui.$('usageButton').events.click();
  await ui.run('loadUsage()');
  assert.equal(ui.$('sheet').open, true);
  const text = ui.$('sheetBody').textContent;
  for (const expected of ['Session', 'Weekly', '49.75%', '83.125%', 'Resets', 'Refresh']) assert.ok(text.includes(expected), expected);
  assert.match(ui.$('usageButton').attrs['aria-label'], /49.75%.*83.125%/);
  assert.equal(ui.$('usage-session-meter').attrs['aria-valuenow'], 49.75);
  assert.equal(ui.$('usage-weekly-meter').attrs['aria-valuenow'], 83.125);
  assert.equal(ui.$('usage-session-meter').attrs['data-tone'], 'green');
  assert.equal(ui.$('usage-weekly-meter').attrs['data-tone'], 'orange');
  assert.equal(ui.$('usageClose').attrs['aria-label'], 'Close');
  for (const removed of ['The circle shows', 'Last checked', 'Green below', 'Videos start only']) assert.ok(!text.includes(removed), removed);
  assert.equal(ui.requests.some(([path]) => path.includes('/films')), false);
  await ui.run('refreshUsage()');
  assert.equal(ui.requests.at(-1)[0], '/api/claude-usage/refresh');
  assert.equal(ui.requests.at(-1)[1].method, 'POST');
});

test('a saved token never claims verified Claude usage access', () => {
  const ui = harness();
  ui.run(fullScript.slice(fullScript.indexOf('function claudeStatus()')).split('async function loadAccount()')[0]);
  for (const [accountLogin, usage, text] of [
    [false, {state: 'unknown'}, 'Reconnect needed'],
    [true, {state: 'unknown'}, 'Sign-in saved'],
    [true, {state: 'error', stale: true}, 'Usage unavailable'],
    [true, {state: 'ready', checkedAt: Date.now() / 1000, stale: false}, 'Connected']]) {
    ui.run(`state.account={claude:{connected:true,accountLogin:${accountLogin}}};claudeUsage=${JSON.stringify(usage)};renderClaudeLine();`);
    assert.equal(ui.$('claudePill').textContent.trim(), text);
    assert.equal(ui.$('claudeButton').textContent, 'Reconnect Claude');
  }
});

test('settings sliders show their values and save both independent limits as numbers', () => {
  const ui = harness(), patches = [];
  for (const id of ['s_sessionUsageLimit', 's_weeklyUsageLimit']) {
    ui.nodes.set(id, {value: '70', events: {}, addEventListener(k, fn) { this.events[k] = fn; }});
    ui.nodes.set(id + '_value', {textContent: '70%'});
  }
  ui.nodes.set('settingsSaved', {});
  const context = {$: ui.$, Number, saveSettings: patch => patches.push(patch)};
  vm.createContext(context);
  const handlers = fullScript.slice(fullScript.indexOf("for (const key of ['sessionUsageLimit', 'weeklyUsageLimit'])")).split('function setTheme')[0];
  vm.runInContext(handlers, context);
  for (const [key, value] of [['sessionUsageLimit', 0], ['weeklyUsageLimit', 95]]) {
    ui.$('s_' + key).value = String(value);
    ui.$('s_' + key).events.input();
    assert.equal(ui.$('s_' + key + '_value').textContent, value + '%');
    ui.$('s_' + key).events.change();
    assert.equal(patches.at(-1)[key], value);
  }
});

const readyUsage = (weeklyPercent = 94, age = 0) => ({state: 'ready', checkedAt: Math.floor(Date.now() / 1000) - age,
  sessionPercent: 1, weeklyPercent, stale: age > 300});

test('an out-of-date reading refreshes automatically without opening the popup', async () => {
  const ui = harness();
  ui.setTransport(async path => path.endsWith('/refresh') ? readyUsage() : readyUsage(80, 600));
  await ui.run('loadUsage()');
  assert.deepEqual(ui.requests.map(([path]) => path), ['/api/claude-usage', '/api/claude-usage/refresh']);
  assert.equal(ui.$('sheet').open, false);
  assert.equal(ui.$('usageNumber').textContent, '94%');
  assert.equal(ui.$('usageButton').dataset.checking, 'false');
  assert.equal(ui.requests.some(([path]) => /films|runs/.test(path)), false);
});

test('reopening immediately shows the last reading and animates during the network request', async () => {
  const ui = harness({...readyUsage(94, 600), refreshing: true, requestedAt: 1, accessToken: 'never-cache-this'});
  let finish;
  ui.setTransport(() => new Promise(resolve => { finish = resolve; }));
  const check = ui.run('loadUsage()');
  assert.equal(ui.$('usageNumber').textContent, '94%');
  assert.equal(ui.$('usageButton').dataset.tone, 'unknown', 'last known readings are not presented as current');
  assert.equal(ui.$('usageButton').dataset.checking, 'true');
  assert.equal(ui.$('usageButton').attrs['aria-busy'], 'true');
  assert.match(ui.$('usageButton').attrs['aria-label'], /Checking.*Last known/);
  ui.$('usageButton').events.click();
  assert.equal(ui.requests.length, 1, 'opening the sheet shares the existing check');
  assert.match(ui.$('sheetBody').textContent, /Checking/);
  finish(readyUsage(95)); await check;
  assert.equal(ui.$('usageNumber').textContent, '95%');
  assert.equal(ui.$('usageButton').attrs['aria-busy'], 'false');
  const cached = ui.values.get('claudeUsage');
  for (const field of ['refreshing', 'requestedAt', 'accessToken', 'stale']) assert.equal(cached[field], undefined);
});

test('returning to the app, page restoration and reconnecting the network refresh usage automatically', async () => {
  for (const event of ['visibilitychange', 'pageshow', 'online']) {
    const ui = harness();
    ui.setTransport(async path => path.endsWith('/refresh') ? readyUsage(95) : readyUsage(94, 600));
    await ui.events.get(event)();
    assert.equal(ui.$('usageNumber').textContent, '95%', event);
    assert.equal(ui.requests.at(-1)[0], '/api/claude-usage/refresh', event);
  }
  const hidden = harness(); hidden.context.document.hidden = true;
  await hidden.events.get('visibilitychange')(); await hidden.run('loadUsage()');
  assert.equal(hidden.requests.length, 0);
});

test('a runner check keeps animating between polls and stops when the fresh result arrives', async () => {
  const ui = harness();
  ui.setReading({...readyUsage(94, 600), refreshing: true});
  await ui.run('loadUsage()');
  assert.equal(ui.requests.length, 1, 'a pending workflow is not dispatched again');
  assert.equal(ui.$('usageButton').dataset.checking, 'true');
  assert.ok([...ui.timers.values()].some(t => t.ms === 3000));
  ui.setReading(readyUsage(96)); await ui.run('loadUsage()');
  assert.equal(ui.$('usageButton').dataset.checking, 'false');
  assert.equal(ui.$('usageNumber').textContent, '96%');
});

test('a timed out request stops checking, keeps the last reading and schedules recovery', async () => {
  const ui = harness(readyUsage());
  ui.setTransport((path, {signal}) => new Promise((resolve, reject) => signal.addEventListener('abort', () => reject(Object.assign(new Error('timeout'), {name: 'AbortError'})))));
  const check = ui.run('loadUsage()');
  [...ui.timers.values()].find(t => t.ms === 20000).fn();
  await check;
  assert.equal(ui.$('usageButton').dataset.checking, 'false');
  assert.equal(ui.$('usageButton').dataset.tone, 'unknown');
  assert.equal(ui.$('usageNumber').textContent, '94%');
  assert.ok([...ui.timers.values()].some(t => t.ms === 30000));
});

test('waking replaces a suspended request and ignores its late response', async () => {
  const ui = harness(readyUsage());
  let oldResponse;
  ui.setTransport(() => new Promise(resolve => { oldResponse = resolve; }));
  const oldCheck = ui.run('loadUsage()');
  ui.run('usageStartedAt = Date.now() - 25000');
  ui.setTransport(async () => readyUsage(96));
  await ui.events.get('pageshow')();
  assert.equal(ui.$('usageNumber').textContent, '96%');
  oldResponse(readyUsage(10)); await oldCheck;
  assert.equal(ui.$('usageNumber').textContent, '96%');
});

test('sign-out and a reconnected account discard old readings and ignore in-flight responses', async () => {
  const ui = harness(readyUsage());
  let finish;
  ui.setTransport(() => new Promise(resolve => { finish = resolve; }));
  const check = ui.run('loadUsage()');
  ui.run('resetUsage(); renderUsage()');
  assert.equal(ui.values.get('claudeUsage'), null);
  assert.equal(ui.$('usageNumber').textContent, '—');
  finish(readyUsage()); await check;
  assert.equal(ui.$('usageNumber').textContent, '—');
  assert.equal(ui.values.get('claudeUsage'), null);
});
