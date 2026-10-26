"""
BMS Ticket Checker — CI/Headless mode for GitHub Actions.
Runs once, checks all configured watches, emails on changes.
State is persisted via a JSON artifact.

What to watch lives in trackers.json and is managed from Telegram (see chat.py).
"""

import os
import re
import sys
import json
from html import escape
from datetime import datetime, timedelta, timezone
from dataclasses import dataclass, field
from urllib.parse import urlparse
import requests

from chat import ConsoleTelegram, Telegram, buy_url, card_text, city_name, done_buttons, main_buttons, title, TIP
from district import check_district, district_url

RESEND_API_KEY = os.getenv("RESEND_API_KEY", "")
RESEND_TO_EMAIL = os.getenv("RESEND_TO_EMAIL", "")
RESEND_FROM_EMAIL = os.getenv("RESEND_FROM_EMAIL", "aviiciii@resend.dev")

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

STATE_FILE = "bms_state.json"
META_FILE = "meta.json"            # last check / last heartbeat / failure count
TRACKERS_FILE = "trackers.json"    # what to watch (managed from Telegram)
CHAT_FILE = "chat.json"            # setup-card message ids (written by the relay, read here)
HEARTBEAT_HOUR = int(os.getenv("HEARTBEAT_HOUR", "20"))   # IST: one "still watching" note a day, 8 pm
IST = timezone(timedelta(hours=5, minutes=30))

# ──────────────────────────────────────────────────────────────────────
# CONSTANTS
# ──────────────────────────────────────────────────────────────────────
AVAIL_STATUS_MAP = {
    "0": ("SOLD OUT",    "🔴"),
    "1": ("ALMOST FULL", "🟡"),
    "2": ("FILLING FAST","🟠"),
    "3": ("AVAILABLE",   "🟢"),
}

DATE_STYLE_MAP = {
    "date-selected": "BOOKABLE",
    "date-disabled": "NOT_OPEN",
    "date-default":  "AVAILABLE",
}

TIME_PERIODS = {
    "morning":   (600, 1200),
    "afternoon": (1200, 1600),
    "evening":   (1600, 1900),
    "night":     (1900, 2400),
}

REGION_MAP = {
    "chennai":    ("CHEN",   "chennai",    "13.056", "80.206", "tf3"),
    "mumbai":     ("MUMBAI", "mumbai",     "19.076", "72.878", "te7"),
    "delhi-ncr":  ("NCR",    "delhi-ncr",  "28.613", "77.209", "ttn"),
    "delhi":      ("NCR",    "delhi-ncr",  "28.613", "77.209", "ttn"),
    "bengaluru":  ("BANG",   "bengaluru",  "12.972", "77.594", "tdr"),
    "bangalore":  ("BANG",   "bengaluru",  "12.972", "77.594", "tdr"),
    "hyderabad":  ("HYD",    "hyderabad",  "17.385", "78.487", "tep"),
    "kolkata":    ("KOLK",   "kolkata",    "22.573", "88.364", "tun"),
    "pune":       ("PUNE",   "pune",       "18.520", "73.856", "te2"),
    "kochi":      ("KOCH",   "kochi",      "9.932",  "76.267", "t9z"),
}


# ─────────────────────────────────────���────────────────────────────────
# DATA
# ──────────────────────────────────────────────────────────────────────
@dataclass
class CatInfo:
    name: str
    price: str
    status: str

@dataclass
class ShowInfo:
    venue_code: str
    venue_name: str
    session_id: str
    date_code: str
    time: str
    time_code: str
    screen_attr: str
    categories: list[CatInfo] = field(default_factory=list)
    fmt: str = ""            # "3D", "IMAX 3D"... each format is its own BookMyShow event

@dataclass
class DateInfo:
    date_code: str
    status: str


# ──────────────────────────────────────────────────────────────────────
# URL PARSER + REGION RESOLVER
# ──────────────────────────────────────────────────────────────────────
def parse_bms_url(url):
    path = urlparse(url).path.strip("/")
    parts = path.split("/")
    result = {"event_code": None, "date_code": None, "region_slug": None}
    for p in parts:
        if re.match(r"^ET\d{8,}$", p):
            result["event_code"] = p
        elif re.match(r"^\d{8}$", p):
            result["date_code"] = p
    if "movies" in parts:
        idx = parts.index("movies")
        if idx + 1 < len(parts):
            result["region_slug"] = parts[idx + 1]
    return result


def resolve_region(slug):
    key = (slug or "").lower().strip()
    if key in REGION_MAP:
        return REGION_MAP[key]
    return (key.upper()[:6], key, "0", "0", "")


# ──────────────────────────────────────────────────────────────────────
# BMS API
# ──────────────────────────────────────────────────────────────────────
API_URL = (
    "https://in.bookmyshow.com/api/movies-data/v4/"
    "showtimes-by-event/primary-dynamic"
)


def fetch_bms(event_code, date_code, region_code, region_slug,
              lat, lon, geohash):
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/145.0.0.0 Safari/537.36"
        ),
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": (
            f"https://in.bookmyshow.com/movies/"
            f"{region_slug}/buytickets/{event_code}/"
        ),
        "sec-ch-ua": '"Chromium";v="145", "Not:A-Brand";v="99"',
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": '"macOS"',
        "x-app-code": "WEB",
        "x-region-code": region_code,
        "x-region-slug": region_slug,
        "x-geohash": geohash,
        "x-latitude": lat,
        "x-longitude": lon,
        "x-location-selection": "manual",
        "x-lsid": "",
    }
    params = {
        "eventCode": event_code,
        "dateCode": date_code or "",
        "isDesktop": "true",
        "regionCode": region_code,
        "xLocationShared": "false",
        "memberId": "", "lsId": "", "subCode": "",
        "lat": lat, "lon": lon,
    }
    try:
        resp = requests.get(API_URL, headers=headers,
                            params=params, timeout=15)
        if resp.status_code == 200:
            return resp.json()
        print(f"  HTTP {resp.status_code}")
    except requests.RequestException as e:
        print(f"  Request failed: {e}")
    return None


# ──────────────────────────────────────────────────────────────────────
# PARSERS
# ──────────────────────────────────────────────────────────────────────
def parse_movie_info(data):
    info = {"name": "Unknown Movie", "language": ""}
    for w in data.get("data", {}).get("topStickyWidgets", []):
        if w.get("type") == "horizontal-text-list":
            for item in w.get("data", []):
                for row in item.get("leftText", {}).get("data", []):
                    for c in row.get("components", []):
                        if "•" in c.get("text", ""):
                            info["language"] = c["text"].strip()
    bs = data.get("data", {}).get("bottomSheetData", {})
    for w in bs.get("format-selector", {}).get("widgets", []):
        if w.get("type") == "vertical-text-list":
            for d in w.get("data", []):
                if d.get("styleId") == "bottomsheet-subtitle":
                    info["name"] = d.get("text", info["name"])
    return info


def parse_formats(data, event_code):
    """[(event_code, format)] for every format of the same language as event_code.
    BookMyShow lists 2D / 3D / IMAX 3D... as separate events; a PXL show can live under 3D."""
    widgets = data.get("data", {}).get("bottomSheetData", {}).get("format-selector", {}).get("widgets", [])
    for w in widgets:
        if w.get("type") != "chip-list":
            continue
        chips = [((c.get("cta", {}).get("additionalData", {}).get("eventCode") or ""), c.get("title", ""))
                 for c in w.get("data", [])]
        if any(code == event_code for code, _ in chips):
            return [(code, title) for code, title in chips if code]
    return [(event_code, "")]


def parse_dates(data):
    dates = []
    for w in data.get("data", {}).get("topStickyWidgets", []):
        if w.get("type") != "horizontal-block-list":
            continue
        for item in w.get("data", []):
            texts = item.get("data", [])
            if len(texts) >= 3:
                style = item.get("styleId", "")
                dates.append(DateInfo(
                    date_code=item.get("id", ""),
                    status=DATE_STYLE_MAP.get(style, "UNKNOWN"),
                ))
    return dates


def parse_shows(data):
    shows = []
    for w in data.get("data", {}).get("showtimeWidgets", []):
        if w.get("type") != "groupList":
            continue
        for g in w.get("data", []):
            if g.get("type") != "venueGroup":
                continue
            for card in g.get("data", []):
                if card.get("type") != "venue-card":
                    continue
                addl = card.get("additionalData", {})
                vname = addl.get("venueName", "Unknown")
                vcode = addl.get("venueCode", "")

                for st in card.get("showtimes", []):
                    sa = st.get("additionalData", {})
                    date_code = str(
                        sa.get("showDateCode", "")
                        or sa.get("dateCode", "")
                    ).strip()
                    if not date_code and re.match(
                            r"^\d{8}", sa.get("cutOffDateTime", "")):
                        date_code = sa["cutOffDateTime"][:8]

                    show = ShowInfo(
                        venue_code=vcode,
                        venue_name=vname,
                        session_id=sa.get("sessionId", ""),
                        date_code=date_code,
                        time=st.get("title", ""),
                        time_code=sa.get("showTimeCode", ""),
                        screen_attr=(st.get("screenAttr", "")
                                     or sa.get("attributes", "")),
                    )
                    for cat in sa.get("categories", []):
                        ca = str(cat.get("availStatus", ""))
                        lbl, _ = AVAIL_STATUS_MAP.get(ca, ("UNKNOWN", ""))
                        show.categories.append(CatInfo(
                            name=cat.get("priceDesc", ""),
                            price=cat.get("curPrice", "0"),
                            status=ca,
                        ))
                    shows.append(show)
    return shows


# ──────────────────────────────────────────────────────────────────────
# FILTERING
# ──────────────────────────────────────────────────────────────────────
def filter_shows(shows, theatre_filter, time_periods, date_codes, formats=()):
    """Theatre keywords match the theatre name (or screen/format, for older trackers). Each format
    filter ("3d pxl") needs all its words in the show's format + screen. Filters combine with AND."""
    result = []
    kws = [k.strip().lower() for k in theatre_filter.split(",")
           if k.strip()] if theatre_filter else []
    periods = [p.strip().lower() for p in time_periods.split(",")
               if p.strip()] if time_periods else []
    dates_set = set(d.strip() for d in date_codes.split(",")
                    if d.strip()) if date_codes else set()

    for s in shows:
        # Theatre filter
        if kws:
            name_lower = f"{s.venue_name} {s.screen_attr} {s.fmt}".lower()
            if not any(k in name_lower for k in kws):
                continue

        # Format filter (2D / 3D / IMAX 3D / PXL / 3D PXL ...)
        if formats:
            have = f"{s.fmt} {s.screen_attr}".lower().split()
            if not any(all(w in have for w in f.split()) for f in formats):
                continue

        # Date filter
        if dates_set and s.date_code and s.date_code not in dates_set:
            continue

        # Time period filter
        if periods:
            try:
                tc = int(s.time_code)
            except ValueError:
                tc = 0
            matched = False
            for p in periods:
                if p in TIME_PERIODS or re.fullmatch(r"\d{4}-\d{4}", p):
                    lo, hi = TIME_PERIODS.get(p) or map(int, p.split("-"))   # "1900-2000" = 7-8 pm
                    if lo <= tc < hi:
                        matched = True
                        break
            if not matched:
                continue

        result.append(s)
    return result


# ──────────────────────────────────────────────────────────────────────
# STATE (for change detection between runs)
# ──────────────────────────────────────────────────────────────────────
def load_state():
    try:
        with open(STATE_FILE) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_state(state):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)


def build_state(shows, dates):
    """Build a comparable state dict."""
    show_state = {}
    for s in shows:
        for c in s.categories:
            key = f"{s.venue_code}|{s.session_id}|{s.date_code}|{c.name}"
            show_state[key] = {
                "venue": s.venue_name,
                "time": s.time,
                "fmt": " ".join(x for x in (s.fmt, s.screen_attr) if x),
                "date": s.date_code,
                "cat": c.name,
                "price": c.price,
                "status": c.status,
            }

    date_state = {
        d.date_code: d.status for d in dates
    }

    return {"shows": show_state, "dates": date_state}


def detect_changes(old_state, new_state):
    changes = []

    # New dates opening
    old_dates = old_state.get("dates", {})
    new_dates = new_state.get("dates", {})
    for dc, status in new_dates.items():
        old_status = old_dates.get(dc)
        if (old_status == "NOT_OPEN"
                and status in ("BOOKABLE", "AVAILABLE")):
            changes.append(f"📅 NEW DATE OPENED: {dc}")

    old_shows = old_state.get("shows", {})
    new_shows = new_state.get("shows", {})

    # New showtimes
    for key in set(new_shows) - set(old_shows):
        s = new_shows[key]
        fmt = f" {s['fmt']}" if s.get("fmt") else ""
        changes.append(
            f"🆕 NEW: {s['venue']} {s['time']}{fmt} [{s['date']}] "
            f"— {s['cat']} ₹{s['price']}"
        )

    # Sold out → available
    for key, new_s in new_shows.items():
        old_s = old_shows.get(key)
        if old_s and old_s["status"] == "0" and new_s["status"] != "0":
            lbl, ico = AVAIL_STATUS_MAP.get(
                new_s["status"], ("UNKNOWN", "⚪")
            )
            changes.append(
                f"{ico} BACK: {new_s['venue']} {new_s['time']} "
                f"[{new_s['date']}] — {new_s['cat']} → {lbl}"
            )

    return changes


# ──────────────────────────────────────────────────────────────────────
# EMAIL NOTIFICATION (Resend)
# ──────────────────────────────────────────────────────────────────────
def _cat_status_label(status):
    return AVAIL_STATUS_MAP.get(status, ("UNKNOWN", ""))[0]


def send_email(subject, changes, shows, movie_info):
    api_key = RESEND_API_KEY.strip()
    to = RESEND_TO_EMAIL.strip()
    frm = RESEND_FROM_EMAIL.strip() or "onboarding@resend.dev"

    if not api_key or not to:
        print("  ⚠️  Skipping email — RESEND_API_KEY or RESEND_TO_EMAIL not set.")
        return

    now_str = datetime.now().strftime("%d %b %Y, %I:%M %p")
    movie_name = movie_info.get("name", "Movie")

    # Build changes HTML
    changes_html = ""
    if changes:
        rows = "".join(
            f'<li style="padding:3px 0;font-size:14px;">{escape(c)}</li>'
            for c in changes
        )
        changes_html = f"""
        <h3 style="margin:0 0 8px 0;font-size:15px;font-weight:bold;color:#333;">
            Changes Detected
        </h3>
        <ul style="margin:0 0 20px 0;padding-left:20px;line-height:1.6;color:#333;">
            {rows}
        </ul>"""

    # Build shows section grouped by venue
    venue_groups = {}
    for s in shows:
        venue_groups.setdefault(s.venue_name, []).append(s)

    shows_html = ""
    for vname, vshows in venue_groups.items():
        show_rows = ""
        for s in vshows:
            cats = " | ".join(
                f"{escape(c.name)} Rs.{escape(c.price)} ({_cat_status_label(c.status)})"
                for c in s.categories
            )
            fmt = f" [{escape(s.screen_attr)}]" if s.screen_attr else ""
            show_rows += (
                f'<tr>'
                f'<td style="padding:5px 8px;border-bottom:1px solid #ddd;'
                f'font-size:13px;vertical-align:top;">'
                f'{escape(s.time)}{fmt}</td>'
                f'<td style="padding:5px 8px;border-bottom:1px solid #ddd;'
                f'font-size:13px;vertical-align:top;">'
                f'{cats}</td>'
                f'</tr>'
            )

        shows_html += f"""
        <p style="margin:14px 0 4px 0;font-size:14px;font-weight:bold;color:#333;">
            {escape(vname)}
        </p>
        <table style="width:100%;border-collapse:collapse;font-size:13px;">
            <tr style="background:#f5f5f5;">
                <th style="padding:5px 8px;text-align:left;border-bottom:1px solid #ddd;
                           font-weight:bold;">Time</th>
                <th style="padding:5px 8px;text-align:left;border-bottom:1px solid #ddd;
                           font-weight:bold;">Categories</th>
            </tr>
            {show_rows}
        </table>"""

    html = f"""<!doctype html>
<html>
<head><meta charset="utf-8"></head>
<body style="margin:0;padding:24px;font-family:Arial,Helvetica,sans-serif;
             font-size:14px;color:#333;background:#fff;">
    <h2 style="margin:0 0 4px 0;font-size:18px;color:#111;">
        BMS Alert: {escape(movie_name)}
    </h2>
    <p style="margin:0 0 20px 0;font-size:13px;color:#666;">
        {escape(now_str)}
    </p>
    <hr style="border:none;border-top:1px solid #ddd;margin:0 0 20px 0;">
    {changes_html}
    <h3 style="margin:0 0 8px 0;font-size:15px;font-weight:bold;color:#333;">
        Current Showtimes
    </h3>
    {shows_html}
    <p style="margin:24px 0 0 0;font-size:12px;color:#999;">
        This is an automated alert from BMS Ticket Notifier.
    </p>
</body>
</html>"""

    # Build plain-text version with full show details
    plain_lines = [subject, "", f"Checked at: {now_str}", ""]
    if changes:
        plain_lines.append("Changes Detected:")
        plain_lines.extend(f"  - {c}" for c in changes)
        plain_lines.append("")
    plain_lines.append("Current Showtimes:")
    for vname, vshows in venue_groups.items():
        plain_lines.append(f"\n{vname}")
        for s in vshows:
            cats = " | ".join(
                f"{c.name} Rs.{c.price} ({_cat_status_label(c.status)})"
                for c in s.categories
            )
            fmt = f" [{s.screen_attr}]" if s.screen_attr else ""
            plain_lines.append(f"  {s.time}{fmt} - {cats}")
    plain_lines.extend(["", "This is an automated alert from BMS Ticket Notifier."])
    plain = "\n".join(plain_lines)

    try:
        resp = requests.post(
            "https://api.resend.com/emails",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={
                "from": frm, "to": [to],
                "subject": subject,
                "text": plain, "html": html,
            },
            timeout=15,
        )
        if resp.status_code in (200, 201):
            print(f"  ✅ Email sent to {to}")
        else:
            print(f"  ❌ Resend {resp.status_code}: {resp.text}")
            return
    except requests.RequestException as e:
        print(f"  ❌ Email failed: {e}")
        return




# ──────────────────────────────────────────────────────────────────────
# RUN: answer chat messages, check every tracker, send alerts + the daily note
# ──────────────────────────────────────────────────────────────────────
def load_json(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def write_json_if_changed(path, data):
    """Only touch the file when content changed, so the bot doesn't commit every run."""
    new = json.dumps(data, indent=2, ensure_ascii=False) + "\n"
    try:
        with open(path, encoding="utf-8") as f:
            if f.read() == new:
                return
    except FileNotFoundError:
        pass
    with open(path, "w", encoding="utf-8") as f:
        f.write(new)


def make_telegram():
    token, chat = os.getenv("TELEGRAM_BOT_TOKEN", "").strip(), os.getenv("TELEGRAM_CHAT_ID", "").strip()
    if token and chat:
        return Telegram(token, chat)
    print("  ⚠️  TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID not set - printing instead.")
    return ConsoleTelegram()


def check_bms(t, old, fetch=fetch_bms):
    """One BookMyShow tracker -> dict(ok, result, state, changes, shows, movie_info)."""
    region_code, region_slug, lat, lon, geohash = resolve_region(t["region_slug"])
    all_shows, all_dates, got = [], [], False
    movie_info = {"name": "Unknown", "language": ""}
    formats = None
    for dc in (t.get("dates") or [""]):
        first = fetch(t["event_code"], dc, region_code, region_slug, lat, lon, geohash)
        if first and formats is None:
            formats = parse_formats(first, t["event_code"])
        for code, fmt in formats or [(t["event_code"], "")]:
            data = first if code == t["event_code"] else \
                fetch(code, dc, region_code, region_slug, lat, lon, geohash)
            if not data:
                continue
            got = True
            if movie_info["name"] == "Unknown":
                movie_info = parse_movie_info(data)
            if code == t["event_code"]:
                all_dates.extend(parse_dates(data))
            for show in parse_shows(data):
                show.fmt = fmt
                all_shows.append(show)
    seen = {}
    for d in all_dates:
        seen.setdefault(d.date_code, d.status)
    catalog = {"name": "" if movie_info["name"] in ("Unknown", "Unknown Movie") else movie_info["name"],
               "language": movie_info["language"], "dates": [[k, v] for k, v in seen.items()],
               "venues": sorted({s.venue_name for s in all_shows}),
               "formats": [f for _, f in formats or [] if f],
               "screens": sorted({s.screen_attr for s in all_shows if s.screen_attr})} if got else None
    if not all_shows:
        return {"ok": got, "result": "no showtimes listed yet" if got else "BookMyShow gave no data",
                "state": None, "changes": [], "shows": [], "movie_info": movie_info, "catalog": catalog}

    filtered = filter_shows(all_shows, ",".join(t.get("theatres", [])), ",".join(t.get("times", [])),
                            ",".join(t.get("dates", [])), t.get("formats", []))
    new_state = build_state(filtered, all_dates)
    changes = []
    if old.get("shows"):
        changes = detect_changes(old, new_state)
    elif new_state["shows"]:
        theatres = sorted({v["venue"] for v in new_state["shows"].values()})
        slots = sorted({(v["date"], v["venue"], v["time"], v.get("fmt", ""))
                        for v in new_state["shows"].values()})
        changes = [f"🎟️ BOOKINGS OPEN: {len(slots)} matching show(s) at {len(theatres)} theatre(s)"] + \
            [f"   {d[6:8]}/{d[4:6]} {tm} · {v}" + (f" · {f}" if f else "") for d, v, tm, f in slots[:10]]
    return {"ok": True, "result": f"{len(filtered)} matching showtimes", "state": new_state,
            "changes": changes, "shows": filtered, "movie_info": movie_info, "catalog": catalog}


def refresh_cards(tg, trackers, meta, cards):
    """Re-render each setup card with the latest status; edit only when the text changed."""
    hashes = meta.setdefault("card_hash", {})
    for t in trackers:
        card = cards.get(t["id"]) or {}
        if not card.get("id") or t.get("source") == "district":
            continue
        done = card.get("done")
        text = card_text(t, meta.get("catalog", {}).get(t["id"]), meta["results"].get(t["id"]),
                         "✅ Saved. /list shows everything I'm tracking." if done else TIP)
        if hashes.get(t["id"]) != text and tg.edit(card["id"], text,
                                                   done_buttons(t["id"]) if done else main_buttons(t["id"])):
            hashes[t["id"]] = text
    for tid in [k for k in hashes if not any(t["id"] == k for t in trackers)]:
        hashes.pop(tid)


def run(now=None, tg=None, fetch=fetch_bms, district_check=check_district, only=None):
    """only: a tracker id the relay asked to check right away (skips the others and the daily note)."""
    now = now or datetime.now(IST)
    only = only if only is not None else os.getenv("ONLY_TRACKER", "").strip()
    print(f"[{now:%Y-%m-%d %H:%M:%S} IST] BMS Ticket Checker" + (f" (only {only})" if only else ""))
    trackers = load_json(TRACKERS_FILE, [])
    meta = load_json(META_FILE, {})
    state = load_state()
    if "shows" in state:                      # old single-movie format
        state = {}
    tg = tg or make_telegram()

    # Chat (links, buttons, commands) is answered instantly by the relay (relay.py on Vercel),
    # which owns trackers.json/chat.json. This run only reads them.
    meta.pop("tg_offset", None)

    # 1. checks
    results = meta.setdefault("results", {})
    catalog = meta.setdefault("catalog", {})
    bms_ran = bms_ok = False
    for tid in [k for k in state if not any(t["id"] == k for t in trackers)]:
        state.pop(tid)                        # tracker removed in chat
    for t in trackers:
        if not t.get("enabled", True) or (only and t["id"] != only):
            continue
        epoch = t.get("epoch", 0)
        if state.get(t["id"], {}).get("_epoch", 0) != epoch:
            state.pop(t["id"], None)          # resumed in chat: fresh start, alert again if open
        if t.get("source") == "district":
            ok, result, is_open = district_check(t)
            was_open = state.get(t["id"], {}).get("open", False)
            if ok:
                state[t["id"]] = {"open": is_open, "_epoch": epoch}
            if is_open and not was_open:
                tg.send(f"🎟️ BOOKINGS OPEN on District: {t['label']} ({t['city']})\n{result}\n"
                        f"{district_url(t)}")
        else:
            bms_ran = True
            r = check_bms(t, state.get(t["id"], {}), fetch)
            ok, result = r["ok"], r["result"]
            bms_ok = bms_ok or ok
            if r["state"] is not None:
                state[t["id"]] = {**r["state"], "_epoch": epoch}
            if r["catalog"] is not None:
                catalog[t["id"]] = r["catalog"]
            if r["changes"]:
                subject = (f"BMS Alert: {title(t, catalog.get(t['id']))} ({city_name(t['region_slug'])})"
                           f" - {len(r['changes'])} change(s)")
                lines = [subject, ""] + r["changes"][:15] + ["", buy_url(t)]
                tg.send("\n".join(lines))
                send_email(subject, r["changes"], r["shows"], r["movie_info"])
        results[t["id"]] = result
        print(f"  {t['id']}: {result}")
    for d in (results, catalog):
        for tid in [k for k in d if not any(t["id"] == k for t in trackers)]:
            d.pop(tid)

    # 2. keep each setup card in Telegram current (booking status, validated dates/theatres)
    refresh_cards(tg, trackers, meta, load_json(CHAT_FILE, {}).get("cards", {}))

    # 3. one-time warning if BookMyShow keeps returning nothing; one "still watching" note a day
    if bms_ran and not only:
        meta["fails"] = 0 if bms_ok else meta.get("fails", 0) + 1
        if meta["fails"] == 3:
            tg.send("⚠️ Can't read BookMyShow: 3 checks in a row got no data. "
                    "It may be blocking GitHub, so check manually.")
    if not only and now.hour >= HEARTBEAT_HOUR and meta.get("last_heartbeat") != now.date().isoformat():
        active = [t for t in trackers if t.get("enabled", True)]
        if active:
            lines = [f"{i}. {title(t, catalog.get(t['id']))}: {results.get(t['id'], 'not checked yet')}"
                     for i, t in enumerate(active, 1)]
            tg.send(f"🫡 Still watching {len(active)} tracker(s), checking every 10 minutes:\n"
                    + "\n".join(lines))
        meta["last_heartbeat"] = now.date().isoformat()

    write_json_if_changed(META_FILE, meta)
    write_json_if_changed(STATE_FILE, state)
    print("  Done.")


if __name__ == "__main__":
    run()
