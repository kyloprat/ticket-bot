"""Telegram conversation: share a BookMyShow link, tune what to watch, manage trackers.

Send the bot a BookMyShow movie link (details like "6, 7 Nov 3D PXL Mani Square evening" can
come in the same message or the next one). It asks the city if it can't tell, then shows one
"setup card" that it keeps editing: live booking status (filled in by the checker) and buttons
for dates, theatres, format, time and city. Nothing is watched until you tap Done.

Several people can use the bot: each has their own trackers. The owner (TELEGRAM_CHAT_ID)
approves new people with one tap.
Commands: /list  /pause N  /resume N  /remove N  /edit N  /help  (owner: /users, /revoke ID)
"""
from __future__ import annotations

import calendar
import json
import os
import re
from datetime import date, datetime, timedelta, timezone

import requests

from district import parse_district_link

PERIODS = ["morning", "afternoon", "evening", "night"]
CITIES = ["bengaluru", "kolkata", "mumbai", "delhi-ncr", "chennai", "hyderabad", "pune", "kochi"]
CITY_ALIASES = {"bengaluru": "bengaluru", "bangalore": "bengaluru", "blr": "bengaluru", "kolkata": "kolkata",
                "calcutta": "kolkata", "mumbai": "mumbai", "bombay": "mumbai", "delhi": "delhi-ncr",
                "ncr": "delhi-ncr", "gurgaon": "delhi-ncr", "noida": "delhi-ncr", "chennai": "chennai",
                "madras": "chennai", "hyderabad": "hyderabad", "pune": "pune", "kochi": "kochi", "cochin": "kochi"}
HOME_CITY = "bengaluru"
OPEN_STATUSES = ("BOOKABLE", "AVAILABLE")
BMS_LINK = re.compile(r"https?://(?:www\.)?in\.bookmyshow\.com/movies/\S+", re.I)
EVENT_CODE = re.compile(r"ET\d{8,}", re.I)
SHARE_TITLE = re.compile(r"about the movie\s+(.+?)\s+https?://", re.I | re.S)
SHARE_TEXT = re.compile(r"all that you would like to explore and know about the movie\s+.+?(?=https?://)|@bookmyshow",
                        re.I | re.S)
IST = timezone(timedelta(hours=5, minutes=30))
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36")

MONTHS = {m.lower(): i for i, m in enumerate(calendar.month_name) if m}
MONTHS.update({m.lower(): i for i, m in enumerate(calendar.month_abbr) if m})
MONTHS["sept"] = 9
WEEKDAYS = {d.lower(): i for i, d in enumerate(calendar.day_name)}
WEEKDAYS.update({d.lower(): i for i, d in enumerate(calendar.day_abbr)})
WEEKDAYS.update({"tues": 1, "wednes": 2, "thur": 3, "thurs": 3})

HELP = (
    "Send me a BookMyShow movie link and I'll watch it for you.\n"
    "Add details in the same message or the next one, e.g. 6, 7 Nov 3D PXL Mani Square evening\n"
    "I show a card with the live booking status; adjust it with the buttons and tap ✅ Done "
    "to start watching.\n\n"
    "/list - what I'm watching for you\n"
    "/edit N - change dates, theatres, format, time or city\n"
    "/pause N, /resume N, /remove N\n"
    "N is the number shown in /list. Checks run every ~10 minutes."
)
TIP = "Tip: type it in one line, e.g. 6, 7 Nov 7-8 pm 3D PXL Mani Square"
NOT_YET = "👉 Not watching yet. Tap ✅ Done when this looks right."
SAVED = "✅ Watching. /list shows everything."

# Screens and formats BookMyShow uses; a typed run of these becomes one format filter ("3D PXL").
FORMAT_WORDS = ["2d", "3d", "imax", "pxl", "4dx", "screenx", "screen x", "insignia", "dolby", "atmos", "ice",
                "mx4d", "epiq", "onyx", "laser", "playhouse", "luxe", "lux", "director's cut", "gold"]
FORMAT_RUN = re.compile(r"\b(?:" + "|".join(re.escape(w) for w in FORMAT_WORDS) + r")(?:\s+(?:"
                        + "|".join(re.escape(w) for w in FORMAT_WORDS) + r"))*\b", re.I)
DEFAULT_FORMATS = ["2D", "3D", "IMAX", "IMAX 3D", "PXL", "3D PXL", "4DX", "Insignia"]


def _load_cinemas() -> dict:
    """cinemas.json (made by cinemas.py): every cinema per city, to check typed theatre names."""
    try:
        with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "cinemas.json"), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


CINEMAS = _load_cinemas()


def cinema_matches(city: str, keyword: str) -> list[str]:
    return [c for c in CINEMAS.get(city, []) if keyword.lower() in c.lower()]


# ── links ───────────────────────────────────────────────────────────────
def parse_link(text: str) -> dict | None:
    m = BMS_LINK.search(text or "")
    if not m:
        return None
    url = m.group(0).rstrip(").,>")
    path = [p for p in url.split("?")[0].split("#")[0].split("/")[3:] if p]   # movies/<city>/...
    code = next((p for p in path if EVENT_CODE.fullmatch(p)), None)
    if not code or len(path) < 3:
        return None
    slug = next((p for p in path[2:] if p.lower() != "buytickets" and not EVENT_CODE.fullmatch(p)), "")
    return {"event_code": code.upper(), "region_slug": path[1].lower(), "slug": slug.lower()}


def link_extras(text: str) -> str:
    """What the person typed around a link, without the link and BookMyShow's share text."""
    return BMS_LINK.sub(" ", SHARE_TEXT.sub(" ", text or "")).strip()


def buy_url(t: dict) -> str:
    return (f"https://in.bookmyshow.com/movies/{t['region_slug']}/{t['slug'] or 'movie'}"
            f"/buytickets/{t['event_code']}/")


def page_url(t: dict) -> str:
    """Works before bookings open; /buytickets/ shows 'no shows' until then."""
    return f"https://in.bookmyshow.com/movies/{t['region_slug']}/{t['slug'] or 'movie'}/{t['event_code']}"


def slug_label(slug: str) -> str:
    return " ".join(w.capitalize() for w in slug.split("-") if w) or "Movie"


def share_title(text: str) -> str:
    """Movie name from BookMyShow's share text ("...about the movie X - (Hindi) https://...")."""
    m = SHARE_TITLE.search(text or "")
    return re.sub(r"\s+", " ", m.group(1)).strip() if m else ""


def fetch_label(link: dict) -> str:
    """Movie name from the page title, falling back to the URL slug."""
    t = {"region_slug": link["region_slug"], "slug": link["slug"], "event_code": link["event_code"]}
    try:
        r = requests.get(page_url(t), headers={"User-Agent": UA, "Accept-Language": "en-IN,en;q=0.9"},
                         timeout=8)
        title = re.search(r"<title>([^<]+)</title>", r.text)
        if r.ok and title:
            name = re.split(r"\s+\(\d{4}\)|\s+-\s+Movie|\s+Movie\b|\s+\|", title.group(1))[0].strip()
            name = name.replace("&amp;", "&").replace("&#x27;", "'").replace("&#39;", "'")
            if name:
                return name
    except requests.RequestException:
        pass
    return slug_label(link["slug"])


def city_name(slug: str) -> str:
    return "Delhi NCR" if slug == "delhi-ncr" else slug.replace("-", " ").title()


# ── dates ───────────────────────────────────────────────────────────────
def _make(y: int | None, mo: int, d: int, today: date, part: str) -> str:
    years = [y] if y else [today.year, today.year + 1]
    for yr in years:
        try:
            dt = date(yr, mo, d)
        except ValueError:
            continue
        if y or dt >= today:
            return dt.strftime("%Y%m%d")
    raise ValueError(f"Couldn't read '{part.strip()}' as a date")


_D = r"\d{1,2}(?:st|nd|rd|th)?"
_SEP = r"\s*(?:,|&|\band\b|-|–|\bto\b)\s*|\s+"
_MON = "(?:" + "|".join(sorted(MONTHS, key=len, reverse=True)) + r")\b\.?"
_WD = "(?:" + "|".join(sorted(WEEKDAYS, key=len, reverse=True)) + r")(?:day)?\b"
DATE_PATTERNS = [
    ("iso", re.compile(r"\b(\d{4})-(\d{1,2})-(\d{1,2})\b")),
    ("ymd8", re.compile(r"\b(20\d{6})\b")),
    ("dmy", re.compile(r"\b(\d{1,2})[/.-](\d{1,2})[/.-](\d{2}|\d{4})\b")),
    ("dm", re.compile(r"\b(\d{1,2})/(\d{1,2})\b")),
    ("days_mon", re.compile(rf"\b({_D}(?:(?:{_SEP}){_D})*)\s*({_MON})(?:\s+(\d{{4}}))?", re.I)),
    ("mon_days", re.compile(rf"\b({_MON})\s*({_D}(?:\s*(?:,|&|\band\b|-|–|\bto\b)\s*{_D})*)\b(?:\s+(\d{{4}}))?", re.I)),
    ("rel", re.compile(r"\b(today|tonight|tomorrow|tmrw|day after tomorrow|this weekend|weekend)\b", re.I)),
    ("wd", re.compile(rf"\b(?:(this|next)\s+)?({_WD})", re.I)),
]


def _day_list(spec: str, mo: int, y: int | None, today: date, part: str) -> list[str]:
    """'6, 7' / '6-8' / '6th and 9th' in month mo -> date codes."""
    out: list[str] = []
    tokens = re.findall(r"\d{1,2}|-|–|\bto\b", spec)
    i = 0
    while i < len(tokens):
        lo = int(tokens[i])
        if i + 2 < len(tokens) and tokens[i + 1] in ("-", "–", "to") and tokens[i + 2].isdigit():
            hi = int(tokens[i + 2])
            if hi < lo or hi - lo > 14:
                raise ValueError(f"Couldn't read '{part.strip()}' as a date range (max 15 days)")
            out += [_make(y, mo, d, today, part) for d in range(lo, hi + 1)]
            i += 3
        else:
            if tokens[i].isdigit():
                out.append(_make(y, mo, lo, today, part))
            i += 1
    return out


def _weekday(name: str, which: str | None, today: date) -> date:
    """'fri' / 'this sat' -> the coming one (today counts); 'next fri' -> the one after if that is today."""
    key = name.lower()
    key = key if key in WEEKDAYS else key[:-3]          # "friday" -> "fri"
    ahead = (WEEKDAYS[key] - today.weekday()) % 7
    if which == "next" and ahead == 0:
        ahead = 7
    return today + timedelta(days=ahead)


def take_dates(text: str, today: date) -> tuple[list[str], str]:
    """Pull every date phrase out of free text -> (date codes, text without them). Raises ValueError."""
    out: list[str] = []
    for kind, rx in DATE_PATTERNS:
        def repl(m, kind=kind):
            g = m.groups()
            part = m.group(0)
            if kind == "iso":
                out.append(_make(int(g[0]), int(g[1]), int(g[2]), today, part))
            elif kind == "ymd8":
                out.append(g[0])
            elif kind in ("dmy", "dm"):
                y = (int(g[2]) + (2000 if len(g[2]) == 2 else 0)) if kind == "dmy" else None
                if not 1 <= int(g[1]) <= 12:
                    raise ValueError(f"Couldn't read '{part}' as a date (day/month)")
                out.append(_make(y, int(g[1]), int(g[0]), today, part))
            elif kind == "days_mon":
                out.extend(_day_list(g[0], MONTHS[g[1].lower().rstrip(".")], int(g[2]) if g[2] else None, today, part))
            elif kind == "mon_days":
                out.extend(_day_list(g[1], MONTHS[g[0].lower().rstrip(".")], int(g[2]) if g[2] else None, today, part))
            elif kind == "rel":
                w = g[0].lower()
                if w in ("today", "tonight"):
                    out.append(today.strftime("%Y%m%d"))
                elif w in ("tomorrow", "tmrw"):
                    out.append((today + timedelta(days=1)).strftime("%Y%m%d"))
                elif w == "day after tomorrow":
                    out.append((today + timedelta(days=2)).strftime("%Y%m%d"))
                else:
                    sat = today + timedelta(days=(5 - today.weekday()) % 7)
                    out.extend([sat.strftime("%Y%m%d"), (sat + timedelta(days=1)).strftime("%Y%m%d")])
            else:
                out.append(_weekday(g[1], g[0] and g[0].lower(), today).strftime("%Y%m%d"))
            return " "
        text = rx.sub(repl, text)
    return sorted(set(out)), text


def bare_days(text: str, today: date, hint: list[str] | None = None) -> list[str]:
    """'7 and 8' with no month (when asked for dates): the month BookMyShow lists, else this month
    if those days are still ahead, else next month."""
    if not re.fullmatch(rf"\s*{_D}(?:(?:{_SEP}){_D})*\s*", text, re.I):
        return []
    nxt = today.replace(day=1) + timedelta(days=32)
    months = sorted({(int(d[:4]), int(d[4:6])) for d in hint or []}) + [(today.year, today.month),
                                                                      (nxt.year, nxt.month)]
    for y, mo in months:
        try:
            days = _day_list(text, mo, y, today, text)
        except ValueError:
            continue
        if all(d >= today.strftime("%Y%m%d") for d in days):
            return days
    raise ValueError(f"Couldn't read '{text.strip()}' as dates")


def parse_dates(text: str, today: date) -> list[str]:
    """Only dates allowed: '18-20 Dec, 25 Dec' -> codes. Raises ValueError on anything else."""
    codes, rest = take_dates(text, today)
    if re.sub(r"[\s,;&]|\band\b", "", rest):
        raise ValueError(f"Couldn't read '{text.strip()}' as a date")
    return codes


def fmt_date(code: str) -> str:
    return date(int(code[:4]), int(code[4:6]), int(code[6:8])).strftime("%d %b %Y").lstrip("0")


def short_date(code: str) -> str:
    return date(int(code[:4]), int(code[4:6]), int(code[6:8])).strftime("%a %d %b").replace(" 0", " ")


# ── times ───────────────────────────────────────────────────────────────
_T = r"(\d{1,2})(?:[:.](\d{2}))?\s*(am|pm)?"
TIME_RANGE = re.compile(rf"\b{_T}\s*(?:-|–|to)\s*{_T}", re.I)
TIME_AFTER = re.compile(rf"\b(after|from|before|till|until)\s+{_T}", re.I)
TIME_ONE = re.compile(r"\b(\d{1,2})(?:[:.](\d{2}))?\s*(am|pm)\b", re.I)


def _hhmm(h, m, ap) -> int:
    h = int(h) % 12 + (12 if (ap or "").lower() == "pm" else 0)
    return h * 100 + int(m or 0)


def fmt_clock(v: int) -> str:
    if v >= 2400:
        return "midnight"
    h, m = divmod(v, 100)
    ap = "am" if h < 12 else "pm"
    h = h % 12 or 12
    return f"{h}:{m:02d} {ap}" if m else f"{h} {ap}"


def fmt_time(entry: str) -> str:
    """'evening' -> 'Evening'; '1900-2000' -> '7-8 pm'."""
    if entry in PERIODS:
        return entry.capitalize()
    lo, hi = (int(x) for x in entry.split("-"))
    a, b = fmt_clock(lo), fmt_clock(hi)
    if a.endswith(b[-2:]) and b != "midnight":
        a = a[:-3]
    return f"{a}-{b}"


def _take_times(text: str) -> tuple[list[str], str]:
    """Pull time ranges/periods out of free text -> (entries, text without them)."""
    out: list[str] = []

    def rng(m):
        h1, m1, ap1, h2, m2, ap2 = m.groups()
        if not (ap1 or ap2):
            return m.group(0)                       # "18-20" is a date range, not a time
        hi = _hhmm(h2, m2, ap2 or ap1)
        lo = _hhmm(h1, m1, ap1 or ap2)
        if not ap1 and lo > hi:
            lo = _hhmm(h1, m1, "am")                # "11-1 pm" = 11 am to 1 pm
        out.append(f"{lo:04d}-{hi if hi else 2400:04d}")
        return " "

    def bound(m):
        word, h, mm, ap = m.groups()
        if not ap:
            return m.group(0)
        v = _hhmm(h, mm, ap)
        out.append(f"{v:04d}-2400" if word.lower() in ("after", "from") else f"0000-{v:04d}")
        return " "

    def one(m):
        v = _hhmm(*m.groups())
        out.append(f"{v:04d}-{min(v + 100, 2400):04d}")   # "7 pm" = shows starting 7-8 pm
        return " "

    text = TIME_RANGE.sub(rng, text)
    text = TIME_AFTER.sub(bound, text)
    text = TIME_ONE.sub(one, text)
    for p in PERIODS:
        if re.search(rf"\b{p}\b", text, re.I):
            out.append(p)
            text = re.sub(rf"\b{p}\b", " ", text, flags=re.I)
    return out, text


# ── one-line requests: "6, 7 Nov 7-8 pm 3D PXL Mani Square" ─────────────
FILLER = {"at", "on", "in", "for", "show", "shows", "showtime", "showtimes", "ticket", "tickets", "from",
          "the", "between", "only", "please", "pm", "am", "any", "time", "date", "dates", "theatre",
          "theatres", "theater", "theaters", "cinema", "cinemas", "set", "and", "of", "with", "want",
          "need", "i", "me", "book", "booking", "watch", "movie", "&"}


def parse_query(text: str, today: date) -> dict:
    """Free text -> {'city', 'dates', 'times', 'formats', 'theatres'} with only what was found.
    Raises ValueError for a date-looking phrase that isn't a real date."""
    out: dict = {}
    times, rest = _take_times(text)
    if times:
        out["times"] = list(dict.fromkeys(times))
    dates, rest = take_dates(rest, today)
    if dates:
        out["dates"] = dates
    formats = [re.sub(r"\s+", " ", m).lower() for m in FORMAT_RUN.findall(rest)]
    if formats:
        out["formats"] = list(dict.fromkeys(formats))
        rest = FORMAT_RUN.sub(" ", rest)
    for word, slug in CITY_ALIASES.items():
        if re.search(rf"\b{word}\b", rest, re.I):
            out["city"] = slug
            rest = re.sub(rf"\b{word}\b", " ", rest, flags=re.I)
    names = []
    for seg in re.split(r"[,;/]|\band\b|\bor\b", rest, flags=re.I):
        words = [w for w in re.findall(r"[\w'&.:-]+", seg) if w.lower().strip(".:") not in FILLER]
        if words:
            names.append(" ".join(words).lower())
    if names:
        out["theatres"] = list(dict.fromkeys(names))
    return out


def summarize(q: dict) -> str:
    bits = []
    if q.get("city"):
        bits.append(f"📍 {city_name(q['city'])}")
    if q.get("dates"):
        bits.append("📅 " + ", ".join(short_date(d) for d in q["dates"]))
    if q.get("theatres"):
        bits.append("🏛 " + ", ".join(q["theatres"]))
    if q.get("formats"):
        bits.append("🎞 " + ", ".join(x.upper() for x in q["formats"]))
    if q.get("times"):
        bits.append("🕐 " + ", ".join(fmt_time(x) for x in q["times"]))
    return " · ".join(bits)


# ── trackers ────────────────────────────────────────────────────────────
FILTERS = ("dates", "theatres", "formats", "times")


def new_tracker(link: dict, label: str, owner: str = "", admin: str = "") -> dict:
    suffix = f"-{owner}" if owner and owner != admin else ""
    return {"id": f"{link['event_code'].lower()}-{link['region_slug']}{suffix}", "label": label,
            "event_code": link["event_code"], "region_slug": link["region_slug"], "slug": link["slug"],
            "dates": [], "theatres": [], "formats": [], "times": [], "enabled": True, "confirmed": False,
            **({"chat": owner} if suffix else {})}


def signature(t: dict) -> str:
    return json.dumps([t["region_slug"]] + [sorted(t.get(k, [])) for k in FILTERS])


def title(t: dict, cat: dict | None = None) -> str:
    cat = cat or {}
    name = cat.get("name") or t["label"]
    lang = (cat.get("language") or "").split("•")[0].strip()      # every format is checked
    return f"{name} · {lang}" if lang else name


def describe(t: dict, cat: dict | None = None) -> str:
    if t.get("source") == "district":
        return f"{t['label']} - District ({t['city']})\n   alert when showtimes appear"
    dates = ", ".join(short_date(d) for d in t["dates"]) or "any open date"
    theatres = ", ".join(t["theatres"]) or "any theatre"
    formats = ", ".join(x.upper() for x in t.get("formats", [])) or "any format"
    times = ", ".join(fmt_time(x) for x in t["times"]) or "any time"
    flag = "" if t.get("confirmed", True) else " (not started: tap Done)"
    return (f"{title(t, cat)} ({city_name(t['region_slug'])}){flag}\n"
            f"   📅 {dates} · 🏛 {theatres} · 🎞 {formats} · 🕐 {times}")


def open_dates(cat: dict | None) -> list[str]:
    return [d for d, s in (cat or {}).get("dates", []) if s in OPEN_STATUSES]


def _span(codes: list[str]) -> str:
    if not codes:
        return ""
    return short_date(codes[0]) if len(codes) == 1 else f"{short_date(codes[0])} - {short_date(codes[-1])}"


def status_line(cat: dict | None, result: str | None) -> str:
    if not cat:
        if result:
            return f"Last check: {result}"
        return "🔎 Checking BookMyShow now (usually about a minute)..."
    venues = cat.get("venues", [])
    if venues:
        return f"🟢 Bookings OPEN: {_span(open_dates(cat))} · {len(venues)} theatre(s) listed so far"
    return "⏳ Bookings not open yet. I'll alert you the moment they open."


def theatre_options(cat: dict | None) -> list[str]:
    """Theatre buttons: the cinemas BookMyShow lists for this movie."""
    return (cat or {}).get("venues", [])


def format_options(cat: dict | None) -> list[str]:
    """Format buttons: what this movie is listed in (formats, then special screens), else common ones."""
    cat = cat or {}
    listed = cat.get("formats", []) + [x for x in cat.get("screens", []) if x not in cat.get("formats", [])]
    return listed or DEFAULT_FORMATS


def card_text(t: dict, cat: dict | None = None, result: str | None = None, note: str = "") -> str:
    """The setup card. Shared by the relay (on taps) and the checker (after each check)."""
    statuses = {d: s for d, s in (cat or {}).get("dates", [])}
    known = [v.lower() for v in theatre_options(cat) + (cat or {}).get("formats", [])]

    def date_tag(d):                     # validated against what BookMyShow lists, once we know
        if not cat:
            return short_date(d)
        s = statuses.get(d)
        return f"{short_date(d)} ({'open' if s in OPEN_STATUSES else 'not open yet' if s else 'not listed yet'})"

    def theatre_tag(k):
        if cat and any(k in v for v in known):
            return f"{k} (showing it)"
        names = cinema_matches(t["region_slug"], k)
        if len(names) == 1:
            where = names[0]
        elif names:
            where = f"{len(names)} cinemas"
        else:
            where = f"no cinema by that name in {city_name(t['region_slug'])}" if CINEMAS else ""
        if cat and names:
            where += ", not showing it yet"
        return f"{k} ({where})" if where else k

    fmt_known = " ".join((cat or {}).get("formats", []) + (cat or {}).get("screens", [])).lower().split()

    def format_tag(k):
        if not cat:
            return k.upper()
        ok = all(w in fmt_known for w in k.split())
        return f"{k.upper()} ({'showing' if ok else 'not listed yet'})"

    dates = ", ".join(date_tag(d) for d in t["dates"]) or (
        f"any open date ({_span(open_dates(cat))})" if open_dates(cat) else "any date")
    theatres = ", ".join(theatre_tag(k) for k in t["theatres"]) or "any theatre"
    formats = ", ".join(format_tag(k) for k in t.get("formats", [])) or "any format"
    times = ", ".join(fmt_time(x) for x in t["times"]) or "any time"
    lines = [f"🎬 {title(t, cat)}", f"📍 {city_name(t['region_slug'])}", "", status_line(cat, result), "",
             "Alert me for:", f"📅 {dates}", f"🏛 {theatres}", f"🎞 {formats}", f"🕐 {times}"]
    if cat and (cat.get("formats") or cat.get("screens")):
        lines += ["", "Available: " + " · ".join(x for x in (", ".join(cat.get("formats", [])),
                                                    ", ".join(cat.get("screens", []))) if x)]
    if cat and result and t.get("confirmed", True):
        lines += ["", f"Right now: {result}"]
    if note:
        lines += ["", note]
    return "\n".join(lines)


def main_buttons(tid: str, confirmed: bool = True):
    return [[("📅 Dates", f"set:{tid}:dates"), ("🏛 Theatres", f"set:{tid}:theatres")],
            [("🎞 Format", f"set:{tid}:formats"), ("🕐 Time", f"set:{tid}:times")],
            [("📍 City", f"set:{tid}:city"),
             ("✅ Done" if confirmed else "✅ Done, start watching", f"set:{tid}:done")]]


def done_buttons(tid: str):
    return [[("✏️ Edit", f"set:{tid}:edit")]]


def _rows(buttons, n):
    return [buttons[i:i + n] for i in range(0, len(buttons), n)]


def time_buttons(t: dict):
    tick = lambda p: ("✅ " if p in t["times"] else "") + p.capitalize()  # noqa: E731
    return ([[(tick(p), f"tm:{t['id']}:{p}") for p in PERIODS[:2]],
             [(tick(p), f"tm:{t['id']}:{p}") for p in PERIODS[2:]],
             [("Any time", f"tm:{t['id']}:any"), ("⬅️ Back", f"set:{t['id']}:back")]])


def date_buttons(t: dict, cat: dict | None):
    days = [(d, s) for d, s in (cat or {}).get("dates", [])][:15]
    btns = [((("✅ " if d in t["dates"] else "") + ("" if s in OPEN_STATUSES else "⏳ ") + short_date(d)),
             f"dt:{t['id']}:{d}") for d, s in days]
    return _rows(btns, 3) + [[("Any open date", f"dt:{t['id']}:any"), ("⬅️ Back", f"set:{t['id']}:back")]]


def theatre_buttons(t: dict, cat: dict | None):
    btns = [(("✅ " if v.lower() in t["theatres"] else "") + v[:32], f"vn:{t['id']}:{i}")
            for i, v in enumerate(theatre_options(cat)[:14])]
    return _rows(btns, 1) + [[("Any theatre", f"vn:{t['id']}:any"), ("⬅️ Back", f"set:{t['id']}:back")]]


def format_buttons(t: dict, cat: dict | None):
    opts = format_options(cat)[:14]
    btns = [(("✅ " if o.lower() in t.get("formats", []) else "") + o, f"fm:{t['id']}:{i}")
            for i, o in enumerate(opts)]
    return _rows(btns, 2) + [[("Any format", f"fm:{t['id']}:any"), ("⬅️ Back", f"set:{t['id']}:back")]]


def city_buttons(prefix: str, link_city: str = "", back: str = ""):
    order = [HOME_CITY] + ([link_city] if link_city and link_city != HOME_CITY else []) + \
            [c for c in CITIES if c not in (HOME_CITY, link_city)]
    btns = [(city_name(c) + (" (link)" if c == link_city and c != HOME_CITY else ""), f"{prefix}{c}")
            for c in order if c in CITIES]
    rows = _rows(btns, 2)
    return rows + ([[("⬅️ Back", back)]] if back else [])


# ── Telegram client ─────────────────────────────────────────────────────
def _markup(buttons):
    return {"inline_keyboard": [[{"text": label, "callback_data": data} for label, data in row]
                                for row in (buttons or [])]}


class Telegram:
    """`chat` on each call picks the recipient; default is the owner's chat."""

    def __init__(self, token: str, chat_id: str):
        self.base = f"https://api.telegram.org/bot{token}"
        self.chat_id = str(chat_id)

    def _call(self, method: str, **payload):
        try:
            r = requests.post(f"{self.base}/{method}", json=payload, timeout=30)
            if r.ok:
                return r.json().get("result")
            if "message is not modified" not in r.text:
                print(f"  ❌ Telegram {method} {r.status_code}: {r.text[:200]}")
        except requests.RequestException as e:
            print(f"  ❌ Telegram {method} failed: {e}")
        return None

    def send(self, text: str, buttons=None, on_id=None, chat=None):
        payload = {"chat_id": str(chat or self.chat_id), "text": text[:4000], "disable_web_page_preview": True}
        if buttons:
            payload["reply_markup"] = _markup(buttons)
        mid = (self._call("sendMessage", **payload) or {}).get("message_id")
        if on_id and mid:
            on_id(mid)
        return mid

    def edit(self, message_id: int, text: str, buttons=None, chat=None) -> bool:
        res = self._call("editMessageText", chat_id=str(chat or self.chat_id), message_id=message_id,
                         text=text[:4000], disable_web_page_preview=True, reply_markup=_markup(buttons))
        return res is not None

    def edit_buttons(self, message_id: int, buttons, chat=None) -> None:
        self._call("editMessageReplyMarkup", chat_id=str(chat or self.chat_id), message_id=message_id,
                   reply_markup=_markup(buttons))

    def answer(self, callback_id: str, text: str = "") -> None:
        self._call("answerCallbackQuery", callback_query_id=callback_id, text=text)


class ConsoleTelegram:
    """Used when no token is set: prints instead of sending."""
    chat_id = ""

    def __init__(self):
        self.sent: list[str] = []

    def send(self, text, buttons=None, on_id=None, chat=None):
        self.sent.append(text)
        print(f"\n[telegram {chat or ''}]\n" + text + "\n")

    def edit(self, message_id, text, buttons=None, chat=None):
        print(f"\n[telegram edit {message_id}]\n" + text + "\n")
        return True

    def edit_buttons(self, message_id, buttons, chat=None):
        pass

    def answer(self, callback_id, text=""):
        pass


# ── conversation ────────────────────────────────────────────────────────
def migrate_state(state: dict, admin: str) -> dict:
    """chat.json used to hold one person's state at the top level; move it under the owner."""
    if "users" not in state:
        old = {k: state.pop(k) for k in ("awaiting", "cards") if k in state}
        state["users"] = {admin: old} if admin and old else {}
    state.setdefault("allowed", [])
    state.setdefault("pending", {})
    return state


class Chat:
    """Owns trackers.json and chat.json; the checker owns results/catalog (read here).
    chat.json: {"users": {chat_id: {"awaiting", "cards", "prefill"}}, "allowed": [...], "pending": {...}}."""

    def __init__(self, tg, trackers: list, state: dict, today: date, results: dict | None = None,
                 region_ok=lambda slug: True, label_fn=fetch_label, catalog: dict | None = None,
                 admin: str | None = None, now: datetime | None = None):
        self.tg, self.trackers = tg, trackers
        self.admin = str(admin if admin is not None else getattr(tg, "chat_id", "") or "")
        self.state = migrate_state(state, self.admin)
        self.results = results or {}
        self.catalog = catalog or {}
        self.today, self.region_ok, self.label_fn = today, region_ok, label_fn
        self.now = now or datetime.now(IST)
        self.uid = self.admin
        self.lookups: list[str] = []          # trackers that need a BookMyShow check right away

    # -- per-person state
    @property
    def me(self) -> dict:
        return self.state["users"].setdefault(self.uid, {})

    def _say(self, text, buttons=None, on_id=None):
        return self.tg.send(text, buttons, on_id=on_id, chat=self.uid)

    def _mine(self) -> list[dict]:
        return [t for t in self.trackers if str(t.get("chat") or self.admin) == self.uid]

    def _find(self, ref: str) -> dict | None:
        ref = (ref or "").strip().lower()
        mine = self._mine()
        if ref.isdigit() and 1 <= int(ref) <= len(mine):
            return mine[int(ref) - 1]
        return next((t for t in mine if t["id"] == ref), None)

    def _card(self, t: dict, note: str = "") -> str:
        return card_text(t, self.catalog.get(t["id"]), self.results.get(t["id"]), note)

    def _cards(self) -> dict:
        return self.me.setdefault("cards", {})

    def _show(self, t: dict, note: str = "", message_id=None, buttons=None) -> None:
        """Edit the card in place (button taps) or post a fresh one at the bottom (typed input)."""
        confirmed = t.get("confirmed", True)
        buttons = buttons or main_buttons(t["id"], confirmed)
        if not note and not confirmed:
            note = NOT_YET
        cards, tid = self._cards(), t["id"]
        cards[tid] = {**cards.get(tid, {}), "done": buttons[0][0][1].endswith(":edit")}
        if message_id:
            cards[tid]["id"] = message_id
            self.tg.edit(message_id, self._card(t, note), buttons, chat=self.uid)
            return
        old = cards[tid].get("id")
        if old:
            self.tg.edit_buttons(old, [], chat=self.uid)      # one live card: strip the old one's buttons
        uid = self.uid

        def remember(mid, tid=tid, uid=uid):
            self.state["users"].setdefault(uid, {}).setdefault("cards", {}).setdefault(tid, {})["id"] = mid
        mid = self._say(self._card(t, note), buttons, on_id=remember)
        if mid:
            remember(mid)

    def _active(self) -> dict | None:
        aw = self.me.get("awaiting") or {}
        return self._find(aw.get("id", "")) if aw.get("id") else None

    # -- entry point
    def handle(self, update: dict) -> None:
        cb = update.get("callback_query")
        msg = update.get("message") or (cb or {}).get("message") or {}
        chat = msg.get("chat") or {}
        if chat.get("type", "private") != "private":
            return
        self.uid = str(chat.get("id", ""))
        if not self.uid:
            return
        if cb:
            self.tg.answer(cb["id"])
        if not self._allowed():
            self._knock(update.get("message") or {}, cb)
            return
        if cb:
            self._callback(cb.get("data", ""), msg.get("message_id"))
        elif update.get("message") and msg.get("text"):
            self._text(msg["text"].strip())

    # -- who may use the bot
    def _allowed(self) -> bool:
        return self.uid == self.admin or self.uid in self.state["allowed"]

    def _knock(self, msg: dict, cb) -> None:
        if cb or not msg or self.uid in self.state["pending"]:
            if msg and not cb:
                self._say("Still waiting for the owner to let you in.")
            return
        who = msg.get("from") or msg.get("chat") or {}
        name = " ".join(x for x in (who.get("first_name"), who.get("last_name")) if x) or "Someone"
        handle = f" (@{who['username']})" if who.get("username") else ""
        self.state["pending"][self.uid] = f"{name}{handle}"
        self._say("This is a private bot. I've asked the owner to let you in; you'll get a message here.")
        self.tg.send(f"👤 {name}{handle} wants to use the ticket bot.",
                     [[("✅ Allow", f"ok:{self.uid}"), ("🚫 Ignore", f"no:{self.uid}")]], chat=self.admin)

    def _decide(self, kind: str, uid: str, message_id) -> None:
        if self.uid != self.admin:
            return
        name = self.state["pending"].pop(uid, uid)
        if kind == "ok":
            if uid not in self.state["allowed"]:
                self.state["allowed"].append(uid)
            self.tg.send("✅ You're in. Send me a BookMyShow movie link to start.\n\n" + HELP, chat=uid)
        self.tg.edit(message_id, f"{'✅ Allowed' if kind == 'ok' else '🚫 Ignored'}: {name}", [], chat=self.admin)

    # -- buttons
    def _callback(self, data: str, message_id) -> None:
        kind, _, rest = data.partition(":")
        if kind in ("ok", "no"):
            self._decide(kind, rest, message_id)
            return
        if kind == "nc":                              # city picked for a new link
            self._new_in_city(rest, message_id)
            return
        tid, _, arg = rest.partition(":")
        t = self._find(tid)
        if not t:
            self._say("That tracker no longer exists. /list")
            return
        cat = self.catalog.get(t["id"])
        if kind == "set":
            self._menu(t, arg, cat, message_id)
        elif kind == "tm":
            if arg == "any":
                t["times"] = []
            elif arg in PERIODS:
                t["times"] = [p for p in t["times"] if p != arg] if arg in t["times"] else t["times"] + [arg]
            self._show(t, "Tap times of day, or type a range like 7-8 pm.", message_id, time_buttons(t))
        elif kind == "dt":
            if arg == "any":
                t["dates"] = []
            elif re.fullmatch(r"\d{8}", arg):
                t["dates"] = sorted(set(t["dates"]) ^ {arg})
            self._show(t, "Tap dates, or type them (e.g. 6, 7 Nov).", message_id, date_buttons(t, cat))
        elif kind == "vn":
            venues = theatre_options(cat)
            if arg == "any":
                t["theatres"] = []
            elif arg.isdigit() and int(arg) < len(venues):
                name = venues[int(arg)].lower()
                t["theatres"] = [x for x in t["theatres"] if x != name] if name in t["theatres"] \
                    else t["theatres"] + [name]
            self._show(t, "Tap theatres, or type part of a name (e.g. Mani Square).", message_id,
                       theatre_buttons(t, cat))
        elif kind == "fm":
            opts = [o.lower() for o in format_options(cat)]
            cur = t.setdefault("formats", [])
            if arg == "any":
                t["formats"] = []
            elif arg.isdigit() and int(arg) < len(opts):
                o = opts[int(arg)]
                t["formats"] = [x for x in cur if x != o] if o in cur else cur + [o]
            self._show(t, "Tap formats, or type them (e.g. 3D PXL, IMAX).", message_id, format_buttons(t, cat))
        elif kind == "ct":
            self._move(t, arg, message_id)

    def _done(self, t: dict, message_id) -> None:
        cat = self.catalog.get(t["id"])
        if open_dates(cat) and not t["dates"] and not t.get("any_date"):
            self.me["awaiting"] = {"id": t["id"], "field": "dates"}
            t["any_date"] = True                       # a second Done means "any open date" is fine
            self._show(t, f"Bookings are open for {_span(open_dates(cat))}. Which dates do you want? "
                          "Tap them (or Any open date), then Done.", message_id, date_buttons(t, cat))
            return
        if signature(t) != t.get("confirmed_sig"):
            t["epoch"] = t.get("epoch", 0) + 1         # changed filters: fresh check, alert on what matches
            t["confirmed_sig"] = signature(t)
        t["confirmed"] = True
        self.me.pop("awaiting", None)
        self.lookups.append(t["id"])
        self._show(t, SAVED + " Checking now.", message_id, done_buttons(t["id"]))

    def _menu(self, t: dict, arg: str, cat, message_id) -> None:
        tid = t["id"]
        if arg == "done":
            self._done(t, message_id)
        elif arg in ("back", "edit"):
            self.me["awaiting"] = {"id": tid, "field": "any"}
            self._show(t, TIP, message_id)
        elif arg == "times":
            self.me["awaiting"] = {"id": tid, "field": "times"}
            self._show(t, "Tap times of day, or type a range like 7-8 pm.", message_id, time_buttons(t))
        elif arg == "dates":
            self.me["awaiting"] = {"id": tid, "field": "dates"}
            hint = "Tap dates, or type them (e.g. 6, 7 Nov)." if (cat or {}).get("dates") else \
                "Type the dates, e.g. 6, 7 Nov or 18-20 Dec (buttons appear once BookMyShow lists dates)."
            self._show(t, hint, message_id, date_buttons(t, cat))
        elif arg == "theatres":
            self.me["awaiting"] = {"id": tid, "field": "theatres"}
            hint = "Tap theatres, or type part of a name (e.g. Mani Square, South City)." if \
                theatre_options(cat) else "Type part of a cinema name, e.g. Mani Square, South City."
            self._show(t, hint, message_id, theatre_buttons(t, cat))
        elif arg == "formats":
            self.me["awaiting"] = {"id": tid, "field": "formats"}
            self._show(t, "Tap formats, or type them (e.g. 3D PXL, IMAX).", message_id, format_buttons(t, cat))
        elif arg == "city":
            self._show(t, "Which city?", message_id,
                       city_buttons(f"ct:{tid}:", t["region_slug"], back=f"set:{tid}:back"))

    def _move(self, t: dict, city: str, message_id, show: bool = True) -> None:
        if city not in CITIES or city == t["region_slug"]:
            if show:
                self._show(t, TIP, message_id)
            return
        suffix = t["id"][len(f"{t['event_code'].lower()}-{t['region_slug']}"):]
        new_id = f"{t['event_code'].lower()}-{city}{suffix}"
        if self._find(new_id):
            if show:
                self._show(t, f"Already tracking it in {city_name(city)} (see /list).", message_id)
            return
        cards = self._cards()
        if t["id"] in cards:
            cards[new_id] = cards.pop(t["id"])
        t["id"], t["region_slug"] = new_id, city
        t["theatres"] = []                              # theatres belong to the old city
        self.me["awaiting"] = {"id": new_id, "field": "any"}
        self.lookups.append(new_id)
        if show:
            self._show(t, f"📍 Switched to {city_name(city)}.", message_id)

    # -- text
    def _text(self, text: str) -> None:
        d = parse_district_link(text)
        if d:
            self._add_district(d)
            return
        link = parse_link(text)
        if link:
            self._add(link, share_title(text), link_extras(text))
            return
        if text.startswith("/"):
            self._command(text)
            return
        aw = self.me.get("awaiting") or {}
        if aw.get("field") == "district_city":
            self._finish_district(aw, text)
            return
        if aw.get("field") == "newcity":
            self._more_for_new(aw, text)
            return
        t = self._active()
        if t:
            self._apply(t, aw.get("field", "any"), text)
            return
        self._prefill(text)

    def _parse(self, text: str, hint: list[str] | None = None, field: str = "any") -> dict:
        """parse_query, plus bare day numbers ("7 and 8") when the question was about dates."""
        if field == "dates":
            days = bare_days(text, self.today, hint)
            if days:
                return {"dates": days}
        return parse_query(text, self.today)

    def _prefill(self, text: str) -> None:
        """Details typed before the link: keep them for the next link (10 minutes)."""
        try:
            q = self._parse(text)
        except ValueError as e:
            self._say(f"⚠️ {e}. Send me a BookMyShow movie link, or /help.")
            return
        if not q or set(q) == {"theatres"}:          # plain chat ("hello") isn't a request
            self._say("Send me a BookMyShow movie link, or /help.")
            return
        self.me["prefill"] = {"q": q, "at": self.now.isoformat()}
        self._say(f"Noted: {summarize(q)}\nNow send the BookMyShow link.")

    def _take_prefill(self) -> dict:
        pre = self.me.pop("prefill", None) or {}
        try:
            fresh = self.now - datetime.fromisoformat(pre.get("at", "")) < timedelta(minutes=10)
        except ValueError:
            fresh = False
        return pre.get("q", {}) if fresh else {}

    def _apply(self, t: dict, field: str, text: str) -> None:
        if text.lower() in ("any", "clear", "all", "none") and field in FILTERS:
            t[field] = []
            self._show(t, f"✅ Cleared {field}.")
            return
        try:
            q = self._parse(text, open_dates(self.catalog.get(t["id"])), field)
        except ValueError as e:
            self._show(t, f"⚠️ {e}. Try: 6, 7 Nov or 18-20 Dec")
            return
        city = q.pop("city", None)
        if city:
            self._move(t, city, None, show=False)         # same tracker object, new id and city
        if not q and not city:
            self._show(t, f"⚠️ Couldn't read '{text}'. {TIP}")
            return
        if field == "dates" and "dates" not in q:
            self._show(t, f"⚠️ No date in '{text}'. Try: 6, 7 Nov or 18-20 Dec")
            return
        for k, v in q.items():
            t[k] = v
        self.me["awaiting"] = {"id": t["id"], "field": "any"}
        names = {"dates": "dates", "times": "time", "formats": "format", "theatres": "theatres"}
        changed = ", ".join(names[k] for k in names if k in q)
        moved = f"📍 {city_name(t['region_slug'])}. " if city else ""
        self._show(t, f"✅ {moved}" + (f"Updated {changed}. " if changed else "") + "Tap ✅ Done to save and check now.")

    def _add(self, link: dict, label: str = "", extras: str = "") -> None:
        try:
            q = {**self._take_prefill(), **(parse_query(extras, self.today) if extras else {})}
        except ValueError as e:
            q = {}
            self._say(f"⚠️ {e}. I'll ignore that part.")
        label = label or self.label_fn(link)
        existing = [t for t in self._mine() if t.get("event_code") == link["event_code"]]
        aw = {"field": "newcity", "link": link, "label": label, "q": q}
        if q.get("city"):
            self.me["awaiting"] = aw
            self._new_in_city(q["city"], None)
            return
        self.me["awaiting"] = aw
        note = ("\nAlready watching in: " + ", ".join(city_name(t["region_slug"]) for t in existing)) \
            if existing else ""
        got = f"\nGot: {summarize(q)}" if q else ""
        self._say(f"🎬 {label}{got}\nWhich city? (BookMyShow links carry the city they were shared from.){note}",
                  city_buttons("nc:", link["region_slug"]))

    def _more_for_new(self, aw: dict, text: str) -> None:
        """Details sent as a separate message right after the link."""
        try:
            q = parse_query(text, self.today)
        except ValueError as e:
            self._say(f"⚠️ {e}. Pick the city above, or send the details again.")
            return
        aw["q"] = {**aw.get("q", {}), **q}
        if q.get("city"):
            self._new_in_city(q["city"], None)
            return
        self._say(f"Got: {summarize(aw['q'])}\nWhich city?", city_buttons("nc:", aw["link"]["region_slug"]))

    def _new_in_city(self, city: str, message_id) -> None:
        aw = self.me.get("awaiting") or {}
        if aw.get("field") != "newcity" or city not in CITIES:
            self._say("Send the link again, please.")
            return
        link = {**aw["link"], "region_slug": city}
        t = new_tracker(link, aw.get("label") or slug_label(link["slug"]), self.uid, self.admin)
        existing = self._find(t["id"])
        q = {k: v for k, v in (aw.get("q") or {}).items() if k in FILTERS}
        if existing:
            t = existing
            note = "You're already watching this; showing its card."
        else:
            self.trackers.append(t)
            note = ""
        for k, v in q.items():
            t[k] = v
        self.lookups.append(t["id"])
        self.me["awaiting"] = {"id": t["id"], "field": "any"}
        if not note:
            note = (f"Got: {summarize(q)}. " if q else "") + NOT_YET
        self._show(t, note, message_id)

    def _add_district(self, d: dict) -> None:
        if not d["city"]:
            self.me["awaiting"] = {"field": "district_city", "link": d}
            self._say("That District link has no city. Which city? (e.g. Bengaluru)")
            return
        tid = f"{d['mv'].lower()}-{d['city']}" + (f"-{self.uid}" if self.uid != self.admin else "")
        existing = self._find(tid)
        if existing:
            self._say(f"Already tracking:\n{describe(existing)}")
            return
        t = {"id": tid, "source": "district", "label": slug_label(d["slug"]), "slug": d["slug"],
             "city": d["city"], "mv": d["mv"], "dates": [], "theatres": [], "times": [], "enabled": True,
             **({"chat": self.uid} if self.uid != self.admin else {})}
        self.trackers.append(t)
        self._say(f"👀 Tracking started:\n{describe(t)}\n\n"
                  "Heads-up: district.in refuses GitHub's servers, so this only works when "
                  "the watcher runs from another network (it will show as blocked in /list).")

    def _finish_district(self, aw: dict, text: str) -> None:
        city = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
        city = {"bangalore": "bengaluru", "bombay": "mumbai", "gurgaon": "gurugram"}.get(city, city)
        self.me.pop("awaiting", None)
        if not city:
            self._say("Send the District link again.")
            return
        self._add_district({**aw["link"], "city": city})

    def _command(self, text: str) -> None:
        cmd, _, arg = text.partition(" ")
        cmd = cmd.split("@")[0].lower()
        if cmd in ("/start", "/help"):
            self._say(HELP)
        elif cmd == "/list":
            mine = self._mine()
            if not mine:
                self._say("Nothing tracked yet. Send me a BookMyShow movie link.")
                return
            lines = []
            for i, t in enumerate(mine, 1):
                flag = "" if t.get("enabled", True) else " ⏸ paused"
                res = self.results.get(t["id"], "not checked yet")
                lines.append(f"{i}. {describe(t, self.catalog.get(t['id']))}{flag}\n   last: {res}")
            self._say("\n\n".join(lines))
        elif cmd == "/users" and self.uid == self.admin:
            allowed = self.state["allowed"]
            self._say("People who can use the bot (besides you):\n" +
                      ("\n".join(f"• {u}" for u in allowed) or "nobody yet") +
                      "\n\n/revoke ID removes someone.")
        elif cmd == "/revoke" and self.uid == self.admin:
            uid = arg.strip()
            if uid in self.state["allowed"]:
                self.state["allowed"].remove(uid)
                for t in [t for t in self.trackers if str(t.get("chat")) == uid]:
                    self.trackers.remove(t)
                self.state["users"].pop(uid, None)
                self._say(f"Removed {uid} and their trackers.")
            else:
                self._say("Usage: /revoke ID (see /users)")
        elif cmd in ("/pause", "/resume", "/remove", "/edit"):
            t = self._find(arg)
            if not t:
                self._say(f"Usage: {cmd} N  (N from /list)")
            elif cmd == "/edit" and t.get("source") == "district":
                self._say("District trackers only alert when showtimes appear; nothing to edit.")
            elif cmd == "/edit":
                self.me["awaiting"] = {"id": t["id"], "field": "any"}
                self._show(t, TIP)
            elif cmd == "/remove":
                self.trackers.remove(t)                 # the checker drops its state next run
                self._cards().pop(t["id"], None)
                self._say(f"🗑 Stopped tracking {t['label']}.")
            else:
                t["enabled"] = cmd == "/resume"
                if t["enabled"]:
                    t["epoch"] = t.get("epoch", 0) + 1  # fresh start: alert again if already open
                    self.lookups.append(t["id"])
                self._say(f"{'▶️ Resumed' if t['enabled'] else '⏸ Paused'} {t['label']}.")
        else:
            self._say("Unknown command. /help")
