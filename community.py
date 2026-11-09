import os
import re
import csv
import io
import json
import html
import time
import unicodedata
import requests
import gspread

from pathlib import Path
from google.oauth2.service_account import Credentials


SPREADSHEET_ID = "1Fpp_sJbGjuKxUAcWhZOM1YqHJJrUHTWE56lO4dthhak"
OUTPUT_TAB = "community"
METADATA_TAB = "METADATA_RAW"

CONSTANTS_39S_ID = "1B8tX9VL2PcSJKyuHFVd2UT_8kYlY4ZdwHwg9MfWOPug"
CONSTANTS_39S_TAB = "Constants"

CONSTANTS_COMMUNITY_ID = "1Yv3GXnCIgEIbHL72EuZ-d5q_l-auPgddWi4Efa14jq0"
CONSTANTS_COMMUNITY_TAB = "Constant Overrides"

FORUM_URLS = {
    "APPEND": "https://pjsekai.com/?%E6%A5%BD%E6%9B%B2%E9%9B%A3%E6%98%93%E5%BA%A6%E8%A1%A8APPEND",
    "MASTER": "https://pjsekai.com/?%E6%A5%BD%E6%9B%B2%E9%9B%A3%E6%98%93%E5%BA%A6%E8%A1%A8MASTER",
    "EXPERT": "https://pjsekai.com/?%E6%A5%BD%E6%9B%B2%E9%9B%A3%E6%98%93%E5%BA%A6%E8%A1%A8EXPERT",
    "HARD": "https://pjsekai.com/?%E6%A5%BD%E6%9B%B2%E9%9B%A3%E6%98%93%E5%BA%A6%E8%A1%A8HARD",
}

DIFFICULTY_ORDER = {
    "append": 0,
    "master": 1,
    "expert": 2,
    "hard": 3,
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

REQUEST_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36"
}


def get_client():
    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive",
    ]

    raw = os.environ.get("GCP_SA_KEY")

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


def normalize_title(value):
    value = unicodedata.normalize("NFKC", str(value or "")).casefold()
    value = value.replace("’", "'").replace("　", " ")
    value = re.sub(r"\s+", " ", value)
    value = re.sub(r'[「」『』【】［］\[\]（）()]', "", value)
    return value.strip()


def norm(value):
    return str(value or "").strip().casefold()


def numeric_id(value):
    match = re.search(r"\d+", str(value or "").strip())
    return int(match.group()) if match else None


def normalize_difficulty(value):
    value = norm(value)

    aliases = {
        "append": "append",
        "master": "master",
        "expert": "expert",
        "hard": "hard",
    }

    return aliases.get(value, value)


def clean(value):
    value = html.unescape(str(value or ""))
    value = re.sub(r"\s+", " ", value)
    return value.strip()


def strip_tags(value):
    return clean(re.sub(r"<[^>]+>", " ", value))


def get_header_index(header, candidates):
    if isinstance(candidates, str):
        candidates = [candidates]

    normalized = [norm(value) for value in header]

    for candidate in candidates:
        candidate = norm(candidate)

        if candidate in normalized:
            return normalized.index(candidate)

    raise ValueError(
        f"Missing column. Expected one of {candidates}. "
        f"Available columns: {header}"
    )


def find_header_row(values, required_headers):
    required = {norm(value) for value in required_headers}

    for index, row in enumerate(values):
        available = {norm(value) for value in row}

        if required.issubset(available):
            return index

    raise ValueError(
        f"Could not find header row containing {required_headers}. "
        f"First rows: {values[:5]}"
    )


def load_metadata(workbook):
    worksheet = workbook.worksheet(METADATA_TAB)
    values = worksheet.get_all_values()

    if not values:
        raise ValueError(f"{METADATA_TAB} is empty.")

    header = values[0]

    title_col = get_header_index(header, "title_jp")
    id_col = get_header_index(header, "id")

    title_to_id = {}
    id_to_title = {}

    for row in values[1:]:
        if len(row) <= max(title_col, id_col):
            continue

        title = normalize_title(row[title_col])
        song_id = numeric_id(row[id_col])

        if not title or song_id is None:
            continue

        title_to_id[title] = song_id
        id_to_title[song_id] = row[title_col].strip()

    print(f"Loaded {len(title_to_id)} Japanese title-to-ID mappings.")
    print(f"Loaded {len(id_to_title)} metadata IDs.")
    print("Metadata matching columns: title_jp + id.")

    return title_to_id, id_to_title


def load_constants_table(
    client,
    spreadsheet_id,
    worksheet_name,
    override_title_fallback=False,
):
    spreadsheet = client.open_by_key(spreadsheet_id)
    worksheet = spreadsheet.worksheet(worksheet_name)
    values = worksheet.get_all_values()

    if not values:
        print(f"Warning: {worksheet_name} is empty.")
        return []

    header_index = find_header_row(
        values,
        ["Constant", "Difficulty", "Song ID"],
    )

    header = values[header_index]

    constant_col = get_header_index(header, "Constant")
    difficulty_col = get_header_index(header, "Difficulty")
    id_col = get_header_index(header, "Song ID")

    try:
        title_col = get_header_index(
            header,
            ["Song", "Song JP", "Japanese Song", "title_jp"],
        )
    except ValueError:
        title_col = None

    records = []

    for row in values[header_index + 1:]:
        required = max(
            constant_col,
            difficulty_col,
            id_col,
        )

        if len(row) <= required:
            continue

        if override_title_fallback:
            title = (
                str(row[1]).strip()
                if len(row) > 1 and str(row[1]).strip()
                else str(row[0]).strip() if row else ""
            )
        else:
            title = (
                str(row[title_col]).strip()
                if title_col is not None and len(row) > title_col
                else ""
            )

        constant = str(row[constant_col]).strip()
        difficulty = normalize_difficulty(row[difficulty_col])
        song_id = numeric_id(row[id_col])

        if not difficulty or song_id is None:
            continue

        records.append({
            "title": title,
            "constant": constant,
            "difficulty": difficulty,
            "id": song_id,
        })

    print(
        f"Loaded {len(records)} records from "
        f"'{worksheet_name}'."
    )

    return records


def build_constant_lookups(records, metadata_id_to_title):
    constants_by_title = {}
    ids = set()
    unmatched_ids = set()

    for record in records:
        song_id = record["id"]
        difficulty = record["difficulty"]
        constant = record["constant"]

        ids.add(song_id)

        jp_title = metadata_id_to_title.get(song_id)

        if not jp_title:
            unmatched_ids.add(song_id)
            continue

        title_key = normalize_title(jp_title)

        if title_key and constant:
            constants_by_title[
                (title_key, difficulty)
            ] = constant

    print(
        f"39s constants linked through metadata IDs: "
        f"{len(constants_by_title)}."
    )

    if unmatched_ids:
        print(
            f"39s IDs missing from METADATA_RAW: "
            f"{len(unmatched_ids)}."
        )
        print(
            "First unmatched IDs: "
            + ", ".join(map(str, sorted(unmatched_ids)[:20]))
        )

    return constants_by_title, ids


def build_override_lookup(records):
    overrides = {}

    for record in records:
        key = (
            record["id"],
            record["difficulty"],
        )

        overrides[key] = record

    return overrides


def parse_difficulty(difficulty):
    url = FORUM_URLS[difficulty]

    response = requests.get(
        url,
        timeout=45,
        headers=REQUEST_HEADERS,
    )
    response.raise_for_status()

    source = response.content.decode(
        "utf-8",
        errors="replace",
    )

    source = re.sub(
        r"</?font[^>]*>",
        "",
        source,
        flags=re.I,
    )

    rows = []

    anchor_pattern = re.compile(
        r'id=["\'][a-z]+(\d+)["\']',
        re.I,
    )

    sections = [
        {
            "position": match.start(),
            "level": match.group(1),
        }
        for match in anchor_pattern.finditer(source)
    ]

    sections.append({
        "position": len(source),
        "level": None,
    })

    for index in range(len(sections) - 1):
        level = sections[index]["level"]

        section = source[
            sections[index]["position"]:
            sections[index + 1]["position"]
        ]

        tbody_match = re.search(
            r"<tbody[^>]*>([\s\S]*?)</tbody>",
            section,
            re.I,
        )

        if not tbody_match:
            continue

        tbody = tbody_match.group(1)

        for row_match in re.finditer(
            r"<tr[^>]*>([\s\S]*?)</tr>",
            tbody,
            re.I,
        ):
            cells = re.findall(
                r"<td[^>]*>([\s\S]*?)</td>",
                row_match.group(1),
                re.I,
            )

            if len(cells) < 2:
                continue

            title_match = re.search(
                r"<a[^>]*>([\s\S]*?)</a>",
                cells[0],
                re.I,
            )

            title = strip_tags(
                title_match.group(1)
                if title_match
                else cells[0]
            )

            if not title:
                continue

            judgment = ""
            history = ""

            memo_match = re.search(
                r'<div[^>]*class=["\'][^"\']*memo1[^"\']*["\'][^>]*>([\s\S]*?)</div>',
                cells[1],
                re.I,
            )

            if memo_match:
                memo_html = memo_match.group(1)

                history_match = re.search(
                    r'<span[^>]*class=["\'][^"\']*memo2[^"\']*["\'][^>]*>([\s\S]*?)</span>',
                    memo_html,
                    re.I,
                )

                if history_match:
                    history = strip_tags(history_match.group(1))
                    memo_html = memo_html.replace(
                        history_match.group(0),
                        "",
                    )

                judgment = strip_tags(memo_html)

            else:
                judgment = strip_tags(cells[1])

            elements = ""

            if len(cells) > 2:
                element_items = re.findall(
                    r"<li[^>]*>([\s\S]*?)</li>",
                    cells[2],
                    re.I,
                )

                elements = ", ".join(
                    strip_tags(item)
                    for item in element_items
                    if strip_tags(item)
                )

            comments = (
                strip_tags(cells[3])
                if len(cells) > 3
                else ""
            )

            rows.append([
                title,
                judgment,
                elements,
                comments,
                difficulty,
                str(level or ""),
                history,
            ])

    unique_rows = []
    seen = set()

    for row in rows:
        key = (
            normalize_title(row[0]),
            row[4],
            row[5],
        )

        if key in seen:
            continue

        seen.add(key)
        unique_rows.append(row)

    print(
        f"{difficulty}: scraped {len(unique_rows)} rows."
    )

    if not unique_rows:
        raise RuntimeError(
            f"No rows scraped for {difficulty}. "
            "The page structure may have changed."
        )

    return unique_rows


def sort_rows(rows):
    return sorted(
        rows,
        key=lambda row: (
            row[7] == "",
            numeric_id(row[7]) or 999999999,
            DIFFICULTY_ORDER.get(
                normalize_difficulty(row[4]),
                99,
            ),
            normalize_title(row[0]),
        ),
    )


def main():
    print("=== COMMUNITY scraper started ===")

    scraped_rows = []

    for index, difficulty in enumerate(
        FORUM_URLS,
        start=1,
    ):
        rows = parse_difficulty(difficulty)
        scraped_rows.extend(rows)

        print(
            f"[{index}/4] {difficulty}: "
            f"{len(rows)} rows."
        )

        time.sleep(0.4)

    if not scraped_rows:
        raise RuntimeError(
            "No community rows found. Google Sheets was not modified."
        )

    client = get_client()
    workbook = client.open_by_key(SPREADSHEET_ID)

    metadata_title_to_id, metadata_id_to_title = load_metadata(
        workbook
    )

    constants_39s_records = load_constants_table(
        client,
        CONSTANTS_39S_ID,
        CONSTANTS_39S_TAB,
    )

    override_records = load_constants_table(
        client,
        CONSTANTS_COMMUNITY_ID,
        CONSTANTS_COMMUNITY_TAB,
        override_title_fallback=True,
    )

    constants_by_title, constants_ids = build_constant_lookups(
        constants_39s_records,
        metadata_id_to_title,
    )

    override_lookup = build_override_lookup(
        override_records
    )

    print(
        f"Metadata title-to-ID mappings: "
        f"{len(metadata_title_to_id)}."
    )

    print(
        f"IDs present in 39s Constants: "
        f"{len(constants_ids)}."
    )

    print(
        f"Community override records: "
        f"{len(override_records)}."
    )

    output_rows = []
    represented_override_keys = set()
    unmatched_titles = set()

    for item in scraped_rows:
        title = item[0]
        title_key = normalize_title(title)
        difficulty = normalize_difficulty(item[4])

        song_id = metadata_title_to_id.get(title_key)

        if song_id is None:
            unmatched_titles.add(title)

        constant_39s = constants_by_title.get(
            (title_key, difficulty),
            "",
        )

        constant_override = ""

        if song_id is not None:
            override_key = (song_id, difficulty)
            override_record = override_lookup.get(override_key)

            if override_record:
                represented_override_keys.add(override_key)
                constant_override = override_record["constant"]

        output_rows.append(
            item + [
                str(song_id) if song_id is not None else "",
                constant_39s,
                constant_override,
            ]
        )

    output_rows = sort_rows(output_rows)

    appended_rows = []

    existing_keys = {
        (
            numeric_id(row[7]),
            normalize_difficulty(row[4]),
        )
        for row in output_rows
        if numeric_id(row[7]) is not None
    }

    for record in override_records:
        song_id = record["id"]
        difficulty = record["difficulty"]

        if song_id in constants_ids:
            continue

        key = (song_id, difficulty)

        if key in existing_keys:
            continue

        jp_title = (
            metadata_id_to_title.get(song_id)
            or record["title"]
        )

        appended_rows.append([
            jp_title,
            "",
            "",
            "",
            difficulty.upper(),
            "",
            "",
            str(song_id),
            "",
            record["constant"],
        ])

        existing_keys.add(key)

    appended_rows = sort_rows(appended_rows)

    final_rows = output_rows + appended_rows

    print(f"Scraped rows: {len(output_rows)}.")
    print(f"Override-only rows appended: {len(appended_rows)}.")
    print(f"Unmatched community titles: {len(unmatched_titles)}.")

    if unmatched_titles:
        print(
            "First unmatched community titles: "
            + ", ".join(sorted(unmatched_titles)[:20])
        )

    try:
        worksheet = workbook.worksheet(OUTPUT_TAB)
    except gspread.WorksheetNotFound:
        worksheet = workbook.add_worksheet(
            title=OUTPUT_TAB,
            rows=max(1000, len(final_rows) + 10),
            cols=len(HEADERS),
        )

    worksheet.clear()

    worksheet.resize(
        rows=max(1000, len(final_rows) + 10),
        cols=len(HEADERS),
    )

    worksheet.update(
        range_name="A1",
        values=[HEADERS] + final_rows,
        value_input_option="RAW",
    )

    matched_ids = sum(
        1 for row in final_rows
        if row[7]
    )

    matched_39s = sum(
        1 for row in final_rows
        if row[8]
    )

    matched_overrides = sum(
        1 for row in final_rows
        if row[9]
    )

    print("=== COMMUNITY scraper completed ===")
    print(f"Total output rows: {len(final_rows)}.")
    print(f"IDs matched: {matched_ids}.")
    print(f"39s constants matched: {matched_39s}.")
    print(f"Overrides matched: {matched_overrides}.")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        import traceback
        traceback.print_exc()
        raise