
import asyncio
import datetime
import json
import os
import re
import subprocess
import sys
from urllib.parse import urljoin, urlparse

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
            subprocess.check_call([
                sys.executable, '-m', 'playwright', 'install', 'chromium'
            ])

from bs4 import BeautifulSoup
import gspread
from google.oauth2.service_account import Credentials
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeoutError

SPREADSHEET_ID = '1Fpp_sJbGjuKxUAcWhZOM1YqHJJrUHTWE56lO4dthhak'
SHEET_NAME = 'hololive'
BASE_URL = 'https://holodori.best'
SONGS_URL = f'{BASE_URL}/songs'

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
SERVICE_ACCOUNT = os.path.join(SCRIPT_DIR, 'service_account.json')
IS_GITHUB_ACTIONS = os.environ.get('GITHUB_ACTIONS') == 'true'

CONCURRENCY = 5
PAGE_TIMEOUT = 25000
DISCOVERY_SCROLLS = 30

HEADERS = [
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

DIFFICULTIES = ['easy', 'normal', 'hard', 'expert']

DIFF_NAMES = {
    'easy': ['easy', 'ez'],
    'normal': ['normal', 'norm'],
    'hard': ['hard'],
    'expert': ['expert', 'ex'],
}


def clean(value):
    if value is None:
        return '-'
    value = re.sub(r'\s+', ' ', str(value)).strip()
    return value if value else '-'


def absolute_url(value, page_url):
    if not value:
        return '-'
    value = value.strip()
    if value.startswith(('data:', 'blob:', 'javascript:')):
        return '-'
    return urljoin(page_url, value)


def format_release_date(value):
    value = clean(value)
    if value == '-':
        return '-'

    patterns = [
        '%B %d, %Y at %I:%M %p',
        '%B %d, %Y %I:%M %p',
        '%B %d, %Y',
    ]

    cleaned = re.sub(r'\s+(UTC|GMT)([+-]\d{1,2})?', '', value).strip()

    for pattern in patterns:
        try:
            dt = datetime.datetime.strptime(cleaned, pattern)
            return dt.strftime('%d/%m/%y %H:%M:%S')
        except ValueError:
            continue

    return value


def get_label_value(soup, wanted):
    wanted = wanted.casefold()

    for dt in soup.find_all('dt'):
        label = clean(dt.get_text(' ', strip=True)).casefold()
        dd = dt.find_next_sibling('dd')

        if dd and wanted in label:
            return clean(dd.get_text(' ', strip=True))

    # Fallback for label/value structures not using dt/dd.
    for element in soup.find_all(['div', 'li', 'p']):
        text = clean(element.get_text(' ', strip=True))
        if ':' in text:
            label, value = text.split(':', 1)
            if label.strip().casefold() == wanted:
                return clean(value)

    return '-'


def extract_difficulties(soup, body_text, music_id, page_url):
    result = {
        diff: {'level': '-', 'notes': '-', 'chartUrl': '-'}
        for diff in DIFFICULTIES
    }

    # First inspect rendered elements containing difficulty information.
    candidates = soup.find_all(
        lambda tag: tag.name in ['button', 'div', 'a', 'section', 'article']
        and tag.get_text(' ', strip=True)
        and len(tag.get_text(' ', strip=True)) < 500
    )

    for element in candidates:
        text = clean(element.get_text(' ', strip=True))
        lowered = text.casefold()

        for diff in DIFFICULTIES:
            if result[diff]['level'] != '-':
                continue

            if not any(name in lowered for name in DIFF_NAMES[diff]):
                continue

            level_match = re.search(
                r'(?:^|\s)(\d{1,2})(?:\s|$)',
                text
            )
            notes_match = re.search(
                r'([\d,]+)\s*combo',
                text,
                re.I
            )

            if level_match and notes_match:
                result[diff]['level'] = level_match.group(1)
                result[diff]['notes'] = notes_match.group(1).replace(',', '')

    # Text fallback based on the actual visible difficulty listing.
    normalized_text = re.sub(r'\s+', ' ', body_text)

    for diff in DIFFICULTIES:
        if result[diff]['level'] != '-':
            continue

        names = '|'.join(DIFF_NAMES[diff])
        match = re.search(
            rf'(\d{{1,2}})\s*(?:{names})\s*([\d,]+)\s*combo',
            normalized_text,
            re.I
        )

        if match:
            result[diff]['level'] = match.group(1)
            result[diff]['notes'] = match.group(2).replace(',', '')

    # Extract only real chart URLs found in the rendered page.
    for element in soup.find_all(['a', 'img', 'source', 'iframe']):
        raw_url = (
            element.get('href')
            or element.get('src')
            or element.get('data-src')
            or ''
        )

        if not raw_url:
            continue

        url = absolute_url(raw_url, page_url)
        lowered = url.casefold()

        if url == '-':
            continue

        for diff in DIFFICULTIES:
            if result[diff]['chartUrl'] != '-':
                continue

            diff_names = DIFF_NAMES[diff]
            matches_id = music_id.casefold() in lowered
            matches_diff = any(name in lowered for name in diff_names)
            looks_like_chart = any(
                token in lowered
                for token in ['chart', '.sus', 'score', 'difficulty']
            )

            if matches_id and matches_diff and looks_like_chart:
                result[diff]['chartUrl'] = url

    return result


def extract_song(soup, body_text, music_id, page_url):
    title_tag = soup.find('h1')
    title = clean(title_tag.get_text(' ', strip=True) if title_tag else '-')

    if title == '-':
        title = '-'

    artist = get_label_value(soup, 'artist')
    composer = get_label_value(soup, 'composer')
    lyricist = get_label_value(soup, 'lyricist')
    arranger = get_label_value(soup, 'arranger')
    duration = get_label_value(soup, 'duration')
    release_date = format_release_date(
        get_label_value(soup, 'release date')
    )

    mv_match = re.search(
        r'Has In-game MV\?\s*(Yes|No)',
        body_text,
        re.I
    )
    has_mv = mv_match.group(1).capitalize() if mv_match else '-'

    # Use actual image URLs from the song page.
    jacket_url = '-'

    for img in soup.find_all('img'):
        src = img.get('src') or img.get('data-src') or ''
        alt = (img.get('alt') or '').casefold()
        lowered = src.casefold()

        if (
            'jacket' in lowered
            or 'jacket' in alt
            or music_id.casefold() in lowered
            and 'img' in lowered
        ):
            jacket_url = absolute_url(src, page_url)
            break

    # Use actual audio/source elements or audio links.
    audio_url = '-'

    for element in soup.find_all(['audio', 'source', 'a']):
        raw_url = (
            element.get('src')
            or element.get('href')
            or ''
        )

        if raw_url and (
            'audio' in raw_url.casefold()
            or raw_url.casefold().endswith(('.mp3', '.ogg', '.wav', '.m4a'))
        ):
            audio_url = absolute_url(raw_url, page_url)
            break

    difficulties = extract_difficulties(
        soup,
        body_text,
        music_id,
        page_url
    )

    return [
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
        difficulties['easy']['level'],
        difficulties['easy']['notes'],
        difficulties['easy']['chartUrl'],
        difficulties['normal']['level'],
        difficulties['normal']['notes'],
        difficulties['normal']['chartUrl'],
        difficulties['hard']['level'],
        difficulties['hard']['notes'],
        difficulties['hard']['chartUrl'],
        difficulties['expert']['level'],
        difficulties['expert']['notes'],
        difficulties['expert']['chartUrl'],
    ]


async def discover_song_ids(browser):
    print('Discovering actual songs from the website...')

    page = await browser.new_page()
    await page.goto(
        SONGS_URL,
        wait_until='domcontentloaded',
        timeout=PAGE_TIMEOUT
    )

    try:
        await page.wait_for_selector('a[href*="/songs/"]', timeout=12000)
    except PlaywrightTimeoutError:
        pass

    song_ids = set()
    previous_count = -1
    stable_rounds = 0

    for _ in range(DISCOVERY_SCROLLS):
        hrefs = await page.locator('a[href*="/songs/"]').evaluate_all(
            '(els) => els.map(e => e.href)'
        )

        for href in hrefs:
            match = re.search(r'/songs/(m\d+)(?:[/?#]|$)', href, re.I)
            if match:
                song_ids.add(match.group(1).lower())

        if len(song_ids) == previous_count:
            stable_rounds += 1
        else:
            stable_rounds = 0

        if stable_rounds >= 4:
            break

        previous_count = len(song_ids)

        await page.evaluate(
            'window.scrollTo(0, document.body.scrollHeight)'
        )
        await page.wait_for_timeout(350)

    await page.close()

    if not song_ids:
        raise RuntimeError(
            'No real song links were found on /songs. '
            'The scraper stopped instead of inventing IDs.'
        )

    def sort_key(song_id):
        match = re.search(r'\d+', song_id)
        return int(match.group()) if match else 999999

    result = sorted(song_ids, key=sort_key)

    print(f'Found {len(result)} actual song links.')
    return result


async def scrape_one_song(browser, semaphore, index, total, music_id):
    page_url = f'{BASE_URL}/songs/{music_id}'

    async with semaphore:
        page = await browser.new_page()

        try:
            response = await page.goto(
                page_url,
                wait_until='domcontentloaded',
                timeout=PAGE_TIMEOUT
            )

            if response is None:
                raise RuntimeError('No HTTP response')

            if response.status >= 400:
                raise RuntimeError(f'HTTP {response.status}')

            try:
                await page.wait_for_selector('h1', timeout=10000)
            except PlaywrightTimeoutError:
                pass

            # Allow client-rendered metadata to finish loading.
            await page.wait_for_timeout(300)

            html = await page.content()
            body_text = await page.locator('body').inner_text()

            soup = BeautifulSoup(html, 'html.parser')

            title_tag = soup.find('h1')
            title = clean(
                title_tag.get_text(' ', strip=True)
                if title_tag else '-'
            )

            # Do not write a fabricated ID-as-title record.
            if title == '-' or title.casefold() == music_id.casefold():
                raise RuntimeError(
                    'Song page did not provide a real title'
                )

            row = extract_song(
                soup,
                body_text,
                music_id,
                page_url
            )

            print(
                f'[{index}/{total}] {music_id} | {row[1]}'
            )

            return row

        except Exception as error:
            print(f'[failed] {music_id}: {error}')
            return None

        finally:
            await page.close()


async def scrape_website():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)

        try:
            song_ids = await discover_song_ids(browser)
            semaphore = asyncio.Semaphore(CONCURRENCY)

            tasks = [
                scrape_one_song(
                    browser,
                    semaphore,
                    index,
                    len(song_ids),
                    music_id
                )
                for index, music_id in enumerate(song_ids, 1)
            ]

            results = await asyncio.gather(*tasks)

            rows = [row for row in results if row is not None]
            failed = len(song_ids) - len(rows)

            print('\nScraping summary')
            print(f'  Discovered: {len(song_ids)}')
            print(f'  Successfully scraped: {len(rows)}')
            print(f'  Failed: {failed}')

            if not rows:
                raise RuntimeError(
                    'No song pages were successfully scraped. '
                    'Google Sheets was not modified.'
                )

            return rows

        finally:
            await browser.close()


def write_to_google_sheets(rows):
    print('\nConnecting to Google Sheets...')

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
    spreadsheet = gc.open_by_key(SPREADSHEET_ID)

    values = [HEADERS] + rows

    try:
        worksheet = spreadsheet.worksheet(SHEET_NAME)
    except gspread.exceptions.WorksheetNotFound:
        worksheet = spreadsheet.add_worksheet(
            title=SHEET_NAME,
            rows=max(100, len(values) + 10),
            cols=len(HEADERS)
        )

    worksheet.clear()
    worksheet.resize(
        rows=max(100, len(values) + 10),
        cols=len(HEADERS)
    )

    chunk_size = 1000

    for start in range(0, len(values), chunk_size):
        chunk = values[start:start + chunk_size]
        end_row = start + len(chunk)

        worksheet.update(
            range_name=f'A{start + 1}:X{end_row}',
            values=chunk,
            value_input_option='RAW'
        )

        print(f'  Written rows {start + 1}-{end_row}')

    print(
        f'\nDone. {len(rows)} songs written to {SHEET_NAME}.'
    )


def SCRAPE_HOLODORI():
    print('=== HOLODORI WEBSITE SCRAPER ===')

    rows = asyncio.run(scrape_website())

    # Do not clear the existing sheet unless scraping produced data.
    write_to_google_sheets(rows)

    print('=== SCRAPE COMPLETED ===')


if __name__ == '__main__':
    try:
        SCRAPE_HOLODORI()
    except Exception:
        import traceback
        traceback.print_exc()
    finally:
        if not IS_GITHUB_ACTIONS:
            input('Press Enter to exit...')