"""Per-site scrapers. Ported from downloader_script_v2.R (rvest -> selectolax).

scrape_url(url) -> {stars: [..], tags: "a, b, c", description: str, error: str|None}.
Live network needed; run on the user's machine. Cloudflare-guarded sites may need a
headless browser later (Playwright) — those return an error for now.
"""
import re
import requests
from selectolax.parser import HTMLParser

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
_BRACKET = re.compile(r'\[.*?\]')


def _debracket(s):
    return _BRACKET.sub('', s or '').strip()


def fetch(url, timeout=20):
    r = requests.get(url, headers={'User-Agent': UA}, timeout=timeout)
    r.raise_for_status()
    return HTMLParser(r.text)


def _title(doc):
    for sel, attr in [("meta[property='og:title']", 'content'),
                      ("meta[name='twitter:title']", 'content')]:
        n = doc.css_first(sel)
        if n and n.attributes.get(attr):
            return n.attributes[attr].strip()
    h1 = doc.css_first('h1')
    if h1:
        return h1.text(strip=True)
    t = doc.css_first('title')
    return t.text(strip=True) if t else ''


def scrape_url(url):
    try:
        doc = fetch(url)
    except Exception as e:
        return {'stars': [], 'tags': None, 'description': '', 'error': str(e)}

    stars, tags = [], []
    if 'hqporner' in url:
        stars = [n.text(strip=True) for n in doc.css('span.meta_data a.click-trigger')]
        tags = [n.text(strip=True) for n in doc.css("a[href*='/category/']")]
    elif 'eporner' in url:
        stars = [n.attributes.get('content') for n in doc.css("meta[itemprop='actor']")]
        tags = [n.attributes.get('content') for n in doc.css("meta[property='video:tag']")]
    elif 'xtapes' in url:
        items = [a.text(strip=True) for a in doc.css('#cat-tag ul li a')]
        tags = items
        slug = url.replace('-', ' ').lower()
        stars = [it for it in items if _debracket(it).lower() and _debracket(it).lower() in slug]
    else:
        return {'stars': [], 'tags': None, 'description': _title(doc), 'error': 'unknown site'}

    stars = [_debracket(s) for s in stars if s and s.strip()]
    stars = [s for s in stars if s]
    tags = [t.strip() for t in tags if t and t.strip()]
    return {'stars': stars, 'tags': ', '.join(dict.fromkeys(tags)) or None,
            'description': _title(doc), 'error': None}
