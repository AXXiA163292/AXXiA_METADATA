import datetime
import json
import os
import re
import subprocess
import sys
import time

REQUIRED = {
    'playwright': 'playwright',
    'bs4': 'beautifulsoup4',
    'gspread': 'gspread',
    'google.oauth2': 'google-auth',
}

for mod, pkg in REQUIRED.items():
  try:
    __import__(mod)
  except ImportError:
    subprocess.check_call([sys.executable, '-m', 'pip', 'install', pkg])
    if mod == 'playwright':
      subprocess.check_call(
          [sys.executable, '-m', 'playwright', 'install', 'chromium']
      )

from bs4 import BeautifulSoup
import gspread
from google.oauth2.service_account import Credentials
from playwright.sync_api import sync_playwright
import requests

SPREADSHEET_ID = '1Fpp_sJbGjuKxUAcWhZOM1YqHJJrUHTWE56lO4dthhak'
SHEET_NAME = 'hololive_best'

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
SERVICE_ACCOUNT = os.path.join(SCRIPT_DIR, 'service_account.json')
IS_GITHUB_ACTIONS = os.environ.get('GITHUB_ACTIONS') == 'true'


def format_release_date(date_str):
  """Converts release date strings like 'August 29, 2026 at 5:00 AM GMT+2'

  to '29/08/26 05:00:00'.
  """
  if not date_str or date_str == '-':
    return '-'
  try:
    cleaned = re.sub(r'\s+(GMT|UTC)[+-]?\d*', '', date_str).strip()
    dt = datetime.datetime.strptime(cleaned, '%B %d, %Y at %I:%M %p')
    return dt.strftime('%d/%m/%y %H:%M:%S')
  except Exception:
    try:
      cleaned = re.sub(r'\s+(GMT|UTC)[+-]?\d*', '', date_str).strip()
      dt = datetime.datetime.strptime(cleaned, '%B %d, %Y %I:%M %p')
      return dt.strftime('%d/%m/%y %H:%M:%S')
    except Exception:
      return date_str


def get_all_song_ids_via_playwright(base_url):
  """Intercepts API network traffic or scrapes song cards directly using Playwright

  to ensure all songs are collected without missing any.
  """
  song_ids = set()
  print('Fetching song list...')

  with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    page = browser.new_page()

    # Intercept network responses to grab the raw json data if it fetches via XHR/fetch
    def handle_response(response):
      if 'api/songs' in response.url:
        try:
          data = response.json()

          def extract(item):
            if isinstance(item, dict):
              sid = item.get('musicId') or item.get('id')
              if sid:
                song_ids.add(str(sid))
              for v in item.values():
                if isinstance(v, (list, dict)):
                  extract(v)
            elif isinstance(item, list):
              for sub in item:
                extract(sub)

          extract(data)
        except Exception:
          pass

    page.on('response', handle_response)

    try:
      # Go to songs index or main page
      page.goto(f'{base_url}/songs', wait_until='networkidle', timeout=15000)
    except Exception:
      try:
        page.goto(base_url, wait_until='networkidle', timeout=15000)
      except Exception as e:
        print(f'  [warn] Navigation timeout or error: {e}')

    # Also scrape DOM elements directly in case IDs are rendered inside links/cards
    try:
      page.wait_for_selector('a[href*="/songs/"]', timeout=5000)
    except Exception:
      pass

    anchors = page.eval_on_selector_all(
        'a[href*="/songs/"]', 'elements => elements.map(e => e.href)'
    )
    for href in anchors:
      match = re.search(r'/songs/([m]?\d+)', href)
      if match:
        song_ids.add(match.group(1))

    browser.close()

  # Fallback if still empty
  if not song_ids:
    print('  [info] Using expanded fallback song ID sequence generator.')
    for i in range(1, 200):
      song_ids.add(f'm{i:04d}')

  def sort_key(sid):
    num = re.sub(r'\D', '', sid)
    return int(num) if num.isdigit() else 999999

  sorted_ids = sorted(list(song_ids), key=sort_key)
  print(f'-> {len(sorted_ids)} songs\n')
  return sorted_ids


def SCRAPE_HOLODORI():
  print('=== HOLODORI BEST HTML RENDERED SCRAPER started ===')
  base_url = 'https://holodori.best'

  headers = [
      'musicId',
      'title',
      'artist',
      'composer',
      'lyricist',
      'arranger',
      'duration',
      'releaseDate',
      'jacketUrl',
      'audioUrl',
      'hasMv',
      'pageUrl',
      'ezPlayLevel',
      'ezTotalNoteCount',
      'ezChartUrl',
      'normalPlayLevel',
      'normalTotalNoteCount',
      'normalChartUrl',
      'hardPlayLevel',
      'hardTotalNoteCount',
      'hardChartUrl',
      'expertPlayLevel',
      'expertTotalNoteCount',
      'expertChartUrl',
  ]

  meta_rows = [headers]

  song_ids = get_all_song_ids_via_playwright(base_url)
  total_songs = len(song_ids)

  with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    page = browser.new_page()

    for idx, music_id in enumerate(song_ids):
      page_url = f'{base_url}/songs/{music_id}'

      title = music_id
      artist = composer = lyricist = arranger = duration = release_date = '-'
      jacket_url = f'https://cdn.holodori.dev/assets/assetbundles/img_music_jacket_{music_id}/img_music_jacket_{music_id}.webp'
      audio_url = f'https://api.holodori.best/api/songs/{music_id}/audio'
      has_mv = 'No'

      diff_data = {
          'easy': {'level': '-', 'notes': '-', 'chartUrl': '-'}.copy(),
          'normal': {'level': '-', 'notes': '-', 'chartUrl': '-'}.copy(),
          'hard': {'level': '-', 'notes': '-', 'chartUrl': '-'}.copy(),
          'expert': {'level': '-', 'notes': '-', 'chartUrl': '-'}.copy(),
      }

      try:
        page.goto(page_url, wait_until='domcontentloaded')
        try:
          page.wait_for_selector('.song-detail-title', timeout=10000)
        except Exception:
          pass

        html_content = page.content()
        soup = BeautifulSoup(html_content, 'html.parser')

        # 1. Title
        t_elem = soup.find('h1', class_='song-detail-title')
        if t_elem and t_elem.text.strip():
          title = t_elem.text.strip()

        # 2. Jacket
        j_img = soup.find('img', class_='song-detail-jacket')
        if j_img and j_img.get('src'):
          jacket_url = j_img['src'].split('?')[0]

        # 3. Metadata (<dl class="song-meta">)
        meta_list = soup.find('dl', class_='song-meta')
        if meta_list:
          for row in meta_list.find_all('div', class_='song-meta-row'):
            dt, dd = row.find('dt'), row.find('dd')
            if dt and dd:
              k = dt.text.strip().lower()
              v = dd.text.strip()
              if 'artist' in k:
                artist = v
              elif 'composer' in k:
                composer = v
              elif 'lyricist' in k:
                lyricist = v
              elif 'arranger' in k:
                arranger = v
              elif 'duration' in k:
                duration = v
              elif 'release' in k:
                release_date = format_release_date(v)

        # 4. MV Status
        mv_section = soup.find(class_='mv-section')
        if mv_section and 'Yes' in mv_section.text:
          has_mv = 'Yes'

        # 5. Difficulties / Charts
        for diff in soup.find_all(['button', 'div'], class_='diff-card'):
          lvl_tag = diff.find('span', class_='diff-circle')
          dname_tag = diff.find('span', class_='diff-card-name')
          notes_tag = diff.find('span', class_='diff-card-notes')

          if dname_tag and dname_tag.text.strip():
            dname = dname_tag.text.strip().lower()
            target_k = None
            if 'easy' in dname or dname == 'ez':
              target_k = 'easy'
            elif 'normal' in dname or dname == 'norm':
              target_k = 'normal'
            elif 'hard' in dname:
              target_k = 'hard'
            elif 'expert' in dname or dname == 'ex':
              target_k = 'expert'

            if target_k and diff_data[target_k]['level'] == '-':
              lvl = lvl_tag.text.strip() if lvl_tag else '-'
              notes_raw = notes_tag.text.strip() if notes_tag else '-'
              notes_match = re.search(r'\d+', notes_raw.replace('\u202f', ''))
              notes = notes_match.group(0) if notes_match else '-'
              chart_url = f'https://cdn.holodori.dev/assets/resources/chart_{music_id}_{target_k}.sus/chart_{music_id}_{target_k}.png'

              diff_data[target_k] = {
                  'level': lvl,
                  'notes': notes,
                  'chartUrl': chart_url,
              }
      except Exception as err:
        print(f'  [warn] Error parsing {music_id}: {err}')

      print(f'  [{idx+1}/{total_songs}] {music_id} • {title}')

      meta_rows.append([
          music_id,
          title,
          artist,
          composer,
          lyricist,
          arranger,
          duration,
          release_date,
          jacket_url,
          audio_url,
          has_mv,
          page_url,
          diff_data['easy']['level'],
          diff_data['easy']['notes'],
          diff_data['easy']['chartUrl'],
          diff_data['normal']['level'],
          diff_data['normal']['notes'],
          diff_data['normal']['chartUrl'],
          diff_data['hard']['level'],
          diff_data['hard']['notes'],
          diff_data['hard']['chartUrl'],
          diff_data['expert']['level'],
          diff_data['expert']['notes'],
          diff_data['expert']['chartUrl'],
      ])

    browser.close()

  print('\nWriting to Google Sheets...')
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
    ws.clear()
  except gspread.exceptions.WorksheetNotFound:
    ws = ss.add_worksheet(
        title=SHEET_NAME, rows=max(len(meta_rows) + 5, 100), cols=26
    )

  CHUNK = 5000
  for i in range(0, len(meta_rows), CHUNK):
    ws.update(
        range_name=f'A{i + 1}',
        values=meta_rows[i : i + CHUNK],
        value_input_option='RAW',
    )

  print('=== Done Successfully ===')


if __name__ == '__main__':
  try:
    SCRAPE_HOLODORI()
  except Exception as e:
    import traceback

    traceback.print_exc()
  finally:
    if not IS_GITHUB_ACTIONS:
      input('Press Enter to exit...')