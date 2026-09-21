import csv
import io
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
from bs4 import BeautifulSoup, Tag
import gspread
from google.oauth2.service_account import Credentials
import requests

SPREADSHEET_ID = '1Fpp_sJbGjuKxUAcWhZOM1YqHJJrUHTWE56lO4dthhak'
SHEET_NAME = 'judgment'

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
SERVICE_ACCOUNT = os.path.join(SCRIPT_DIR, 'service_account.json')
CONSTANTS_SID = '1B8tX9VL2PcSJKyuHFVd2UT_8kYlY4ZdwHwg9MfWOPug'

IS_GITHUB_ACTIONS = os.environ.get('GITHUB_ACTIONS') == 'true'

DIFFICULTIES = ['APPEND', 'MASTER', 'EXPERT', 'HARD']
DIFF_LABEL = {
    'APPEND': 'Append',
    'MASTER': 'Master',
    'EXPERT': 'Expert',
    'HARD': 'Hard',
}

S = requests.Session()
S.headers.update({'User-Agent': 'Mozilla/5.0'})


def load_constants():
  """Returns dict: (diff_label, song_id_int) -> constant_str"""
  url = f'https://docs.google.com/spreadsheets/d/{CONSTANTS_SID}/gviz/tq?tqx=out:csv&sheet=Constants&range=C:G'
  resp = S.get(url, timeout=30)
  resp.raise_for_status()
  reader = csv.reader(io.StringIO(resp.text))
  rows = list(reader)
  lookup = {}
  for row in rows[1:]:
    if len(row) < 5:
      continue
    constant = row[0].strip().replace(',', '.')
    diff = row[3].strip()
    sid = row[4].strip()
    if not constant or not diff or not sid:
      continue
    try:
      lookup[(diff, int(sid))] = constant
    except ValueError:
      pass
  print(f'  Constants loaded: {len(lookup)} entries')
  return lookup


def get_constant(constants, song_id, difficulty):
  if song_id is None:
    return '-'
  diff_label = DIFF_LABEL.get(difficulty, difficulty)
  try:
    return constants.get((diff_label, int(song_id)), '-')
  except (ValueError, TypeError):
    return '-'


def strip_tags(s):
  return re.sub(
      r'\s+', ' ', re.sub(r'<[^>]+>', ' ', s).replace('&nbsp;', ' ')
  ).strip()


def clean(v):
  s = (v or '').strip()
  return s if s else '-'


def parse_difficulty(difficulty):
  url = 'https://pjsekai.com/?' + requests.utils.quote(
      '楽曲難易度表' + difficulty
  )
  try:
    resp = S.get(url, timeout=30)
  except Exception as e:
    print(f'  [error] fetch {difficulty}: {e}')
    return []

  html = resp.content.decode('utf-8', errors='replace')
  html = re.sub(r'</?font[^>]*>', '', html, flags=re.I)

  rows = []
  anchor_re = re.compile(r'id="[a-z]+(\d+)"', re.I)
  section_starts = [
      {'index': m.start(), 'lvl': m.group(1)} for m in anchor_re.finditer(html)
  ]
  section_starts.append({'index': len(html), 'lvl': None})

  for s in range(len(section_starts) - 1):
    lvl = section_starts[s]['lvl']
    section = html[section_starts[s]['index'] : section_starts[s + 1]['index']]
    tbody_m = re.search(r'<tbody>([\s\S]*?)</tbody>', section, re.I)
    if not tbody_m:
      continue
    tbody = tbody_m.group(1)

    for row_m in re.finditer(r'<tr>([\s\S]*?)</tr>', tbody, re.I):
      row_html = row_m.group(1)
      cells = re.findall(r'<td[^>]*>([\s\S]*?)</td>', row_html, re.I)
      if len(cells) < 2:
        continue

      title_m = re.search(r'<a[^>]*>([\s\S]*?)</a>', cells[0], re.I)
      title = strip_tags(title_m.group(1) if title_m else cells[0])
      if not title:
        continue

      judgment = history = ''
      memo1_m = re.search(
          r'<div[^>]*class="memo1"[^>]*>([\s\S]*?)</div>', cells[1], re.I
      )
      if memo1_m:
        memo1_html = memo1_m.group(1)
        memo2_m = re.search(
            r'<span[^>]*class="memo2"[^>]*>([\s\S]*?)</span>', memo1_html, re.I
        )
        if memo2_m:
          history = strip_tags(memo2_m.group(1))
          memo1_html = memo1_html.replace(memo2_m.group(0), '')
        judgment = strip_tags(memo1_html)
      else:
        judgment = strip_tags(cells[1])

      elements = ', '.join(
          strip_tags(li)
          for li in re.findall(
              r'<li[^>]*>([\s\S]*?)</li>',
              cells[2] if len(cells) > 2 else '',
              re.I,
          )
          if strip_tags(li)
      )
      comments = strip_tags(cells[3]) if len(cells) > 3 else ''

      rows.append([
          clean(title),
          clean(judgment),
          clean(elements),
          clean(comments),
          difficulty,
          clean(lvl),
          clean(history),
      ])

  return rows


def load_jp_musics_ids(gc, spreadsheet_id):
  ws = gc.open_by_key(spreadsheet_id).worksheet('jp_musics')
  data = ws.get('A:E')
  mapping = {}
  for row in data[1:]:
    if len(row) < 5:
      continue
    sid = row[0].strip()
    title = row[4].strip()
    if title and sid:
      mapping[title] = sid
  print(f'  jp_musics loaded: {len(mapping)} entries')
  return mapping


def JUDGMENT():
  print('=== JUDGMENT() started ===')

  all_rows = []
  for diff in DIFFICULTIES:
    print(f'  Fetching {diff}...')
    rows = parse_difficulty(diff)
    print(f'    -> {len(rows)} rows')
    all_rows.extend(rows)
    time.sleep(0.5)

  if not all_rows:
    print('[error] No data scraped.')
    sys.exit(1)

  print(f'Total rows: {len(all_rows)}')

  print('Connecting to Google Sheets...')
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

  print('Loading lookup tables...')
  constants = load_constants()
  id_map = load_jp_musics_ids(gc, SPREADSHEET_ID)

  final_rows = []
  for row in all_rows:
    title = row[0]
    difficulty = row[4]
    song_id = id_map.get(title)
    constant = (
        get_constant(constants, song_id, difficulty) if title != '-' else '-'
    )
    final_rows.append(row + [constant])

  ss = gc.open_by_key(SPREADSHEET_ID)
  try:
    ws = ss.worksheet(SHEET_NAME)
    ws.clear()
  except gspread.exceptions.WorksheetNotFound:
    ws = ss.add_worksheet(title=SHEET_NAME, rows=len(final_rows) + 10, cols=8)

  header = [[
      'title',
      'judgment',
      'elements',
      'comments',
      'difficulty',
      'lvl',
      'history',
      '39sConstant',
  ]]
  ws.update(
      range_name='A1', values=header + final_rows, value_input_option='RAW'
  )

  print(f"=== Done. {len(final_rows)} rows written to '{SHEET_NAME}' tab. ===")


if __name__ == '__main__':
  import traceback

  try:
    JUDGMENT()
  except Exception as e:
    print('\n' + '=' * 40)
    print('CRASH LOG:')
    traceback.print_exc()
    print('=' * 40 + '\n')
  finally:
    if not IS_GITHUB_ACTIONS:
      input('Press Enter to exit...')