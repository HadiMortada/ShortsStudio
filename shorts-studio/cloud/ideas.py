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
KEEP = 20


def idea_prompt(settings, history, existing, count=10):
    from content import avoid_list, rules_text
    recent = [x.get('topic', '') for x in history[-90:] if isinstance(x, dict)]
    queued = [x.get('topic', '') for x in existing]
    return f'''You plan topics for a YouTube Shorts channel of short animated explainers.
Subject area: {settings.get('domain') or 'Science and general knowledge'}
Audience: {settings.get('audience', 'General audience')}
Language: {settings.get('language', 'English')}
Tone: {settings.get('tone', 'Clear, engaging and factual')}
Use WebSearch to find what people are curious about right now in this subject (questions people
ask, surprising discoveries in the news, common myths, seasonal events). Do not invent trends,
search volumes or view counts. Propose {count} different topics, each a specific question,
surprising fact, myth or story with a strong visual hook that works in 20-40 seconds, using only
well-established knowledge. Vary sub-topics. Avoid these recent topics: {json.dumps(recent, ensure_ascii=False)}
and these already queued: {json.dumps(queued, ensure_ascii=False)}.
Rank by how likely each is to make a viewer stop scrolling and watch to the end.
Return ONLY JSON: {{"ideas": [{{"topic": short title in the requested language,
"angle": the hook in one line, "brief": a 2-4 sentence creative brief in the requested language,
"reason": why it should interest people, one line, "score": 1-100,
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
                    'source': source, 'createdAt': int(time.time())})
    if len(out) < 3:
        raise ValueError('Too few usable ideas')
    return out


def merge(existing, new, keep=KEEP):
    """Keep the owner's list and order; add new ideas after it, best first, without duplicates."""
    seen = {re.sub(r'\W+', '', x.get('topic', '').lower()) for x in existing}
    fresh = [x for x in sorted(new, key=lambda x: -x['score']) if re.sub(r'\W+', '', x['topic'].lower()) not in seen]
    return (list(existing) + fresh)[:keep]


async def claude_ideas(settings, history, existing, run=None):
    from actions_film import claude_env, make_author
    binary = shutil.which('claude')
    if not binary or not os.environ.get('CLAUDE_CODE_OAUTH_TOKEN', '').strip():
        raise RuntimeError('Claude is not connected')
    with tempfile.TemporaryDirectory(prefix='claude-home-') as home:
        author = make_author(binary, claude_env(home), 'auto', **({'run': run} if run else {}))
        from agent import json_result
        from content import avoid_list
        return clean_ideas(json_result(await author(idea_prompt(settings, history, existing))), 'claude',
                           avoid_list(settings))


def backup_ideas(settings, history, existing, writers=None):
    from backup import ask, writers_from_env
    prompt = idea_prompt(settings, history, existing).replace(
        'Use WebSearch to find what people are curious about right now in this subject',
        'From your own knowledge, think about what people are commonly curious about in this subject')
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


def main():
    from actions_deliver import RepositoryFile, summary
    settings = load(SETTINGS, {})
    history = load(HISTORY, [])
    target = RepositoryFile(IDEAS)
    existing = target.read([]) or []
    try:
        new = asyncio.run(claude_ideas(settings, history, existing))
        who = 'Claude (with web research)'
    except Exception as error:
        print('Claude could not suggest ideas:', str(error)[:300], file=sys.stderr)
        new = backup_ideas(settings, history, existing)
        who = 'the free backup writer'
    ideas = merge(existing, new)
    target.write(ideas, 'Add topic ideas for Shorts Studio autopilot')
    summary(f'Added {len(ideas) - len(existing)} topic ideas from {who}. The list now has {len(ideas)}.')
    from notify import notify
    notify('ideas', f'{len(ideas) - len(existing)} new topic ideas', f'From {who}. Tap to choose what comes next.', '/#autopilot')


if __name__ == '__main__':
    main()
