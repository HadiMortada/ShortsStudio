"""On-screen graphics for edited videos: what is said, shown the way a professional editor would.

Claude (video_edit.py) chooses the visuals from the transcript; this module draws them with Pillow
(Arabic and Hebrew are shaped and set right to left through Raqm) and encodes each one as a short
clip with transparency that FFmpeg lays over the video:

  hook     the opening line, big, for the first seconds
  title    a key phrase popping in as it is said
  emoji    a relevant emoji with a little bounce
  quote    a card for a Quran verse, hadith, proverb or quotation, with its source
  point    a numbered point when the speaker lists things
  picture  a full-frame cutaway photo (B-roll) from Wikimedia Commons, chosen by Claude after
           looking at the candidates, with a slow Ken Burns move

Every clip is drawn once as a still card and then animated (pop, slide, fade), which keeps
rendering fast on GitHub's runner.
"""
import io
import json
import math
import re
import subprocess
import sys
from functools import lru_cache
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from PIL import Image, ImageDraw, ImageFilter, ImageFont

RTL = {'ar', 'fa', 'ur', 'he', 'iw', 'ps', 'yi', 'sd', 'ug', 'ku'}
CJK = {'zh', 'ja', 'ko', 'yue'}

# accent (boxes, bars), ink (text on the accent), glow (the spoken word in captions)
THEMES = {
    'gold': ((246, 186, 18), (22, 20, 16), (255, 214, 64)),
    'coral': ((232, 85, 61), (255, 255, 255), (255, 138, 101)),
    'teal': ((14, 160, 140), (255, 255, 255), (77, 232, 210)),
    'blue': ((47, 111, 235), (255, 255, 255), (125, 179, 255)),
    'green': ((38, 150, 88), (255, 255, 255), (124, 227, 161)),
    'purple': ((123, 77, 219), (255, 255, 255), (195, 166, 255)),
    'white': ((250, 250, 250), (20, 20, 20), (255, 224, 102)),
}
KINDS = ('hook', 'title', 'emoji', 'quote', 'point', 'picture')
USER_AGENT = 'ShortsStudio/1.0 (automatic video editor)'


def theme(name):
    return THEMES.get(name, THEMES['gold'])


def ass_color(rgb):
    r, g, b = rgb
    return f'&H00{b:02X}{g:02X}{r:02X}&'


# ---------------------------------------------------------------------------------------------
# Fonts and text

@lru_cache(maxsize=None)
def font_file(families, bold=True):
    """The file of the first installed family (fc-match answers with a substitute when one is missing)."""
    for name in (families,) if isinstance(families, str) else families:
        try:
            found = subprocess.run(['fc-match', '-f', '%{family}|%{file}', f'{name}:{"bold" if bold else "regular"}'],
                                   capture_output=True, text=True, timeout=20).stdout.strip()
        except (OSError, subprocess.SubprocessError):
            continue
        names, _, path = found.partition('|')
        if path and Path(path).is_file() and name.lower() in names.lower():
            return path
    for fallback in ('/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf', '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf'):
        if Path(fallback).is_file():
            return fallback
    raise RuntimeError('No font found for on-screen graphics')


def family(role, language):
    """Preferred font families, best first: Kufi for Arabic titles, Amiri (proper Quran brackets) for quotes."""
    if language in RTL:
        if language in ('he', 'iw', 'yi'):
            return ('Noto Sans Hebrew',)
        return {'title': ('Noto Kufi Arabic', 'Noto Sans Arabic'),
                'quote': ('Amiri', 'Noto Naskh Arabic', 'Noto Sans Arabic')}.get(role, ('Noto Sans Arabic',))
    if language in CJK:
        return ('Noto Sans CJK JP',) if language == 'ja' else ('Noto Sans CJK SC',)
    return {'title': ('Noto Sans Display', 'Noto Sans')}.get(role, ('Noto Sans',))


@lru_cache(maxsize=64)
def load_font(role, language, size, bold=True):
    path = font_file(family(role, language), bold)
    try:
        return ImageFont.truetype(path, size, layout_engine=ImageFont.Layout.RAQM)
    except (OSError, ImportError, KeyError):
        return ImageFont.truetype(path, size)


def text_options(language):
    return {'direction': 'rtl', 'language': language} if language in RTL else {}


def measure(font, text, language):
    probe = ImageDraw.Draw(Image.new('L', (8, 8)))
    try:
        box = probe.textbbox((0, 0), text, font=font, **text_options(language))
    except (KeyError, ValueError, OSError):
        box = probe.textbbox((0, 0), text, font=font)
    return box


def wrap(text, font, width, language, lines=3):
    """Greedy word wrap; the last line ends with an ellipsis when the text does not fit."""
    words = text.split() if language not in ('zh', 'ja', 'yue') else list(text)
    joiner = '' if language in ('zh', 'ja', 'yue') else ' '
    out, current = [], ''
    for word in words:
        trial = (current + joiner + word) if current else word
        box = measure(font, trial, language)
        if box[2] - box[0] <= width or not current:
            current = trial
        else:
            out.append(current)
            current = word
    if current:
        out.append(current)
    if len(out) > lines:
        out = out[:lines]
        out[-1] = out[-1].rstrip('.,،') + '…'
    return out


def draw_text(draw, xy, text, font, fill, language, anchor='mm'):
    try:
        draw.text(xy, text, font=font, fill=fill, anchor=anchor, **text_options(language))
    except (KeyError, ValueError, OSError):
        draw.text(xy, text, font=font, fill=fill, anchor=anchor)


def shadowed(card, radius, offset, opacity=110):
    """The card on a transparent canvas with a soft drop shadow."""
    pad = radius * 3
    canvas = Image.new('RGBA', (card.width + 2 * pad, card.height + 2 * pad), (0, 0, 0, 0))
    shadow = Image.new('RGBA', canvas.size, (0, 0, 0, 0))
    alpha = card.getchannel('A').point(lambda a: a * opacity // 255)
    shadow.paste(Image.new('RGBA', card.size, (0, 0, 0, 255)), (pad, pad + offset), alpha)
    shadow = shadow.filter(ImageFilter.GaussianBlur(radius))
    canvas.alpha_composite(shadow)
    canvas.alpha_composite(card, (pad, pad))
    return canvas


def rounded(size, radius, fill):
    card = Image.new('RGBA', size, (0, 0, 0, 0))
    ImageDraw.Draw(card).rounded_rectangle((0, 0, size[0] - 1, size[1] - 1), radius=radius, fill=fill)
    return card


# ---------------------------------------------------------------------------------------------
# Still cards

def title_card(text, width, scale, language, colors, big=False):
    accent, ink, _ = colors
    size = round((92 if big else 80) * scale) if len(text) <= 16 else round((76 if big else 66) * scale)
    font = load_font('title', language, size)
    lines = wrap(text, font, width * 0.78, language, lines=2)
    heights = [measure(font, line, language) for line in lines]
    line_h = max(b[3] - b[1] for b in heights) + round(size * (0.55 if language in RTL else 0.32))
    text_w = max(b[2] - b[0] for b in heights)
    pad_x, pad_y = round(size * 0.62), round(size * 0.42)
    card = rounded((text_w + 2 * pad_x, line_h * len(lines) + 2 * pad_y - round(size * 0.15)), round(size * 0.42), accent + (255,))
    draw = ImageDraw.Draw(card)
    for n, line in enumerate(lines):
        draw_text(draw, (card.width / 2, pad_y + line_h * n + line_h / 2 - round(size * 0.08)), line, font, ink + (255,), language)
    return shadowed(card, round(14 * scale), round(10 * scale))


def emoji_card(emoji, scale):
    path = font_file(('Noto Color Emoji',), bold=False)
    try:
        font = ImageFont.truetype(path, 109, layout_engine=ImageFont.Layout.RAQM)
    except OSError:
        return None
    image = Image.new('RGBA', (160, 160), (0, 0, 0, 0))
    try:
        ImageDraw.Draw(image).text((80, 80), emoji, font=font, embedded_color=True, anchor='mm')
    except (OSError, ValueError, KeyError):
        return None
    box = image.getbbox()
    if not box:
        return None
    side = round(190 * scale)
    image = image.crop(box)
    image = image.resize((side, round(side * image.height / image.width)), Image.LANCZOS)
    return shadowed(image, round(10 * scale), round(8 * scale), opacity=90)


def quote_card(text, source, kind, width, scale, language, colors):
    accent = colors[0]
    if kind == 'verse' and language == 'ar' and not text.startswith('﴿'):
        text = '﴿' + text.strip('﴿﴾ ') + '﴾'
    size = round((58 if len(text) < 90 else 50) * scale)
    font = load_font('quote', language, size)
    small = load_font('body', language, round(36 * scale))
    inner = width * 0.86 - round(110 * scale)
    lines = wrap(text, font, inner, language, lines=5)
    line_h = max(measure(font, line, language)[3] - measure(font, line, language)[1] for line in lines) + round(size * (0.75 if language in RTL else 0.45))
    source_h = round(64 * scale) if source else 0
    pad = round(54 * scale)
    card = rounded((round(width * 0.86), line_h * len(lines) + 2 * pad + source_h), round(30 * scale), (14, 14, 16, 214))
    draw = ImageDraw.Draw(card)
    bar_x = card.width - round(18 * scale) if language in RTL else round(12 * scale)
    draw.rounded_rectangle((bar_x, pad, bar_x + round(7 * scale), card.height - pad), radius=round(4 * scale), fill=accent + (255,))
    for n, line in enumerate(lines):
        draw_text(draw, (card.width / 2, pad + line_h * n + line_h / 2), line, font, (255, 255, 255, 255), language)
    if source:
        y = pad + line_h * len(lines) + source_h / 2
        draw.line((card.width / 2 - round(40 * scale), y - round(26 * scale), card.width / 2 + round(40 * scale), y - round(26 * scale)),
                  fill=accent + (255,), width=max(2, round(3 * scale)))
        draw_text(draw, (card.width / 2, y + round(4 * scale)), source, small, accent + (255,), language)
    return shadowed(card, round(18 * scale), round(12 * scale))


def point_card(number, text, width, scale, language, colors):
    accent, ink, _ = colors
    size = round(58 * scale)
    font = load_font('title', language, size)
    digit = load_font('title', 'en', round(64 * scale))
    lines = wrap(text, font, width * 0.6, language, lines=2)
    line_h = max(measure(font, line, language)[3] - measure(font, line, language)[1] for line in lines) + round(size * (0.55 if language in RTL else 0.3))
    text_w = max(measure(font, line, language)[2] - measure(font, line, language)[0] for line in lines)
    circle = round(124 * scale)
    gap, pad = round(18 * scale), round(36 * scale)
    pill_w, pill_h = text_w + 2 * pad, max(circle, line_h * len(lines) + 2 * round(22 * scale))
    card = Image.new('RGBA', (circle + gap + pill_w, pill_h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(card)
    rtl = language in RTL
    cx = card.width - circle if rtl else 0
    px = 0 if rtl else circle + gap
    draw.ellipse((cx, (pill_h - circle) // 2, cx + circle, (pill_h + circle) // 2), fill=accent + (255,))
    draw_text(draw, (cx + circle / 2, pill_h / 2 - round(4 * scale)), str(number), digit, ink + (255,), 'en')
    draw.rounded_rectangle((px, 0, px + pill_w, pill_h), radius=round(28 * scale), fill=(255, 255, 255, 245))
    top = (pill_h - line_h * len(lines)) / 2
    for n, line in enumerate(lines):
        draw_text(draw, (px + pill_w / 2, top + line_h * n + line_h / 2 - round(4 * scale)), line, font, (20, 20, 22, 255), language)
    return shadowed(card, round(14 * scale), round(10 * scale))


def cover(image, size):
    """Scale and centre-crop to fill SIZE."""
    width, height = size
    ratio = max(width / image.width, height / image.height)
    image = image.resize((math.ceil(image.width * ratio), math.ceil(image.height * ratio)), Image.LANCZOS)
    left, top = (image.width - width) // 2, (image.height - height) // 2
    return image.crop((left, top, left + width, top + height))


# ---------------------------------------------------------------------------------------------
# Animation

def ease_back(x):
    c1 = 1.70158
    return 1 + (c1 + 1) * (x - 1) ** 3 + c1 * (x - 1) ** 2


def ease_out(x):
    return 1 - (1 - x) ** 3


def frames(visual, card, fps, size):
    """Yield (frame RGBA image, x, y) for each frame of an animated card."""
    duration = visual['end'] - visual['start']
    count = max(1, round(duration * fps))
    kind = visual['type']
    width, height = size
    if kind == 'picture':
        still = card.convert('RGBA')
        shade = Image.new('L', (1, 256))
        for y in range(256):
            shade.putpixel((0, y), int(150 * max(0.0, (y / 255 - 0.55) / 0.45) ** 1.6))
        shade = shade.resize((width, height))
        for n in range(count):
            t = n / fps
            zoom = 1.0 + 0.09 * (t / max(duration, 0.1))
            w, h = round(width * zoom), round(height * zoom)
            frame = still.resize((w, h), Image.BILINEAR).crop(((w - width) // 2, (h - height) // 2, (w - width) // 2 + width, (h - height) // 2 + height))
            frame.paste((0, 0, 0), (0, 0), shade)
            fade = min(1.0, t / 0.18, (duration - t) / 0.18)
            if fade < 1:
                frame.putalpha(frame.getchannel('A').point(lambda a, f=max(0.0, fade): int(a * f)))
            yield frame, 0, 0
        return
    box_w, box_h = round(card.width * 1.12), round(card.height * 1.12)
    cx, cy = visual['x'], visual['y']
    tilt = visual.get('tilt', 0.0)
    for n in range(count):
        t = n / fps
        enter = min(1.0, t / (0.32 if kind in ('title', 'hook', 'emoji') else 0.36))
        leave = min(1.0, max(0.0, (duration - t) / 0.22))
        if kind in ('title', 'hook', 'emoji'):
            scale = 0.55 + 0.45 * ease_back(enter)
            dy = 0
        else:
            scale = 1.0
            dy = round((1 - ease_out(enter)) * 46 * height / 1920)
        if kind == 'emoji':
            dy += round(math.sin(t * 5.2) * 7 * height / 1920)
            angle = math.sin(t * 4.1) * 7 + tilt
        else:
            angle = tilt
        scale *= 0.94 + 0.06 * leave
        alpha = min(ease_out(enter) * 1.6, 1.0) * leave
        if kind == 'point':
            slide = round((1 - ease_out(enter)) * 120 * width / 1080) * (1 if visual.get('rtl') else -1)
        else:
            slide = 0
        piece = card
        if abs(scale - 1) > 1e-3:
            piece = card.resize((max(1, round(card.width * scale)), max(1, round(card.height * scale))), Image.BICUBIC)
        if abs(angle) > 0.05:
            piece = piece.rotate(angle, resample=Image.BICUBIC, expand=True)
        if alpha < 0.999:
            piece = piece.copy()
            piece.putalpha(piece.getchannel('A').point(lambda a, f=max(0.0, alpha): int(a * f)))
        frame = Image.new('RGBA', (box_w + 40, box_h + 40), (0, 0, 0, 0))
        frame.alpha_composite(piece, ((frame.width - piece.width) // 2, (frame.height - piece.height) // 2))
        yield frame, round(cx - frame.width / 2) + slide, round(cy - frame.height / 2) + dy


def encode(visual, card, fps, size, path):
    """Write the animated card as a QuickTime RLE clip with transparency. Returns (x, y, w, h)."""
    images = list(frames(visual, card, fps, size))
    width, height = size
    # One fixed box for the clip: the union of every frame's placement, clipped to the screen.
    left = max(0, min(x for _, x, _ in images))
    top = max(0, min(y for _, _, y in images))
    right = min(width, max(x + f.width for f, x, _ in images))
    bottom = min(height, max(y + f.height for f, _, y in images))
    w, h = (right - left) // 2 * 2, (bottom - top) // 2 * 2
    if w < 2 or h < 2:
        return None
    process = subprocess.Popen(['ffmpeg', '-hide_banner', '-nostdin', '-y', '-v', 'error', '-f', 'rawvideo', '-pix_fmt', 'rgba',
                                '-s', f'{w}x{h}', '-r', str(fps), '-i', '-', '-c:v', 'qtrle', '-pix_fmt', 'argb', str(path)],
                               stdin=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        for frame, x, y in images:
            # Cropping outside a frame pads it with transparent pixels.
            process.stdin.write(frame.crop((left - x, top - y, left - x + w, top - y + h)).tobytes())
        process.stdin.close()
    except BrokenPipeError:
        pass
    if process.wait(timeout=600):
        raise RuntimeError('Encoding a graphic failed: ' + process.stderr.read().decode(errors='replace')[-800:])
    return left, top, w, h


# ---------------------------------------------------------------------------------------------
# Placement and timing

def layout(visuals, size, language, colors, pictures=None):
    """Cards and screen positions for the scheduled visuals. Pictures need a downloaded image."""
    width, height = size
    scale = min(width, height) / 1080
    vertical = height >= width
    rtl = language in RTL
    placed, side = [], 1
    for visual in visuals:
        kind = visual['type']
        try:
            if kind in ('title', 'hook'):
                card = title_card(visual['text'], width, scale, language, colors, big=kind == 'hook')
                top = height * (0.115 if vertical else 0.09)
                placed.append({**visual, 'card': card, 'x': width / 2, 'y': top + card.height / 2,
                               'tilt': 0.0 if kind == 'hook' else (-1.6 if len(placed) % 2 else 1.6)})
            elif kind == 'emoji':
                card = emoji_card(visual['emoji'], scale)
                if card is None:
                    continue
                side = -side
                placed.append({**visual, 'card': card, 'x': width * (0.5 + 0.3 * side), 'y': height * (0.31 if vertical else 0.3)})
            elif kind == 'quote':
                card = quote_card(visual['text'], visual.get('source', ''), visual.get('kind', 'quote'), width, scale, language, colors)
                placed.append({**visual, 'card': card, 'x': width / 2, 'y': max(height * 0.33, card.height / 2 + height * 0.07)})
            elif kind == 'point':
                card = point_card(visual.get('number', 1), visual['text'], width, scale, language, colors)
                placed.append({**visual, 'card': card, 'x': width / 2, 'y': height * (0.18 if vertical else 0.17), 'rtl': rtl})
            elif kind == 'picture':
                image = (pictures or {}).get(visual.get('id'))
                if image is None:
                    continue
                placed.append({**visual, 'card': cover(image.convert('RGB'), size), 'x': 0, 'y': 0})
        except Exception as error:  # one bad graphic never stops the edit
            print(f'Graphic skipped ({kind}):', str(error)[:200], file=sys.stderr)
    return placed


def render_clips(visuals, size, fps, folder, language, colors, pictures=None):
    """Encode every visual; returns the overlays for video_edit.render, pictures first."""
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    overlays = []
    for n, visual in enumerate(layout(visuals, size, language, colors, pictures)):
        path = folder / f'graphic-{n:03d}.mov'
        try:
            box = encode(visual, visual['card'], float(fps), size, path)
        except Exception as error:
            print('Graphic skipped:', str(error)[:200], file=sys.stderr)
            continue
        if box:
            overlays.append({'path': str(path), 'x': box[0], 'y': box[1], 'start': visual['start'], 'end': visual['end'],
                             'type': visual['type']})
    return sorted(overlays, key=lambda o: (o['type'] != 'picture', o['start']))


# ---------------------------------------------------------------------------------------------
# Pictures (B-roll) from Wikimedia Commons

def http_json(url, opener=urlopen, timeout=25):
    with opener(Request(url, headers={'User-Agent': USER_AGENT}), timeout=timeout) as response:
        return json.loads(response.read().decode())


def http_bytes(url, opener=urlopen, timeout=40, limit=12_000_000):
    with opener(Request(url, headers={'User-Agent': USER_AGENT}), timeout=timeout) as response:
        data = response.read(limit + 1)
    if len(data) > limit:
        raise ValueError('image too large')
    return data


def plain(html):
    return ' '.join(re.sub(r'<[^>]+>', ' ', str(html or '')).split())[:120]


def commons_search(query, opener=urlopen, limit=4):
    """Freely licensed photos for QUERY: [{'thumb', 'page', 'credit'}]."""
    params = {'action': 'query', 'format': 'json', 'generator': 'search', 'gsrnamespace': 6, 'gsrlimit': 12,
              'gsrsearch': f'filetype:bitmap {query}', 'prop': 'imageinfo', 'iiprop': 'url|size|mime|extmetadata',
              'iiurlwidth': 1280, 'iiextmetadatafilter': 'Artist|LicenseShortName'}
    data = http_json('https://commons.wikimedia.org/w/api.php?' + urlencode(params), opener)
    found = []
    for page in sorted((data.get('query') or {}).get('pages', {}).values(), key=lambda p: p.get('index', 99)):
        info = (page.get('imageinfo') or [{}])[0]
        width, height = info.get('width') or 0, info.get('height') or 0
        if info.get('mime') not in ('image/jpeg', 'image/png') or width < 900 or not height or not 0.45 <= width / height <= 2.4:
            continue
        thumb = info.get('thumburl') or ''
        if not thumb.startswith('https://upload.wikimedia.org/'):
            continue
        meta = info.get('extmetadata') or {}
        credit = plain((meta.get('Artist') or {}).get('value')) or 'Wikimedia Commons'
        license_name = plain((meta.get('LicenseShortName') or {}).get('value'))
        found.append({'thumb': thumb, 'page': info.get('descriptionurl', ''),
                      'credit': f'{credit} ({license_name})' if license_name else credit})
        if len(found) >= limit:
            break
    return found


def contact_sheet(images, cell=360):
    """Up to four candidates side by side, numbered 1-4, as PNG bytes."""
    sheet = Image.new('RGB', (cell * len(images) + 10 * (len(images) + 1), cell + 20), (30, 30, 30))
    draw = ImageDraw.Draw(sheet)
    font = load_font('title', 'en', 44)
    for n, image in enumerate(images):
        thumb = cover(image.convert('RGB'), (cell, cell))
        x = 10 + n * (cell + 10)
        sheet.paste(thumb, (x, 10))
        draw.rounded_rectangle((x + 8, 18, x + 64, 74), radius=12, fill=(0, 0, 0))
        draw.text((x + 36, 46), str(n + 1), font=font, fill=(255, 255, 255), anchor='mm')
    out = io.BytesIO()
    sheet.save(out, 'PNG')
    return out.getvalue()


def picture_prompt(requests, language_label):
    lines = '\n'.join(f'Sheet {n}: should show "{r.get("about") or r["query"]}" (searched for "{r["query"]}")'
                      for n, r in enumerate(requests, 1))
    return f"""You are choosing B-roll photos for a short video spoken in {language_label}. Each sheet shows up to
four numbered candidate photos from Wikimedia Commons.
{lines}

For each sheet pick the ONE photo that best and most literally shows what is needed, as a full-screen
cutaway in a professional short video. Answer 0 when none fits well. Always answer 0 for photos that
show text, logos, watermarks, maps or diagrams, are blurry, dark or low quality, show identifiable
people's faces, depict a prophet or any holy figure, or could offend a religious audience.

Answer with ONLY JSON like {{"1": 2, "2": 0}}."""


def choose_pictures(requests, ask_with_images, opener=urlopen, language_label='the original language'):
    """Search, show Claude the candidates, use the chosen photos.
    Returns ({request id: PIL image}, [{'title', 'url', 'credit'}] for the YouTube description)."""
    sheets, candidates = [], []
    for request in requests:
        try:
            found = commons_search(request['query'], opener)
            images = []
            for item in found:
                try:
                    images.append((item, Image.open(io.BytesIO(http_bytes(item['thumb'], opener))).convert('RGB')))
                except Exception:
                    continue
        except Exception as error:
            print('Picture search failed:', str(error)[:200], file=sys.stderr)
            images = []
        if images:
            sheets.append(request)
            candidates.append(images)
    if not sheets:
        return {}, []
    answer = ask_with_images(picture_prompt(sheets, language_label),
                             [contact_sheet([image for _, image in found]) for found in candidates])
    raw = re.sub(r'^```(?:json)?\s*|\s*```$', '', (answer or '').strip())
    start, end = raw.find('{'), raw.rfind('}')
    picks = json.loads(raw[start:end + 1]) if start >= 0 else {}
    chosen, credits = {}, []
    for n, (request, found) in enumerate(zip(sheets, candidates), 1):
        try:
            pick = int(picks.get(str(n), 0))
        except (TypeError, ValueError):
            pick = 0
        if 1 <= pick <= len(found):
            item, image = found[pick - 1]
            chosen[request['id']] = image
            title = re.sub(r'^File:|\.[A-Za-z]{3,4}$', '', item['page'].rsplit('/', 1)[-1]).replace('_', ' ')
            credits.append({'title': 'Photo: ' + (title or request['query']), 'url': item['page'], 'credit': item['credit']})
    return chosen, credits
