
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
METADATA_TAB = "metadata raw"

CONSTANTS_39S_ID = "1B8tX9VL2PcSJKyuHFVd2UT_8kYlY4ZdwHwg9MfWOPug"
CONSTANTS_COMMUNITY_ID = "1Yv3GXnCIgEIbHL72EuZ-d5q_l-auPgddWi4Efa14jq0"
CONSTANTS_COMMUNITY_GID = "182216"

# Keep the same four source URLs used by your previous scraper.
# Each URL must return the original CSV dataset.
SOURCE_URLS = {
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
    value = re.sub(r"\s+", " ", value)
    return value


def numeric_id(value):
    value = str(value or "").strip()
    match = re.search(r"\d+", value)
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
            f"Missing required column '{name}'. Found: {header}"
        )

    return normalized.index(norm(name))


def load_metadata(workbook):
    ws = workbook.worksheet(METADATA_TAB)
    values = ws.get_all_values()

    if not values:
        raise ValueError(f"'{METADATA_TAB}' is empty.")

    header = values[0]
    title_jp_col = get_header_index(header, "title_jp")

    # Metadata matching is based on title_jp, not title or musicId.
    result = {}

    for row in values[1:]:
        if len(row) <= title_jp_col:
            continue

        title_jp = norm(row[title_jp_col])

        if title_jp:
            result[title_jp] = row

    print(
        f"Metadata loaded: {len(result)} entries. "
        f"title_jp is column {title_jp_col + 1} "
        f"(spreadsheet column {gspread.utils.rowcol_to_a1(1, title_jp_col + 1).rstrip('1')})."
    )

    return result


def load_constants_39s():
    url = (
        "https://docs.google.com/spreadsheets/d/"
        + CONSTANTS_39S_ID
        + "/export?format=csv&sheet=Constants"
    )

    rows = fetch_csv(url)

    if not rows:
        return {}

    header = rows[0]
    song_col = get_header_index(header, "Song")
    constant_col = get_header_index(header, "Constant")
    difficulty_col = get_header_index(header, "Difficulty")
    id_col = get_header_index(header, "Song ID")

    result = {}

    for row in rows[1:]:
        if max(song_col, constant_col, difficulty_col, id_col) >= len(row):
            continue

        song_id = numeric_id(row[id_col])
        difficulty = norm(row[difficulty_col])
        constant = row[constant_col].strip()

        if song_id is not None and difficulty and constant:
            result[(difficulty, song_id)] = constant

    return result


def load_constants_community():
    url = (
        "https://docs.google.com/spreadsheets/d/"
        + CONSTANTS_COMMUNITY_ID
        + "/export?format=csv&gid="
        + CONSTANTS_COMMUNITY_GID
    )

    rows = fetch_csv(url)

    if not rows:
        return {}

    header = rows[0]
    song_col = get_header_index(header, "Song")
    constant_col = get_header_index(header, "Constant")
    difficulty_col = get_header_index(header, "Difficulty")
    id_col = get_header_index(header, "Song ID")

    result = {}

    for row in rows[1:]:
        if max(song_col, constant_col, difficulty_col, id_col) >= len(row):
            continue

        song_id = numeric_id(row[id_col])
        difficulty = norm(row[difficulty_col])
        constant = row[constant_col].strip()

        if song_id is not None and difficulty and constant:
            result[(difficulty, song_id)] = constant

    return result


def load_source_id_maps():
    maps = {}

    for difficulty, url in SOURCE_URLS.items():
        rows = fetch_csv(url)

        if not rows:
            continue

        # Source dataset: A=Song, B=Japanese Song, C=Constant,
        # D=Level, E=Note Count, F=Difficulty, G=Song ID.
        for row in rows[1:]:
            if len(row) < 7:
                continue

            title = norm(row[0])
            song_id = numeric_id(row[6])

            if title and song_id is not None:
                maps[title] = song_id

    return maps


def get_song_id(title, metadata, source_ids):
    key = norm(title)

    # Match the Japanese title against metadata raw title_jp.
    metadata_row = metadata.get(key)

    if metadata_row:
        # The metadata row is used as the match, while the actual
        # numeric song ID is resolved from the source dataset.
        pass

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

    anchors = soup.select('a[id^="a"]')

    for anchor in anchors:
        section_id = anchor.get("id", "")
        if not section_id:
            continue

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

            title = cells[0].get_text(" ", strip=True)
            judgment = cells[1].get_text(" ", strip=True)
            elements = cells[2].get_text(" ", strip=True)
            comments = cells[3].get_text(" ", strip=True)
            lvl = cells[4].get_text(" ", strip=True)

            output.append([
                title,
                judgment,
                elements,
                comments,
                difficulty,
                lvl,
                section_id,
            ])

    return output


def main():
    client = get_client()
    workbook = client.open_by_key(SPREADSHEET_ID)

    metadata = load_metadata(workbook)
    constants_39s = load_constants_39s()
    constants_community = load_constants_community()
    source_ids = load_source_id_maps()

    rows = []

    for difficulty, url in SOURCE_URLS.items():
        if not url:
            raise ValueError(f"Missing CSV URL for {difficulty}.")

        scraped = scrape_community_page(url, difficulty)

        for item in scraped:
            title = item[0]
            song_id = get_song_id(title, metadata, source_ids)

            id_39s = (
                constants_39s.get((norm(difficulty), song_id), "")
                if song_id is not None else ""
            )

            id_community = (
                constants_community.get((norm(difficulty), song_id), "")
                if song_id is not None else ""
            )

            rows.append(item + [
                song_id if song_id is not None else "",
                id_39s,
                id_community,
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