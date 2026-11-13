import assert from 'node:assert/strict';
import {afterEach, beforeEach, test} from 'node:test';
import worker, {autopilotTick, dueSlot, vapidHeader, autopilotSettings, cleanIdeas, filmRequest, hashtags, makeSession, publishRequest, suggestHashtags, validSession} from '../src/worker.js';
import nacl from '../src/vendor/nacl-fast.cjs';
import blake from '../src/vendor/blake2b.cjs';

const repoKeys = nacl.box.keyPair.fromSecretKey(new Uint8Array(32).fill(7));
function unseal(b64) {
  const sealed = Uint8Array.from(Buffer.from(b64, 'base64'));
  const epk = sealed.slice(0, 32);
  const input = new Uint8Array(64); input.set(epk, 0); input.set(repoKeys.publicKey, 32);
  const opened = nacl.box.open(sealed.slice(32), blake.blake2b(input, undefined, 24), epk, repoKeys.secretKey);
  return opened && new TextDecoder().decode(opened);
}
function githubSecretRoutes() {
  on('GET', /\/actions\/secrets\/public-key$/, reply({key_id: 'k1', key: Buffer.from(repoKeys.publicKey).toString('base64')}));
  on('PUT', /\/actions\/secrets\/STUDIO_GITHUB_TOKEN$/, reply(null, 201));
}

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
    {id: 4, display_title: 'Autopilot film', status: 'completed', conclusion: 'success', event: 'schedule', created_at: 'x', updated_at: 'y', html_url: 'u'},
    {id: 3, display_title: 'Film 11112222', status: 'completed', conclusion: 'failure', event: 'workflow_dispatch',
      created_at: '2026-11-11T11:33:44Z', run_started_at: '2026-11-11T11:33:44Z', updated_at: '2026-11-11T11:33:47Z', html_url: 'u'}]}));
  on('GET', /\/actions\/runs\/5\/jobs$/, reply({jobs: [{name: 'film', status: 'in_progress',
    steps: [{name: 'Install Claude Code', status: 'completed'}, {name: 'Create the film', status: 'in_progress'}]}]}));
  const {runs} = await (await call('/api/runs', {cookie: await session()})).json();
  assert.equal(runs[0].requestId, 'abcd1234');
  assert.equal(runs[0].stage, 'Writing and rendering');
  assert.equal(runs[1].stage, undefined);
  assert.equal(runs[1].blocked, false);
  assert.equal(runs[2].blocked, true);
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
  const put = JSON.parse(calls.find((c) => c.method === 'PUT' && c.url.includes('/contents/shorts-studio/autopilot/settings.json')).body);
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
  assert.equal(autopilotSettings({days: [1]}).endCard, true);
  assert.equal(autopilotSettings({days: [1], endCard: false}).endCard, false);
  assert.deepEqual(autopilotSettings({days: [1]}).avoid, []);
  assert.deepEqual(autopilotSettings({days: [1], avoid: ['israel', ' Pork ', 'pork', 'لحم الخنزير']}).avoid, ['israel', 'pork', 'لحم الخنزير']);
  assert.deepEqual(autopilotSettings({days: [1], avoid: []}).avoid, []);
  assert.throws(() => autopilotSettings({days: [1], avoid: ['<script>']}), /plain words/);
});

test('github errors become clear messages', async () => {
  on('GET', /\/actions\/workflows\/.+\/runs/, reply({message: 'Bad credentials'}, 401));
  const response = await call('/api/runs', {cookie: await session()});
  assert.equal(response.status, 502);
  assert.match((await response.json()).error, /rejected the token/);
});

test('renewing Claude starts the sign-in workflow with a fresh request id', async () => {
  githubSecretRoutes();
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
  // The job's own copy of the GitHub token is encrypted with the repository key.
  const put = JSON.parse(calls.find((c) => c.method === 'PUT').body);
  assert.equal(put.key_id, 'k1');
  assert.equal(unseal(put.encrypted_value), 'github_pat_x');
  assert.ok(!put.encrypted_value.includes('github_pat_x'));
});

test('renewal explains missing token permissions', async () => {
  on('GET', /\/actions\/secrets\/public-key$/, reply({message: 'Resource not accessible'}, 403));
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
  on('GET', /\/actions\/workflows\/shorts-studio-claude-login\.yml\/runs/, reply({workflow_runs: [
    {display_title: 'Claude sign-in ' + 'b'.repeat(32), status: 'in_progress', html_url: 'u1'},
    {display_title: 'Claude sign-in ' + 'd'.repeat(32), status: 'completed', conclusion: 'failure', html_url: 'u2',
      run_started_at: '2026-11-11T11:38:02Z', updated_at: '2026-11-11T11:43:06Z'},
    {display_title: 'Claude sign-in ' + '9'.repeat(32), status: 'completed', conclusion: 'failure', html_url: 'u3',
      run_started_at: '2026-11-11T11:38:02Z', updated_at: '2026-11-11T11:38:06Z'}]}));
  status = await (await call('/api/claude-login?nonce=' + 'b'.repeat(32), {cookie})).json();
  assert.deepEqual([status.state, status.phase], ['starting', 'running']);
  status = await (await call('/api/claude-login?nonce=' + 'e'.repeat(32), {cookie})).json();
  assert.deepEqual([status.state, status.phase], ['starting', 'queued']);
  status = await (await call('/api/claude-login?nonce=' + 'd'.repeat(32), {cookie})).json();
  assert.equal(status.state, 'failed');
  assert.equal(status.details, 'u2');
  assert.match(status.message, /before Claude answered/);
  status = await (await call('/api/claude-login?nonce=' + '9'.repeat(32), {cookie})).json();
  assert.equal(status.blocked, true);
  assert.match(status.message, /Billing and licensing/);
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

test('account reports the Claude connection and its yearly expiry', async () => {
  on('GET', /\/actions\/secrets\/CLAUDE_CODE_OAUTH_TOKEN$/, reply({name: 'CLAUDE_CODE_OAUTH_TOKEN', updated_at: '2026-11-11T10:00:00Z'}));
  const data = await (await call('/api/account', {cookie: await session()})).json();
  assert.equal(data.repo, 'me/studio');
  assert.deepEqual(data.claude, {connected: true, renewedAt: '2026-11-11T10:00:00Z', expiresAt: '2027-11-11T10:00:00.000Z'});
  assert.equal(data.youtube.connected, false);
  routes = [];
  on('GET', /\/actions\/secrets\/CLAUDE_CODE_OAUTH_TOKEN$/, reply({message: 'Not Found'}, 404));
  assert.equal((await (await call('/api/account', {cookie: await session()})).json()).claude.connected, false);
});

test('a running film can be cancelled', async () => {
  on('POST', /\/actions\/runs\/55\/cancel$/, reply(null, 202));
  const response = await call('/api/runs/55/cancel', {method: 'POST', cookie: await session()});
  assert.equal(response.status, 200);
  assert.equal((await call('/api/runs/x/cancel', {method: 'POST', cookie: await session()})).status, 404);
});

test('settings keep the writer choice', () => {
  assert.equal(autopilotSettings({days: [1]}).writer, 'auto');
  assert.equal(autopilotSettings({days: [1], writer: 'claude'}).writer, 'claude');
  assert.throws(() => autopilotSettings({days: [1], writer: 'gpt'}), /writer/);
});

test('cancelling a sign-in stops its GitHub job', async () => {
  const nonce = 'f'.repeat(32);
  on('GET', /\/actions\/workflows\/shorts-studio-claude-login\.yml\/runs/, reply({workflow_runs: [
    {id: 77, display_title: 'Claude sign-in ' + nonce, status: 'in_progress'}]}));
  on('POST', /\/actions\/runs\/77\/cancel$/, reply(null, 202));
  on('DELETE', /\/actions\/variables\/STUDIO_LOGIN_CODE$/, reply(null, 204));
  const response = await call('/api/claude-login?nonce=' + nonce, {method: 'DELETE', cookie: await session()});
  assert.equal(response.status, 200);
  assert.ok(calls.some((c) => c.method === 'POST' && c.url.endsWith('/actions/runs/77/cancel')));
});

const GOOGLE = {web: {client_id: '123-abc.apps.googleusercontent.com', client_secret: 'GOCSPX-test', project_id: 'p'}};

async function connectYouTube() {
  const variables = {};
  on('PATCH', /\/actions\/variables\/STUDIO_YOUTUBE_CLIENT$/, () => new Response(null, {status: 404}));
  on('POST', /\/actions\/variables$/, (r) => { const b = JSON.parse(r.body); variables[b.name] = b.value; return new Response(null, {status: 201}); });
  on('GET', /\/actions\/variables\/STUDIO_YOUTUBE_CLIENT$/, () => variables.STUDIO_YOUTUBE_CLIENT
    ? new Response(JSON.stringify({value: variables.STUDIO_YOUTUBE_CLIENT})) : new Response('{}', {status: 404}));
  const response = await call('/api/youtube/connect', {method: 'POST', cookie: await session(), body: {client: GOOGLE}});
  return {response, variables};
}

test('connecting YouTube stores the client encrypted and starts Google consent', async () => {
  const {response, variables} = await connectYouTube();
  assert.equal(response.status, 200);
  const {url} = await response.json();
  const consent = new URL(url);
  assert.equal(consent.origin, 'https://accounts.google.com');
  assert.equal(consent.searchParams.get('client_id'), GOOGLE.web.client_id);
  assert.equal(consent.searchParams.get('redirect_uri'), BASE + '/api/youtube/callback');
  assert.equal(consent.searchParams.get('scope'), 'https://www.googleapis.com/auth/youtube.upload');
  assert.equal(consent.searchParams.get('access_type'), 'offline');
  assert.match(response.headers.get('Set-Cookie'), /^ytn=[0-9a-f]{32}; Path=\/api\/youtube\/callback;.*SameSite=Lax/);
  assert.ok(!variables.STUDIO_YOUTUBE_CLIENT.includes('GOCSPX-test'), 'client secret must be encrypted');
  const bad = await call('/api/youtube/connect', {method: 'POST', cookie: await session(), body: {client: {web: {client_id: 'x'}}}});
  assert.equal(bad.status, 400);
});

test('the Google callback saves a sealed refresh token for the upload job', async () => {
  const {response} = await connectYouTube();
  const {url} = await response.json();
  const state = new URL(url).searchParams.get('state');
  const nonce = /ytn=([0-9a-f]{32})/.exec(response.headers.get('Set-Cookie'))[1];
  githubSecretRoutes();
  routes = routes.filter(([m, p]) => !(m === 'PUT' && String(p).includes('STUDIO_GITHUB_TOKEN')));
  let exchange;
  on('POST', /^https:\/\/oauth2\.googleapis\.com\/token$/, (r) => { exchange = new URLSearchParams(r.body); return new Response(JSON.stringify({refresh_token: '1//refresh', access_token: 'a'})); });
  on('PUT', /\/actions\/secrets\/YOUTUBE_OAUTH_JSON$/, reply(null, 201));
  // Without the browser cookie, the callback is refused.
  const stolen = await call(`/api/youtube/callback?code=c&state=${encodeURIComponent(state)}`);
  assert.equal(stolen.headers.get('Location'), '/?youtube=expired#account');
  const done = await call(`/api/youtube/callback?code=4/abc&state=${encodeURIComponent(state)}`, {headers: {Cookie: 'ytn=' + nonce}});
  assert.equal(done.status, 302);
  assert.equal(done.headers.get('Location'), '/?youtube=connected#account');
  assert.equal(exchange.get('code'), '4/abc');
  assert.equal(exchange.get('client_secret'), 'GOCSPX-test');
  assert.equal(exchange.get('redirect_uri'), BASE + '/api/youtube/callback');
  const put = JSON.parse(calls.find((c) => c.method === 'PUT' && c.url.endsWith('/YOUTUBE_OAUTH_JSON')).body);
  const saved = JSON.parse(unseal(put.encrypted_value));
  assert.deepEqual([saved.refresh_token, saved.client_id, saved.client_secret, saved.token_uri],
    ['1//refresh', GOOGLE.web.client_id, 'GOCSPX-test', 'https://oauth2.googleapis.com/token']);
  assert.equal((await call('/api/youtube/callback?error=access_denied')).headers.get('Location'), '/?youtube=cancelled#account');
});

test('account reports the YouTube redirect address to register', async () => {
  on('GET', /\/actions\/secrets\/CLAUDE_CODE_OAUTH_TOKEN$/, reply({}, 404));
  on('GET', /\/actions\/secrets\/YOUTUBE_OAUTH_JSON$/, reply({name: 'YOUTUBE_OAUTH_JSON'}));
  const data = await (await call('/api/account', {cookie: await session()})).json();
  assert.deepEqual(data.youtube, {connected: true, clientSaved: false, redirectUri: BASE + '/api/youtube/callback'});
});

test('autopilot keeps sorted post times', () => {
  assert.deepEqual(autopilotSettings({days: [1], times: ['17:30', '09:00']}).times, ['09:00', '17:30']);
  assert.deepEqual(autopilotSettings({days: [1]}).times, ['18:00']);
  assert.throws(() => autopilotSettings({days: [1], times: ['9am']}), /Post times/);
  assert.throws(() => autopilotSettings({days: [1], times: ['09:00', '09:00']}), /Post times/);
});

test('publishing selected films starts the publish workflow', async () => {
  on('POST', /\/actions\/workflows\/shorts-studio-publish\.yml\/dispatches$/, reply(null, 204));
  const later = new Date(Date.now() + 3600000).toISOString();
  const response = await call('/api/publish', {method: 'POST', cookie: await session(),
    body: {privacy: 'public', films: [{run: '12', publishAt: later}, {run: '13'}]}});
  assert.equal(response.status, 202);
  const sent = JSON.parse(calls[0].body);
  assert.equal(sent.inputs.privacy, 'public');
  const films = JSON.parse(sent.inputs.films);
  assert.deepEqual(films.map((f) => f.run), ['12', '13']);
  assert.match(films[0].publishAt, /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$/);
  assert.equal(films[1].publishAt, '');
});

test('publish requests are validated', () => {
  const soon = new Date(Date.now() + 60000).toISOString();
  const later = new Date(Date.now() + 3600000).toISOString();
  assert.throws(() => publishRequest({films: []}), /between 1 and 20/);
  assert.throws(() => publishRequest({films: [{run: '1'}, {run: '1'}]}), /Invalid/);
  assert.throws(() => publishRequest({films: [{run: '1', publishAt: soon}]}), /10 minutes/);
  assert.throws(() => publishRequest({privacy: 'private', films: [{run: '1', publishAt: later}]}), /Only public/);
  assert.equal(publishRequest({films: [{run: '1'}]}).privacy, 'public');
});

test('captions are on unless turned off', () => {
  assert.equal(autopilotSettings({days: [1]}).captions, true);
  assert.equal(autopilotSettings({days: [1], captions: false}).captions, false);
});

test('hashtags are cleaned, deduplicated and limited', () => {
  assert.deepEqual(hashtags('#Fofowa science, #Science #shorts معلومات'), ['#Fofowa', '#science', '#معلومات']);
  assert.deepEqual(hashtags(['#a-b!', '']), ['#ab']);
  assert.throws(() => hashtags('a b c d e f g h i'), /at most 8/);
  assert.equal(hashtags('a b c d e f g h i', 9).length, 9);
  assert.deepEqual(autopilotSettings({days: [1], hashtags: '#Fofowa'}).hashtags, ['#Fofowa']);
  assert.deepEqual(publishRequest({films: [{run: '1'}], hashtags: 'space #facts'}).hashtags, ['#space', '#facts']);
});

test('each film carries hashtags the page can edit', () => {
  assert.deepEqual(suggestHashtags('Why does the Moon cause tides?', 'en'), ['#moon', '#cause', '#tides', '#facts', '#didyouknow', '#learn']);
  assert.ok(suggestHashtags('لماذا نرى القمر في النهار؟', 'ar').includes('#معلومات'));
  const request = publishRequest({films: [{run: '1', hashtags: ['moon', '#tides']}, {run: '2'}]});
  assert.deepEqual(request.films[0].hashtags, ['#moon', '#tides']);
  assert.equal(request.films[1].hashtags, undefined);
  assert.throws(() => publishRequest({films: [{run: '1', hashtags: 'a b c d e f g h i j'.split(' ')}]}), /at most 9/);
});

test('topic ideas can be read, edited and refreshed', async () => {
  const stored = [{id: 'a1', topic: 'Why is Mars red?', brief: 'Explain.', score: 88, source: 'claude'}];
  on('GET', /\/contents\/shorts-studio\/autopilot\/ideas\.json/, reply({content: Buffer.from(JSON.stringify(stored)).toString('base64'), sha: 's1'}));
  on('GET', /\/actions\/workflows\/shorts-studio-ideas\.yml\/runs/, reply({workflow_runs: [{status: 'in_progress'}]}));
  on('PUT', /\/contents\/shorts-studio\/autopilot\/ideas\.json$/, reply({content: {sha: 's2'}}));
  on('POST', /\/actions\/workflows\/shorts-studio-ideas\.yml\/dispatches$/, reply(null, 204));
  const cookie = await session();
  const read = await (await call('/api/ideas', {cookie})).json();
  assert.deepEqual([read.ideas[0].topic, read.sha, read.refreshing], ['Why is Mars red?', 's1', true]);
  const saved = await (await call('/api/ideas', {method: 'PUT', cookie, body: {sha: 's1', ideas: [{topic: 'My own idea'}, stored[0]]}})).json();
  assert.equal(saved.sha, 's2');
  assert.equal(saved.ideas[0].source, 'you');
  assert.equal(saved.ideas[0].brief, 'My own idea');
  const put = JSON.parse(calls.find((c) => c.method === 'PUT').body);
  assert.equal(put.sha, 's1');
  assert.equal((await call('/api/ideas/refresh', {method: 'POST', cookie})).status, 202);
});

test('idea lists are validated', () => {
  assert.throws(() => cleanIdeas([{topic: ''}]), /needs a topic/);
  assert.throws(() => cleanIdeas(new Array(41).fill({topic: 'x'})), /at most 40/);
  assert.equal(cleanIdeas([{topic: 'T', lane: 'science'}])[0].lane, 'science', 'each idea keeps its autopilot topic');
  assert.equal(cleanIdeas([{topic: 'T', lane: 'Bad Lane!'}])[0].lane, undefined);
  const [idea] = cleanIdeas([{topic: 'T', score: 500, evidence: [{url: 'http://no'}, {url: 'https://ok', title: 'ok'}]}]);
  assert.equal(idea.score, 100);
  assert.deepEqual(idea.evidence, [{title: 'ok', url: 'https://ok'}]);
});

function variableStore() {
  const vars = {};
  on('GET', /\/actions\/variables\/[A-Z_]+$/, (r) => {
    const name = r.url.split('/').pop();
    return vars[name] ? new Response(JSON.stringify({value: vars[name]})) : new Response('{}', {status: 404});
  });
  on('PATCH', /\/actions\/variables\/[A-Z_]+$/, (r) => {
    const name = r.url.split('/').pop();
    if (!vars[name]) return new Response(null, {status: 404});
    vars[name] = JSON.parse(r.body).value; return new Response(null, {status: 204});
  });
  on('POST', /\/actions\/variables$/, (r) => { const b = JSON.parse(r.body); vars[b.name] = b.value; return new Response(null, {status: 201}); });
  return vars;
}

test('jobs report events with the notify key and phones are woken', async () => {
  const vars = variableStore();
  githubSecretRoutes();
  on('PUT', /\/actions\/secrets\/STUDIO_NOTIFY_KEY$/, reply(null, 201));
  const pushes = [];
  on('POST', /^https:\/\/push\.example\//, (r) => { pushes.push(r); return new Response(null, {status: r.url.endsWith('/gone') ? 410 : 201}); });
  const cookie = await session();
  const sub = await call('/api/push/subscribe', {method: 'POST', cookie, body: {subscription: {endpoint: 'https://push.example/phone'}}});
  assert.equal(sub.status, 200);
  await call('/api/push/subscribe', {method: 'POST', cookie, body: {subscription: {endpoint: 'https://push.example/gone'}}});
  assert.equal(JSON.parse(vars.STUDIO_PAGE_URL).url, BASE);
  const put = JSON.parse(calls.find((c) => c.method === 'PUT' && c.url.endsWith('/STUDIO_NOTIFY_KEY')).body);
  const key = unseal(put.encrypted_value);
  assert.ok(key.length > 20);
  // Wrong key is refused.
  assert.equal((await call('/api/notify', {method: 'POST', body: {kind: 'ready', title: 'x'}, headers: {Authorization: 'Bearer nope'}})).status, 401);
  const ok = await call('/api/notify', {method: 'POST', body: {kind: 'ready', title: 'Film ready: Why is Mars red?', text: 'Made by Claude', url: '/#library'},
    headers: {Authorization: 'Bearer ' + key}});
  assert.equal(ok.status, 200);
  assert.equal(pushes.length, 2);
  assert.match(pushes[0].headers.Authorization, /^vapid t=[\w-]+\.[\w-]+\.[\w-]+, k=[\w-]+$/);
  await call('/api/notify', {method: 'POST', body: {kind: 'heartbeat', title: 'check'}, headers: {Authorization: 'Bearer ' + key}});
  assert.equal(pushes.length, 2, 'heartbeat does not wake phones');
  await call('/api/notify', {method: 'POST', body: {kind: 'failed', title: 'A film did not finish'}, headers: {Authorization: 'Bearer ' + key}});
  assert.equal(pushes.length, 3, 'the phone that answered 410 was removed');
  const events = await (await call('/api/events', {cookie})).json();
  assert.equal(events.events.length, 2);
  assert.equal(events.events[1].title, 'Film ready: Why is Mars red?');
  assert.ok(events.lastCheck > 0);
  assert.ok(!vars.STUDIO_PUSH.includes('push.example'), 'subscriptions are stored encrypted');
});

test('VAPID tokens are valid ES256 signatures', async () => {
  const pair = await crypto.subtle.generateKey({name: 'ECDSA', namedCurve: 'P-256'}, true, ['sign', 'verify']);
  const jwk = await crypto.subtle.exportKey('jwk', pair.privateKey);
  const raw = new Uint8Array(await crypto.subtle.exportKey('raw', pair.publicKey));
  const state = {privateJwk: jwk, publicKey: Buffer.from(raw).toString('base64url')};
  const header = await vapidHeader(state, 'https://fcm.googleapis.com/fcm/send/abc', 'https://studio.example', 0);
  const token = /t=([^,]+)/.exec(header)[1];
  const [h, p, sig] = token.split('.');
  assert.deepEqual(JSON.parse(Buffer.from(p, 'base64url')), {aud: 'https://fcm.googleapis.com', exp: 43200, sub: 'https://studio.example'});
  const valid = await crypto.subtle.verify({name: 'ECDSA', hash: 'SHA-256'}, pair.publicKey, Buffer.from(sig, 'base64url'), new TextEncoder().encode(h + '.' + p));
  assert.ok(valid);
});

test('the autopilot clock finds the due post time in the owner timezone', () => {
  const s = {timezone: 'Asia/Beirut', days: [0, 1, 2, 3, 4, 5, 6], times: ['22:56', '09:05']};
  const at = (iso) => new Date(iso);
  assert.equal(dueSlot(s, new Set(), at('2026-11-11T20:58:00Z')).label, '2026-11-11T22:56+02:00'); // 2 min late
  assert.equal(dueSlot(s, new Set(), at('2026-11-11T20:58:00Z')).late, true);
  assert.equal(dueSlot(s, new Set(), at('2026-11-11T20:10:00Z')).late, false);                    // 46 min early
  assert.equal(dueSlot(s, new Set(), at('2026-11-11T19:50:00Z')), null);                          // 66 min early
  assert.equal(dueSlot(s, new Set(['2026-11-11T22:56+02:00']), at('2026-11-11T20:58:00Z')), null);
  assert.equal(dueSlot(s, new Set(), at('2026-11-11T20:58:00Z'), new Set(['2026-11-11T22:56+02:00'])), null);
  assert.equal(dueSlot({...s, days: [0]}, new Set(), at('2026-11-11T20:58:00Z')), null);           // Wednesday not selected
  assert.equal(dueSlot(s, new Set(), at('2026-11-12T06:30:00Z')).label, '2026-11-12T09:05+02:00');
  assert.equal(dueSlot({...s, timezone: 'UTC', times: ['00:10']}, new Set(), at('2026-11-11T23:50:00Z')).label, '2026-11-12T00:10+00:00');
});

test('a clock tick starts the due film once and records the check', async () => {
  const settings = {enabled: true, domain: 'Science', timezone: 'Asia/Beirut', days: [0, 1, 2, 3, 4, 5, 6], times: ['22:56']};
  const file = (value) => () => new Response(JSON.stringify({content: Buffer.from(JSON.stringify(value)).toString('base64'), sha: 'x'}));
  on('GET', /\/contents\/shorts-studio\/autopilot\/settings\.json/, file(settings));
  on('GET', /\/contents\/shorts-studio\/autopilot\/ideas\.json/, file([{topic: 'a'}, {topic: 'b'}, {topic: 'c'}]));
  on('GET', /\/contents\/shorts-studio\/autopilot\/history\.json/, () => new Response('{}', {status: 404}));
  let runs = [];
  on('GET', /\/actions\/workflows\/shorts-studio-claude\.yml\/runs/, () => new Response(JSON.stringify({workflow_runs: runs})));
  on('POST', /\/actions\/workflows\/shorts-studio-claude\.yml\/dispatches$/, reply(null, 204));
  const vars = variableStore();
  const result = await autopilotTick(env, new Date('2026-11-11T20:58:00Z'));
  assert.deepEqual(result, {state: 'started', label: '2026-11-11T22:56+02:00', notes: []});
  const dispatch = calls.find((c) => c.url.endsWith('/shorts-studio-claude.yml/dispatches'));
  assert.deepEqual(JSON.parse(dispatch.body).inputs, {brief: '', slot: '2026-11-11T22:56+02:00'});
  const events = JSON.parse(vars.STUDIO_EVENTS);
  assert.equal(events.events[0].title, 'Autopilot started the 22:56 film');
  assert.ok(events.lastCheck > 0);
  runs = [{display_title: 'Autopilot film 2026-11-11T22:56+02:00', status: 'in_progress'}];
  assert.equal((await autopilotTick(env, new Date('2026-11-11T21:03:00Z'))).state, 'waiting');
});

test('a failed post time is retried once, then left alone', async () => {
  const settings = {enabled: true, domain: 'Science', timezone: 'Asia/Beirut', days: [0, 1, 2, 3, 4, 5, 6], times: ['22:56']};
  const file = (value) => () => new Response(JSON.stringify({content: Buffer.from(JSON.stringify(value)).toString('base64'), sha: 'x'}));
  on('GET', /\/contents\/shorts-studio\/autopilot\/settings\.json/, file(settings));
  on('GET', /\/contents\/shorts-studio\/autopilot\/ideas\.json/, file([{topic: 'a'}, {topic: 'b'}, {topic: 'c'}]));
  on('GET', /\/contents\/shorts-studio\/autopilot\/history\.json/, () => new Response('{}', {status: 404}));
  const failed = {display_title: 'Autopilot film 2026-11-11T22:56+02:00', status: 'completed', conclusion: 'failure'};
  let runs = [failed];
  on('GET', /\/actions\/workflows\/shorts-studio-claude\.yml\/runs/, () => new Response(JSON.stringify({workflow_runs: runs})));
  on('POST', /\/actions\/workflows\/shorts-studio-claude\.yml\/dispatches$/, reply(null, 204));
  variableStore();
  assert.equal((await autopilotTick(env, new Date('2026-11-11T21:10:00Z'))).state, 'started');
  runs = [failed, failed];
  assert.equal((await autopilotTick(env, new Date('2026-11-11T21:30:00Z'))).state, 'waiting');
});

test('a failing extra never stops the film from starting', async () => {
  const settings = {enabled: true, domain: 'Science', timezone: 'Asia/Beirut', days: [0, 1, 2, 3, 4, 5, 6], times: ['22:56']};
  const file = (value) => () => new Response(JSON.stringify({content: Buffer.from(JSON.stringify(value)).toString('base64'), sha: 'x'}));
  on('GET', /\/contents\/shorts-studio\/autopilot\/settings\.json/, file(settings));
  on('GET', /\/contents\/shorts-studio\/autopilot\/ideas\.json/, file([]));
  on('GET', /\/contents\/shorts-studio\/autopilot\/history\.json/, () => new Response('{}', {status: 404}));
  on('GET', /\/actions\/variables\//, reply({message: 'Resource not accessible'}, 403));
  on('GET', /\/actions\/workflows\/shorts-studio-ideas\.yml\/runs/, reply({message: 'boom'}, 500));
  on('GET', /\/actions\/workflows\/shorts-studio-claude\.yml\/runs/, reply({workflow_runs: []}));
  on('POST', /\/actions\/workflows\/shorts-studio-claude\.yml\/dispatches$/, reply(null, 204));
  const result = await autopilotTick(env, new Date('2026-11-11T21:10:00Z'));
  assert.equal(result.state, 'started');
  assert.equal(result.label, '2026-11-11T22:56+02:00');
  assert.ok(result.notes.some((n) => n.startsWith('last check')));
  assert.ok(calls.some((c) => c.url.endsWith('/shorts-studio-claude.yml/dispatches')));
});

test('check now explains what is next', async () => {
  // A post time 12 hours away is never due, whenever the test runs.
  const later = new Date(Date.now() + 12 * 3600000);
  const at = `${String(later.getUTCHours()).padStart(2, '0')}:${String(later.getUTCMinutes()).padStart(2, '0')}`;
  const settings = {enabled: true, domain: 'Science', timezone: 'UTC', days: [0, 1, 2, 3, 4, 5, 6], times: [at]};
  const file = (value) => () => new Response(JSON.stringify({content: Buffer.from(JSON.stringify(value)).toString('base64'), sha: 'x'}));
  on('GET', /\/contents\/shorts-studio\/autopilot\/settings\.json/, file(settings));
  on('GET', /\/contents\/shorts-studio\/autopilot\/(ideas|history)\.json/, file([{topic: 'a'}, {topic: 'b'}, {topic: 'c'}]));
  on('GET', /\/actions\/workflows\/shorts-studio-claude\.yml\/runs/, reply({workflow_runs: []}));
  variableStore();
  const result = await (await call('/api/autopilot/check', {method: 'POST', cookie: await session(), body: {}})).json();
  assert.equal(result.state, 'waiting');
  assert.ok(result.next.endsWith(`T${at}+00:00`), result.next);
});

// ---- Instagram ----
const IG_ID = '17841400000000001';
function libraryRoutes(films) {
  on('GET', /\/releases\?per_page=100$/, reply([draft]));
  on('GET', /\/releases\/assets\/1$/, reply(films));
  on('GET', /\/releases\/assets\/2$/, () => new Response('MP4DATA', {status: 200, headers: {'Content-Length': '7'}}));
}
function instagramRoutes({accountType = 'MEDIA_CREATOR', status = 'FINISHED'} = {}) {
  const seen = [];
  on('POST', /^https:\/\/api\.instagram\.com\/oauth\/access_token$/, (r) => { seen.push(['code', String(r.body)]); return new Response(JSON.stringify({data: [{access_token: 'SHORT', user_id: 1}]})); });
  on('GET', /^https:\/\/graph\.instagram\.com\/access_token\?/, reply({access_token: 'LONGTOKEN', expires_in: 5184000}));
  on('GET', /^https:\/\/graph\.instagram\.com\/v24\.0\/me\?/, reply({user_id: IG_ID, username: 'science.facts', account_type: accountType}));
  on('POST', new RegExp(`/v24\\.0/${IG_ID}/media$`), (r) => { seen.push(['media', String(r.body)]); return new Response(JSON.stringify({id: 'C1'})); });
  on('GET', /\/v24\.0\/C1\?/, reply({status_code: status}));
  on('POST', new RegExp(`/v24\\.0/${IG_ID}/media_publish$`), (r) => { seen.push(['publish', String(r.body)]); return new Response(JSON.stringify({id: 'M1'})); });
  on('GET', /\/v24\.0\/M1\?/, reply({permalink: 'https://www.instagram.com/reel/abc/'}));
  return seen;
}
async function connectInstagram(cookie) {
  await call('/api/instagram/app', {method: 'POST', cookie, body: {appId: '123456789', appSecret: '0123456789abcdef0123456789abcdef'}});
  const started = await call('/api/instagram/connect', {method: 'POST', cookie, body: {}});
  const consent = new URL((await started.json()).url);
  const nonce = /ign=([0-9a-f]{32})/.exec(started.headers.get('Set-Cookie'))[1];
  const back = await call(`/api/instagram/callback?code=abc%23_&state=${encodeURIComponent(consent.searchParams.get('state'))}`, {headers: {Cookie: `ign=${nonce}`}});
  return {consent, back};
}

test('Instagram app details are checked', async () => {
  const {instagramApp, instagramIds, instagramCaption} = await import('../src/worker.js');
  assert.throws(() => instagramApp({appId: 'abc', appSecret: '0123456789abcdef0123456789abcdef'}), /app ID/);
  assert.throws(() => instagramApp({appId: '123456789', appSecret: 'short'}), /secret/);
  assert.deepEqual(instagramIds(['17841400000000001', '17841400000000001']), ['17841400000000001']);
  assert.throws(() => instagramIds(['me']), /Instagram/);
  const caption = instagramCaption({title: 'Why is the sky blue?', description: 'Light scatters.', hashtags: ['#Shorts', '#sky']}, ['#science']);
  assert.equal(caption, 'Why is the sky blue?\n\nLight scatters.\n\n#science #sky #reels #reelsinstagram #explore #explorepage');
  assert.match(instagramCaption({title: 'T', language: 'ar', hashtags: ['#علوم']}), /#علوم #ريلز #اكسبلور/);
});

test('Instagram: connect accounts, queue a Reel and publish it as soon as Instagram has processed it', async () => {
  const vars = variableStore();
  libraryRoutes([{run: '42', title: 'Why is the sky blue?', description: 'Light scatters.', hashtags: ['#sky', '#science']}]);
  const seen = instagramRoutes();
  const cookie = await session();
  const {consent, back} = await connectInstagram(cookie);
  assert.equal(consent.host, 'www.instagram.com');
  assert.equal(consent.searchParams.get('client_id'), '123456789');
  assert.match(consent.searchParams.get('scope'), /instagram_business_content_publish/);
  assert.equal(back.headers.get('Location'), '/?instagram=connected#account');
  assert.match(seen[0][1], /code=abc&|code=abc$/, 'the trailing #_ is removed from the code');
  assert.ok(!vars.STUDIO_INSTAGRAM.includes('LONGTOKEN'), 'tokens are stored encrypted');
  const account = await (await call('/api/account', {cookie})).json();
  assert.deepEqual(account.instagram.accounts.map((a) => [a.id, a.username, a.token]), [[IG_ID, 'science.facts', undefined]]);

  const published = await call('/api/publish', {method: 'POST', cookie, body: {films: [{run: '42'}], privacy: 'public', youtube: false, instagram: [IG_ID]}});
  assert.equal(published.status, 202);
  assert.equal((await published.json()).instagram, 1);
  assert.ok(!calls.some((c) => c.url.includes('shorts-studio-publish.yml')), 'YouTube was not chosen');
  const media = new URLSearchParams(seen.find((s) => s[0] === 'media')[1]);
  assert.equal(media.get('media_type'), 'REELS');
  assert.match(media.get('caption'), /^Why is the sky blue\?\n\nLight scatters\.\n\n#sky #science #reels #reelsinstagram #explore #explorepage$/);
  const link = new URL(media.get('video_url'));
  assert.equal(link.origin, BASE);
  assert.equal(await (await call(link.pathname)).text(), 'MP4DATA', 'Meta can download the film through the signed link');
  assert.equal((await call(link.pathname.replace('/42/', '/43/'))).status, 403, 'the link only opens its own film');
  assert.equal(JSON.parse(vars.STUDIO_IG_QUEUE).posts[0].state, 'processing');

  await (await import('../src/worker.js')).instagramTick(env, Date.now());
  const post = JSON.parse(vars.STUDIO_IG_QUEUE).posts[0];
  assert.deepEqual([post.state, post.permalink], ['posted', 'https://www.instagram.com/reel/abc/']);
  assert.equal(new URLSearchParams(seen.find((s) => s[0] === 'publish')[1]).get('creation_id'), 'C1');
  assert.match(JSON.parse(vars.STUDIO_EVENTS).events[0].title, /Posted on Instagram: Why is the sky blue\?/);
  const {films} = await (await call('/api/films', {cookie})).json();
  assert.deepEqual(films[0].instagram.map((p) => [p.username, p.state]), [['science.facts', 'posted']]);
});

test('Instagram: scheduled Reels wait for their time; personal accounts are refused', async () => {
  const vars = variableStore();
  libraryRoutes([{run: '42', title: 'Tides', hashtags: []}]);
  const seen = instagramRoutes();
  const cookie = await session();
  await connectInstagram(cookie);
  const at = new Date(Date.now() + 3 * 3600000).toISOString();
  await call('/api/publish', {method: 'POST', cookie, body: {films: [{run: '42', publishAt: at}], privacy: 'public', youtube: false, instagram: [IG_ID]}});
  const {instagramTick} = await import('../src/worker.js');
  await instagramTick(env, Date.now());
  assert.equal(JSON.parse(vars.STUDIO_IG_QUEUE).posts[0].state, 'waiting');
  assert.ok(!seen.some((s) => s[0] === 'media'), 'nothing is sent to Instagram hours early');
  await instagramTick(env, Date.parse(at) - 5 * 60000);
  assert.equal(JSON.parse(vars.STUDIO_IG_QUEUE).posts[0].state, 'processing', 'processing starts shortly before the post time');
  await instagramTick(env, Date.parse(at) - 4 * 60000);
  assert.equal(JSON.parse(vars.STUDIO_IG_QUEUE).posts[0].state, 'processing', 'and it is not published early');
  await instagramTick(env, Date.parse(at) + 30000);
  assert.equal(JSON.parse(vars.STUDIO_IG_QUEUE).posts[0].state, 'posted');

  routes = []; variableStore(); instagramRoutes({accountType: 'PERSONAL'});
  const {back} = await connectInstagram(cookie);
  assert.equal(back.headers.get('Location'), '/?instagram=notpro#account');
});

test('jobs queue Reels with the notify key only', async () => {
  variableStore();
  libraryRoutes([{run: '42', title: 'Tides', hashtags: []}]);
  instagramRoutes();
  await connectInstagram(await session());
  const denied = await call('/api/instagram/queue', {method: 'POST', body: {run: '42', accounts: [IG_ID]}, headers: {Authorization: 'Bearer nope'}});
  assert.equal(denied.status, 401);
});

test('Instagram: a pasted dashboard token connects the account, for either token kind', async () => {
  const vars = variableStore();
  libraryRoutes([{run: '42', title: 'Tides', hashtags: []}]);
  const cookie = await session();
  // An Instagram-login token answers on graph.instagram.com.
  on('GET', /^https:\/\/graph\.instagram\.com\/v24\.0\/me\?.*access_token=IGAAtoken/, reply({user_id: IG_ID, username: 'science.facts', account_type: 'MEDIA_CREATOR'}));
  // A Unified-login token answers on graph.facebook.com, given the account ID.
  on('GET', /^https:\/\/graph\.instagram\.com\//, reply({error: {message: 'Invalid OAuth access token'}}, 400));
  on('GET', /^https:\/\/graph\.facebook\.com\/v24\.0\/me\?/, reply({error: {message: 'Unsupported get request'}}, 400));
  on('GET', /^https:\/\/graph\.facebook\.com\/v24\.0\/17841400000000002\?/, reply({id: '17841400000000002', username: 'history.daily'}));
  const token = (t, accountId) => call('/api/instagram/token', {method: 'POST', cookie, body: {token: t, accountId}});
  const first = await token('IGAAtoken' + 'x'.repeat(40));
  assert.equal((await first.json()).username, 'science.facts');
  const without = await token('EAAtoken' + 'y'.repeat(40));
  assert.equal(without.status, 400);
  assert.match((await without.json()).error, /account ID/);
  const second = await token('EAAtoken' + 'y'.repeat(40), '17841400000000002');
  assert.equal((await second.json()).username, 'history.daily');
  assert.equal((await token('short')).status, 400);
  const account = await (await call('/api/account', {cookie})).json();
  assert.deepEqual(account.instagram.accounts.map((a) => [a.username, a.expiresAt === null]), [['science.facts', false], ['history.daily', true]]);
  assert.ok(!vars.STUDIO_INSTAGRAM.includes('EAAtoken'), 'tokens are stored encrypted');

  // Reels for the Unified-login account go through graph.facebook.com.
  on('POST', /^https:\/\/graph\.facebook\.com\/v24\.0\/17841400000000002\/media$/, reply({id: 'C9'}));
  await call('/api/publish', {method: 'POST', cookie, body: {films: [{run: '42'}], privacy: 'public', youtube: false, instagram: ['17841400000000002']}});
  assert.equal(JSON.parse(vars.STUDIO_IG_QUEUE).posts[0].container, 'C9');
});

test('Instagram: a personal account token is refused', async () => {
  variableStore();
  on('GET', /^https:\/\/graph\.instagram\.com\/v24\.0\/me\?/, reply({user_id: IG_ID, username: 'me', account_type: 'PERSONAL'}));
  const response = await call('/api/instagram/token', {method: 'POST', cookie: await session(), body: {token: 'IGAA' + 'z'.repeat(40)}});
  assert.equal(response.status, 400);
  assert.match((await response.json()).error, /Professional/);
});

test('Instagram: a Unified-login token finds its account without the ID when Meta allows it', async () => {
  variableStore();
  on('GET', /^https:\/\/graph\.instagram\.com\//, reply({error: {message: 'Invalid OAuth access token'}}, 400));
  on('GET', /^https:\/\/graph\.facebook\.com\/v24\.0\/me\?/, reply({error: {message: 'Unsupported get request'}}, 400));
  on('GET', /^https:\/\/graph\.facebook\.com\/v24\.0\/me\/accounts\?/, reply({data: [{instagram_business_account: {id: '17841400000000003', username: 'space.now'}}]}));
  const response = await call('/api/instagram/token', {method: 'POST', cookie: await session(), body: {token: 'EAA' + 'q'.repeat(40)}});
  assert.equal((await response.json()).username, 'space.now');
});


test('autopilot topics: each has its own post times, and the due one is named', async () => {
  const {topicsOf, autopilotTopics} = await import('../src/worker.js');
  const s = {enabled: true, timezone: 'Asia/Beirut', days: [0, 1, 2, 3, 4, 5, 6], times: ['18:00'], domain: 'Science',
    topics: [{id: 'science', name: 'Science', domain: 'Physics', times: ['09:00']},
             {id: 'history', name: 'History', domain: 'Discoveries', times: ['18:30'], voice: 'ar-LB-RamiNeural'}]};
  assert.deepEqual(topicsOf(s).map(([id, t]) => [id, t.times[0], t.timezone]), [['science', '09:00', 'Asia/Beirut'], ['history', '18:30', 'Asia/Beirut']]);
  const at = (iso) => new Date(iso);
  const morning = dueSlot(s, new Set(), at('2026-11-14T06:30:00Z'));
  assert.deepEqual([morning.label, morning.name], ['2026-11-14T09:00+02:00#science', 'Science']);
  assert.equal(dueSlot(s, new Set(), at('2026-11-14T16:00:00Z')).label, '2026-11-14T18:30+02:00#history');
  assert.equal(dueSlot(s, new Set(['2026-11-14T09:00+02:00#science']), at('2026-11-14T06:30:00Z')), null);
  assert.throws(() => autopilotTopics([{id: 'a'}, {id: 'a'}]), /Invalid topic/);
  assert.throws(() => autopilotTopics(new Array(9).fill(0).map((_, i) => ({id: 't' + i}))), /at most 8/);
  const saved = autopilotSettings({days: [1], topics: [{id: 'science', name: 'Science', domain: 'Physics', days: [0, 2], times: ['09:00'], instagram: ['17841400000000001']}]});
  assert.deepEqual([saved.topics[0].days, saved.topics[0].instagram], [[0, 2], ['17841400000000001']]);
  assert.throws(() => autopilotSettings({days: [1], enabled: true, topics: [{id: 'a', domain: ''}]}), /subject/);
});

test('autopilot topics: the clock starts the due topic and names it', async () => {
  const settings = {enabled: true, timezone: 'Asia/Beirut', days: [0, 1, 2, 3, 4, 5, 6], times: ['18:00'], domain: 'Science',
    topics: [{id: 'history', name: 'History', domain: 'Discoveries', times: ['22:56'], days: [0, 1, 2, 3, 4, 5, 6]}]};
  const file = (value) => () => new Response(JSON.stringify({content: Buffer.from(JSON.stringify(value)).toString('base64'), sha: 'x'}));
  on('GET', /\/contents\/shorts-studio\/autopilot\/settings\.json/, file(settings));
  on('GET', /\/contents\/shorts-studio\/autopilot\/ideas\.json/, file([{topic: 'a', lane: 'history'}, {topic: 'b', lane: 'history'}, {topic: 'c', lane: 'history'}]));
  on('GET', /\/contents\/shorts-studio\/autopilot\/history\.json/, () => new Response('{}', {status: 404}));
  on('GET', /\/actions\/workflows\/shorts-studio-claude\.yml\/runs/, reply({workflow_runs: []}));
  on('POST', /\/actions\/workflows\/shorts-studio-claude\.yml\/dispatches$/, reply(null, 204));
  const vars = variableStore();
  const result = await autopilotTick(env, new Date('2026-11-11T20:58:00Z'));
  assert.equal(result.label, '2026-11-11T22:56+02:00#history');
  const dispatch = calls.find((c) => c.url.endsWith('/shorts-studio-claude.yml/dispatches'));
  assert.equal(JSON.parse(dispatch.body).inputs.slot, '2026-11-11T22:56+02:00#history');
  assert.equal(JSON.parse(vars.STUDIO_EVENTS).events[0].title, 'Autopilot started the 22:56 History film');
  assert.ok(!calls.some((c) => c.url.includes('shorts-studio-ideas.yml/dispatches')), 'the topic has enough ideas');
});

test('deleted Shorts and Reels stop showing as published and can be posted again', async () => {
  const vars = variableStore();
  const job = 'a'.repeat(32);
  libraryRoutes([
    {run: '42', title: 'Tides', jobId: job, youtube: 'https://www.youtube.com/shorts/DELETED01', createdAt: 3},
    {run: '43', title: 'Moon', youtube: 'https://www.youtube.com/shorts/PRIVATE01', createdAt: 2},
    {run: '44', title: 'Stars', youtube: 'https://www.youtube.com/shorts/LIVE00001', createdAt: 1}]);
  on('GET', /^https:\/\/www\.youtube\.com\/oembed\?/, (r) => new Response('{}', {status: r.url.includes('DELETED01') ? 404 : r.url.includes('PRIVATE01') ? 401 : 200}));
  on('GET', new RegExp(`/contents/shorts-studio/ledger/${job}\\.json`), reply({sha: 'L1'}));
  on('DELETE', new RegExp(`/contents/shorts-studio/ledger/${job}\\.json`), reply({}));
  const cookie = await session();
  instagramRoutes();
  await connectInstagram(cookie);
  await call('/api/publish', {method: 'POST', cookie, body: {films: [{run: '44'}], privacy: 'public', youtube: false, instagram: [IG_ID]}});
  const {instagramTick} = await import('../src/worker.js');
  await instagramTick(env, Date.now());
  assert.equal(JSON.parse(vars.STUDIO_IG_QUEUE).posts[0].state, 'posted');
  // The Reel was deleted on Instagram; an expired token must not count as deleted.
  routes.unshift(['GET', /\/v24\.0\/M1\?fields=id/, reply({error: {code: 100, error_subcode: 33, message: 'Object does not exist'}}, 400)]);

  const result = await (await call('/api/films/check', {method: 'POST', cookie, body: {}})).json();
  assert.equal(result.removed, 2);
  assert.ok(calls.some((c) => c.method === 'DELETE' && c.url.includes(`/ledger/${job}.json`)), 'the upload record is cleared');
  const {films} = await (await call('/api/films', {cookie})).json();
  const byRun = Object.fromEntries(films.map((f) => [f.run, f]));
  assert.equal(byRun['42'].youtube, '', 'the deleted Short is no longer published');
  assert.equal(byRun['43'].youtube, 'https://www.youtube.com/shorts/PRIVATE01', 'a private Short still counts');
  assert.deepEqual(byRun['44'].instagram, [], 'the deleted Reel is gone');
  const again = await call('/api/publish', {method: 'POST', cookie, body: {films: [{run: '44'}], privacy: 'public', youtube: false, instagram: [IG_ID]}});
  assert.equal((await again.json()).instagram, 1, 'it can be posted again');
  assert.match(JSON.parse(vars.STUDIO_EVENTS).events.map((e) => e.title).join('|'), /Deleted on YouTube: Tides/);
});

test('an Instagram token problem is not taken for a deleted Reel', async () => {
  const vars = variableStore();
  libraryRoutes([{run: '44', title: 'Stars'}]);
  instagramRoutes();
  const cookie = await session();
  await connectInstagram(cookie);
  await call('/api/publish', {method: 'POST', cookie, body: {films: [{run: '44'}], privacy: 'public', youtube: false, instagram: [IG_ID]}});
  const {instagramTick, checkPublished} = await import('../src/worker.js');
  await instagramTick(env, Date.now());
  routes.unshift(['GET', /\/v24\.0\/M1\?fields=id/, reply({error: {code: 190, message: 'Token expired'}}, 400)]);
  await checkPublished(env);
  assert.equal(JSON.parse(vars.STUDIO_IG_QUEUE).posts[0].state, 'posted');
});


test('a Reel deleted on Instagram is found even while Instagram still answers for it', async () => {
  const vars = variableStore();
  libraryRoutes([{run: '44', title: 'Stars'}]);
  instagramRoutes();
  const cookie = await session();
  await connectInstagram(cookie);
  await call('/api/publish', {method: 'POST', cookie, body: {films: [{run: '44'}], privacy: 'public', youtube: false, instagram: [IG_ID]}});
  const {instagramTick} = await import('../src/worker.js');
  await instagramTick(env, Date.now());
  // "Recently deleted": the Reel itself still answers, but it is gone from the account's posts.
  routes.unshift(['GET', /\/v24\.0\/M1\?fields=id/, reply({id: 'M1'})]);
  routes.unshift(['GET', new RegExp(`/v24\\.0/${IG_ID}/media\\?`), reply({data: [{id: 'M7', timestamp: new Date(Date.now() - 86400000).toISOString()}]})]);
  const result = await (await call('/api/films/check', {method: 'POST', cookie, body: {}})).json();
  assert.equal(result.removed, 1);
  assert.equal(JSON.parse(vars.STUDIO_IG_QUEUE).posts[0].state, 'removed');
});

test('a Reel still on the profile stays published', async () => {
  const vars = variableStore();
  libraryRoutes([{run: '44', title: 'Stars'}]);
  instagramRoutes();
  const cookie = await session();
  await connectInstagram(cookie);
  await call('/api/publish', {method: 'POST', cookie, body: {films: [{run: '44'}], privacy: 'public', youtube: false, instagram: [IG_ID]}});
  const {instagramTick, checkPublished} = await import('../src/worker.js');
  await instagramTick(env, Date.now());
  routes.unshift(['GET', new RegExp(`/v24\\.0/${IG_ID}/media\\?`), reply({data: [{id: 'M1', timestamp: new Date().toISOString()}]})]);
  await checkPublished(env);
  assert.equal(JSON.parse(vars.STUDIO_IG_QUEUE).posts[0].state, 'posted');
});


test('Activity can be cleared; the last check is kept', async () => {
  const vars = variableStore();
  vars.STUDIO_EVENTS = JSON.stringify({events: [{id: 'a', t: 1, title: 'x'}], lastCheck: 123});
  const response = await call('/api/events/clear', {method: 'POST', cookie: await session(), body: {}});
  assert.equal(response.status, 200);
  assert.deepEqual(JSON.parse(vars.STUDIO_EVENTS), {events: [], lastCheck: 123});
});
