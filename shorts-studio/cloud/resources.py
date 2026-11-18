"""Bounded reference folders, text extraction, and licensed Wikimedia assets."""
import hashlib
import io
import json
import re
import shutil
import stat
import subprocess
import zipfile
from pathlib import Path, PurePosixPath
from urllib.parse import quote, urlencode, urlsplit
from urllib.request import Request, build_opener, HTTPRedirectHandler
from xml.etree import ElementTree

MAX_FILE = 35 * 1024 * 1024
MAX_BUNDLE = 100 * 1024 * 1024
TEXT_SUFFIXES = {'.txt', '.md', '.json', '.csv', '.srt', '.pdf', '.docx'}
IMAGE_SUFFIXES = {'.png', '.jpg', '.jpeg', '.webp'}
VIDEO_SUFFIXES = {'.mp4', '.webm', '.mov'}


def identifier(value):
    if not re.fullmatch(r'[a-f0-9]{24}', value):
        raise ValueError('Invalid reference folder')
    return value


def safe_name(value):
    p = PurePosixPath(value.replace('\\', '/'))
    if p.is_absolute() or any(x in ('..', '') for x in p.parts) or ':' in value:
        raise ValueError('Unsafe filename in folder')
    return str(p)[:300]


def extract_text(name, data):
    suffix = Path(name).suffix.lower()
    if suffix == '.pdf':
        from pypdf import PdfReader
        pdf = PdfReader(io.BytesIO(data))
        if pdf.is_encrypted:
            raise ValueError('Password-protected PDF cannot be read')
        chunks, count = [], 0
        for n, page in enumerate(pdf.pages[:300]):
            text = page.extract_text() or ''
            chunks.append(f'[Page {n+1}]\n{text}')
            count += len(text)
            if count > 180000:
                break
        result = '\n'.join(chunks)
        if not any(c.isalpha() for c in ''.join(x.split(']', 1)[-1] for x in chunks)):
            raise ValueError('This PDF has no readable text; upload a text PDF or extracted text')
        return result[:180000]
    if suffix == '.docx':
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            info = archive.getinfo('word/document.xml')
            if info.file_size > 8 * 1024 * 1024:
                raise ValueError('Word document is too large')
            root = ElementTree.fromstring(archive.read(info))
            ns = '{http://schemas.openxmlformats.org/wordprocessingml/2006/main}'
            return '\n'.join(''.join(x.itertext()) for x in root.iter(ns + 'p'))[:180000]
    try:
        return data.decode('utf-8-sig')[:180000]
    except UnicodeDecodeError:
        raise ValueError('Text files must use UTF-8 encoding') from None


def normalize_media(data, output, kind):
    if len(data) > MAX_FILE:
        raise ValueError('Media file exceeds 35 MB')
    if kind == 'image':
        from PIL import Image, ImageOps
        with Image.open(io.BytesIO(data)) as image:
            if image.width * image.height > 30_000_000:
                raise ValueError('Image dimensions are too large')
            image = ImageOps.exif_transpose(image).convert('RGBA')
            image.thumbnail((2160, 3840))
            image.save(output, 'PNG')
    else:
        raw = output.with_suffix('.input')
        raw.write_bytes(data)
        try:
            probe = json.loads(subprocess.check_output(['ffprobe', '-v', 'error', '-show_format',
                '-show_streams', '-of', 'json', str(raw)], timeout=20))
            duration = float(probe['format']['duration'])
            videos = [s for s in probe['streams'] if s['codec_type'] == 'video']
            if not 0 < duration <= 120 or not videos or videos[0]['width'] * videos[0]['height'] > 9_000_000:
                raise ValueError('Use a video clip up to 120 seconds and 4K')
            subprocess.run(['ffmpeg', '-v', 'error', '-y', '-i', str(raw), '-map', '0:v:0', '-an',
                '-vf', 'scale=1080:1920:force_original_aspect_ratio=decrease:force_divisible_by=2',
                '-c:v', 'libx264', '-preset', 'fast', '-crf', '22', '-movflags', '+faststart',
                str(output)], check=True, timeout=180, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        finally:
            raw.unlink(missing_ok=True)


class ResourceStore:
    def __init__(self, root):
        self.root = Path(root) / 'resources'
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)

    def ingest(self, files, rights=False):
        import secrets
        if not rights:
            raise ValueError('Confirm that you may use the uploaded materials')
        expanded, total = [], 0
        for name, data in files:
            name = safe_name(name)
            if len(data) > MAX_FILE:
                raise ValueError('Each upload must be at most 35 MB')
            if name.lower().endswith('.zip'):
                with zipfile.ZipFile(io.BytesIO(data)) as archive:
                    for entry in archive.infolist():
                        safe_name(entry.filename)
                        if stat.S_ISLNK(entry.external_attr >> 16):
                            raise ValueError('Folder archives cannot contain symbolic links')
                        if entry.is_dir():
                            continue
                        if entry.file_size > MAX_FILE or total + entry.file_size > MAX_BUNDLE:
                            raise ValueError('Expanded reference folder exceeds 100 MB')
                        total += entry.file_size
                        if len(expanded) >= 100:
                            raise ValueError('Use at most 100 files per folder')
                        expanded.append((entry.filename, archive.read(entry)))
            else:
                total += len(data)
                expanded.append((name, data))
            if total > MAX_BUNDLE or len(expanded) > 100:
                raise ValueError('Use at most 100 files and 100 MB per folder')
        key = secrets.token_hex(12)
        folder = self.root / key
        folder.mkdir(mode=0o700)
        manifest = {'id': key, 'files': [], 'warnings': []}
        try:
            chars = 0
            for index, (name, data) in enumerate(expanded):
                suffix = Path(name).suffix.lower()
                item = {'name': name, 'id': f'upload_{index}', 'source': 'User-provided',
                        'license': 'User confirmed usage rights'}
                if suffix in TEXT_SUFFIXES:
                    text = extract_text(name, data)
                    if not text.strip():
                        manifest['warnings'].append(name + ': no readable text')
                        continue
                    limit = max(0, 180000 - chars)
                    item.update(kind='text', text=text[:limit])
                    if len(text) > limit:
                        manifest['warnings'].append(name + ': text truncated at the folder context limit')
                    chars += len(item['text'])
                elif suffix in IMAGE_SUFFIXES | VIDEO_SUFFIXES:
                    kind = 'image' if suffix in IMAGE_SUFFIXES else 'video'
                    filename = item['id'] + ('.png' if kind == 'image' else '.mp4')
                    normalize_media(data, folder / filename, kind)
                    item.update(kind=kind, file=filename)
                else:
                    manifest['warnings'].append(name + ': unsupported file type')
                    continue
                manifest['files'].append(item)
            if not manifest['files']:
                raise ValueError('No usable files. Upload PDF, Word, UTF-8 text, images or video clips.')
            (folder / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False))
            return self.summary(manifest)
        except Exception as error:
            shutil.rmtree(folder)
            if isinstance(error, ValueError):
                raise
            raise ValueError('A reference file could not be read. Use valid PDFs, Word documents, text or supported media.') from None

    @staticmethod
    def summary(manifest):
        return {**manifest, 'files': [{k: v for k, v in x.items() if k != 'text'} for x in manifest['files']]}

    def list(self):
        return [self.summary(json.loads(p.read_text())) for p in sorted(self.root.glob('*/manifest.json'))]

    def context(self, keys, public):
        public.mkdir(parents=True, exist_ok=True)
        context, citations, remaining = [], [], 180000
        for key in keys:
            folder = self.root / identifier(key)
            try:
                manifest = json.loads((folder / 'manifest.json').read_text())
            except FileNotFoundError:
                raise ValueError('A selected reference folder no longer exists') from None
            for item in manifest['files']:
                name = key[:6] + '_' + item['id']
                if item['kind'] == 'text':
                    text = item['text'][:remaining]
                    remaining -= len(text)
                    context.append({'name': item['name'], 'text': text,
                                    'truncated': len(text) < len(item['text'])})
                else:
                    file = name + Path(item['file']).suffix
                    shutil.copyfile(folder / item['file'], public / file)
                    context.append({'name': item['name'], 'kind': item['kind'], 'asset': 'assets/' + file})
                citations.append({'title': item['name'], 'url': '', 'credit': item['license']})
        return context, citations


class RestrictedRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        check_media_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


# Free archives films may use, and only these hosts: Wikimedia Commons (any subject) and the
# NASA Image and Video Library (space and Earth science; NASA media is generally public domain).
MEDIA_HOSTS = ('commons.wikimedia.org', 'upload.wikimedia.org', 'thumb.wikimedia.org',  # Commons serves resized copies here too
               'images-api.nasa.gov', 'images-assets.nasa.gov')
REUSABLE = re.compile(r'(?:CC0(?: 1\.0)?|Public domain|PD[- ].*|CC BY(?:-SA)? [1-4]\.0(?: [a-z]{2,3})?)', re.I)


def check_media_url(url):
    parsed = urlsplit(url)
    if parsed.scheme != 'https' or parsed.hostname not in MEDIA_HOSTS or parsed.username or parsed.password or parsed.port not in (None, 443):
        raise ValueError('Only verified Wikimedia or NASA media URLs may be downloaded')


check_commons_url = check_media_url  # older name


def user_agent(source=None):
    """Wikimedia refuses automated downloads that do not name the tool and a way to reach its owner
    (https://meta.wikimedia.org/wiki/User-Agent_policy): name this app and its own public repository."""
    import os
    source = os.environ if source is None else source
    repo = str(source.get('GITHUB_REPOSITORY', '') or '')
    contact = 'https://github.com/' + repo if re.fullmatch(r'[\w.-]+/[\w.-]+', repo) else 'personal video editor'
    return f'ShortsStudio/0.8 ({contact}; licensed media with credits)'


def commons_fetch(url, limit):
    check_media_url(url)
    with build_opener(RestrictedRedirect()).open(Request(url, headers={'User-Agent': user_agent()}), timeout=25) as response:
        data = response.read(limit + 1)
        if len(data) > limit:
            raise ValueError('Media download exceeds the size limit')
        return data


media_fetch = commons_fetch


def plain(value):
    import html
    return html.unescape(re.sub('<[^>]+>', '', value))[:1500]


def _commons_file(title, item, kind, destination, fetch):
    """Verify one Commons file's license and kind, then download it (a 2000 px copy for photos)."""
    meta = item.get('extmetadata', {})
    license_name = plain(meta.get('LicenseShortName', {}).get('value', ''))
    # Keep provenance and attribution; never infer permission from search thumbnails.
    if not REUSABLE.fullmatch(license_name.strip()):
        raise ValueError('No supported reusable license was verified for ' + title)
    mime = item.get('mime', '')
    if mime.startswith('image/'):
        found = 'image' if mime != 'image/svg+xml' or item.get('thumburl') else ''  # drawings come as a PNG copy
    else:
        found = 'video' if mime.startswith('video/') or mime == 'application/ogg' else ''
    if not found:
        raise ValueError('This Commons file is not an image or video')
    if kind != found:
        raise ValueError('Requested media kind does not match the Commons file')
    url = (item.get('thumburl') if kind == 'image' else '') or item.get('url', '')
    check_media_url(url)
    normalize_media(fetch(url, MAX_FILE), destination, kind)
    return {'title': title, 'url': item.get('descriptionurl', 'https://commons.wikimedia.org/wiki/' + title.replace(' ', '_')),
            'credit': plain(meta.get('Artist', {}).get('value', '')) + ' · ' + license_name +
                      ' · ' + plain(meta.get('LicenseUrl', {}).get('value', '')) + ' · Cropped/animated for this film',
            'license': license_name, 'asset': 'assets/' + destination.name, 'source': 'Wikimedia Commons'}


COMMONS_INFO = {'prop': 'imageinfo', 'iiprop': 'url|extmetadata|mime|size', 'iiurlwidth': '2000',
                'iiextmetadatafilter': 'LicenseShortName|Artist|LicenseUrl'}


def _commons_exact(title, kind, destination, fetch):
    if not title.startswith('File:') or len(title) > 350:
        raise ValueError('Use the exact Wikimedia Commons File: title')
    info = json.loads(fetch('https://commons.wikimedia.org/w/api.php?' + urlencode(
        {'action': 'query', 'format': 'json', 'titles': title, **COMMONS_INFO}), 2_000_000))
    pages = list(info.get('query', {}).get('pages', {}).values())
    item = pages[0].get('imageinfo', [{}])[0] if pages else {}
    return _commons_file(title, item, kind, destination, fetch)


def _commons_search(query, kind, destination, fetch, tries=8):
    """The best freely licensed Commons match for a few search words."""
    words = ' '.join(str(query).split())[:200]
    info = json.loads(fetch('https://commons.wikimedia.org/w/api.php?' + urlencode(
        {'action': 'query', 'format': 'json', 'generator': 'search', 'gsrnamespace': '6', 'gsrlimit': str(tries),
         'gsrsearch': words + (' filetype:bitmap' if kind == 'image' else ' filetype:video'), **COMMONS_INFO}), 2_000_000))
    pages = sorted(info.get('query', {}).get('pages', {}).values(), key=lambda p: p.get('index', 99))
    problem = 'No freely licensed Commons match for "' + words + '"'
    for page in pages:
        item = (page.get('imageinfo') or [{}])[0]
        if kind == 'image' and min(item.get('width', 0), item.get('height', 0)) < 500:
            continue  # too small to fill a phone screen
        try:
            return _commons_file(page.get('title', ''), item, kind, destination, fetch)
        except Exception as error:
            problem = f'{type(error).__name__}: {str(error)[:200]}'
    raise ValueError(problem)


NASA_NOT_FREE = re.compile(r'©|copyright|getty|associated press|\bap photo|reuters|courtesy of(?! nasa)', re.I)


def _nasa_search(query, destination, fetch, tries=6):
    """A NASA Image Library photo for a few search words (public domain; credited to NASA)."""
    words = ' '.join(str(query).split())[:200]
    data = json.loads(fetch('https://images-api.nasa.gov/search?' + urlencode(
        {'q': words, 'media_type': 'image', 'page_size': str(tries)}), 3_000_000))
    for entry in (data.get('collection', {}).get('items') or [])[:tries]:
        meta = (entry.get('data') or [{}])[0]
        nasa_id = str(meta.get('nasa_id', ''))
        about = ' '.join(str(meta.get(k, '')) for k in ('description', 'photographer', 'secondary_creator', 'title'))
        if not re.fullmatch(r'[\w.\-]{2,120}', nasa_id) or NASA_NOT_FREE.search(about):
            continue  # third-party photos in the NASA library are not public domain
        base = f'https://images-assets.nasa.gov/image/{quote(nasa_id)}/{quote(nasa_id)}'
        for size in ('~large.jpg', '~medium.jpg', '~orig.jpg'):
            try:
                normalize_media(fetch(base + size, MAX_FILE), destination, 'image')
            except Exception:
                continue
            title = plain(str(meta.get('title') or nasa_id))[:200]
            who = plain(str(meta.get('photographer') or meta.get('center') or ''))[:120]
            return {'title': title, 'url': f'https://images.nasa.gov/details/{quote(nasa_id)}',
                    'credit': 'NASA' + (' / ' + who if who else '') + ' · Public domain · Cropped/animated for this film',
                    'license': 'Public domain (NASA)', 'asset': 'assets/' + destination.name, 'source': 'NASA'}
    raise ValueError('No NASA image found for "' + words + '"')


def fetch_media(request, destination, fetch=media_fetch):
    """A real photo or clip for the film, with its license verified and credit kept. Tries the exact
    Commons file Claude named, then a search of the archive Claude chose, then the other archive."""
    kind, title = request.get('kind', 'image'), str(request.get('title') or '').strip()
    query = str(request.get('query') or '').strip() or re.sub(r'^File:|\.[a-z0-9]{2,4}$|[_()]', ' ', title, flags=re.I).strip()
    nasa_first = request.get('source') == 'nasa'
    steps = []
    if title.startswith('File:'):
        steps.append(lambda: _commons_exact(title, kind, destination, fetch))
    if query:
        commons = lambda: _commons_search(query, kind, destination, fetch)
        nasa = lambda: _nasa_search(query, destination, fetch)
        steps += ([nasa, commons] if nasa_first else [commons]) if kind == 'image' else [commons]
        if kind == 'image' and not nasa_first:
            steps.append(nasa)
    problems = [] if steps else ['Give a search query or an exact Commons File: title']
    for step in steps:
        try:
            return step()
        except Exception as error:
            destination.unlink(missing_ok=True)
            problems.append(f'{type(error).__name__}: {str(error)[:200]}')
    raise ValueError(' | '.join(problems))


def fetch_commons(request, destination, fetch=commons_fetch):
    """Older entry point: an exact Commons title only."""
    return _commons_exact(request['title'], request['kind'], destination, fetch)
