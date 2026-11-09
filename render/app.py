import io
import os
import json
import tempfile
import threading
import importlib
from pathlib import Path

import requests
from flask import Flask, request, jsonify

from rg_converters import detect
from rg_converters.format import sus
from rg_converters.format.sonolus_leveldata import next_sekai
import sekaiworld.scores.render as render_module

SECRET = os.environ["RENDER_SECRET"]        # same value as the Worker's RENDER_SECRET
APP_ID = os.environ["DISCORD_APP_ID"]       # 1456950637248905392
MAX_BYTES = 9_500_000                       # stay under Discord's upload limit

# ---- label patch: "Chart drawn by sbuga.com" -> "drawn by XX! bot" ----
OLD_LABEL = "Chart drawn by sbuga.com"
NEW_LABEL = "drawn by XX! bot"
pkg_dir = Path(render_module.__file__).parent
for f in pkg_dir.rglob("*.py"):
    try:
        s = f.read_text(encoding="utf-8")
    except Exception:
        continue
    if OLD_LABEL in s:
        f.write_text(s.replace(OLD_LABEL, NEW_LABEL), encoding="utf-8")
        print("patched label in", f)
render_module = importlib.reload(render_module)
ChartRenderer = render_module.ChartRenderer
load_sus = render_module.load_sus

app = Flask(__name__)
render_lock = threading.Lock()   # one render at a time (memory)


def download(url, path):
    r = requests.get(url, timeout=60)
    r.raise_for_status()
    path.write_bytes(r.content)


def encode_png(img):
    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    scale = 1.0
    while buf.tell() > MAX_BYTES and scale > 0.3:
        scale *= 0.8
        small = img.resize((int(img.width * scale), int(img.height * scale)))
        buf = io.BytesIO()
        small.save(buf, format="PNG", optimize=True)
    return buf.getvalue()


def render_chart(chart_url, title, jacket_url, difficulty):
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        level_path = tmp / "chart.dat"
        jacket_path = tmp / "jacket.png"
        sus_path = tmp / "chart.sus"

        download(chart_url, level_path)
        raw = level_path.read_bytes()
        formats = detect(raw)
        if "next_sekai" not in formats:
            raise ValueError(f"Unexpected chart format: {formats}")

        score = next_sekai.load(
            raw, next_sekai.ImportOptions(error_on_unsupported_feature=False)
        )
        sus_text, _ = sus.dumps(score, sus.ExportOptions(allow_layers=True))
        sus_path.write_text(sus_text, encoding="utf-8")

        kwargs = {}
        if jacket_url:
            download(jacket_url, jacket_path)
            kwargs["jacket"] = str(jacket_path)

        render_score, bar_lengths = load_sus(str(sus_path))
        renderer = ChartRenderer(
            render_score,
            title=title,
            difficulty=difficulty.lower(),
            bar_lengths=bar_lengths,
            **kwargs,
        )
        return encode_png(renderer.render())


def job(p):
    hook = f"https://discord.com/api/v10/webhooks/{APP_ID}/{p['token']}/messages/@original"
    try:
        with render_lock:
            png = render_chart(
                p["chart_url"],
                p["title"],
                p.get("jacket_url"),
                p.get("difficulty") or "MASTER"
            )

        embed = {
            "title": p.get("embed_title") or p["title"],
            "color": p.get("embed_color", 0x808080),
            "image": {"url": "attachment://chart.png"}
        }

        if p.get("embed_footer") or p.get("charter"):
            embed["footer"] = {
                "text": p.get("embed_footer") or p.get("charter")
            }

        payload = {
            "embeds": [embed],
            "attachments": [{"id": 0, "filename": "chart.png"}]
        }

        r = requests.patch(
            hook,
            data={"payload_json": json.dumps(payload)},
            files={"files[0]": ("chart.png", png, "image/png")},
            timeout=90,
        )
        r.raise_for_status()

    except Exception as e:
        requests.patch(
            hook,
            json={"content": f"Render failed: {e}"[:1900]},
            timeout=30
        )


def handle_render():
    if request.headers.get("authorization") != f"Bearer {SECRET}":
        return jsonify(error="unauthorized"), 401
    threading.Thread(target=job, args=(request.get_json(),), daemon=True).start()
    return jsonify(ok=True), 202


@app.route("/render", methods=["POST"])
def render():
    return handle_render()


# also accept POST on "/" so a RENDER_URL without "/render" still works
@app.route("/", methods=["GET", "POST"])
def root():
    if request.method == "POST":
        return handle_render()
    return jsonify(ok=True)