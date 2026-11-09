import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

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
        print(f'Installing missing library: {pip_name}...')
        subprocess.check_call([sys.executable, '-m', 'pip', 'install', pip_name])

from bs4 import BeautifulSoup, Tag
import gspread
from google.oauth2.service_account import Credentials
import requests

SPREADSHEET_ID = '1Fpp_sJbGjuKxUAcWhZOM1YqHJJrUHTWE56lO4dthhak'
SHEET_NAME = 'fandom'
SCRIPT_DIR = Path(__file__).resolve().parent
SERVICE_ACCOUNT = SCRIPT_DIR / 'service_account.json'
CACHE_FILE = SCRIPT_DIR / 'fandom_cache.json'
API = 'https://projectsekai.fandom.com/api.php'
DELAY = 0.4
IS_GITHUB_ACTIONS = os.environ.get('GITHUB_ACTIONS') == 'true'

HEADERS = [
    'id', 'fandomTitle', 'url', 'unit', 'jp', 'tc', 'en', 'kr', 'cn', 'type',
    'romanizedTitle', 'translatedTitle', 'chineseTitle', 'hangulTitle', 'bpm',
    'apRelease_jp', 'apRelease_tc', 'apRelease_en', 'apRelease_kr',
    'apRelease_cn', 'intro', 'trivia', 'fullLength', 'gameLength', 'audioLinks',
    'romajiLyrics', 'japaneseLyrics', 'englishLyrics', 'koreanLyrics',
    'romanjaLyrics', 'pinyinLyrics', 'chineseLyrics',
]

S = requests.Session()
S.headers.update({'User-Agent': 'Mozilla/5.0 (compatible; AXXiA-Metadata/1.0)'})

MONTHS = {
    'january': '01', 'february': '02', 'march': '03', 'april': '04',
    'may': '05', 'june': '06', 'july': '07', 'august': '08',
    'september': '09', 'october': '10', 'november': '11', 'december': '12',
}

AUDIO_ORDER = ['Full Version', 'English Version', 'Instrumental']
KNOWN_CHARS = {
    'Hoshino Ichika', 'Tenma Saki', 'Mochizuki Honami', 'Hinomori Shiho',
    'Hanasato Minori', 'Kiritani Haruka', 'Momoi Airi', 'Hinomori Shizuku',
    'Azusawa Kohane', 'Shiraishi An', 'Shinonome Akito', 'Aoyagi Toya',
    'Tenma Tsukasa', 'Otori Emu', 'Kusanagi Nene', 'Kamishiro Rui',
    'Yoisaki Kanade', 'Asahina Mafuyu', 'Shinonome Ena', 'Akiyama Mizuki',
    'Hatsune Miku', 'Kagamine Rin', 'Kagamine Len', 'Megurine Luka', 'MEIKO', 'KAITO',
}


def fetch(page, retries=3):
    for attempt in range(retries):
        try:
            response = S.get(
                API,
                params={'action': 'parse', 'page': page, 'prop': 'text', 'format': 'json'},
                timeout=30,
            )
            response.raise_for_status()
            data = response.json()
            if 'error' in data:
                print(f'  API error for {page}: {data["error"]}')
                return None
            return data
        except (requests.RequestException, ValueError) as exc:
            print(f'  Fetch {attempt + 1}/{retries} failed for {page}: {exc}')
            if attempt + 1 < retries:
                time.sleep(1.5 * (attempt + 1))
    return None


def clean(value):
    if value is None:
        return '-'
    text = value.get_text(' ', strip=True) if isinstance(value, Tag) else str(value)
    text = re.sub(r'\s+', ' ', text).strip()
    text = text.replace('\u2014', '-').replace('\u2013', '-')
    return text or '-'


def get_field(infobox, *sources):
    if not infobox:
        return '-'
    for source in sources:
        item = infobox.find(attrs={'data-source': source})
        value = item.find(class_=re.compile(r'pi-data-value')) if item else None
        if value:
            result = clean(value)
            if result != '-':
                return result
    return '-'


def normalize_date(value):
    value = re.sub(r'\s*[\[(]\s*\d+\s*[\])][\[(\d\])]*$', '', value).strip()
    match = re.match(r'([A-Za-z]+)\s+(\d{1,2}),?\s+(\d{4})', value)
    if match:
        month = MONTHS.get(match.group(1).lower())
        if month:
            return f'{int(match.group(2)):02d}/{month}/{match.group(3)}'
    match = re.match(r'^(\d{1,2})/(\d{1,2})/(\d{4})$', value)
    if match:
        return f'{int(match.group(2)):02d}/{int(match.group(1)):02d}/{match.group(3)}'
    return value


def get_date_field(infobox, *sources):
    value = get_field(infobox, *sources)
    return '-' if value == '-' else normalize_date(value)


def get_song_id(value):
    raw = str(value or '').strip()
    raw = re.sub(r'\s*\[.*$', '', raw).strip()
    if not raw or re.fullmatch(r'-+', raw) or raw.upper() == 'TBA':
        return 'TBA'
    match = re.match(r'^(\d+)', raw)
    return match.group(1) if match else None


def valid_cache_entry(value):
    return (
        isinstance(value, list)
        and len(value) == 22
        and any(str(item).strip() not in ('', '-') for item in value)
    )


def get_client():
    scopes = [
        'https://www.googleapis.com/auth/spreadsheets',
        'https://www.googleapis.com/auth/drive',
    ]
    raw = os.environ.get('GCP_SA_KEY')
    if raw:
        credentials = Credentials.from_service_account_info(json.loads(raw), scopes=scopes)
    else:
        credentials = Credentials.from_service_account_file(str(SERVICE_ACCOUNT), scopes=scopes)
    return gspread.authorize(credentials)


def fix_artist(artist):
    artist = re.sub(r'VIRTUAL SINGER 1$', 'VIRTUAL SINGER', artist)
    artist = re.sub(r' and ', ', ', artist)
    artist = re.sub(r'\s+&\s+', ', ', artist)
    artist = artist.replace('All Original Units', 'ALL ORIGINAL UNITS')
    artist = artist.replace('Project SEKAI x Ensemble Stars', 'Project SEKAI × Ensemble Stars!!')
    artist = re.sub(r'25 ji,|25-ji,', '25-ji', artist)
    artist = re.sub(r'Nightcord de\b(?!\.)', 'Nightcord de.', artist)
    return artist.replace('Kyujitsu, Shumijin Doushi de.', 'Kyujitsu Shumijin Doushi de.')


def find_song_html(title):
    candidates = list(dict.fromkeys([
        title,
        title + ' (song)',
        title.replace('[', '(').replace(']', ')'),
    ]))
    for candidate in candidates:
        result = fetch(candidate)
        if not result or 'parse' not in result:
            continue
        page_html = result['parse']['text'].get('*', '')
        page_soup = BeautifulSoup(page_html, 'html.parser')
        if page_soup.find('aside', class_=re.compile(r'portable-infobox')):
            return page_soup
    return None


def extract_audio_and_versions(soup):
    audio_entries = []
    audio_heading = soup.find(id='Audio')
    if audio_heading:
        parent = audio_heading.find_parent()
        table = parent.find_next('table') if parent else None
        if table:
            for tr in table.find_all('tr'):
                cells = tr.find_all('td')
                if len(cells) < 2:
                    continue
                source = tr.find('source') or tr.find('audio')
                if not source:
                    continue
                audio_url = source.get('src', '-')
                if 'static.wikia.nocookie.net' not in audio_url or '.ogg' not in audio_url:
                    continue
                raw_version = clean(cells[1])
                duration = '-'
                if len(cells) > 2:
                    candidate_duration = clean(cells[2])
                    if re.fullmatch(r'\d+:\d{2}', candidate_duration):
                        duration = candidate_duration
                if raw_version.count('(') > raw_version.count(')'):
                    raw_version += ')' * (raw_version.count('(') - raw_version.count(')'))
                version = raw_version
                for inner in reversed(re.findall(r'\(([^)]*)\)', raw_version)):
                    if (
                        'Version' in inner or 'Instrumental' in inner
                        or inner.startswith('Full/Game')
                        or re.match(r'^Game\s*[-–]', inner)
                        or re.match(r'^Game\s+Version', inner)
                    ):
                        version = inner
                        break
                if version.count('(') > version.count(')'):
                    version += ')' * (version.count('(') - version.count(')'))
                version = re.sub(r'Full/Game Size', 'Full/Game Version', version, flags=re.I)
                version = re.sub(r'^Game(?!\s+Version)(\s+-)', r'Game Version\1', version)
                parts = version.split(' - ')
                version_type = parts[0].strip().replace('(', '').replace(')', '').strip()
                artist = ' - '.join(parts[1:]).strip().replace('(', '').replace(')', '').strip() if len(parts) > 1 else ''
                audio_entries.append({
                    'versionType': version_type,
                    'artist': fix_artist(artist),
                    'url': audio_url,
                    'duration': duration,
                })

    non_instrumental_indices = [
        index for index, entry in enumerate(audio_entries)
        if entry['versionType'] not in ('Instrumental', 'Menu Instrumental', 'Menu Instrumental - New')
    ]
    versions_map = {}
    versions_heading = soup.find(id='Versions')
    if versions_heading:
        parent = versions_heading.find_parent()
        if parent:
            tab_contents = []
            sibling = parent
            while True:
                sibling = sibling.find_next_sibling()
                if not sibling or (sibling.name and re.match(r'h[12]', sibling.name)):
                    break
                tab_contents.extend(sibling.find_all('div', class_=re.compile(r'wds-tab__content')))
            for tab_index, block in enumerate(tab_contents):
                unit = ''
                first_row = block.find('tr')
                if first_row:
                    link = first_row.find('a', href=re.compile(r'/wiki/'))
                    if link:
                        candidate = link.get('title', '') or link.get_text(strip=True)
                        unit = 'OTHER' if candidate.casefold() in {name.casefold() for name in KNOWN_CHARS} else candidate
                singers = []
                for bold in block.find_all('b'):
                    link = bold.find('a')
                    if link:
                        singer = re.sub(r'^w:c:[^:]+:', '', link.get_text(strip=True)).replace('&amp;', '&').strip()
                        if singer != unit:
                            singers.append(singer)
                if tab_index < len(non_instrumental_indices):
                    versions_map[non_instrumental_indices[tab_index]] = {
                        'unit': unit or 'OTHER',
                        'singers': ','.join(singers),
                    }

    archive, normal, special = [], [], []
    for index, entry in enumerate(audio_entries):
        entry['origIdx'] = index
        if entry['artist'] == 'VBS Archive':
            archive.append(entry)
        elif entry['versionType'] in AUDIO_ORDER:
            special.append((AUDIO_ORDER.index(entry['versionType']), entry))
        else:
            normal.append(entry)
    special.sort(key=lambda item: item[0])
    sorted_audio = normal + [item[1] for item in special] + archive

    audio_parts, new_audio_parts = [], []
    non_menu_count = 0
    for entry in sorted_audio:
        version_type = entry['versionType']
        is_menu = version_type == 'Menu Instrumental'
        is_new_menu = (
            version_type in ('Menu Instrumental', 'Menu Instrumental - New') and entry['artist'] == 'New'
        ) or version_type == 'Menu Instrumental - New'
        version_data = versions_map.get(entry['origIdx'], {'unit': '', 'singers': ''})
        singers = '' if is_menu else version_data['singers']
        unit = '' if is_menu else (version_data['unit'] or 'OTHER')
        part = f"{version_type};{entry['artist']};{entry['duration']};{entry['url']};{singers};{unit}"
        if is_new_menu:
            new_audio_parts.append('99;' + part)
        elif is_menu:
            audio_parts.append('00;' + part)
        else:
            non_menu_count += 1
            audio_parts.append(f'{non_menu_count:02d};' + part)

    used = {entry['origIdx'] for entry in sorted_audio}
    tab_labels = []
    if versions_heading:
        parent = versions_heading.find_parent()
        if parent:
            tab_labels = [a.get_text(strip=True) for a in parent.find_all('a', class_=re.compile(r'wds-tabs__tab'))]
            if not tab_labels:
                for div in parent.find_all('div', class_=re.compile(r'wds-tabs__tab-label')):
                    link = div.find('a')
                    if link:
                        tab_labels.append(link.get_text(strip=True))
    for tab_index, original_index in enumerate(non_instrumental_indices):
        if original_index not in used and original_index in versions_map:
            data = versions_map[original_index]
            tab_label = tab_labels[tab_index] if tab_index < len(tab_labels) else '-'
            non_menu_count += 1
            audio_parts.append(
                f"{non_menu_count:02d};-;{tab_label};-;;{data['singers']};{data['unit'] or 'OTHER'}"
            )
    all_parts = audio_parts + new_audio_parts
    audio_links = '[' + ';'.join(all_parts) + ']' if all_parts else '-'
    return audio_links


def extract_lyrics(soup):
    lyrics = {}
    heading = soup.find(id='Lyrics')
    if not heading:
        return lyrics
    parent = heading.find_parent()
    if not parent:
        return lyrics
    wrapper = parent.find_next(class_=re.compile(r'wds-tabs__wrapper'))
    labels = [a.get_text(strip=True) for a in wrapper.find_all('a')] if wrapper else []
    poems = []
    for element in parent.find_all_next():
        if not isinstance(element, Tag):
            continue
        if element.name and re.match(r'h[12]', element.name):
            break
        if 'poem' not in (element.attrs.get('class') or []):
            continue
        for span in element.find_all('span', class_=re.compile(r'copy')):
            span.decompose()
        text = re.sub(r'[ \t]+', ' ', element.get_text('\n'))
        text = re.sub(r'\n{3,}', '\n\n', text).strip()
        if len(text) > 5:
            poems.append(text)
    for index, label in enumerate(labels):
        if index < len(poems):
            lyrics[label] = poems[index]
    return lyrics


def parse_song_page(soup):
    infobox = soup.find('aside', class_=re.compile(r'portable-infobox'))
    if not infobox:
        return None

    romanized = clean(infobox.find('h2', class_=re.compile(r'pi-title')))
    english = get_field(infobox, 'english', 'official english')
    chinese = get_field(infobox, 'simp cn')
    hangul = get_field(infobox, 'hangul')
    bpm = get_field(infobox, 'bpm')

    duration = get_field(infobox, 'duration')
    full_length = game_length = '-'
    if duration != '-':
        full_match = re.search(r'(\d+:\d{2})\s*\(Full\)', duration, re.I)
        game_match = re.search(r'(\d+:\d{2})\s*\(Game(?:\s*Size)?\)', duration, re.I)
        if full_match:
            full_length = full_match.group(1)
        if game_match:
            game_length = game_match.group(1)
        if full_length == '-' and game_length == '-':
            fallback = re.search(r'(\d+:\d{2})', duration)
            if fallback:
                full_length = fallback.group(1)

    append_dates = [
        get_date_field(infobox, 'append_date', 'app_date'),
        get_date_field(infobox, 'append_tw_date', 'app_tw_date'),
        get_date_field(infobox, 'append_en_date', 'app_en_date'),
        get_date_field(infobox, 'append_kr_date', 'app_kr_date'),
        get_date_field(infobox, 'append_cn_date', 'app_cn_date'),
    ]
    panel = infobox.find('section', class_=re.compile(r'wds-tabber'))
    if panel:
        for index, block in enumerate(panel.find_all('div', class_=re.compile(r'wds-tab__content'))[:5]):
            for label in block.find_all(class_=re.compile(r'pi-data-label')):
                if label.get_text(strip=True).upper() == 'APPEND':
                    value = label.find_next(class_=re.compile(r'pi-data-value'))
                    if value:
                        append_dates[index] = normalize_date(clean(value))

    intro = '-'
    aside = soup.find('aside')
    if aside:
        for paragraph in aside.find_all_next('p'):
            candidate = clean(paragraph)
            if len(candidate) > 30:
                intro = candidate
                break

    trivia_items = []
    trivia_heading = soup.find(id='Trivia')
    if trivia_heading:
        parent = trivia_heading.find_parent()
        if parent:
            list_tag = parent.find_next_sibling('ul') or parent.find_next('ul')
            if list_tag:
                trivia_items = [clean(item) for item in list_tag.find_all('li')]
    trivia = ' | '.join(trivia_items) if trivia_items else '-'

    audio_links = extract_audio_and_versions(soup)
    lyrics = extract_lyrics(soup)

    return [
        romanized, english, chinese, hangul, bpm,
        *append_dates,
        intro, trivia, full_length, game_length, audio_links,
        lyrics.get('Romaji', '-'),
        lyrics.get('Japanese', '-'),
        lyrics.get('English', '-'),
        lyrics.get('Korean', '-'),
        lyrics.get('Romanja', '-'),
        lyrics.get('Pinyin', '-'),
        lyrics.get('Chinese', '-'),
    ]


def parse_song_list():
    result = fetch('Song_List')
    if not result or 'parse' not in result:
        raise RuntimeError('Could not fetch Song_List from FANDOM.')
    page_html = result['parse']['text']['*'].replace('724&#91;1&#93;', '724')
    soup = BeautifulSoup(page_html, 'html.parser')
    songs = []
    section = ''
    for tag in soup.find_all(['h2', 'tbody']):
        if tag.name == 'h2':
            headline = tag.find(class_='mw-headline')
            if headline:
                section = headline.get_text(strip=True).replace(' Songs', '')
            continue
        for tr in tag.find_all('tr'):
            cells = tr.find_all('td')
            if len(cells) < 7:
                continue
            song_id = get_song_id(clean(cells[0]))
            if not song_id or (song_id != 'TBA' and not song_id.isdigit()):
                continue
            link = cells[2].find('a', href=re.compile(r'^/wiki/'))
            url = 'https://projectsekai.fandom.com' + link['href'] if link else ''
            title = clean(cells[2])
            if title == '-':
                continue
            cell = lambda index: clean(cells[index]) if len(cells) > index else '-'
            songs.append([
                song_id, title, url, cell(4), cell(5), cell(6),
                cell(7), cell(8), cell(9), section,
            ])
    songs.sort(key=lambda row: (0, int(row[0])) if row[0].isdigit() else (1, row[1].casefold()))
    return songs


def load_cache():
    if IS_GITHUB_ACTIONS or not CACHE_FILE.exists():
        if IS_GITHUB_ACTIONS:
            print('  GitHub Actions: cache loading skipped.')
        return {}
    try:
        with CACHE_FILE.open('r', encoding='utf-8') as file:
            loaded = json.load(file)
        if not isinstance(loaded, dict):
            return {}
        cache = {key: value for key, value in loaded.items() if valid_cache_entry(value)}
        print(f'  Cache loaded: {len(cache)} valid songs.')
        return cache
    except (OSError, json.JSONDecodeError) as exc:
        print(f'  Cache could not be loaded: {exc}')
        return {}


def write_sheet(rows):
    client = get_client()
    workbook = client.open_by_key(SPREADSHEET_ID)
    try:
        worksheet = workbook.worksheet(SHEET_NAME)
    except gspread.WorksheetNotFound:
        worksheet = workbook.add_worksheet(
            title=SHEET_NAME,
            rows=max(1000, len(rows) + 1),
            cols=len(HEADERS),
        )
        worksheet.update(range_name='A1', values=[HEADERS], value_input_option='RAW')
    worksheet.resize(rows=max(1000, len(rows) + 1), cols=len(HEADERS))
    worksheet.batch_clear(['A2:AF'])
    if rows:
        worksheet.update(range_name='A2', values=rows, value_input_option='RAW')


def FANDOM():
    print('=== FANDOM() started ===')
    cache = load_cache()
    song_rows = parse_song_list()
    print(f'  Song list: {len(song_rows)} songs.')

    meta_rows = []
    failed = 0
    new_entries = 0
    total_new = sum(
        1 for row in song_rows
        if not valid_cache_entry(cache.get(f'{row[0]}|{row[1]}'))
    )

    for index, song in enumerate(song_rows, start=1):
        song_id, title = song[0], song[1]
        cache_key = f'{song_id}|{title}'
        cached = cache.get(cache_key)
        if not IS_GITHUB_ACTIONS and valid_cache_entry(cached):
            meta_rows.append(cached)
            continue

        new_entries += 1
        print(f'  [{new_entries}/{total_new}] {song_id} • {title}')
        page_soup = find_song_html(title)
        metadata = parse_song_page(page_soup) if page_soup else None

        if not metadata or len(metadata) != 22:
            print(f'  [retry next run] Metadata unavailable: {title}')
            meta_rows.append(['-'] * 22)
            failed += 1
        else:
            meta_rows.append(metadata)
            cache[cache_key] = metadata
        time.sleep(DELAY)

    if len(meta_rows) != len(song_rows):
        raise RuntimeError(f'Row count mismatch: {len(song_rows)} songs, {len(meta_rows)} metadata rows.')

    final_rows = [song + metadata for song, metadata in zip(song_rows, meta_rows)]
    if any(len(row) != len(HEADERS) for row in final_rows):
        raise RuntimeError('Output row width does not match the 32-column fandom header.')

    print(f'Writing {len(final_rows)} rows to Google Sheets...')
    write_sheet(final_rows)

    if not IS_GITHUB_ACTIONS:
        with CACHE_FILE.open('w', encoding='utf-8') as file:
            json.dump(cache, file, ensure_ascii=False, indent=2)
        print(f'  Cache saved: {len(cache)} valid songs.')
    else:
        print('  GitHub Actions: cache saving skipped.')

    print('=== FANDOM scraper completed ===')
    print(f'Songs written: {len(final_rows)}.')
    print(f'Metadata failures left uncached for retry: {failed}.')


if __name__ == '__main__':
    try:
        FANDOM()
    except Exception:
        import traceback
        traceback.print_exc()
        raise
    finally:
        if not IS_GITHUB_ACTIONS:
            try:
                input('Press Enter to exit...')
            except EOFError:
                pass