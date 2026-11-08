import os
import re
import csv
import json
import io
import requests
import gspread

from pathlib import Path
from bs4 import BeautifulSoup
from google.oauth2.service_account import Credentials


SPREADSHEET_ID = "1Fpp_sJbGjuKxUAcWhZOM1YqHJJrUHTWE56lO4dthhak"

OUTPUT_TAB = "community"
METADATA_TAB = "METADATA_RAW"

CONSTANTS_39S_ID = "1B8tX9VL2PcSJKyuHFVd2UT_8kYlY4ZdwHwg9MfWOPug"
CONSTANTS_39S_TAB = "Constants"

CONSTANTS_COMMUNITY_ID = "1Yv3GXnCIgEIbHL72EuZ-d5q_l-auPgddWi4Efa14jq0"
CONSTANTS_COMMUNITY_TAB = "Constant Overrides"


# Actual community difficulty-table pages.
FORUM_URLS = {
    "APPEND": "https://pjsekai.com/?%E6%A5%BD%E6%9B%B2%E9%9B%A3%E6%98%93%E5%BA%A6%E8%A1%A8APPEND",
    "MASTER": "https://pjsekai.com/?%E6%A5%BD%E6%9B%B2%E9%9B%A3%E6%98%93%E5%BA%A6%E8%A1%A8MASTER",
    "EXPERT": "https://pjsekai.com/?%E6%A5%BD%E6%9B%B2%E9%9B%A3%E6%98%93%E5%BA%A6%E8%A1%A8EXPERT",
    "HARD": "https://pjsekai.com/?%E6%A5%BD%E6%9B%B2%E9%9B%A3%E6%98%93%E5%BA%A6%E8%A1%A8HARD",
}


# Optional ID mapping CSVs.
# Leave blank if METADATA_RAW already contains a song ID column.
# These must be CSV URLs, not the four forum URLs above.
SOURCE_CSV_URLS = {
    "APPEND": "",
    "MASTER": "",
    "EXPERT": "",
    "HARD": "",
}


HEADERS = [
    "title",
    "judgment",
    "elements",
    "comments",
    "difficulty",
    "lvl",
    "history",
    "id",
    "39sConstant",
    "Not39s",
]


SONG_ID_HEADERS = [
    "id",
    "song_id",
    "song id",
    "music_id",
    "music id",
    "musicid",
    "musicid",
]


REQUEST_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36"
}


def get_client():
    raw = os.environ.get("GCP_SA_KEY")

    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive",
    ]

    if raw:
        credentials = Credentials.from_service_account_info(
            json.loads(raw),
            scopes=scopes,
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
        raise ValueError("A source CSV URL is missing.")

    response = requests.get(
        url,
        timeout=30,
        headers=REQUEST_HEADERS,
    )
    response.raise_for_status()

    return list(
        csv.reader(
            io.StringIO(response.content.decode("utf-8-sig"))
        )
    )


def get_header_index(header, name):
    normalized = [norm(value) for value in header]

    if norm(name) not in normalized:
        raise ValueError(
            f"Missing column '{name}'. Available columns: {header}"
        )

    return normalized.index(norm(name))


def find_header_row(values, required_headers):
    required = {norm(value) for value in required_headers}

    for index, row in enumerate(values):
        normalized = {norm(value) for value in row}

        if required.issubset(normalized):
            return index

    raise ValueError(
        "Could not find a header row containing: "
        + ", ".join(required_headers)
        + ". First rows received: "
        + repr(values[:5])
    )


def find_optional_id_column(header):
    normalized = [norm(value) for value in header]

    for candidate in SONG_ID_HEADERS:
        if norm(candidate) in normalized:
            return normalized.index(norm(candidate))

    return None


def load_metadata(workbook):
    ws = workbook.worksheet(METADATA_TAB)
    values = ws.get_all_values()

    if not values:
        raise ValueError(f"'{METADATA_TAB}' is empty.")

    header = values[0]

    title_jp_col = get_header_index(header, "title_jp")
    id_col = find_optional_id_column(header)

    metadata = {}
    metadata_ids = {}

    for row in values[1:]:
        if len(row) <= title_jp_col:
            continue

        title_jp = norm(row[title_jp_col])

        if not title_jp:
            continue

        metadata[title_jp] = row

        if id_col is not None and len(row) > id_col:
            song_id = numeric_id(row[id_col])

            if song_id is not None:
                metadata_ids[title_jp] = song_id

    column_letter = gspread.utils.rowcol_to_a1(
        1,
        title_jp_col + 1,
    ).rstrip("1")

    print(
        f"Loaded {len(metadata)} metadata titles. "
        f"title_jp is column {column_letter}."
    )

    if id_col is not None:
        print(
            f"Found song IDs directly in METADATA_RAW: "
            f"{len(metadata_ids)}."
        )
    else:
        print("No song ID column found in METADATA_RAW.")

    return metadata, metadata_ids


def load_constants(client, spreadsheet_id, worksheet_name):
    spreadsheet = client.open_by_key(spreadsheet_id)

    try:
        ws = spreadsheet.worksheet(worksheet_name)
    except gspread.WorksheetNotFound:
        available = [
            worksheet.title
            for worksheet in spreadsheet.worksheets()
        ]

        raise ValueError(
            f"Worksheet '{worksheet_name}' not found in "
            f"{spreadsheet_id}. Available worksheets: {available}"
        )

    values = ws.get_all_values()

    if not values:
        print(f"Warning: '{worksheet_name}' is empty.")
        return {}

    header_row_index = find_header_row(
        values,
        ["Constant", "Difficulty", "Song ID"],
    )

    header = values[header_row_index]

    constant_col = get_header_index(header, "Constant")
    difficulty_col = get_header_index(header, "Difficulty")
    id_col = get_header_index(header, "Song ID")

    result = {}

    for row in values[header_row_index + 1:]:
        required_col = max(
            constant_col,
            difficulty_col,
            id_col,
        )

        if len(row) <= required_col:
            continue

        song_id = numeric_id(row[id_col])
        difficulty = norm(row[difficulty_col])
        constant = str(row[constant_col]).strip()

        if song_id is None or not difficulty or not constant:
            continue

        result[(difficulty, song_id)] = constant

    print(
        f"Loaded {len(result)} constants from "
        f"'{worksheet_name}'."
    )

    return result


def load_source_id_maps(metadata_ids):
    title_to_id = dict(metadata_ids)

    for difficulty, url in SOURCE_CSV_URLS.items():
        if not url:
            continue

        rows = fetch_csv(url)

        # Source CSV columns:
        # A = Song
        # B = Japanese Song
        # C = Constant
        # D = Level
        # E = Note Count
        # F = Difficulty
        # G = Song ID

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

        print(f"Loaded ID mappings from {difficulty} CSV.")

    print(f"Total title-to-ID mappings: {len(title_to_id)}")

    if not title_to_id:
        raise ValueError(
            "No song IDs were found. Add a song ID column to "
            "METADATA_RAW or provide the four SOURCE_CSV_URLS."
        )

    return title_to_id


def get_song_id(title, metadata, source_ids):
    key = norm(title)

    # Only accept titles present in METADATA_RAW.
    if key not in metadata:
        return None

    return source_ids.get(key)


def scrape_community_page(url, difficulty):
    response = requests.get(
        url,
        timeout=45,
        headers=REQUEST_HEADERS,
    )
    response.raise_for_status()

    soup = BeautifulSoup(response.text, "html.parser")

    # Prefer the main page content to avoid unrelated tables.
    content = (
        soup.select_one("#body")
        or soup.select_one("#contents")
        or soup.select_one("#content")
        or soup
    )

    output = []
    seen = set()

    for table in content.find_all("table"):
        for tr in table.find_all("tr"):
            cells = tr.find_all(
                ["td", "th"],
                recursive=False,
            )

            # The difficulty tables contain five data columns.
            if len(cells) < 5:
                continue

            if any(cell.name == "th" for cell in cells):
                continue

            values = [
                cell.get_text(" ", strip=True)
                for cell in cells[:5]
            ]

            if not values[0]:
                continue

            # Ignore repeated rows.
            row_key = tuple(values)

            if row_key in seen:
                continue

            seen.add(row_key)

            # Find the nearest preceding section anchor.
            previous_anchors = table.find_all_previous(
                "a",
                id=re.compile(r"^a"),
            )

            section_id = (
                previous_anchors[0].get("id", "")
                if previous_anchors
                else ""
            )

            output.append([
                values[0],       # title
                values[1],       # judgment
                values[2],       # elements
                values[3],       # comments
                difficulty,
                values[4],       # lvl
                section_id,
            ])

    print(
        f"{difficulty}: scraped {len(output)} rows "
        f"from {url}"
    )

    if not output:
        raise ValueError(
            f"No difficulty rows found on the {difficulty} page. "
            "The page structure may have changed."
        )

    return output


def main():
    client = get_client()
    workbook = client.open_by_key(SPREADSHEET_ID)

    metadata, metadata_ids = load_metadata(workbook)

    constants_39s = load_constants(
        client,
        CONSTANTS_39S_ID,
        CONSTANTS_39S_TAB,
    )

    constants_community = load_constants(
        client,
        CONSTANTS_COMMUNITY_ID,
        CONSTANTS_COMMUNITY_TAB,
    )

    source_ids = load_source_id_maps(metadata_ids)

    rows = []

    for difficulty, url in FORUM_URLS.items():
        scraped = scrape_community_page(
            url,
            difficulty,
        )

        for item in scraped:
            title = item[0]

            song_id = get_song_id(
                title,
                metadata,
                source_ids,
            )

            constant_39s = (
                constants_39s.get(
                    (norm(difficulty), song_id),
                    "",
                )
                if song_id is not None
                else ""
            )

            constant_community = (
                constants_community.get(
                    (norm(difficulty), song_id),
                    "",
                )
                if song_id is not None
                else ""
            )

            rows.append(
                item + [
                    song_id if song_id is not None else "",
                    constant_39s,
                    constant_community,
                ]
            )

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
            rows=max(1000, len(rows) + 1),
            cols=len(HEADERS),
        )

    worksheet.clear()

    worksheet.update(
        range_name="A1",
        values=[HEADERS] + rows,
        value_input_option="RAW",
    )

    print(
        f"Updated {len(rows)} rows in '{OUTPUT_TAB}'."
    )


if __name__ == "__main__":
    main()