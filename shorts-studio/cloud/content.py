"""The owner's "Never in my films" list: what no film may mention, show or suggest.

Two layers: the rules are added to every writing prompt (Claude and the free backup), and the
finished film's text is checked before anything is saved or posted. The check reads the narration,
on-screen text (the animation source), titles, descriptions, hashtags and requested image names, in
English and Arabic. It cannot see pictures, so the prompt rules also forbid showing these things.
"""
import json
import re

# key: (label used in prompts and messages, English words, Arabic words).
# English words match whole words (plural -s/-es included); Arabic words match anywhere in a word,
# so prefixes such as ال، و، ب are covered.
CATEGORIES = {
    'israel': ('Israel (never name it, show its flag or label it on a map)',
               ['israel', 'israeli', 'zionist', 'zionism', 'tel aviv'],
               ['إسرائيل', 'اسرائيل', 'إسرائيلي', 'اسرائيلي', 'صهيون', 'تل أبيب', 'تل ابيب']),
    'alcohol': ('alcohol and alcoholic drinks',
                ['alcohol', 'alcoholic', 'beer', 'wine', 'vodka', 'whiskey', 'whisky', 'liquor', 'cocktail',
                 'champagne', 'booze', 'drunk', 'brewery', 'tequila', 'rum', 'hangover'],
                ['كحول', 'خمر', 'خمور', 'بيرة', 'نبيذ', 'سكران', 'ويسكي', 'فودكا']),
    'drugs': ('recreational or illegal drugs (medicines are fine)',
              ['cocaine', 'heroin', 'marijuana', 'cannabis', 'methamphetamine', 'crystal meth', 'lsd', 'opium',
               'hashish', 'narcotic', 'illegal drug', 'recreational drug', 'drug dealer', 'drug cartel',
               'drug addict', 'drug addiction', 'drug abuse', 'overdose', 'getting high'],
              ['مخدرات', 'مخدّرات', 'حشيش', 'كوكايين', 'هيروين', 'ماريجوانا', 'أفيون', 'افيون']),
    'smoking': ('smoking, tobacco and vaping',
                ['cigarette', 'cigar', 'tobacco', 'smoking', 'vape', 'vaping', 'e-cigarette', 'nicotine', 'hookah',
                 'shisha'],
                ['سيجارة', 'سجائر', 'سيجار', 'تدخين', 'تبغ', 'أرجيلة', 'نرجيلة', 'شيشة']),
    'gambling': ('gambling and betting',
                 ['gambling', 'gamble', 'casino', 'betting', 'lottery', 'poker', 'roulette', 'slot machine',
                  'jackpot'],
                 ['قمار', 'كازينو', 'مراهنة', 'مراهنات', 'يانصيب']),
    'violence': ('graphic violence, gore, suicide or self-harm',
                 ['gore', 'gory', 'beheading', 'beheaded', 'torture', 'massacre', 'suicide', 'self-harm',
                  'dismember'],
                 ['انتحار', 'تعذيب', 'مجزرة', 'قطع الرأس']),
    'sexual': ('sexual content or nudity',
               ['porn', 'pornography', 'nude', 'nudity', 'naked', 'erotic', 'sexy', 'sexual'],
               ['إباحي', 'اباحي', 'عاري', 'عارية', 'جنسي']),
    'profanity': ('swearing or crude language',
                  ['fuck', 'fucking', 'shit', 'bitch', 'bastard', 'asshole', 'motherfucker', 'wtf'],
                  []),
}
# Words the owner may type that mean a whole category (so "alcohol" also blocks beer, wine...).
ALIASES = {
    'israel': 'israel', 'israeli': 'israel', 'إسرائيل': 'israel', 'اسرائيل': 'israel',
    'alcohol': 'alcohol', 'alcoholic drinks': 'alcohol', 'drinking': 'alcohol', 'كحول': 'alcohol', 'الكحول': 'alcohol', 'خمر': 'alcohol',
    'drugs': 'drugs', 'drug': 'drugs', 'illegal drugs': 'drugs', 'مخدرات': 'drugs', 'المخدرات': 'drugs',
    'smoking': 'smoking', 'tobacco': 'smoking', 'cigarettes': 'smoking', 'تدخين': 'smoking', 'التدخين': 'smoking',
    'gambling': 'gambling', 'betting': 'gambling', 'قمار': 'gambling', 'القمار': 'gambling',
    'violence': 'violence', 'gore': 'violence', 'violence and gore': 'violence', 'عنف': 'violence', 'العنف': 'violence',
    'sexual': 'sexual', 'sexual content': 'sexual', 'nudity': 'sexual', 'sex': 'sexual', 'جنس': 'sexual',
    'profanity': 'profanity', 'swearing': 'profanity', 'bad words': 'profanity', 'شتائم': 'profanity',
}
# What the list starts with, shown as typed words.
DEFAULT_AVOID = ['israel', 'alcohol', 'drugs', 'smoking', 'gambling', 'violence', 'sexual content', 'swearing']
MAX_ITEMS = 30


def avoid_list(settings):
    """The owner's list from settings.json; everything is blocked when the setting was never saved."""
    value = settings.get('avoid') if isinstance(settings, dict) else None
    if not isinstance(value, list):
        return list(DEFAULT_AVOID)
    out = []
    for item in value[:MAX_ITEMS]:
        item = str(item).strip().lower()[:40]
        if item and item not in out:
            out.append(item)
    return out


def _entries(avoid):
    seen = set()
    for item in avoid or ():
        item = ALIASES.get(str(item).strip().lower(), item)
        if item in seen:
            continue
        seen.add(item)
        if item in CATEGORIES:
            label, english, arabic = CATEGORIES[item]
            yield item, label, english, arabic
        elif str(item).strip():
            word = str(item).strip().lower()
            arabic = bool(re.search(r'[؀-ۿ]', word))
            yield word, word, ([] if arabic else [word]), ([word] if arabic else [])


def _pattern(english, arabic):
    parts = [r'(?<![\w-])' + r'[\s-]+'.join(map(re.escape, w.split())) + r'(?:s|es)?(?![\w])' for w in english]
    parts += [re.escape(w) for w in arabic]
    return re.compile('|'.join(parts), re.IGNORECASE) if parts else None


def find(text, avoid):
    """The labels of blocked items mentioned in text (empty when nothing matches)."""
    text = text if isinstance(text, str) else json.dumps(text, ensure_ascii=False)
    hits = []
    for key, label, english, arabic in _entries(avoid):
        pattern = _pattern(english, arabic)
        if pattern and pattern.search(text):
            hits.append(key)
    return hits


def design_problems(design, avoid):
    """Blocked items found anywhere the viewer can see or hear them, or in YouTube's text."""
    parts = [design.get(k, '') for k in ('title', 'narration', 'description', 'source')]
    parts += [json.dumps(design.get(k, ''), ensure_ascii=False) for k in ('beats', 'youtube', 'assets')]
    return find('\n'.join(str(p) for p in parts), avoid)


def labels(hits):
    return ', '.join(CATEGORIES[h][0].split(' (')[0] if h in CATEGORIES else h for h in hits)


def rules_text(avoid):
    """The prompt block telling the writer what can never appear."""
    entries = list(_entries(avoid))
    if not entries:
        return ''
    lines = '\n'.join('- ' + label for _, label, _, _ in entries)
    return ('\nCONTENT RULES (the channel owner\'s, always apply): the film must never mention, show, depict, '
            'imply or use as an example any of the following, in narration, on-screen text, drawings, images, '
            'titles, descriptions or hashtags. Choose another example or topic instead:\n' + lines + '\n')
