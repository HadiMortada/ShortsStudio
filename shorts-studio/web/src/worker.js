import {seal} from './sealed.js';

// Shorts Studio web: a password-protected phone page that starts films on GitHub Actions,
// follows their progress and plays finished films from the private draft-release library.
// Secrets (Cloudflare): STUDIO_PASSWORD, STUDIO_GITHUB_TOKEN. Vars: GITHUB_REPO, GITHUB_BRANCH.

export const VOICES = ['auto', 'en-US-AndrewMultilingualNeural', 'en-US-AvaMultilingualNeural', 'en-US-GuyNeural',
  'en-US-JennyNeural', 'en-GB-RyanNeural', 'en-GB-SoniaNeural', 'ar-LB-RamiNeural', 'ar-LB-LaylaNeural',
  'ar-SA-HamedNeural', 'ar-SA-ZariyahNeural'];
const WORKFLOW = 'shorts-studio-claude.yml';
const EDIT_WORKFLOW = 'shorts-studio-edit.yml';
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
    privacy: pick(body.privacy, ['private', 'unlisted', 'public'], 'visibility'), instagram: instagramIds(body.instagram),
    youtube: youtubeChannel(body.youtube)};
}

// A YouTube channel id (UC + 22 characters), or '' for the default channel.
export function youtubeChannel(value) {
  const id = String(value ?? '').trim();
  if (id && !/^UC[\w-]{22}$/.test(id)) throw new HttpError(400, 'Invalid YouTube channel choice');
  return id;
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
const CLAUDE_USAGE = 'STUDIO_CLAUDE_USAGE';
const USAGE_WORKFLOW = 'shorts-studio-usage.yml';
const USAGE_FRESH_SECONDS = 300;

// ---- Which AI writes and edits: Claude or ChatGPT (Settings → AI, saved in settings.json) ----
export const AI_PROVIDERS = ['claude', 'chatgpt'];
export const AI_NAMES = {claude: 'Claude', chatgpt: 'ChatGPT'};
// Settings → AI → Model, the default first (same lists as cloud/ai.py CHOICES).
export const CLAUDE_MODELS = ['claude-opus-5-5', 'claude-fable-5-2', 'claude-sonnet-5-5', 'claude-haiku-5-5'];
export const CHATGPT_MODELS = ['gpt-6.2-astra', 'gpt-6.2-sol', 'gpt-6.1-terra', 'gpt-6-luna'];
let aiCache = {at: 0, value: 'claude'};
export async function activeAi(env, fresh = false) {
  if (!fresh && Date.now() - aiCache.at < 30000) return aiCache.value;
  let value = 'claude';
  try {
    const response = await github(env, 'GET', `/contents/${SETTINGS}?ref=${encodeURIComponent(env.GITHUB_BRANCH || 'main')}`);
    if (response.ok) {
      const settings = JSON.parse(fromBase64((await response.json()).content));
      if (AI_PROVIDERS.includes(settings?.ai)) value = settings.ai;
    }
  } catch {}
  aiCache = {at: Date.now(), value};
  return value;
}
const rememberAi = (value) => { aiCache = {at: Date.now(), value: AI_PROVIDERS.includes(value) ? value : 'claude'}; };

export function cleanUsage(body, now = Math.floor(Date.now() / 1000)) {
  const value = {state: body?.state === 'ready' ? 'ready' : 'error',
    provider: AI_PROVIDERS.includes(body?.provider) ? body.provider : 'claude',
    checkedAt: Number.isInteger(body?.checkedAt) && body.checkedAt > 0 && body.checkedAt <= now + 30 ? body.checkedAt : null};
  if (!value.checkedAt) throw new HttpError(400, 'Invalid usage timestamp');
  if (value.state === 'ready') {
    for (const key of ['sessionPercent', 'weeklyPercent']) {
      if (typeof body[key] !== 'number' || !Number.isFinite(body[key]) || body[key] < 0 || body[key] > 100) {
        throw new HttpError(400, 'Invalid usage percentage');
      }
      value[key] = body[key];
    }
    for (const key of ['sessionResetsAt', 'weeklyResetsAt']) {
      if (typeof body[key] === 'string' && body[key].length <= 64 && Number.isFinite(Date.parse(body[key]))) value[key] = body[key];
    }
  } else {
    // Never echo raw provider errors or arbitrary runner text to the browser.
    const name = AI_NAMES[value.provider];
    value.message = `${name} usage could not be read. Check your ${name} sign-in and try again later.`;
  }
  return value;
}

async function usageSnapshot(env) {
  const [stored, provider] = await Promise.all([readVariable(env, CLAUDE_USAGE).then((v) => v || {}), activeAi(env)]);
  const now = Math.floor(Date.now() / 1000);
  let snapshot = {state: 'unknown'};
  if (stored.checkedAt) {
    try { snapshot = cleanUsage(stored, now); } catch {}
  }
  // A reading of the other AI (before Settings → AI was switched) is never shown as this one's.
  if (snapshot.provider && snapshot.provider !== provider) snapshot = {state: 'unknown'};
  const sameAi = (AI_PROVIDERS.includes(stored.requestedFor) ? stored.requestedFor : 'claude') === provider;
  const pending = sameAi && Number.isInteger(stored.requestedAt) && now - stored.requestedAt < 180 && stored.requestedAt > (stored.checkedAt || 0);
  return {...snapshot, provider, requestedAt: sameAi ? stored.requestedAt || null : null, refreshing: pending,
    stale: snapshot.state !== 'ready' || now - snapshot.checkedAt > USAGE_FRESH_SECONDS};
}

async function refreshUsage(env, url) {
  const snapshot = await usageSnapshot(env), now = Math.floor(Date.now() / 1000);
  const login = await readVariable(env, LOGIN_STATE);
  const reconnected = login?.state === 'done' && login.updatedAt > Math.max(snapshot.checkedAt || 0, snapshot.requestedAt || 0);
  if (!reconnected && (snapshot.refreshing || now - Math.max(snapshot.checkedAt || 0, snapshot.requestedAt || 0) < 120)) return snapshot;
  const provider = snapshot.provider || 'claude';
  const direct = provider === 'chatgpt' ? await directChatGptUsage(env) : await directUsage(env);
  if (direct) {
    await writeVariable(env, CLAUDE_USAGE, direct);
    return {...direct, refreshing: false, stale: direct.state !== 'ready'};
  }
  await setupNotify(env, url);
  await githubJson(env, 'POST', `/actions/workflows/${USAGE_WORKFLOW}/dispatches`, {ref: env.GITHUB_BRANCH || 'main'});
  await writeVariable(env, CLAUDE_USAGE, {...snapshot, requestedAt: now, requestedFor: provider});
  return {...snapshot, requestedAt: now, refreshing: true};
}

// Opens an access-only record sealed by the Actions account job (cloud/*_account.py).
async function openAccess(env, name, context) {
  const record = await readVariable(env, name);
  try {
    if (typeof record?.envelope !== 'string' || record.envelope.length > 60000) return null;
    const bytes = fromB64url(record.envelope);
    const key = await crypto.subtle.importKey('raw', await crypto.subtle.digest('SHA-256', encoder.encode(await notifyKey(env))), 'AES-GCM', false, ['decrypt']);
    // Node stores IV + tag + ciphertext; WebCrypto expects ciphertext + tag.
    const ciphertext = new Uint8Array(bytes.length - 12);
    ciphertext.set(bytes.subarray(28)); ciphertext.set(bytes.subarray(12, 28), bytes.length - 28);
    const plain = await crypto.subtle.decrypt({name: 'AES-GCM', iv: bytes.subarray(0, 12), additionalData: encoder.encode(env.GITHUB_REPO + ':' + context)}, key, ciphertext);
    const access = JSON.parse(new TextDecoder().decode(plain));
    if (typeof access.accessToken !== 'string' || !/^[\x21-\x7e]{20,16384}$/.test(access.accessToken) ||
        typeof access.expiresAt !== 'number' || !Number.isFinite(access.expiresAt) || access.expiresAt <= Date.now() + 60000) return null;
    return access;
  } catch { return null; }
}

// ChatGPT (Codex) limits: the same reading the Codex CLI's /status shows. A 5-hour window and
// a weekly window on Plus and Pro; a plan with only one window reports 0% for the other.
export function chatGptUsage(data, checkedAt) {
  const usage = {state: 'ready', provider: 'chatgpt', checkedAt};
  for (const window of [data?.rate_limit?.primary_window, data?.rate_limit?.secondary_window]) {
    const value = window?.used_percent, length = window?.limit_window_seconds;
    if (typeof value !== 'number' || !Number.isFinite(value) || value < 0 || value > 100) continue;
    const label = typeof length === 'number' && length > 0 && length <= 86400 ? 'session' : 'weekly';
    usage[label + 'Percent'] = value;
    if (Number.isFinite(window.reset_at) && window.reset_at > 0) usage[label + 'ResetsAt'] = new Date(window.reset_at * 1000).toISOString();
  }
  if (usage.sessionPercent === undefined && usage.weeklyPercent === undefined) throw new Error('Invalid usage');
  usage.sessionPercent ??= 0; usage.weeklyPercent ??= 0;
  return cleanUsage(usage);
}

async function directChatGptUsage(env) {
  const access = await openAccess(env, 'STUDIO_CHATGPT_USAGE_ACCESS', 'chatgpt-usage-access');
  if (!access || typeof access.accountId !== 'string' || !/^[\x21-\x7e]{1,200}$/.test(access.accountId)) return null;
  try {
    const response = await fetch('https://chatgpt.com/backend-api/wham/usage', {method: 'GET', redirect: 'error', signal: AbortSignal.timeout(8000),
      headers: {Authorization: 'Bearer ' + access.accessToken, 'ChatGPT-Account-Id': access.accountId, Accept: 'application/json', 'User-Agent': 'ShortsStudio'}});
    if (!response.ok) throw new Error('Usage unavailable');
    const text = await response.text();
    if (text.length > 65536) throw new Error('Invalid usage');
    return chatGptUsage(JSON.parse(text), Math.floor(Date.now() / 1000));
  } catch {
    // Provider edges may reject hosting IPs: the runner route stays available.
    return null;
  }
}

async function directUsage(env) {
  // Authenticated, encrypted access-only record. Refresh credentials remain in
  // the serialized Actions account job; neither credential reaches the phone.
  const access = await openAccess(env, 'STUDIO_CLAUDE_USAGE_ACCESS', 'usage-access');
  if (!access) return null;
  const checkedAt = Math.floor(Date.now() / 1000);
  try {
    const response = await fetch('https://api.anthropic.com/api/oauth/usage', {method: 'GET', redirect: 'error', signal: AbortSignal.timeout(8000),
      headers: {Authorization: 'Bearer ' + access.accessToken, 'anthropic-beta': 'oauth-2025-04-20', Accept: 'application/json', 'User-Agent': 'ShortsStudio'}});
    if (!response.ok) throw new Error('Usage unavailable');
    const text = await response.text();
    if (text.length > 65536) throw new Error('Invalid usage');
    const data = JSON.parse(text);
    return cleanUsage({state: 'ready', provider: 'claude', checkedAt, sessionPercent: data.five_hour?.utilization, weeklyPercent: data.seven_day?.utilization,
      sessionResetsAt: data.five_hour?.resets_at, weeklyResetsAt: data.seven_day?.resets_at});
  } catch {
    // Some provider edges reject hosting IPs. Keep the existing runner route
    // available for those cases as well as expired credentials.
    return null;
  }
}

async function receiveUsage(env, request) {
  const auth = (request.headers.get('Authorization') || '').replace(/^Bearer\s+/, '');
  if (!(await samePassword(auth, await notifyKey(env)))) throw new HttpError(401, 'Not allowed');
  const body = cleanUsage(await readBody(request));
  const previous = await readVariable(env, CLAUDE_USAGE);
  if (body.checkedAt >= (previous?.checkedAt || 0)) await writeVariable(env, CLAUDE_USAGE, body);
  return {ok: true};
}
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

// Autopilot topics: each has its own subject, language, voice, length, days, times and accounts;
// the rest is shared. A topic's post time is labelled "<ISO time>#<topic id>" (see cloud/slots.py).
const TOPIC_KEYS = ['domain', 'audience', 'language', 'tone', 'seconds', 'voice', 'days', 'times', 'publish', 'privacy', 'instagram', 'youtube'];
export function topicsOf(settings) {
  const base = {...settings};
  delete base.topics;
  const list = (Array.isArray(settings.topics) ? settings.topics : []).filter((t) => t && /^[a-z0-9]{1,12}$/.test(String(t.id || '')))
    .map((t) => [t.id, {...base, ...Object.fromEntries(TOPIC_KEYS.filter((k) => t[k] !== undefined).map((k) => [k, t[k]])), name: t.name || ''}]);
  return list.length ? list : [['', base]];
}

// The earliest due post time of any topic.
export function dueSlot(settings, claimed, now = new Date(), busy = new Set()) {
  let best = null;
  for (const [id, topic] of topicsOf(settings)) {
    if (id && !String(topic.domain || '').trim()) continue;
    const found = dueOne(topic, claimed, now, busy, id ? '#' + id : '');
    if (found && (!best || found.slot < best.slot)) best = {...found, topic: id, name: topic.name || ''};
  }
  return best;
}

function dueOne(settings, claimed, now, busy, suffix) {
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
      const label = `${p.year}-${p.month}-${p.day}T${t}${sign}${pad2(Math.floor(Math.abs(offset) / 60))}:${pad2(Math.abs(offset) % 60)}` + suffix;
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

// "Never in my films": words the owner types (empty to start). Words naming a category (alcohol,
// drugs...) block the whole category in content.py.

export function avoidList(value) {
  if (value === undefined || value === null) return [];
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
  if (!settings.enabled || !topicsOf(settings).some(([, t]) => String(t.domain || '').trim())) return {state: 'off', notes};
  const branch = env.GITHUB_BRANCH || 'main';
  await extra('topic ideas', async () => {
    const ideas = await repoJson(env, IDEAS, []);
    // Ask for ideas when any topic is running low (ideas without a topic belong to the first one).
    const lanes = topicsOf(settings), first = lanes[0][0];
    const low = lanes.some(([id, t]) => String(t.domain || '').trim() &&
      (Array.isArray(ideas) ? ideas : []).filter((x) => (x.lane || first) === id).length < 3);
    if (Array.isArray(ideas) && low) {
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
    title: `Autopilot started the ${due.time}${due.name ? ' ' + due.name : ''} film`,
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
  if (!Array.isArray(value) || value.length > 40) throw new HttpError(400, 'Keep at most 40 ideas');
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
      signal: String(item.signal || '').slice(0, 160),
      hashtags: (Array.isArray(item.hashtags) ? item.hashtags : []).slice(0, 6).map((t) => String(t))
        .filter((t) => /^#[\p{L}\p{N}_]{1,40}$/u.test(t)),
      source: ['claude', 'chatgpt', 'backup', 'you'].includes(item.source) ? item.source : 'you',
      ...(/^[a-z0-9]{1,12}$/.test(String(item.lane || '')) ? {lane: item.lane} : {}),
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

// Real YouTube view data saved by the ideas step (shorts-studio/autopilot/trends.json), trimmed for the page.
const VIEWS = 'shorts-studio/autopilot/trends.json';
export function viewSummary(data) {
  if (!data || typeof data !== 'object') return null;
  const lanes = {};
  for (const [id, lane] of Object.entries(data.lanes && typeof data.lanes === 'object' ? data.lanes : {})) {
    if (!/^[a-z0-9]{1,12}$/.test(id) || !lane || typeof lane !== 'object') continue;
    lanes[id] = {updatedAt: Number(lane.updatedAt) || null,
      hashtags: (Array.isArray(lane.hashtags) ? lane.hashtags : []).slice(0, 8).map((h) => String(h?.tag || ''))
        .filter((t) => /^#[\p{L}\p{N}_]{1,40}$/u.test(t)),
      top: (Array.isArray(lane.top) ? lane.top : []).slice(0, 3).filter((v) => /^https:\/\/www\.youtube\.com\/shorts\/[\w-]{11}$/.test(String(v?.url)))
        .map((v) => ({title: String(v.title || '').slice(0, 150), views: Number(v.views) || 0, url: v.url}))};
  }
  return {updatedAt: Number(data.updatedAt) || null, problem: String(data.problem || '').slice(0, 200), lanes};
}
async function readViews(env) {
  return viewSummary(await repoJson(env, VIEWS, null));
}

// ---- Insights: what Claude learned from the channel's own numbers (cloud/analytics.py) ----
const LESSONS = 'shorts-studio/autopilot/lessons.json';
export function insightSummary(data) {
  const text = (v, n) => String(v ?? '').slice(0, n);
  const list = (v, max, n) => (Array.isArray(v) ? v : []).filter((x) => typeof x === 'string' && x.trim()).slice(0, max).map((x) => text(x, n));
  const findings = (v) => (Array.isArray(v) ? v : []).filter((x) => x && typeof x === 'object' && x.lesson).slice(0, 6)
    .map((x) => ({lesson: text(x.lesson, 240), evidence: text(x.evidence, 240)}));
  const num = (v) => (typeof v === 'number' && Number.isFinite(v) ? v : null);
  if (!data || typeof data !== 'object') return {lessons: null};
  const table = (Array.isArray(data.table) ? data.table : []).slice(0, 30)
    .filter((r) => r && /^https:\/\/www\.youtube\.com\/shorts\/[\w-]{11}$/.test(String(r.url)))
    .map((r) => ({title: text(r.title, 150), url: r.url, views: num(r.views), viewsPerDay: num(r.viewsPerDay),
      watchedPercent: num(r.watchedPercent), afterHook: num(r.afterHook), subscribersGained: num(r.subscribersGained),
      ageDays: num(r.ageDays)}));
  return {lessons: data.summary ? {summary: text(data.summary, 600), films: num(data.films) || 0, early: !!data.early,
    by: text(data.by, 40), working: findings(data.working), notWorking: findings(data.notWorking), rules: list(data.rules, 8, 240),
    moreTopics: list(data.moreTopics, 6, 120), lessTopics: list(data.lessTopics, 6, 120), hooks: list(data.hooks, 4, 200),
    bestSeconds: num(data.bestSeconds), bestTimes: list(data.bestTimes, 3, 40), experiments: list(data.experiments, 3, 240), table} : null,
    updatedAt: num(data.updatedAt), checkedAt: num(data.checkedAt), problem: text(data.problem, 300)};
}
async function readInsights(env) {
  const [data, runs] = await Promise.all([repoJson(env, LESSONS, null),
    githubJson(env, 'GET', '/actions/workflows/shorts-studio-learn.yml/runs?per_page=3').catch(() => null)]);
  const refreshing = (runs?.workflow_runs || []).some((r) => r.status !== 'completed');
  return {...insightSummary(data), refreshing};
}

// ---- Connectors (paid helpers for films; see cloud/connectors.py) ----
// Higgsfield is "Sign in with Higgsfield" (below). The ElevenLabs key goes straight into an Actions
// secret; the page only learns whether one is saved.
const CONNECTORS = {
  elevenlabs: {secret: 'ELEVENLABS_API_KEY', pattern: /^[A-Za-z0-9_-]{20,120}$/,
    hint: 'Paste the ElevenLabs API key (ElevenLabs → Developers → API keys).'},
};
const SPEND = 'STUDIO_SPEND';
const month = (now = new Date()) => now.toISOString().slice(0, 7);
const money = (value, max) => {
  const n = Number(value);
  return Number.isFinite(n) ? Math.max(0, Math.min(max, Math.round(n * 100) / 100)) : 0;
};

export function connectorSettings(value) {
  const v = value && typeof value === 'object' ? value : {};
  const ai = v.ai && typeof v.ai === 'object' ? v.ai : {}, voice = v.voice && typeof v.voice === 'object' ? v.voice : {};
  const whole = (x, max, fallback) => Number.isInteger(Number(x)) ? Math.max(0, Math.min(max, Number(x))) : fallback;
  const voiceId = (x) => {
    const id = String(x ?? '').trim();
    if (id && !/^[A-Za-z0-9]{10,40}$/.test(id)) throw new HttpError(400, 'An ElevenLabs voice ID has only letters and numbers');
    return id;
  };
  return {ai: {images: ai.images === undefined ? true : bool(ai.images), clips: bool(ai.clips),
      maxImages: whole(ai.maxImages, 8, 4), maxClips: whole(ai.maxClips, 3, 1),
      credits: ai.credits === undefined ? 100 : money(ai.credits, 100000)},
    voice: {enabled: voice.enabled === undefined ? true : bool(voice.enabled), en: voiceId(voice.en), ar: voiceId(voice.ar),
      budget: voice.budget === undefined ? 5 : money(voice.budget, 1000)}};
}

async function readSpend(env, now = new Date()) {
  const stored = await readVariable(env, SPEND);
  return stored && stored.month === month(now)
    ? {month: stored.month, higgsfield_credits: money(stored.higgsfield_credits, 1e9), elevenlabs: money(stored.elevenlabs, 1e6)}
    : {month: month(now), higgsfield_credits: 0, elevenlabs: 0};
}

export async function addSpend(env, body, now = new Date()) {
  const service = String(body?.service || '');
  if (!['higgsfield_credits', 'elevenlabs'].includes(service)) throw new HttpError(400, 'Unknown connector');
  const amount = Number(body?.amount);
  if (!Number.isFinite(amount) || amount < 0 || amount > (service === 'higgsfield_credits' ? 500 : 50)) throw new HttpError(400, 'Invalid amount');
  const spend = await readSpend(env, now);
  spend[service] = Math.round(((spend[service] || 0) + amount) * 10000) / 10000;
  await writeVariable(env, SPEND, spend);
  return spend;
}

// ---- Sign in with Higgsfield: the owner's plan credits, through Higgsfield's MCP ----
// Standard MCP sign-in: the page registers itself with Higgsfield's sign-in server once, then uses
// the authorization-code flow with PKCE. The lasting sign-in stays encrypted in a repository
// variable; film jobs ask for a short-lived token with their notification key.
const HF_AUTH = 'https://clerk.higgsfield.ai';
const HF_MCP = 'https://mcp.higgsfield.ai/mcp';
const HF_CALLBACK = '/api/higgsfield/callback';
const HF_VAULT = 'STUDIO_HIGGSFIELD';
const HF_SCOPE = 'openid email offline_access';

async function hfVault(env) {
  const stored = await readVariable(env, HF_VAULT);
  return (stored && await unlock(env, stored.box)) || {};
}
const saveHfVault = async (env, vault) => writeVariable(env, HF_VAULT, {box: await lock(env, vault)});

async function hfToken(vault, params) {
  const response = await fetch(HF_AUTH + '/oauth/token', {method: 'POST',
    headers: {'Content-Type': 'application/x-www-form-urlencoded'},
    body: new URLSearchParams({client_id: vault.clientId, resource: HF_MCP, ...params})});
  return response.ok ? response.json() : null;
}

async function higgsfieldConnect(env, url) {
  const vault = await hfVault(env);
  const redirect = url.origin + HF_CALLBACK;
  let clientId = vault.clientId && vault.redirect === redirect ? vault.clientId : '';
  if (!clientId) {
    const registered = await fetch(HF_AUTH + '/oauth/register', {method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({client_name: 'Shorts Studio', redirect_uris: [redirect], grant_types: ['authorization_code', 'refresh_token'],
        response_types: ['code'], token_endpoint_auth_method: 'none', scope: HF_SCOPE})});
    const data = registered.ok ? await registered.json() : {};
    if (typeof data.client_id !== 'string' || !data.client_id) throw new HttpError(502, 'Higgsfield did not accept the sign-in request. Try again later.');
    clientId = data.client_id;
    await saveHfVault(env, {...vault, clientId, redirect});
  }
  const verifier = b64url(crypto.getRandomValues(new Uint8Array(32)));
  const challenge = b64url(await crypto.subtle.digest('SHA-256', encoder.encode(verifier)));
  const nonce = hex(16);
  const state = await lock(env, {nonce, verifier, exp: Date.now() + 15 * 60000});
  const consent = new URL(HF_AUTH + '/oauth/authorize');
  consent.search = new URLSearchParams({response_type: 'code', client_id: clientId, redirect_uri: redirect, scope: HF_SCOPE,
    state, code_challenge: challenge, code_challenge_method: 'S256', resource: HF_MCP, prompt: 'consent'}).toString();
  return json({url: consent.href}, 200, {'Set-Cookie': `hfn=${nonce}; Path=${HF_CALLBACK}; Max-Age=900; HttpOnly; Secure; SameSite=Lax`});
}

async function higgsfieldCallback(request, env, url) {
  const back = (result) => new Response(null, {status: 302, headers: {Location: `/?higgsfield=${result}#settings`,
    'Set-Cookie': `hfn=; Path=${HF_CALLBACK}; Max-Age=0; HttpOnly; Secure; SameSite=Lax`}});
  if (url.searchParams.get('error')) return back('cancelled');
  const state = await unlock(env, url.searchParams.get('state') || '');
  const cookie = /(?:^|;\s*)hfn=([0-9a-f]{32})/.exec(request.headers.get('Cookie') || '');
  if (!state || state.exp < Date.now() || !cookie || cookie[1] !== state.nonce) return back('expired');
  const vault = await hfVault(env);
  const code = url.searchParams.get('code') || '';
  if (!vault.clientId || !code || code.length > 4096) return back('failed');
  const token = await hfToken(vault, {grant_type: 'authorization_code', code, redirect_uri: vault.redirect, code_verifier: state.verifier});
  if (!token?.access_token) return back('failed');
  if (!token.refresh_token) return back('norefresh');
  await saveHfVault(env, {...vault, refresh: token.refresh_token, access: token.access_token,
    accessExp: Date.now() + (Number(token.expires_in) || 3600) * 1000, connectedAt: Date.now()});
  return back('connected');
}

// A token for the MCP, renewed when it has less than ten minutes left. Higgsfield may replace the
// lasting sign-in on each renewal, so the newest one is always kept.
export async function higgsfieldAccess(env, now = Date.now()) {
  const vault = await hfVault(env);
  if (!vault.refresh) return null;
  if (vault.access && vault.accessExp > now + 10 * 60000) return vault.access;
  const token = await hfToken(vault, {grant_type: 'refresh_token', refresh_token: vault.refresh});
  if (!token?.access_token) return null;
  await saveHfVault(env, {...vault, access: token.access_token, accessExp: now + (Number(token.expires_in) || 3600) * 1000,
    refresh: token.refresh_token || vault.refresh});
  return token.access_token;
}

async function higgsfieldSignOut(env) {
  const vault = await hfVault(env);
  if (vault.refresh) {
    await fetch(HF_AUTH + '/oauth/token/revoke', {method: 'POST', headers: {'Content-Type': 'application/x-www-form-urlencoded'},
      body: new URLSearchParams({client_id: vault.clientId, token: vault.refresh})}).catch(() => null);
  }
  await saveHfVault(env, {clientId: vault.clientId, redirect: vault.redirect});
  return {ok: true};
}

// One MCP tool call (streamable HTTP; the answer may come as JSON or as an event stream).
function rpcAnswer(text, id) {
  const tries = text.trim().startsWith('{') ? [text] : text.split('\n').filter((l) => l.startsWith('data:')).map((l) => l.slice(5)).reverse();
  for (const item of tries) {
    try { const message = JSON.parse(item); if (message.id === id) return message; } catch {}
  }
  return null;
}
export async function mcpCall(token, name, args) {
  const headers = {Authorization: 'Bearer ' + token, 'Content-Type': 'application/json', Accept: 'application/json, text/event-stream'};
  // After initialize, every request names its session and the protocol version (MCP 2025-06-18).
  const post = (payload, session) => fetch(HF_MCP, {method: 'POST', headers: {...headers,
    ...(session ? {'Mcp-Session-Id': session} : {}), ...(payload.method !== 'initialize' ? {'MCP-Protocol-Version': '2025-06-18'} : {})},
    body: JSON.stringify(payload)});
  const init = await post({jsonrpc: '2.0', id: 1, method: 'initialize', params: {protocolVersion: '2025-06-18', capabilities: {},
    clientInfo: {name: 'ShortsStudio', version: '1'}}});
  if (!init.ok) throw new HttpError(502, `Higgsfield answered ${init.status} when connecting${init.status === 401 ? ' (sign-in not accepted; sign out and sign in again)' : ''}.`);
  const session = init.headers.get('Mcp-Session-Id') || '';
  await init.text();
  await post({jsonrpc: '2.0', method: 'notifications/initialized'}, session).then((r) => r.text()).catch(() => '');
  const call = await post({jsonrpc: '2.0', id: 2, method: 'tools/call', params: {name, arguments: args}}, session);
  const raw = await call.text();
  const answer = rpcAnswer(raw, 2);
  const result = answer?.result;
  if (!result || result.isError) {
    const why = answer?.error?.message || (result?.content || []).map((c) => c.text || '').join(' ') || `status ${call.status}`;
    throw new HttpError(502, 'Higgsfield: ' + String(why).slice(0, 160));
  }
  if (result.structuredContent && typeof result.structuredContent === 'object') return result.structuredContent;
  const text = (result.content || []).filter((c) => c.type === 'text').map((c) => c.text).join(' ');
  try { return JSON.parse(text); } catch { return {text}; }
}

// Credits left on the owner's plan, for the Connectors card.
export function creditsFrom(value) {
  const seen = [];
  const walk = (v, key) => {
    if (typeof v === 'number' && /credit|balance/i.test(key || '') && !/used|spent|cost/i.test(key || '')) seen.push(v);
    else if (v && typeof v === 'object') for (const [k, x] of Object.entries(v)) walk(x, k);
  };
  walk(value, '');
  return seen.length ? seen[0] : null;
}
async function higgsfieldBalance(env) {
  const token = await higgsfieldAccess(env);
  if (!token) throw new HttpError(404, 'Higgsfield sign-in could not be renewed. Sign out and sign in again.');
  const balance = await mcpCall(token, 'balance', {});
  const plan = balance && typeof balance === 'object'
    ? (balance.plan?.name || balance.plan || balance.subscription?.name || balance.subscription_plan_type || '') : '';
  return {credits: creditsFrom(balance), plan: typeof plan === 'string' ? plan.slice(0, 40) : ''};
}

async function saveConnectorKey(env, name, body) {
  const connector = CONNECTORS[name];
  const key = String(body?.key ?? '').trim();
  if (!connector.pattern.test(key)) throw new HttpError(400, connector.hint);
  // A quick check that ElevenLabs accepts the key (nothing is generated or charged).
  const check = await fetch('https://api.elevenlabs.io/v1/user', {headers: {'xi-api-key': key}});
  let text = '';
  try { text = await check.text(); } catch {}
  if (check.status === 401 && !/missing_permissions/i.test(text)) {
    throw new HttpError(400, 'ElevenLabs did not accept this key. Copy it again and paste it.');
  }
  await saveSecret(env, connector.secret, key);
  return {ok: true};
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
  const instagram = instagramIds(body.instagram);
  const youtube = body.youtube === undefined ? true : bool(body.youtube);
  if (!youtube && !instagram.length) throw new HttpError(400, 'Choose where to publish');
  return {films, privacy, hashtags: hashtags(body.hashtags), youtube, instagram, channel: youtubeChannel(body.channel)};
}

function validDays(value) {
  const days = Array.isArray(value) ? value.map(Number) : [];
  if (!days.length || days.some((d) => !Number.isInteger(d) || d < 0 || d > 6) || new Set(days).size !== days.length) {
    throw new HttpError(400, 'Choose at least one day');
  }
  return [...days].sort();
}

export function autopilotTopics(value) {
  if (value === undefined || value === null) return [];
  if (!Array.isArray(value) || value.length > 8) throw new HttpError(400, 'Keep at most 8 topics');
  const seen = new Set();
  return value.map((t) => {
    const id = String(t?.id || '');
    if (!/^[a-z0-9]{1,12}$/.test(id) || seen.has(id)) throw new HttpError(400, 'Invalid topic');
    seen.add(id);
    return {id, name: str(t.name, 40, 'Topic name') || 'Topic', domain: str(t.domain, 2000, 'Domain'),
      audience: str(t.audience, 400, 'Audience') || 'General audience', language: str(t.language, 80, 'Language') || 'English',
      tone: str(t.tone, 400, 'Tone') || 'Clear, engaging and factual', seconds: int(t.seconds, 15, 60, 30, 'Length'),
      voice: pick(t.voice, VOICES, 'narrator'), days: validDays(t.days === undefined ? [0, 1, 2, 3, 4, 5, 6] : t.days), times: postTimes(t.times),
      publish: bool(t.publish), privacy: pick(t.privacy, ['private', 'unlisted', 'public'], 'visibility'), instagram: instagramIds(t.instagram),
      youtube: youtubeChannel(t.youtube)};
  });
}

export function autopilotSettings(body) {
  const limits = {};
  for (const key of ['sessionUsageLimit', 'weeklyUsageLimit']) {
    const value = body[key] === undefined ? 70 : body[key];
    if (!Number.isInteger(value) || value < 0 || value > 100) throw new HttpError(400, 'Usage limits must be whole percentages from 0 to 100');
    limits[key] = value;
  }
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
    ai: pick(body.ai, AI_PROVIDERS, 'AI'),
    claudeModel: pick(body.claudeModel, CLAUDE_MODELS, 'Claude model'),
    chatgptModel: pick(body.chatgptModel, CHATGPT_MODELS, 'ChatGPT model'),
    captions: body.captions === undefined ? true : bool(body.captions),
    endCard: body.endCard === undefined ? true : bool(body.endCard), sound: body.sound === undefined ? true : bool(body.sound),
    ...limits, hashtags: hashtags(body.hashtags),
    avoid: avoidList(body.avoid), instagram: instagramIds(body.instagram), youtube: youtubeChannel(body.youtube),
    connectors: connectorSettings(body.connectors),
    topics: autopilotTopics(body.topics)};
  if (settings.enabled && !topicsOf(settings).some(([, t]) => t.domain)) throw new HttpError(400, 'Describe the subject area before turning autopilot on');
  return settings;
}

const STAGES = [
  [/install|prepare|set up|checkout|run actions/i, 'Preparing the studio'],
  [/create the film/i, 'Writing and rendering'],
  [/upload-artifact|post /i, 'Packing the film'],
  [/save to the library|download-artifact/i, 'Saving to your library'],
];

// Video edits (shorts-studio-edit.yml) report their own steps.
const EDIT_STAGES = [
  [/download the upload/i, 'Receiving your video'],
  [/edit the video/i, 'Editing'],
  [/save to the library/i, 'Saving to your library'],
  [/install|prepare|set up|checkout|run actions|get the|keep the|check the/i, 'Preparing the editor'],
];

function stageOf(jobs, stages = STAGES) {
  const job = jobs.find((j) => j.status === 'in_progress') || jobs.find((j) => j.status === 'queued');
  if (!job) return 'Starting';
  const step = (job.steps || []).find((s) => s.status === 'in_progress');
  if (job.name === 'deliver') return 'Saving to your library';
  for (const [pattern, label] of stages) if (step && pattern.test(step.name)) return label;
  return stages === EDIT_STAGES ? 'Preparing the editor' : 'Working';
}

// A job GitHub refuses to start (billing, used-up minutes, Actions disabled) fails within seconds.
export const BLOCKED = 'GitHub did not start the job. Private repositories get a limited number of free Actions ' +
  'minutes, and GitHub stops all jobs once they are used up or if billing needs attention. Check GitHub → Settings → ' +
  'Billing and licensing, or make the repository public: Actions are free and unlimited for public repositories.';
export const quickFail = (r) => r.status === 'completed' && r.conclusion === 'failure' &&
  Date.parse(r.updated_at) - Date.parse(r.run_started_at || r.created_at) < 20000;

async function runs(env) {
  // Films and video edits; the edit workflow may not exist yet on an older branch.
  const [films, edits] = await Promise.all([
    githubJson(env, 'GET', `/actions/workflows/${WORKFLOW}/runs?per_page=10`),
    githubJson(env, 'GET', `/actions/workflows/${EDIT_WORKFLOW}/runs?per_page=10`).catch(() => ({workflow_runs: []}))]);
  const list = [...(films.workflow_runs || []).map((r) => [r, 'film']), ...(edits.workflow_runs || []).map((r) => [r, 'edit'])]
    .map(([r, kind]) => ({id: r.id, kind, title: r.display_title, status: r.status,
      conclusion: r.conclusion, event: r.event, createdAt: r.created_at, updatedAt: r.updated_at, url: r.html_url,
      requestId: (/^(?:Film|Edit) ([0-9a-f]{8})$/.exec(r.display_title || '') || [])[1] || null, blocked: quickFail(r)}))
    .sort((a, b) => Date.parse(b.createdAt) - Date.parse(a.createdAt));
  await Promise.all(list.filter((r) => r.status !== 'completed').slice(0, 4).map(async (run) => {
    try {
      const jobs = await githubJson(env, 'GET', `/actions/runs/${run.id}/jobs`);
      run.stage = stageOf(jobs.jobs || [], run.kind === 'edit' ? EDIT_STAGES : STAGES);
    } catch {
      run.stage = 'Working';
    }
  }));
  return list;
}

// ---- Video edits: the page uploads a video in pieces to a private draft release ----
const UPLOADS = 'Shorts Studio uploads';
export const UPLOAD_PIECE = 20 * 1024 * 1024;   // under Cloudflare's 100 MB request limit, light on memory
export const UPLOAD_MAX_PIECES = 150;           // 3 GB: a long talk for Best moments
const UPLOAD_KEEP_MS = 7 * 86400000;            // uploads stay a week so edited Shorts can be fixed
const pieceName = (id, part) => `edit-${id}-${String(part).padStart(3, '0')}.part`;

async function uploadsRelease(env, create) {
  const releases = await githubJson(env, 'GET', '/releases?per_page=100');
  const found = releases.find((r) => r.draft && r.name === UPLOADS);
  if (found || !create) return found || null;
  return githubJson(env, 'POST', '/releases', {tag_name: 'shorts-studio-uploads', name: UPLOADS, draft: true,
    body: 'Videos uploaded for editing from Shorts Studio (draft release, visible only to collaborators). ' +
      'Each upload is removed once its edit is saved.'});
}

async function uploadAssets(env, draft) {
  const assets = [];
  for (let page = 1; page <= 5; page++) {
    const batch = await githubJson(env, 'GET', `/releases/${draft.id}/assets?per_page=100&page=${page}`);
    assets.push(...batch);
    if (batch.length < 100) break;
  }
  return assets;
}

export async function uploadPiece(env, request, id, part, now = Date.now()) {
  if (!/^[0-9a-f]{8}$/.test(id)) throw new HttpError(400, 'Invalid upload');
  if (!Number.isInteger(part) || part < 0 || part >= UPLOAD_MAX_PIECES) throw new HttpError(413, 'This video is too large. Upload one under 3 GB.');
  if (Number(request.headers.get('Content-Length') || 0) > UPLOAD_PIECE) throw new HttpError(413, 'Upload piece is too large');
  const data = new Uint8Array(await request.arrayBuffer());
  if (!data.length || data.length > UPLOAD_PIECE) throw new HttpError(data.length ? 413 : 400, data.length ? 'Upload piece is too large' : 'Empty upload');
  const draft = await uploadsRelease(env, true);
  const name = pieceName(id, part);
  const assets = await uploadAssets(env, draft);
  for (const asset of assets) {
    // A retried piece replaces itself; the first piece of a new upload also clears abandoned ones.
    const stale = part === 0 && /^edit-[0-9a-f]{8}-\d{3}\.part$/.test(asset.name) && now - Date.parse(asset.created_at) > UPLOAD_KEEP_MS;
    if (asset.name === name || stale) await github(env, 'DELETE', `/releases/assets/${asset.id}`);
  }
  const response = await github(env, 'POST', `https://uploads.github.com/repos/${env.GITHUB_REPO}/releases/${draft.id}/assets?name=${name}`,
    data, {'Content-Type': 'application/octet-stream'});
  if (!response.ok) throw new HttpError(502, githubMessage(response.status));
  return {part, size: data.length};
}

export function editRequest(body) {
  const id = String(body.id || '');
  if (!/^[0-9a-f]{8}$/.test(id)) throw new HttpError(400, 'Invalid upload');
  const parts = int(body.parts, 1, UPLOAD_MAX_PIECES, undefined, 'Pieces');
  // The file name only labels the video; keep it short and printable.
  const name = String(typeof body.name === 'string' ? body.name : '').replace(/[\u0000-\u001f\u007f<>]/g, '').trim().slice(0, 120);
  return {id, parts, name, options: editOptions(body.options)};
}

// The Edit page's options (cloud/video_edit.py checks them again).
export const EDIT_TRANSLATE = ['', 'en', 'ar', 'fr', 'es', 'de', 'tr', 'ur', 'id', 'pt', 'fa'];
export const EDIT_DEFAULTS = {mode: 'auto', shape: 'vertical', translate: '', captions: 'pop', teaser: true, ending: true, fix: true, sfx: false};
export function editOptions(value) {
  const given = value && typeof value === 'object' && !Array.isArray(value) ? value : {};
  const out = {...EDIT_DEFAULTS};
  for (const [key, allowed] of [['mode', ['auto', 'one', 'moments']], ['shape', ['vertical', 'original']],
    ['translate', EDIT_TRANSLATE], ['captions', ['pop', 'clean', 'boxed']]]) {
    if (given[key] !== undefined && given[key] !== null) out[key] = pick(given[key], allowed, key);
  }
  for (const key of ['teaser', 'ending', 'fix', 'sfx']) if (given[key] !== undefined) out[key] = bool(given[key]);
  return out;
}

// What the owner may change when fixing an edited Short: caption lines (by key), graphics (by number),
// the opening line, and cuts to restore.
export function fixRequest(body) {
  const changes = {};
  const captions = body.captions && typeof body.captions === 'object' && !Array.isArray(body.captions) ? body.captions : {};
  const lines = Object.entries(captions);
  if (lines.length > 600) throw new HttpError(400, 'Too many caption changes');
  if (lines.length) {
    changes.captions = {};
    for (const [key, text] of lines) {
      if (!/^t?\d{1,6}\.\d{2}$/.test(key)) throw new HttpError(400, 'Invalid caption line');
      changes.captions[key] = str(text, 200, 'Caption');
    }
  }
  const visuals = body.visuals && typeof body.visuals === 'object' && !Array.isArray(body.visuals) ? body.visuals : {};
  if (Object.keys(visuals).length) {
    changes.visuals = {};
    for (const [n, change] of Object.entries(visuals).slice(0, 120)) {
      if (!/^\d{1,3}$/.test(n) || !change || typeof change !== 'object') throw new HttpError(400, 'Invalid graphic');
      changes.visuals[n] = change.remove ? {remove: true} : {text: str(change.text, 320, 'Graphic text')};
    }
  }
  if (typeof body.hook === 'string') changes.hook = str(body.hook, 60, 'Opening line');
  if (Array.isArray(body.restore)) {
    if (body.restore.length > 400 || body.restore.some((n) => !Number.isInteger(n) || n < 0 || n > 999)) throw new HttpError(400, 'Invalid cut');
    if (body.restore.length) changes.restore = [...new Set(body.restore)];
  }
  if (!Object.keys(changes).length) throw new HttpError(400, 'Nothing to change');
  const text = JSON.stringify(changes);
  if (text.length > 60000) throw new HttpError(413, 'Too many changes at once');
  return text;
}

async function startEdit(env, body) {
  const edit = editRequest(body);
  const draft = await uploadsRelease(env, false);
  const found = new Set(draft ? (await uploadAssets(env, draft)).map((a) => a.name) : []);
  for (let part = 0; part < edit.parts; part++) {
    if (!found.has(pieceName(edit.id, part))) throw new HttpError(409, 'The upload is incomplete. Choose the video again.');
  }
  await githubJson(env, 'POST', `/actions/workflows/${EDIT_WORKFLOW}/dispatches`, {ref: env.GITHUB_BRANCH || 'main',
    inputs: {upload: edit.id, parts: String(edit.parts), name: edit.name, request_id: edit.id, options: JSON.stringify(edit.options),
      ...(body.revise ? {revise: body.revise, changes: body.changes} : {})}});
  return {requestId: edit.id};
}

async function libraryAsset(env, name) {
  const draft = await release(env);
  const asset = draft && (draft.assets || []).find((a) => a.name === name);
  if (!asset) throw new HttpError(404, 'This video has no saved edit to fix');
  return (await assetResponse(env, asset, null)).json();
}

function editSourceLink(graphic) {
  if (typeof graphic.url !== 'string') return '';
  if (graphic.type === 'picture') return graphic.url.startsWith('https://commons.wikimedia.org/wiki/File:') ? graphic.url : '';
  if (graphic.type !== 'reference') return '';
  try {
    const url = new URL(graphic.url);
    return url.protocol === 'https:' && !url.username && !url.password ? url.href : '';
  } catch { return ''; }
}

// The parts of a Short's saved plan the page shows for fixing (not the word list).
async function editPlan(env, run) {
  const plan = await libraryAsset(env, `film-${run}.json`);
  return {language: plan.language || 'en', options: plan.options || {},
    groups: (plan.groups || []).slice(0, 6500).map((g) => ({key: String(g.key), s: g.s, e: g.e, text: String(g.text || ''),
      unsure: !!g.unsure, visible: g.visible !== false, translation: String(g.translation || '')})),
    graphics: (plan.graphics || []).slice(0, 120).map((g) => ({index: g.index, type: g.type, start: g.start, text: String(g.text || ''),
      source: String(g.source || ''), url: editSourceLink(g)})),
    cuts: (plan.cuts || []).slice(0, 400).map((c) => ({index: c.index, why: String(c.why || ''), text: String(c.text || '')}))};
}

async function fixShort(env, run, body) {
  const changes = fixRequest(body);
  const {films} = await library(env);
  const film = films.find((f) => String(f.run) === run);
  if (!film || film.kind !== 'edit' || !film.plan) throw new HttpError(404, 'This video has no saved edit to fix');
  if (!/^[0-9a-f]{8}$/.test(String(film.upload || '')) || !Number.isInteger(film.parts) || film.parts < 1) {
    throw new HttpError(409, 'This video was edited before fixes were possible. Upload it again.');
  }
  try {
    return await startEdit(env, {id: film.upload, parts: film.parts, name: film.brief || '', revise: run, changes});
  } catch (error) {
    if (error instanceof HttpError && error.status === 409) throw new HttpError(409, 'The original video is no longer stored (it is kept for 7 days). Upload it again.');
    throw error;
  }
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
  writer: ['claude', 'chatgpt'].includes(f.writer) ? f.writer : 'backup', writerDetail: f.writerDetail || '', seconds: f.seconds || null,
  createdAt: f.createdAt || null, brief: f.brief || '', youtube: f.youtube || '', uploadError: f.uploadError || '',
  autonomous: !!f.autonomous, fallbackReason: f.fallbackReason || '', poster: !!f.poster,
  claudeIssue: ['signin', 'limit'].includes(f.claudeIssue) ? f.claudeIssue : '',
  scheduledFor: typeof f.scheduledFor === 'string' ? f.scheduledFor : '', slot: typeof f.slot === 'string' ? f.slot : '',
  kind: f.kind === 'edit' ? 'edit' : 'film', editDetail: typeof f.editDetail === 'string' ? f.editDetail.slice(0, 200) : '',
  language: typeof f.language === 'string' ? f.language.slice(0, 8) : '',
  captionPreview: f.kind === 'edit' && !!f.captionPreview,
  canFix: f.kind === 'edit' && !!f.plan && Number(f.keepUntil || 0) * 1000 > Date.now(), keepUntil: Number(f.keepUntil || 0) || null});

async function removeFilm(env, run) {
  const {draft, films} = await library(env);
  if (!draft) throw new HttpError(404, 'Film not found');
  const names = new Set([`film-${run}.mp4`, `film-${run}-preview.mp4`, `film-${run}.png`, `film-${run}.json`, 'library.json']);
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
  if (path === '/api/higgsfield/callback' && method === 'GET') return higgsfieldCallback(request, env, url);
  if (path === '/api/instagram/callback' && method === 'GET') return instagramCallback(request, env, url);
  if (path === '/api/notify' && method === 'POST') return json(await notify(env, request));
  if (path === '/api/claude-usage/report' && method === 'POST') return json(await receiveUsage(env, request));
  // Meta downloads films through a signed two-hour link; nothing else is public.
  const link = /^\/api\/public\/films\/(\d{1,20})\/(\d{9,11})\/([A-Za-z0-9_-]{20,})\/film\.mp4$/.exec(path);
  if (link && (method === 'GET' || method === 'HEAD')) {
    if (!(await validFilmLink(env, link[1], link[2], link[3]))) throw new HttpError(403, 'This link has expired');
    const draft = await release(env);
    const asset = draft && (draft.assets || []).find((a) => a.name === `film-${link[1]}.mp4`);
    if (!asset) throw new HttpError(404, 'Film not found');
    return assetResponse(env, asset, request, {'Content-Type': 'video/mp4', 'Cache-Control': 'no-store'});
  }
  // GitHub jobs queue Instagram posts with the same key they use for notifications.
  if (path === '/api/instagram/queue' && method === 'POST') {
    const auth = (request.headers.get('Authorization') || '').replace(/^Bearer\s+/, '');
    if (!(await samePassword(auth, await notifyKey(env)))) throw new HttpError(401, 'Not allowed');
    const body = await readBody(request);
    const run = String(body.run ?? '');
    if (!/^\d{1,20}$/.test(run)) throw new HttpError(400, 'Invalid film');
    const at = body.publishAt ? Date.parse(body.publishAt) : 0;
    const added = await queueInstagram(env, [{run, accounts: instagramIds(body.accounts), publishAt: Number.isFinite(at) ? at : 0}]);
    try { await instagramTick(env, Date.now(), {publish: false}); } catch (error) { console.error('instagram', error); }
    return json({queued: added.length});
  }
  // Film jobs read this month's connector spending and report what they spend (same key).
  if (path === '/api/higgsfield/token' && method === 'POST') {
    const auth = (request.headers.get('Authorization') || '').replace(/^Bearer\s+/, '');
    if (!(await samePassword(auth, await notifyKey(env)))) throw new HttpError(401, 'Not allowed');
    const token = await higgsfieldAccess(env);
    if (!token) throw new HttpError(404, 'Higgsfield is not signed in');
    return json({access_token: token});
  }
  if (path === '/api/spend' || path === '/api/spend/report') {
    const auth = (request.headers.get('Authorization') || '').replace(/^Bearer\s+/, '');
    if (!(await samePassword(auth, await notifyKey(env)))) throw new HttpError(401, 'Not allowed');
    if (path === '/api/spend' && method === 'GET') return json(await readSpend(env));
    if (path === '/api/spend/report' && method === 'POST') return json(await addSpend(env, await readBody(request)));
    throw new HttpError(404, 'Not found');
  }
  if (!(await validSession(env, request.headers.get('Cookie')))) throw new HttpError(401, 'Please sign in');

  if (path === '/api/claude-usage' && method === 'GET') return json(await usageSnapshot(env));
  if (path === '/api/claude-usage/refresh' && method === 'POST') return json(await refreshUsage(env, url));

  if (path === '/api/session' && method === 'GET') return json({ok: true, repo: env.GITHUB_REPO, voices: VOICES});
  if (path === '/api/films' && method === 'POST') {
    const film = filmRequest(await readBody(request));
    const id = [...crypto.getRandomValues(new Uint8Array(4))].map((b) => b.toString(16).padStart(2, '0')).join('');
    await githubJson(env, 'POST', `/actions/workflows/${WORKFLOW}/dispatches`, {ref: env.GITHUB_BRANCH || 'main',
      inputs: {brief: film.brief, seconds: String(film.seconds), voice: film.voice, writer: film.writer,
        research: film.research, publish: film.publish ? 'true' : 'false', privacy: film.privacy, request_id: id,
        ...(film.instagram.length ? {instagram: film.instagram.join(',')} : {}), ...(film.youtube ? {youtube: film.youtube} : {})}});
    return json({requestId: id}, 202);
  }
  if (path === '/api/runs' && method === 'GET') return json({runs: await runs(env)});
  if (path === '/api/edits/upload' && method === 'POST') {
    return json(await uploadPiece(env, request, url.searchParams.get('id') || '', Number(url.searchParams.get('part'))));
  }
  if (path === '/api/edits' && method === 'POST') {
    const body = await readBody(request);
    return json(await startEdit(env, {id: body.id, parts: body.parts, name: body.name, options: body.options}), 202);
  }
  const editPlanPath = /^\/api\/films\/(\d{1,22})\/plan$/.exec(path);
  if (editPlanPath && method === 'GET') return json(await editPlan(env, editPlanPath[1]));
  const fixPath = /^\/api\/films\/(\d{1,22})\/fix$/.exec(path);
  if (fixPath && method === 'POST') return json(await fixShort(env, fixPath[1], await readBody(request)), 202);
  if (path === '/api/publish' && method === 'POST') {
    const {films, privacy, hashtags: tags, youtube, instagram, channel} = publishRequest(await readBody(request));
    const id = [...crypto.getRandomValues(new Uint8Array(4))].map((b) => b.toString(16).padStart(2, '0')).join('');
    let queued = 0;
    if (instagram.length) {
      // Instagram posts go into the page's own queue; the one-minute clock publishes them.
      queued = (await queueInstagram(env, films.map((f) => ({run: f.run, accounts: instagram,
        publishAt: f.publishAt ? Date.parse(f.publishAt) : 0, hashtags: f.hashtags})))).length;
      try { await instagramTick(env, Date.now(), {publish: false}); } catch (error) { console.error('instagram', error); }
    }
    if (youtube) {
      await githubJson(env, 'POST', '/actions/workflows/shorts-studio-publish.yml/dispatches', {ref: env.GITHUB_BRANCH || 'main',
        inputs: {films: JSON.stringify(films), privacy, hashtags: tags.join(' '), request_id: id, ...(channel ? {channel} : {})}});
    }
    return json({requestId: id, count: films.length, instagram: queued}, 202);
  }
  if (path === '/api/films/check' && method === 'POST') return json(await checkPublished(env));
  if (path === '/api/films' && method === 'GET') {
    const [{films}, posts, removed] = await Promise.all([library(env), instagramPosts(env), readRemoved(env)]);
    const gone = new Set(removed.youtube.map((x) => x.url));
    return json({films: films.map((f) => ({...publicFilm(gone.has(f.youtube) ? {...f, youtube: '', scheduledFor: ''} : f),
      instagram: posts.filter((p) => p.run === String(f.run) && p.state !== 'removed')
      .map((p) => ({username: p.username, state: p.state, permalink: p.permalink || '', publishAt: p.publishAt || 0, error: p.error || ''}))}))});
  }
  const media = /^\/api\/films\/(\d{1,22})\/(video|poster|caption-preview)$/.exec(path);
  if (media && (method === 'GET' || method === 'HEAD')) {
    const [, run, kind] = media;
    const draft = await release(env);
    const ext = kind === 'poster' ? 'png' : 'mp4';
    const name = `film-${run}${kind === 'caption-preview' ? '-preview' : ''}.${ext}`;
    const asset = draft && (draft.assets || []).find((a) => a.name === name);
    if (!asset) throw new HttpError(404, 'Film not found');
    const disposition = url.searchParams.get('download') ? 'attachment' : 'inline';
    return assetResponse(env, asset, request, {'Content-Type': kind === 'poster' ? 'image/png' : 'video/mp4',
      'Content-Disposition': `${disposition}; filename="shorts-studio-${run}.${ext}"`});
  }
  const remove = /^\/api\/films\/(\d{1,20})$/.exec(path);
  if (remove && method === 'DELETE') {
    await removeFilm(env, remove[1]);
    return json({ok: true});
  }
  if (path === '/api/autopilot' && method === 'GET') {
    const file = await githubJson(env, 'GET', `/contents/${SETTINGS}?ref=${encodeURIComponent(env.GITHUB_BRANCH || 'main')}`);
    const settings = JSON.parse(fromBase64(file.content));
    rememberAi(settings?.ai);
    return json({settings, sha: file.sha});
  }
  if (path === '/api/autopilot' && method === 'PUT') {
    const body = await readBody(request);
    const settings = autopilotSettings(body.settings || {});
    const saved = await githubJson(env, 'PUT', `/contents/${SETTINGS}`, {message: 'Update autopilot settings from the Shorts Studio page',
      content: toBase64(JSON.stringify(settings, null, 2) + '\n'), sha: str(body.sha, 64, 'Version'),
      branch: env.GITHUB_BRANCH || 'main'});
    rememberAi(settings.ai);
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
  if (path === '/api/events/clear' && method === 'POST') {
    // Empty the list; the last autopilot check stays so the health card keeps working.
    const data = await readEvents(env);
    await writeVariable(env, EVENTS, {events: [], lastCheck: data.lastCheck});
    return json({ok: true});
  }
  if (path === '/api/push/setup' && method === 'POST') return json(await setupNotify(env, url));
  if (path === '/api/push/key' && method === 'GET') return json({publicKey: (await pushState(env)).publicKey});
  if (path === '/api/push/subscribe' && method === 'POST') return json(await subscribe(env, url, await readBody(request)));
  if (path === '/api/push/unsubscribe' && method === 'POST') return json(await unsubscribe(env, await readBody(request)));
  if (path === '/api/push/test' && method === 'POST') {
    await recordEvent(env, {kind: 'test', title: 'Notifications are on', text: 'You will hear from Shorts Studio when films are ready or need attention.', url: '/#activity'}, true);
    return json({ok: true});
  }
  if (path === '/api/ideas' && method === 'GET') {
    const [ideas, views] = await Promise.all([readIdeas(env), readViews(env).catch(() => null)]);
    return json({...ideas, views});
  }
  if (path === '/api/ideas' && method === 'PUT') {
    const body = await readBody(request);
    const ideas = cleanIdeas(body.ideas);
    const saved = await githubJson(env, 'PUT', `/contents/${IDEAS}`, {message: 'Update topic ideas from the Shorts Studio page',
      content: toBase64(JSON.stringify(ideas, null, 1) + '\n'), ...(body.sha ? {sha: str(body.sha, 64, 'Version')} : {}),
      branch: env.GITHUB_BRANCH || 'main'});
    return json({ideas, sha: saved.content.sha});
  }
  if (path === '/api/insights' && method === 'GET') return json(await readInsights(env));
  if (path === '/api/insights/refresh' && method === 'POST') {
    const id = hex(4);
    await githubJson(env, 'POST', '/actions/workflows/shorts-studio-learn.yml/dispatches', {ref: env.GITHUB_BRANCH || 'main',
      inputs: {request_id: id}});
    return json({requestId: id}, 202);
  }
  if (path === '/api/ideas/refresh' && method === 'POST') {
    const id = hex(4);
    const sent = (request.headers.get('Content-Type') || '').includes('json') ? await readBody(request) : {};
    const topic = String(sent.topic || '');
    if (topic && !/^[a-z0-9]{1,12}$/.test(topic)) throw new HttpError(400, 'Invalid topic');
    await githubJson(env, 'POST', '/actions/workflows/shorts-studio-ideas.yml/dispatches', {ref: env.GITHUB_BRANCH || 'main',
      inputs: {request_id: id, ...(topic ? {topic} : {})}});
    return json({requestId: id}, 202);
  }
  if (path === '/api/account' && method === 'GET') return json(await account(env, url));
  if (path === '/api/higgsfield/connect' && method === 'POST') return higgsfieldConnect(env, url);
  if (path === '/api/higgsfield/balance' && method === 'GET') return json(await higgsfieldBalance(env));
  if (path === '/api/higgsfield/account' && method === 'DELETE') return json(await higgsfieldSignOut(env));
  const connector = /^\/api\/connectors\/(elevenlabs)$/.exec(path);
  if (connector && method === 'POST') return json(await saveConnectorKey(env, connector[1], await readBody(request)));
  if (connector && method === 'DELETE') {
    const response = await github(env, 'DELETE', `/actions/secrets/${CONNECTORS[connector[1]].secret}`);
    if (!response.ok && response.status !== 404) throw new HttpError(502, githubMessage(response.status));
    return json({ok: true});
  }
  if (path === '/api/youtube/connect' && method === 'POST') return youtubeConnect(env, url, await readBody(request));
  if (path === '/api/instagram/app' && method === 'POST') return json(await instagramSaveApp(env, url, await readBody(request)));
  if (path === '/api/instagram/connect' && method === 'POST') return instagramConnect(env, url);
  if (path === '/api/instagram/token' && method === 'POST') return json(await instagramAddToken(env, url, await readBody(request)));
  const igRemove = /^\/api\/instagram\/accounts\/(\d{5,25})$/.exec(path);
  if (igRemove && method === 'DELETE') {
    const vault = await igVault(env);
    await saveIgVault(env, {...vault, accounts: vault.accounts.filter((a) => a.id !== igRemove[1])});
    return json({ok: true});
  }
  if (path === '/api/youtube/disconnect' && method === 'POST') {
    await saveYtVault(env, {channels: [], default: ''});
    for (const name of ['YOUTUBE_OAUTH_JSON', 'YOUTUBE_ACCOUNTS_JSON']) {
      const response = await github(env, 'DELETE', `/actions/secrets/${name}`);
      if (!response.ok && response.status !== 404) throw new HttpError(502, githubMessage(response.status));
    }
    return json({ok: true});
  }
  const ytChannel = /^\/api\/youtube\/channels\/(UC[\w-]{22})(\/default)?$/.exec(path);
  if (ytChannel && ((method === 'DELETE' && !ytChannel[2]) || (method === 'POST' && ytChannel[2]))) {
    const vault = await ytVault(env);
    if (!vault.channels.some((c) => c.id === ytChannel[1])) throw new HttpError(404, 'That channel is not connected');
    const channels = ytChannel[2] ? vault.channels : vault.channels.filter((c) => c.id !== ytChannel[1]);
    const chosen = ytChannel[2] ? ytChannel[1] : (vault.default === ytChannel[1] ? channels[0]?.id || '' : vault.default);
    await syncYouTube(env, {channels, default: chosen});
    return json({ok: true});
  }
  const cancel = /^\/api\/runs\/(\d{1,20})\/cancel$/.exec(path);
  const resume = /^\/api\/runs\/(\d{1,20})\/continue$/.exec(path);
  if (resume && method === 'POST') {
    const run = await githubJson(env, 'GET', `/actions/runs/${resume[1]}`);
    if (run.path?.split('@')[0] === `.github/workflows/${EDIT_WORKFLOW}` && run.head_branch === (env.GITHUB_BRANCH || 'main') &&
        (!run.head_repository?.full_name || run.head_repository.full_name === env.GITHUB_REPO)) {
      // A failed edit starts again as a new run of the same upload (kept until an edit is saved). A GitHub
      // re-run would replay the old code, so a fix to the editor would never reach the retry.
      if (run.status !== 'completed' || !['failure', 'cancelled', 'timed_out'].includes(run.conclusion)) throw new HttpError(409, 'This edit is already running or finished');
      const upload = (/^Edit ([0-9a-f]{8})$/.exec(run.display_title || '') || [])[1];
      if (!upload) throw new HttpError(400, 'Choose a Shorts Studio edit');
      const draft = await uploadsRelease(env, false);
      const parts = draft ? (await uploadAssets(env, draft)).filter((a) => a.name.startsWith(`edit-${upload}-`)).length : 0;
      if (!parts) throw new HttpError(409, 'The uploaded video is no longer there. Upload it again.');
      const body = (request.headers.get('Content-Type') || '').includes('json') ? await readBody(request) : {};
      return json({...await startEdit(env, {id: upload, parts, name: body.name, options: body.options}), kind: 'edit'}, 202);
    }
    if (run.path?.split('@')[0] !== `.github/workflows/${WORKFLOW}` || run.head_branch !== (env.GITHUB_BRANCH || 'main') ||
        (run.head_repository?.full_name && run.head_repository.full_name !== env.GITHUB_REPO)) throw new HttpError(400, 'Choose a Shorts Studio film');
    if (run.status !== 'completed' || !['failure', 'cancelled', 'timed_out'].includes(run.conclusion)) throw new HttpError(409, 'This film is already running or finished');
    const {jobs = []} = await githubJson(env, 'GET', `/actions/runs/${resume[1]}/jobs?filter=all&per_page=100`);
    const deliver = [...jobs].sort((a, b) => b.id - a.id).find(j => j.name === 'deliver');
    if (deliver?.conclusion === 'success') throw new HttpError(409, 'This film has already been delivered');
    if (jobs.some(j => j.name === 'film' && j.conclusion === 'success') && deliver && ['failure', 'cancelled', 'timed_out'].includes(deliver.conclusion)) {
      // Retry delivery in the original run: its upload receipt prevents duplicates.
      const response = await github(env, 'POST', `/actions/jobs/${deliver.id}/rerun`);
      if (!response.ok) throw new HttpError(502, githubMessage(response.status));
      return json({runId: run.id}, 202);
    }
    const film = filmRequest(await readBody(request));
    const slot = /^Autopilot film (.+)$/.exec(run.display_title || '')?.[1] || '';
    const artifacts = await githubJson(env, 'GET', `/actions/runs/${resume[1]}/artifacts`);
    const saved = artifacts.artifacts?.find(a => a.name === `shorts-studio-film-${resume[1]}` && !a.expired);
    if (!saved && !film.brief && !slot) throw new HttpError(409, 'Saved files expired. Enter the original brief to try again.');
    const id = [...crypto.getRandomValues(new Uint8Array(4))].map(b => b.toString(16).padStart(2, '0')).join('');
    await githubJson(env, 'POST', `/actions/workflows/${WORKFLOW}/dispatches`, {ref: env.GITHUB_BRANCH || 'main',
      inputs: {resume_run: resume[1], request_id: id, brief: film.brief, seconds: String(film.seconds), voice: film.voice,
        writer: film.writer, research: film.research, publish: film.publish ? 'true' : 'false', privacy: film.privacy,
        instagram: film.instagram.join(','), ...(slot ? {slot} : {})}});
    return json({requestId: id}, 202);
  }
  if (cancel && method === 'POST') {
    const response = await github(env, 'POST', `/actions/runs/${cancel[1]}/cancel`);
    if (!response.ok && response.status !== 409) throw new HttpError(502, githubMessage(response.status));
    return json({ok: true});
  }
  if (path === '/api/claude-login' && method === 'POST') return json(await startLogin(env, url), 202);
  if (path === '/api/claude-login' && method === 'GET') return json(await loginStatus(env, url.searchParams.get('nonce') || ''));
  if (path === '/api/claude-login' && method === 'DELETE') return json(await cancelLogin(env, url.searchParams.get('nonce') || ''));
  if (path === '/api/claude-login/code' && method === 'POST') return json(await sendLoginCode(env, await readBody(request)));
  if (path === '/api/chatgpt-login' && method === 'POST') return json(await startLogin(env, url, 'chatgpt'), 202);
  if (path === '/api/chatgpt-login' && method === 'GET') return json(await loginStatus(env, url.searchParams.get('nonce') || '', 'chatgpt'));
  if (path === '/api/chatgpt-login' && method === 'DELETE') return json(await cancelLogin(env, url.searchParams.get('nonce') || '', 'chatgpt'));
  throw new HttpError(404, 'Not found');
}

// ---- Claude sign-in renewal (see cloud/claude_login.py) ----
// The runner and the page exchange the sign-in link and the one-time code through two
// repository variables. The new token itself never leaves the runner.
const LOGIN_STATE = 'STUDIO_LOGIN_STATE';
const LOGIN_CODE = 'STUDIO_LOGIN_CODE';
const LOGIN_WORKFLOW = 'shorts-studio-claude-login.yml';
// ChatGPT uses OpenAI's device sign-in (cloud/chatgpt_login.py): the page shows a link and a
// short code to type on OpenAI's page; nothing is pasted back.
const LOGINS = {
  claude: {workflow: LOGIN_WORKFLOW, title: 'Claude sign-in', name: 'Claude'},
  chatgpt: {workflow: 'shorts-studio-chatgpt-login.yml', title: 'ChatGPT sign-in', name: 'ChatGPT'}};
const NEEDS_PERMISSIONS = 'To sign in to Claude or ChatGPT from this page, give your GitHub token (STUDIO_GITHUB_TOKEN) the Variables and ' +
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
  const [response, savedAccount, savedChatGpt, ai] = await Promise.all([
    github(env, 'GET', '/actions/secrets/CLAUDE_CODE_OAUTH_TOKEN'),
    github(env, 'GET', '/actions/secrets/CLAUDE_ACCOUNT_OAUTH_JSON'),
    github(env, 'GET', '/actions/secrets/CHATGPT_ACCOUNT_JSON'), activeAi(env, true)]);
  let chatgpt = {connected: false, renewedAt: null};
  if (savedChatGpt.ok) chatgpt = {connected: true, renewedAt: (await savedChatGpt.json()).updated_at || null};
  else if (savedChatGpt.status === 403) chatgpt.permission = false;
  let claude = {connected: false, accountLogin: savedAccount.ok, renewedAt: null, expiresAt: null};
  const stored = savedAccount.ok ? savedAccount : response;
  if (stored.ok) {
    const {updated_at: updated} = await stored.json();
    claude = {...claude, connected: true, renewedAt: updated};
  } else if (stored.status === 403) {
    claude.permission = false;
  } else if (stored.status !== 404) {
    throw new HttpError(502, githubMessage(stored.status));
  }
  const youtube = await github(env, 'GET', '/actions/secrets/YOUTUBE_OAUTH_JSON');
  const client = await github(env, 'GET', `/actions/variables/${YT_CLIENT}`);
  let instagram = {app: false, accounts: [], redirectUri: url ? url.origin + IG_CALLBACK : ''};
  try { instagram = await instagramStatus(env, url); } catch (error) { instagram.error = igShort(error); }
  const [el, spend, plan] = await Promise.all([github(env, 'GET', '/actions/secrets/ELEVENLABS_API_KEY'),
    readSpend(env).catch(() => null), hfVault(env).catch(() => ({}))]);
  const connectors = {higgsfield: !!plan.refresh, elevenlabs: el.ok, spend};
  return {repo: env.GITHUB_REPO, branch: env.GITHUB_BRANCH || 'main', ai, claude, chatgpt, connectors,
    youtube: {connected: youtube.ok, clientSaved: client.ok, redirectUri: url ? url.origin + YT_CALLBACK : '',
      channels: youtube.ok ? await ytChannels(env).catch(() => []) : []}, instagram};
}

// ---- YouTube connection (Google OAuth, official upload scope) ----
// The Google client JSON is uploaded once and kept in a repository variable, encrypted with a key
// derived from the studio password. The resulting refresh token is sealed into the
// YOUTUBE_OAUTH_JSON Actions secret, which the deliver job uses to upload.
const YT_CLIENT = 'STUDIO_YOUTUBE_CLIENT';
const YT_CALLBACK = '/api/youtube/callback';
// Upload films, read public view data (most-viewed Shorts and their hashtags) for ideas, and read
// the channel's own analytics (watch time, viewers kept past the hook) so films learn what works.
const YT_ANALYTICS = 'https://www.googleapis.com/auth/yt-analytics.readonly';
const YT_SCOPE = 'https://www.googleapis.com/auth/youtube.upload https://www.googleapis.com/auth/youtube.readonly ' + YT_ANALYTICS;

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
    response_type: 'code', scope: YT_SCOPE, access_type: 'offline', prompt: 'select_account consent', include_granted_scopes: 'true', state}).toString();
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
  const credentials = {type: 'authorized_user', client_id: client.client_id, client_secret: client.client_secret,
    refresh_token: token.refresh_token, token_uri: 'https://oauth2.googleapis.com/token',
    scopes: String(token.scope || YT_SCOPE).split(' ').filter(Boolean)};
  // Which channel this sign-in is for (the read access lets us ask YouTube).
  const mine = await fetch('https://www.googleapis.com/youtube/v3/channels?part=snippet&mine=true',
    {headers: {Authorization: 'Bearer ' + token.access_token}});
  const item = mine.ok ? ((await mine.json()).items || [])[0] : null;
  if (!item || !/^UC[\w-]{22}$/.test(String(item.id))) return back(mine.ok ? 'nochannel' : 'failed');
  const channel = {id: item.id, title: String(item.snippet?.title || 'YouTube channel').slice(0, 80),
    handle: String(item.snippet?.customUrl || '').slice(0, 60), token: credentials, addedAt: Date.now()};
  const vault = await ytVault(env);
  if (!vault.channels.some((c) => c.id === channel.id) && vault.channels.length >= YT_MAX_CHANNELS) return back('toomany');
  const channels = [...vault.channels.filter((c) => c.id !== channel.id), channel];
  await syncYouTube(env, {channels, default: vault.default && channels.some((c) => c.id === vault.default) ? vault.default : channel.id});
  return back('connected');
}

// Connected channels live encrypted in a repository variable (so the page can list and change them);
// their sign-ins are copied into Actions secrets for the film jobs: YOUTUBE_ACCOUNTS_JSON (every
// channel, by id) and YOUTUBE_OAUTH_JSON (the default channel, also used for view data).
const YT_VAULT = 'STUDIO_YOUTUBE';
const YT_MAX_CHANNELS = 10;
async function ytVault(env) {
  const stored = await readVariable(env, YT_VAULT);
  const vault = (stored && await unlock(env, stored.box)) || {};
  return {channels: Array.isArray(vault.channels) ? vault.channels : [], default: String(vault.default || '')};
}
const saveYtVault = async (env, vault) => writeVariable(env, YT_VAULT, {box: await lock(env, vault)});
async function syncYouTube(env, vault) {
  await saveYtVault(env, vault);
  if (!vault.channels.length) return;
  const main = vault.channels.find((c) => c.id === vault.default) || vault.channels[0];
  await saveSecret(env, 'YOUTUBE_ACCOUNTS_JSON', JSON.stringify(Object.fromEntries(vault.channels.map((c) => [c.id, c.token]))));
  await saveSecret(env, 'YOUTUBE_OAUTH_JSON', JSON.stringify(main.token));
}
async function ytChannels(env) {
  const vault = await ytVault(env);
  return vault.channels.map((c) => ({id: c.id, title: c.title, handle: c.handle, default: c.id === (vault.default || vault.channels[0]?.id),
    // Watch-time data needs the analytics permission (channels signed in before it was asked for lack it).
    analytics: (Array.isArray(c.token?.scopes) ? c.token.scopes : []).includes(YT_ANALYTICS)}));
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

async function startLogin(env, url, provider = 'claude') {
  const flow = LOGINS[provider];
  await syncRunnerToken(env);
  await setupNotify(env, url);
  const nonce = hex(16);
  await variable(env, 'DELETE', LOGIN_CODE);
  await writeVariable(env, LOGIN_STATE, {nonce, provider, state: 'starting', message: `Starting the official ${flow.name} sign-in…`,
    updatedAt: Math.floor(Date.now() / 1000)});
  const response = await github(env, 'POST', `/actions/workflows/${flow.workflow}/dispatches`,
    {ref: env.GITHUB_BRANCH || 'main', inputs: {nonce}});
  if (!response.ok) throw new HttpError(502, githubMessage(response.status));
  return {nonce};
}

export function safeSignInUrl(value, provider = 'claude') {
  try {
    const url = new URL(value);
    if (url.protocol !== 'https:') return '';
    if (provider === 'chatgpt') return ['auth.openai.com', 'chatgpt.com', 'auth.chatgpt.com'].includes(url.hostname) ? url.href : '';
    return ['claude.com', 'claude.ai'].includes(url.hostname) && url.pathname.includes('oauth/authorize') ? url.href : '';
  } catch {
    return '';
  }
}

async function loginStatus(env, nonce, provider = 'claude') {
  if (!/^[0-9a-f]{32}$/.test(nonce)) throw new HttpError(400, 'Invalid sign-in request');
  const flow = LOGINS[provider];
  const state = await readVariable(env, LOGIN_STATE);
  if (!state || state.nonce !== nonce || state.state === 'starting') {
    // Until the job reports in, follow the GitHub run itself so a failure shows at once.
    const data = await githubJson(env, 'GET', `/actions/workflows/${flow.workflow}/runs?per_page=5`);
    const run = (data.workflow_runs || []).find((r) => r.display_title === `${flow.title} ${nonce}`);
    if (run && run.status === 'completed') {
      return {state: 'failed', url: '', details: run.html_url, blocked: quickFail(run),
        message: run.conclusion === 'cancelled' ? 'The sign-in was cancelled.'
          : quickFail(run) ? BLOCKED : `The sign-in job stopped before ${flow.name} answered. Try again.`};
    }
    return {state: 'starting', phase: !run || run.status !== 'in_progress' ? 'queued' : 'running',
      message: !run || run.status !== 'in_progress' ? 'Waiting for GitHub to start the sign-in…' : `Opening the official ${flow.name} sign-in…`};
  }
  const states = ['starting', 'link', 'checking', 'saving', 'done', 'failed'];
  const result = {state: states.includes(state.state) ? state.state : 'failed', message: String(state.message || '').slice(0, 300),
    url: state.state === 'link' ? safeSignInUrl(state.url, provider) : ''};
  if (provider === 'chatgpt') {
    // OpenAI's one-time device code, typed by the owner on OpenAI's page.
    result.userCode = state.state === 'link' && /^[A-Z0-9]{4,5}-[A-Z0-9]{4,6}$/.test(String(state.userCode || '')) ? state.userCode : '';
  }
  return result;
}

async function cancelLogin(env, nonce, provider = 'claude') {
  if (!/^[0-9a-f]{32}$/.test(nonce)) throw new HttpError(400, 'Invalid sign-in request');
  const flow = LOGINS[provider];
  const data = await githubJson(env, 'GET', `/actions/workflows/${flow.workflow}/runs?per_page=5`);
  const run = (data.workflow_runs || []).find((r) => r.display_title === `${flow.title} ${nonce}` && r.status !== 'completed');
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

// ---- Instagram (official Instagram API with Instagram Login) ----
// Reels go to Instagram professional (Creator or Business) accounts. The owner's Meta app ID and
// secret and each account's 60-day token live in one encrypted repository variable; the clock
// renews tokens before they expire. Meta downloads each film from a signed link that works for two
// hours, so nothing in the library becomes public. Posts wait in a queue that the one-minute clock
// works through: create the Reel container shortly before the post time, wait until Instagram has
// processed it, then publish at the post time.
const IG_VAULT = 'STUDIO_INSTAGRAM';
const IG_QUEUE = 'STUDIO_IG_QUEUE';
const IG_CALLBACK = '/api/instagram/callback';
const IG_GRAPH = 'https://graph.instagram.com/v24.0';
// Apps made with Meta's newer "Unified login" get tokens for graph.facebook.com instead.
const FB_GRAPH = 'https://graph.facebook.com/v24.0';
const graphFor = (account) => account.host === 'facebook' ? FB_GRAPH : IG_GRAPH;
const IG_SCOPE = 'instagram_business_basic,instagram_business_content_publish';
const IG_MAX_ACCOUNTS = 10;
const IG_EARLY = 15 * 60000;          // start processing this long before the post time
const IG_LINK_SECONDS = 2 * 3600;     // how long Meta may download the film

async function igVault(env) {
  const stored = await readVariable(env, IG_VAULT);
  const vault = (stored && await unlock(env, stored.box)) || {};
  return {app: vault.app || null, accounts: Array.isArray(vault.accounts) ? vault.accounts : [], origin: vault.origin || '',
    checkedAt: vault.checkedAt || 0};
}
const saveIgVault = async (env, vault) => writeVariable(env, IG_VAULT, {box: await lock(env, vault)});

export function instagramApp(body) {
  const id = String(body.appId ?? '').trim(), secret = String(body.appSecret ?? '').trim();
  if (!/^\d{6,25}$/.test(id)) throw new HttpError(400, 'Paste the Instagram app ID (numbers only) from Meta for Developers.');
  if (!/^[0-9a-f]{32}$/i.test(secret)) throw new HttpError(400, 'Paste the Instagram app secret (32 letters and numbers).');
  return {id, secret};
}

export function instagramIds(value) {
  const list = Array.isArray(value) ? value.map((x) => String(x)) : [];
  if (list.length > IG_MAX_ACCOUNTS || list.some((x) => !/^\d{5,25}$/.test(x))) throw new HttpError(400, 'Invalid Instagram account choice');
  return [...new Set(list)];
}

async function igFetch(url, {params = {}, method = 'GET'} = {}) {
  const target = new URL(url);
  const init = {method, headers: {'User-Agent': 'ShortsStudio'}};
  // Keep a query the address already has (Meta's "next page" links carry one).
  if (method === 'GET') for (const [k, v] of Object.entries(params)) target.searchParams.set(k, v);
  else init.body = new URLSearchParams(params);
  const response = await fetch(target.href, init);
  let data = {};
  try { data = await response.json(); } catch {}
  if (!response.ok || data.error) {
    const message = data.error?.error_user_msg || data.error?.message || data.error_message || `Instagram answered ${response.status}`;
    throw new HttpError(502, 'Instagram: ' + String(message).slice(0, 240));
  }
  return data;
}

async function mediaKey(env) {
  const seed = await crypto.subtle.digest('SHA-256', encoder.encode('shorts-studio-media:' + env.STUDIO_PASSWORD + ':' + env.GITHUB_REPO));
  return crypto.subtle.importKey('raw', seed, {name: 'HMAC', hash: 'SHA-256'}, false, ['sign', 'verify']);
}
export async function filmLink(env, origin, run, now = Date.now()) {
  const exp = Math.floor(now / 1000) + IG_LINK_SECONDS;
  const sig = b64url(await crypto.subtle.sign('HMAC', await mediaKey(env), encoder.encode(`${run}.${exp}`)));
  return `${origin}/api/public/films/${run}/${exp}/${sig}/film.mp4`;
}
export async function validFilmLink(env, run, exp, sig, now = Date.now()) {
  if (Number(exp) < now / 1000 || Number(exp) > now / 1000 + IG_LINK_SECONDS + 60) return false;
  try {
    return await crypto.subtle.verify('HMAC', await mediaKey(env), fromB64url(sig), encoder.encode(`${run}.${exp}`));
  } catch {
    return false;
  }
}

async function instagramSaveApp(env, url, body) {
  const app = instagramApp(body);
  const vault = await igVault(env);
  await saveIgVault(env, {...vault, app, origin: url.origin});
  return {ok: true};
}

async function instagramConnect(env, url) {
  const vault = await igVault(env);
  if (!vault.app) throw new HttpError(400, 'Save your Instagram app ID and secret first.');
  if (vault.accounts.length >= IG_MAX_ACCOUNTS) throw new HttpError(400, `You can connect up to ${IG_MAX_ACCOUNTS} Instagram accounts.`);
  if (vault.origin !== url.origin) await saveIgVault(env, {...vault, origin: url.origin});
  const nonce = hex(16);
  const state = await lock(env, {nonce, exp: Date.now() + 15 * 60000});
  const consent = new URL('https://www.instagram.com/oauth/authorize');
  // force_reauth lets you choose a different Instagram account each time.
  consent.search = new URLSearchParams({client_id: vault.app.id, redirect_uri: url.origin + IG_CALLBACK, response_type: 'code',
    scope: IG_SCOPE, state, force_reauth: 'true', enable_fb_login: '0'}).toString();
  return json({url: consent.href}, 200, {'Set-Cookie': `ign=${nonce}; Path=${IG_CALLBACK}; Max-Age=900; HttpOnly; Secure; SameSite=Lax`});
}

async function instagramCallback(request, env, url) {
  const back = (result) => new Response(null, {status: 302, headers: {Location: `/?instagram=${result}#account`,
    'Set-Cookie': `ign=; Path=${IG_CALLBACK}; Max-Age=0; HttpOnly; Secure; SameSite=Lax`}});
  if (url.searchParams.get('error')) return back('cancelled');
  const state = await unlock(env, url.searchParams.get('state') || '');
  const cookie = /(?:^|;\s*)ign=([0-9a-f]{32})/.exec(request.headers.get('Cookie') || '');
  if (!state || state.exp < Date.now() || !cookie || cookie[1] !== state.nonce) return back('expired');
  const code = (url.searchParams.get('code') || '').replace(/#_$/, '');
  const vault = await igVault(env);
  if (!vault.app || !code || code.length > 2048) return back('failed');
  try {
    let short = await igFetch('https://api.instagram.com/oauth/access_token', {method: 'POST', params: {client_id: vault.app.id,
      client_secret: vault.app.secret, grant_type: 'authorization_code', redirect_uri: url.origin + IG_CALLBACK, code}});
    if (Array.isArray(short.data)) short = short.data[0] || {};
    if (!short.access_token) return back('failed');
    const long = await igFetch('https://graph.instagram.com/access_token', {params: {grant_type: 'ig_exchange_token',
      client_secret: vault.app.secret, access_token: short.access_token}});
    const me = await igFetch(IG_GRAPH + '/me', {params: {fields: 'user_id,username,account_type', access_token: long.access_token}});
    if (!['BUSINESS', 'MEDIA_CREATOR'].includes(me.account_type)) return back('notpro');
    const id = String(me.user_id || me.id), now = Date.now();
    const account = {id, username: String(me.username || id).slice(0, 60), token: long.access_token,
      expiresAt: now + (Number(long.expires_in) || 5184000) * 1000, refreshedAt: now, addedAt: now};
    const accounts = [...vault.accounts.filter((a) => a.id !== id), account].slice(-IG_MAX_ACCOUNTS);
    await saveIgVault(env, {...vault, accounts, origin: url.origin});
    return back('connected');
  } catch (error) {
    console.error('instagram connect', error);
    return back('failed');
  }
}

// The simplest way in: paste the token Meta's dashboard generates (Use cases → Customize → API setup
// → Generate access tokens). Both token kinds work: Instagram-login tokens (graph.instagram.com, 60
// days, renewed by the clock) and Unified-login tokens (graph.facebook.com, which do not expire).
export async function instagramAddToken(env, url, body) {
  const token = String(body.token ?? '').trim();
  const hint = String(body.accountId ?? '').trim();
  if (!/^[A-Za-z0-9_\-.|]{30,2000}$/.test(token)) throw new HttpError(400, 'Paste the whole access token from Meta (it starts with IG or EAA).');
  if (hint && !/^\d{5,25}$/.test(hint)) throw new HttpError(400, 'The Instagram account ID has only numbers.');
  const vault = await igVault(env);
  let account = null, problem = null;
  try {
    const me = await igFetch(IG_GRAPH + '/me', {params: {fields: 'user_id,username,account_type', access_token: token}});
    if (me.account_type && !['BUSINESS', 'MEDIA_CREATOR'].includes(me.account_type)) {
      throw new HttpError(400, 'That is a personal account. Switch it to a Professional (Creator) account in Instagram, then try again.');
    }
    account = {id: String(me.user_id || me.id), username: me.username, host: 'instagram'};
  } catch (error) {
    if (error instanceof HttpError && error.status === 400) throw error;
    problem = error;
  }
  // Unified-login tokens answer on graph.facebook.com: with the account ID, or by finding the account.
  const tries = hint ? [[`${FB_GRAPH}/${hint}`, 'id,username', (me) => me.id]] : [
    [`${FB_GRAPH}/me`, 'user_id,username', (me) => me.user_id],
    [`${FB_GRAPH}/me/accounts`, 'instagram_business_account{id,username}', (me) => {
      const found = (me.data || []).map((p) => p.instagram_business_account).find(Boolean);
      if (found) me.username = found.username;
      return found && found.id;
    }]];
  for (const [address, fields, pickId] of tries) {
    if (account) break;
    try {
      const me = await igFetch(address, {params: {fields, access_token: token}});
      const id = String(pickId(me) || '');
      if (/^\d{5,25}$/.test(id) && me.username) account = {id, username: me.username, host: 'facebook'};
    } catch (error) {
      problem = error;
    }
  }
  if (!account) {
    throw new HttpError(400, hint ? 'Meta did not accept this token for that account. ' + igShort(problem)
      : 'Paste the Instagram account ID too: it is the number shown next to the account in step 2 (Generate access tokens). ' +
        (problem ? '(' + igShort(problem) + ')' : ''));
  }
  if (vault.accounts.length >= IG_MAX_ACCOUNTS && !vault.accounts.some((a) => a.id === account.id)) {
    throw new HttpError(400, `You can connect up to ${IG_MAX_ACCOUNTS} Instagram accounts.`);
  }
  const now = Date.now();
  const saved = {...account, username: String(account.username).slice(0, 60), token,
    expiresAt: account.host === 'instagram' ? now + 60 * 86400000 : 0, refreshedAt: now, addedAt: now};
  await saveIgVault(env, {...vault, origin: url.origin, accounts: [...vault.accounts.filter((a) => a.id !== account.id), saved]});
  return {ok: true, username: saved.username};
}

async function instagramStatus(env, url) {
  const vault = await igVault(env);
  return {app: !!vault.app, appId: vault.app ? vault.app.id : '', redirectUri: url ? url.origin + IG_CALLBACK : '',
    accounts: vault.accounts.map((a) => ({id: a.id, username: a.username, expiresAt: a.expiresAt ? new Date(a.expiresAt).toISOString() : null}))};
}

async function readQueue(env) {
  const data = await readVariable(env, IG_QUEUE);
  return {posts: Array.isArray(data?.posts) ? data.posts : []};
}
async function writeQueue(env, queue) {
  const active = queue.posts.filter((p) => p.state === 'waiting' || p.state === 'processing');
  let done = queue.posts.filter((p) => !active.includes(p)).sort((a, b) => (b.doneAt || 0) - (a.doneAt || 0)).slice(0, 60);
  while (JSON.stringify({posts: [...active, ...done]}).length > 40000 && done.length) done = done.slice(0, -5);
  await writeVariable(env, IG_QUEUE, {posts: [...active, ...done]});
}

// Hashtags people browse on Instagram, added after the film's own topic, subject and discovery ones.
const IG_REACH = {en: ['#reels', '#reelsinstagram', '#explore', '#explorepage'], ar: ['#ريلز', '#اكسبلور', '#reels', '#explore']};
export function instagramCaption(film, ownerTags = []) {
  const pack = film.youtubePack || {};
  const title = String(pack.title || film.title || '').trim();
  const text = String(pack.description || film.description || '').trim();
  const seen = new Set(), tags = [];
  const language = String(film.language || 'en').slice(0, 2) === 'ar' ? 'ar' : 'en';
  for (const tag of [...ownerTags, ...(Array.isArray(film.hashtags) ? film.hashtags : []), ...IG_REACH[language]]) {
    const word = String(tag).replace(/^#+/, '').replace(/[^\p{L}\p{N}_]/gu, '');
    if (word && word.toLowerCase() !== 'shorts' && !seen.has(word.toLowerCase())) { seen.add(word.toLowerCase()); tags.push('#' + word); }
  }
  return [title, text, tags.slice(0, 20).join(' ')].filter(Boolean).join('\n\n').slice(0, 2200);
}

// Add Reels to the queue: one post per film and account. publishAt is a time in ms (0 = now).
async function queueInstagram(env, items) {
  const vault = await igVault(env);
  if (!vault.accounts.length) throw new HttpError(400, 'Connect an Instagram account first.');
  const {films} = await library(env);
  let ownerTags = [];
  try { ownerTags = (await repoJson(env, SETTINGS, {})).hashtags || []; } catch {}
  const queue = await readQueue(env);
  const added = [];
  for (const {run, accounts, publishAt, hashtags: tags} of items) {
    const film = films.find((f) => String(f.run) === String(run));
    if (!film) throw new HttpError(404, 'Film not found');
    for (const id of accounts) {
      const account = vault.accounts.find((a) => a.id === id);
      if (!account) throw new HttpError(400, 'That Instagram account is not connected any more.');
      if (queue.posts.some((p) => p.run === String(run) && p.account === id && ['waiting', 'processing', 'posted'].includes(p.state))) continue;
      const post = {id: hex(6), run: String(run), account: id, username: account.username, title: film.title || 'Your film',
        caption: instagramCaption(tags ? {...film, hashtags: tags} : film, tags ? [] : ownerTags),
        publishAt: publishAt || Date.now(), state: 'waiting', tries: 0, createdAt: Date.now()};
      queue.posts.push(post); added.push(post);
    }
  }
  if (added.length) await writeQueue(env, queue);
  return added;
}

const igShort = (error) => (error instanceof HttpError ? error.message : String(error?.message || error)).slice(0, 240);

// One pass over the queue. publish=false only prepares Reels (used right after queueing).
export async function instagramTick(env, now = Date.now(), {publish = true} = {}) {
  const queue = await readQueue(env);
  const active = queue.posts.filter((p) => p.state === 'waiting' || p.state === 'processing');
  if (!active.length) return {state: 'idle'};
  const vault = await igVault(env);
  const events = [];
  let changed = false;
  const fail = (post, message) => {
    Object.assign(post, {state: 'failed', error: message, doneAt: now}); changed = true;
    events.push({kind: 'failed', title: `Not posted on Instagram: ${post.title}`, text: `@${post.username}: ${message}`});
  };
  for (const post of active) {
    const account = vault.accounts.find((a) => a.id === post.account);
    if (!account) { fail(post, 'This Instagram account was removed.'); continue; }
    try {
      if (post.state === 'waiting') {
        if (post.publishAt - now > IG_EARLY || !vault.origin) continue;
        const made = await igFetch(`${graphFor(account)}/${account.id}/media`, {method: 'POST', params: {media_type: 'REELS',
          video_url: await filmLink(env, vault.origin, post.run, now), caption: post.caption, share_to_feed: 'true', access_token: account.token}});
        Object.assign(post, {state: 'processing', container: String(made.id), containerAt: now, error: ''}); changed = true;
        continue;
      }
      const status = await igFetch(`${graphFor(account)}/${post.container}`, {params: {fields: 'status_code,status', access_token: account.token}});
      if (status.status_code === 'FINISHED') {
        if (!publish || now < post.publishAt) continue;
        const done = await igFetch(`${graphFor(account)}/${account.id}/media_publish`, {method: 'POST',
          params: {creation_id: post.container, access_token: account.token}});
        let permalink = '';
        try { permalink = (await igFetch(`${graphFor(account)}/${done.id}`, {params: {fields: 'permalink', access_token: account.token}})).permalink || ''; } catch {}
        Object.assign(post, {state: 'posted', media: String(done.id), permalink, doneAt: now}); changed = true;
        events.push({kind: 'posted', title: `Posted on Instagram: ${post.title}`, text: `As a Reel on @${post.username}.`});
      } else if (['ERROR', 'EXPIRED'].includes(status.status_code) || now - post.containerAt > 60 * 60000) {
        post.tries = (post.tries || 0) + 1; changed = true;
        if (post.tries >= 2) fail(post, String(status.status || 'Instagram could not process the video.').slice(0, 200));
        else Object.assign(post, {state: 'waiting', container: '', error: String(status.status || '').slice(0, 200)});
      }
    } catch (error) {
      post.tries = (post.tries || 0) + 1; post.error = igShort(error); changed = true;
      if (post.tries >= 3) fail(post, post.error);
    }
  }
  if (changed) await writeQueue(env, queue);
  for (const event of events) {
    try { await recordEvent(env, {id: hex(6), t: Math.floor(now / 1000), url: '/#library', ...event}, true); } catch {}
  }
  return {state: 'worked', active: active.length, events: events.length};
}

// Keep each account's 60-day token fresh (Instagram allows a refresh once a token is a day old).
export async function instagramMaintain(env, now = Date.now()) {
  const vault = await igVault(env);
  if (!vault.accounts.length || now - vault.checkedAt < 12 * 3600000) return {state: 'skipped'};
  let refreshed = 0;
  for (const account of vault.accounts) {
    // Unified-login tokens do not expire; Instagram-login tokens are renewed while still valid.
    if (account.host === 'facebook' || !account.expiresAt || now - account.refreshedAt < 5 * 86400000 || account.expiresAt < now) continue;
    try {
      const fresh = await igFetch('https://graph.instagram.com/refresh_access_token', {params: {grant_type: 'ig_refresh_token', access_token: account.token}});
      Object.assign(account, {token: fresh.access_token, expiresAt: now + (Number(fresh.expires_in) || 5184000) * 1000, refreshedAt: now});
      refreshed++;
    } catch (error) {
      console.error('instagram refresh', account.username, igShort(error));
    }
  }
  await saveIgVault(env, {...vault, checkedAt: now});
  return {state: 'checked', refreshed};
}

async function instagramPosts(env) {
  try {
    return (await readQueue(env)).posts;
  } catch {
    return [];
  }
}

// ---- Keeping "published" true ----
// If you delete a Short on YouTube or a Reel on Instagram, the app stops calling it published and
// lets you post it again. YouTube is asked through its public oEmbed address (a deleted video
// answers 404; a private or scheduled one answers 401, so it still counts as uploaded); Instagram
// is asked for the Reel itself. Deleted YouTube links are listed in STUDIO_REMOVED, and their upload
// record is cleared so the publish job may upload the film again.
const REMOVED = 'STUDIO_REMOVED';
const youtubeId = (url) => (/(?:shorts\/|watch\?v=|youtu\.be\/)([A-Za-z0-9_-]{6,20})/.exec(String(url || '')) || [])[1] || '';

async function readRemoved(env) {
  try {
    const data = await readVariable(env, REMOVED);
    return {youtube: Array.isArray(data?.youtube) ? data.youtube : [], cursor: Number(data?.cursor) || 0};
  } catch {
    return {youtube: [], cursor: 0};
  }
}

async function youtubeGone(id) {
  const response = await fetch('https://www.youtube.com/oembed?format=json&url=' + encodeURIComponent('https://www.youtube.com/watch?v=' + id),
    {headers: {'User-Agent': 'ShortsStudio'}});
  return response.status === 404 || response.status === 400;
}

export async function checkPublished(env, now = Date.now(), {batch = 15} = {}) {
  const changes = [];
  // Instagram: a deleted Reel is no longer in the account's list of posts. (Asking for the Reel itself
  // is not enough: Instagram keeps deleted posts in "Recently deleted" for 30 days.) If the list
  // cannot be read, the Reel itself is asked for: error code 100 means it does not exist.
  try {
    const queue = await readQueue(env);
    const posted = queue.posts.filter((p) => p.state === 'posted' && p.media).sort((a, b) => (b.doneAt || 0) - (a.doneAt || 0)).slice(0, 30);
    if (posted.length) {
      const vault = await igVault(env);
      const lists = {};
      for (const id of new Set(posted.map((p) => p.account))) {
        const account = vault.accounts.find((a) => a.id === id);
        if (!account) continue;
        try {
          const ids = new Set();
          let next = `${graphFor(account)}/${account.id}/media?` + new URLSearchParams({fields: 'id,timestamp', limit: '100', access_token: account.token});
          let oldest = Infinity, complete = false;
          for (let page = 0; page < 2 && next; page++) {
            const data = await igFetch(next);
            for (const item of data.data || []) {
              ids.add(String(item.id));
              const when = Date.parse(item.timestamp || '');
              if (Number.isFinite(when)) oldest = Math.min(oldest, when);
            }
            next = data.paging?.next || '';
            complete = !next;
          }
          lists[id] = {ids, oldest, complete, account};
        } catch {
          lists[id] = {account};
        }
      }
      for (const post of posted) {
        const list = lists[post.account];
        if (!list) continue;
        let gone = false;
        if (list.ids) {
          // Only judge Reels the list reaches back to.
          gone = !list.ids.has(String(post.media)) && (list.complete || (post.doneAt || 0) >= list.oldest);
        } else {
          const response = await fetch(`${graphFor(list.account)}/${post.media}?` + new URLSearchParams({fields: 'id', access_token: list.account.token}));
          let data = {};
          try { data = await response.json(); } catch {}
          gone = !response.ok && data.error && Number(data.error.code) === 100;
        }
        if (gone) {
          Object.assign(post, {state: 'removed', removedAt: now});
          changes.push({title: `Deleted on Instagram: ${post.title}`, text: `@${post.username}. You can post it again from the Library.`});
        }
      }
      if (changes.length) await writeQueue(env, queue);
    }
  } catch (error) {
    console.error('instagram check', error);
  }
  // YouTube: check the newest films, then a few older ones each time in turn.
  try {
    const removed = await readRemoved(env);
    const gone = new Set(removed.youtube.map((x) => x.url));
    const {films} = await library(env);
    const live = films.filter((f) => f.youtube && !gone.has(f.youtube)).sort((a, b) => (b.createdAt || 0) - (a.createdAt || 0));
    const older = live.slice(5);
    const start = older.length ? removed.cursor % older.length : 0;
    const pick = [...live.slice(0, 5), ...older.slice(start, start + batch - 5), ...older.slice(0, Math.max(0, start + batch - 5 - older.length))];
    let found = 0;
    for (const film of [...new Set(pick)]) {
      const id = youtubeId(film.youtube);
      if (!id) continue;
      try {
        if (!(await youtubeGone(id))) continue;
      } catch {
        continue;
      }
      found++;
      removed.youtube = [{url: film.youtube, run: String(film.run), at: now}, ...removed.youtube].slice(0, 200);
      changes.push({title: `Deleted on YouTube: ${film.title || 'Your film'}`, text: 'You can publish it again from the Library.'});
      // Clear the upload record so the film may be uploaded again.
      // The record is named after the film's job (or, for older films, its run number).
      const job = /^[0-9a-f]{32}$/.test(String(film.jobId || '')) ? film.jobId
        : [...new Uint8Array(await crypto.subtle.digest('SHA-256', encoder.encode('film:' + film.run)))].map((b) => b.toString(16).padStart(2, '0')).join('').slice(0, 32);
      {
        const path = `/contents/shorts-studio/ledger/${job}.json`;
        const file = await github(env, 'GET', path + '?ref=' + encodeURIComponent(env.GITHUB_BRANCH || 'main'));
        if (file.ok) {
          const {sha} = await file.json();
          await github(env, 'DELETE', path, {message: 'Shorts Studio: the YouTube upload was deleted', sha, branch: env.GITHUB_BRANCH || 'main'});
        }
      }
    }
    removed.cursor = (removed.cursor + Math.max(0, batch - 5)) % Math.max(1, older.length);
    await writeVariable(env, REMOVED, removed);
    if (found || changes.length) {
      for (const change of changes) {
        try { await recordEvent(env, {id: hex(6), t: Math.floor(now / 1000), kind: 'info', url: '/#library', ...change}, false); } catch {}
      }
    }
  } catch (error) {
    console.error('youtube check', error);
  }
  return {removed: changes.length};
}

export default {
  // One clock, every minute: Instagram posts each minute, autopilot every 5 minutes.
  async scheduled(event, env, ctx) {
    if (!env.STUDIO_PASSWORD || !env.STUDIO_GITHUB_TOKEN || !env.GITHUB_REPO) return;
    const now = new Date(event.scheduledTime || Date.now());
    ctx.waitUntil((async () => {
      if (now.getUTCMinutes() % 5 === 0) await autopilotTick(env, now).catch((error) => console.error('autopilot', error));
      await instagramTick(env, now.getTime()).catch((error) => console.error('instagram', error));
      if (now.getUTCMinutes() === 17) await instagramMaintain(env, now.getTime()).catch((error) => console.error('instagram tokens', error));
      if (now.getUTCMinutes() === 41) await checkPublished(env, now.getTime()).catch((error) => console.error('published check', error));
    })());
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
