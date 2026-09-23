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

from bs4 import BeautifulSoup, Tag
import gspread
from google.oauth2.service_account import Credentials
import requests

SPREADSHEET_ID = '1Fpp_sJbGjuKxUAcWhZOM1YqHJJrUHTWE56lO4dthhak'
SHEET_NAME = 'fandom'

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
SERVICE_ACCOUNT = os.path.join(SCRIPT_DIR, 'service_account.json')
API = 'https://projectsekai.fandom.com/api.php'
DELAY = 0.4
IS_GITHUB_ACTIONS = os.environ.get('GITHUB_ACTIONS') == 'true'

S = requests.Session()
S.headers.update({'User-Agent': 'Mozilla/5.0'})


def fetch(page, retries=3):
  for i in range(retries):
    try:
      r = S.get(
          API,
          params={
              'action': 'parse',
              'page': page,
              'prop': 'text',
              'format': 'json',
          },
          timeout=20,
      )
      return r.json()
    except Exception:
      if i < retries - 1:
        time.sleep(1.5)
  return None


MONTHS = {
    'january': '01',
    'february': '02',
    'march': '03',
    'april': '04',
    'may': '05',
    'june': '06',
    'july': '07',
    'august': '08',
    'september': '09',
    'october': '10',
    'november': '11',
    'december': '12',
}


def normalize_date(t):
  t = re.sub(r'\s*[\[\(]\s*\d+\s*[\]\)][\[\(\d\]\)]*$', '', t).strip()
  m = re.match(r'([A-Za-z]+)\s+(\d{1,2}),?\s+(\d{4})', t)
  if m:
    mon = MONTHS.get(m.group(1).lower())
    if mon:
      return f'{int(m.group(2)):02d}/{mon}/{m.group(3)}'
  m = re.match(r'^(\d{1,2})/(\d{1,2})/(\d{4})$', t)
  if m:
    return f'{int(m.group(2)):02d}/{int(m.group(1)):02d}/{m.group(3)}'
  return t


def clean(tag):
  if tag is None:
    return '-'
  t = tag.get_text(' ', strip=True) if isinstance(tag, Tag) else str(tag)
  t = re.sub(r'\s+', ' ', t).strip().replace('\u2014', '-').replace('\u2013', '-')
  return t if t else '-'


def get_field(ib, *sources):
  if not ib:
    return '-'
  for src in sources:
    tag = ib.find(attrs={'data-source': src})
    if tag:
      v = tag.find(class_=re.compile(r'pi-data-value'))
      if v:
        t = clean(v)
        if t and t != '-':
          return t
  return '-'


def get_date_field(ib, *sources):
  t = get_field(ib, *sources)
  return '-' if t == '-' else normalize_date(t)


def FANDOM():
  print('=== FANDOM() started ===')
  print('Fetching song list...')
  raw = fetch('Song_List')
  if not raw or 'parse' not in raw:
    print('[error] Could not fetch Song_List')
    sys.exit(1)

  html = raw['parse']['text']['*'].replace('724&#91;1&#93;', '724')
  soup = BeautifulSoup(html, 'html.parser')
  song_rows, section = [], ''

  for tag in soup.find_all(['h2', 'tbody']):
    if tag.name == 'h2':
      h = tag.find(class_='mw-headline')
      if h:
        section = h.get_text(strip=True).replace(' Songs', '')
      continue
    for tr in tag.find_all('tr'):
      cells = tr.find_all('td')
      if len(cells) < 6:
        continue
      sid_raw = clean(cells[0])
      clean_id = str(sid_raw).split('[')[0].strip()
      sid = (
          'TBA'
          if (re.match(r'^-+$', clean_id) or clean_id.upper() == 'TBA')
          else re.sub(r'\D', '', clean_id)
      )

      if not sid or len(sid) > 15:
        continue
      link = cells[2].find('a', href=re.compile(r'^/wiki/'))
      url = ('https://projectsekai.fandom.com' + link['href']) if link else ''
      title = BeautifulSoup(clean(cells[2]), 'html.parser').get_text()
      song_rows.append([
          sid,
          title,
          url,
          clean(cells[4]),
          clean(cells[5]),
          clean(cells[6]),
          clean(cells[7]) if len(cells) > 7 else '-',
          clean(cells[8]) if len(cells) > 8 else '-',
          clean(cells[9]) if len(cells) > 9 else '-',
          section,
      ])

  song_rows.sort(key=lambda r: (0, int(r[0])) if r[0].isdigit() else (1, r[1]))
  total_songs = len(song_rows)
  print(f'  -> {total_songs} songs')

  EMPTY = ['-'] * 22
  meta_rows = []

  for idx, row in enumerate(song_rows):
    sid, title = row[0], row[1]
    if sid and sid != '-' and sid.upper() != 'TBA':
      print(f'  [{idx+1}/{total_songs}] {sid} • {title}')
    else:
      print(f'  [{idx+1}/{total_songs}] {title}')
    time.sleep(DELAY)

    data = fetch(title)
    html2 = None
    for candidate in [
        title,
        title + ' (song)',
        title.replace('[', '(').replace(']', ')'),
    ]:
      d = fetch(candidate) if candidate != title else data
      if d and 'parse' in d:
        h = d['parse']['text']['*']
        if 'class="portable-infobox' in h:
          html2 = h
          break

    if html2 is None:
      meta_rows.append(EMPTY[:])
      continue

    sp = BeautifulSoup(html2, 'html.parser')
    ib = sp.find('aside', class_=re.compile(r'portable-infobox'))

    romanized = '-'
    if ib:
      pt = ib.find('h2', class_=re.compile(r'pi-title'))
      if pt:
        romanized = clean(pt)

    english = get_field(ib, 'english', 'official english')
    chinese = get_field(ib, 'simp cn')
    hangul = get_field(ib, 'hangul')
    bpm = get_field(ib, 'bpm')

    dur = get_field(ib, 'duration')
    full_len = game_len = '-'
    if dur != '-':
      mf = re.search(r'(\d+:\d{2})\s*\(Full\)', dur, re.I)
      mg = re.search(r'(\d+:\d{2})\s*\(Game(?:\s*Size)?\)', dur, re.I)
      full_len = mf.group(1) if mf else '-'
      game_len = mg.group(1) if mg else '-'
      if full_len == '-' and game_len == '-':
        ma = re.search(r'(\d+:\d{2})', dur)
        if ma:
          full_len = ma.group(1)

    append = [
        get_date_field(ib, *srcs)
        for srcs in [
            ['append_date', 'app_date'],
            ['append_tw_date', 'app_tw_date'],
            ['append_en_date', 'app_en_date'],
            ['append_kr_date', 'app_kr_date'],
            ['append_cn_date', 'app_cn_date'],
        ]
    ]

    intro = '-'
    aside = sp.find('aside')
    if aside:
      for p in aside.find_all_next('p'):
        t = clean(p)
        if len(t) > 30:
          intro = t
          break

    trivia_items = []
    th = sp.find(id='Trivia')
    if th:
      parent = th.find_parent()
      if parent:
        ul = parent.find_next_sibling('ul') or parent.find_next('ul')
        if ul:
          for li in ul.find_all('li'):
            trivia_items.append(clean(li))
    trivia = ' | '.join(trivia_items) if trivia_items else '-'

    meta_rows.append([
        romanized,
        english,
        chinese,
        hangul,
        bpm,
        append[0],
        append[1],
        append[2],
        append[3],
        append[4],
        intro,
        trivia,
        full_len,
        game_len,
        '-',
        '-',
        '-',
        '-',
        '-',
        '-',
        '-',
        '-',
    ])

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

  try:
    ws = ss.worksheet(SHEET_NAME)
    # Clear data starting from row 2 to keep headers completely safe
    ws.batch_clear(['A2:Z'])
  except gspread.exceptions.WorksheetNotFound:
    ws = ss.add_worksheet(title=SHEET_NAME, rows=len(meta_rows) + 10, cols=25)

  ws.update(range_name='A2', values=meta_rows, value_input_option='RAW')
  print(f'=== Done. {len(meta_rows)} songs written. ===')


if __name__ == '__main__':
  try:
    FANDOM()
  except Exception as e:
    import traceback

    traceback.print_exc()
  finally:
    if not IS_GITHUB_ACTIONS:
      input('Press Enter to exit...')