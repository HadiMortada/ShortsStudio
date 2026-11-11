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
from urllib.parse import urlencode, urlsplit
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
        check_commons_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def check_commons_url(url):
    parsed = urlsplit(url)
    if parsed.scheme != 'https' or parsed.hostname not in ('commons.wikimedia.org', 'upload.wikimedia.org') or parsed.username or parsed.password or parsed.port not in (None, 443):
        raise ValueError('Only verified Wikimedia media URLs may be downloaded')


def commons_fetch(url, limit):
    check_commons_url(url)
    with build_opener(RestrictedRedirect()).open(Request(url, headers={
            'User-Agent': 'ShortsStudio/0.6 (licensed-media personal video editor)'}), timeout=25) as response:
        data = response.read(limit + 1)
        if len(data) > limit:
            raise ValueError('Media download exceeds the size limit')
        return data


def plain(value):
    import html
    return html.unescape(re.sub('<[^>]+>', '', value))[:1500]


def fetch_commons(request, destination, fetch=commons_fetch):
    title = request['title']
    if not title.startswith('File:') or len(title) > 350:
        raise ValueError('Use the exact Wikimedia Commons File: title')
    query = urlencode({'action': 'query', 'format': 'json', 'prop': 'imageinfo',
        'iiprop': 'url|extmetadata|mime', 'titles': title})
    info = json.loads(fetch('https://commons.wikimedia.org/w/api.php?' + query, 2_000_000))
    pages = list(info.get('query', {}).get('pages', {}).values())
    item = pages[0].get('imageinfo', [{}])[0] if pages else {}
    meta = item.get('extmetadata', {})
    license_name = plain(meta.get('LicenseShortName', {}).get('value', ''))
    # Keep provenance and attribution; never infer permission from search thumbnails.
    if not re.fullmatch(r'(?:CC0|Public domain|CC BY(?:-SA)? [1-4]\.0)', license_name, re.I):
        raise ValueError('No supported reusable license was verified for ' + title)
    url = item.get('url', '')
    check_commons_url(url)
    mime = item.get('mime', '')
    kind = 'image' if mime.startswith('image/') else 'video' if mime.startswith('video/') else ''
    if not kind:
        raise ValueError('This Commons file is not an image or video')
    if request['kind'] != kind:
        raise ValueError('Requested media kind does not match the Commons file')
    normalize_media(fetch(url, MAX_FILE), destination, kind)
    return {'title': title, 'url': item.get('descriptionurl', 'https://commons.wikimedia.org/wiki/' + title),
            'credit': plain(meta.get('Artist', {}).get('value', '')) + ' · ' + license_name +
                      ' · ' + plain(meta.get('LicenseUrl', {}).get('value', '')) + ' · Cropped/animated for this film',
            'license': license_name, 'asset': 'assets/' + destination.name}
