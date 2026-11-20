"""Learn from the channel's own results, so the next films get more views.

1. collect(): every published film in the library with its numbers. YouTube Analytics (with the
   yt-analytics.readonly permission) adds watch time, how much of each film people watched, how
   many were still watching after the first seconds, shares and subscribers gained. Without that
   permission only views, likes and comments are read.
2. Claude compares the films that did well with the ones that did not and writes lessons: what to
   do more, what to stop, which topics and hooks work, the best length and posting times.
3. The lessons are saved to shorts-studio/autopilot/lessons.json. Every new film, autopilot topic
   choice and idea list reads them (lessons_block()), so the channel keeps learning.

Runs weekly and from the page ("Analyze now"). Numbers are never invented: films without data are
left out, and lessons from fewer than five films are marked as early signs.
"""
import asyncio
import json
import os
import re
import shutil
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

LESSONS = 'shorts-studio/autopilot/lessons.json'
SETTINGS = 'shorts-studio/autopilot/settings.json'
REPORTS = 'https://youtubeanalytics.googleapis.com/v2/reports'
METRICS = ('views', 'estimatedMinutesWatched', 'averageViewDuration', 'averageViewPercentage', 'likes', 'shares',
           'subscribersGained', 'comments')
MAX_FILMS = 40        # newest published films analyzed
RETENTION_FILMS = 25  # films that also get a watch curve (one request each)
VIDEO_ID = re.compile(r'(?:shorts/|v=|youtu\.be/)([\w-]{11})')


class AnalyticsUnavailable(RuntimeError):
    """Watch-time data cannot be read; views, likes and comments still can."""


def video_id(url):
    match = VIDEO_ID.search(str(url or ''))
    return match.group(1) if match else ''


def report(token, params, opener=urlopen):
    request = Request(REPORTS + '?' + urlencode(params), headers={'Authorization': 'Bearer ' + token})
    try:
        with opener(request, timeout=30) as response:
            data = json.loads(response.read().decode())
    except HTTPError as error:
        body = (error.read() or b'').decode(errors='replace').lower()
        if error.code == 403 and ('accessnotconfigured' in body or 'has not been used' in body or 'disabled' in body):
            raise AnalyticsUnavailable('Turn on the "YouTube Analytics API" in your Google Cloud project, '
                                       'then tap Analyze now again.') from None
        if error.code in (401, 403):
            raise AnalyticsUnavailable('For watch-time data, tap Allow next to your channel in Account → YouTube.') from None
        raise
    names = [c.get('name') for c in data.get('columnHeaders', [])]
    return [dict(zip(names, row)) for row in data.get('rows') or []]


def video_metrics(token, ids, today, opener=urlopen):
    """{video id: totals} for these videos since they were posted."""
    out = {}
    for start in range(0, len(ids), 200):
        rows = report(token, {'ids': 'channel==MINE', 'startDate': '2020-01-01', 'endDate': today,
                              'metrics': ','.join(METRICS), 'dimensions': 'video',
                              'filters': 'video==' + ','.join(ids[start:start + 200]), 'maxResults': 200}, opener)
        for row in rows:
            out[row.get('video')] = {k: row.get(k) for k in METRICS}
    return out


def retention(token, vid, seconds, today, opener=urlopen):
    """How many were still watching after the hook (about 3 s), at the middle and at the end.
    Values are the share of viewers (above 1 when people replay). relative: compared with
    similar videos on YouTube (0.5 = typical, higher is better)."""
    rows = report(token, {'ids': 'channel==MINE', 'startDate': '2020-01-01', 'endDate': today,
                          'metrics': 'audienceWatchRatio,relativeRetentionPerformance',
                          'dimensions': 'elapsedVideoTimeRatio', 'filters': 'video==' + vid}, opener)
    curve = sorted((float(r.get('elapsedVideoTimeRatio') or 0), float(r.get('audienceWatchRatio') or 0),
                    float(r.get('relativeRetentionPerformance') or 0)) for r in rows)
    if not curve:
        return None

    def at(ratio):
        return round(min(curve, key=lambda p: abs(p[0] - ratio))[1], 3)
    hook = min(1.0, 3 / seconds) if seconds else 0.1
    return {'afterHook': at(hook), 'middle': at(0.5), 'end': at(1.0),
            'relative': round(sum(p[2] for p in curve) / len(curve), 3)}


def sign_ins(source):
    """{channel id or '' for the default channel: sign-in}."""
    out = {}
    raw = (source.get('YOUTUBE_OAUTH_JSON') or '').strip()
    if raw:
        try:
            out[''] = json.loads(raw)
        except ValueError:
            pass
    try:
        accounts = json.loads(source.get('YOUTUBE_ACCOUNTS_JSON') or '{}')
    except ValueError:
        accounts = {}
    for channel, value in (accounts.items() if isinstance(accounts, dict) else []):
        if isinstance(value, dict) and value.get('refresh_token'):
            out[channel] = value
    return out


def posted_local(published, zone):
    try:
        from zoneinfo import ZoneInfo
        moment = datetime.fromisoformat(published.replace('Z', '+00:00')).astimezone(ZoneInfo(zone or 'UTC'))
    except (ValueError, TypeError, KeyError, ImportError):
        return '', ''
    return moment.strftime('%A'), moment.strftime('%H:00')


def collect(films, source, zone='UTC', opener=urlopen, now=None):
    """(rows, problem): the newest published films with their numbers, newest first."""
    from trends import NeedsReconnect, access_token, details
    now = now or datetime.now(timezone.utc)
    today = now.date().isoformat()
    published = [f for f in films if isinstance(f, dict) and video_id(f.get('youtube'))][:MAX_FILMS]
    if not published:
        return [], 'No films have been published to YouTube yet.'
    accounts = sign_ins(source)
    if not accounts:
        return [], 'Connect YouTube on the page to read your films’ numbers.'
    groups = {}
    for film in published:
        channel = film.get('channel') or ''
        groups.setdefault(channel if channel in accounts else '', []).append(film)
    rows, problems, curves = [], [], 0
    for channel, group in groups.items():
        if channel not in accounts:
            problems.append('Some films are on a channel that is no longer connected.')
            continue
        try:
            token = access_token(accounts[channel], opener)
        except NeedsReconnect as error:
            problems.append(str(error))
            continue
        ids = [video_id(f['youtube']) for f in group]
        public = {v['id']: v for v in details(ids, token, opener)}
        try:
            deep = video_metrics(token, ids, today, opener)
        except AnalyticsUnavailable as error:
            deep = None
            problems.append(str(error))
        for film in group:
            vid = video_id(film['youtube'])
            info = public.get(vid)
            if not info:
                continue  # removed or private on YouTube
            seconds = info.get('seconds') or film.get('seconds') or 0
            when = film.get('scheduledFor') or info.get('publishedAt') or info.get('published') or ''
            try:
                moment = datetime.fromisoformat(str(when).replace('Z', '+00:00'))
                moment = moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)
                age = max(0.5, (now - moment).total_seconds() / 86400)
            except ValueError:
                age = max(0.5, (now.timestamp() - float(film.get('createdAt') or now.timestamp())) / 86400)
            day, hour = posted_local(str(when), zone)
            profile = film.get('profile') if isinstance(film.get('profile'), dict) else {}
            row = {'title': info['title'] or film.get('title', ''), 'url': 'https://www.youtube.com/shorts/' + vid,
                   'topic': profile.get('topic') or film.get('brief', '')[:160], 'angle': profile.get('angle', ''),
                   'hook': profile.get('hook') or '', 'seconds': seconds, 'language': film.get('language', ''),
                   'writer': film.get('writer', ''), 'voice': profile.get('voice', ''), 'aiMedia': bool(profile.get('aiMedia')),
                   'realPhotos': bool(film.get('sources')), 'hashtags': (film.get('hashtags') or [])[:8],
                   'postedDay': day, 'postedHour': hour, 'ageDays': round(age, 1),
                   'views': info['views'], 'viewsPerDay': round(info['views'] / age, 1),
                   'likes': info['likes'], 'comments': info['comments'], 'channel': channel}
            if deep is not None and vid in deep:
                d = deep[vid]
                row.update(watchedPercent=d.get('averageViewPercentage'), watchedSeconds=d.get('averageViewDuration'),
                           shares=d.get('shares'), subscribersGained=d.get('subscribersGained'))
                if curves < RETENTION_FILMS and age >= 2:
                    try:
                        row['stillWatching'] = retention(token, vid, seconds, today, opener)
                        curves += 1
                    except (AnalyticsUnavailable, HTTPError, ValueError) as error:
                        print('Watch curve unavailable:', str(error)[:120], file=sys.stderr)
            rows.append(row)
    return rows, ' '.join(dict.fromkeys(problems))


def lessons_prompt(rows, settings):
    deep = any('watchedPercent' in r for r in rows)
    return f'''You are the analyst for a YouTube Shorts channel that makes short animated explainers.
Subject: {settings.get('domain') or 'Science and general knowledge'}. Audience: {settings.get('audience', 'General')}.
Below are the channel's own films with their real numbers (newest first). viewsPerDay is the fairest
way to compare films of different ages; films under 2 days old are still growing.
{'watchedPercent is the average share of the film watched; stillWatching.afterHook is the share of viewers still watching after the first ~3 seconds (the hook), middle and end are later points, and above 1 means people replayed; relative compares the curve with similar Shorts on YouTube (0.5 is typical).' if deep else 'Only views, likes and comments are available (no watch-time data).'}

Find out why some films get many more views than others. Compare the best quarter with the worst
quarter: topics, angles, hooks (the first sentence), length, language, voice, real photos vs
animation, AI media, hashtags, posting day and hour, and (when available) how many people stay past
the hook and to the end. Only state what the numbers support; with fewer than 5 films, call the
lessons early signs. Never invent numbers. Write rules a scriptwriter can follow in the next film.

FILMS:
{json.dumps(rows, ensure_ascii=False)}

Return ONLY JSON: {{"summary": 2-3 plain sentences on what drives views on this channel,
"working": [{{"lesson": string, "evidence": the numbers behind it}}] (up to 6),
"notWorking": [{{"lesson": string, "evidence": string}}] (up to 6),
"rules": [up to 8 short, concrete instructions for the next films, e.g. "Open with a surprising question in the first 2 seconds"],
"moreTopics": [up to 6 kinds of topics to make more of], "lessTopics": [up to 6 kinds to make less of],
"hooks": [up to 4 opening lines from the best films that worked, quoted exactly],
"bestSeconds": the film length in seconds that works best, or null,
"bestTimes": [up to 3 posting times like "Friday 18:00", only if the data shows a difference],
"experiments": [up to 3 things to try next to learn more]}}'''


def clean_lessons(raw, rows, problem=''):
    from backup import text

    def items(key, limit, size=240):
        value = raw.get(key) if isinstance(raw, dict) else None
        return [text(x, size) for x in (value if isinstance(value, list) else []) if isinstance(x, str) and x.strip()][:limit]

    def findings(key):
        value = raw.get(key) if isinstance(raw, dict) else None
        out = []
        for x in (value if isinstance(value, list) else [])[:6]:
            if isinstance(x, dict) and text(x.get('lesson'), 240):
                out.append({'lesson': text(x['lesson'], 240), 'evidence': text(x.get('evidence'), 240)})
        return out
    if not isinstance(raw, dict) or not text(raw.get('summary'), 600):
        raise ValueError('No summary')
    rules = items('rules', 8)
    if not rules:
        raise ValueError('No rules')
    try:
        best = int(raw.get('bestSeconds')) if raw.get('bestSeconds') is not None else None
        best = best if best and 15 <= best <= 60 else None
    except (TypeError, ValueError):
        best = None
    top = sorted(rows, key=lambda r: -r['viewsPerDay'])
    return {'updatedAt': int(time.time()), 'films': len(rows), 'early': len(rows) < 5, 'problem': problem,
            'summary': text(raw['summary'], 600), 'working': findings('working'), 'notWorking': findings('notWorking'),
            'rules': rules, 'moreTopics': items('moreTopics', 6, 120), 'lessTopics': items('lessTopics', 6, 120),
            'hooks': items('hooks', 4, 200), 'bestSeconds': best, 'bestTimes': items('bestTimes', 3, 40),
            'experiments': items('experiments', 3),
            'table': [{k: r.get(k) for k in ('title', 'url', 'views', 'viewsPerDay', 'likes', 'watchedPercent',
                                             'subscribersGained', 'ageDays', 'seconds')}
                      | {'afterHook': (r.get('stillWatching') or {}).get('afterHook')} for r in top[:30]]}


def lessons_block(path=LESSONS):
    """What the channel's own numbers taught, written for the film, topic and idea prompts."""
    try:
        data = json.loads(Path(path).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return ''
    if not isinstance(data, dict) or not data.get('rules'):
        return ''
    lines = [f'\nLESSONS FROM THIS CHANNEL\'S OWN ANALYTICS ({data.get("films", 0)} films'
             + (', early signs' if data.get('early') else '') + '). Follow them unless the brief says otherwise:']
    lines += ['- ' + r for r in data['rules'][:8]]
    if data.get('moreTopics'):
        lines.append('Topics that get views here: ' + '; '.join(data['moreTopics'][:6]))
    if data.get('lessTopics'):
        lines.append('Topics that get few views here: ' + '; '.join(data['lessTopics'][:6]))
    if data.get('hooks'):
        lines.append('Openings that worked: ' + ' | '.join(f'"{h}"' for h in data['hooks'][:4]))
    if data.get('bestSeconds'):
        lines.append(f'Best length so far: about {data["bestSeconds"]} seconds.')
    return '\n'.join(lines) + '\n'


async def claude_lessons(prompt):
    """Lessons from the AI chosen in Settings (Claude or ChatGPT); text only, no web."""
    import ai
    from actions_film import make_author
    from agent import json_result
    engine = ai.engine()
    binary = shutil.which(engine.binary)
    if not binary or not engine.signed_in(os.environ):
        raise RuntimeError(engine.name + ' is not connected')
    with tempfile.TemporaryDirectory(prefix=engine.key + '-home-') as home:
        author = make_author(binary, engine.env(home), 'off', effort='medium')
        return json_result(await author(prompt))


def main():
    from actions_deliver import Library, RepositoryFile, summary
    settings = {}
    try:
        settings = json.loads(Path(SETTINGS).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        pass
    from slots import topics_of
    first = topics_of(settings)[0][1]
    films = Library(os.environ['GITHUB_REPOSITORY']).index()
    rows, problem = collect(films, os.environ, settings.get('timezone') or 'UTC')
    target = RepositoryFile(LESSONS)
    old = target.read({}) or {}
    if not rows:
        target.write({**old, 'problem': problem or 'No numbers yet.', 'checkedAt': int(time.time())},
                     'Shorts Studio analytics: nothing to learn yet')
        summary('Nothing to learn yet. ' + problem)
        return
    print(f'Read the numbers of {len(rows)} films.', file=sys.stderr)
    prompt = lessons_prompt(rows, first)
    import ai
    who = ai.name()
    try:
        raw = asyncio.run(claude_lessons(prompt))
        lessons = clean_lessons(raw, rows, problem)
    except Exception as error:
        print(who + ' could not analyze the films:', str(error)[:300], file=sys.stderr)
        from backup import ask, writers_from_env
        lessons, _ = ask(writers_from_env(), prompt, lambda raw: clean_lessons(raw, rows, problem))
        who = 'the free backup writer'
    lessons['by'] = who
    lessons['checkedAt'] = lessons['updatedAt']
    target.write(lessons, 'Shorts Studio analytics: new lessons')
    summary(f'{who} studied {len(rows)} films. {lessons["summary"]}' + (' ' + problem if problem else ''))
    from notify import notify
    notify('ideas', 'New lessons from your analytics', lessons['summary'][:180], '/#insights')


if __name__ == '__main__':
    main()


# ---- Before each autopilot film: which Up next idea is most likely to get views ----

def evidence_text(path=LESSONS, views=None):
    """The channel's lessons and film numbers plus the most-viewed Shorts, for judging ideas ('' if none)."""
    from trends import prompt_block
    parts = [lessons_block(path)]
    try:
        data = json.loads(Path(path).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        data = {}
    table = data.get('table') if isinstance(data, dict) else None
    if isinstance(table, list) and table:
        parts.append("This channel's films, most views per day first:\n" + '\n'.join(
            f'- "{r.get("title", "")}": {r.get("views")} views, {r.get("viewsPerDay")}/day'
            + (f', watched {r["watchedPercent"]}%' if r.get('watchedPercent') is not None else '')
            + (f', {round(r["afterHook"] * 100)}% stayed past the start' if r.get('afterHook') is not None else '')
            for r in table[:20] if isinstance(r, dict)) + '\n')
    if views:
        parts.append(prompt_block(views))
    return ''.join(p for p in parts if p)


def idea_review_prompt(pool, evidence, settings):
    ideas = [{'id': x['id'], 'topic': x.get('topic', ''), 'angle': x.get('angle', ''), 'why': x.get('reason', ''),
              'signal': x.get('signal', ''), 'addedBy': 'owner' if x.get('source') == 'you' else {'chatgpt': 'ChatGPT', 'backup': 'the free backup'}.get(x.get('source'), 'Claude')} for x in pool]
    return f'''You decide which film a YouTube Shorts channel makes next, to get the most views and interest.
Subject: {settings.get('domain') or 'General knowledge'}. Audience: {settings.get('audience', 'General')}.
Judge each idea in the Up next list against the real evidence below: what got views (and kept viewers past
the start) on this channel, what did not, and what the most-viewed Shorts on this subject are about.
Pick the ONE idea most likely to make viewers stop scrolling, watch to the end and subscribe.
Mark an idea for removal only when the evidence shows people are not interested in it (for example it is
the kind of topic that got few views here, or nothing like it gets views on Shorts), and say why in a few
words with the number behind it. Keep ideas you are unsure about. If no idea is worth making, set pick to
null and a fresh topic will be researched instead. Do not search the web; use only this evidence.
{evidence}
UP NEXT IDEAS:
{json.dumps(ideas, ensure_ascii=False)}

Return ONLY JSON: {{"pick": the chosen id or null, "why": one short sentence,
"remove": [{{"id": string, "why": short reason with the evidence}}]}}'''


def clean_review(raw, pool):
    """(picked idea or None, [removed ideas with why], why). Ideas the owner added are never removed."""
    from backup import text
    if not isinstance(raw, dict):
        raise ValueError('No review')
    by_id = {x['id']: x for x in pool}
    pick = raw.get('pick')
    if pick is not None and pick not in by_id:
        raise ValueError('The chosen idea is not in the list')
    removed = []
    for item in raw.get('remove') if isinstance(raw.get('remove'), list) else []:
        idea = by_id.get(item.get('id')) if isinstance(item, dict) else None
        if idea and idea['id'] != pick and idea.get('source') != 'you' and idea['id'] not in {r['id'] for r in removed}:
            removed.append({'id': idea['id'], 'topic': idea.get('topic', ''), 'why': text(item.get('why'), 200)})
    if pick is None and not removed and pool:
        pick = pool[0]['id']  # "none" without saying why: keep the list's order
    return (by_id[pick] if pick else None), removed, text(raw.get('why'), 300)
