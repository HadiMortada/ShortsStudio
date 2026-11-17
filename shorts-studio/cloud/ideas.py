"""Topic ideas for Autopilot: Claude researches what people find interesting within the subject
area and keeps a ranked "Up next" list (shorts-studio/autopilot/ideas.json) that the owner can
edit on the Shorts Studio page. Autopilot films take the top idea; the free backup writer
suggests ideas (without live research) when Claude is unavailable.
"""
import asyncio
import hashlib
import json
import os
import re
import shutil
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

IDEAS = 'shorts-studio/autopilot/ideas.json'
SETTINGS = 'shorts-studio/autopilot/settings.json'
HISTORY = 'shorts-studio/autopilot/history.json'
KEEP = 40


def idea_prompt(settings, history, existing, count=10, signals=None):
    from content import avoid_list, rules_text
    from trends import prompt_block
    data = prompt_block(signals)
    recent = [x.get('topic', '') for x in history[-90:] if isinstance(x, dict)]
    queued = [x.get('topic', '') for x in existing]
    return f'''You plan topics for a YouTube Shorts channel of short animated explainers.
Subject area: {settings.get('domain') or 'Science and general knowledge'}
Audience: {settings.get('audience', 'General audience')}
Language: {settings.get('language', 'English')}
Tone: {settings.get('tone', 'Clear, engaging and factual')}
{data}Use WebSearch to find what is getting the most views right now on YouTube Shorts, TikTok and Instagram
Reels in this subject, and what people are curious about (questions people ask, surprising discoveries
in the news, common myths, seasonal events). {'Build on the real view data above: notice which sub-topics, formats and hooks get the most views, and propose fresh films in those winning patterns (never copy a title). ' if data else ''}Do not invent trends,
search volumes or view counts. Propose {count} different topics, each a specific question,
surprising fact, myth or story with a strong visual hook that works in 20-40 seconds, using only
well-established knowledge. Vary sub-topics. Avoid these recent topics: {json.dumps(recent, ensure_ascii=False)}
and these already queued: {json.dumps(queued, ensure_ascii=False)}.
Rank by how many views each is likely to get: proven demand first, then how likely it is to make a
viewer stop scrolling and watch to the end.
Return ONLY JSON: {{"ideas": [{{"topic": short title in the requested language,
"angle": the hook in one line, "brief": a 2-4 sentence creative brief in the requested language,
"reason": why it should interest people, one line, "score": 1-100,
"signal": the real demand it rides, one short line with a number only if you saw it (e.g. "Similar Short:
2.1M views this month"; empty if none),
"hashtags": 3-6 hashtags that bring views for this idea, taken from the data above when possible,
"evidence": [{{"title": string, "url": HTTPS URL}}] (0-3 sources you actually found)}}]}}''' + rules_text(avoid_list(settings))


def clean_ideas(raw, source, avoid=()):
    from backup import text
    from content import find
    items = raw.get('ideas') if isinstance(raw, dict) else raw
    if not isinstance(items, list):
        raise ValueError('No ideas list')
    out = []
    for item in items[:15]:
        if not isinstance(item, dict) or not text(item.get('topic'), 200):
            continue
        try:
            score = max(1, min(100, int(float(item.get('score', 50)))))
        except (TypeError, ValueError):
            score = 50
        evidence = [{'title': text(e.get('title'), 200), 'url': str(e.get('url', ''))[:500]}
                    for e in (item.get('evidence') or [])[:3]
                    if isinstance(e, dict) and str(e.get('url', '')).startswith('https://')]
        topic = text(item['topic'], 200)
        if find({k: item.get(k) for k in ('topic', 'angle', 'brief')}, avoid):
            continue  # touches the owner's "Never in my films" list
        out.append({'id': hashlib.sha256((topic + str(time.time())).encode()).hexdigest()[:12], 'topic': topic,
                    'angle': text(item.get('angle'), 300), 'brief': text(item.get('brief'), 2000) or topic,
                    'reason': text(item.get('reason'), 300), 'score': score, 'evidence': evidence,
                    'signal': text(item.get('signal'), 160), 'hashtags': clean_tags(item.get('hashtags'), avoid),
                    'source': source, 'createdAt': int(time.time())})
    if len(out) < 3:
        raise ValueError('Too few usable ideas')
    return out


def clean_tags(value, avoid=()):
    from content import find
    from youtube import hashtag
    out = []
    for item in (value if isinstance(value, list) else [])[:8]:
        tag = hashtag(item) if isinstance(item, str) else ''
        if tag and tag.lower() not in {t.lower() for t in out} and tag.lower() != '#shorts' and not find(tag, avoid):
            out.append(tag)
    return out[:6]


def merge(existing, new, keep=KEEP):
    """Keep the owner's list and order; add new ideas after it, best first, without duplicates."""
    seen = {re.sub(r'\W+', '', x.get('topic', '').lower()) for x in existing}
    fresh = [x for x in sorted(new, key=lambda x: -x['score']) if re.sub(r'\W+', '', x['topic'].lower()) not in seen]
    return (list(existing) + fresh)[:keep]


async def claude_ideas(settings, history, existing, run=None, signals=None):
    from actions_film import claude_env, make_author
    binary = shutil.which('claude')
    if not binary or not (os.environ.get('STUDIO_CLAUDE_ACCESS') or os.environ.get('CLAUDE_CODE_OAUTH_TOKEN', '').strip()):
        raise RuntimeError('Claude is not connected')
    with tempfile.TemporaryDirectory(prefix='claude-home-') as home:
        author = make_author(binary, claude_env(home), 'auto', **({'run': run} if run else {}))
        from agent import json_result
        from content import avoid_list
        return clean_ideas(json_result(await author(idea_prompt(settings, history, existing, signals=signals))), 'claude',
                           avoid_list(settings))


def backup_ideas(settings, history, existing, writers=None, signals=None):
    from backup import ask, writers_from_env
    prompt = idea_prompt(settings, history, existing, signals=signals).replace(
        'Use WebSearch to find what is getting the most views right now on YouTube Shorts, TikTok and Instagram\n'
        'Reels in this subject, and what people are curious about',
        'From your own knowledge (no web access), think about what gets views on Shorts in this subject and what people\n'
        'are commonly curious about')
    from content import avoid_list
    ideas, _ = ask(writers if writers is not None else writers_from_env(), prompt,
                   lambda raw: clean_ideas(raw, 'backup', avoid_list(settings)))
    return ideas


def load(path, default):
    try:
        value = json.loads(Path(path).read_text(encoding='utf-8'))
        return value if isinstance(value, type(default)) else default
    except (OSError, ValueError):
        return default


def lanes_to_fill(settings, existing, wanted=''):
    """[(topic id, topic settings)] that need ideas: the one asked for, else each with fewer than 3."""
    from slots import topics_of
    lanes = topics_of(settings)
    first = lanes[0][0]
    if wanted:
        return [(tid, s) for tid, s in lanes if tid == wanted]
    count = lambda tid: sum(1 for x in existing if (x.get('lane') or first) == tid)
    return [(tid, s) for tid, s in lanes if str(s.get('domain', '')).strip() and count(tid) < 3] or lanes[:1]


TRENDS_FILE = 'shorts-studio/autopilot/trends.json'


def view_access(source=None):
    """(token, problem, own films) for reading YouTube view data; (None, reason, []) without access."""
    source = os.environ if source is None else source
    raw = source.get('YOUTUBE_OAUTH_JSON', '').strip()
    if not raw:
        return None, 'Connect YouTube on the Shorts Studio page so ideas can use real view data.', []
    from trends import NeedsReconnect, access_token, own_films
    try:
        token = access_token(raw)
    except NeedsReconnect as error:
        return None, str(error), []
    except Exception as error:
        return None, 'YouTube view data unavailable: ' + str(error)[:200], []
    own = []
    try:
        from actions_deliver import Library
        films = Library(source['GITHUB_REPOSITORY']).index() if source.get('GITHUB_REPOSITORY') else []
        own = own_films([f.get('youtube') for f in films if isinstance(f, dict)], token)
    except Exception as error:
        print('Own film views unavailable:', str(error)[:200], file=sys.stderr)
    return token, '', own


def main():
    from actions_deliver import RepositoryFile, summary
    settings = load(SETTINGS, {})
    history = load(HISTORY, [])
    target = RepositoryFile(IDEAS)
    existing = target.read([]) or []
    from slots import topics_of
    first = topics_of(settings)[0][0]
    ideas, who = list(existing), 'Claude (with web research)'
    token, problem, own = view_access()
    trends_file = RepositoryFile(TRENDS_FILE)
    trends = trends_file.read({}) or {}
    lanes_data = trends.get('lanes') if isinstance(trends.get('lanes'), dict) else {}
    for lane, topic_settings in lanes_to_fill(settings, existing, os.environ.get('STUDIO_TOPIC', '').strip()):
        queued = [x for x in ideas if (x.get('lane') or first) == lane]
        signals = None
        if token:
            try:
                from content import avoid_list
                from trends import lane_signals
                signals = lane_signals(topic_settings, token, avoid_list(topic_settings), own)
                lanes_data[lane or 'default'] = signals
                print(f'View data for {topic_settings.get("domain")!r}: {len(signals["top"])} top Shorts, '
                      f'{len(signals["hashtags"])} hashtags', file=sys.stderr)
            except Exception as error:
                problem = problem or 'YouTube view data unavailable: ' + str(error)[:200]
                print(problem, file=sys.stderr)
        try:
            new = asyncio.run(claude_ideas(topic_settings, history, queued, signals=signals))
        except Exception as error:
            print('Claude could not suggest ideas:', str(error)[:300], file=sys.stderr)
            new = backup_ideas(topic_settings, history, queued, signals=signals)
            who = 'the free backup writer'
        if lane:
            new = [{**x, 'lane': lane} for x in new]
        ideas = merge(ideas, new)
    target.write(ideas, 'Add topic ideas for Shorts Studio autopilot')
    try:
        trends_file.write({'updatedAt': int(time.time()), 'problem': problem, 'lanes': lanes_data},
                          'Update Shorts Studio view data')
    except Exception as error:
        print('Could not save view data:', str(error)[:200], file=sys.stderr)
    summary(f'Added {len(ideas) - len(existing)} topic ideas from {who}. The list now has {len(ideas)}.'
            + (' ' + problem if problem else ' Ideas used real YouTube view data.' if token else ''))
    from notify import notify
    notify('ideas', f'{len(ideas) - len(existing)} new topic ideas', f'From {who}. Tap to choose what comes next.', '/#autopilot')


if __name__ == '__main__':
    main()
