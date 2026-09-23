import datetime
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

import gspread
from google.oauth2.service_account import Credentials
import requests

SPREADSHEET_ID = '1Fpp_sJbGjuKxUAcWhZOM1YqHJJrUHTWE56lO4dthhak'
SHEET_NAME = 'custom'

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
SERVICE_ACCOUNT = os.path.join(SCRIPT_DIR, 'service_account.json')
DELAY = 0.3
IS_GITHUB_ACTIONS = os.environ.get('GITHUB_ACTIONS') == 'true'

S = requests.Session()
S.headers.update({'User-Agent': 'Mozilla/5.0'})
EMPTY = ['-'] * 18


def resolve_url(url):
  url = url.strip()
  if re.match(r'^[0-9a-f]{32}$', url):
    return 'https://untitledcharts.com/sonolus/levels/UnCh-' + url
  if re.match(r'^rush-\d+-\d+-\w+$', url):
    return 'https://sonolus.sbuga.com/sonolus/levels/sekai-rush-' + url[5:]
  if re.match(r'^\d+-\d+-\w+$', url):
    return 'https://sonolus.sekai.best/sonolus/levels/sekai-best-' + url
  if re.match(r'^\d+$', url):
    return (
        'https://coconut.sonolus.com/next-sekai/sonolus/levels/coconut-next-sekai-'
        + url
    )
  m = re.search(r'untitledcharts\.com/levels/(UnCh-[0-9a-f]+)/?$', url)
  if m:
    return 'https://untitledcharts.com/sonolus/levels/' + m.group(1)
  m = re.search(
      r'coconut\.sonolus\.com/next-sekai/levels/(coconut-next-sekai-\d+)/?$',
      url,
  )
  if m:
    return (
        'https://coconut.sonolus.com/next-sekai/sonolus/levels/' + m.group(1)
    )
  m = re.search(r'sonolus\.sekai\.best/levels/(sekai-best-[\w-]+)/?$', url)
  if m:
    return 'https://sonolus.sekai.best/sonolus/levels/' + m.group(1)
  m = re.search(r'sonolus\.sbuga\.com/levels/(sekai-rush-[\w-]+)/?$', url)
  if m:
    return 'https://sonolus.sbuga.com/sonolus/levels/' + m.group(1)
  m = re.search(r'cc\.milkbun\.org/levels/(chcy-[\w]+)/?$', url)
  if m:
    return 'https://cc.milkbun.org/sonolus/levels/' + m.group(1)
  m = re.search(r'ptlv\.sevenc7c\.com/levels/(ptlv-[\w]+)/?$', url)
  if m:
    return 'https://ptlv.sevenc7c.com/sonolus/levels/' + m.group(1)
  return url


def is_url(val):
  return bool(
      val
      and (
          val.startswith('http')
          or re.match(r'^[0-9a-f]{32}$', val)
          or re.match(r'^(rush-)?\d+[-\w]*$', val)
      )
  )


def fetch_level(raw_url):
  api_url = resolve_url(raw_url)
  try:
    r = S.get(api_url, timeout=15)
    data = r.json()
  except Exception:
    return EMPTY[:]

  if not (
      data.get('item')
      and data['item'].get('cover')
      and data['item'].get('bgm')
  ):
    return EMPTY[:]

  it = data['item']
  url = api_url

  if 'untitledcharts.com' in url:
    source, apiBase = 'Untitled Charts', 'https://untitledcharts.com'
  elif 'coconut.sonolus.com/next-sekai' in url:
    source, apiBase = 'Next Sekai', 'https://coconut.sonolus.com/next-sekai'
  elif 'sonolus.sekai.best' in url:
    source, apiBase = 'Sekai Best', 'https://sonolus.sekai.best'
  elif 'sonolus.sbuga.com' in url:
    source, apiBase = 'Sbuga', 'https://sonolus.sbuga.com'
  elif 'cc.milkbun.org' in url:
    source, apiBase = 'Chart Cyanvas', 'https://cc.milkbun.org'
  elif 'ptlv.sevenc7c.com' in url:
    source, apiBase = 'Potato Leave', 'https://ptlv.sevenc7c.com'
  else:
    source, apiBase = '-', ''

  def asset(srl):
    if not srl or not srl.get('url'):
      return '-'
    u = srl['url']
    return u if u.startswith('http') else apiBase + u

  level_id = it.get('name', '-')
  for prefix in ['UnCh-', 'coconut-next-sekai-', 'chcy-', 'ptlv-']:
    if level_id.startswith(prefix):
      level_id = level_id[len(prefix) :]
      break

  KNOWN_DIFFS = {'EASY', 'NORMAL', 'HARD', 'EXPERT', 'MASTER', 'APPEND'}
  META_ICONS = {'heartHollow', 'comment', 'clock', 'unlock', 'lock'}
  AGO_RE = re.compile(r'^\d+(s|min|h|d|w|mo|y)\s*ago$', re.I)

  tags = it.get('tags') or []
  difficulty = '-'
  for t in tags:
    if t.get('title', '').lstrip('#').upper() in KNOWN_DIFFS:
      difficulty = t['title'].lstrip('#').upper()
      break
  if difficulty == '-':
    for d in KNOWN_DIFFS:
      if d in (it.get('title', '')).upper():
        difficulty = d
        break

  tag_list = []
  for t in tags:
    lbl = t.get('title', '').lstrip('#')
    if lbl.upper() in KNOWN_DIFFS or t.get('icon') in META_ICONS:
      continue
    if t.get('icon') == 'tag' or (not t.get('icon') and not AGO_RE.match(lbl)):
      tag_list.append(lbl)
  tags_col = ','.join(tag_list) if tag_list else '-'

  privacy = '-'
  for t in tags:
    if t.get('icon') in ('unlock', 'lock'):
      privacy = t.get('title', '-')
      break

  likes = comments_count = '-'
  for t in tags:
    if t.get('icon') == 'heartHollow':
      likes = t.get('title', '-')
    if t.get('icon') == 'comment':
      comments_count = t.get('title', '-')

  upload_date = '-'
  if source != 'Untitled Charts':
    for t in tags:
      if not t.get('title') or t.get('icon'):
        continue
      m = re.match(r'^(\d+)(mo|w|d|h)\s*ago$', t['title'].strip(), re.I)
      if not m:
        continue

      num, unit = int(m.group(1)), m.group(2).lower()
      now = datetime.date.today()
      if unit == 'mo':
        now = now.replace(month=((now.month - 1 - num) % 12) + 1)
      elif unit == 'w':
        now -= datetime.timedelta(weeks=num)
      elif unit == 'd':
        now -= datetime.timedelta(days=num)
      upload_date = now.strftime('%m/%Y')
      break

  staff_pick = any(
      t.get('icon') == 'trophy' and 'staff' in t.get('title', '').lower()
      for t in tags
  )

  name = it.get('name', '')
  if source == 'Untitled Charts':
    level_url = f'https://untitledcharts.com/levels/{name}/'
  elif source == 'Next Sekai':
    level_url = f'https://coconut.sonolus.com/next-sekai/levels/{name}'
  elif source == 'Sekai Best':
    level_url = f'https://sonolus.sekai.best/levels/{name}'
  elif source == 'Sbuga':
    level_url = f'https://sonolus.sbuga.com/levels/{name}'
  elif source == 'Chart Cyanvas':
    level_url = f'https://cc.milkbun.org/levels/{name}'
  else:
    level_url = '-'

  if source == 'Untitled Charts' and level_url != '-':
    try:
      page_r = S.get(level_url, timeout=15)
      page_h = page_r.text
      dm = re.search(
          r'article:published_time"[^>]*content="(\d{4})-(\d{2})-(\d{2})', page_h
      )
      if dm:
        upload_date = f'{dm.group(3)}/{dm.group(2)}/{dm.group(1)}'
      lm2 = re.search(
          r'stat-label[^>]*>Likes</span>[\s\S]{0,100}?stat-value[^>]*>(\d+)<',
          page_h,
      )
      if lm2:
        likes = lm2.group(1)
      cm2 = re.search(
          r'stat-label[^>]*>Comments</span>[\s\S]{0,100}?stat-value[^>]*>(\d+)<',
          page_h,
      )
      if cm2:
        comments_count = cm2.group(1)
    except Exception:
      pass

  return [
      level_id,
      it.get('title') or '-',
      it.get('artists') or '-',
      it.get('author') or '-',
      it.get('rating') or '-',
      difficulty,
      data.get('description') or '-',
      tags_col,
      str(staff_pick),
      privacy,
      asset(it.get('cover')),
      asset(it.get('bgm')),
      asset(it.get('preview')),
      asset(it.get('data')),
      level_url,
      likes,
      comments_count,
      upload_date,
  ]


def CUSTOM():
  print('=== CUSTOM() started ===')
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

  ws = (
      gspread.authorize(creds).open_by_key(SPREADSHEET_ID).worksheet(SHEET_NAME)
  )

  col_a = ws.col_values(1)
  col_c = ws.col_values(3)
  ids = col_a[1:]
  urls = col_c[1:]

  if not urls:
    print('No data in column C.')
    return

  cleaned_urls = [(raw or '').strip().rstrip('/') for raw in urls]
  ws.update(
      range_name=f'C2:C{1+len(cleaned_urls)}',
      values=[[v] for v in cleaned_urls],
  )

  updates = []
  total_urls = len(cleaned_urls)

  for i, raw in enumerate(cleaned_urls):
    row = i + 2
    song_id = ids[i].strip() if i < len(ids) else '-'
    if not raw:
      continue

    if not is_url(raw):
      if song_id and song_id != '-':
        print(f'  [{i+1}/{total_urls}] {song_id} • {raw} — not a URL')
      else:
        print(f'  [{i+1}/{total_urls}] {raw} — not a URL')
      updates.append((row, EMPTY[:]))
      continue

    time.sleep(DELAY)
    values = fetch_level(raw)
    song_name = values[1] if values[1] != '-' else raw
    if song_id and song_id != '-':
      print(f'  [{i+1}/{total_urls}] {song_id} • {song_name}')
    else:
      print(f'  [{i+1}/{total_urls}] {song_name}')
    updates.append((row, values))

  if updates:
    data_batch = [
        {'range': f'{SHEET_NAME}!D{r}:U{r}', 'values': [v]} for r, v in updates
    ]
    ws.spreadsheet.values_batch_update(
        {'valueInputOption': 'RAW', 'data': data_batch}
    )

  print(f'=== Done. {len(updates)} rows written. ===')


if __name__ == '__main__':
  try:
    CUSTOM()
  except Exception as e:
    import traceback

    traceback.print_exc()
  finally:
    if not IS_GITHUB_ACTIONS:
      input('Press Enter to exit...')