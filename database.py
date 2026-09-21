import json
import os
import re
import subprocess
import sys
import time

# 1. Auto-install missing packages
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
    print(
        f'Installing missing library: {pip_name}... (this may take a moment)'
    )
    subprocess.check_call([sys.executable, '-m', 'pip', 'install', pip_name])

# 2. Now safely import everything your script needs
import gspread
from google.oauth2.service_account import Credentials
import requests

SPREADSHEET_ID = '1Fpp_sJbGjuKxUAcWhZOM1YqHJJrUHTWE56lO4dthhak'

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
SERVICE_ACCOUNT = os.path.join(SCRIPT_DIR, 'service_account.json')

IS_GITHUB_ACTIONS = os.environ.get('GITHUB_ACTIONS') == 'true'

REGIONS = {
    'jp': 'sekai-master-db-diff',
    'tc': 'sekai-master-db-tc-diff',
    'en': 'sekai-master-db-en-diff',
    'kr': 'sekai-master-db-kr-diff',
    'cn': 'sekai-master-db-cn-diff',
}

FILES = [
    ('outsideCharacters', 'outsideCharacters.json'),
    ('musics', 'musics.json'),
    ('musicArtists', 'musicArtists.json'),
    ('musicCollaborations', 'musicCollaborations.json'),
    ('musicTags', 'musicTags.json'),
    ('musicVocals', 'musicVocals.json'),
    ('musicDifficulties', 'musicDifficulties.json'),
    ('musicOriginals', 'musicOriginals.json'),
    ('musicAssetVariants', 'musicAssetVariants.json'),
]

SCHEMAS = {
    'musicDifficulties': [
        'id',
        'musicId',
        'musicDifficulty',
        'playLevel',
        'totalNoteCount',
    ],
    'musicVocals': [
        'id',
        'musicId',
        'musicVocalType',
        'seq',
        'releaseConditionId',
        'caption',
        'characters',
        'assetbundleName',
        'archivePublishedAt',
        'specialSeasonId',
        'archiveDisplayType',
    ],
    'musicTags': [
        'musicId',
        'musicTag',
    ],
    'outsideCharacters': [
        'id',
        'name',
    ],
    'musics': [
        'id',
        'seq',
        'releaseConditionId',
        'categories',
        'title',
        'pronunciation',
        'creatorArtistId',
        'lyricist',
        'composer',
        'arranger',
        'dancerCount',
        'selfDancerPosition',
        'assetbundleName',
        'publishedAt',
        'releasedAt',
        'fillerSec',
        'isNewlyWrittenMusic',
        'isFullLength',
        'musicCollaborationId',
    ],
}

BASE_URL = 'https://raw.githubusercontent.com/Sekai-World/{repo}/main/{file}'

S = requests.Session()
S.headers.update({'User-Agent': 'Mozilla/5.0'})


def fetch_json(region, filename):
  repo = REGIONS[region]
  url = BASE_URL.format(repo=repo, file=filename)
  try:
    r = S.get(url, timeout=30)
    if r.status_code == 404:
      print(f'    [skip] 404 {url}')
      return None
    r.raise_for_status()
    return r.json()
  except Exception as e:
    print(f'    [error] {url}: {e}')
    return None


def flatten_value(val):
  if val is None:
    return ''
  if isinstance(val, bool):
    return str(val).upper()
  if isinstance(val, (int, float)):
    return val
  if isinstance(val, str):
    return val
  if isinstance(val, list):
    parts = []
    for item in val:
      if isinstance(item, dict):
        parts.append(
            '{'
            + ', '.join(f'{k}:{flatten_value(v)}' for k, v in item.items())
            + '}'
        )
      else:
        parts.append(str(flatten_value(item)))
    return ', '.join(parts)
  if isinstance(val, dict):
    return (
        '{' + ', '.join(f'{k}:{flatten_value(v)}' for k, v in val.items()) + '}'
    )
  return str(val)


def json_to_rows(data, sheet_suffix):
  if not data or not isinstance(data, list):
    return [['No data']]

  fixed_headers = SCHEMAS.get(sheet_suffix)

  if fixed_headers:
    headers = fixed_headers
  else:
    headers = []
    seen = set()
    for item in data:
      if isinstance(item, dict):
        for k in item.keys():
          if k not in seen:
            seen.add(k)
            headers.append(k)
    if not headers:
      return [['No data']]

  rows = [headers]
  for item in data:
    if isinstance(item, dict):
      rows.append([flatten_value(item.get(h)) for h in headers])
    else:
      rows.append([flatten_value(item)] + [''] * (len(headers) - 1))

  return rows


def write_sheet(ss, sheet_name, rows):
  try:
    ws = ss.worksheet(sheet_name)
    ws.clear()
  except gspread.exceptions.WorksheetNotFound:
    ws = ss.add_worksheet(
        title=sheet_name,
        rows=max(len(rows) + 5, 100),
        cols=max(len(rows[0]) + 2 if rows else 10, 10),
    )

  CHUNK = 5000
  for i in range(0, len(rows), CHUNK):
    chunk = rows[i : i + CHUNK]
    start_row = i + 1
    ws.update(
        range_name=f'A{start_row}',
        values=chunk,
        value_input_option='RAW',
    )
  return len(rows) - 1


def DATABASE():
  print('=== DATABASE() started ===')

  scopes = [
      'https://www.googleapis.com/auth/spreadsheets',
      'https://www.googleapis.com/auth/drive',
  ]

  # Load credentials from GitHub Secret or local file
  if os.environ.get('GCP_SA_KEY'):
    key_dict = json.loads(os.environ['GCP_SA_KEY'])
    creds = Credentials.from_service_account_info(key_dict, scopes=scopes)
  else:
    creds = Credentials.from_service_account_file(
        SERVICE_ACCOUNT, scopes=scopes
    )

  gc = gspread.authorize(creds)
  ss = gc.open_by_key(SPREADSHEET_ID)

  total_sheets = 0
  total_rows = 0

  for sheet_suffix, filename in FILES:
    for region in REGIONS:
      sheet_name = f'{region}_{sheet_suffix}'
      print(f'  [{sheet_name}] fetching...')

      data = fetch_json(region, filename)
      if data is None:
        print(f'  [{sheet_name}] skipped (no data)')
        continue

      rows = json_to_rows(data, sheet_suffix)
      n = write_sheet(ss, sheet_name, rows)
      print(f'  [{sheet_name}] wrote {n} rows ({len(rows[0])} cols)')
      total_sheets += 1
      total_rows += n
      time.sleep(0.3)

  print(f'=== Done. {total_sheets} sheets written, {total_rows} total rows. ===')


if __name__ == '__main__':
  import traceback

  try:
    DATABASE()
  except Exception as e:
    print('\n' + '=' * 40)
    print('CRASH LOG:')
    traceback.print_exc()
    print('=' * 40 + '\n')
  finally:
    if not IS_GITHUB_ACTIONS:
      input('Press Enter to exit...')