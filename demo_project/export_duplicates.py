"""Re-scan web_links.xlsx and export every duplicate group with its MERGE outcome
(the rules in migrate_excel) to app/data/duplicates.csv for review."""
import os
import sys
import csv
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import openpyxl
from identity import canonicalize_url, slugify_link, desc_key, extract_site
from db import APP_DIR
from migrate_excel import merge_group, _rows, _num, WORKBOOK

OUT = os.path.join(APP_DIR, 'data', 'duplicates.csv')
SHEETS = ['hq', 'grave', 'new']


def main(workbook=WORKBOOK):
    wb = openpyxl.load_workbook(workbook, read_only=True, data_only=True)
    groups = defaultdict(list)
    for sheet in SHEETS:
        if sheet not in wb.sheetnames:
            continue
        for r in _rows(wb[sheet]):
            link = r.get('Link')
            if not link:
                continue
            url = canonicalize_url(str(link))
            ld = r.get('link_desc') or slugify_link(url)
            key = desc_key(ld)
            if not key:
                continue
            groups[key].append({
                'sheet': sheet, 'desc_key': key, 'link_desc': ld, 'url': url,
                'site': extract_site(url), 'status': _num(r.get('Status')),
                'watch_count': _num(r.get('WatchedCount')) or 0,
                'favorite': 1 if str(r.get('keep') or '').strip() == 'T' else 0,
                'date_added': r.get('DateAdded'), 'date_watched': r.get('Date'),
                'tags': r.get('tags'),
                'stars': [s.strip() for s in str(r.get('Stars') or '').split(',') if s.strip()],
            })

    dups = {k: v for k, v in groups.items() if len(v) > 1}
    fields = ['group', 'row', 'sheet', 'site', 'status', 'watch_count', 'favorite',
              'date_added', 'date_watched', 'link_desc', 'url']
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    out = OUT
    try:
        open(out, 'a').close()
    except PermissionError:
        out = OUT.replace('.csv', '_new.csv')   # original is open in Excel
    with open(out, 'w', newline='', encoding='utf-8-sig') as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for gi, (key, members) in enumerate(sorted(dups.items()), 1):
            m = merge_group(members)
            for src in members:
                w.writerow({'group': gi, 'row': 'source', 'sheet': src['sheet'],
                            'site': src['site'], 'status': src['status'],
                            'watch_count': src['watch_count'], 'favorite': src['favorite'],
                            'date_added': (src['date_added'] or ''), 'date_watched': (src['date_watched'] or ''),
                            'link_desc': src['link_desc'], 'url': src['url']})
            w.writerow({'group': gi, 'row': 'MERGED', 'sheet': m['location'],
                        'site': m['site'], 'status': m['status'], 'watch_count': m['watch_count'],
                        'favorite': m['favorite'], 'date_added': (m['date_added'] or ''),
                        'date_watched': (m['date_watched'] or ''), 'link_desc': m['link_desc'], 'url': m['url']})
    print(f'duplicate groups: {len(dups)}  ->  {out}')
    # summary of status changes caused by merging
    moved = 0
    for members in dups.values():
        m = merge_group(members)
        watch_before = any(s['sheet'] == 'hq' and s['status'] == 4 for s in members)
        if watch_before and m['location'] != 'watch':
            moved += 1
    print(f'hq status-4 videos pulled out of the watch list by a 9/94 duplicate: {moved}')


if __name__ == '__main__':
    main()
