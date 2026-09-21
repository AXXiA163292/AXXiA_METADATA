import os, sys, subprocess

# 1. Auto-install missing packages
REQUIRED = {
    'requests': 'requests', 
    'bs4': 'beautifulsoup4', 
    'gspread': 'gspread', 
    'google.oauth2': 'google-auth'
}

for module_name, pip_name in REQUIRED.items():
    try:
        __import__(module_name)
    except ImportError:
        print(f"Installing missing library: {pip_name}... (this may take a moment)")
        subprocess.check_call([sys.executable, "-m", "pip", "install", pip_name])

# 2. Now safely import everything your script needs
import re, time, json, requests, gspread
from bs4 import BeautifulSoup, Tag
from google.oauth2.service_account import Credentials

SPREADSHEET_ID  = "1Fpp_sJbGjuKxUAcWhZOM1YqHJJrUHTWE56lO4dthhak"
SHEET_NAME      = "custom"

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
SERVICE_ACCOUNT = os.path.join(SCRIPT_DIR, "service_account.json")
DELAY           = 0.3
CUSTOM_CACHE_FILE = "custom_cache.json"

IS_GITHUB_ACTIONS = os.environ.get("GITHUB_ACTIONS") == "true"

S = requests.Session()
S.headers.update({"User-Agent": "Mozilla/5.0"})

EMPTY = ["-"] * 18

def resolve_url(url):
    url = url.strip()
    if re.match(r"^[0-9a-f]{32}$", url):
        return "https://untitledcharts.com/sonolus/levels/UnCh-" + url
    if re.match(r"^rush-\d+-\d+-\w+$", url):
        return "https://sonolus.sbuga.com/sonolus/levels/sekai-rush-" + url[5:]
    if re.match(r"^\d+-\d+-\w+$", url):
        return "https://sonolus.sekai.best/sonolus/levels/sekai-best-" + url
    if re.match(r"^\d+$", url):
        return "https://coconut.sonolus.com/next-sekai/sonolus/levels/coconut-next-sekai-" + url
    m = re.search(r"untitledcharts\.com/levels/(UnCh-[0-9a-f]+)/?$", url)
    if m: return "https://untitledcharts.com/sonolus/levels/" + m.group(1)
    m = re.search(r"coconut\.sonolus\.com/next-sekai/levels/(coconut-next-sekai-\d+)/?$", url)
    if m: return "https://coconut.sonolus.com/next-sekai/sonolus/levels/" + m.group(1)
    m = re.search(r"sonolus\.sekai\.best/levels/(sekai-best-[\w-]+)/?$", url)
    if m: return "https://sonolus.sekai.best/sonolus/levels/" + m.group(1)
    m = re.search(r"sonolus\.sbuga\.com/levels/(sekai-rush-[\w-]+)/?$", url)
    if m: return "https://sonolus.sbuga.com/sonolus/levels/" + m.group(1)
    m = re.search(r"cc\.milkbun\.org/levels/(chcy-[\w]+)/?$", url)
    if m: return "https://cc.milkbun.org/sonolus/levels/" + m.group(1)
    m = re.search(r"ptlv\.sevenc7c\.com/levels/(ptlv-[\w]+)/?$", url)
    if m: return "https://ptlv.sevenc7c.com/sonolus/levels/" + m.group(1)
    return url

def is_url(val):
    return bool(val and (val.startswith("http") or re.match(r"^[0-9a-f]{32}$", val)
        or re.match(r"^(rush-)?\d+[-\w]*$", val)))

def fetch_level(raw_url):
    api_url = resolve_url(raw_url)
    try:
        r = S.get(api_url, timeout=15)
        data = r.json()
    except Exception as e:
        print(f"    [error] {e}")
        return EMPTY[:]

    if not (data.get("item") and data["item"].get("cover") and data["item"].get("bgm")):
        return EMPTY[:]

    it   = data["item"]
    url  = api_url

    # source + apiBase
    if "untitledcharts.com" in url:        source, apiBase = "Untitled Charts", "https://untitledcharts.com"
    elif "coconut.sonolus.com/next-sekai" in url: source, apiBase = "Next Sekai",      "https://coconut.sonolus.com/next-sekai"
    elif "sonolus.sekai.best" in url:      source, apiBase = "Sekai Best",      "https://sonolus.sekai.best"
    elif "sonolus.sbuga.com" in url:       source, apiBase = "Sbuga",           "https://sonolus.sbuga.com"
    elif "cc.milkbun.org" in url:          source, apiBase = "Chart Cyanvas",   "https://cc.milkbun.org"
    elif "ptlv.sevenc7c.com" in url:       source, apiBase = "Potato Leave",    "https://ptlv.sevenc7c.com"
    else:                                  source, apiBase = "-",               ""

    def asset(srl):
        if not srl or not srl.get("url"): return "-"
        u = srl["url"]
        return u if u.startswith("http") else apiBase + u

    # level id (strip prefix)
    level_id = it.get("name", "-")
    for prefix, strip in [("UnCh-",""), ("coconut-next-sekai-",""), ("chcy-",""), ("ptlv-","")]:
        if level_id.startswith(prefix): level_id = level_id[len(prefix):]; break

    KNOWN_DIFFS  = {"EASY","NORMAL","HARD","EXPERT","MASTER","APPEND"}
    META_ICONS   = {"heartHollow","comment","clock","unlock","lock"}
    AGO_RE       = re.compile(r"^\d+(s|min|h|d|w|mo|y)\s*ago$", re.I)

    tags = it.get("tags") or []

    # difficulty
    difficulty = "-"
    for t in tags:
        if t.get("title","").lstrip("#").upper() in KNOWN_DIFFS:
            difficulty = t["title"].lstrip("#").upper(); break
    if difficulty == "-":
        for d in KNOWN_DIFFS:
            if d in (it.get("title","")).upper(): difficulty = d; break

    # tag list
    tag_list = []
    for t in tags:
        lbl = t.get("title","").lstrip("#")
        if lbl.upper() in KNOWN_DIFFS: continue
        if t.get("icon") in META_ICONS: continue
        if t.get("icon") == "tag" or (not t.get("icon") and not AGO_RE.match(lbl)):
            tag_list.append(lbl)
    tags_col = ",".join(tag_list) if tag_list else "-"

    privacy = "-"
    for t in tags:
        if t.get("icon") in ("unlock","lock"): privacy = t.get("title","-"); break

    likes = comments_count = "-"
    for t in tags:
        if t.get("icon") == "heartHollow": likes = t.get("title","-")
        if t.get("icon") == "comment":      comments_count = t.get("title","-")

    upload_date = "-"
    if source != "Untitled Charts":
        for t in tags:
            if not t.get("title") or t.get("icon"): continue
            m = re.match(r"^(\d+)(mo|w|d|h)\s*ago$", t["title"].strip(), re.I)
            if not m: continue
            import datetime
            num, unit = int(m.group(1)), m.group(2).lower()
            now = datetime.date.today()
            if   unit == "mo": now = now.replace(month=((now.month-1-num)%12)+1)
            elif unit == "w":  now -= datetime.timedelta(weeks=num)
            elif unit == "d":  now -= datetime.timedelta(days=num)
            upload_date = now.strftime("%m/%Y"); break

    staff_pick = any(t.get("icon") == "trophy" and "staff" in t.get("title","").lower() for t in tags)

    # level URLs
    name = it.get("name","")
    if source == "Untitled Charts":
        level_url = f"https://untitledcharts.com/levels/{name}/"
        beta_url  = f"https://beta.untitledcharts.com/levels/{name}/"
    elif source == "Next Sekai":
        level_url = f"https://coconut.sonolus.com/next-sekai/levels/{name}"; beta_url = "-"
    elif source == "Sekai Best":
        level_url = f"https://sonolus.sekai.best/levels/{name}"; beta_url = "-"
    elif source == "Sbuga":
        level_url = f"https://sonolus.sbuga.com/levels/{name}"; beta_url = "-"
    elif source == "Chart Cyanvas":
        level_url = f"https://cc.milkbun.org/levels/{name}"; beta_url = "-"
    else:
        level_url = beta_url = "-"

    # UnCh: scrape page for upload date, likes, comments, leaderboard
    if source == "Untitled Charts" and level_url != "-":
        try:
            page_r = S.get(level_url, timeout=15)
            page_h = page_r.text
            dm = re.search(r'article:published_time"[^>]*content="(\d{4})-(\d{2})-(\d{2})', page_h)
            if dm: upload_date = f"{dm.group(3)}/{dm.group(2)}/{dm.group(1)}"
            lm2 = re.search(r'stat-label[^>]*>Likes</span>[\s\S]{0,100}?stat-value[^>]*>(\d+)<', page_h)
            if lm2: likes = lm2.group(1)
            cm2 = re.search(r'stat-label[^>]*>Comments</span>[\s\S]{0,100}?stat-value[^>]*>(\d+)<', page_h)
            if cm2: comments_count = cm2.group(1)
        except: pass

    return [
        level_id,
        it.get("title") or "-",
        it.get("artists") or "-",
        it.get("author") or "-",
        it.get("rating") or "-",
        difficulty,
        data.get("description") or "-",
        tags_col,
        str(staff_pick),
        privacy,
        asset(it.get("cover")),
        asset(it.get("bgm")),
        asset(it.get("preview")),
        asset(it.get("data")),
        level_url,
        likes,
        comments_count,
        upload_date,
    ]

def CUSTOM():
    print("=== CUSTOM() started ===")

    cache = {}
    if IS_GITHUB_ACTIONS:
        print("  Running on GitHub Actions: Cache ignored.")
    elif os.path.exists(CUSTOM_CACHE_FILE):
        with open(CUSTOM_CACHE_FILE, "r", encoding="utf-8") as f:
            cache = json.load(f)
        print(f"  Cache loaded: {len(cache)} entries")

    creds = Credentials.from_service_account_file(SERVICE_ACCOUNT, scopes=[
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive",
    ])
    ws = gspread.authorize(creds).open_by_key(SPREADSHEET_ID).worksheet(SHEET_NAME)

    col_a = ws.col_values(1)
    col_c = ws.col_values(3)
    ids  = col_a[1:]
    urls = col_c[1:]

    if not urls:
        print("No data in column C."); return

    # strip trailing "/" from all C cells and write back first
    cleaned_urls = [(raw or "").strip().rstrip("/") for raw in urls]
    c_write = [[v] for v in cleaned_urls]
    ws.update(range_name=f"C2:C{1+len(cleaned_urls)}", values=c_write)
    print(f"  Cleaned {sum(1 for o,n in zip(urls,cleaned_urls) if o!=n)} trailing slashes in column C")

    updates = []
    new_entries = 0

    for i, raw in enumerate(cleaned_urls):
        row = i + 2
        raw = raw.strip()
        song_id = ids[i].strip() if i < len(ids) else "-"
        if not raw:
            continue
        if not is_url(raw):
            print(f"  [{song_id}] {raw} — not a URL, writing all -")
            updates.append((row, EMPTY[:]))
            continue
        # extract server_id from URL before fetching (strip known prefixes)
        resolved = resolve_url(raw)
        m_sid = re.search(r"/levels/(?:UnCh-|coconut-next-sekai-|sekai-best-|sekai-rush-|chcy-|ptlv-)?([^/?\s]+)", resolved)
        server_id = m_sid.group(1) if m_sid else "-"
        cache_key = f"{song_id}|{server_id}"
        if not IS_GITHUB_ACTIONS and cache_key in cache:
            song_name = cache[cache_key][1] if cache[cache_key][1] != "-" else raw
            updates.append((row, cache[cache_key]))
            continue
        time.sleep(DELAY)
        values = fetch_level(raw)
        # use server_id from actual response as authoritative
        actual_server_id = values[0]
        if actual_server_id != "-":
            final_key = f"{song_id}|{actual_server_id}"
            cache[final_key] = values
            new_entries += 1
        song_name = values[1] if values[1] != "-" else raw
        print(f"  [{song_id}] {song_name}")
        updates.append((row, values))

    # batch all writes into one request to avoid 429 rate limit
    if updates:
        data_batch = []
        for row, values in updates:
            data_batch.append({
                "range": f"{SHEET_NAME}!D{row}:U{row}",
                "values": [values]
            })
        ws.spreadsheet.values_batch_update({
            "valueInputOption": "RAW",
            "data": data_batch
        })

    if not IS_GITHUB_ACTIONS:
        with open(CUSTOM_CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(cache, f, ensure_ascii=False, indent=2)
        print(f"  Cache saved ({new_entries} new entries)")
    else:
        print("  Running on GitHub Actions: Cache save skipped.")

    print(f"=== Done. {len(updates)} rows written. ===")

if __name__ == "__main__":
    import traceback
    try:
        CUSTOM()
    except Exception as e:
        print("\n" + "="*40)
        print("CRASH LOG:")
        traceback.print_exc()
        print("="*40 + "\n")
    finally:
        if not IS_GITHUB_ACTIONS:
            input("Press Enter to exit...")