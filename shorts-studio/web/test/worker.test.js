import assert from 'node:assert/strict';
import {afterEach, beforeEach, test} from 'node:test';
import worker, {autopilotSettings, filmRequest, makeSession, validSession} from '../src/worker.js';

const env = {STUDIO_PASSWORD: 'correct horse', STUDIO_GITHUB_TOKEN: 'github_pat_x', GITHUB_REPO: 'me/studio',
  GITHUB_BRANCH: 'main', ASSETS: {fetch: async () => new Response('<html>page</html>')}};
const BASE = 'https://studio.example.workers.dev';
let calls, routes, realFetch;

beforeEach(() => {
  calls = [];
  routes = [];
  realFetch = globalThis.fetch;
  globalThis.fetch = async (url, options = {}) => {
    const request = {url: String(url), method: options.method || 'GET', headers: options.headers || {}, body: options.body};
    calls.push(request);
    for (const [method, pattern, reply] of routes) {
      if (method === request.method && pattern.test(request.url)) return reply(request);
    }
    return new Response('{}', {status: 404});
  };
});
afterEach(() => { globalThis.fetch = realFetch; });

const on = (method, pattern, reply) => routes.push([method, pattern, reply]);
const reply = (value, status = 200, headers = {}) => () => new Response(value === null ? null : JSON.stringify(value), {status, headers});

async function call(path, {method = 'GET', body, cookie, headers = {}} = {}) {
  return worker.fetch(new Request(BASE + path, {method, body: body === undefined ? undefined : JSON.stringify(body),
    headers: {...(body !== undefined ? {'Content-Type': 'application/json'} : {}), ...(cookie ? {Cookie: cookie} : {}), ...headers}}), env);
}
const session = async () => 'ss=' + await makeSession(env);

const draft = {id: 9, draft: true, name: 'Shorts Studio films', assets: [
  {id: 1, name: 'library.json'}, {id: 2, name: 'film-42.mp4'}, {id: 3, name: 'film-42.png'}]};

test('pages are served without a session; the API requires one', async () => {
  assert.equal(await (await call('/')).text(), '<html>page</html>');
  const response = await call('/api/films');
  assert.equal(response.status, 401);
  assert.equal(calls.length, 0);
});

test('setup errors are explained', async () => {
  const response = await worker.fetch(new Request(BASE + '/api/session'), {...env, STUDIO_GITHUB_TOKEN: ''});
  assert.equal(response.status, 503);
  const {error} = await response.json();
  assert.match(error, /cannot see: STUDIO_GITHUB_TOKEN\. /);
  assert.doesNotMatch(error, /STUDIO_PASSWORD/);
  assert.match(error, /not the Build section/);
});

test('login sets a signed, http-only session cookie', async () => {
  const wrong = await call('/api/login', {method: 'POST', body: {password: 'nope'}});
  assert.equal(wrong.status, 401);
  const right = await call('/api/login', {method: 'POST', body: {password: 'correct horse'}});
  assert.equal(right.status, 200);
  const cookie = right.headers.get('Set-Cookie');
  assert.match(cookie, /HttpOnly; Secure; SameSite=Strict/);
  assert.ok(await validSession(env, cookie.split(';')[0]));
  assert.equal(await validSession({...env, STUDIO_PASSWORD: 'changed'}, cookie.split(';')[0]), false);
  assert.equal(await validSession(env, 'ss=99999999999.forgedforgedforgedforged'), false);
  const expired = 'ss=' + await makeSession(env, Date.now() - 40 * 86400000);
  assert.equal(await validSession(env, expired), false);
});

test('cross-site writes are refused', async () => {
  const response = await call('/api/films', {method: 'POST', body: {brief: 'x'}, cookie: await session(),
    headers: {Origin: 'https://evil.example'}});
  assert.equal(response.status, 403);
  assert.equal(calls.length, 0);
});

test('creating a film dispatches the workflow with validated inputs', async () => {
  on('POST', /\/actions\/workflows\/shorts-studio-claude\.yml\/dispatches$/, reply(null, 204));
  const response = await call('/api/films', {method: 'POST', cookie: await session(), body: {brief: ' Tides ',
    seconds: 45, voice: 'ar-LB-RamiNeural', writer: 'backup', research: 'off', publish: true, privacy: 'unlisted'}});
  assert.equal(response.status, 202);
  const {requestId} = await response.json();
  assert.match(requestId, /^[0-9a-f]{8}$/);
  const sent = JSON.parse(calls[0].body);
  assert.deepEqual(sent, {ref: 'main', inputs: {brief: 'Tides', seconds: '45', voice: 'ar-LB-RamiNeural', writer: 'backup',
    research: 'off', publish: 'true', privacy: 'unlisted', request_id: requestId}});
  assert.equal(calls[0].headers.Authorization, 'Bearer github_pat_x');
});

test('invalid film requests never reach GitHub', async () => {
  for (const body of [{seconds: 5}, {voice: 'robot'}, {writer: 'gpt'}, {privacy: 'friends'}, {brief: 'x'.repeat(5000)}]) {
    assert.throws(() => filmRequest(body));
  }
  const response = await call('/api/films', {method: 'POST', cookie: await session(), body: {seconds: 99}});
  assert.equal(response.status, 400);
  assert.equal(calls.length, 0);
});

test('runs report progress for active jobs', async () => {
  on('GET', /\/actions\/workflows\/.+\/runs/, reply({workflow_runs: [
    {id: 5, display_title: 'Film abcd1234', status: 'in_progress', conclusion: null, event: 'workflow_dispatch', created_at: 'x', updated_at: 'y', html_url: 'u'},
    {id: 4, display_title: 'Autopilot film', status: 'completed', conclusion: 'success', event: 'schedule', created_at: 'x', updated_at: 'y', html_url: 'u'}]}));
  on('GET', /\/actions\/runs\/5\/jobs$/, reply({jobs: [{name: 'film', status: 'in_progress',
    steps: [{name: 'Install Claude Code', status: 'completed'}, {name: 'Create the film', status: 'in_progress'}]}]}));
  const {runs} = await (await call('/api/runs', {cookie: await session()})).json();
  assert.equal(runs[0].requestId, 'abcd1234');
  assert.equal(runs[0].stage, 'Writing and rendering');
  assert.equal(runs[1].stage, undefined);
  assert.equal(calls.length, 2);
});

test('library lists films from the draft release index', async () => {
  on('GET', /\/releases\?per_page=100$/, reply([{id: 1, draft: false, name: 'v1', assets: []}, draft]));
  on('GET', /\/releases\/assets\/1$/, reply(null, 302, {Location: 'https://objects.example/library'}));
  on('GET', /^https:\/\/objects\.example\/library$/, reply([{run: '42', title: 'Tides', writer: 'backup', poster: 3,
    video: 2, secret: 'ignored'}]));
  const {films} = await (await call('/api/films', {cookie: await session()})).json();
  assert.equal(films.length, 1);
  assert.equal(films[0].title, 'Tides');
  assert.equal(films[0].poster, true);
  assert.equal(films[0].secret, undefined);
  const storage = calls.find((c) => c.url.startsWith('https://objects.example'));
  assert.equal(storage.headers.Authorization, undefined);
});

test('video streams with range support and never leaks the token to storage', async () => {
  on('GET', /\/releases\?per_page=100$/, reply([draft]));
  on('GET', /\/releases\/assets\/2$/, reply(null, 302, {Location: 'https://objects.example/video'}));
  on('GET', /^https:\/\/objects\.example\/video$/, () => new Response('partial', {status: 206,
    headers: {'Content-Range': 'bytes 0-6/100', 'Content-Length': '7'}}));
  const response = await call('/api/films/42/video?download=1', {cookie: await session(), headers: {Range: 'bytes=0-6'}});
  assert.equal(response.status, 206);
  assert.equal(response.headers.get('Content-Type'), 'video/mp4');
  assert.equal(response.headers.get('Content-Range'), 'bytes 0-6/100');
  assert.match(response.headers.get('Content-Disposition'), /^attachment; filename="shorts-studio-42.mp4"/);
  assert.equal(await response.text(), 'partial');
  const storage = calls.find((c) => c.url === 'https://objects.example/video');
  assert.deepEqual(storage.headers, {Range: 'bytes=0-6'});
  assert.equal((await call('/api/films/7/video', {cookie: await session()})).status, 404);
  assert.equal((await call('/api/films/../video', {cookie: await session()})).status, 404);
});

test('deleting a film removes its assets and rewrites the index', async () => {
  on('GET', /\/releases\?per_page=100$/, reply([draft]));
  on('GET', /\/releases\/assets\/1$/, reply(null, 302, {Location: 'https://objects.example/library'}));
  on('GET', /^https:\/\/objects\.example\/library$/, reply([{run: '42'}, {run: '41'}]));
  on('DELETE', /\/releases\/assets\/\d+$/, reply(null, 204));
  on('POST', /^https:\/\/uploads\.github\.com\/repos\/me\/studio\/releases\/9\/assets\?name=library\.json$/, reply({id: 10}, 201));
  const response = await call('/api/films/42', {method: 'DELETE', cookie: await session()});
  assert.equal(response.status, 200);
  const deleted = calls.filter((c) => c.method === 'DELETE').map((c) => c.url.split('/').pop()).sort();
  assert.deepEqual(deleted, ['1', '2', '3']);
  const upload = calls.find((c) => c.url.startsWith('https://uploads.github.com'));
  assert.deepEqual(JSON.parse(new TextDecoder().decode(upload.body)), [{run: '41'}]);
});

test('autopilot settings round-trip through the repository file', async () => {
  const stored = {enabled: false, domain: 'العلوم', days: [0, 1], timezone: 'Asia/Beirut'};
  const content = Buffer.from(JSON.stringify(stored)).toString('base64');
  on('GET', /\/contents\/shorts-studio\/autopilot\/settings\.json\?ref=main$/, reply({content, sha: 'abc'}));
  on('PUT', /\/contents\/shorts-studio\/autopilot\/settings\.json$/, reply({content: {sha: 'def'}}));
  const loaded = await (await call('/api/autopilot', {cookie: await session()})).json();
  assert.deepEqual(loaded, {settings: stored, sha: 'abc'});
  const saved = await call('/api/autopilot', {method: 'PUT', cookie: await session(),
    body: {sha: 'abc', settings: {...stored, enabled: true, days: [6, 0], seconds: 45, publishBackup: true, extra: 'dropped'}}});
  assert.equal(saved.status, 200);
  const put = JSON.parse(calls.at(-1).body);
  assert.equal(put.sha, 'abc');
  assert.equal(put.branch, 'main');
  const written = JSON.parse(Buffer.from(put.content, 'base64').toString());
  assert.deepEqual(written.days, [0, 6]);
  assert.equal(written.domain, 'العلوم');
  assert.equal(written.publishBackup, true);
  assert.equal(written.extra, undefined);
});

test('autopilot validation', () => {
  assert.throws(() => autopilotSettings({days: []}), /day/);
  assert.throws(() => autopilotSettings({days: [7]}), /day/);
  assert.throws(() => autopilotSettings({days: [1], timezone: 'Mars/Base'}), /timezone/);
  assert.throws(() => autopilotSettings({days: [1], enabled: true, domain: ' '}), /subject/);
  assert.equal(autopilotSettings({days: [1]}).voice, 'auto');
});

test('github errors become clear messages', async () => {
  on('GET', /\/actions\/workflows\/.+\/runs/, reply({message: 'Bad credentials'}, 401));
  const response = await call('/api/runs', {cookie: await session()});
  assert.equal(response.status, 502);
  assert.match((await response.json()).error, /rejected the token/);
});

test('renewing Claude starts the sign-in workflow with a fresh request id', async () => {
  on('DELETE', /\/actions\/variables\/STUDIO_LOGIN_CODE$/, reply(null, 404));
  on('PATCH', /\/actions\/variables\/STUDIO_LOGIN_STATE$/, reply(null, 404));
  on('POST', /\/actions\/variables$/, reply(null, 201));
  on('POST', /\/actions\/workflows\/shorts-studio-claude-login\.yml\/dispatches$/, reply(null, 204));
  const response = await call('/api/claude-login', {method: 'POST', cookie: await session()});
  assert.equal(response.status, 202);
  const {nonce} = await response.json();
  assert.match(nonce, /^[0-9a-f]{32}$/);
  const created = JSON.parse(calls.find((c) => c.method === 'POST' && c.url.endsWith('/actions/variables')).body);
  assert.equal(created.name, 'STUDIO_LOGIN_STATE');
  assert.equal(JSON.parse(created.value).nonce, nonce);
  assert.deepEqual(JSON.parse(calls.at(-1).body), {ref: 'main', inputs: {nonce}});
});

test('renewal explains missing token permissions', async () => {
  on('DELETE', /\/actions\/variables\//, reply({message: 'Resource not accessible'}, 403));
  const response = await call('/api/claude-login', {method: 'POST', cookie: await session()});
  assert.equal(response.status, 403);
  assert.match((await response.json()).error, /Variables and Secrets permissions/);
});

test('renewal status only shows official Claude sign-in links for the current request', async () => {
  const nonce = 'a'.repeat(32);
  let state = {nonce, state: 'link', message: 'Open the link', url: 'https://claude.com/cai/oauth/authorize?code=true&state=x'};
  on('GET', /\/actions\/variables\/STUDIO_LOGIN_STATE$/, () => new Response(JSON.stringify({value: JSON.stringify(state)})));
  const cookie = await session();
  let status = await (await call('/api/claude-login?nonce=' + nonce, {cookie})).json();
  assert.equal(status.state, 'link');
  assert.equal(status.url, state.url);
  state = {...state, url: 'https://evil.example/oauth/authorize'};
  assert.equal((await (await call('/api/claude-login?nonce=' + nonce, {cookie})).json()).url, '');
  status = await (await call('/api/claude-login?nonce=' + 'b'.repeat(32), {cookie})).json();
  assert.equal(status.state, 'starting');
  assert.equal((await call('/api/claude-login?nonce=bad', {cookie})).status, 400);
});

test('the one-time code is handed to the waiting runner only', async () => {
  const nonce = 'c'.repeat(32);
  let state = {nonce, state: 'link'};
  on('GET', /\/actions\/variables\/STUDIO_LOGIN_STATE$/, () => new Response(JSON.stringify({value: JSON.stringify(state)})));
  on('PATCH', /\/actions\/variables\/STUDIO_LOGIN_CODE$/, reply(null, 204));
  const cookie = await session();
  const code = 'AbCdEf123456_-xyz#state789';
  const sent = await call('/api/claude-login/code', {method: 'POST', cookie, body: {nonce, code}});
  assert.equal(sent.status, 200);
  const patch = calls.find((c) => c.method === 'PATCH');
  assert.deepEqual(JSON.parse(JSON.parse(patch.body).value), {nonce, code});
  assert.equal((await call('/api/claude-login/code', {method: 'POST', cookie, body: {nonce, code: 'x y'}})).status, 400);
  state = {nonce, state: 'saving'};
  assert.equal((await call('/api/claude-login/code', {method: 'POST', cookie, body: {nonce, code}})).status, 409);
});
