
import os
import re
import json
import asyncio
from pathlib import Path
from urllib.parse import urljoin

import gspread
from google.oauth2.service_account import Credentials
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeoutError

SPREADSHEET_ID = "1Fpp_sJbGjuKxUAcWhZOM1YqHJJrUHTWE56lO4dthhak"
WORKSHEET_NAME = "holodori"

BASE_URL = "https://holodori.best"
SONGS_URL = f"{BASE_URL}/songs"

HEADLESS = True
DETAIL_CONCURRENCY = 5
PAGE_TIMEOUT = 30000
SCROLL_DELAY = 1
MAX_STABLE_ROUNDS = 5
MAX_SCROLL_ROUNDS = 100

DIFFICULTIES = ["easy", "normal", "hard", "expert"]

HEADERS = [
    "musicId", "title", "artist", "composer", "lyricist",
    "arranger", "duration", "releaseDate", "jacketUrl",
    "audioUrl", "hasMv", "pageUrl",
    "ezPlayLevel", "ezTotalNoteCount", "ezChartUrl",
    "normalPlayLevel", "normalTotalNoteCount", "normalChartUrl",
    "hardPlayLevel", "hardTotalNoteCount", "hardChartUrl",
    "expertPlayLevel", "expertTotalNoteCount", "expertChartUrl",
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


def clean(value):
    return re.sub(r"\s+", " ", str(value or "")).strip()


def absolute(value, base):
    if not value:
        return ""

    value = clean(value)

    if value.startswith(("data:", "javascript:", "#")):
        return ""

    return urljoin(base, value)


def empty_difficulties():
    return {
        name: {"level": "", "notes": "", "chart": ""}
        for name in DIFFICULTIES
    }


def normalize_difficulty(value):
    value = clean(value).casefold()

    for name in DIFFICULTIES:
        if name in value:
            return name

    aliases = {
        "ez": "easy",
        "nm": "normal",
        "hr": "hard",
        "ex": "expert",
    }

    return aliases.get(value, "")


async def parse_listing_row(row):
    return await row.evaluate("""element => {
        const title = element.querySelector('.song-row-title');
        const jacket = element.querySelector('img.song-row-jacket');
        const duration = element.querySelector('.song-row-length');
        const href = element.getAttribute('href') || '';
        const match = href.match(/\\/songs\\/([^/?#]+)/);

        return {
            id: match ? match[1] : '',
            href,
            title: title
                ? (title.getAttribute('title') || title.textContent || '').trim()
                : '',
            jacket: jacket
                ? (
                    jacket.getAttribute('src') ||
                    jacket.getAttribute('data-src') ||
                    jacket.getAttribute('data-lazy-src') ||
                    ''
                )
                : '',
            duration: duration ? duration.textContent.trim() : '',
            levels: [...element.querySelectorAll('.song-diffs .diff-circle')]
                .map(el => el.textContent.trim())
        };
    }""")


async def discover_songs(page):
    print(f"Opening {SONGS_URL}")

    await page.goto(
        SONGS_URL,
        wait_until="domcontentloaded",
        timeout=PAGE_TIMEOUT,
    )

    try:
        await page.locator(".song-row").first.wait_for(
            state="attached",
            timeout=PAGE_TIMEOUT,
        )
    except PlaywrightTimeoutError:
        print("No song rows found.")
        return []

    previous_count = -1
    stable_rounds = 0

    for round_number in range(MAX_SCROLL_ROUNDS):
        count = await page.locator(".song-row").count()
        print(f"Scroll {round_number + 1}: {count} songs")

        if count == previous_count:
            stable_rounds += 1
        else:
            stable_rounds = 0
            previous_count = count

        if stable_rounds >= MAX_STABLE_ROUNDS:
            break

        await page.evaluate(
            "() => window.scrollTo(0, document.documentElement.scrollHeight)"
        )
        await page.wait_for_timeout(int(SCROLL_DELAY * 1000))

    await page.wait_for_timeout(1000)

    locator = page.locator(".song-row")
    count = await locator.count()
    songs = {}

    for index in range(count):
        try:
            item = await parse_listing_row(locator.nth(index))
        except Exception as error:
            print(f"Could not parse row {index}: {error}")
            continue

        music_id = clean(item["id"])
        title = clean(item["title"])

        if not music_id or not title:
            continue

        difficulties = empty_difficulties()

        for difficulty, level in zip(DIFFICULTIES, item["levels"]):
            difficulties[difficulty]["level"] = clean(level)

        songs[music_id] = {
            "id": music_id,
            "title": title,
            "artist": "",
            "composer": "",
            "lyricist": "",
            "arranger": "",
            "duration": clean(item["duration"]),
            "releaseDate": "",
            "jacketUrl": absolute(item["jacket"], SONGS_URL),
            "audioUrl": "",
            "hasMv": "",
            "pageUrl": absolute(item["href"], SONGS_URL),
            "difficulties": difficulties,
        }

    result = list(songs.values())
    print(f"Discovered {len(result)} unique songs.")
    return result


def bot_wall(text):
    text = clean(text).casefold()

    return any(term in text for term in [
        "verify you are human",
        "checking your browser",
        "access denied",
        "captcha",
        "security check",
        "are you a robot",
    ])


async def extract_detail_data(page, music):
    result = {
        "artist": "",
        "composer": "",
        "lyricist": "",
        "arranger": "",
        "duration": "",
        "releaseDate": "",
        "audioUrl": "",
        "hasMv": "",
        "difficulties": empty_difficulties(),
    }

    body_text = await page.locator("body").inner_text(timeout=10000)

    if bot_wall(body_text):
        print(f"Bot check blocked detail page: {music['id']}")
        return result

    dom_data = await page.evaluate("""() => {
        const normalize = value =>
            (value || '').replace(/\\s+/g, ' ').trim();

        const metadata = {};

        const add = (key, value) => {
            key = normalize(key).replace(/:$/, '').toLowerCase();
            value = normalize(value);

            if (key && value && !metadata[key]) {
                metadata[key] = value;
            }
        };

        document.querySelectorAll('dt').forEach(dt => {
            const dd = dt.nextElementSibling;
            if (dd && dd.tagName.toLowerCase() === 'dd') {
                add(dt.textContent, dd.textContent);
            }
        });

        document.querySelectorAll('tr').forEach(tr => {
            const cells = [...tr.querySelectorAll('th,td')];
            if (cells.length >= 2) {
                add(cells[0].textContent, cells[1].textContent);
            }
        });

        const links = [...document.querySelectorAll('a[href]')].map(a => ({
            href: a.href,
            text: normalize(a.textContent)
        }));

        const media = [
            ...document.querySelectorAll('audio[src], audio source[src]')
        ].map(el => el.src).filter(Boolean);

        const difficultyElements = [
            ...document.querySelectorAll(
                '[class*="difficulty"], [class*="chart"], ' +
                '[class*="diff"], [class*="note"]'
            )
        ].map(el => ({
            text: normalize(el.innerText),
            className: String(el.className || '')
        })).filter(el => el.text);

        return {metadata, links, media, difficultyElements};
    }""")

    metadata = dom_data["metadata"]

    def find_field(*labels):
        for key, value in metadata.items():
            if any(label in key for label in labels):
                return clean(value)
        return ""

    result["artist"] = find_field("artist", "vocal")
    result["composer"] = find_field("composer", "music")
    result["lyricist"] = find_field("lyricist", "lyrics")
    result["arranger"] = find_field("arranger", "arrangement")
    result["duration"] = find_field("duration", "length") or music["duration"]
    result["releaseDate"] = find_field("release date", "released", "release")

    if dom_data["media"]:
        result["audioUrl"] = absolute(dom_data["media"][0], music["pageUrl"])
    else:
        for link in dom_data["links"]:
            href = link["href"]

            if re.search(r"\.(mp3|ogg|wav|m4a|aac|flac)(?:\?|$)", href, re.I):
                result["audioUrl"] = absolute(href, music["pageUrl"])
                break

    lower_text = body_text.casefold()

    if re.search(r"\b(no mv|without mv|mv unavailable)\b", lower_text):
        result["hasMv"] = "FALSE"
    elif re.search(r"\b(mv|music video|3dmv|2dmv)\b", lower_text):
        result["hasMv"] = "TRUE"

    # Parse difficulty cards and note counts from actual visible DOM.
    for item in dom_data["difficultyElements"]:
        text = clean(item["text"])
        class_name = clean(item["className"]).casefold()

        difficulty = normalize_difficulty(class_name)

        if not difficulty:
            difficulty = normalize_difficulty(text)

        if not difficulty:
            continue

        level_match = re.search(
            r"\b(?:level|lv\.?)\s*[:#]?\s*(\d{1,2})\b",
            text,
            re.I,
        )

        notes_match = re.search(
            r"\b([\d,]+)\s*(?:notes?|combos?)\b",
            text,
            re.I,
        )

        if level_match:
            result["difficulties"][difficulty]["level"] = level_match.group(1)

        if notes_match:
            result["difficulties"][difficulty]["notes"] = (
                notes_match.group(1).replace(",", "")
            )

    # Text fallback for layouts that do not expose dedicated difficulty cards.
    normalized_text = clean(body_text)

    patterns = [
        r"(\d{1,2})\s*(Easy|Normal|Hard|Expert)\s*([\d,]+)\s*(?:notes?|combos?)",
        r"(Easy|Normal|Hard|Expert)\s*(\d{1,2})\s*([\d,]+)\s*(?:notes?|combos?)",
    ]

    for pattern in patterns:
        for match in re.finditer(pattern, normalized_text, re.I):
            groups = match.groups()

            if groups[0].isdigit():
                level, name, notes = groups
            else:
                name, level, notes = groups

            difficulty = normalize_difficulty(name)

            if not difficulty:
                continue

            current = result["difficulties"][difficulty]

            if not current["level"]:
                current["level"] = level

            if not current["notes"]:
                current["notes"] = notes.replace(",", "")

    # Only use chart links that actually exist in the page.
    for link in dom_data["links"]:
        combined = f"{link['href']} {link['text']}".casefold()

        difficulty = normalize_difficulty(combined)

        if not difficulty:
            continue

        if re.search(r"\b(chart|score|difficulty|譜面)\b", combined):
            result["difficulties"][difficulty]["chart"] = absolute(
                link["href"], music["pageUrl"]
            )

    return result


async def enrich_song(context, semaphore, music):
    async with semaphore:
        page = await context.new_page()

        try:
            await page.goto(
                music["pageUrl"],
                wait_until="domcontentloaded",
                timeout=PAGE_TIMEOUT,
            )

            await page.wait_for_timeout(500)

            details = await extract_detail_data(page, music)

            for field in [
                "artist", "composer", "lyricist", "arranger",
                "duration", "releaseDate", "audioUrl", "hasMv",
            ]:
                if details.get(field):
                    music[field] = details[field]

            for difficulty in DIFFICULTIES:
                for field in ["level", "notes", "chart"]:
                    value = details["difficulties"][difficulty][field]

                    if value:
                        music["difficulties"][difficulty][field] = value

            print(f"Processed {music['id']}: {music['title']}")

        except PlaywrightTimeoutError:
            print(f"Timeout: {music['id']}")

        except Exception as error:
            print(f"Error on {music['id']}: {error}")

        finally:
            await page.close()

    return music


def to_output_row(music):
    row = [
        music["id"],
        music["title"],
        music["artist"] or "-",
        music["composer"] or "-",
        music["lyricist"] or "-",
        music["arranger"] or "-",
        music["duration"] or "-",
        music["releaseDate"] or "-",
        music["jacketUrl"] or "-",
        music["audioUrl"] or "-",
        music["hasMv"] or "-",
        music["pageUrl"] or "-",
    ]

    for difficulty in DIFFICULTIES:
        data = music["difficulties"][difficulty]

        row.extend([
            data["level"] or "-",
            data["notes"] or "-",
            data["chart"] or "-",
        ])

    return row


def write_sheet(rows):
    if not rows:
        print("No songs found. Sheet unchanged.")
        return

    client = get_client()
    spreadsheet = client.open_by_key(SPREADSHEET_ID)

    try:
        worksheet = spreadsheet.worksheet(WORKSHEET_NAME)
    except gspread.WorksheetNotFound:
        worksheet = spreadsheet.add_worksheet(
            title=WORKSHEET_NAME,
            rows=max(1000, len(rows) + 1),
            cols=len(HEADERS),
        )

    worksheet.clear()

    values = [HEADERS] + rows

    for start in range(0, len(values), 500):
        worksheet.update(
            range_name=f"A{start + 1}",
            values=values[start:start + 500],
            value_input_option="RAW",
        )

    print(f"Wrote {len(rows)} songs to '{WORKSHEET_NAME}'.")


async def main():
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=HEADLESS)

        context = await browser.new_context(
            viewport={"width": 1440, "height": 1000},
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/131.0.0.0 Safari/537.36"
            ),
            locale="en-US",
        )

        listing_page = await context.new_page()

        try:
            songs = await discover_songs(listing_page)

            if not songs:
                print("No songs discovered. Sheet unchanged.")
                return

            semaphore = asyncio.Semaphore(DETAIL_CONCURRENCY)

            enriched = await asyncio.gather(*[
                enrich_song(context, semaphore, song)
                for song in songs
            ])

            rows = [to_output_row(song) for song in enriched]
            write_sheet(rows)

        finally:
            await listing_page.close()
            await context.close()
            await browser.close()


if __name__ == "__main__":
    asyncio.run(main())