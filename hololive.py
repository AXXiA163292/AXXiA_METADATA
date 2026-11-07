
import os
import re
import json
import asyncio
from pathlib import Path
from urllib.parse import urljoin, urlparse

import gspread
from google.oauth2.service_account import Credentials
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeoutError

# ============================================================
# CONFIG
# ============================================================

SPREADSHEET_ID = "1Fpp_sJbGjuKxUAcWhZOM1YqHJJrUHTWE56lO4dthhak"
WORKSHEET_NAME = "holodori"

BASE_URL = "https://holodori.best"
SONGS_URL = f"{BASE_URL}/songs"

HEADLESS = True
DETAIL_CONCURRENCY = 5
PAGE_TIMEOUT = 30000
SCROLL_DELAY = 1.0
MAX_STABLE_ROUNDS = 5
MAX_SCROLL_ROUNDS = 100

HEADERS = [
    "musicId",
    "title",
    "artist",
    "composer",
    "lyricist",
    "arranger",
    "duration",
    "releaseDate",
    "jacketUrl",
    "audioUrl",
    "hasMv",
    "pageUrl",
    "ezPlayLevel",
    "ezTotalNoteCount",
    "ezChartUrl",
    "normalPlayLevel",
    "normalTotalNoteCount",
    "normalChartUrl",
    "hardPlayLevel",
    "hardTotalNoteCount",
    "hardChartUrl",
    "expertPlayLevel",
    "expertTotalNoteCount",
    "expertChartUrl",
]

DIFFICULTIES = ["easy", "normal", "hard", "expert"]

# ============================================================
# GOOGLE SHEETS
# ============================================================

def get_gspread_client():
    raw_key = os.environ.get("GCP_SA_KEY")

    if raw_key:
        info = json.loads(raw_key)
        credentials = Credentials.from_service_account_info(
            info,
            scopes=[
                "https://www.googleapis.com/auth/spreadsheets",
                "https://www.googleapis.com/auth/drive",
            ],
        )
    else:
        key_path = Path(__file__).resolve().parent / "service_account.json"

        if not key_path.exists():
            raise FileNotFoundError(
                "Missing GCP_SA_KEY environment variable or service_account.json"
            )

        credentials = Credentials.from_service_account_file(
            str(key_path),
            scopes=[
                "https://www.googleapis.com/auth/spreadsheets",
                "https://www.googleapis.com/auth/drive",
            ],
        )

    return gspread.authorize(credentials)


def get_worksheet():
    client = get_gspread_client()
    spreadsheet = client.open_by_key(SPREADSHEET_ID)

    try:
        return spreadsheet.worksheet(WORKSHEET_NAME)
    except gspread.WorksheetNotFound:
        return spreadsheet.add_worksheet(
            title=WORKSHEET_NAME,
            rows=1000,
            cols=len(HEADERS),
        )


def write_to_sheet(rows):
    if not rows:
        print("No songs found. Sheet was not modified.")
        return

    worksheet = get_worksheet()

    # Keep the existing worksheet and replace its contents.
    worksheet.clear()

    all_values = [HEADERS] + rows

    for start in range(0, len(all_values), 500):
        chunk = all_values[start:start + 500]
        worksheet.update(
            range_name=f"A{start + 1}",
            values=chunk,
            value_input_option="RAW",
        )

    print(f"Wrote {len(rows)} songs to '{WORKSHEET_NAME}'.")


# ============================================================
# GENERAL HELPERS
# ============================================================

def clean_text(value):
    if value is None:
        return ""

    return re.sub(r"\s+", " ", str(value)).strip()


def absolute_url(value, base_url):
    if not value:
        return ""

    value = clean_text(value)

    if value.startswith(("data:", "javascript:", "#")):
        return ""

    return urljoin(base_url, value)


def normalize_difficulty(value):
    value = clean_text(value).casefold()

    aliases = {
        "ez": "easy",
        "easy": "easy",
        "normal": "normal",
        "hard": "hard",
        "expert": "expert",
    }

    return aliases.get(value, "")


def empty_difficulties():
    return {
        difficulty: {
            "level": "",
            "notes": "",
            "chart": "",
        }
        for difficulty in DIFFICULTIES
    }


# ============================================================
# LISTING PAGE
# ============================================================

async def parse_listing_row(row):
    return await row.evaluate(
        """element => {
            const titleElement = element.querySelector(
                '.song-row-title'
            );

            const jacketElement = element.querySelector(
                'img.song-row-jacket'
            );

            const durationElement = element.querySelector(
                '.song-row-length'
            );

            const difficultyElements = [
                ...element.querySelectorAll('.song-diffs .diff-circle')
            ];

            const href = element.getAttribute('href') || '';

            const match = href.match(/\\/songs\\/([^/?#]+)/);

            const difficulties = difficultyElements.map(
                element => (element.textContent || '').trim()
            );

            return {
                id: match ? match[1] : '',
                href: href,
                title: titleElement
                    ? (
                        titleElement.getAttribute('title') ||
                        titleElement.textContent ||
                        ''
                    ).trim()
                    : '',
                jacket: jacketElement
                    ? (
                        jacketElement.getAttribute('src') ||
                        jacketElement.getAttribute('data-src') ||
                        jacketElement.getAttribute('data-lazy-src') ||
                        ''
                    )
                    : '',
                duration: durationElement
                    ? durationElement.textContent.trim()
                    : '',
                difficulties: difficulties
            };
        }"""
    )


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
        print("No .song-row elements found on the listing page.")
        return []

    seen_count = 0
    stable_rounds = 0

    for round_number in range(MAX_SCROLL_ROUNDS):
        rows = page.locator(".song-row")
        current_count = await rows.count()

        if current_count == seen_count:
            stable_rounds += 1
        else:
            stable_rounds = 0
            seen_count = current_count

        print(
            f"Listing scroll {round_number + 1}: "
            f"{current_count} song rows"
        )

        if stable_rounds >= MAX_STABLE_ROUNDS:
            break

        await page.evaluate(
            """() => {
                window.scrollTo(
                    0,
                    Math.max(
                        document.body.scrollHeight,
                        document.documentElement.scrollHeight
                    )
                );
            }"""
        )

        await page.wait_for_timeout(int(SCROLL_DELAY * 1000))

    # One final pass after lazy-loaded content has settled.
    await page.wait_for_timeout(1000)

    locator = page.locator(".song-row")
    count = await locator.count()

    songs_by_id = {}

    for index in range(count):
        row = locator.nth(index)

        try:
            data = await parse_listing_row(row)
        except Exception as error:
            print(f"Could not parse listing row {index}: {error}")
            continue

        music_id = clean_text(data.get("id"))

        if not music_id:
            continue

        title = clean_text(data.get("title"))

        if not title:
            continue

        page_url = absolute_url(
            data.get("href"),
            SONGS_URL,
        )

        difficulties = empty_difficulties()

        for difficulty, level in zip(
            DIFFICULTIES,
            data.get("difficulties", []),
        ):
            difficulties[difficulty]["level"] = clean_text(level)

        songs_by_id[music_id] = {
            "id": music_id,
            "title": title,
            "artist": "",
            "composer": "",
            "lyricist": "",
            "arranger": "",
            "duration": clean_text(data.get("duration")),
            "releaseDate": "",
            "jacketUrl": absolute_url(
                data.get("jacket"),
                SONGS_URL,
            ),
            "audioUrl": "",
            "hasMv": "",
            "pageUrl": page_url,
            "difficulties": difficulties,
        }

    songs = list(songs_by_id.values())

    print(f"Discovered {len(songs)} unique songs.")

    return songs


# ============================================================
# DETAIL PAGE EXTRACTION
# ============================================================

def detect_bot_wall(text):
    text = clean_text(text).casefold()

    indicators = [
        "verify you are human",
        "checking your browser",
        "access denied",
        "captcha",
        "security check",
        "are you a robot",
    ]

    return any(indicator in text for indicator in indicators)


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

    body_text = await page.locator("body").inner_text(
        timeout=10000
    )

    if detect_bot_wall(body_text):
        print(f"Bot wall detected for {music['id']}.")
        return result

    # --------------------------------------------------------
    # Structured metadata: dt/dd, th/td, and label/value rows
    # --------------------------------------------------------

    metadata = await page.evaluate(
        """() => {
            const output = {};

            const normalize = value =>
                (value || '').replace(/\\s+/g, ' ').trim();

            const add = (key, value) => {
                key = normalize(key).replace(/:$/, '').toLowerCase();
                value = normalize(value);

                if (key && value && !output[key]) {
                    output[key] = value;
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

            document.querySelectorAll(
                '[class*="metadata"], [class*="detail"], [class*="info"]'
            ).forEach(container => {
                const children = [...container.children];

                if (children.length === 2) {
                    add(
                        children[0].textContent,
                        children[1].textContent
                    );
                }
            });

            const links = [...document.querySelectorAll('a[href]')].map(
                element => ({
                    href: element.href,
                    text: normalize(element.textContent)
                })
            );

            const media = [
                ...document.querySelectorAll('audio[src], audio source[src]')
            ].map(element => element.src).filter(Boolean);

            const images = [
                ...document.querySelectorAll('img')
            ].map(element => ({
                src: element.src,
                alt: element.alt || ''
            }));

            return {
                metadata,
                links,
                media,
                images,
                title: document.querySelector('h1')
                    ? normalize(document.querySelector('h1').textContent)
                    : ''
            };
        }"""
    )

    fields = metadata.get("metadata", {})

    def find_metadata(*labels):
        for key, value in fields.items():
            normalized_key = key.casefold()

            if any(label in normalized_key for label in labels):
                return clean_text(value)

        return ""

    result["artist"] = find_metadata("artist", "vocal")
    result["composer"] = find_metadata("composer", "music")
    result["lyricist"] = find_metadata("lyricist", "lyrics")
    result["arranger"] = find_metadata("arranger", "arrangement")
    result["duration"] = find_metadata("duration", "length") or music["duration"]
    result["releaseDate"] = find_metadata(
        "release date",
        "released",
        "release",
    )

    # --------------------------------------------------------
    # Audio URL: only actual media sources found in the DOM
    # --------------------------------------------------------

    media_urls = metadata.get("media", [])

    if media_urls:
        result["audioUrl"] = absolute_url(
            media_urls[0],
            music["pageUrl"],
        )
    else:
        for link in metadata.get("links", []):
            href = clean_text(link.get("href"))

            if re.search(
                r"\.(mp3|ogg|wav|m4a|aac|flac)(?:\?|$)",
                href,
                re.I,
            ):
                result["audioUrl"] = absolute_url(
                    href,
                    music["pageUrl"],
                )
                break

    # --------------------------------------------------------
    # MV availability
    # --------------------------------------------------------

    mv_text = body_text.casefold()

    if re.search(r"\b(no mv|without mv|mv unavailable)\b", mv_text):
        result["hasMv"] = "FALSE"
    elif re.search(r"\b(mv|music video|3dmv|2dmv)\b", mv_text):
        result["hasMv"] = "TRUE"

    # --------------------------------------------------------
    # Difficulty levels and note counts
    # --------------------------------------------------------

    difficulty_data = await page.evaluate(
        """() => {
            const result = [];

            const normalize = value =>
                (value || '').replace(/\\s+/g, ' ').trim();

            const selectors = [
                '[class*="difficulty"]',
                '[class*="chart"]',
                '[class*="diff"]',
                '[class*="note"]'
            ];

            const elements = new Set();

            selectors.forEach(selector => {
                document.querySelectorAll(selector).forEach(element => {
                    elements.add(element);
                });
            });

            elements.forEach(element => {
                const text = normalize(element.innerText);

                if (text) {
                    result.push({
                        text,
                        className: String(element.className || '')
                    });
                }
            });

            return result;
        }"""
    )

    for item in difficulty_data:
        text = clean_text(item.get("text"))
        class_name = clean_text(item.get("className")).casefold()

        difficulty = normalize_difficulty(
            class_name.replace("-", " ").replace("_", " ")
        )

        if not difficulty:
            for name in DIFFICULTIES:
                if re.search(rf"\\b{re.escape(name)}\\b", text, re.I):
                    difficulty = name
                    break

        if not difficulty:
            continue

        # Match a level and a note count only when both are visible
        # in the same extracted element.
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
            result["difficulties"][difficulty]["level"] = (
                level_match.group(1)
            )

        if notes_match:
            result["difficulties"][difficulty]["notes"] = (
                notes_match.group(1).replace(",", "")
            )

    # Fallback for pages where difficulty and combo counts appear
    # together in the visible text without dedicated metadata labels.
    normalized_text = clean_text(body_text)

    patterns = [
        r"(\d{1,2})\s*(Easy|Normal|Hard|Expert)\s*([\d,]+)\s*(?:notes?|combos?)",
        r"(Easy|Normal|Hard|Expert)\s*(\d{1,2})\s*([\d,]+)\s*(?:notes?|combos?)",
    ]

    for pattern in patterns:
        for match in re.finditer(pattern, normalized_text, re.I):
            groups = match.groups()

            if groups[0].isdigit():
                level, difficulty_name, notes = groups
            else:
                difficulty_name, level, notes = groups

            difficulty = normalize_difficulty(difficulty_name)

            if not difficulty:
                continue

            if not result["difficulties"][difficulty]["level"]:
                result["difficulties"][difficulty]["level"] = level

            if not result["difficulties"][difficulty]["notes"]:
                result["difficulties"][difficulty]["notes"] = (
                    notes.replace(",", "")
                )

    # --------------------------------------------------------
    # Chart URLs: only actual links extracted from the page
    # --------------------------------------------------------

    for link in metadata.get("links", []):
        href = clean_text(link.get("href"))
        link_text = clean_text(link.get("text"))

        if not href:
            continue

        combined = f"{href} {link_text}".casefold()

        difficulty = ""

        for name in DIFFICULTIES:
            if re.search(rf"\b{re.escape(name)}\b", combined):
                difficulty = name
                break

        if not difficulty:
            continue

        if re.search(r"\b(chart|score|difficulty|譜面)\b", combined):
            result["difficulties"][difficulty]["chart"] = absolute_url(
                href,
                music["pageUrl"],
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
                "artist",
                "composer",
                "lyricist",
                "arranger",
                "duration",
                "releaseDate",
                "audioUrl",
                "hasMv",
            ]:
                if details.get(field):
                    music[field] = details[field]

            for difficulty in DIFFICULTIES:
                for field in ["level", "notes", "chart"]:
                    value = details["difficulties"][difficulty].get(field)

                    if value:
                        music["difficulties"][difficulty][field] = value

            print(f"Enriched {music['id']}: {music['title']}")

        except PlaywrightTimeoutError:
            print(f"Timeout on {music['id']}: {music['title']}")

        except Exception as error:
            print(f"Error on {music['id']}: {error}")

        finally:
            await page.close()

    return music


# ============================================================
# OUTPUT
# ============================================================

def to_output_row(music):
    difficulties = music["difficulties"]

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
        data = difficulties[difficulty]

        row.extend([
            data["level"] or "-",
            data["notes"] or "-",
            data["chart"] or "-",
        ])

    return row


# ============================================================
# MAIN
# ============================================================

async def main():
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(
            headless=HEADLESS,
        )

        context = await browser.new_context(
            viewport={
                "width": 1440,
                "height": 1000,
            },
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
                print(
                    "No songs discovered. "
                    "The spreadsheet was left unchanged."
                )
                return

            semaphore = asyncio.Semaphore(DETAIL_CONCURRENCY)

            tasks = [
                enrich_song(context, semaphore, music)
                for music in songs
            ]

            enriched_songs = await asyncio.gather(*tasks)

            # Preserve discovery order.
            rows = [
                to_output_row(music)
                for music in enriched_songs
            ]

            write_to_sheet(rows)

            print("Holodori scraping completed.")

        finally:
            await listing_page.close()
            await context.close()
            await browser.close()


if __name__ == "__main__":
    asyncio.run(main())