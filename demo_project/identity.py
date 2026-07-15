"""URL canonicalization, description slug, and dedup key.

A video's identity is its DESCRIPTION, not its URL: site URLs mutate (id numbers
change) while the slug stays put. `desc_key` is the dedup identity; `url` is mutable.
Ported from downloader_script_v2.R (normalize_url + slugify_link).
"""
import re

# CONFIG$url_rewrites
_URL_REWRITES = [
    (re.compile(r'https://hqporner\.com/'), 'https://m.hqporner.com/'),
    (re.compile(r'v\.xtapes'),  'xtapes'),
    (re.compile(r'en\.xtapes'), 'xtapes'),
    (re.compile(r'hd\.xtapes'), 'xtapes'),
]

_SLUG_STRIP_1 = re.compile(r'.*hdporn/[0-9]{2,9}-|.*xtapes\.[a-z]+/([0-9]{2,9}/)?|.*cuties4u\.com/videos/')
_SLUG_STRIP_2 = re.compile(r'^.*/(?=[A-Za-z])|hdporn/[0-9]{2,9}-')
_SLUG_STRIP_3 = re.compile(r'/|\.html')
_LEAD_ID = re.compile(r'^\d{2,}[-_]')          # site id prefix, e.g. 98784-
_TRAIL_ID = re.compile(r'[-_]\d{4,}$')         # site id suffix, e.g. -277730 (4+ digits so series "-3" survives)
_PUNCT = re.compile(r'[^a-zA-Z0-9\s]')   # also splits underscores (hqporner uses _)
_WS = re.compile(r'\s+')
_SUBDOMAINS = {'www', 'm', 'v', 'en', 'hd', 'ww3', 'ww', 'de', 'fr'}


def extract_site(url: str) -> str:
    """Registrable-ish site label: m.hqporner.com -> hqporner, bingato.com -> bingato."""
    if not url:
        return ''
    netloc = url.split('//', 1)[-1].split('/', 1)[0].split('?', 1)[0].lower()
    parts = [p for p in netloc.split('.') if p]
    while len(parts) > 1 and parts[0] in _SUBDOMAINS:
        parts = parts[1:]
    return parts[0] if parts else ''


def canonicalize_url(url: str) -> str:
    if not url:
        return url
    u = url.strip()
    for pat, repl in _URL_REWRITES:
        u = pat.sub(repl, u)
    return u


def slugify_link(url: str) -> str:
    """URL -> human 'link description' slug, site-agnostic so the same title on two
    different sites (bingato vs hqporner) yields the same key."""
    if not url:
        return ''
    x = _SLUG_STRIP_1.sub('', url)
    x = _SLUG_STRIP_2.sub('', x)
    x = _SLUG_STRIP_3.sub('', x)
    x = _LEAD_ID.sub('', x)          # strip leading site id
    x = _TRAIL_ID.sub('', x)         # strip trailing site id (keeps short series numbers)
    x = _PUNCT.sub(' ', x)
    return _WS.sub(' ', x).strip()


def desc_key(link_desc: str) -> str:
    """Normalized dedup identity from a link description."""
    return _WS.sub(' ', (link_desc or '').lower()).strip()


def split_stars(s: str) -> list:
    """Split a Stars string into individual performers. Handles ' & ' and commas."""
    if not s:
        return []
    s = str(s).replace(' & ', ', ')
    return [p.strip() for p in s.split(',') if p.strip() and p.strip() not in ('0', 'NA')]
