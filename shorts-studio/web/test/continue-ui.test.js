import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {test} from 'node:test';
import vm from 'node:vm';

const html = readFileSync(new URL('../public/index.html', import.meta.url), 'utf8');
const full = [...html.matchAll(/<script>([\s\S]*?)<\/script>/g)].at(-1)[1];
const script = full.split('// ---- In progress ----')[1].split('// ---- Films ----')[0];
function harness() {
  const values = new Map(), nodes = new Map(), calls = [];
  const store = {get: (k, fallback) => values.has(k) ? values.get(k) : fallback, set: (k, v) => values.set(k, v)};
  const $ = id => { if (!nodes.has(id)) nodes.set(id, {value: id === 'writer' ? 'claude' : id === 'voice' ? 'auto' : 'private',
    checked: false, hidden: false, children: [], replaceChildren(...children) { this.children = children; }}); return nodes.get(id); };
  const context = {store, $, Date, Math, Set, Object, JSON, state: {seconds: 30}, STAGES: [], document: {addEventListener() {}},
    setTimeout() {}, clearTimeout() {}, toast() {}, loadFilms() {}, igChosen: () => [],
    api: async (path, options) => { calls.push({path, options}); return path === '/api/runs' ? {runs: []} : {requestId: 'abcd1234'}; },
    h: (tag, attrs, ...children) => ({tag, attrs, children: children.flat().filter(x => x !== null)})};
  vm.createContext(context); vm.runInContext(script, context);
  return {store, calls, run: code => vm.runInContext(code, context)};
}
test('failed card offers Continue and Details while an active card offers Cancel', () => {
  const ui = harness();
  const failed = ui.run("jobRow('Science', 'Did not finish', 1, {failed:true,runId:42,url:'https://github.com/run'})");
  const actions = failed.children[3];
  assert.deepEqual(actions.children.map(x => x.children[0]), ['Details', 'Remove', 'Continue']);
  assert.equal(actions.children[2].attrs['aria-label'], 'Continue Science');
  const active = ui.run("jobRow('Science', 'Working', 0.5, {runId:42})");
  assert.equal(active.children[0].children[1].children[0], 'Cancel');
});
test('long Arabic, mixed-direction and unbroken titles keep Continue in a separate action row', () => {
  const ui = harness();
  for (const title of ['أنشئ فيديو عن غدير خُم باللغة العربية '.repeat(80), 'Ghadir غدير خُم 2026 '.repeat(80), 'x'.repeat(4000)]) {
    const card = ui.run(`jobRow(${JSON.stringify(title)}, 'Did not finish', 1, {failed:true,runId:42,url:'https://github.com/run'})`);
    const heading = card.children[0], actions = card.children[3];
    assert.equal(heading.children.length, 1, 'the title cannot displace actions in the same row');
    assert.equal(heading.children[0].attrs.dir, 'auto');
    assert.equal(actions.attrs.class, 'job-actions');
    assert.equal(actions.attrs.dir, 'ltr');
    assert.equal(actions.children[2].children[0], 'Continue');
    assert.equal(actions.children[2].attrs.type, 'button');
    assert.match(actions.children[2].attrs.class, /job-continue/);
  }
});
test('Continue keeps the saved options, prevents duplicate taps and follows the resumed run', async () => {
  const ui = harness();
  const saved = {brief: 'The full original brief', seconds: 45, voice: 'auto', writer: 'auto', publish: true, privacy: 'public'};
  ui.store.set('filmRequests', {old: saved});
  await Promise.all([ui.run("continueRun(42, 'Science', 'old')"), ui.run("continueRun(42, 'Science', 'old')")]);
  const posts = ui.calls.filter(x => x.path === '/api/runs/42/continue');
  assert.equal(posts.length, 1);
  assert.deepEqual(JSON.parse(posts[0].options.body), saved);
  assert.equal(ui.store.get('pending')[0].id, 'abcd1234');
  assert.equal(ui.store.get('continued')[0], 42);
});
test('Clear all hides unfinished films, forgets waiting ones and stops running ones', async () => {
  const ui = harness();
  ui.store.set('pending', [{id: 'p1', brief: 'Waiting', at: Date.now()}]);
  ui.run("confirmSheet = async () => true; jobsNow = {failed: [7, 8], running: [9], pending: 1}");
  await ui.run('clearJobs()');
  assert.deepEqual([...ui.store.get('hiddenRuns')], [7, 8]);
  assert.equal(ui.store.get('pending').length, 0);
  assert.deepEqual(ui.calls.filter(x => x.path.endsWith('/cancel')).map(x => x.path), ['/api/runs/9/cancel']);
  ui.run('hideRuns([8, 10])');
  assert.deepEqual([...ui.store.get('hiddenRuns')], [7, 8, 10]);
});
test('Clear all does nothing when the owner says no', async () => {
  const ui = harness();
  ui.run("confirmSheet = async () => false; jobsNow = {failed: [7], running: [9], pending: 0}");
  await ui.run('clearJobs()');
  assert.equal(ui.store.get('hiddenRuns'), undefined);
  assert.equal(ui.calls.length, 0);
});
