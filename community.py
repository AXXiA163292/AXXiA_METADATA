
import os
import re
import csv
import json
import io
import requests
import gspread
from pathlib import Path
from google.oauth2.service_account import Credentials

SPREADSHEET_ID = "1Fpp_sJbGjuKxUAcWhZOM1YqHJJrUHTWE56lO4dthhak"

OUTPUT_TAB = "community"
METADATA_TAB = "METADATA_RAW"

CONSTANTS_39S_ID = "1B8tX9VL2PcSJKyuHFVd2UT_8kYlY4ZdwHwg9MfWOPug"
CONSTANTS_COMMUNITY_ID = "1Yv3GXnCIgEIbHL72EuZ-d5q_l-auPgddWi4Efa14jq0"
CONSTANTS_COMMUNITY_GID = "182216"

# Keep your original four CSV URLs here.
SOURCE_URLS = {
    "APPEND": "",
    "MASTER": "",
    "EXPERT": "",
    "HARD": "",
}

HEADERS = [
    "title", "judgment", "elements", "comments",
    "difficulty", "lvl", "history", "id",
    "39sConstant", "Not39s",
]


def get_client():
    raw = os.environ.get("GCP_SA_KEY")
    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive",
    ]

    if raw:
        credentials = Credentials.from_service_account_info(
            json.loads(raw), scopes=scopes
        )
    else:
        credentials = Credentials.from_service_account_file(
            str(Path(__file__).resolve().parent / "service_account.json"),
            scopes=scopes,
        )

    return gspread.authorize(credentials)


def norm(value):
    value = str(value or "").strip().casefold()
    return re.sub(r"\s+", " ", value)


def numeric_id(value):
    match = re.search(r"\d+", str(value or "").strip())
    return int(match.group()) if match else None


def fetch_csv(url):
    if not url:
        raise ValueError("A source CSV URL is missing from SOURCE_URLS.")

    response = requests.get(url, timeout=30)
    response.raise_for_status()
    return list(csv.reader(io.StringIO(response.text)))


def get_header_index(header, name):
    normalized = [norm(value) for value in header]

    if norm(name) not in normalized:
        raise ValueError(
            f"Missing column '{name}'. Available columns: {header}"
        )

    return normalized.index(norm(name))


def load_metadata(workbook):
    ws = workbook.worksheet(METADATA_TAB)
    values = ws.get_all_values()

    if not values:
        raise ValueError(f"'{METADATA_TAB}' is empty.")

    header = values[0]
    title_jp_col = get_header_index(header, "title_jp")

    result = {}

    for row in values[1:]:
        if len(row) <= title_jp_col:
            continue

        title_jp = norm(row[title_jp_col])

        if title_jp:
            result[title_jp] = row

    column_letter = gspread.utils.rowcol_to_a1(
        1, title_jp_col + 1
    ).rstrip("1")

    print(
        f"Loaded {len(result)} metadata titles. "
        f"title_jp is column {column_letter}."
    )

    return result


def load_constants(spreadsheet_id, sheet_name=None, gid=None):
    if sheet_name:
        url = (
            f"https://docs.google.com/spreadsheets/d/{spreadsheet_id}"
            f"/export?format=csv&sheet={sheet_name}"
        )
    else:
        url = (
            f"https://docs.google.com/spreadsheets/d/{spreadsheet_id}"
            f"/export?format=csv&gid={gid}"
        )

    rows = fetch_csv(url)

    if not rows:
        return {}

    header = rows[0]
    constant_col = get_header_index(header, "Constant")
    difficulty_col = get_header_index(header, "Difficulty")
    id_col = get_header_index(header, "Song ID")

    result = {}

    for row in rows[1:]:
        if max(constant_col, difficulty_col, id_col) >= len(row):
            continue

        song_id = numeric_id(row[id_col])
        difficulty = norm(row[difficulty_col])
        constant = row[constant_col].strip()

        if song_id is not None and difficulty and constant:
            result[(difficulty, song_id)] = constant

    return result


def load_source_id_maps():
    title_to_id = {}

    for difficulty, url in SOURCE_URLS.items():
        rows = fetch_csv(url)

        # Source CSV columns:
        # A = Song, B = Japanese Song, C = Constant,
        # D = Level, E = Note Count, F = Difficulty, G = Song ID.
        for row in rows[1:]:
            if len(row) < 7:
                continue

            english_title = norm(row[0])
            japanese_title = norm(row[1])
            song_id = numeric_id(row[6])

            if song_id is None:
                continue

            if english_title:
                title_to_id[english_title] = song_id

            if japanese_title:
                title_to_id[japanese_title] = song_id

    return title_to_id


def get_song_id(title, metadata, source_ids):
    key = norm(title)

    # Confirm that the Japanese title exists in METADATA_RAW.
    if key not in metadata:
        return None

    return source_ids.get(key)


def scrape_community_page(url, difficulty):
    from bs4 import BeautifulSoup

    response = requests.get(
        url,
        timeout=30,
        headers={"User-Agent": "Mozilla/5.0"},
    )
    response.raise_for_status()

    soup = BeautifulSoup(response.text, "html.parser")
    output = []

    for anchor in soup.select('a[id^="a"]'):
        section_id = anchor.get("id", "")
        section = anchor.find_parent()

        if not section:
            continue

        table = section.find_next("table")

        if not table:
            continue

        for tr in table.select("tbody tr"):
            cells = tr.find_all("td")

            if len(cells) < 5:
                continue

            output.append([
                cells[0].get_text(" ", strip=True),
                cells[1].get_text(" ", strip=True),
                cells[2].get_text(" ", strip=True),
                cells[3].get_text(" ", strip=True),
                difficulty,
                cells[4].get_text(" ", strip=True),
                section_id,
            ])

    return output


def main():
    client = get_client()
    workbook = client.open_by_key(SPREADSHEET_ID)

    metadata = load_metadata(workbook)

    constants_39s = load_constants(
        CONSTANTS_39S_ID,
        sheet_name="Constants",
    )

    constants_community = load_constants(
        CONSTANTS_COMMUNITY_ID,
        gid=CONSTANTS_COMMUNITY_GID,
    )

    source_ids = load_source_id_maps()

    rows = []

    for difficulty, url in SOURCE_URLS.items():
        scraped = scrape_community_page(url, difficulty)

        for item in scraped:
            title = item[0]
            song_id = get_song_id(title, metadata, source_ids)

            constant_39s = (
                constants_39s.get((norm(difficulty), song_id), "")
                if song_id is not None else ""
            )

            constant_community = (
                constants_community.get((norm(difficulty), song_id), "")
                if song_id is not None else ""
            )

            rows.append(item + [
                song_id if song_id is not None else "",
                constant_39s,
                constant_community,
            ])

    difficulty_order = {
        "append": 0,
        "master": 1,
        "expert": 2,
        "hard": 3,
    }

    rows.sort(
        key=lambda row: (
            numeric_id(row[7]) or 999999999,
            difficulty_order.get(norm(row[4]), 99),
            norm(row[0]),
        )
    )

    if not rows:
        print("No rows found. Sheet unchanged.")
        return

    try:
        worksheet = workbook.worksheet(OUTPUT_TAB)
    except gspread.WorksheetNotFound:
        worksheet = workbook.add_worksheet(
            title=OUTPUT_TAB,
            rows=1000,
            cols=len(HEADERS),
        )

    worksheet.clear()
    worksheet.update(
        range_name="A1",
        values=[HEADERS] + rows,
        value_input_option="RAW",
    )

    print(f"Updated {len(rows)} rows in '{OUTPUT_TAB}'.")


if __name__ == "__main__":
    main()