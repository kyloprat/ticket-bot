"""Telegram conversation: share a BookMyShow link, tune what to watch, manage trackers.

Send the bot a BookMyShow movie link. It asks which city, then shows one "setup card" that
it keeps editing: live booking status (filled in by the checker), and buttons for dates,
theatres, time of day and city. Anything can also be typed in one line: "6 Nov 7-8 pm PXL".
Commands: /list  /pause N  /resume N  /remove N  /edit N  /help   (N = number from /list)
"""
from __future__ import annotations

import calendar
import json
import os
import re
from datetime import date, timedelta

import requests

from district import parse_district_link

PERIODS = ["morning", "afternoon", "evening", "night"]
CITIES = ["bengaluru", "kolkata", "mumbai", "delhi-ncr", "chennai", "hyderabad", "pune", "kochi"]
HOME_CITY = "bengaluru"
OPEN_STATUSES = ("BOOKABLE", "AVAILABLE")
BMS_LINK = re.compile(r"https?://(?:www\.)?in\.bookmyshow\.com/movies/\S+", re.I)
EVENT_CODE = re.compile(r"ET\d{8,}", re.I)
SHARE_TITLE = re.compile(r"about the movie\s+(.+?)\s+https?://", re.I | re.S)
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36")

MONTHS = {m.lower(): i for i, m in enumerate(calendar.month_name) if m}
MONTHS.update({m.lower(): i for i, m in enumerate(calendar.month_abbr) if m})
MONTHS["sept"] = 9

HELP = (
    "Send me a BookMyShow movie link and I'll track it.\n"
    "I ask which city, then show a card with the live booking status. Narrow it with the "
    "buttons, or type it in one line, e.g. 6 Nov 7-8 pm PXL\n\n"
    "/list - what I'm tracking\n"
    "/edit N - change dates, theatres, time or city\n"
    "/pause N, /resume N, /remove N\n"
    "N is the number shown in /list. Checks run every ~10 minutes."
)
TIP = "Tip: type it in one line, e.g. 6 Nov 7-8 pm 3D PXL Mani Square"

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
    raise ValueError(f"Couldn't read '{part}' as a date")


def parse_dates(text: str, today: date) -> list[str]:
    """'18-20 Dec, 25 Dec, 2027-01-01' -> ['20261218', '20261219', ...]. Raises ValueError."""
    out: list[str] = []
    for part in re.split(r"[;,]|\band\b", text.lower()):
        part = part.strip()
        if not part:
            continue
        if part in ("today", "tomorrow"):
            out.append((today + timedelta(days=part == "tomorrow")).strftime("%Y%m%d"))
        elif re.fullmatch(r"\d{8}", part):
            out.append(part)
        elif m := re.fullmatch(r"(\d{4})-(\d{1,2})-(\d{1,2})", part):
            out.append(_make(int(m[1]), int(m[2]), int(m[3]), today, part))
        elif m := re.fullmatch(r"(\d{1,2})(?:st|nd|rd|th)?\s*(?:-|to)\s*(\d{1,2})(?:st|nd|rd|th)?\s*([a-z]+)(?:\s+(\d{4}))?", part):
            mo = MONTHS.get(m[3])
            lo, hi = int(m[1]), int(m[2])
            if not mo or hi < lo or hi - lo > 14:
                raise ValueError(f"Couldn't read '{part}' as a date range (max 15 days)")
            out += [_make(int(m[4]) if m[4] else None, mo, d, today, part) for d in range(lo, hi + 1)]
        elif m := re.fullmatch(r"(\d{1,2})(?:st|nd|rd|th)?\s*([a-z]+)(?:\s+(\d{4}))?", part):
            mo = MONTHS.get(m[2])
            if not mo:
                raise ValueError(f"Couldn't read '{part}' as a date")
            out.append(_make(int(m[3]) if m[3] else None, mo, int(m[1]), today, part))
        elif m := re.fullmatch(r"([a-z]+)\s+(\d{1,2})(?:st|nd|rd|th)?(?:\s+(\d{4}))?", part):
            mo = MONTHS.get(m[1])
            if not mo:
                raise ValueError(f"Couldn't read '{part}' as a date")
            out.append(_make(int(m[3]) if m[3] else None, mo, int(m[2]), today, part))
        else:
            raise ValueError(f"Couldn't read '{part}' as a date")
    return sorted(set(out))


def fmt_date(code: str) -> str:
    return date(int(code[:4]), int(code[4:6]), int(code[6:8])).strftime("%d %b %Y").lstrip("0")


def short_date(code: str) -> str:
    return date(int(code[:4]), int(code[4:6]), int(code[6:8])).strftime("%d %b").lstrip("0")


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


# ── one-line requests: "6 Nov 7-8 pm PXL" ──────────────────────────────
_MON = "(?:" + "|".join(sorted(MONTHS, key=len, reverse=True)) + ")"
_ORD = r"(?:st|nd|rd|th)?"
DATE_PHRASE = re.compile(
    rf"\b\d{{4}}-\d{{1,2}}-\d{{1,2}}\b|\b\d{{8}}\b|\btoday\b|\btomorrow\b"
    rf"|\b\d{{1,2}}{_ORD}\s*(?:(?:-|to)\s*\d{{1,2}}{_ORD}\s*)?{_MON}\b(?:\s+\d{{4}})?"
    rf"|\b{_MON}\s+\d{{1,2}}{_ORD}\b(?:\s+\d{{4}})?", re.I)
FILLER = {"at", "on", "in", "for", "show", "shows", "showtime", "showtimes", "ticket", "tickets", "from",
          "the", "between", "only", "please", "pm", "am", "any", "time", "date", "dates", "theatre",
          "theatres", "theater", "theaters", "cinema", "cinemas", "set", "and"}


def parse_query(text: str, today: date) -> dict:
    """Free text -> {'dates', 'times', 'formats', 'theatres'} with only what was found.
    Raises ValueError for a date-looking phrase that isn't a real date."""
    out: dict = {}
    times, rest = _take_times(text)
    if times:
        out["times"] = list(dict.fromkeys(times))
    phrases = DATE_PHRASE.findall(rest)
    if phrases:
        out["dates"] = parse_dates(", ".join(phrases), today)
        rest = DATE_PHRASE.sub(" ", rest)
    formats = [re.sub(r"\s+", " ", m).lower() for m in FORMAT_RUN.findall(rest)]
    if formats:
        out["formats"] = list(dict.fromkeys(formats))
        rest = FORMAT_RUN.sub(" ", rest)
    names = []
    for seg in re.split(r"[,;/]|\band\b|\bor\b", rest, flags=re.I):
        words = [w for w in re.findall(r"[\w'&.:-]+", seg) if w.lower().strip(".:") not in FILLER]
        if words:
            names.append(" ".join(words).lower())
    if names:
        out["theatres"] = list(dict.fromkeys(names))
    return out


# ── trackers ────────────────────────────────────────────────────────────
def new_tracker(link: dict, label: str) -> dict:
    return {"id": f"{link['event_code'].lower()}-{link['region_slug']}", "label": label,
            "event_code": link["event_code"], "region_slug": link["region_slug"], "slug": link["slug"],
            "dates": [], "theatres": [], "formats": [], "times": [], "enabled": True}


def title(t: dict, cat: dict | None = None) -> str:
    cat = cat or {}
    name = cat.get("name") or t["label"]
    lang = (cat.get("language") or "").split("•")[0].strip()      # every format is checked
    return f"{name} · {lang}" if lang else name


def describe(t: dict, cat: dict | None = None) -> str:
    if t.get("source") == "district":
        return f"{t['label']} - District ({t['city']})\n   alert when showtimes appear"
    dates = ", ".join(fmt_date(d) for d in t["dates"]) or "any date"
    theatres = ", ".join(t["theatres"]) or "any theatre"
    formats = ", ".join(x.upper() for x in t.get("formats", [])) or "any format"
    times = ", ".join(fmt_time(x) for x in t["times"]) or "any time"
    return (f"{title(t, cat)} ({city_name(t['region_slug'])})\n"
            f"   📅 {dates} · 🏛 {theatres} · 🎞 {formats} · 🕐 {times}")


def _span(codes: list[str]) -> str:
    if not codes:
        return ""
    return short_date(codes[0]) if len(codes) == 1 else f"{short_date(codes[0])} - {short_date(codes[-1])}"


def status_line(cat: dict | None, result: str | None) -> str:
    if not cat:
        if result:
            return f"Last check: {result}"
        return "🔎 Checking BookMyShow now (usually 1-10 min)..."
    open_dates = [d for d, s in cat.get("dates", []) if s in OPEN_STATUSES]
    venues = cat.get("venues", [])
    if venues:
        return f"🟢 Bookings OPEN: {_span(open_dates)} · {len(venues)} theatre(s) listed so far"
    return "⏳ Bookings not open yet. I'll alert you the moment they open."


def theatre_options(cat: dict | None) -> list[str]:
    """Theatre buttons: the cinemas BookMyShow lists for this movie."""
    return (cat or {}).get("venues", [])


def format_options(cat: dict | None) -> list[str]:
    """Format buttons: what this movie is listed in (formats, then special screens), else common ones."""
    cat = cat or {}
    listed = cat.get("formats", []) + [x for x in cat.get("screens", []) if x not in cat.get("formats", [])]
    return listed or DEFAULT_FORMATS


def format_buttons(t: dict, cat: dict | None):
    opts = format_options(cat)[:14]
    btns = [(("✅ " if o.lower() in t.get("formats", []) else "") + o, f"fm:{t['id']}:{i}")
            for i, o in enumerate(opts)]
    return _rows(btns, 2) + [[("Any format", f"fm:{t['id']}:any"), ("⬅️ Back", f"set:{t['id']}:back")]]


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

    dates = ", ".join(date_tag(d) for d in t["dates"]) or "any date"
    theatres = ", ".join(theatre_tag(k) for k in t["theatres"]) or "any theatre"
    formats = ", ".join(format_tag(k) for k in t.get("formats", [])) or "any format"
    times = ", ".join(fmt_time(x) for x in t["times"]) or "any time"
    lines = [f"🎬 {title(t, cat)}", f"📍 {city_name(t['region_slug'])}", "", status_line(cat, result), "",
             "Alert me for:", f"📅 {dates}", f"🏛 {theatres}", f"🎞 {formats}", f"🕐 {times}"]
    if cat and (cat.get("formats") or cat.get("screens")):
        lines += ["", "Available: " + " · ".join(x for x in (", ".join(cat.get("formats", [])),
                                                    ", ".join(cat.get("screens", []))) if x)]
    if cat and result:
        lines += ["", f"Right now: {result}"]
    if note:
        lines += ["", note]
    return "\n".join(lines)


def main_buttons(tid: str):
    return [[("📅 Dates", f"set:{tid}:dates"), ("🏛 Theatres", f"set:{tid}:theatres")],
            [("🎞 Format", f"set:{tid}:formats"), ("🕐 Time", f"set:{tid}:times")],
            [("📍 City", f"set:{tid}:city"), ("✅ Done", f"set:{tid}:done")]]


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
    return _rows(btns, 3) + [[("Any date", f"dt:{t['id']}:any"), ("⬅️ Back", f"set:{t['id']}:back")]]


def theatre_buttons(t: dict, cat: dict | None):
    btns = [(("✅ " if v.lower() in t["theatres"] else "") + v[:32], f"vn:{t['id']}:{i}")
            for i, v in enumerate(theatre_options(cat)[:14])]
    return _rows(btns, 1) + [[("Any theatre", f"vn:{t['id']}:any"), ("⬅️ Back", f"set:{t['id']}:back")]]


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

    def send(self, text: str, buttons=None, on_id=None):
        payload = {"chat_id": self.chat_id, "text": text[:4000], "disable_web_page_preview": True}
        if buttons:
            payload["reply_markup"] = _markup(buttons)
        mid = (self._call("sendMessage", **payload) or {}).get("message_id")
        if on_id and mid:
            on_id(mid)
        return mid

    def edit(self, message_id: int, text: str, buttons=None) -> bool:
        res = self._call("editMessageText", chat_id=self.chat_id, message_id=message_id, text=text[:4000],
                         disable_web_page_preview=True, reply_markup=_markup(buttons))
        return res is not None

    def edit_buttons(self, message_id: int, buttons) -> None:
        self._call("editMessageReplyMarkup", chat_id=self.chat_id, message_id=message_id,
                   reply_markup=_markup(buttons))

    def answer(self, callback_id: str, text: str = "") -> None:
        self._call("answerCallbackQuery", callback_query_id=callback_id, text=text)


class ConsoleTelegram:
    """Used when no token is set: prints instead of sending."""
    chat_id = ""

    def __init__(self):
        self.sent: list[str] = []

    def send(self, text, buttons=None, on_id=None):
        self.sent.append(text)
        print("\n[telegram]\n" + text + "\n")

    def edit(self, message_id, text, buttons=None):
        print(f"\n[telegram edit {message_id}]\n" + text + "\n")
        return True

    def edit_buttons(self, message_id, buttons):
        pass

    def answer(self, callback_id, text=""):
        pass


# ── conversation ────────────────────────────────────────────────────────
class Chat:
    """Owns trackers.json and chat.json (`meta` here: pending question, setup-card message ids).
    The checker owns results/catalog; they are only read here."""

    def __init__(self, tg, trackers: list, meta: dict, today: date, results: dict | None = None,
                 region_ok=lambda slug: True, label_fn=fetch_label, catalog: dict | None = None):
        self.tg, self.trackers, self.meta = tg, trackers, meta
        self.results = results or {}
        self.catalog = catalog or {}
        self.today, self.region_ok, self.label_fn = today, region_ok, label_fn
        self.lookups: list[str] = []          # trackers that need a BookMyShow check right away

    # -- helpers
    def _find(self, ref: str) -> dict | None:
        ref = (ref or "").strip().lower()
        if ref.isdigit() and 1 <= int(ref) <= len(self.trackers):
            return self.trackers[int(ref) - 1]
        return next((t for t in self.trackers if t["id"] == ref), None)

    def _card(self, t: dict, note: str = "") -> str:
        return card_text(t, self.catalog.get(t["id"]), self.results.get(t["id"]), note)

    def _cards(self) -> dict:
        return self.meta.setdefault("cards", {})

    def _show(self, t: dict, note: str = "", message_id=None, buttons=None) -> None:
        """Edit the card in place (button taps) or post a fresh one at the bottom (typed input)."""
        buttons = buttons or main_buttons(t["id"])
        cards, tid = self._cards(), t["id"]
        cards[tid] = {**cards.get(tid, {}), "done": buttons and buttons[0][0][1].endswith(":edit")}
        if message_id:
            cards[tid]["id"] = message_id
            self.tg.edit(message_id, self._card(t, note), buttons)
            return
        old = cards[tid].get("id")
        if old:
            self.tg.edit_buttons(old, [])               # one live card: strip the old one's buttons

        def remember(mid, tid=tid):
            self._cards().setdefault(tid, {})["id"] = mid
        mid = self.tg.send(self._card(t, note), buttons, on_id=remember)
        if mid:
            remember(mid)

    def _active(self) -> dict | None:
        aw = self.meta.get("awaiting") or {}
        return self._find(aw.get("id", "")) if aw.get("id") else None

    # -- entry point
    def handle(self, update: dict) -> None:
        cb = update.get("callback_query")
        msg = update.get("message") or (cb or {}).get("message") or {}
        if str((msg.get("chat") or {}).get("id", "")) != self.tg.chat_id and self.tg.chat_id:
            return                                    # only the owner can talk to the bot
        if cb:
            self.tg.answer(cb["id"])
            self._callback(cb.get("data", ""), msg.get("message_id"))
        elif update.get("message") and msg.get("text"):
            self._text(msg["text"].strip())

    # -- buttons
    def _callback(self, data: str, message_id) -> None:
        kind, _, rest = data.partition(":")
        if kind == "nc":                              # city picked for a new link
            self._new_in_city(rest, message_id)
            return
        tid, _, arg = rest.partition(":")
        t = self._find(tid)
        if not t:
            self.tg.send("That tracker no longer exists. /list")
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
            self.lookups.append(t["id"])
            self._show(t, "Tap dates, or type them (e.g. 18-20 Dec).", message_id, date_buttons(t, cat))
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

    def _menu(self, t: dict, arg: str, cat, message_id) -> None:
        tid = t["id"]
        if arg == "done":
            self.meta.pop("awaiting", None)
            self._show(t, "✅ Saved. /list shows everything I'm tracking.", message_id, done_buttons(tid))
        elif arg in ("back", "edit"):
            self.meta["awaiting"] = {"id": tid, "field": "any"}
            self._show(t, TIP, message_id)
        elif arg == "times":
            self.meta["awaiting"] = {"id": tid, "field": "times"}
            self._show(t, "Tap times of day, or type a range like 7-8 pm.", message_id, time_buttons(t))
        elif arg == "dates":
            self.meta["awaiting"] = {"id": tid, "field": "dates"}
            hint = "Tap dates, or type them (e.g. 18-20 Dec)." if (cat or {}).get("dates") else \
                "Type the dates, e.g. 18-20 Dec, 25 Dec (buttons appear once BookMyShow lists dates)."
            self._show(t, hint, message_id, date_buttons(t, cat))
        elif arg == "theatres":
            self.meta["awaiting"] = {"id": tid, "field": "theatres"}
            hint = "Tap theatres, or type part of a name (e.g. Mani Square, South City)." if \
                theatre_options(cat) else "Type part of a cinema name, e.g. Mani Square, South City."
            self._show(t, hint, message_id, theatre_buttons(t, cat))
        elif arg == "formats":
            self.meta["awaiting"] = {"id": tid, "field": "formats"}
            self._show(t, "Tap formats, or type them (e.g. 3D PXL, IMAX).", message_id, format_buttons(t, cat))
        elif arg == "city":
            self._show(t, "Which city?", message_id,
                       city_buttons(f"ct:{tid}:", t["region_slug"], back=f"set:{tid}:back"))

    def _move(self, t: dict, city: str, message_id) -> None:
        if city not in CITIES or city == t["region_slug"]:
            self._show(t, TIP, message_id)
            return
        new_id = f"{t['event_code'].lower()}-{city}"
        if self._find(new_id):
            self._show(t, f"Already tracking it in {city_name(city)} (see /list).", message_id)
            return
        cards = self._cards()
        if t["id"] in cards:
            cards[new_id] = cards.pop(t["id"])
        t["id"], t["region_slug"] = new_id, city
        self.meta["awaiting"] = {"id": new_id, "field": "any"}
        self.lookups.append(new_id)
        self._show(t, f"📍 Switched to {city_name(city)}.", message_id)

    # -- text
    def _text(self, text: str) -> None:
        d = parse_district_link(text)
        if d:
            self._add_district(d)
            return
        link = parse_link(text)
        if link:
            self._add(link, share_title(text))
            return
        if text.startswith("/"):
            self._command(text)
            return
        aw = self.meta.get("awaiting") or {}
        if aw.get("field") == "district_city":
            self._finish_district(aw, text)
            return
        t = self._active()
        if t:
            self._apply(t, aw.get("field", "any"), text)
            return
        self.tg.send("Send me a BookMyShow movie link, or /help.")

    def _apply(self, t: dict, field: str, text: str) -> None:
        if text.lower() in ("any", "clear", "all", "none") and field in ("dates", "theatres", "formats", "times"):
            t[field] = []
            self._show(t, f"✅ Cleared {field}.")
            return
        try:
            q = parse_query(text, self.today)
        except ValueError as e:
            self._show(t, f"⚠️ {e}. Try: 6 Nov, 18-20 Dec")
            return
        if not q:
            self._show(t, f"⚠️ Couldn't read '{text}'. {TIP}")
            return
        if field == "dates" and "dates" not in q:
            self._show(t, f"⚠️ No date in '{text}'. Try: 6 Nov, 18-20 Dec")
            return
        for k, v in q.items():
            t[k] = v
        if "dates" in q:
            self.lookups.append(t["id"])
        self.meta["awaiting"] = {"id": t["id"], "field": "any"}
        names = {"dates": "dates", "times": "time", "formats": "format", "theatres": "theatres"}
        self._show(t, "✅ Updated " + ", ".join(names[k] for k in names if k in q) + ".")

    def _add(self, link: dict, label: str = "") -> None:
        existing = [t for t in self.trackers if t.get("event_code") == link["event_code"]]
        label = label or self.label_fn(link)
        self.meta["awaiting"] = {"field": "newcity", "link": link, "label": label}
        note = ("\nAlready tracking in: " + ", ".join(city_name(t["region_slug"]) for t in existing)) \
            if existing else ""
        self.tg.send(f"🎬 {label}\nWhich city should I watch?{note}",
                     city_buttons("nc:", link["region_slug"]))

    def _new_in_city(self, city: str, message_id) -> None:
        aw = self.meta.get("awaiting") or {}
        if aw.get("field") != "newcity" or city not in CITIES:
            self.tg.send("Send the link again, please.")
            return
        link = {**aw["link"], "region_slug": city}
        tid = f"{link['event_code'].lower()}-{city}"
        t = self._find(tid)
        note = "Already tracking this. " + TIP if t else "👀 Tracking started. " + TIP
        if not t:
            t = new_tracker(link, aw.get("label") or slug_label(link["slug"]))
            self.trackers.append(t)
            self.lookups.append(tid)
        self.meta["awaiting"] = {"id": tid, "field": "any"}
        self._show(t, note, message_id)

    def _add_district(self, d: dict) -> None:
        if not d["city"]:
            self.meta["awaiting"] = {"field": "district_city", "link": d}
            self.tg.send("That District link has no city. Which city? (e.g. Bengaluru)")
            return
        tid = f"{d['mv'].lower()}-{d['city']}"
        existing = self._find(tid)
        if existing:
            self.tg.send(f"Already tracking:\n{describe(existing)}")
            return
        t = {"id": tid, "source": "district", "label": slug_label(d["slug"]), "slug": d["slug"],
             "city": d["city"], "mv": d["mv"], "dates": [], "theatres": [], "times": [], "enabled": True}
        self.trackers.append(t)
        self.tg.send(f"👀 Tracking started:\n{describe(t)}\n\n"
                     "Heads-up: district.in refuses GitHub's servers, so this only works when "
                     "the watcher runs from another network (it will show as blocked in /list).")

    def _finish_district(self, aw: dict, text: str) -> None:
        city = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
        city = {"bangalore": "bengaluru", "bombay": "mumbai", "gurgaon": "gurugram"}.get(city, city)
        self.meta.pop("awaiting", None)
        if not city:
            self.tg.send("Send the District link again.")
            return
        self._add_district({**aw["link"], "city": city})

    def _command(self, text: str) -> None:
        cmd, _, arg = text.partition(" ")
        cmd = cmd.split("@")[0].lower()
        if cmd in ("/start", "/help"):
            self.tg.send(HELP)
        elif cmd == "/list":
            if not self.trackers:
                self.tg.send("Nothing tracked yet. Send me a BookMyShow movie link.")
                return
            lines = []
            for i, t in enumerate(self.trackers, 1):
                flag = "" if t.get("enabled", True) else " ⏸ paused"
                res = self.results.get(t["id"], "not checked yet")
                lines.append(f"{i}. {describe(t, self.catalog.get(t['id']))}{flag}\n   last: {res}")
            self.tg.send("\n\n".join(lines))
        elif cmd in ("/pause", "/resume", "/remove", "/edit"):
            t = self._find(arg)
            if not t:
                self.tg.send(f"Usage: {cmd} N  (N from /list)")
            elif cmd == "/edit" and t.get("source") == "district":
                self.tg.send("District trackers only alert when showtimes appear; nothing to edit.")
            elif cmd == "/edit":
                self.meta["awaiting"] = {"id": t["id"], "field": "any"}
                self._show(t, TIP)
            elif cmd == "/remove":
                self.trackers.remove(t)                 # the checker drops its state next run
                self._cards().pop(t["id"], None)
                self.tg.send(f"🗑 Stopped tracking {t['label']}.")
            else:
                t["enabled"] = cmd == "/resume"
                if t["enabled"]:
                    t["epoch"] = t.get("epoch", 0) + 1  # fresh start: alert again if already open
                self.tg.send(f"{'▶️ Resumed' if t['enabled'] else '⏸ Paused'} {t['label']}.")
        else:
            self.tg.send("Unknown command. /help")
