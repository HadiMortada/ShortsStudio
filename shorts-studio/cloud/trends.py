"""Real view data from YouTube, so ideas and hashtags follow what people actually watch.

- top_shorts(): the most-viewed Shorts about a subject published in the last weeks (YouTube search,
  ordered by views), with their view counts and the hashtags they use.
- rank_hashtags(): hashtags weighted by the views of the videos that use them. A hashtag must be used
  by at least two different channels, so one channel's brand tag never wins.
- own_films(): how the channel's own recent films did, so ideas learn from what worked here.

Reading needs the YouTube connection to include read access (youtube.readonly). Older connections
were upload-only: then everything here reports NeedsReconnect and films keep the writer's hashtags.
"""
import json
import math
import re
import time
from datetime import datetime, timedelta, timezone
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

API = 'https://www.googleapis.com/youtube/v3/'
TRENDS = 'shorts-studio/autopilot/trends.json'
# Hashtags that say "this is a Short" add no reach (#Shorts is always added first anyway).
FORMAT_TAGS = {'shorts', 'short', 'youtubeshorts', 'ytshorts', 'shortsvideo', 'shortvideo', 'shortsfeed',
               'shortsyoutube', 'youtube', 'video', 'viral', 'viralvideo', 'trending', 'fyp', 'foryou',
               'foryoupage', 'subscribe', 'reels', 'reel', 'tiktok'}
LANGUAGES = {'english': 'en', 'arabic': 'ar', 'french': 'fr', 'spanish': 'es', 'german': 'de', 'turkish': 'tr',
             'portuguese': 'pt', 'italian': 'it', 'russian': 'ru', 'hindi': 'hi', 'urdu': 'ur', 'indonesian': 'id'}
READ_SCOPES = {'https://www.googleapis.com/auth/youtube.readonly', 'https://www.googleapis.com/auth/youtube',
               'https://www.googleapis.com/auth/youtube.force-ssl'}
HASHTAG = re.compile(r'#(\w{2,40})', re.UNICODE)


class NeedsReconnect(RuntimeError):
    """The YouTube connection is upload-only; reconnecting on the page adds read access."""


def language_code(value):
    value = str(value or '').strip().lower()
    return LANGUAGES.get(value) or (value[:2] if re.fullmatch(r'[a-z]{2}(-.*)?', value) else '')


def access_token(raw, opener=urlopen):
    """A fresh access token from the YOUTUBE_OAUTH_JSON secret (refresh token; all granted scopes)."""
    info = json.loads(raw) if isinstance(raw, str) else raw
    if not isinstance(info, dict) or not info.get('refresh_token'):
        raise RuntimeError('YouTube is not connected')
    body = urlencode({'client_id': info['client_id'], 'client_secret': info['client_secret'],
                      'refresh_token': info['refresh_token'], 'grant_type': 'refresh_token'}).encode()
    with opener(Request(info.get('token_uri') or 'https://oauth2.googleapis.com/token', data=body,
                        method='POST'), timeout=30) as response:
        token = json.loads(response.read().decode())
    granted = set(str(token.get('scope', '')).split())
    if granted and not granted & READ_SCOPES:
        raise NeedsReconnect('Reconnect YouTube on the Shorts Studio page so ideas can read view data.')
    return token['access_token']


def call(path, params, token, opener=urlopen):
    request = Request(API + path + '?' + urlencode(params), headers={'Authorization': 'Bearer ' + token})
    try:
        with opener(request, timeout=30) as response:
            return json.loads(response.read().decode())
    except HTTPError as error:
        if error.code in (401, 403) and b'insufficient' in (error.read() or b'').lower():
            raise NeedsReconnect('Reconnect YouTube on the Shorts Studio page so ideas can read view data.') from None
        raise


def _seconds(duration):
    match = re.fullmatch(r'P(?:(\d+)D)?T?(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?', duration or '')
    if not match:
        return 0
    d, h, m, s = (int(x or 0) for x in match.groups())
    return d * 86400 + h * 3600 + m * 60 + s


def details(ids, token, opener=urlopen):
    """Views, likes, hashtags and length for video ids (1 quota unit per 50 ids)."""
    out = []
    for start in range(0, len(ids), 50):
        data = call('videos', {'part': 'snippet,statistics,contentDetails', 'id': ','.join(ids[start:start + 50]),
                               'maxResults': 50}, token, opener)
        for item in data.get('items', []):
            snippet, stats = item.get('snippet', {}), item.get('statistics', {})
            text = (snippet.get('title') or '') + '\n' + (snippet.get('description') or '')
            out.append({'id': item['id'], 'title': (snippet.get('title') or '')[:150],
                        'channel': (snippet.get('channelTitle') or '')[:80], 'channelId': snippet.get('channelId', ''),
                        'views': int(stats.get('viewCount') or 0), 'likes': int(stats.get('likeCount') or 0),
                        'comments': int(stats.get('commentCount') or 0),
                        'published': (snippet.get('publishedAt') or '')[:10],
                        'seconds': _seconds(item.get('contentDetails', {}).get('duration')),
                        'hashtags': list(dict.fromkeys(h.lower() for h in HASHTAG.findall(text)))[:30]})
    return out


def top_shorts(query, token, language='', days=21, limit=25, opener=urlopen, now=None):
    """The most-viewed recent Shorts for a search (search costs 100 of the 10,000 daily quota units)."""
    now = now or datetime.now(timezone.utc)
    params = {'part': 'id', 'type': 'video', 'q': query, 'order': 'viewCount', 'videoDuration': 'short',
              'maxResults': limit, 'publishedAfter': (now - timedelta(days=days)).strftime('%Y-%m-%dT%H:%M:%SZ'),
              'safeSearch': 'strict'}
    if language:
        params['relevanceLanguage'] = language
    found = call('search', params, token, opener)
    ids = [x['id']['videoId'] for x in found.get('items', []) if x.get('id', {}).get('videoId')]
    videos = [v for v in details(ids, token, opener) if 0 < v['seconds'] <= 180] if ids else []
    return sorted(videos, key=lambda v: -v['views'])


def rank_hashtags(videos, avoid=(), limit=20):
    """[{tag, videos, channels, views}] best first: weighted by the views of the videos using them."""
    from content import find
    table = {}
    for video in videos:
        for tag in video.get('hashtags', []):
            if tag in FORMAT_TAGS or tag.isdigit():
                continue
            row = table.setdefault(tag, {'tag': '#' + tag, 'videos': 0, 'channels': set(), 'views': 0, 'score': 0.0})
            row['videos'] += 1
            row['channels'].add(video.get('channelId') or video.get('channel'))
            row['views'] += video['views']
            row['score'] += math.log10(video['views'] + 10)
    rows = [r for r in table.values() if len(r['channels']) >= 2 and not find(r['tag'].replace('_', ' '), avoid)]
    rows.sort(key=lambda r: (-r['score'], -r['views']))
    return [{'tag': r['tag'], 'videos': r['videos'], 'channels': len(r['channels']), 'views': r['views']}
            for r in rows[:limit]]


def own_films(urls, token, opener=urlopen):
    """The channel's own films with their views, best first (from the library's YouTube links)."""
    ids = []
    for url in urls:
        match = re.search(r'(?:shorts/|v=|youtu\.be/)([\w-]{11})', str(url or ''))
        if match and match.group(1) not in ids:
            ids.append(match.group(1))
    return sorted(details(ids[:50], token, opener), key=lambda v: -v['views']) if ids else []


def views(n):
    return f'{n / 1e6:.1f}M' if n >= 1e6 else f'{n / 1e3:.0f}K' if n >= 1e3 else str(n)


def lane_signals(settings, token, avoid=(), own=(), opener=urlopen, now=None):
    """Everything the idea writer should know about one topic, small enough for a prompt."""
    language = language_code(settings.get('language'))
    queries = list(dict.fromkeys(q for q in (str(settings.get('domain') or '').strip(),
                                             str(settings.get('name') or '').strip()) if q))[:2]
    videos, seen = [], set()
    for query in queries:
        for video in top_shorts(query, token, language, opener=opener, now=now):
            if video['id'] not in seen:
                seen.add(video['id'])
                videos.append(video)
    videos.sort(key=lambda v: -v['views'])
    from content import find
    videos = [v for v in videos if not find(v['title'], avoid)]
    return {'updatedAt': int(time.time()), 'queries': queries, 'language': language,
            'top': [{'title': v['title'], 'views': v['views'], 'likes': v['likes'], 'channel': v['channel'],
                     'published': v['published'], 'url': 'https://www.youtube.com/shorts/' + v['id']} for v in videos[:15]],
            'hashtags': rank_hashtags(videos, avoid),
            'own': [{'title': v['title'], 'views': v['views'], 'likes': v['likes'], 'published': v['published']}
                    for v in own][:12]}


def prompt_block(signals):
    """The real data, written for the idea prompt."""
    if not signals or not signals.get('top'):
        return ''
    lines = ['\nREAL YOUTUBE VIEW DATA (fetched just now; use it, do not invent other numbers):',
             'Most-viewed Shorts on this subject in the last 3 weeks:']
    lines += [f'- {views(v["views"])} views: "{v["title"]}" ({v["channel"]})' for v in signals['top'][:15]]
    if signals.get('hashtags'):
        lines.append('Hashtags those Shorts use, by the views behind them: ' +
                     ', '.join(f'{h["tag"]} ({views(h["views"])})' for h in signals['hashtags'][:15]))
    if signals.get('own'):
        lines.append("This channel's own recent films, most viewed first (learn what works here):")
        lines += [f'- {views(v["views"])} views: "{v["title"]}"' for v in signals['own'][:10]]
    return '\n'.join(lines) + '\n'


def film_hashtags(design, token, avoid=(), opener=urlopen, limit=6):
    """Hashtags used by the most-viewed Shorts about this film's own subject."""
    pack = design.get('youtube') if isinstance(design.get('youtube'), dict) else {}
    tags = [t for t in (pack.get('tags') or []) if isinstance(t, str) and t.strip()]
    query = (tags[0] if tags else pack.get('title') or design.get('title') or '').strip()[:100]
    if not query:
        return []
    language = language_code(str(design.get('language') or '').split('-')[0])
    return [h['tag'] for h in rank_hashtags(top_shorts(query, token, language, days=60, opener=opener), avoid)][:limit]
