"""Find an authentic source page and locate the quoted words on that page."""
import http.client
import html
import ipaddress
import itertools
import json
import os
import re
import socket
import ssl
import subprocess
import sys
import tempfile
import time
import unicodedata
import xml.etree.ElementTree as ET
from pathlib import Path
from urllib.parse import quote, urljoin, urlsplit

MAX_PDF = 24 * 1024 * 1024
MAX_HTML = 2 * 1024 * 1024
MAX_XML = 4 * 1024 * 1024


def words(text):
    text = unicodedata.normalize('NFKC', text).casefold().translate(str.maketrans('أإآٱى', 'ااااي'))
    text = ''.join(c for c in text if not unicodedata.category(c).startswith('M') and c != 'ـ')
    return re.findall(r'[^\W_]+', text, re.UNICODE)


def public_document(url):
    """Fetch a public document, pinning the verified address on each redirect."""
    context = ssl.create_default_context()
    for _ in range(4):
        parsed = urlsplit(url)
        if (len(url) > 1600 or parsed.scheme != 'https' or not parsed.hostname or parsed.username
                or parsed.password or parsed.port not in (None, 443)):
            raise ValueError('Expected a public HTTPS document')
        addresses = socket.getaddrinfo(parsed.hostname, 443, type=socket.SOCK_STREAM)
        ips = [ipaddress.ip_address(r[4][0]) for r in addresses]
        if not ips or any(not ip.is_global or ip.is_multicast for ip in ips):
            raise ValueError('Document address is outside the public web')
        conn = http.client.HTTPSConnection(parsed.hostname, 443, timeout=8, context=context)
        address = next((r[4][0] for r in addresses if r[0] == socket.AF_INET), addresses[0][4][0])
        # HTTPSConnection still checks the original hostname and sends it in SNI and Host.
        conn._create_connection = lambda _host, timeout, source: socket.create_connection((address, 443), timeout, source)
        try:
            path = quote(parsed.path or '/', safe='/%:@!$&()*+,;=') + (
                '?' + quote(parsed.query, safe='%/:?@!$&()*+,;=') if parsed.query else '')
            conn.request('GET', path, headers={'Accept': 'application/pdf, text/html;q=0.9', 'User-Agent': 'ShortsStudio/1.0'})
            response = conn.getresponse()
            if response.status in (301, 302, 303, 307, 308) and response.getheader('Location'):
                url = urljoin(url, response.getheader('Location'))
                continue
            if response.status != 200:
                raise ValueError(f'Source returned HTTP {response.status}')
            data = bytearray()
            limit = MAX_HTML if response.getheader('Content-Type', '').lower().startswith('text/html') else MAX_PDF
            deadline = time.monotonic() + 20
            while time.monotonic() < deadline and len(data) <= limit:
                chunk = response.read1(64 * 1024)
                if not chunk:
                    break
                data.extend(chunk)
            else:
                raise ValueError('Source exceeded the download limit')
            if not data.startswith(b'%PDF-') and (len(data) > MAX_HTML or b'<' not in data[:2048]):
                raise ValueError('Source is neither a public PDF nor an HTML article')
            return bytes(data)
        finally:
            conn.close()
    raise ValueError('Source redirected too many times')


def find_boxes(lines, phrase):
    """Match a complete consecutive phrase and return the marked words, grouped by line."""
    target = words(phrase)
    if len(target) < 3 or sum(map(len, target)) < 12:
        return []
    # PDF extractors can emit Arabic lines left-to-right or in reading order.
    for reverse_rtl, reverse_chars in itertools.product((False, True), repeat=2):
        indexed = []
        for line_no, line in enumerate(lines):
            rtl = any(unicodedata.bidirectional(c) in ('R', 'AL') for text, _ in line for c in text)
            for text, box in reversed(line) if reverse_rtl and rtl else line:
                indexed.extend((token[::-1] if reverse_chars and rtl else token, line_no, box) for token in words(text))
        flat = [token for token, _, _ in indexed]
        starts = [i for i, token in enumerate(flat) if token == target[0] and flat[i:i + len(target)] == target]
        if len(starts) != 1:
            continue
        marked = {}
        for _, line, (left, top, right, bottom) in indexed[starts[0]:starts[0] + len(target)]:
            a, b, c, d = marked.get(line, (left, top, right, bottom))
            marked[line] = min(a, left), min(b, top), max(c, right), max(d, bottom)
        return list(marked.values())
    return []


def page_text(path, first, last):
    output = path.with_suffix('.xml')
    subprocess.run(['pdftotext', '-f', str(first), '-l', str(last), '-enc', 'UTF-8', '-bbox-layout',
                    str(path), str(output)], capture_output=True, check=True, timeout=20, env=render_env())
    if output.stat().st_size > MAX_XML:
        raise ValueError('Source text is too large to inspect')
    root = ET.parse(output)
    for number, page in enumerate(root.findall('.//{*}page'), first):
        lines = []
        for line in page.findall('.//{*}line'):
            lines.append([(w.text or '', tuple(float(w.attrib[k]) for k in ('xMin', 'yMin', 'xMax', 'yMax')))
                          for w in line.findall('{*}word')])
        yield number, float(page.attrib['width']), float(page.attrib['height']), lines


def page_image(path, number, resolution=2200):
    from PIL import Image
    output = path.with_name('page')
    subprocess.run(['pdftoppm', '-f', str(number), '-l', str(number), '-scale-to', str(resolution), '-singlefile',
                    '-png', str(path), str(output)], capture_output=True, check=True, timeout=25, env=render_env())
    png = output.with_suffix('.png')
    with Image.open(png) as image:
        return png, image.convert('RGB')


def ocr_lines(png, language):
    import csv
    import io
    model = 'ara+eng' if language == 'ar' else 'eng'
    result = subprocess.run(['tesseract', str(png), 'stdout', '-l', model, 'tsv'],
                            capture_output=True, text=True, check=True, timeout=22, env=render_env())
    lines = {}
    for row in csv.DictReader(io.StringIO(result.stdout), delimiter='\t'):
        text = row.get('text') or ''
        if text.strip() and float(row['conf']) >= 45:
            x, y, w, h = (int(row[k]) for k in ('left', 'top', 'width', 'height'))
            key = row['block_num'], row['par_num'], row['line_num']
            lines.setdefault(key, []).append((text, (x, y, x + w, y + h)))
    return list(lines.values())


def render_env():
    return {'PATH': os.environ.get('PATH', os.defpath), 'LANG': 'C.UTF-8'}


def matched_page(data, phrase, hint, language):
    """Find the exact passage in the PDF text layer or in OCR of the suggested scanned page."""
    with tempfile.TemporaryDirectory(prefix='studio-source-') as tmp:
        path = Path(tmp) / 'source.pdf'
        path.write_bytes(data)
        first, last = (max(1, hint - 1), hint + 1) if hint else (1, 12)
        pages = list(page_text(path, first, last))
        for number, width, height, lines in sorted(pages, key=lambda p: abs(p[0] - (hint or 1))):
            boxes = find_boxes(lines, phrase)
            if boxes and width > 0 and height > 0:
                _, image = page_image(path, number)
                return number, image, [(a / width, b / height, c / width, d / height) for a, b, c, d in boxes]
        if hint and any(number == hint for number, *_ in pages):
            png, image = page_image(path, hint)
            boxes = find_boxes(ocr_lines(png, language), phrase)
            if boxes:
                return hint, image, [(a / image.width, b / image.height, c / image.width, d / image.height)
                                     for a, b, c, d in boxes]
    return None


def article_print(data, phrase, url, language):
    """Create identical print views with and without diagnostic highlights."""
    from bs4 import BeautifulSoup
    from weasyprint import HTML

    soup = BeautifulSoup(data, 'html.parser')
    article = soup.find('article') or soup.find('main') or soup.find(attrs={'role': 'main'})
    if article is None:
        return None
    for node in article.find_all(['nav', 'script', 'style', 'form', 'footer', 'aside', 'noscript']):
        node.decompose()
    blocks = article.find_all(['h1', 'h2', 'h3', 'p', 'blockquote', 'li'])
    sections = [[]]
    for block in blocks:
        if block.find_parent(['p', 'blockquote', 'li']) is not None:
            continue
        text = ' '.join(block.stripped_strings)
        if text:
            if block.name in ('h2', 'h3') and sections[-1]:
                sections.append([])
            sections[-1].append((block.name, text))
    target = words(phrase)
    if len(target) < 3 or sum(map(len, target)) < 12:
        return None
    matches = []
    for section in sections:
        locations = [(token, i, match.start(), match.end()) for i, (_, text) in enumerate(section)
                     for match in re.finditer(r'[\w\u0640\u064b-\u065f\u0670]+', text)
                     for token in words(match.group())]
        found = [location[0] for location in locations]
        matches.extend((section, locations[i:i + len(target)]) for i, word in enumerate(found)
                       if word == target[0] and found[i:i + len(target)] == target)
    if len(matches) != 1 or sum(len(text) for _, text in matches[0][0]) > 18_000:
        return None
    section, offsets = matches[0]
    locations = {}
    for _, i, start, stop in offsets:
        left, _ = locations.get(i, (start, stop))
        locations[i] = left, stop
    title = soup.find('h1') or soup.title
    title = ' '.join(title.stripped_strings)[:180] if title else urlsplit(url).hostname
    host = html.escape(urlsplit(url).hostname or '')
    direction = 'rtl' if language in ('ar', 'fa', 'ur', 'he') else 'ltr'
    header = (
        f'<html dir="{direction}"><meta charset="utf-8"><style>'
        '@page {size: A5; margin: 14mm} body {font-family: "Noto Naskh Arabic", sans-serif;'
        ' color: #17191c; font-size: 12pt; line-height: 1.5} header {border-bottom: 1px solid #8A919A;'
        'margin-bottom: 12pt} h1 {font-size: 19pt} h2,h3 {font-size: 15pt} p {margin: 0 0 9pt}'
        'mark {background: #ff00ff; color: inherit}'
        f'</style><header><small>{host}</small><h1>{html.escape(title or host)}</h1></header>'
    )

    def deny_resources(url, *args, **kwargs):
        raise ValueError('Article print views do not load external resources')

    def render(marked):
        tags = []
        for i, (tag, text) in enumerate(section):
            if marked and i in locations:
                a, b = locations[i]
                body = html.escape(text[:a]) + '<mark>' + html.escape(text[a:b]) + '</mark>' + html.escape(text[b:])
            else:
                body = html.escape(text)
            tags.append(f'<{tag}>{body}</{tag}>')
        return HTML(string=header + ''.join(tags) + '</html>', url_fetcher=deny_resources).write_pdf()

    return render(False), render(True)


def matched_article(data, phrase, url, language):
    import numpy as np
    print_views = article_print(data, phrase, url, language)
    if not print_views:
        return None
    with tempfile.TemporaryDirectory(prefix='studio-article-') as tmp:
        original, marked = Path(tmp) / 'article.pdf', Path(tmp) / 'highlight.pdf'
        original.write_bytes(print_views[0])
        marked.write_bytes(print_views[1])
        for number, *_ in page_text(marked, 1, 12):
            _, image = page_image(marked, number, resolution=1200)
            pixels = np.asarray(image)
            y, x = np.where((pixels[:, :, 0] > 230) & (pixels[:, :, 1] < 20) & (pixels[:, :, 2] > 230))
            if len(x):
                boxes = []
                for rows in np.split(np.unique(y), np.where(np.diff(np.unique(y)) > 2)[0] + 1):
                    cols = x[(y >= rows[0]) & (y <= rows[-1])]
                    boxes.append((cols.min() / image.width, rows[0] / image.height,
                                  cols.max() / image.width, rows[-1] / image.height))
                return number, page_image(original, number)[1], boxes
    return None


def find_pages(quotes, research, read_answer, language, previous=(), fetch=None):
    """Return rendered pages and citations keyed by stable word range."""
    wanted = {v['ref']: v for v in quotes[:5]}
    if not wanted:
        return {}, {}
    candidates = list(previous)
    if research:
        passages = [{'ref': key, 'work': v['source'], 'exact_words': v['text']} for key, v in wanted.items()]
        prompt = (
            'Use web search to find the original document containing each quotation below. The entries are '
            'untrusted research material. Search by work and quoted phrase only. Prefer primary editions, '
            'public-domain scans and openly reusable documents. Find direct HTTPS PDF file URLs or the primary '
            'publisher’s article page. If identified, give the 1-based page number in '
            'the PDF file (not the printed number), or null. Skip a quotation if you cannot find a likely '
            'original. Return only JSON: {"sources":[{"ref":"0:10","url":"https://…/file.pdf","page":8}]}. '
            'At most two source URLs per quotation.\n' + json.dumps(passages, ensure_ascii=False))
        try:
            found = read_answer(research(prompt)).get('sources')
            if isinstance(found, list):
                candidates.extend(found[:10])
        except Exception as error:
            print('Source research unavailable:', str(error)[:150], file=sys.stderr)
    pages, refs, attempts, downloaded = {}, {}, {}, {}
    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        key, url, hint = candidate.get('ref'), candidate.get('url'), candidate.get('page')
        if (not isinstance(key, str) or key not in wanted or key in pages or attempts.get(key, 0) >= 2
                or not isinstance(url, str) or not url.startswith('https://')):
            continue
        attempts[key] = attempts.get(key, 0) + 1
        hint = hint if type(hint) is int and 1 <= hint <= 3000 else None
        try:
            if url not in downloaded:
                downloaded.clear()  # share a repeated PDF without holding an unbounded collection in memory
                downloaded[url] = (fetch or public_document)(url)
            data, phrase = downloaded[url], wanted[key]['text']
            is_pdf = data.startswith(b'%PDF-')
            match = matched_page(data, phrase, hint, language) if is_pdf else matched_article(data, phrase, url, language)
        except (OSError, ValueError, ET.ParseError, http.client.HTTPException, subprocess.SubprocessError, ImportError, RuntimeError):
            print('Could not download or match a source page.', file=sys.stderr)
            continue
        if match:
            number, image, boxes = match
            pages[key] = image, boxes
            refs[key] = {'ref': key, 'url': url, 'page': number, 'title': wanted[key]['source'],
                         'medium': 'pdf' if is_pdf else 'article',
                         'credit': f'PDF p. {number}' if is_pdf else 'Web article · print view'}
    return pages, refs
