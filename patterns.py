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


PAGE_URL = "https://pjsekai.com/?03c3cbe6ba#e9ac9fe1"
BASE_URL = "https://pjsekai.com/"
SPREADSHEET_ID = "1Fpp_sJbGjuKxUAcWhZOM1YqHJJrUHTWE56lO4dthhak"
SHEET_NAME = "patterns"
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
SERVICE_ACCOUNT = os.path.join(SCRIPT_DIR, "service_account.json")
BATCH_SIZE = 40

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
TRANSLATOR = GoogleTranslator(source="ja", target="en")


def clean_text(element):
    return re.sub(r"\s+", " ", element.get_text(" ", strip=True)).strip()


def translate_text(text):
    if not text:
        return ""

    if text in TRANSLATIONS:
        return TRANSLATIONS[text]

    for attempt in range(3):
        try:
            translated = TRANSLATOR.translate(text) or ""
            TRANSLATIONS[text] = translated
            time.sleep(0.15)
            return translated
        except Exception as error:
            print(f"    Translation attempt {attempt + 1} failed: {error}")
            time.sleep(1.5 * (attempt + 1))

    TRANSLATIONS[text] = ""
    return ""


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

    for row in table.select("tbody tr"):
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
                translate_text(description),
            ])

    return results


def extract_notes(container):
    notes = []

    for paragraph in container.find_all("p", recursive=False):
        text = clean_text(paragraph)

        if text:
            notes.append(text)

    return " ".join(notes)


def scrape_page():
    response = SESSION.get(PAGE_URL, timeout=60)
    response.raise_for_status()

    soup = BeautifulSoup(response.text, "html.parser")
    rows = []

    containers = soup.select('td.rgncss[id^="rgn_content"]')

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
            json.dumps(difficulties["hard"], ensure_ascii=False),
            json.dumps(difficulties["expert"], ensure_ascii=False),
            json.dumps(difficulties["master"], ensure_ascii=False),
            json.dumps(difficulties["append"], ensure_ascii=False),
            notes,
        ])

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

    print(f"=== Done. Appended {count} rows to '{SHEET_NAME}'. ===")


if __name__ == "__main__":
    main()