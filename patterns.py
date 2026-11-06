
import json
import os
import re
import subprocess
import sys
import time
from urllib.parse import urljoin

import requests
import gspread
from bs4 import BeautifulSoup
from google.oauth2.service_account import Credentials

try:
    from deep_translator import GoogleTranslator
except ImportError:
    subprocess.check_call([sys.executable, "-m", "pip", "install", "deep-translator"])
    from deep_translator import GoogleTranslator

try:
    from pykakasi import kakasi
except ImportError:
    subprocess.check_call([sys.executable, "-m", "pip", "install", "pykakasi"])
    from pykakasi import kakasi


PAGE_URL = "https://pjsekai.com/?03c3cbe6ba#e9ac9fe1"
BASE_URL = "https://pjsekai.com/"
SPREADSHEET_ID = "1Fpp_sJbJguKxUAcWhZOM1YqHJJrUHTWE56lO4dthhak"
SHEET_NAME = "patterns"
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
SERVICE_ACCOUNT = os.path.join(SCRIPT_DIR, "service_account.json")

BATCH_SIZE = 40
TRANSLATION_BATCH_CHARS = 4000
TRANSLATION_MARKER = "ZXQAXXIAREF{:06d}QXZ"

DIFFICULTIES = {
    "HARD": "hard",
    "EX": "expert",
    "EXPERT": "expert",
    "MAS": "master",
    "MASTER": "master",
    "APPEND": "append",
    "APD": "append",
    "APP": "append",
}

SESSION = requests.Session()
SESSION.headers.update({
    "User-Agent": "Mozilla/5.0 (compatible; AXXiA_METADATA/1.0)"
})

TRANSLATIONS = {}
ROMAJI_CACHE = {}
TRANSLATOR = GoogleTranslator(source="ja", target="en")
KAKASI = kakasi()


def clean_text(element):
    return re.sub(r"\s+", " ", element.get_text(" ", strip=True)).strip()


def to_romaji(text):
    if not text:
        return ""

    if text in ROMAJI_CACHE:
        return ROMAJI_CACHE[text]

    try:
        converted = "".join(
            item["hepburn"] for item in KAKASI.convert(text)
        )
        ROMAJI_CACHE[text] = converted
        return converted

    except Exception as error:
        print(f"    Romaji conversion failed: {error}")
        ROMAJI_CACHE[text] = ""
        return ""


def build_translation_batches(texts):
    batches = []
    current = []
    current_length = 0

    for index, text in enumerate(texts):
        marker = TRANSLATION_MARKER.format(index)
        segment = f"{marker}\n{text}"
        segment_length = len(segment) + 2

        if current and current_length + segment_length > TRANSLATION_BATCH_CHARS:
            batches.append(current)
            current = []
            current_length = 0

        current.append((index, marker, text))
        current_length += segment_length

    if current:
        batches.append(current)

    return batches


def translate_texts(texts):
    unique_texts = list(dict.fromkeys(text for text in texts if text))
    missing = [
        text for text in unique_texts
        if text not in TRANSLATIONS
    ]

    if not missing:
        return TRANSLATIONS

    batches = build_translation_batches(missing)
    marker_pattern = re.compile(r"ZXQAXXIAREF(\d{6})QXZ")

    print(
        f"Translating {len(missing)} unique descriptions "
        f"in {len(batches)} requests."
    )

    for batch_number, batch in enumerate(batches, 1):
        combined = "\n\n".join(
            f"{marker}\n{text}"
            for _, marker, text in batch
        )

        translated = None

        for attempt in range(3):
            try:
                translated = TRANSLATOR.translate(combined) or ""
                break

            except Exception as error:
                print(
                    f"    Translation batch {batch_number}, "
                    f"attempt {attempt + 1} failed: {error}"
                )

                if attempt < 2:
                    time.sleep(2 * (attempt + 1))

        batch_results = {}

        if translated:
            parts = marker_pattern.split(translated)

            for position in range(1, len(parts) - 1, 2):
                try:
                    batch_results[int(parts[position])] = (
                        parts[position + 1].strip()
                    )
                except (ValueError, IndexError):
                    continue

        expected_indexes = {
            index for index, _, _ in batch
        }

        if expected_indexes.issubset(batch_results):
            for index, _, original in batch:
                TRANSLATIONS[original] = batch_results[index]

            print(
                f"    Translated batch "
                f"{batch_number}/{len(batches)}."
            )

            time.sleep(0.3)
            continue

        print(
            f"    Batch {batch_number} markers were altered "
            f"or translation failed. Using individual fallback."
        )

        for _, _, original in batch:
            result = ""

            for attempt in range(3):
                try:
                    result = TRANSLATOR.translate(original) or ""
                    break

                except Exception as error:
                    print(
                        f"    Individual translation attempt "
                        f"{attempt + 1} failed: {error}"
                    )

                    if attempt < 2:
                        time.sleep(2 * (attempt + 1))

            TRANSLATIONS[original] = result
            time.sleep(0.3)

    return TRANSLATIONS


def get_image_url(img):
    source = img.get("data-src") or img.get("src") or ""

    if not source or "spacer.png" in source:
        return ""

    return urljoin(BASE_URL, source)


def extract_difficulty_table(container):
    results = {
        "hard": [],
        "expert": [],
        "master": [],
        "append": [],
    }

    table = container.select_one(".ie5 table.style_table")

    if not table:
        return results

    current_difficulty = None

    for row in table.find_all("tr"):
        cells = row.find_all("td", recursive=False)

        if not cells:
            continue

        first_text = clean_text(cells[0]).upper()

        if first_text in DIFFICULTIES:
            current_difficulty = DIFFICULTIES[first_text]

        if not current_difficulty:
            continue

        description_cell = cells[-1]
        description = clean_text(description_cell)

        if first_text in DIFFICULTIES:
            image_cells = cells[1:-1]
        else:
            image_cells = cells[:-1]

        images = []

        for cell in image_cells:
            images.extend(cell.find_all("img"))

        images.extend(description_cell.find_all("img"))

        seen = set()

        for img in images:
            url = get_image_url(img)

            if not url or url in seen:
                continue

            seen.add(url)

            results[current_difficulty].extend([
                url,
                description,
                "",
                "",
            ])

    return results


def extract_notes(container):
    notes = []

    for paragraph in container.find_all("p", recursive=False):
        text = clean_text(paragraph)

        if text:
            notes.append(text)

    return " ".join(notes)


def enrich_rows(rows):
    descriptions = []

    for row in rows:
        for column in range(1, 5):
            references = json.loads(row[column])

            descriptions.extend(
                references[index + 1]
                for index in range(0, len(references), 4)
                if references[index + 1]
            )

    unique_descriptions = list(dict.fromkeys(descriptions))

    print(
        f"Found {len(unique_descriptions)} unique descriptions."
    )

    print("Converting descriptions to romaji...")

    romaji = {
        text: to_romaji(text)
        for text in unique_descriptions
    }

    translations = translate_texts(unique_descriptions)

    print("Applying translations and romaji...")

    for row in rows:
        for column in range(1, 5):
            references = json.loads(row[column])

            for index in range(0, len(references), 4):
                description = references[index + 1]

                references[index + 2] = romaji.get(
                    description, ""
                )

                references[index + 3] = translations.get(
                    description, ""
                )

            row[column] = json.dumps(
                references,
                ensure_ascii=False
            )


def scrape_page():
    response = SESSION.get(PAGE_URL, timeout=60)
    response.raise_for_status()

    soup = BeautifulSoup(response.text, "html.parser")
    rows = []

    containers = soup.select(
        'td.rgncss[id^="rgn_content"]'
    )

    for container in containers:
        heading = container.find_previous("h4")

        if not heading:
            continue

        title = clean_text(heading)
        title = title.replace("\u200c", "").strip()

        if not title:
            continue

        difficulties = extract_difficulty_table(container)
        notes = extract_notes(container)

        rows.append([
            title,
            json.dumps(
                difficulties["hard"],
                ensure_ascii=False
            ),
            json.dumps(
                difficulties["expert"],
                ensure_ascii=False
            ),
            json.dumps(
                difficulties["master"],
                ensure_ascii=False
            ),
            json.dumps(
                difficulties["append"],
                ensure_ascii=False
            ),
            notes,
        ])

    enrich_rows(rows)

    return rows


def connect_sheet():
    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive",
    ]

    if os.environ.get("GCP_SA_KEY"):
        credentials = Credentials.from_service_account_info(
            json.loads(os.environ["GCP_SA_KEY"]),
            scopes=scopes,
        )
    else:
        credentials = Credentials.from_service_account_file(
            SERVICE_ACCOUNT,
            scopes=scopes,
        )

    client = gspread.authorize(credentials)
    spreadsheet = client.open_by_key(SPREADSHEET_ID)

    try:
        return spreadsheet.worksheet(SHEET_NAME)

    except gspread.exceptions.WorksheetNotFound:
        return spreadsheet.add_worksheet(
            title=SHEET_NAME,
            rows=1000,
            cols=6,
        )


def append_to_sheet(rows):
    if not rows:
        print("No song sections were extracted.")
        return 0

    worksheet = connect_sheet()

    existing_titles = worksheet.col_values(1)
    next_row = max(2, len(existing_titles) + 1)

    for offset in range(0, len(rows), BATCH_SIZE):
        batch = rows[offset:offset + BATCH_SIZE]

        worksheet.update(
            range_name=f"A{next_row + offset}",
            values=batch,
            value_input_option="RAW",
        )

        print(f"  Appended {len(batch)} rows.")
        time.sleep(0.5)

    return len(rows)


def main():
    print("=== PATTERNS scraper started ===")

    rows = scrape_page()

    print(f"Extracted {len(rows)} song sections.")

    count = append_to_sheet(rows)

    print(
        f"=== Done. Appended {count} rows "
        f"to '{SHEET_NAME}'. ==="
    )


if __name__ == "__main__":
    main()
