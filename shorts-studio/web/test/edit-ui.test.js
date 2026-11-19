import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {test} from 'node:test';
import vm from 'node:vm';

const html = readFileSync(new URL('../public/index.html', import.meta.url), 'utf8');
const full = [...html.matchAll(/<script>([\s\S]*?)<\/script>/g)].at(-1)[1];
const progress = full.split('// ---- In progress ----')[1].split('// ---- Films ----')[0];
const upload = full.split('// ---- Edit video: upload in pieces (with progress), then GitHub edits it ----')[1].split('// ---- Autopilot topics ----')[0];

function harness({replies = [], runs = []} = {}) {
  const values = new Map(), nodes = new Map(), calls = [], sent = [], toasts = [];
  const store = {get: (k, fallback) => values.has(k) ? values.get(k) : fallback, set: (k, v) => values.set(k, v)};
  const node = () => ({value: '', hidden: false, children: [], style: {}, attrs: {}, listeners: {}, files: null, textContent: '',
    replaceChildren(...children) { this.children = children; }, setAttribute(k, v) { this.attrs[k] = v; },
    addEventListener(name, fn) { this.listeners[name] = fn; }, classList: {add() {}, remove() {}}});
  const $ = (id) => { if (!nodes.has(id)) nodes.set(id, node()); return nodes.get(id); };
  class XMLHttpRequest {
    constructor() { this.upload = {}; }
    open(method, url) { this.method = method; this.url = url; }
    setRequestHeader(k, v) { this.type = v; }
    abort() { this.onabort(); }
    send(blob) {
      sent.push({url: this.url, size: blob.size, type: this.type});
      const [status, body] = replies.length ? replies.shift() : [200, {}];
      queueMicrotask(() => {
        if (status === 0) return this.onerror();
        this.upload.onprogress?.({loaded: blob.size});
        this.status = status; this.responseText = JSON.stringify(body); this.onload();
      });
    }
  }
  const context = {store, $, Date, Math, Set, Object, JSON, Promise, Error, Uint8Array, String, Number,
    state: {seconds: 30}, STAGES: ['Starting', 'Preparing the studio', 'Writing and rendering', 'Packing the film', 'Saving to your library'],
    EDIT_STAGES: ['Starting', 'Preparing the editor', 'Receiving your video', 'Editing', 'Saving to your library'],
    document: {addEventListener() {}}, navigator: {}, crypto: {getRandomValues: (a) => a.fill(171)}, XMLHttpRequest, queueMicrotask,
    // Retries run at once; polling waits.
    setTimeout: (fn, ms) => { if (ms < 7000) fn(); return 0; }, clearTimeout() {}, addEventListener() {}, toast: (m) => toasts.push(m), loadFilms() {},
    igChosen: () => [], confirmSheet: async () => true, lock() { context.locked = true; },
    api: async (path, options) => { calls.push({path, options}); return path === '/api/runs' ? {runs} : {requestId: 'abababab'}; },
    h: (tag, attrs, ...children) => ({tag, attrs, children: children.flat().filter((x) => x !== null)})};
  vm.createContext(context);
  vm.runInContext(progress + upload, context);
  return {context, store, calls, sent, toasts, $, run: (code) => vm.runInContext(code, context)};
}
const file = (size, name = 'My trip.MOV', type = 'video/quicktime') => ({size, name, type,
  slice: (a, b) => ({size: b - a})});
const MB = 1024 * 1024;

test('a video uploads in 20 MB pieces, then the edit starts and is followed', async () => {
  const t = harness();
  await t.run('uploadVideo')(file(45 * MB));
  assert.deepEqual(t.sent.map((x) => [x.url, x.size]), [
    ['/api/edits/upload?id=abababab&part=0', 20 * MB], ['/api/edits/upload?id=abababab&part=1', 20 * MB],
    ['/api/edits/upload?id=abababab&part=2', 5 * MB]]);
  assert.equal(t.sent[0].type, 'application/octet-stream');
  const start = t.calls.find((c) => c.path === '/api/edits');
  assert.deepEqual(JSON.parse(start.options.body), {id: 'abababab', parts: 3, name: 'My trip.MOV'});
  assert.equal(JSON.stringify(t.store.get('pending').map((p) => [p.id, p.brief, p.kind])), '[["abababab","My trip","edit"]]');
  assert.match(t.toasts.at(-1), /editing/);
  assert.equal(t.$('editUpload').hidden, true);
  assert.equal(t.$('editFile').value, '');
});

test('a dropped connection is retried; a refused piece stops the upload', async () => {
  const retried = harness({replies: [[0, {}], [502, {}], [200, {}]]});
  await retried.run('uploadVideo')(file(3 * MB));
  assert.equal(retried.sent.length, 3);
  assert.ok(retried.calls.some((c) => c.path === '/api/edits'));

  const refused = harness({replies: [[413, {error: 'Upload piece is too large'}]]});
  await refused.run('uploadVideo')(file(3 * MB));
  assert.equal(refused.sent.length, 1);
  assert.ok(!refused.calls.some((c) => c.path === '/api/edits'));
  assert.equal(refused.toasts.at(-1), 'Upload piece is too large');
});

test('only videos within the limits are sent', async () => {
  const t = harness();
  await t.run('uploadVideo')(file(10, 'notes.pdf', 'application/pdf'));
  await t.run('uploadVideo')(file(1300 * MB));
  await t.run('uploadVideo')(file(0));
  assert.equal(t.sent.length, 0);
  assert.deepEqual(t.toasts, ['Choose a video file.', 'This video is too large. Upload one under 1.2 GB.', 'This video is empty.']);
  await t.run('uploadVideo')(file(MB, 'clip.mp4', ''));   // some phones send no type: the name decides
  assert.equal(t.sent.length, 1);
});

test('edits show on the Edit page with Retry; films stay on Create', async () => {
  const now = new Date().toISOString();
  const t = harness({runs: [
    {id: 9, kind: 'edit', requestId: 'abababab', status: 'in_progress', stage: 'Editing', createdAt: now, updatedAt: now},
    {id: 8, kind: 'edit', requestId: 'cdcdcdcd', status: 'completed', conclusion: 'failure', createdAt: now, updatedAt: now, url: 'u'},
    {id: 7, kind: 'film', requestId: 'efefefef', status: 'in_progress', stage: 'Writing and rendering', createdAt: now, updatedAt: now}]});
  t.store.set('briefs', {abababab: 'My trip'});
  t.store.set('done', [8]);
  await t.run('pollRuns')();
  const edits = t.$('editJobs').children, films = t.$('jobs').children;
  assert.equal(edits.length, 2);
  assert.equal(films.length, 1);
  assert.equal(t.$('editMaking').hidden, false);
  assert.equal(t.$('navEditDot').hidden, false);
  const text = (node) => typeof node === 'string' ? node : (node.children || []).map(text).join(' ');
  assert.match(text(edits[0]), /My trip.*Editing · 0 min/);
  assert.match(text(edits[1]), /Your video.*Did not finish.*Retry/);
  assert.doesNotMatch(text(edits[1]), /Continue/);
  const retry = edits[1].children.at(-1).children.at(-1);
  await retry.attrs.onclick();
  assert.ok(t.calls.some((c) => c.path === '/api/runs/8/continue'));
  assert.ok(!t.store.get('continued', []).includes(8));   // a retried edit keeps its run, so it is not hidden
});
