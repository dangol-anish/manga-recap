"""Small, independent HTML adapters for the two approved download sources."""
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit
import ipaddress
import re
import socket
from decimal import Decimal

import httpx

SOURCES = {'mangapill.com': 'MangaPill', 'weebcentral.com': 'WeebCentral'}
HEADERS = {'User-Agent': 'Mozilla/5.0', 'Accept': 'text/html,image/*;q=0.9,*/*;q=0.8'}
MAX_HTML = 12 * 1024 * 1024
MAX_IMAGE = 40 * 1024 * 1024


def source_url(value):
    p = urlsplit(value.strip())
    host = (p.hostname or '').lower().removeprefix('www.')
    if p.scheme != 'https' or p.username or p.password or p.port not in (None, 443) or host not in SOURCES:
        raise ValueError('Use an HTTPS manga URL from mangapill.com or weebcentral.com.')
    expected = '/manga/' if host == 'mangapill.com' else '/series/'
    if not p.path.startswith(expected) or not p.path[len(expected):].split('/')[0]:
        raise ValueError(f'Paste a manga series URL containing {expected}, not a chapter URL.')
    path = p.path.rstrip('/')
    if host == 'weebcentral.com':
        ident = p.path.split('/')[2]
        if not re.fullmatch(r'[A-Za-z0-9]{26}', ident):
            raise ValueError('Paste a specific WeebCentral series URL, not its random-series link.')
        path = '/series/' + ident
    return f'https://{host}{path}', host


def public_url(value):
    p = urlsplit(value)
    if p.scheme != 'https' or not p.hostname or p.username or p.password or p.port not in (None, 443):
        raise ValueError('The source returned an unsupported image or redirect URL.')
    addresses = socket.getaddrinfo(p.hostname, 443, type=socket.SOCK_STREAM)
    if not addresses or any(not ipaddress.ip_address(a[4][0]).is_global for a in addresses):
        raise ValueError('The source returned a non-public network address.')
    return value


class Markup(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.links, self.images = [], []
        self.title, self.og_title = '', ''
        self.anchor = None
        self.heading = False
        self.heading_done = False

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == 'h1' and not self.heading_done: self.heading = True
        if tag == 'meta' and attrs.get('property') == 'og:title': self.og_title = attrs.get('content', '')
        if tag == 'a': self.anchor = [attrs.get('href', ''), '']
        if tag == 'img': self.images.append(attrs)
        if self.anchor and tag in ('br', 'span', 'div'): self.anchor[1] += ' '

    def handle_data(self, data):
        if self.heading: self.title += data
        if self.anchor: self.anchor[1] += data

    def handle_endtag(self, tag):
        if tag == 'h1' and self.heading:
            self.heading = False
            self.heading_done = True
        if tag == 'a' and self.anchor:
            self.links.append(tuple(self.anchor))
            self.anchor = None


def parse(html):
    result = Markup()
    result.feed(html)
    return result


def chapter_sort(item):
    number = re.search(r'(?:chapter|episode|ch\.?)\s*#?\s*(\d+(?:\.\d+)?)', item['name'], re.I)
    if number: return (0, Decimal(number[1]), item['id'])
    return (1, item['name'].casefold(), item['id'])


def chapter_list(html, base):
    result, seen = [], set()
    host = urlsplit(base).hostname
    for href, text in parse(html).links:
        url = urljoin(base, href)
        p = urlsplit(url)
        if p.hostname != host or not p.path.startswith('/chapters/') or p.scheme != 'https': continue
        ident = p.path.rstrip('/').split('/')[-1]
        if not ident or ident in seen: continue
        seen.add(ident)
        name = ' '.join(text.split()).split('Last Read', 1)[0].strip()
        # WeebCentral's links may also contain dates and page counts.
        if host == 'weebcentral.com':
            match = re.search(r'(?:Chapter|Episode)\s*\d+(?:\.\d+)?', name, re.I)
            if match: name = match[0]
        result.append({'id': ident, 'name': name or ident, 'url': f'https://{host}{p.path}'})
    return sorted(result, key=chapter_sort)


async def fetch(client, url, referer, limit=MAX_HTML):
    """Validate every redirect; bound response size before decoding/writing."""
    for _ in range(6):
        public_url(url)
        async with client.stream('GET', url, headers={**HEADERS, 'Referer': referer}) as response:
            if response.is_redirect:
                location = response.headers.get('location')
                if not location: raise ValueError('Source redirect has no destination.')
                url = urljoin(url, location)
                continue
            if response.status_code in (403, 429):
                raise ValueError(f'Source returned HTTP {response.status_code}. Wait and retry; access may be restricted.')
            response.raise_for_status()
            data = bytearray()
            async for chunk in response.aiter_bytes():
                data.extend(chunk)
                if len(data) > limit: raise ValueError('Source response exceeds the download size limit.')
            return bytes(data)
    raise ValueError('Too many redirects from source.')


async def catalog(url):
    url, host = source_url(url)
    async with httpx.AsyncClient(timeout=httpx.Timeout(20, connect=10), follow_redirects=False) as client:
        page = parse((await fetch(client, url, f'https://{host}/')).decode('utf-8', 'replace'))
        html = (await fetch(client, url.rstrip('/') + '/full-chapter-list', url)).decode('utf-8', 'replace') if host == 'weebcentral.com' else None
        chapters = chapter_list(html, url) if html is not None else chapter_list_from_page(page, url)
        title = ' '.join((page.title or page.og_title).split())
        if not title or not chapters:
            raise ValueError('No manga title or chapters found. The source layout may have changed or blocked access.')
        return {'title': title, 'url': url, 'source': SOURCES[host], 'host': host, 'chapters': chapters}


def chapter_list_from_page(page, base):
    # Reuse identical parsing rules without constructing HTML from untrusted text.
    result, seen = [], set()
    for href, text in page.links:
        url = urljoin(base, href)
        p = urlsplit(url)
        if p.hostname != urlsplit(base).hostname or p.scheme != 'https' or not p.path.startswith('/chapters/'): continue
        ident = p.path.rstrip('/').split('/')[-1]
        if not ident or ident in seen: continue
        seen.add(ident)
        result.append({'id': ident, 'name': ' '.join(text.split()) or ident, 'url': url})
    return sorted(result, key=chapter_sort)


async def image_urls(client, chapter, host):
    url = chapter['url']
    if host == 'weebcentral.com':
        url = url.rstrip('/') + '/images?is_prev=False&current_page=1&reading_style=long_strip'
    page = parse((await fetch(client, url, f'https://{host}/')).decode('utf-8', 'replace'))
    urls = []
    for image in page.images:
        if host == 'mangapill.com' and 'js-page' not in image.get('class', '').split(): continue
        src = image.get('data-src') or image.get('src')
        if src:
            # Preserve query strings: CDNs may use signed URLs.
            urls.append(urljoin(url, src.strip()))
    if not urls: raise ValueError('No page images found. The source may have changed or blocked this chapter.')
    return urls
