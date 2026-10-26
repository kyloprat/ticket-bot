"""Offline tests: fake Telegram, fake BookMyShow API, fake District. Run: python -m pytest -q"""
import json
import sys
from datetime import date, datetime
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import chat  # noqa: E402
import district  # noqa: E402
import main  # noqa: E402

TODAY = date(2026, 10, 26)
NOW_MORNING = datetime(2026, 10, 27, 9, 0, tzinfo=main.IST)
NOW_8PM = datetime(2026, 10, 27, 20, 5, tzinfo=main.IST)

DOOMSDAY = "https://in.bookmyshow.com/movies/bengaluru/avengers-doomsday/ET00439706"
DOOMSDAY_BUY = "https://in.bookmyshow.com/movies/bengaluru/avengers-doomsday/buytickets/ET00439706/"


# ── fakes ───────────────────────────────────────────────────────────────
class FakeTg:
    chat_id = "42"

    def __init__(self):
        self.sent = []          # (text, buttons)
        self.edits = []         # (message_id, buttons) for button-only edits
        self.edited = []        # (message_id, text, buttons)
        self.log = []           # (text, buttons) of every new or edited message, in order

    def send(self, text, buttons=None, on_id=None):
        self.sent.append((text, buttons))
        self.log.append((text, buttons))
        if on_id:
            on_id(100 + len(self.sent))
        return 100 + len(self.sent)

    def edit(self, message_id, text, buttons=None):
        self.edited.append((message_id, text, buttons))
        self.log.append((text, buttons))
        return True

    def edit_buttons(self, message_id, buttons):
        self.edits.append((message_id, buttons))

    def answer(self, callback_id, text=""):
        pass

    @property
    def texts(self):
        return [t for t, _ in self.sent]

    @property
    def last(self):
        """Latest text the user sees (a new message or an edited card)."""
        return self.log[-1][0]

    @property
    def last_data(self):
        return {b[1] for row in (self.log[-1][1] or []) for b in row}


def msg(uid, text, chat_id=42):
    return {"update_id": uid, "message": {"message_id": uid, "chat": {"id": chat_id}, "text": text}}


def tap(uid, data, chat_id=42):
    return {"update_id": uid, "callback_query": {
        "id": f"cb{uid}", "data": data, "message": {"message_id": 99, "chat": {"id": chat_id}}}}


def api(venues):
    """Minimal BookMyShow primary-dynamic payload with the given theatre names."""
    cards = [{"type": "venue-card", "additionalData": {"venueName": v, "venueCode": v[:4].upper()},
              "showtimes": [{"title": "07:20 PM", "additionalData": {
                  "sessionId": "1", "showDateCode": "20261218", "showTimeCode": "1920",
                  "categories": [{"priceDesc": "Gold", "curPrice": "300", "availStatus": "3"}]}}]}
             for v in venues]
    return {"data": {"topStickyWidgets": [], "showtimeWidgets": [
        {"type": "groupList", "data": [{"type": "venueGroup", "data": cards}]}]}}


def make_chat(trackers=None, meta=None, results=None, tg=None, catalog=None):
    tg = tg or FakeTg()
    c = chat.Chat(tg, trackers if trackers is not None else [], meta if meta is not None else {},
                  TODAY, results=results, catalog=catalog, label_fn=lambda link: "Avengers: Doomsday")
    return c, tg


RAMAYANA_KOL = "https://in.bookmyshow.com/movies/kolkata/namit-malhotras-ramayana-hindi/ET00451914"
RAMAYANA_SHARE = ("All that you would like to explore and know about the movie Namit Malhotra's Ramayana - "
                  f"(Hindi) {RAMAYANA_KOL} @Bookmyshow")
OPEN_CAT = {"name": "Namit Malhotra's Ramayana", "language": "Hindi  •  2D",
            "dates": [["20261105", "BOOKABLE"], ["20261106", "AVAILABLE"], ["20261109", "NOT_OPEN"]],
            "venues": ["INOX: City Center, Salt Lake", "PVR: Mani Square"]}


# ── links and dates ─────────────────────────────────────────────────────
def test_parse_link_movie_page_and_buytickets_and_query():
    for url in (DOOMSDAY, DOOMSDAY_BUY, DOOMSDAY + "?utm=x"):
        link = chat.parse_link(f"check this {url} thanks")
        assert link == {"event_code": "ET00439706", "region_slug": "bengaluru", "slug": "avengers-doomsday"}
    assert chat.parse_link("https://example.com/movies/x/y/ET00439706") is None
    assert chat.parse_link("https://in.bookmyshow.com/movies/bengaluru") is None
    assert chat.share_title(RAMAYANA_SHARE) == "Namit Malhotra's Ramayana - (Hindi)"


def test_buy_and_page_urls():
    t = {"region_slug": "bengaluru", "slug": "avengers-doomsday", "event_code": "ET00439706"}
    assert chat.buy_url(t) == DOOMSDAY_BUY
    assert chat.page_url(t) == DOOMSDAY


def test_parse_dates():
    assert chat.parse_dates("18 Dec", TODAY) == ["20261218"]
    assert chat.parse_dates("18-20 Dec, 25 December", TODAY) == ["20261218", "20261219", "20261220", "20261225"]
    assert chat.parse_dates("Dec 18th and 2027-01-02", TODAY) == ["20261218", "20270102"]
    assert chat.parse_dates("20261218", TODAY) == ["20261218"]
    assert chat.parse_dates("5 Jan", TODAY) == ["20270105"]        # already past this year -> next year
    assert chat.parse_dates("tomorrow", TODAY) == ["20261027"]
    for bad in ("someday", "31 Feb", "20-10 Dec", "1-30 Dec"):
        with pytest.raises(ValueError):
            chat.parse_dates(bad, TODAY)


def test_parse_query_one_line_requests():
    q = chat.parse_query
    assert q("6 Nov 7-8 PM PXL", TODAY) == {"times": ["1900-2000"], "dates": ["20261106"], "formats": ["pxl"]}
    assert q("7:50 PM mani square 3D PXL 6 Nov", TODAY) == {
        "times": ["1950-2050"], "dates": ["20261106"], "formats": ["3d pxl"], "theatres": ["mani square"]}
    assert q("IMAX 3D at South City or Mani Square", TODAY) == {
        "formats": ["imax 3d"], "theatres": ["south city", "mani square"]}
    assert q("18-20 Dec, evening and night", TODAY) == {
        "times": ["evening", "night"], "dates": ["20261218", "20261219", "20261220"]}
    assert q("PVR Orion, INOX", TODAY) == {"theatres": ["pvr orion", "inox"]}
    assert q("after 9 pm", TODAY) == {"times": ["2100-2400"]}
    assert q("7 pm on Dec 18", TODAY) == {"times": ["1900-2000"], "dates": ["20261218"]}
    assert q("11-1 pm", TODAY) == {"times": ["1100-1300"]}
    assert q("", TODAY) == {}
    with pytest.raises(ValueError):
        q("31 Feb", TODAY)


def test_fmt_time_and_range_filter():
    assert chat.fmt_time("1900-2000") == "7-8 pm" and chat.fmt_time("1100-1300") == "11 am-1 pm"
    assert chat.fmt_time("2100-2400") == "9 pm-midnight" and chat.fmt_time("evening") == "Evening"
    shows = main.parse_shows(api(["PVR Forum"]))                         # one show at 19:20
    assert main.filter_shows(shows, "", "1900-2000", "") == shows
    assert main.filter_shows(shows, "", "2000-2100", "") == []


# ── conversation ────────────────────────────────────────────────────────
def test_link_asks_city_then_shows_one_card():
    trackers, meta = [], {}
    c, tg = make_chat(trackers, meta)
    c.handle(msg(1, RAMAYANA_SHARE))
    assert trackers == [] and "Which city" in tg.last and "Namit Malhotra's Ramayana" in tg.last
    assert tg.last_data >= {"nc:bengaluru", "nc:kolkata"}
    assert tg.log[-1][1][0][0] == ("Bengaluru", "nc:bengaluru")          # home city first
    c.handle(tap(2, "nc:bengaluru"))
    assert [t["id"] for t in trackers] == ["et00451914-bengaluru"]
    assert tg.edited[-1][0] == 99 and "📍 Bengaluru" in tg.last and "Checking BookMyShow" in tg.last
    assert meta["cards"]["et00451914-bengaluru"]["id"] == 99 and c.lookups == ["et00451914-bengaluru"]
    assert tg.last_data == {f"set:et00451914-bengaluru:{x}" for x in ("dates", "theatres", "formats", "times", "city", "done")}


def test_typed_line_updates_everything_and_validates_against_bookmyshow():
    trackers, meta = [], {}
    c, tg = make_chat(trackers, meta, catalog={"et00451914-kolkata": OPEN_CAT},
                      results={"et00451914-kolkata": "0 matching showtimes"})
    c.handle(msg(1, RAMAYANA_KOL))
    c.handle(tap(2, "nc:kolkata"))
    assert "🟢 Bookings OPEN: 5 Nov - 6 Nov · 2 theatre(s)" in tg.last
    c.handle(tap(3, "set:et00451914-kolkata:dates"))
    c.handle(msg(4, "6 Nov 7-8 PM PXL mani square"))                    # what was rejected before
    t = trackers[0]
    assert (t["dates"], t["times"], t["formats"], t["theatres"]) == (
        ["20261106"], ["1900-2000"], ["pxl"], ["mani square"])
    assert "6 Nov (open)" in tg.last and "PXL (not listed yet)" in tg.last and "7-8 pm" in tg.last
    assert "mani square (showing it)" in tg.last
    assert "Updated dates, time, format, theatres" in tg.last
    assert tg.sent[-1][0] == tg.last and tg.edits[-1] == (99, [])        # fresh card; old one stripped
    assert meta["cards"][t["id"]]["id"] == 100 + len(tg.sent)
    c.handle(msg(5, "someday soon"))
    assert t["theatres"] == ["someday soon"]                             # free text = theatre name
    c.handle(tap(6, "set:et00451914-kolkata:dates"))
    c.handle(msg(7, "someday"))
    assert "No date in 'someday'" in tg.last and t["dates"] == ["20261106"]


def test_date_and_theatre_buttons_come_from_bookmyshow():
    trackers, meta = [], {}
    c, tg = make_chat(trackers, meta, catalog={"et00451914-kolkata": OPEN_CAT})
    c.handle(msg(1, RAMAYANA_KOL))
    c.handle(tap(2, "nc:kolkata"))
    c.handle(tap(3, "set:et00451914-kolkata:dates"))
    labels = [b[0] for row in tg.log[-1][1] for b in row]
    assert labels[:3] == ["5 Nov", "6 Nov", "⏳ 9 Nov"]
    c.handle(tap(4, "dt:et00451914-kolkata:20261106"))
    assert trackers[0]["dates"] == ["20261106"] and "✅ 6 Nov" in {b[0] for row in tg.log[-1][1] for b in row}
    c.handle(tap(5, "set:et00451914-kolkata:theatres"))
    c.handle(tap(6, "vn:et00451914-kolkata:1"))
    assert trackers[0]["theatres"] == ["pvr: mani square"] and "pvr: mani square (showing it)" in tg.last
    c.handle(tap(7, "vn:et00451914-kolkata:1"))
    assert trackers[0]["theatres"] == []
    c.handle(tap(8, "set:et00451914-kolkata:times"))
    c.handle(tap(9, "tm:et00451914-kolkata:night"))
    c.handle(msg(10, "7-8 pm"))
    assert trackers[0]["times"] == ["1900-2000"]
    c.handle(tap(11, "set:et00451914-kolkata:done"))
    assert "Saved" in tg.last and tg.last_data == {"set:et00451914-kolkata:edit"}
    assert meta["cards"]["et00451914-kolkata"]["done"] is True


def test_city_button_moves_tracker():
    trackers, meta = [], {}
    c, tg = make_chat(trackers, meta)
    c.handle(msg(1, RAMAYANA_KOL))
    c.handle(tap(2, "nc:kolkata"))
    c.handle(tap(3, "set:et00451914-kolkata:city"))
    c.handle(tap(4, "ct:et00451914-kolkata:bengaluru"))
    assert trackers[0]["id"] == "et00451914-bengaluru" and trackers[0]["region_slug"] == "bengaluru"
    assert "et00451914-bengaluru" in meta["cards"] and "Switched to Bengaluru" in tg.last
    c.handle(msg(5, RAMAYANA_KOL))
    assert "Already tracking in: Bengaluru" in tg.last


def test_same_city_twice_does_not_duplicate():
    trackers = []
    c, tg = make_chat(trackers)
    for i in (1, 3):
        c.handle(msg(i, DOOMSDAY))
        c.handle(tap(i + 1, "nc:bengaluru"))
    assert len(trackers) == 1 and "Already tracking this" in tg.last


def test_commands_list_pause_resume_remove_edit():
    trackers, meta = [], {}
    c, tg = make_chat(trackers, meta, results={"et00439706-bengaluru": "no showtimes listed yet"})
    c.handle(msg(1, DOOMSDAY))
    c.handle(tap(2, "nc:bengaluru"))
    c.handle(msg(3, "/list"))
    assert "1. Avengers: Doomsday (Bengaluru)" in tg.last and "no showtimes listed yet" in tg.last
    c.handle(msg(4, "/pause 1"))
    c.handle(msg(5, "/list"))
    assert trackers[0]["enabled"] is False and "⏸ paused" in tg.last
    c.handle(msg(6, "/resume 1"))
    assert trackers[0]["enabled"] is True and trackers[0]["epoch"] == 1      # checker starts fresh
    c.handle(msg(7, "/edit 1"))
    assert "Avengers: Doomsday" in tg.last and "set:et00439706-bengaluru:dates" in tg.last_data
    c.handle(msg(8, "/remove 1"))
    assert trackers == [] and meta["cards"] == {}
    c.handle(msg(9, "/remove 9"))
    assert "Usage" in tg.last


def test_other_chats_are_ignored():
    trackers = []
    c, tg = make_chat(trackers)
    c.handle(msg(1, DOOMSDAY, chat_id=666))
    c.handle(tap(2, "nc:bengaluru", chat_id=666))
    assert trackers == [] and tg.log == []


def test_stray_text_without_a_card_points_to_help():
    c, tg = make_chat()
    c.handle(msg(1, "hello"))
    assert "Send me a BookMyShow movie link" in tg.last


def test_district_link_with_and_without_city():
    trackers, meta = [], {}
    c, tg = make_chat(trackers, meta)
    c.handle(msg(1, "https://www.district.in/movies/namit-malhotra-s-ramayana-movie-tickets-in-kolkata-MV176276"))
    assert trackers[0]["source"] == "district" and trackers[0]["city"] == "kolkata"
    assert trackers[0]["slug"] == "namit-malhotra-s-ramayana" and trackers[0]["mv"] == "MV176276"
    c.handle(msg(2, "https://www.district.in/movies/avengers-doomsday-movie-tickets-MV176735"))
    assert meta["awaiting"]["field"] == "district_city" and len(trackers) == 1
    c.handle(msg(3, "Bangalore"))
    assert trackers[1]["id"] == "mv176735-bengaluru" and "awaiting" not in meta
    assert "refuses GitHub" in tg.texts[-1]


def test_district_parse_and_url():
    link = district.parse_district_link(
        "https://www.district.in/movies/namit-malhotra-s-ramayana-movie-tickets-in-bengaluru-MV176276?frmtid=rtb7dmwvuo")
    assert link == {"slug": "namit-malhotra-s-ramayana", "city": "bengaluru", "mv": "MV176276"}
    assert district.district_url(link).endswith("namit-malhotra-s-ramayana-movie-tickets-in-bengaluru-MV176276")
    assert district.parse_district_link("https://www.district.in/movies/") is None


def test_district_check_reads_sessions():
    def page(body):
        class R:
            status_code = 200
            text = f'<html><script id="__NEXT_DATA__" type="application/json">{json.dumps(body)}</script></html>'
        return lambda url, **kw: R()

    t = {"slug": "x", "city": "bengaluru", "mv": "MV1"}
    closed = {"props": {"isReleased": False, "release_date": "2026-12-18"}}
    live = {"languageFormatGroups": [{"number_of_sessions": 83}, {"number_of_sessions": 4}]}
    assert district.check_district(t, get=page(closed)) == (True, "no showtimes listed yet (release 2026-12-18)", False)
    assert district.check_district(t, get=page(live)) == (True, "87 sessions listed", True)

    class Blocked:
        status_code, text = 403, "denied"
    ok, result, is_open = district.check_district(t, get=lambda url, **kw: Blocked())
    assert (ok, is_open) == (False, False) and "HTTP 403" in result


# ── checks and the run loop ─────────────────────────────────────────────
def doomsday_tracker(**kw):
    t = chat.new_tracker(chat.parse_link(DOOMSDAY), "Avengers: Doomsday")
    t.update(kw)
    return t


def test_check_bms_first_showtimes_alert_then_quiet_then_new_theatre():
    t = doomsday_tracker()
    r = main.check_bms(t, {}, fetch=lambda *a: api([]))
    assert r["ok"] and r["result"] == "no showtimes listed yet" and r["changes"] == []
    r = main.check_bms(t, {}, fetch=lambda *a: api(["PVR Forum", "INOX Garuda"]))
    assert "BOOKINGS OPEN" in r["changes"][0]
    r2 = main.check_bms(t, r["state"], fetch=lambda *a: api(["PVR Forum", "INOX Garuda"]))
    assert r2["changes"] == []
    r3 = main.check_bms(t, r["state"], fetch=lambda *a: api(["PVR Forum", "INOX Garuda", "Cinepolis Orion"]))
    assert any("NEW" in c and "Cinepolis" in c for c in r3["changes"])


def test_check_bms_theatre_filter_and_no_data():
    t = doomsday_tracker(theatres=["pvr"])
    r = main.check_bms(t, {}, fetch=lambda *a: api(["INOX Garuda"]))
    assert r["ok"] and r["state"]["shows"] == {} and r["changes"] == []
    r = main.check_bms(t, r["state"], fetch=lambda *a: api(["INOX Garuda", "PVR Forum"]))
    assert "BOOKINGS OPEN" in r["changes"][0]
    assert main.check_bms(t, {}, fetch=lambda *a: None)["ok"] is False


@pytest.fixture
def workdir(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    return tmp_path


def read(name):
    return json.loads(Path(name).read_text(encoding="utf-8"))


def test_run_reads_trackers_but_never_writes_them_and_ignores_chat(workdir):
    Path("trackers.json").write_text(json.dumps([doomsday_tracker()]))
    before = Path("trackers.json").read_bytes()
    tg = FakeTg()
    main.run(now=NOW_MORNING, tg=tg, fetch=lambda *a: api([]))
    assert Path("trackers.json").read_bytes() == before
    assert read("meta.json")["results"]["et00439706-bengaluru"] == "no showtimes listed yet"
    assert tg.sent == []                                       # chat is the relay's job now


def test_run_drops_state_of_removed_trackers_and_restarts_on_resume(workdir):
    t = doomsday_tracker()
    Path("trackers.json").write_text(json.dumps([t]))
    Path("bms_state.json").write_text(json.dumps({"gone-tracker": {"shows": {"x": {}}}}))
    tg = FakeTg()
    main.run(now=NOW_MORNING, tg=tg, fetch=lambda *a: api(["PVR Forum"]))
    assert list(read("bms_state.json")) == ["et00439706-bengaluru"] and len(tg.sent) == 1
    main.run(now=NOW_MORNING, tg=tg, fetch=lambda *a: api(["PVR Forum"]))
    assert len(tg.sent) == 1                                   # already alerted
    t["epoch"] = 1                                             # /resume in chat
    Path("trackers.json").write_text(json.dumps([t]))
    main.run(now=NOW_MORNING, tg=tg, fetch=lambda *a: api(["PVR Forum"]))
    assert len(tg.sent) == 2 and "BOOKINGS OPEN" in tg.texts[-1]


def test_run_alerts_immediately_and_only_once(workdir):
    Path("trackers.json").write_text(json.dumps([doomsday_tracker()]))
    tg = FakeTg()
    main.run(now=NOW_MORNING, tg=tg, fetch=lambda *a: api(["PVR Forum"]))
    assert len(tg.sent) == 1 and "BOOKINGS OPEN" in tg.texts[0] and DOOMSDAY_BUY in tg.texts[0]
    tg2 = FakeTg()
    main.run(now=NOW_MORNING, tg=tg2, fetch=lambda *a: api(["PVR Forum"]))
    assert tg2.sent == []


def test_run_daily_note_once_at_8pm_only(workdir):
    Path("trackers.json").write_text(json.dumps([doomsday_tracker()]))
    nothing = lambda *a: api([])  # noqa: E731
    tg = FakeTg()
    main.run(now=NOW_MORNING, tg=tg, fetch=nothing)
    assert tg.sent == []                                       # 9 am: silent
    main.run(now=NOW_8PM, tg=tg, fetch=nothing)
    assert len(tg.sent) == 1 and "Still watching 1 tracker" in tg.texts[0]
    assert "Avengers: Doomsday: no showtimes listed yet" in tg.texts[0]
    main.run(now=NOW_8PM.replace(minute=15), tg=tg, fetch=nothing)
    assert len(tg.sent) == 1                                   # not again the same day
    main.run(now=NOW_8PM.replace(day=28), tg=tg, fetch=nothing)
    assert len(tg.sent) == 2                                   # next day


def test_run_blocked_warning_once_after_three_failures(workdir):
    Path("trackers.json").write_text(json.dumps([doomsday_tracker()]))
    tg = FakeTg()
    for _ in range(5):
        main.run(now=NOW_MORNING, tg=tg, fetch=lambda *a: None)
    assert sum("Can't read BookMyShow" in t for t in tg.texts) == 1


def test_run_district_blocked_is_not_a_bookmyshow_failure_and_alerts_when_open(workdir):
    d = {"id": "mv1-bengaluru", "source": "district", "label": "Film", "slug": "film", "city": "bengaluru",
         "mv": "MV1", "dates": [], "theatres": [], "times": [], "enabled": True}
    Path("trackers.json").write_text(json.dumps([d]))
    tg = FakeTg()
    for _ in range(4):
        main.run(now=NOW_MORNING, tg=tg, district_check=lambda t: (False, "District blocked this network (HTTP 403)", False))
    assert tg.sent == [] and read("meta.json")["results"]["mv1-bengaluru"].startswith("District blocked")
    main.run(now=NOW_MORNING, tg=tg, district_check=lambda t: (True, "40 sessions listed", True))
    assert len(tg.sent) == 1 and "BOOKINGS OPEN on District" in tg.texts[0]
    main.run(now=NOW_MORNING, tg=tg, district_check=lambda t: (True, "40 sessions listed", True))
    assert len(tg.sent) == 1


def test_run_does_not_rewrite_files_when_nothing_changed(workdir):
    Path("trackers.json").write_text(json.dumps([doomsday_tracker()]))
    main.run(now=NOW_MORNING, tg=FakeTg(), fetch=lambda *a: api([]))
    before = {n: Path(n).stat().st_mtime_ns for n in ("trackers.json", "meta.json", "bms_state.json")}
    main.run(now=NOW_MORNING, tg=FakeTg(), fetch=lambda *a: api([]))
    assert before == {n: Path(n).stat().st_mtime_ns for n in before}


def test_run_saves_catalog_and_refreshes_the_card_only_when_it_changes(workdir):
    t = doomsday_tracker(formats=["pxl"])
    Path("trackers.json").write_text(json.dumps([t]))
    Path("chat.json").write_text(json.dumps({"cards": {t["id"]: {"id": 7, "done": False}}}))
    tg = FakeTg()
    main.run(now=NOW_MORNING, tg=tg, fetch=lambda *a: api([]))
    assert read("meta.json")["catalog"][t["id"]]["venues"] == []
    assert tg.edited[-1][0] == 7 and "Bookings not open yet" in tg.edited[-1][1]
    main.run(now=NOW_MORNING, tg=tg, fetch=lambda *a: api([]))
    assert len(tg.edited) == 1                                     # unchanged: no edit
    main.run(now=NOW_MORNING, tg=tg, fetch=lambda *a: api(["INOX Garuda"]))
    assert read("meta.json")["catalog"][t["id"]]["venues"] == ["INOX Garuda"]
    assert "PXL (not listed yet)" in tg.edited[-1][1] and "Bookings OPEN" in tg.edited[-1][1]
    assert tg.sent == []                                           # pxl not listed: no alert yet


def test_run_only_checks_one_tracker_and_skips_the_daily_note(workdir):
    a, b = doomsday_tracker(), doomsday_tracker(id="other", region_slug="kolkata")
    Path("trackers.json").write_text(json.dumps([a, b]))
    tg = FakeTg()
    main.run(now=NOW_8PM, tg=tg, fetch=lambda *a: api([]), only="other")
    assert list(read("meta.json")["results"]) == ["other"] and tg.sent == []
    assert "last_heartbeat" not in read("meta.json")


def test_check_bms_covers_every_format_and_matches_screens():
    """The real miss: 7:50 PM PXL at Mani Square was listed under the 3D event, not the 2D link."""
    def payload(code, venue, screen):
        d = api([venue])
        d["data"]["showtimeWidgets"][0]["data"][0]["data"][0]["showtimes"][0]["screenAttr"] = screen
        d["data"]["bottomSheetData"] = {"format-selector": {"widgets": [{"type": "chip-list", "text": "Hindi", "data": [
            {"title": "2D", "cta": {"additionalData": {"eventCode": "ET2D"}}},
            {"title": "3D", "cta": {"additionalData": {"eventCode": "ET3D"}}}]}]}}
        return d
    feeds = {"ET2D": payload("ET2D", "INOX Quest", ""), "ET3D": payload("ET3D", "PVR: Mani Square Mall", "PXL")}
    t = doomsday_tracker(event_code="ET2D", formats=["3d pxl"])
    r = main.check_bms(t, {}, fetch=lambda code, *a: feeds[code])
    assert r["catalog"]["formats"] == ["2D", "3D"] and r["catalog"]["screens"] == ["PXL"]
    assert len(r["shows"]) == 1 and r["shows"][0].fmt == "3D"
    assert "BOOKINGS OPEN: 1 matching show" in r["changes"][0]
    assert "PVR: Mani Square Mall · 3D PXL" in r["changes"][1]
    card = chat.card_text(t, r["catalog"], r["result"])
    assert "3D PXL (showing)" in card and "Available: 2D, 3D · PXL" in card
    t2 = doomsday_tracker(event_code="ET2D", theatres=["mani square"], formats=["2d"])
    assert main.check_bms(t2, {}, fetch=lambda code, *a: feeds[code])["shows"] == []   # AND, not OR
