
import json
import os
import re
import time
from urllib.parse import urljoin

import requests
import gspread
from bs4 import BeautifulSoup
from google.oauth2.service_account import Credentials


PAGE_URL = "https://pjsekai.com/?03c3cbe6ba#e9ac9fe1"
BASE_URL = "https://pjsekai.com/"
SPREADSHEET_ID = "1Fpp_sJbGjuKxUAcWhZOM1YqHJJrUHTWE56lO4dthhak"

SHEET_NAME = "patterns_raw"

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

DIFFICULTY_ORDER = ["append", "master", "expert", "hard"]

DIFFICULTY_LABELS = {
    "append": "APPEND",
    "master": "MASTER",
    "expert": "EXPERT",
    "hard": "HARD",
}

DIFFICULTY_PREFIXES = {
    "append": "ap",
    "master": "ms",
    "expert": "ex",
    "hard": "hr",
}

SESSION = requests.Session()
SESSION.headers.update({
    "User-Agent": "Mozilla/5.0 (compatible; AXXiA_METADATA/1.0)"
})


def clean_text(element):
    return re.sub(r"\s+", " ", element.get_text(" ", strip=True)).strip()


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
            })

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

    return rows


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
        prefix = DIFFICULTY_PREFIXES[difficulty]

        headers.append(label)

        for _ in range(max_images[difficulty]):
            headers.extend([
                f"{prefix}PatternUrl",
                f"{prefix}PatternDesc",
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
                    ])
                else:
                    values.extend(["", ""])

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
        maximum = max(
            (
                len(row["difficulties"][difficulty])
                for row in rows
            ),
            default=0,
        )

        print(
            f"  {DIFFICULTY_LABELS[difficulty]}: "
            f"{maximum} maximum patterns"
        )

    return len(rows)


def main():
    print("=== PATTERNS scraper started ===")

    rows = scrape_page()

    print(f"Extracted {len(rows)} song sections.")

    if not rows:
        print("Aborting: no data extracted.")
        return

    spreadsheet = connect_spreadsheet()

    count = write_patterns_sheet(spreadsheet, rows)

    print(
        f"=== Done. Imported {count} songs into {SHEET_NAME}. ==="
    )


if __name__ == "__main__":
    main()