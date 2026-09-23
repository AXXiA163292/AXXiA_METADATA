import json
import os
import subprocess
import sys
import time

REQUIRED = {
    'requests': 'requests',
    'gspread': 'gspread',
    'google.oauth2': 'google-auth',
}

for module_name, pip_name in REQUIRED.items():
  try:
    __import__(module_name)
  except ImportError:
    subprocess.check_call([sys.executable, '-m', 'pip', 'install', pip_name])

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
    'musicTags': ['musicId', 'musicTag'],
    'outsideCharacters': ['id', 'name'],
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
  url = BASE_URL.format(repo=REGIONS[region], file=filename)
  try:
    r = S.get(url, timeout=30)
    if r.status_code == 404:
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
  if isinstance(val, (int, float, str)):
    return val
  if isinstance(val, list):
    return ', '.join(
        '{' + ', '.join(f'{k}:{flatten_value(v)}' for k, v in item.items()) + '}'
        if isinstance(item, dict)
        else str(flatten_value(item))
        for item in val
    )
  if isinstance(val, dict):
    return (
        '{' + ', '.join(f'{k}:{flatten_value(v)}' for k, v in val.items()) + '}'
    )
  return str(val)


def json_to_rows(data, sheet_suffix):
  if not data or not isinstance(data, list):
    return [['No data']]

  headers = SCHEMAS.get(sheet_suffix)
  if not headers:
    headers, seen = [], set()
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
    ws.update(
        range_name=f'A{i + 1}',
        values=rows[i : i + CHUNK],
        value_input_option='RAW',
    )
  return len(rows) - 1


def DATABASE():
  print('=== DATABASE() started ===')
  scopes = [
      'https://www.googleapis.com/auth/spreadsheets',
      'https://www.googleapis.com/auth/drive',
  ]

  if os.environ.get('GCP_SA_KEY'):
    creds = Credentials.from_service_account_info(
        json.loads(os.environ['GCP_SA_KEY']), scopes=scopes
    )
  else:
    creds = Credentials.from_service_account_file(
        SERVICE_ACCOUNT, scopes=scopes
    )

  gc = gspread.authorize(creds)
  ss = gc.open_by_key(SPREADSHEET_ID)

  total_tasks = len(FILES) * len(REGIONS)
  task_count = 0
  total_sheets, total_rows = 0, 0

  for sheet_suffix, filename in FILES:
    for region in REGIONS:
      task_count += 1
      sheet_name = f'{region}_{sheet_suffix}'

      data = fetch_json(region, filename)
      if data is None:
        print(f'  [{task_count}/{total_tasks}] {sheet_name} • skipped (404)')
        continue

      rows = json_to_rows(data, sheet_suffix)
      n = write_sheet(ss, sheet_name, rows)
      print(f'  [{task_count}/{total_tasks}] {sheet_name} • wrote {n} rows')
      total_sheets += 1
      total_rows += n
      time.sleep(0.2)

  print(
      f'=== Done. {total_sheets} sheets written, {total_rows} total rows. ==='
  )


if __name__ == '__main__':
  try:
    DATABASE()
  except Exception as e:
    import traceback

    traceback.print_exc()
  finally:
    if not IS_GITHUB_ACTIONS:
      input('Press Enter to exit...')