
import json
import os
import re
import time
import subprocess
import sys
from urllib.parse import urljoin

import requests
import gspread
from bs4 import BeautifulSoup
from google.oauth2.service_account import Credentials

try:
    from pykakasi import kakasi
except ImportError:
    subprocess.check_call([sys.executable, "-m", "pip", "install", "pykakasi"])
    from pykakasi import kakasi


PAGE_URL = "https://pjsekai.com/?03c3cbe6ba#e9ac9fe1"
BASE_URL = "https://pjsekai.com/"
SPREADSHEET_ID = "1Fpp_sJbGjuKxUAcWhZOM1YqHJJrUHTWE56lO4dthhak"

SHEET_NAME = "patterns"
TRANSLATION_SHEET_NAME = "patterns_translations"

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
SERVICE_ACCOUNT = os.path.join(SCRIPT_DIR, "service_account.json")

BATCH_SIZE = 40
ROMAJI_CACHE = {}
KAKASI = kakasi()

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

DIFFICULTY_ORDER = ["hard", "expert", "master", "append"]

DIFFICULTY_LABELS = {
    "hard": "HARD",
    "expert": "EXPERT",
    "master": "MASTER",
    "append": "APPEND",
}

SESSION = requests.Session()
SESSION.headers.update({
    "User-Agent": "Mozilla/5.0 (compatible; AXXiA_METADATA/1.0)"
})


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


def get_image_url(img):
    source = img.get("data-src") or img.get("src") or ""

    if not source or "spacer.png" in source:
        return ""

    return urljoin(BASE_URL, source)


def extract_difficulty_table(container):
    results = {
        difficulty: []
        for difficulty in DIFFICULTY_ORDER
    }

    table = container.select_one(".ie5 table.style_table")

    if not table:
        return results

    current_difficulty = None
    seen = {
        difficulty: set()
        for difficulty in DIFFICULTY_ORDER
    }

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

        for img in images:
            url = get_image_url(img)

            if not url or url in seen[current_difficulty]:
                continue

            seen[current_difficulty].add(url)

            results[current_difficulty].append({
                "url": url,
                "description": description,
                "romaji": "",
            })

    return results


def extract_notes(container):
    notes = []

    for paragraph in container.find_all("p", recursive=False):
        text = clean_text(paragraph)

        if text:
            notes.append(text)

    return " ".join(notes)


def enrich_rows(rows):
    descriptions = list(dict.fromkeys(
        entry["description"]
        for row in rows
        for difficulty in DIFFICULTY_ORDER
        for entry in row["difficulties"][difficulty]
        if entry["description"]
    ))

    print(f"Found {len(descriptions)} unique descriptions.")
    print("Converting descriptions to romaji...")

    romaji = {}

    for index, description in enumerate(descriptions, 1):
        romaji[description] = to_romaji(description)

        if index % 100 == 0:
            print(f"  Converted {index}/{len(descriptions)}.")

    print("Applying romaji...")

    for row in rows:
        for difficulty in DIFFICULTY_ORDER:
            for entry in row["difficulties"][difficulty]:
                entry["romaji"] = romaji.get(
                    entry["description"], ""
                )

    return descriptions, romaji


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

        rows.append({
            "title": title,
            "notes": notes,
            "difficulties": difficulties,
        })

    descriptions, romaji = enrich_rows(rows)

    return rows, descriptions, romaji


def connect_spreadsheet():
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
    return client.open_by_key(SPREADSHEET_ID)


def get_or_create_sheet(spreadsheet, name, rows=1000, cols=20):
    try:
        return spreadsheet.worksheet(name)
    except gspread.exceptions.WorksheetNotFound:
        return spreadsheet.add_worksheet(
            title=name,
            rows=rows,
            cols=cols,
        )


def build_sheet_data(rows):
    max_images = {
        difficulty: max(
            (
                len(row["difficulties"][difficulty])
                for row in rows
            ),
            default=0,
        )
        for difficulty in DIFFICULTY_ORDER
    }

    headers = ["Song", "Notes"]

    for difficulty in DIFFICULTY_ORDER:
        label = DIFFICULTY_LABELS[difficulty]
        prefix = label.title()

        headers.append(f"{prefix}label ({label})")

        for index in range(1, max_images[difficulty] + 1):
            headers.extend([
                f"{prefix}imageurl{index}",
                f"{prefix}desc{index}",
                f"{prefix}romaji{index}",
            ])

    output = [headers]

    for row in rows:
        values = [
            row["title"],
            row["notes"],
        ]

        for difficulty in DIFFICULTY_ORDER:
            entries = row["difficulties"][difficulty]

            values.append(
                DIFFICULTY_LABELS[difficulty] if entries else ""
            )

            for index in range(max_images[difficulty]):
                if index < len(entries):
                    entry = entries[index]

                    values.extend([
                        entry["url"],
                        entry["description"],
                        entry["romaji"],
                    ])
                else:
                    values.extend(["", "", ""])

        output.append(values)

    return output


def write_patterns_sheet(spreadsheet, rows):
    if not rows:
        print("No song sections were extracted. Sheet unchanged.")
        return 0

    data = build_sheet_data(rows)

    worksheet = get_or_create_sheet(
        spreadsheet,
        SHEET_NAME,
        rows=max(1000, len(data) + 10),
        cols=max(20, len(data[0])),
    )

    worksheet.resize(
        rows=max(1000, len(data) + 10),
        cols=len(data[0]),
    )

    worksheet.clear()

    for offset in range(0, len(data), BATCH_SIZE):
        batch = data[offset:offset + BATCH_SIZE]
        start = offset + 1

        worksheet.update(
            range_name=f"A{start}",
            values=batch,
            value_input_option="RAW",
        )

        print(
            f"  Updated pattern rows {start}-{start + len(batch) - 1}."
        )

        time.sleep(0.3)

    print(f"Patterns sheet updated: {len(rows)} songs.")
    print(f"Total columns: {len(data[0])}")

    for difficulty in DIFFICULTY_ORDER:
        print(
            f"  {DIFFICULTY_LABELS[difficulty]}: "
            f"{max(
                (len(row['difficulties'][difficulty]) for row in rows),
                default=0
            )} maximum images"
        )

    return len(rows)


def append_translations(spreadsheet, descriptions, romaji):
    if not descriptions:
        return 0

    worksheet = get_or_create_sheet(
        spreadsheet,
        TRANSLATION_SHEET_NAME,
        rows=1000,
        cols=3,
    )

    if not worksheet.acell("A1").value:
        worksheet.update(
            range_name="A1:C1",
            values=[["Japanese", "Romaji", "English"]],
            value_input_option="RAW",
        )

    existing = worksheet.get_all_values()
    existing_rows = {}

    for row_number, row in enumerate(existing[1:], start=2):
        if row and row[0]:
            existing_rows[row[0]] = row_number

    new_rows = []
    formula_updates = []

    for description in descriptions:
        row_number = existing_rows.get(description)

        if row_number is None:
            new_rows.append([
                description,
                romaji.get(description, ""),
            ])
        else:
            current_row = existing[row_number - 1]

            current_translation = (
                current_row[2] if len(current_row) > 2 else ""
            )

            if (
                not current_translation
                or current_translation.startswith("#")
            ):
                formula_updates.append({
                    "range": f"C{row_number}",
                    "values": [[
                        f'=IF(A{row_number}="";"";IFERROR(GOOGLETRANSLATE(A{row_number};"ja";"en");""))'
                    ],
                })

    next_row = len(existing) + 1

    for offset in range(0, len(new_rows), BATCH_SIZE):
        batch = new_rows[offset:offset + BATCH_SIZE]
        start = next_row + offset
        end = start + len(batch) - 1

        worksheet.update(
            range_name=f"A{start}:B{end}",
            values=batch,
            value_input_option="RAW",
        )

        formulas = [
            [
                f'=IF(A{row_number}="";"";IFERROR(GOOGLETRANSLATE(A{row_number};"ja";"en");""))'
            ]
            for row_number in range(start, end + 1)
        ]

        worksheet.update(
            range_name=f"C{start}:C{end}",
            values=formulas,
            value_input_option="USER_ENTERED",
        )

        print(
            f"  Added {len(batch)} translation formulas "
            f"({start}-{end})."
        )

        time.sleep(0.3)

    for offset in range(0, len(formula_updates), BATCH_SIZE):
        worksheet.batch_update(
            formula_updates[offset:offset + BATCH_SIZE],
            value_input_option="USER_ENTERED",
        )

    total = len(new_rows) + len(formula_updates)

    print(f"Translation formulas added or restored: {total}")
    print("Google Sheets will calculate the translations.")

    return total


def main():
    print("=== PATTERNS scraper started ===")

    rows, descriptions, romaji = scrape_page()

    print(f"Extracted {len(rows)} song sections.")

    if not rows:
        print("Aborting: no data extracted.")
        return

    spreadsheet = connect_spreadsheet()

    count = write_patterns_sheet(spreadsheet, rows)

    translation_count = append_translations(
        spreadsheet,
        descriptions,
        romaji,
    )

    print(
        f"=== Done. Imported {count} songs. "
        f"Processed {translation_count} translation entries. ==="
    )


if __name__ == "__main__":
    main()