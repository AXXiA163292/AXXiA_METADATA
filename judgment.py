import csv
import io
import json
import os
import re
import subprocess
import sys
import time

REQUIRED = {
    'requests': 'requests',
    'bs4': 'beautifulsoup4',
    'gspread': 'gspread',
    'google.oauth2': 'google-auth',
}

for module_name, pip_name in REQUIRED.items():
    try:
        __import__(module_name)
    except ImportError:
        subprocess.check_call([sys.executable, '-m', 'pip', 'install', pip_name])

from bs4 import BeautifulSoup
import gspread
from google.oauth2.service_account import Credentials
import requests

SPREADSHEET_ID = '1Fpp_sJbGjuKxUAcWhZOM1YqHJJrUHTWE56lO4dthhak'
SHEET_NAME = 'community'

CONSTANTS_SID = '1B8tX9VL2PcSJKyuHFVd2UT_8kYlY4ZdwHwg9MfWOPug'
COMMUNITY_CONSTANTS_SID = '1Yv3GXnCIgEIbHL72EuZ-d5q_l-auPgddWi4Efa14jq0'
COMMUNITY_GID = '182216'

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
SERVICE_ACCOUNT = os.path.join(SCRIPT_DIR, 'service_account.json')
IS_GITHUB_ACTIONS = os.environ.get('GITHUB_ACTIONS') == 'true'

DIFFICULTIES = ['APPEND', 'MASTER', 'EXPERT', 'HARD']

DIFF_LABEL = {
    'APPEND': 'Append',
    'MASTER': 'Master',
    'EXPERT': 'Expert',
    'HARD': 'Hard',
}

DIFF_ORDER = {
    'APPEND': 0,
    'MASTER': 1,
    'EXPERT': 2,
    'HARD': 3,
}

S = requests.Session()
S.headers.update({'User-Agent': 'Mozilla/5.0'})


def normalize_constant(value):
    value = (value or '').strip().replace(',', '.')
    return value if value else ''


def load_csv(url):
    response = S.get(url, timeout=60)
    response.raise_for_status()
    return list(csv.reader(io.StringIO(response.content.decode('utf-8-sig'))))


def load_constants():
    url = (
        f'https://docs.google.com/spreadsheets/d/{CONSTANTS_SID}'
        '/gviz/tq?tqx=out:csv&sheet=Constants&range=C:G'
    )

    rows = load_csv(url)
    lookup = {}

    for row in rows[1:]:
        if len(row) < 5:
            continue

        constant = normalize_constant(row[0])
        difficulty = row[3].strip()
        song_id = row[4].strip()

        if not constant or not difficulty or not song_id:
            continue

        try:
            lookup[(difficulty.casefold(), int(song_id))] = constant
        except ValueError:
            continue

    print(f'  Loaded {len(lookup)} 39s constants.')
    return lookup


def load_community_constants():
    url = (
        f'https://docs.google.com/spreadsheets/d/{COMMUNITY_CONSTANTS_SID}'
        f'/export?format=csv&gid={COMMUNITY_GID}'
    )

    rows = load_csv(url)
    constants = {}

    for row in rows[1:]:
        if len(row) < 7:
            continue

        constant = normalize_constant(row[2])
        difficulty = row[5].strip()
        song_id = row[6].strip()

        if not constant or not difficulty or not song_id:
            continue

        try:
            numeric_id = int(song_id)
            constants[(difficulty.casefold(), numeric_id)] = constant
        except ValueError:
            continue

    print(f'  Loaded {len(constants)} community constants.')
    return constants


def get_constant(lookup, song_id, difficulty):
    if song_id is None:
        return ''

    try:
        return lookup.get(
            (DIFF_LABEL.get(difficulty, difficulty).casefold(), int(song_id)),
            ''
        )
    except (ValueError, TypeError):
        return ''


def strip_tags(value):
    return re.sub(
        r'\s+',
        ' ',
        re.sub(r'<[^>]+>', ' ', value).replace('&nbsp;', ' ')
    ).strip()


def clean(value):
    value = (value or '').strip()
    return value if value else '-'


def parse_difficulty(difficulty):
    url = 'https://pjsekai.com/?' + requests.utils.quote(
        '楽曲難易度表' + difficulty
    )

    try:
        response = S.get(url, timeout=30)
        response.raise_for_status()
    except Exception as error:
        print(f'  [error] fetch {difficulty}: {error}')
        return []

    html = response.content.decode('utf-8', errors='replace')
    html = re.sub(r'</?font[^>]*>', '', html, flags=re.I)

    rows = []
    anchor_re = re.compile(r'id="[a-z]+(\d+)"', re.I)

    section_starts = [
        {'index': match.start(), 'lvl': match.group(1)}
        for match in anchor_re.finditer(html)
    ]

    section_starts.append({'index': len(html), 'lvl': None})

    for index in range(len(section_starts) - 1):
        level = section_starts[index]['lvl']

        section = html[
            section_starts[index]['index']:
            section_starts[index + 1]['index']
        ]

        tbody_match = re.search(
            r'<tbody>([\s\S]*?)</tbody>',
            section,
            re.I
        )

        if not tbody_match:
            continue

        for row_match in re.finditer(
            r'<tr>([\s\S]*?)</tr>',
            tbody_match.group(1),
            re.I
        ):
            cells = re.findall(
                r'<td[^>]*>([\s\S]*?)</td>',
                row_match.group(1),
                re.I
            )

            if len(cells) < 2:
                continue

            title_match = re.search(
                r'<a[^>]*>([\s\S]*?)</a>',
                cells[0],
                re.I
            )

            title = strip_tags(
                title_match.group(1) if title_match else cells[0]
            )

            if not title:
                continue

            judgment = ''
            history = ''

            memo1_match = re.search(
                r'<div[^>]*class="memo1"[^>]*>([\s\S]*?)</div>',
                cells[1],
                re.I
            )

            if memo1_match:
                memo1_html = memo1_match.group(1)

                memo2_match = re.search(
                    r'<span[^>]*class="memo2"[^>]*>([\s\S]*?)</span>',
                    memo1_html,
                    re.I
                )

                if memo2_match:
                    history = strip_tags(memo2_match.group(1))
                    memo1_html = memo1_html.replace(
                        memo2_match.group(0),
                        ''
                    )

                judgment = strip_tags(memo1_html)
            else:
                judgment = strip_tags(cells[1])

            elements = ', '.join(
                strip_tags(item)
                for item in re.findall(
                    r'<li[^>]*>([\s\S]*?)</li>',
                    cells[2] if len(cells) > 2 else '',
                    re.I
                )
                if strip_tags(item)
            )

            comments = strip_tags(cells[3]) if len(cells) > 3 else ''

            rows.append([
                clean(title),
                clean(judgment),
                clean(elements),
                clean(comments),
                difficulty,
                clean(level),
                clean(history),
            ])

    return rows


def load_39s_song_ids(gc):
    worksheet = gc.open_by_key(SPREADSHEET_ID).worksheet('jp_musics')
    data = worksheet.get('A:E')

    mapping = {}

    for row in data[1:]:
        if len(row) >= 5:
            title = row[0].strip()
            song_id = row[4].strip()

            if title and song_id:
                try:
                    mapping[title] = int(song_id)
                except ValueError:
                    pass

    return mapping


def resolve_song_ids(rows, song_title_to_id):
    resolved = []

    for row in rows:
        song_id = song_title_to_id.get(row[0])
        resolved.append(row + [song_id])

    return resolved


def sort_rows(rows):
    def sort_key(row):
        song_id = row[7]
        unmatched = song_id is None

        return (
            unmatched,
            song_id if song_id is not None else float('inf'),
            DIFF_ORDER.get(row[4], 99),
            row[0].casefold(),
        )

    return sorted(rows, key=sort_key)


def JUDGMENT():
    print('=== COMMUNITY scraper started ===')

    all_rows = []

    for index, difficulty in enumerate(DIFFICULTIES, 1):
        rows = parse_difficulty(difficulty)

        print(
            f'  [{index}/{len(DIFFICULTIES)}] '
            f'{difficulty}: {len(rows)} rows'
        )

        all_rows.extend(rows)
        time.sleep(0.4)

    if not all_rows:
        print('[error] No data scraped.')
        sys.exit(1)

    scopes = [
        'https://www.googleapis.com/auth/spreadsheets',
        'https://www.googleapis.com/auth/drive',
    ]

    if os.environ.get('GCP_SA_KEY'):
        credentials = Credentials.from_service_account_info(
            json.loads(os.environ['GCP_SA_KEY']),
            scopes=scopes
        )
    else:
        credentials = Credentials.from_service_account_file(
            SERVICE_ACCOUNT,
            scopes=scopes
        )

    gc = gspread.authorize(credentials)

    print('Loading 39s constants...')
    constants_39s = load_constants()

    print('Loading community constants...')
    community_constants = load_community_constants()

    print('Loading 39s song IDs...')
    song_title_to_id = load_39s_song_ids(gc)

    print('Matching scraped song IDs...')
    final_rows = resolve_song_ids(all_rows, song_title_to_id)

    output = []

    for row in final_rows:
        title = row[0]
        difficulty = row[4]
        song_id = row[7]

        constant_39s = get_constant(
            constants_39s,
            song_id,
            difficulty
        )

        community_constant = get_constant(
            community_constants,
            song_id,
            difficulty
        )

        constant = constant_39s or community_constant

        output.append(
            row[:7] + [
                song_id if song_id is not None else '-',
                constant_39s or '-',
                community_constant or '-',
                constant or '-',
            ]
        )

    output = sort_rows([
        row[:7] + [
            None if row[7] == '-' else row[7],
            row[8],
            row[9],
            row[10],
        ]
        for row in output
    ])

    header = [[
        'title',
        'judgment',
        'elements',
        'comments',
        'difficulty',
        'lvl',
        'history',
        'songId',
        '39sConstant',
        'communityConstant',
        'constant',
    ]]

    final_output = [
        [
            row[0],
            row[1],
            row[2],
            row[3],
            row[4],
            row[5],
            row[6],
            row[7] if row[7] is not None else '-',
            row[8],
            row[9],
            row[10],
        ]
        for row in output
    ]

    spreadsheet = gc.open_by_key(SPREADSHEET_ID)

    try:
        worksheet = spreadsheet.worksheet(SHEET_NAME)
        worksheet.clear()
    except gspread.exceptions.WorksheetNotFound:
        worksheet = spreadsheet.add_worksheet(
            title=SHEET_NAME,
            rows=len(final_output) + 10,
            cols=len(header[0])
        )

    worksheet.resize(
        rows=max(1000, len(final_output) + 10),
        cols=len(header[0])
    )

    worksheet.update(
        range_name='A1',
        values=header + final_output,
        value_input_option='RAW'
    )

    matched = sum(1 for row in final_output if row[7] != '-')
    unmatched = len(final_output) - matched

    print(f'=== Done. {len(final_output)} rows written to {SHEET_NAME}. ===')
    print(f'  Matched song IDs: {matched}')
    print(f'  Unmatched song IDs: {unmatched}')


if __name__ == '__main__':
    try:
        JUDGMENT()
    except Exception:
        import traceback
        traceback.print_exc()
    finally:
        if not IS_GITHUB_ACTIONS:
            input('Press Enter to exit...')