import {seal} from './sealed.js';

// Shorts Studio web: a password-protected phone page that starts films on GitHub Actions,
// follows their progress and plays finished films from the private draft-release library.
// Secrets (Cloudflare): STUDIO_PASSWORD, STUDIO_GITHUB_TOKEN. Vars: GITHUB_REPO, GITHUB_BRANCH.

export const VOICES = ['auto', 'en-US-AndrewMultilingualNeural', 'en-US-AvaMultilingualNeural', 'en-US-GuyNeural',
  'en-US-JennyNeural', 'en-GB-RyanNeural', 'en-GB-SoniaNeural', 'ar-LB-RamiNeural', 'ar-LB-LaylaNeural',
  'ar-SA-HamedNeural', 'ar-SA-ZariyahNeural'];
const WORKFLOW = 'shorts-studio-claude.yml';
const LIBRARY = 'Shorts Studio films';
const SETTINGS = 'shorts-studio/autopilot/settings.json';
const SESSION_DAYS = 30;
const encoder = new TextEncoder();

class HttpError extends Error {
  constructor(status, message) { super(message); this.status = status; }
}

const json = (value, status = 200, headers = {}) => new Response(JSON.stringify(value), {status,
  headers: {'Content-Type': 'application/json; charset=utf-8', 'Cache-Control': 'no-store', ...headers}});

const b64url = (bytes) => btoa(String.fromCharCode(...new Uint8Array(bytes)))
  .replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
const fromB64url = (text) => Uint8Array.from(atob(text.replace(/-/g, '+').replace(/_/g, '/')), (c) => c.charCodeAt(0));
const toBase64 = (text) => { let s = ''; for (const b of encoder.encode(text)) s += String.fromCharCode(b); return btoa(s); };
const fromBase64 = (text) => new TextDecoder().decode(Uint8Array.from(atob(text.replace(/\s/g, '')), (c) => c.charCodeAt(0)));

async function sessionKey(env) {
  // Changing the password signs every device out.
  const seed = await crypto.subtle.digest('SHA-256', encoder.encode('shorts-studio-session:' + env.STUDIO_PASSWORD + ':' + env.GITHUB_REPO));
  return crypto.subtle.importKey('raw', seed, {name: 'HMAC', hash: 'SHA-256'}, false, ['sign', 'verify']);
}

export async function makeSession(env, now = Date.now()) {
  const expires = String(Math.floor(now / 1000) + SESSION_DAYS * 86400);
  const signature = await crypto.subtle.sign('HMAC', await sessionKey(env), encoder.encode(expires));
  return expires + '.' + b64url(signature);
}

export async function validSession(env, cookie, now = Date.now()) {
  const match = /(?:^|;\s*)ss=(\d{1,12})\.([A-Za-z0-9_-]{20,})/.exec(cookie || '');
  if (!match || Number(match[1]) < now / 1000) return false;
  try {
    return await crypto.subtle.verify('HMAC', await sessionKey(env), fromB64url(match[2]), encoder.encode(match[1]));
  } catch {
    return false;
  }
}

async function samePassword(a, b) {
  const [x, y] = await Promise.all([a, b].map((v) => crypto.subtle.digest('SHA-256', encoder.encode(String(v)))));
  const p = new Uint8Array(x), q = new Uint8Array(y);
  let diff = 0;
  for (let i = 0; i < p.length; i++) diff |= p[i] ^ q[i];
  return diff === 0;
}

function githubMessage(status) {
  if (status === 401) return 'GitHub rejected the token. Create a new one and update STUDIO_GITHUB_TOKEN.';
  if (status === 403 || status === 404) return 'The GitHub token cannot reach this repository or workflow. Check its permissions.';
  if (status === 422) return 'GitHub refused the request. Make sure the workflow is on the main branch.';
  return 'GitHub is not responding right now. Try again in a minute.';
}

async function github(env, method, path, body, headers = {}) {
  const url = path.startsWith('https://') ? path : `https://api.github.com/repos/${env.GITHUB_REPO}${path}`;
  return fetch(url, {method, redirect: 'manual', body: body === undefined ? undefined :
    (typeof body === 'string' || body instanceof Uint8Array ? body : JSON.stringify(body)),
    headers: {Authorization: `Bearer ${env.STUDIO_GITHUB_TOKEN}`, Accept: 'application/vnd.github+json',
      'X-GitHub-Api-Version': '2022-11-28', 'User-Agent': 'ShortsStudio-Web',
      ...(body !== undefined && typeof body === 'object' && !(body instanceof Uint8Array) ? {'Content-Type': 'application/json'} : {}),
      ...headers}});
}

async function githubJson(env, method, path, body) {
  const response = await github(env, method, path, body);
  if (!response.ok) throw new HttpError(response.status === 404 ? 404 : 502, githubMessage(response.status));
  return response.status === 204 ? null : response.json();
}

async function readBody(request) {
  if (!(request.headers.get('Content-Type') || '').includes('application/json')) throw new HttpError(415, 'Send JSON');
  try {
    const value = await request.json();
    if (!value || typeof value !== 'object' || Array.isArray(value)) throw new Error();
    return value;
  } catch {
    throw new HttpError(400, 'Invalid request');
  }
}

const pick = (value, allowed, name) => {
  const v = value === undefined || value === null || value === '' ? allowed[0] : value;
  if (!allowed.includes(v)) throw new HttpError(400, `Choose a valid ${name}`);
  return v;
};
const int = (value, low, high, fallback, name) => {
  const v = value === undefined || value === '' ? fallback : Number(value);
  if (!Number.isInteger(v) || v < low || v > high) throw new HttpError(400, `${name} must be ${low}-${high}`);
  return v;
};
const str = (value, max, name) => {
  if (value === undefined || value === null) return '';
  if (typeof value !== 'string' || value.length > max) throw new HttpError(400, `${name} is too long`);
  return value.trim();
};
const bool = (value) => value === true || value === 'true';

export function filmRequest(body) {
  return {brief: str(body.brief, 4000, 'Brief'), seconds: int(body.seconds, 15, 60, 30, 'Length'),
    voice: pick(body.voice, VOICES, 'narrator'), writer: pick(body.writer, ['auto', 'claude', 'backup'], 'writer'),
    research: pick(body.research, ['auto', 'off'], 'research option'), publish: bool(body.publish),
    privacy: pick(body.privacy, ['private', 'unlisted', 'public'], 'visibility')};
}

export function hashtags(value, max = 8) {
  const list = (Array.isArray(value) ? value : String(value || '').split(/[\s,]+/)).map((x) => String(x).trim()).filter(Boolean);
  const out = [];
  for (const item of list) {
    const word = item.replace(/^#+/, '').replace(/[^\p{L}\p{N}_]/gu, '').slice(0, 40);
    if (word && !out.some((x) => x.toLowerCase() === '#' + word.toLowerCase()) && word.toLowerCase() !== 'shorts') out.push('#' + word);
  }
  if (out.length > max) throw new HttpError(400, `Use at most ${max} hashtags`);
  return out;
}

function postTimes(value) {
  const times = Array.isArray(value) && value.length ? value.map(String) : ['18:00'];
  if (times.length > 6 || times.some((t) => !/^(?:[01]\d|2[0-3]):[0-5]\d$/.test(t)) || new Set(times).size !== times.length) {
    throw new HttpError(400, 'Post times must be different times like 09:00, at most 6 a day');
  }
  return [...times].sort();
}

// ---- Activity and phone notifications ----
// GitHub jobs report what happened to POST /api/notify (authorised by a key derived from the
// studio password and saved as the STUDIO_NOTIFY_KEY Actions secret). Events are kept in the
// STUDIO_EVENTS repository variable; important ones wake subscribed phones with a Web Push that
// carries no data: the service worker then fetches the latest event from /api/events.
const EVENTS = 'STUDIO_EVENTS';
const PUSH = 'STUDIO_PUSH';
const PUSH_KINDS = new Set(['ready', 'posted', 'scheduled', 'failed', 'signin', 'ideas', 'test', 'autopilot']);

async function notifyKey(env) {
  const key = await crypto.subtle.importKey('raw', encoder.encode('shorts-studio-notify:' + env.STUDIO_PASSWORD + ':' + env.GITHUB_REPO),
    {name: 'HMAC', hash: 'SHA-256'}, false, ['sign']);
  return b64url(await crypto.subtle.sign('HMAC', key, encoder.encode('notify')));
}

async function setupNotify(env, url) {
  // Give the GitHub jobs this page's address and the key to report events.
  const page = await readVariable(env, 'STUDIO_PAGE_URL');
  if (!page || page.url !== url.origin) {
    await writeVariable(env, 'STUDIO_PAGE_URL', {url: url.origin});
    await saveSecret(env, 'STUDIO_NOTIFY_KEY', await notifyKey(env));
  }
  return {ok: true};
}

async function readEvents(env) {
  const data = (await readVariable(env, EVENTS)) || {};
  return {events: Array.isArray(data.events) ? data.events : [], lastCheck: data.lastCheck || null};
}

async function recordEvent(env, event, push) {
  const data = await readEvents(env);
  if (event.kind === 'heartbeat') {
    data.lastCheck = event.t;
  } else {
    data.events = [event, ...data.events].slice(0, 40);
  }
  // Variables hold up to 48 KB; trim older events if needed.
  while (JSON.stringify(data).length > 40000 && data.events.length > 5) data.events.pop();
  await writeVariable(env, EVENTS, data);
  if (push) await sendPushes(env);
}

async function notify(env, request) {
  const auth = request.headers.get('Authorization') || '';
  if (!(await samePassword(auth.replace(/^Bearer\s+/, ''), await notifyKey(env)))) throw new HttpError(401, 'Not allowed');
  const body = await readBody(request);
  const kind = /^[a-z]{2,20}$/.test(body.kind || '') ? body.kind : 'info';
  const event = {id: hex(6), t: Math.floor(Date.now() / 1000), kind, title: str(body.title, 160, 'Title') || 'Shorts Studio',
    text: str(body.text, 400, 'Text'), url: /^\/(#[a-z]+)?$/.test(body.url || '') ? body.url : '/#activity'};
  if (kind === 'heartbeat') event.t = Math.floor(Date.now() / 1000);
  await recordEvent(env, event, body.push !== false && PUSH_KINDS.has(kind));
  return {ok: true};
}

async function pushState(env) {
  const stored = await readVariable(env, PUSH);
  let state = stored && await unlock(env, stored.box);
  if (!state) {
    const pair = await crypto.subtle.generateKey({name: 'ECDSA', namedCurve: 'P-256'}, true, ['sign', 'verify']);
    const jwk = await crypto.subtle.exportKey('jwk', pair.privateKey);
    const raw = new Uint8Array(await crypto.subtle.exportKey('raw', pair.publicKey));
    state = {privateJwk: {kty: jwk.kty, crv: jwk.crv, x: jwk.x, y: jwk.y, d: jwk.d}, publicKey: b64url(raw), subscriptions: []};
    await writeVariable(env, PUSH, {box: await lock(env, state)});
  }
  return state;
}

async function subscribe(env, url, body) {
  const sub = body.subscription || {};
  let endpoint;
  try { endpoint = new URL(sub.endpoint); } catch { throw new HttpError(400, 'Invalid subscription'); }
  if (endpoint.protocol !== 'https:' || String(sub.endpoint).length > 1000) throw new HttpError(400, 'Invalid subscription');
  await setupNotify(env, url);
  const state = await pushState(env);
  state.subscriptions = [{endpoint: sub.endpoint, at: Date.now()}, ...state.subscriptions.filter((x) => x.endpoint !== sub.endpoint)].slice(0, 5);
  await writeVariable(env, PUSH, {box: await lock(env, state)});
  return {ok: true};
}

async function unsubscribe(env, body) {
  const state = await pushState(env);
  state.subscriptions = state.subscriptions.filter((x) => x.endpoint !== body.endpoint);
  await writeVariable(env, PUSH, {box: await lock(env, state)});
  return {ok: true};
}

export async function vapidHeader(state, endpoint, subject, now = Date.now()) {
  const key = await crypto.subtle.importKey('jwk', {...state.privateJwk, ext: true}, {name: 'ECDSA', namedCurve: 'P-256'}, false, ['sign']);
  const part = (value) => b64url(encoder.encode(JSON.stringify(value)));
  const unsigned = part({typ: 'JWT', alg: 'ES256'}) + '.' + part({aud: new URL(endpoint).origin, exp: Math.floor(now / 1000) + 12 * 3600, sub: subject});
  const signature = await crypto.subtle.sign({name: 'ECDSA', hash: 'SHA-256'}, key, encoder.encode(unsigned));
  return `vapid t=${unsigned}.${b64url(signature)}, k=${state.publicKey}`;
}

async function sendPushes(env) {
  const stored = await readVariable(env, PUSH);
  const state = stored && await unlock(env, stored.box);
  if (!state || !state.subscriptions.length) return;
  const page = await readVariable(env, 'STUDIO_PAGE_URL');
  const subject = page?.url || 'https://github.com/' + env.GITHUB_REPO;
  const gone = [];
  await Promise.all(state.subscriptions.map(async (sub) => {
    try {
      const response = await fetch(sub.endpoint, {method: 'POST', headers: {TTL: '86400', Urgency: 'high',
        Authorization: await vapidHeader(state, sub.endpoint, subject), 'Content-Length': '0'}});
      if (response.status === 404 || response.status === 410) gone.push(sub.endpoint);
    } catch { /* a phone that cannot be reached now still gets the next one */ }
  }));
  if (gone.length) {
    state.subscriptions = state.subscriptions.filter((x) => !gone.includes(x.endpoint));
    await writeVariable(env, PUSH, {box: await lock(env, state)});
  }
}

// ---- Autopilot clock (Cloudflare Cron Trigger, every 5 minutes) ----
// GitHub's own schedules can run hours late, so the page keeps time: it finds the post time that
// is due and starts that film on GitHub, once. Films start up to LEAD minutes early and public
// ones are scheduled on YouTube for the exact minute; up to GRACE minutes late they post at once.
const LEAD = 50, GRACE = 150;
const WEEK = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];
const pad2 = (n) => String(n).padStart(2, '0');

function zoneParts(date, zone) {
  return Object.fromEntries(new Intl.DateTimeFormat('en-US', {timeZone: zone, hourCycle: 'h23', year: 'numeric', month: '2-digit',
    day: '2-digit', hour: '2-digit', minute: '2-digit', weekday: 'short'}).formatToParts(date).map((x) => [x.type, x.value]));
}
function zoneOffsetMinutes(date, zone) {
  const p = zoneParts(date, zone);
  return Math.round((Date.UTC(+p.year, +p.month - 1, +p.day, +p.hour, +p.minute) - Math.floor(date.getTime() / 60000) * 60000) / 60000);
}

export function dueSlot(settings, claimed, now = new Date(), busy = new Set()) {
  const zone = settings.timezone || 'UTC';
  try { new Intl.DateTimeFormat('en-US', {timeZone: zone}); } catch { return null; }
  const days = Array.isArray(settings.days) ? settings.days : [0, 1, 2, 3, 4, 5, 6];
  for (const shift of [-1, 0, 1]) {
    const p = zoneParts(new Date(now.getTime() + shift * 86400000), zone);
    if (!days.includes(WEEK.indexOf(p.weekday))) continue;
    for (const t of [...(settings.times || ['18:00'])].sort()) {
      const [hh, mm] = t.split(':').map(Number);
      const guess = Date.UTC(+p.year, +p.month - 1, +p.day, hh, mm);
      const offset = zoneOffsetMinutes(new Date(guess - zoneOffsetMinutes(new Date(guess), zone) * 60000), zone);
      const slot = new Date(guess - offset * 60000);
      const sign = offset >= 0 ? '+' : '-';
      // Same text as Python's datetime.isoformat(timespec='minutes'), e.g. 2026-11-11T22:56+02:00.
      const label = `${p.year}-${p.month}-${p.day}T${t}${sign}${pad2(Math.floor(Math.abs(offset) / 60))}:${pad2(Math.abs(offset) % 60)}`;
      const minutes = (slot.getTime() - now.getTime()) / 60000;
      if (minutes <= LEAD && minutes >= -GRACE && !claimed.has(label) && !busy.has(label)) return {label, slot, time: t, late: minutes < 10};
    }
  }
  return null;
}

async function repoJson(env, path, fallback) {
  const response = await github(env, 'GET', `/contents/${path}?ref=${encodeURIComponent(env.GITHUB_BRANCH || 'main')}`);
  if (response.status === 404) return fallback;
  if (!response.ok) throw new HttpError(502, githubMessage(response.status));
  try { return JSON.parse(fromBase64((await response.json()).content)); } catch { return fallback; }
}

const MAX_TRIES = 2;

// "Never in my films": words the owner types. Words naming a category (alcohol, drugs...) block the
// whole category in content.py. This is the starting list.
export const AVOID_CATEGORIES = ['israel', 'alcohol', 'drugs', 'smoking', 'gambling', 'violence', 'sexual content', 'swearing'];

export function avoidList(value) {
  if (value === undefined || value === null) return [...AVOID_CATEGORIES];
  if (!Array.isArray(value) || value.length > 30) throw new HttpError(400, 'Keep at most 30 items in Never in my films');
  const out = [];
  for (const item of value) {
    const word = String(item ?? '').trim().toLowerCase().replace(/\s+/g, ' ');
    if (!word) continue;
    if (word.length > 40 || !/^[\p{L}\p{N}][\p{L}\p{N} '-]*$/u.test(word)) throw new HttpError(400, 'Use plain words in Never in my films');
    if (!out.includes(word)) out.push(word);
  }
  return out;
}

export async function autopilotTick(env, now = new Date()) {
  const settings = await repoJson(env, SETTINGS, {});
  // Extras (the "last check" note, topping up ideas) must never stop a film from starting.
  const notes = [];
  const extra = async (name, work) => { try { await work(); } catch (error) { notes.push(name + ': ' + error.message); } };
  await extra('last check', () => recordEvent(env, {kind: 'heartbeat', t: Math.floor(now.getTime() / 1000)}, false));
  if (!settings.enabled || !String(settings.domain || '').trim()) return {state: 'off', notes};
  const branch = env.GITHUB_BRANCH || 'main';
  await extra('topic ideas', async () => {
    const ideas = await repoJson(env, IDEAS, []);
    if (Array.isArray(ideas) && ideas.length < 3) {
      const recent = await githubJson(env, 'GET', '/actions/workflows/shorts-studio-ideas.yml/runs?per_page=5');
      const fresh = (recent.workflow_runs || []).some((r) => now.getTime() - Date.parse(r.created_at) < 6 * 3600000 && r.conclusion !== 'failure');
      if (!fresh) await github(env, 'POST', '/actions/workflows/shorts-studio-ideas.yml/dispatches', {ref: branch, inputs: {request_id: 'autopilot'}});
    }
  });
  const history = await repoJson(env, 'shorts-studio/autopilot/history.json', []);
  const claimed = new Set((Array.isArray(history) ? history : []).map((x) => x && x.slot).filter(Boolean));
  const films = await githubJson(env, 'GET', `/actions/workflows/${WORKFLOW}/runs?per_page=30`);
  const busy = new Set(), failed = {};
  for (const r of films.workflow_runs || []) {
    if (!(r.display_title || '').startsWith('Autopilot film ')) continue;
    const label = r.display_title.slice('Autopilot film '.length);
    if (r.status !== 'completed' || r.conclusion === 'success') busy.add(label);
    else if (r.conclusion === 'failure') failed[label] = (failed[label] || 0) + 1;
  }
  // A post time gets one retry at most, so a broken day never uses Claude over and over.
  for (const [label, count] of Object.entries(failed)) if (count >= MAX_TRIES) busy.add(label);
  const due = dueSlot(settings, claimed, now, busy);
  if (!due) {
    const next = upcoming(settings, now);
    return {state: 'waiting', notes, next: next ? next.label : null};
  }
  await githubJson(env, 'POST', `/actions/workflows/${WORKFLOW}/dispatches`, {ref: branch, inputs: {brief: '', slot: due.label}});
  await extra('notification', () => recordEvent(env, {id: hex(6), t: Math.floor(now.getTime() / 1000), kind: 'autopilot',
    title: `Autopilot started the ${due.time} film`,
    text: due.late ? 'It posts as soon as it is ready (about 15 minutes).' : `It goes live at ${due.time} (${settings.timezone}).`, url: '/#create'}, true));
  return {state: 'started', label: due.label, notes};
}

function upcoming(settings, now) {
  // The next post time that is not due yet, for the "Check now" answer.
  for (let minutes = 55; minutes <= 8 * 24 * 60; minutes += 5) {
    const found = dueSlot(settings, new Set(), new Date(now.getTime() + minutes * 60000));
    if (found && found.slot > now) return found;
  }
  return null;
}

// ---- Topic ideas (Autopilot "Up next") ----
const IDEAS = 'shorts-studio/autopilot/ideas.json';

export function cleanIdeas(value) {
  if (!Array.isArray(value) || value.length > 30) throw new HttpError(400, 'Keep at most 30 ideas');
  const seen = new Set();
  return value.map((item) => {
    const topic = str(item?.topic, 200, 'Topic');
    if (!topic) throw new HttpError(400, 'Every idea needs a topic');
    let id = String(item.id || '');
    if (!/^[a-z0-9]{1,24}$/.test(id) || seen.has(id)) id = hex(6);
    seen.add(id);
    const score = Number.isFinite(Number(item.score)) ? Math.max(0, Math.min(100, Math.round(Number(item.score)))) : 50;
    const evidence = (Array.isArray(item.evidence) ? item.evidence : []).slice(0, 3)
      .filter((e) => typeof e?.url === 'string' && /^https:\/\//.test(e.url) && e.url.length <= 500)
      .map((e) => ({title: String(e.title || '').slice(0, 200), url: e.url}));
    return {id, topic, angle: str(item.angle, 300, 'Angle'), brief: str(item.brief, 2000, 'Brief') || topic,
      reason: str(item.reason, 300, 'Reason'), score, evidence,
      source: ['claude', 'backup', 'you'].includes(item.source) ? item.source : 'you',
      createdAt: Number.isFinite(Number(item.createdAt)) ? Number(item.createdAt) : Math.floor(Date.now() / 1000)};
  });
}

async function readIdeas(env) {
  const [file, runs] = await Promise.all([
    github(env, 'GET', `/contents/${IDEAS}?ref=${encodeURIComponent(env.GITHUB_BRANCH || 'main')}`),
    github(env, 'GET', '/actions/workflows/shorts-studio-ideas.yml/runs?per_page=3')]);
  let ideas = [], sha = '';
  if (file.ok) {
    const data = await file.json();
    sha = data.sha;
    try { ideas = JSON.parse(fromBase64(data.content)); } catch { ideas = []; }
  } else if (file.status !== 404) {
    throw new HttpError(502, githubMessage(file.status));
  }
  const active = runs.ok ? ((await runs.json()).workflow_runs || []).some((r) => r.status !== 'completed') : false;
  return {ideas: Array.isArray(ideas) ? ideas : [], sha, refreshing: active};
}

export function publishRequest(body, now = Date.now()) {
  const privacy = pick(body.privacy, ['public', 'unlisted', 'private'], 'visibility');
  const list = Array.isArray(body.films) ? body.films : [];
  if (!list.length || list.length > 20) throw new HttpError(400, 'Choose between 1 and 20 films');
  const seen = new Set();
  const films = list.map((item) => {
    const run = String(item?.run ?? '');
    if (!/^\d{1,20}$/.test(run) || seen.has(run)) throw new HttpError(400, 'Invalid film selection');
    seen.add(run);
    let publishAt = '';
    if (item.publishAt) {
      const when = Date.parse(item.publishAt);
      if (!Number.isFinite(when) || when < now + 10 * 60000 || when > now + 180 * 86400000) {
        throw new HttpError(400, 'Scheduled times must be at least 10 minutes ahead and within 6 months');
      }
      if (privacy !== 'public') throw new HttpError(400, 'Only public films can be scheduled');
      publishAt = new Date(when).toISOString().replace(/\.\d{3}Z$/, 'Z');
    }
    return {run, publishAt, ...(Array.isArray(item.hashtags) ? {hashtags: hashtags(item.hashtags, 9)} : {})};
  });
  return {films, privacy, hashtags: hashtags(body.hashtags)};
}

export function autopilotSettings(body) {
  const days = Array.isArray(body.days) ? body.days.map(Number) : [];
  if (!days.length || days.some((d) => !Number.isInteger(d) || d < 0 || d > 6) || new Set(days).size !== days.length) {
    throw new HttpError(400, 'Choose at least one day');
  }
  const timezone = str(body.timezone, 64, 'Timezone') || 'UTC';
  try {
    new Intl.DateTimeFormat('en-US', {timeZone: timezone});
  } catch {
    throw new HttpError(400, 'Choose a valid timezone, for example Asia/Beirut');
  }
  const settings = {enabled: bool(body.enabled), domain: str(body.domain, 2000, 'Domain'),
    audience: str(body.audience, 400, 'Audience') || 'General audience', language: str(body.language, 80, 'Language') || 'English',
    tone: str(body.tone, 400, 'Tone') || 'Clear, engaging and factual', timezone, days: [...days].sort(),
    seconds: int(body.seconds, 15, 60, 30, 'Length'), voice: pick(body.voice, VOICES, 'narrator'),
    publish: bool(body.publish), publishBackup: bool(body.publishBackup),
    privacy: pick(body.privacy, ['private', 'unlisted', 'public'], 'visibility'),
    writer: pick(body.writer, ['auto', 'claude', 'backup'], 'writer'), times: postTimes(body.times),
    captions: body.captions === undefined ? true : bool(body.captions),
    endCard: body.endCard === undefined ? true : bool(body.endCard), hashtags: hashtags(body.hashtags),
    avoid: avoidList(body.avoid)};
  if (settings.enabled && !settings.domain) throw new HttpError(400, 'Describe the subject area before turning autopilot on');
  return settings;
}

const STAGES = [
  [/install|prepare|set up|checkout|run actions/i, 'Preparing the studio'],
  [/create the film/i, 'Writing and rendering'],
  [/upload-artifact|post /i, 'Packing the film'],
  [/save to the library|download-artifact/i, 'Saving to your library'],
];

function stageOf(jobs) {
  const job = jobs.find((j) => j.status === 'in_progress') || jobs.find((j) => j.status === 'queued');
  if (!job) return 'Starting';
  const step = (job.steps || []).find((s) => s.status === 'in_progress');
  if (job.name === 'deliver') return 'Saving to your library';
  for (const [pattern, label] of STAGES) if (step && pattern.test(step.name)) return label;
  return 'Working';
}

// A job GitHub refuses to start (billing, used-up minutes, Actions disabled) fails within seconds.
export const BLOCKED = 'GitHub did not start the job. Private repositories get a limited number of free Actions ' +
  'minutes, and GitHub stops all jobs once they are used up or if billing needs attention. Check GitHub → Settings → ' +
  'Billing and licensing, or make the repository public: Actions are free and unlimited for public repositories.';
export const quickFail = (r) => r.status === 'completed' && r.conclusion === 'failure' &&
  Date.parse(r.updated_at) - Date.parse(r.run_started_at || r.created_at) < 20000;

async function runs(env) {
  const data = await githubJson(env, 'GET', `/actions/workflows/${WORKFLOW}/runs?per_page=10`);
  const list = (data.workflow_runs || []).map((r) => ({id: r.id, title: r.display_title, status: r.status,
    conclusion: r.conclusion, event: r.event, createdAt: r.created_at, updatedAt: r.updated_at, url: r.html_url,
    requestId: (/^Film ([0-9a-f]{8})$/.exec(r.display_title || '') || [])[1] || null, blocked: quickFail(r)}));
  await Promise.all(list.filter((r) => r.status !== 'completed').slice(0, 3).map(async (run) => {
    try {
      const jobs = await githubJson(env, 'GET', `/actions/runs/${run.id}/jobs`);
      run.stage = stageOf(jobs.jobs || []);
    } catch {
      run.stage = 'Working';
    }
  }));
  return list;
}

async function release(env) {
  const releases = await githubJson(env, 'GET', '/releases?per_page=100');
  return releases.find((r) => r.draft && r.name === LIBRARY) || null;
}

async function assetResponse(env, asset, request, extra = {}) {
  const first = await github(env, 'GET', `/releases/assets/${asset.id}`, undefined, {Accept: 'application/octet-stream'});
  let response = first;
  if ([301, 302, 303, 307, 308].includes(first.status)) {
    // Never forward the GitHub token to the storage host.
    const range = request && request.headers.get('Range');
    response = await fetch(first.headers.get('Location'), {headers: range ? {Range: range} : {}});
  }
  if (!response.ok) throw new HttpError(502, 'The film could not be loaded from GitHub');
  const headers = new Headers({'Cache-Control': 'private, max-age=3600', 'Accept-Ranges': 'bytes', ...extra});
  for (const name of ['Content-Length', 'Content-Range', 'ETag', 'Last-Modified']) {
    if (response.headers.get(name)) headers.set(name, response.headers.get(name));
  }
  return new Response(response.body, {status: response.status, headers});
}

async function library(env, found) {
  const draft = found === undefined ? await release(env) : found;
  const index = draft && (draft.assets || []).find((a) => a.name === 'library.json');
  if (!index) return {draft, films: []};
  const response = await assetResponse(env, index, null);
  const films = await response.json();
  return {draft, films: Array.isArray(films) ? films : []};
}

const STOP = new Set('the a an and or but of to in on at for with from by is are was were be this that how why what when where who which does your you not into about than then just very more most also only some such out over facts fact'.split(' '));
// Topic hashtags for films saved before the library kept them.
export function suggestHashtags(title, language) {
  const words = String(title || '').split(/\s+/).map((w) => w.replace(/[^\p{L}\p{N}]/gu, '')).filter((w) => w.length >= 4 && !STOP.has(w.toLowerCase()));
  const base = words.slice(0, 3).map((w) => '#' + (/^[\x00-\x7f]+$/.test(w) ? w.toLowerCase() : w));
  const generic = String(language || '').startsWith('ar') ? ['#معلومات', '#هل_تعلم', '#حقائق'] : ['#facts', '#didyouknow', '#learn'];
  return [...new Set([...base, ...generic])].slice(0, 9);
}

const publicFilm = (f) => ({
  hashtags: Array.isArray(f.hashtags) && f.hashtags.length ? f.hashtags.slice(0, 9).map(String) : suggestHashtags(f.title, f.language),run: String(f.run), title: f.title || 'Untitled film', description: f.description || '',
  writer: f.writer === 'claude' ? 'claude' : 'backup', writerDetail: f.writerDetail || '', seconds: f.seconds || null,
  createdAt: f.createdAt || null, brief: f.brief || '', youtube: f.youtube || '', uploadError: f.uploadError || '',
  autonomous: !!f.autonomous, fallbackReason: f.fallbackReason || '', poster: !!f.poster,
  claudeIssue: ['signin', 'limit'].includes(f.claudeIssue) ? f.claudeIssue : '',
  scheduledFor: typeof f.scheduledFor === 'string' ? f.scheduledFor : '', slot: typeof f.slot === 'string' ? f.slot : ''});

async function removeFilm(env, run) {
  const {draft, films} = await library(env);
  if (!draft) throw new HttpError(404, 'Film not found');
  const names = new Set([`film-${run}.mp4`, `film-${run}.png`, 'library.json']);
  for (const asset of draft.assets || []) {
    if (names.has(asset.name)) {
      const response = await github(env, 'DELETE', `/releases/assets/${asset.id}`);
      if (!response.ok && response.status !== 404) throw new HttpError(502, githubMessage(response.status));
    }
  }
  const kept = films.filter((f) => String(f.run) !== run);
  const upload = await github(env, 'POST',
    `https://uploads.github.com/repos/${env.GITHUB_REPO}/releases/${draft.id}/assets?name=library.json`,
    encoder.encode(JSON.stringify(kept)), {'Content-Type': 'application/json'});
  if (!upload.ok) throw new HttpError(502, githubMessage(upload.status));
}

async function route(request, env, url) {
  const path = url.pathname;
  const method = request.method;
  if (method !== 'GET' && method !== 'HEAD') {
    // Cookies are SameSite=Strict; also refuse cross-site writes outright.
    const origin = request.headers.get('Origin');
    if (origin && origin !== url.origin) throw new HttpError(403, 'Cross-site request refused');
  }
  if (path === '/api/login' && method === 'POST') {
    const body = await readBody(request);
    if (typeof body.password !== 'string' || !(await samePassword(body.password, env.STUDIO_PASSWORD))) {
      await new Promise((resolve) => setTimeout(resolve, 800));
      throw new HttpError(401, 'Wrong password');
    }
    const cookie = `ss=${await makeSession(env)}; Path=/; Max-Age=${SESSION_DAYS * 86400}; HttpOnly; Secure; SameSite=Strict`;
    return json({ok: true}, 200, {'Set-Cookie': cookie});
  }
  if (path === '/api/logout' && method === 'POST') {
    return json({ok: true}, 200, {'Set-Cookie': 'ss=; Path=/; Max-Age=0; HttpOnly; Secure; SameSite=Strict'});
  }
  if (path === '/api/youtube/callback' && method === 'GET') return youtubeCallback(request, env, url);
  if (path === '/api/notify' && method === 'POST') return json(await notify(env, request));
  if (!(await validSession(env, request.headers.get('Cookie')))) throw new HttpError(401, 'Please sign in');

  if (path === '/api/session' && method === 'GET') return json({ok: true, repo: env.GITHUB_REPO, voices: VOICES});
  if (path === '/api/films' && method === 'POST') {
    const film = filmRequest(await readBody(request));
    const id = [...crypto.getRandomValues(new Uint8Array(4))].map((b) => b.toString(16).padStart(2, '0')).join('');
    await githubJson(env, 'POST', `/actions/workflows/${WORKFLOW}/dispatches`, {ref: env.GITHUB_BRANCH || 'main',
      inputs: {brief: film.brief, seconds: String(film.seconds), voice: film.voice, writer: film.writer,
        research: film.research, publish: film.publish ? 'true' : 'false', privacy: film.privacy, request_id: id}});
    return json({requestId: id}, 202);
  }
  if (path === '/api/runs' && method === 'GET') return json({runs: await runs(env)});
  if (path === '/api/publish' && method === 'POST') {
    const {films, privacy, hashtags: tags} = publishRequest(await readBody(request));
    const id = [...crypto.getRandomValues(new Uint8Array(4))].map((b) => b.toString(16).padStart(2, '0')).join('');
    await githubJson(env, 'POST', '/actions/workflows/shorts-studio-publish.yml/dispatches', {ref: env.GITHUB_BRANCH || 'main',
      inputs: {films: JSON.stringify(films), privacy, hashtags: tags.join(' '), request_id: id}});
    return json({requestId: id, count: films.length}, 202);
  }
  if (path === '/api/films' && method === 'GET') {
    const {films} = await library(env);
    return json({films: films.map(publicFilm)});
  }
  const media = /^\/api\/films\/(\d{1,20})\/(video|poster)$/.exec(path);
  if (media && (method === 'GET' || method === 'HEAD')) {
    const [, run, kind] = media;
    const draft = await release(env);
    const name = `film-${run}.${kind === 'video' ? 'mp4' : 'png'}`;
    const asset = draft && (draft.assets || []).find((a) => a.name === name);
    if (!asset) throw new HttpError(404, 'Film not found');
    const disposition = url.searchParams.get('download') ? 'attachment' : 'inline';
    return assetResponse(env, asset, request, {'Content-Type': kind === 'video' ? 'video/mp4' : 'image/png',
      'Content-Disposition': `${disposition}; filename="shorts-studio-${run}.${kind === 'video' ? 'mp4' : 'png'}"`});
  }
  const remove = /^\/api\/films\/(\d{1,20})$/.exec(path);
  if (remove && method === 'DELETE') {
    await removeFilm(env, remove[1]);
    return json({ok: true});
  }
  if (path === '/api/autopilot' && method === 'GET') {
    const file = await githubJson(env, 'GET', `/contents/${SETTINGS}?ref=${encodeURIComponent(env.GITHUB_BRANCH || 'main')}`);
    return json({settings: JSON.parse(fromBase64(file.content)), sha: file.sha});
  }
  if (path === '/api/autopilot' && method === 'PUT') {
    const body = await readBody(request);
    const settings = autopilotSettings(body.settings || {});
    const saved = await githubJson(env, 'PUT', `/contents/${SETTINGS}`, {message: 'Update autopilot settings from the Shorts Studio page',
      content: toBase64(JSON.stringify(settings, null, 2) + '\n'), sha: str(body.sha, 64, 'Version'),
      branch: env.GITHUB_BRANCH || 'main'});
    // Check right away, so a post time that is coming up (or just passed) starts now.
    let started = null;
    try { started = await autopilotTick(env); } catch (error) { console.error('autopilot check', error); }
    return json({settings, sha: saved.content.sha, started: started && started.label ? started.label : null});
  }
  if (path === '/api/autopilot/check' && method === 'POST') {
    try {
      return json(await autopilotTick(env));
    } catch (error) {
      return json({state: 'error', message: error instanceof HttpError ? error.message : 'The check failed: ' + error.message});
    }
  }
  if (path === '/api/events' && method === 'GET') return json(await readEvents(env));
  if (path === '/api/push/setup' && method === 'POST') return json(await setupNotify(env, url));
  if (path === '/api/push/key' && method === 'GET') return json({publicKey: (await pushState(env)).publicKey});
  if (path === '/api/push/subscribe' && method === 'POST') return json(await subscribe(env, url, await readBody(request)));
  if (path === '/api/push/unsubscribe' && method === 'POST') return json(await unsubscribe(env, await readBody(request)));
  if (path === '/api/push/test' && method === 'POST') {
    await recordEvent(env, {kind: 'test', title: 'Notifications are on', text: 'You will hear from Shorts Studio when films are ready or need attention.', url: '/#activity'}, true);
    return json({ok: true});
  }
  if (path === '/api/ideas' && method === 'GET') return json(await readIdeas(env));
  if (path === '/api/ideas' && method === 'PUT') {
    const body = await readBody(request);
    const ideas = cleanIdeas(body.ideas);
    const saved = await githubJson(env, 'PUT', `/contents/${IDEAS}`, {message: 'Update topic ideas from the Shorts Studio page',
      content: toBase64(JSON.stringify(ideas, null, 1) + '\n'), ...(body.sha ? {sha: str(body.sha, 64, 'Version')} : {}),
      branch: env.GITHUB_BRANCH || 'main'});
    return json({ideas, sha: saved.content.sha});
  }
  if (path === '/api/ideas/refresh' && method === 'POST') {
    const id = hex(4);
    await githubJson(env, 'POST', '/actions/workflows/shorts-studio-ideas.yml/dispatches', {ref: env.GITHUB_BRANCH || 'main', inputs: {request_id: id}});
    return json({requestId: id}, 202);
  }
  if (path === '/api/account' && method === 'GET') return json(await account(env, url));
  if (path === '/api/youtube/connect' && method === 'POST') return youtubeConnect(env, url, await readBody(request));
  if (path === '/api/youtube/disconnect' && method === 'POST') {
    const response = await github(env, 'DELETE', '/actions/secrets/YOUTUBE_OAUTH_JSON');
    if (!response.ok && response.status !== 404) throw new HttpError(502, githubMessage(response.status));
    return json({ok: true});
  }
  const cancel = /^\/api\/runs\/(\d{1,20})\/cancel$/.exec(path);
  if (cancel && method === 'POST') {
    const response = await github(env, 'POST', `/actions/runs/${cancel[1]}/cancel`);
    if (!response.ok && response.status !== 409) throw new HttpError(502, githubMessage(response.status));
    return json({ok: true});
  }
  if (path === '/api/claude-login' && method === 'POST') return json(await startLogin(env), 202);
  if (path === '/api/claude-login' && method === 'GET') return json(await loginStatus(env, url.searchParams.get('nonce') || ''));
  if (path === '/api/claude-login' && method === 'DELETE') return json(await cancelLogin(env, url.searchParams.get('nonce') || ''));
  if (path === '/api/claude-login/code' && method === 'POST') return json(await sendLoginCode(env, await readBody(request)));
  throw new HttpError(404, 'Not found');
}

// ---- Claude sign-in renewal (see cloud/claude_login.py) ----
// The runner and the page exchange the sign-in link and the one-time code through two
// repository variables. The new token itself never leaves the runner.
const LOGIN_STATE = 'STUDIO_LOGIN_STATE';
const LOGIN_CODE = 'STUDIO_LOGIN_CODE';
const LOGIN_WORKFLOW = 'shorts-studio-claude-login.yml';
const NEEDS_PERMISSIONS = 'To renew Claude from this page, give your GitHub token (STUDIO_GITHUB_TOKEN) the Variables and ' +
  'Secrets permissions (read and write), then try again.';

async function variable(env, method, name, value) {
  const path = '/actions/variables' + (method === 'POST' ? '' : '/' + name);
  const response = await github(env, method, path, value === undefined ? undefined : {name, value: JSON.stringify(value)});
  if (response.status === 403) throw new HttpError(403, NEEDS_PERMISSIONS);
  return response;
}

async function readVariable(env, name) {
  const response = await variable(env, 'GET', name);
  if (response.status === 404) return null;
  if (!response.ok) throw new HttpError(502, githubMessage(response.status));
  try {
    return JSON.parse((await response.json()).value);
  } catch {
    return null;
  }
}

async function writeVariable(env, name, value) {
  let response = await variable(env, 'PATCH', name, value);
  if (response.status === 404) response = await variable(env, 'POST', name, value);
  if (!response.ok) throw new HttpError(502, githubMessage(response.status));
}

async function account(env, url) {
  const response = await github(env, 'GET', '/actions/secrets/CLAUDE_CODE_OAUTH_TOKEN');
  let claude = {connected: false, renewedAt: null, expiresAt: null};
  if (response.ok) {
    const {updated_at: updated} = await response.json();
    const renewed = Date.parse(updated);
    claude = {connected: true, renewedAt: updated, expiresAt: new Date(renewed + 365 * 86400000).toISOString()};
  } else if (response.status === 403) {
    claude.permission = false;
  } else if (response.status !== 404) {
    throw new HttpError(502, githubMessage(response.status));
  }
  const youtube = await github(env, 'GET', '/actions/secrets/YOUTUBE_OAUTH_JSON');
  const client = await github(env, 'GET', `/actions/variables/${YT_CLIENT}`);
  return {repo: env.GITHUB_REPO, branch: env.GITHUB_BRANCH || 'main', claude,
    youtube: {connected: youtube.ok, clientSaved: client.ok, redirectUri: url ? url.origin + YT_CALLBACK : ''}};
}

// ---- YouTube connection (Google OAuth, official upload scope) ----
// The Google client JSON is uploaded once and kept in a repository variable, encrypted with a key
// derived from the studio password. The resulting refresh token is sealed into the
// YOUTUBE_OAUTH_JSON Actions secret, which the deliver job uses to upload.
const YT_CLIENT = 'STUDIO_YOUTUBE_CLIENT';
const YT_CALLBACK = '/api/youtube/callback';
const YT_SCOPE = 'https://www.googleapis.com/auth/youtube.upload';

async function boxKey(env) {
  const seed = await crypto.subtle.digest('SHA-256', encoder.encode('shorts-studio-youtube:' + env.STUDIO_PASSWORD + ':' + env.GITHUB_REPO));
  return crypto.subtle.importKey('raw', seed, 'AES-GCM', false, ['encrypt', 'decrypt']);
}
async function lock(env, value) {
  const iv = crypto.getRandomValues(new Uint8Array(12));
  const data = await crypto.subtle.encrypt({name: 'AES-GCM', iv}, await boxKey(env), encoder.encode(JSON.stringify(value)));
  return b64url(iv) + '.' + b64url(data);
}
async function unlock(env, text) {
  try {
    const [iv, data] = String(text).split('.');
    const plain = await crypto.subtle.decrypt({name: 'AES-GCM', iv: fromB64url(iv)}, await boxKey(env), fromB64url(data));
    return JSON.parse(new TextDecoder().decode(plain));
  } catch {
    return null;
  }
}

export function googleClient(raw) {
  const value = raw && (raw.web || raw.installed);
  if (!value || typeof value.client_id !== 'string' || !value.client_id.endsWith('.apps.googleusercontent.com') ||
      typeof value.client_secret !== 'string' || !value.client_secret || value.client_id.length > 200 || value.client_secret.length > 200) {
    throw new HttpError(400, 'Choose the client JSON file you downloaded from Google Cloud (OAuth client, Web application).');
  }
  return {client_id: value.client_id, client_secret: value.client_secret};
}

async function youtubeConnect(env, url, body) {
  let client;
  if (body.client) {
    client = googleClient(body.client);
    await writeVariable(env, YT_CLIENT, {box: await lock(env, client)});
  } else {
    const stored = await readVariable(env, YT_CLIENT);
    client = stored && await unlock(env, stored.box);
    if (!client) throw new HttpError(400, 'Choose your Google client JSON file first.');
  }
  const nonce = hex(16);
  const state = await lock(env, {nonce, exp: Date.now() + 15 * 60000});
  const consent = new URL('https://accounts.google.com/o/oauth2/v2/auth');
  consent.search = new URLSearchParams({client_id: client.client_id, redirect_uri: url.origin + YT_CALLBACK,
    response_type: 'code', scope: YT_SCOPE, access_type: 'offline', prompt: 'consent', include_granted_scopes: 'true', state}).toString();
  // A short-lived cookie ties the Google redirect back to this browser.
  return json({url: consent.href}, 200, {'Set-Cookie': `ytn=${nonce}; Path=${YT_CALLBACK}; Max-Age=900; HttpOnly; Secure; SameSite=Lax`});
}

async function youtubeCallback(request, env, url) {
  const back = (result) => new Response(null, {status: 302, headers: {Location: `/?youtube=${result}#account`,
    'Set-Cookie': `ytn=; Path=${YT_CALLBACK}; Max-Age=0; HttpOnly; Secure; SameSite=Lax`}});
  if (url.searchParams.get('error')) return back('cancelled');
  const state = await unlock(env, url.searchParams.get('state') || '');
  const cookie = /(?:^|;\s*)ytn=([0-9a-f]{32})/.exec(request.headers.get('Cookie') || '');
  if (!state || state.exp < Date.now() || !cookie || cookie[1] !== state.nonce) return back('expired');
  const stored = await readVariable(env, YT_CLIENT);
  const client = stored && await unlock(env, stored.box);
  const code = url.searchParams.get('code') || '';
  if (!client || !code || code.length > 2048) return back('failed');
  const response = await fetch('https://oauth2.googleapis.com/token', {method: 'POST',
    headers: {'Content-Type': 'application/x-www-form-urlencoded'},
    body: new URLSearchParams({code, client_id: client.client_id, client_secret: client.client_secret,
      redirect_uri: url.origin + YT_CALLBACK, grant_type: 'authorization_code'})});
  const token = response.ok ? await response.json() : {};
  if (!token.refresh_token) return back(response.ok ? 'norefresh' : 'failed');
  await saveSecret(env, 'YOUTUBE_OAUTH_JSON', JSON.stringify({type: 'authorized_user', client_id: client.client_id,
    client_secret: client.client_secret, refresh_token: token.refresh_token, token_uri: 'https://oauth2.googleapis.com/token',
    scopes: [YT_SCOPE]}));
  return back('connected');
}

const hex = (bytes) => [...crypto.getRandomValues(new Uint8Array(bytes))].map((b) => b.toString(16).padStart(2, '0')).join('');

// The sign-in job needs this same GitHub token (to save the new Claude token), so the page
// copies it into the repository's Actions secrets, encrypted as GitHub requires.
async function saveSecret(env, name, plaintext) {
  const keyResponse = await github(env, 'GET', '/actions/secrets/public-key');
  if (keyResponse.status === 403 || keyResponse.status === 404) throw new HttpError(403, NEEDS_PERMISSIONS);
  if (!keyResponse.ok) throw new HttpError(502, githubMessage(keyResponse.status));
  const {key, key_id: keyId} = await keyResponse.json();
  const publicKey = Uint8Array.from(atob(key), (c) => c.charCodeAt(0));
  let value = '';
  for (const b of seal(encoder.encode(plaintext), publicKey)) value += String.fromCharCode(b);
  const saved = await github(env, 'PUT', `/actions/secrets/${name}`, {encrypted_value: btoa(value), key_id: keyId});
  if (saved.status === 403) throw new HttpError(403, NEEDS_PERMISSIONS);
  if (!saved.ok) throw new HttpError(502, githubMessage(saved.status));
}

const syncRunnerToken = (env) => saveSecret(env, 'STUDIO_GITHUB_TOKEN', env.STUDIO_GITHUB_TOKEN);

async function startLogin(env) {
  await syncRunnerToken(env);
  const nonce = hex(16);
  await variable(env, 'DELETE', LOGIN_CODE);
  await writeVariable(env, LOGIN_STATE, {nonce, state: 'starting', message: 'Starting the official Claude sign-in…',
    updatedAt: Math.floor(Date.now() / 1000)});
  const response = await github(env, 'POST', `/actions/workflows/${LOGIN_WORKFLOW}/dispatches`,
    {ref: env.GITHUB_BRANCH || 'main', inputs: {nonce}});
  if (!response.ok) throw new HttpError(502, githubMessage(response.status));
  return {nonce};
}

export function safeSignInUrl(value) {
  try {
    const url = new URL(value);
    return url.protocol === 'https:' && ['claude.com', 'claude.ai'].includes(url.hostname) &&
      url.pathname.includes('oauth/authorize') ? url.href : '';
  } catch {
    return '';
  }
}

async function loginStatus(env, nonce) {
  if (!/^[0-9a-f]{32}$/.test(nonce)) throw new HttpError(400, 'Invalid sign-in request');
  const state = await readVariable(env, LOGIN_STATE);
  if (!state || state.nonce !== nonce || state.state === 'starting') {
    // Until the job reports in, follow the GitHub run itself so a failure shows at once.
    const data = await githubJson(env, 'GET', `/actions/workflows/${LOGIN_WORKFLOW}/runs?per_page=5`);
    const run = (data.workflow_runs || []).find((r) => r.display_title === `Claude sign-in ${nonce}`);
    if (run && run.status === 'completed') {
      return {state: 'failed', url: '', details: run.html_url, blocked: quickFail(run),
        message: run.conclusion === 'cancelled' ? 'The sign-in was cancelled.'
          : quickFail(run) ? BLOCKED : 'The sign-in job stopped before Claude answered. Try again.'};
    }
    return {state: 'starting', phase: !run || run.status !== 'in_progress' ? 'queued' : 'running',
      message: !run || run.status !== 'in_progress' ? 'Waiting for GitHub to start the sign-in…' : 'Opening the official Claude sign-in…'};
  }
  const states = ['starting', 'link', 'checking', 'saving', 'done', 'failed'];
  return {state: states.includes(state.state) ? state.state : 'failed', message: String(state.message || '').slice(0, 300),
    url: state.state === 'link' ? safeSignInUrl(state.url) : ''};
}

async function cancelLogin(env, nonce) {
  if (!/^[0-9a-f]{32}$/.test(nonce)) throw new HttpError(400, 'Invalid sign-in request');
  const data = await githubJson(env, 'GET', `/actions/workflows/${LOGIN_WORKFLOW}/runs?per_page=5`);
  const run = (data.workflow_runs || []).find((r) => r.display_title === `Claude sign-in ${nonce}` && r.status !== 'completed');
  if (run) await github(env, 'POST', `/actions/runs/${run.id}/cancel`);
  await variable(env, 'DELETE', LOGIN_CODE);
  return {ok: true};
}

async function sendLoginCode(env, body) {
  const nonce = str(body.nonce, 64, 'Request');
  const code = str(body.code, 512, 'Code');
  if (!/^[0-9a-f]{32}$/.test(nonce)) throw new HttpError(400, 'Invalid sign-in request');
  if (!/^[A-Za-z0-9_\-#.~]{10,512}$/.test(code)) throw new HttpError(400, 'Paste the whole code from Claude');
  const state = await readVariable(env, LOGIN_STATE);
  if (!state || state.nonce !== nonce || state.state !== 'link') {
    throw new HttpError(409, 'This sign-in is no longer waiting for a code. Start again.');
  }
  await writeVariable(env, LOGIN_CODE, {nonce, code});
  return {ok: true};
}

export default {
  async scheduled(event, env, ctx) {
    if (!env.STUDIO_PASSWORD || !env.STUDIO_GITHUB_TOKEN || !env.GITHUB_REPO) return;
    ctx.waitUntil(autopilotTick(env, new Date(event.scheduledTime || Date.now())).catch((error) => console.error('autopilot', error)));
  },
  async fetch(request, env) {
    const url = new URL(request.url);
    if (!url.pathname.startsWith('/api/')) return env.ASSETS.fetch(request);
    const missing = ['STUDIO_PASSWORD', 'STUDIO_GITHUB_TOKEN', 'GITHUB_REPO'].filter((name) => !env[name]);
    if (missing.length) {
      // Build variables are not visible here: runtime secrets live under Settings → Variables and Secrets.
      return json({error: `Setup is not finished. The page cannot see: ${missing.join(', ')}. In Cloudflare open ` +
        'shortsstudio → Settings → Variables and Secrets (not the Build section), add them as Secret, then Deploy.'}, 503);
    }
    try {
      return await route(request, env, url);
    } catch (error) {
      if (error instanceof HttpError) return json({error: error.message}, error.status);
      console.error(error);
      return json({error: 'Something went wrong. Try again.'}, 500);
    }
  },
};
