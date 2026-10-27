"""Offline tests: fake Telegram, fake BookMyShow API, fake District. Run: python -m pytest -q

The conversation tests replay real chats, including the ones that went wrong on 26-27 Oct."""
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
NOW = datetime(2026, 10, 26, 12, 0, tzinfo=main.IST)
NOW_MORNING = datetime(2026, 10, 27, 9, 0, tzinfo=main.IST)
NOW_8PM = datetime(2026, 10, 27, 20, 5, tzinfo=main.IST)
OWNER, FRIEND = "42", "77"

DOOMSDAY = "https://in.bookmyshow.com/movies/bengaluru/avengers-doomsday/ET00439706"
DOOMSDAY_BUY = "https://in.bookmyshow.com/movies/bengaluru/avengers-doomsday/buytickets/ET00439706/"
RAMAYANA_KOL = "https://in.bookmyshow.com/movies/kolkata/namit-malhotras-ramayana-hindi/ET00451914"
RAMAYANA_SHARE = ("All that you would like to explore and know about the movie Namit Malhotra's Ramayana - "
                  f"(Hindi) {RAMAYANA_KOL} @Bookmyshow")
OPEN_CAT = {"name": "Namit Malhotra's Ramayana", "language": "Hindi  •  2D",
            "dates": [["20261105", "BOOKABLE"], ["20261106", "AVAILABLE"], ["20261109", "NOT_OPEN"]],
            "venues": ["INOX: City Center, Salt Lake", "PVR: Mani Square"], "formats": ["2D", "3D"],
            "screens": ["PXL"]}


# ── fakes ───────────────────────────────────────────────────────────────
class FakeTg:
    chat_id = OWNER

    def __init__(self):
        self.log = []           # (chat, text, buttons) of every new or edited message, in order
        self.sent = []          # (text, buttons) of new messages
        self.edited = []        # (message_id, text, buttons)
        self.edits = []         # (message_id, buttons) for button-only edits
        self.n = 100

    def send(self, text, buttons=None, on_id=None, chat=None):
        self.n += 1
        self.sent.append((text, buttons))
        self.log.append((str(chat or self.chat_id), text, buttons))
        if on_id:
            on_id(self.n)
        return self.n

    def edit(self, message_id, text, buttons=None, chat=None):
        self.edited.append((message_id, text, buttons))
        self.log.append((str(chat or self.chat_id), text, buttons))
        return True

    def edit_buttons(self, message_id, buttons, chat=None):
        self.edits.append((message_id, buttons))

    def answer(self, callback_id, text=""):
        pass

    @property
    def texts(self):
        return [t for t, _ in self.sent]

    @property
    def last(self):
        return self.log[-1][1]

    def last_to(self, who):
        return [t for c, t, _ in self.log if c == who][-1]

    @property
    def last_data(self):
        return {b[1] for row in (self.log[-1][2] or []) for b in row}


def msg(uid, text, chat_id=OWNER, **who):
    return {"update_id": uid, "message": {"message_id": uid, "chat": {"id": int(chat_id), "type": "private"},
                                          "from": who, "text": text}}


def tap(uid, data, chat_id=OWNER, message_id=99):
    return {"update_id": uid, "callback_query": {
        "id": f"cb{uid}", "data": data, "message": {"message_id": message_id, "chat": {"id": int(chat_id)}}}}


def api(venues, screen="", dates=None):
    """Minimal BookMyShow primary-dynamic payload with the given theatre names."""
    cards = [{"type": "venue-card", "additionalData": {"venueName": v, "venueCode": v[:4].upper()},
              "showtimes": [{"title": "07:20 PM", "screenAttr": screen, "additionalData": {
                  "sessionId": "1", "showDateCode": "20261218", "showTimeCode": "1920",
                  "categories": [{"priceDesc": "Gold", "curPrice": "300", "availStatus": "3"}]}}]}
             for v in venues]
    top = [{"type": "horizontal-block-list", "data": [
        {"id": d, "styleId": style, "data": [1, 2, 3]} for d, style in (dates or [])]}]
    return {"data": {"topStickyWidgets": top, "showtimeWidgets": [
        {"type": "groupList", "data": [{"type": "venueGroup", "data": cards}]}]}}


def make_chat(trackers=None, state=None, results=None, tg=None, catalog=None, today=TODAY, now=NOW):
    tg = tg or FakeTg()
    trackers = trackers if trackers is not None else []
    state = state if state is not None else {}

    def handle(update):
        c = chat.Chat(tg, trackers, state, today, results=results, catalog=catalog,
                      label_fn=lambda link: "Avengers: Doomsday", now=now)
        c.handle(update)
        handle.lookups += c.lookups
    handle.lookups = []
    return handle, tg, trackers, state


def me(state, who=OWNER):
    return state["users"][who]


# ── links and parsing ───────────────────────────────────────────────────
def test_parse_link_movie_page_and_buytickets_and_query():
    for url in (DOOMSDAY, DOOMSDAY_BUY, DOOMSDAY + "?utm=x"):
        link = chat.parse_link(f"check this {url} thanks")
        assert link == {"event_code": "ET00439706", "region_slug": "bengaluru", "slug": "avengers-doomsday"}
    assert chat.parse_link("https://example.com/movies/x/y/ET00439706") is None
    assert chat.parse_link("https://in.bookmyshow.com/movies/bengaluru") is None
    assert chat.share_title(RAMAYANA_SHARE) == "Namit Malhotra's Ramayana - (Hindi)"
    assert chat.link_extras(RAMAYANA_SHARE) == ""
    assert chat.link_extras(f"{RAMAYANA_SHARE}\n6, 7 Nov 3D PXL") == "6, 7 Nov 3D PXL"


def test_buy_and_page_urls():
    t = {"region_slug": "bengaluru", "slug": "avengers-doomsday", "event_code": "ET00439706"}
    assert chat.buy_url(t) == DOOMSDAY_BUY
    assert chat.page_url(t) == DOOMSDAY


@pytest.mark.parametrize("text,codes", [
    ("6, 7 Nov", ["20261106", "20261107"]),          # reported: only 7 Nov was kept
    ("6 7 nov", ["20261106", "20261107"]),
    ("6 & 7 Nov", ["20261106", "20261107"]),
    ("6th and 7th Nov", ["20261106", "20261107"]),
    ("Nov 6, 7", ["20261106", "20261107"]),
    ("6 nov, 7 nov", ["20261106", "20261107"]),
    ("6-8 nov", ["20261106", "20261107", "20261108"]),
    ("18-20 Dec, 25 December", ["20261218", "20261219", "20261220", "20261225"]),
    ("Dec 18th and 2027-01-02", ["20261218", "20270102"]),
    ("6/11", ["20261106"]),
    ("06-11-2026", ["20261106"]),
    ("tomorrow and 8 nov", ["20261027", "20261108"]),
    ("fri", ["20261030"]),
    ("this weekend", ["20261031", "20261101"]),
    ("5 Jan", ["20270105"]),                          # already past this year -> next year
])
def test_dates(text, codes):
    assert chat.parse_dates(text, TODAY) == codes


@pytest.mark.parametrize("bad", ["someday", "31 Feb", "20-10 Dec", "1-30 Dec", "6/13"])
def test_bad_dates(bad):
    with pytest.raises(ValueError):
        chat.parse_dates(bad, TODAY)


def test_bare_days_when_asked_for_dates():
    assert chat.bare_days("7 and 8", TODAY, ["20261105", "20261106"]) == ["20261107", "20261108"]
    assert chat.bare_days("28, 29", TODAY) == ["20261028", "20261029"]
    assert chat.bare_days("6, 7", TODAY) == ["20261106", "20261107"]        # past this month -> next
    assert chat.bare_days("PXL", TODAY) == []


def test_parse_query_one_line_requests():
    q = chat.parse_query
    assert q("6 Nov 7-8 PM PXL", TODAY) == {"times": ["1900-2000"], "dates": ["20261106"], "formats": ["pxl"]}
    assert q("7:50 PM mani square 3D PXL 6, 7 Nov", TODAY) == {
        "times": ["1950-2050"], "dates": ["20261106", "20261107"], "formats": ["3d pxl"],
        "theatres": ["mani square"]}
    assert q("kolkata IMAX 3D at South City or Mani Square", TODAY) == {
        "formats": ["imax 3d"], "city": "kolkata", "theatres": ["south city", "mani square"]}
    assert q("18-20 Dec, evening and night", TODAY) == {
        "times": ["evening", "night"], "dates": ["20261218", "20261219", "20261220"]}
    assert q("after 9 pm", TODAY) == {"times": ["2100-2400"]}
    assert q("11-1 pm", TODAY) == {"times": ["1100-1300"]}
    assert q("Satyam Cinemas", TODAY) == {"theatres": ["satyam"]}
    assert q("", TODAY) == {}


def test_fmt_time_and_range_filter():
    assert chat.fmt_time("1900-2000") == "7-8 pm" and chat.fmt_time("1100-1300") == "11 am-1 pm"
    assert chat.fmt_time("2100-2400") == "9 pm-midnight" and chat.fmt_time("evening") == "Evening"
    shows = main.parse_shows(api(["PVR Forum"]))                         # one show at 19:20
    assert main.filter_shows(shows, "", "1900-2000", "") == shows
    assert main.filter_shows(shows, "", "2000-2100", "") == []


# ── conversation ────────────────────────────────────────────────────────
def test_link_asks_city_then_card_waits_for_done():
    handle, tg, trackers, state = make_chat()
    handle(msg(1, RAMAYANA_SHARE))
    assert trackers == [] and "Which city" in tg.last and "Namit Malhotra's Ramayana" in tg.last
    assert tg.log[-1][2][0][0] == ("Bengaluru", "nc:bengaluru")          # home city first
    handle(tap(2, "nc:bengaluru"))
    t = trackers[0]
    assert t["id"] == "et00451914-bengaluru" and t["confirmed"] is False
    assert "Checking BookMyShow" in tg.last and chat.NOT_YET in tg.last
    assert me(state)["cards"][t["id"]]["id"] == 99 and handle.lookups == [t["id"]]
    assert "set:et00451914-bengaluru:done" in tg.last_data


def test_link_with_details_in_the_same_message_skips_questions():
    handle, tg, trackers, state = make_chat()
    handle(msg(1, f"{RAMAYANA_SHARE}\nkolkata 6, 7 Nov 3D PXL mani square evening"))
    t = trackers[0]
    assert t["region_slug"] == "kolkata" and t["dates"] == ["20261106", "20261107"]
    assert (t["formats"], t["theatres"], t["times"]) == (["3d pxl"], ["mani square"], ["evening"])
    assert "Which city" not in " ".join(tg.texts) and chat.NOT_YET in tg.last


def test_link_then_details_as_a_second_message():
    """Reported: the link and the details arrived as two messages and only the link was used."""
    handle, tg, trackers, state = make_chat()
    handle(msg(1, RAMAYANA_KOL))
    handle(msg(2, "6 & 7 Nov 3D PXL"))
    assert trackers == [] and "Got: 📅 Fri 6 Nov, Sat 7 Nov · 🎞 3D PXL" in tg.last and "Which city" in tg.last
    handle(tap(3, "nc:kolkata"))
    assert trackers[0]["dates"] == ["20261106", "20261107"] and trackers[0]["formats"] == ["3d pxl"]
    handle(msg(4, RAMAYANA_KOL))
    handle(msg(5, "bangalore"))                                         # city typed instead of tapped
    assert trackers[1]["region_slug"] == "bengaluru"


def test_details_before_the_link_are_kept_for_ten_minutes():
    handle, tg, trackers, state = make_chat()
    handle(msg(1, "kolkata 6 nov imax"))
    assert "Now send the BookMyShow link" in tg.last
    handle(msg(2, RAMAYANA_KOL))
    t = trackers[0]
    assert (t["region_slug"], t["dates"], t["formats"]) == ("kolkata", ["20261106"], ["imax"])
    handle2, tg2, trackers2, state2 = make_chat()
    handle2(msg(1, "6 nov"))
    me(state2)["prefill"]["at"] = "2026-10-26T11:00:00+05:30"            # an hour old
    handle2(msg(2, RAMAYANA_KOL))
    assert trackers2 == [] and "Which city" in tg2.last


def test_done_with_open_bookings_asks_for_dates_first():
    """Reported: it alerted for tomorrow's shows though no date was chosen."""
    handle, tg, trackers, state = make_chat(catalog={"et00451914-kolkata": OPEN_CAT})
    handle(msg(1, RAMAYANA_KOL))
    handle(tap(2, "nc:kolkata"))
    handle(tap(3, "set:et00451914-kolkata:done"))
    t = trackers[0]
    assert t["confirmed"] is False and "Which dates do you want" in tg.last
    assert "dt:et00451914-kolkata:20261106" in tg.last_data
    handle(tap(4, "dt:et00451914-kolkata:20261106"))
    handle(tap(5, "set:et00451914-kolkata:done"))
    assert t["confirmed"] is True and t["dates"] == ["20261106"] and "Watching" in tg.last
    assert tg.last_data == {"set:et00451914-kolkata:edit"}


def test_done_rechecks_and_restarts_alerts_when_filters_changed():
    """Reported: changing dates didn't trigger a check."""
    handle, tg, trackers, state = make_chat()
    handle(msg(1, f"{DOOMSDAY} 18 Dec"))
    handle(tap(2, "nc:bengaluru"))
    handle(tap(3, "set:et00439706-bengaluru:done"))
    t = trackers[0]
    epoch = t["epoch"]
    handle.lookups.clear()
    handle(tap(4, "set:et00439706-bengaluru:edit"))
    handle(msg(5, "19, 20 Dec"))
    assert t["dates"] == ["20261219", "20261220"] and "Tap ✅ Done" in tg.last
    handle(tap(6, "set:et00439706-bengaluru:done"))
    assert t["epoch"] == epoch + 1 and handle.lookups[-1] == t["id"]     # fresh check, fresh alert
    handle(tap(7, "set:et00439706-bengaluru:edit"))
    handle(tap(8, "set:et00439706-bengaluru:done"))
    assert t["epoch"] == epoch + 1                                       # nothing changed: same epoch


def test_editing_dates_by_buttons_and_by_typing():
    handle, tg, trackers, state = make_chat(catalog={"et00451914-kolkata": OPEN_CAT})
    handle(msg(1, RAMAYANA_KOL))
    handle(tap(2, "nc:kolkata"))
    handle(tap(3, "set:et00451914-kolkata:dates"))
    labels = [b[0] for row in tg.log[-1][2] for b in row]
    assert labels[:3] == ["Thu 5 Nov", "Fri 6 Nov", "⏳ Mon 9 Nov"]
    handle(tap(4, "dt:et00451914-kolkata:20261106"))
    handle(tap(5, "dt:et00451914-kolkata:20261105"))
    assert trackers[0]["dates"] == ["20261105", "20261106"]
    handle(msg(6, "7 and 8"))                                            # bare days, month from BookMyShow
    assert trackers[0]["dates"] == ["20261107", "20261108"]
    handle(tap(7, "set:et00451914-kolkata:dates"))
    handle(msg(8, "someday"))
    assert "No date in 'someday'" in tg.last and trackers[0]["dates"] == ["20261107", "20261108"]


def test_typed_line_validates_against_bookmyshow():
    handle, tg, trackers, state = make_chat(catalog={"et00451914-kolkata": OPEN_CAT},
                                            results={"et00451914-kolkata": "0 matching showtimes"})
    handle(msg(1, RAMAYANA_KOL))
    handle(tap(2, "nc:kolkata"))
    handle(msg(3, "6 Nov 7-8 PM 4DX mani square, south city"))
    assert "Fri 6 Nov (open)" in tg.last and "4DX (not listed yet)" in tg.last
    assert "mani square (showing it)" in tg.last and "south city (INOX: South City, Kolkata, not showing" in tg.last
    assert "Updated dates, time, format, theatres" in tg.last
    assert tg.sent[-1][0] == tg.last and tg.edits[-1] == (99, [])        # fresh card; old one stripped


def test_theatre_format_time_and_city_buttons():
    handle, tg, trackers, state = make_chat(catalog={"et00451914-kolkata": OPEN_CAT})
    handle(msg(1, RAMAYANA_KOL))
    handle(tap(2, "nc:kolkata"))
    handle(tap(3, "set:et00451914-kolkata:theatres"))
    handle(tap(4, "vn:et00451914-kolkata:1"))
    assert trackers[0]["theatres"] == ["pvr: mani square"]
    handle(tap(5, "set:et00451914-kolkata:formats"))
    assert {"fm:et00451914-kolkata:0", "fm:et00451914-kolkata:2"} <= tg.last_data   # 2D, 3D, PXL
    handle(tap(6, "fm:et00451914-kolkata:2"))
    assert trackers[0]["formats"] == ["pxl"]
    handle(tap(7, "set:et00451914-kolkata:times"))
    handle(tap(8, "tm:et00451914-kolkata:night"))
    assert trackers[0]["times"] == ["night"]
    handle(tap(9, "set:et00451914-kolkata:city"))
    handle(tap(10, "ct:et00451914-kolkata:bengaluru"))
    t = trackers[0]
    assert t["id"] == "et00451914-bengaluru" and t["theatres"] == [] and "Switched to Bengaluru" in tg.last
    assert "et00451914-bengaluru" in me(state)["cards"]


def test_commands_list_pause_resume_remove_edit():
    handle, tg, trackers, state = make_chat(results={"et00439706-bengaluru": "no showtimes listed yet"})
    handle(msg(1, DOOMSDAY))
    handle(tap(2, "nc:bengaluru"))
    handle(msg(3, "/list"))
    assert "1. Avengers: Doomsday (Bengaluru) (not started: tap Done)" in tg.last
    handle(msg(4, "/pause 1"))
    handle(msg(5, "/list"))
    assert trackers[0]["enabled"] is False and "⏸ paused" in tg.last
    handle(msg(6, "/resume 1"))
    assert trackers[0]["enabled"] is True and trackers[0]["epoch"] == 1      # checker starts fresh
    handle(msg(7, "/edit 1"))
    assert "set:et00439706-bengaluru:dates" in tg.last_data
    handle(msg(8, "/remove 1"))
    assert trackers == [] and me(state)["cards"] == {}
    handle(msg(9, "/remove 9"))
    assert "Usage" in tg.last


def test_stray_text_without_a_card_points_to_help():
    handle, tg, _, _ = make_chat()
    handle(msg(1, "hello"))
    assert "Send me a BookMyShow movie link" in tg.last


# ── several people ──────────────────────────────────────────────────────
def test_a_new_person_needs_the_owners_ok_then_has_their_own_trackers():
    """Reported: another account got no reply at all."""
    handle, tg, trackers, state = make_chat()
    handle(msg(1, DOOMSDAY, chat_id=FRIEND, first_name="Tanu", username="tanu"))
    assert "private bot" in tg.last_to(FRIEND) and trackers == []
    assert "Tanu (@tanu) wants to use" in tg.last_to(OWNER)
    handle(msg(2, "hello?", chat_id=FRIEND))
    assert "Still waiting" in tg.last_to(FRIEND)
    handle(tap(3, f"ok:{FRIEND}", chat_id=FRIEND))                       # only the owner can approve
    assert FRIEND not in state["allowed"]
    handle(tap(4, f"ok:{FRIEND}"))
    assert FRIEND in state["allowed"] and "You're in" in tg.last_to(FRIEND)
    handle(msg(5, DOOMSDAY, chat_id=FRIEND))
    handle(tap(6, "nc:bengaluru", chat_id=FRIEND))
    handle(msg(7, DOOMSDAY))
    handle(tap(8, "nc:bengaluru"))
    ids = [t["id"] for t in trackers]
    assert ids == [f"et00439706-bengaluru-{FRIEND}", "et00439706-bengaluru"]
    assert trackers[0]["chat"] == FRIEND and "chat" not in trackers[1]
    handle(msg(9, "/list", chat_id=FRIEND))
    assert tg.last_to(FRIEND).count("Avengers") == 1
    handle(tap(10, "set:et00439706-bengaluru:done", chat_id=FRIEND))     # can't touch the owner's
    assert "no longer exists" in tg.last_to(FRIEND) and trackers[1]["confirmed"] is False
    handle(msg(11, f"/revoke {FRIEND}"))
    assert FRIEND not in state["allowed"] and [t["id"] for t in trackers] == ["et00439706-bengaluru"]


def test_old_chat_state_moves_under_the_owner():
    state = {"awaiting": {"id": "x", "field": "any"}, "cards": {"x": {"id": 5}}}
    chat.migrate_state(state, OWNER)
    assert state == {"users": {OWNER: {"awaiting": {"id": "x", "field": "any"}, "cards": {"x": {"id": 5}}}},
                     "allowed": [], "pending": {}}


def test_group_chats_are_ignored():
    handle, tg, trackers, _ = make_chat()
    update = msg(1, DOOMSDAY)
    update["message"]["chat"]["type"] = "group"
    handle(update)
    assert tg.log == [] and trackers == []


# ── District ────────────────────────────────────────────────────────────
def test_district_link_with_and_without_city():
    handle, tg, trackers, state = make_chat()
    handle(msg(1, "https://www.district.in/movies/namit-malhotra-s-ramayana-movie-tickets-in-kolkata-MV176276"))
    assert trackers[0]["source"] == "district" and trackers[0]["city"] == "kolkata"
    handle(msg(2, "https://www.district.in/movies/avengers-doomsday-movie-tickets-MV176735"))
    assert me(state)["awaiting"]["field"] == "district_city" and len(trackers) == 1
    handle(msg(3, "Bangalore"))
    assert trackers[1]["id"] == "mv176735-bengaluru" and "refuses GitHub" in tg.last


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
    t["confirmed"] = True
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


def test_check_bms_any_date_fetches_every_open_day():
    seen = []

    def fetch(code, dc, *a):
        seen.append(dc)
        return api(["PVR Forum"], dates=[("20261105", "date-selected"), ("20261106", "date-default"),
                                         ("20261107", "date-default"), ("20261108", "date-disabled")])
    main.check_bms(doomsday_tracker(), {}, fetch=fetch)
    assert seen == ["", "20261106", "20261107"]
    seen.clear()
    main.check_bms(doomsday_tracker(dates=["20261107"]), {}, fetch=fetch)
    assert seen == ["20261107"]


def test_check_bms_theatre_filter_and_no_data():
    t = doomsday_tracker(theatres=["pvr"])
    r = main.check_bms(t, {}, fetch=lambda *a: api(["INOX Garuda"]))
    assert r["ok"] and r["state"]["shows"] == {} and r["changes"] == []
    r = main.check_bms(t, r["state"], fetch=lambda *a: api(["INOX Garuda", "PVR Forum"]))
    assert "BOOKINGS OPEN" in r["changes"][0]
    assert main.check_bms(t, {}, fetch=lambda *a: None)["ok"] is False


def test_check_bms_covers_every_format_and_matches_screens():
    """The real miss: 7:50 PM PXL at Mani Square was listed under the 3D event, not the 2D link."""
    def payload(venue, screen):
        d = api([venue], screen=screen)
        d["data"]["bottomSheetData"] = {"format-selector": {"widgets": [{"type": "chip-list", "text": "Hindi", "data": [
            {"title": "2D", "cta": {"additionalData": {"eventCode": "ET2D"}}},
            {"title": "3D", "cta": {"additionalData": {"eventCode": "ET3D"}}}]}]}}
        return d
    feeds = {"ET2D": payload("INOX Quest", ""), "ET3D": payload("PVR: Mani Square Mall", "PXL")}
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


@pytest.fixture
def workdir(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    return tmp_path


def read(name):
    return json.loads(Path(name).read_text(encoding="utf-8"))


def write(name, data):
    Path(name).write_text(json.dumps(data), encoding="utf-8")


def test_run_reads_trackers_but_never_writes_them(workdir):
    write("trackers.json", [doomsday_tracker()])
    before = Path("trackers.json").read_bytes()
    tg = FakeTg()
    main.run(now=NOW_MORNING, tg=tg, fetch=lambda *a: api([]))
    assert Path("trackers.json").read_bytes() == before
    assert read("meta.json")["results"]["et00439706-bengaluru"] == "no showtimes listed yet"
    assert tg.sent == []


def test_run_alerts_the_tracker_owner_immediately_and_only_once(workdir):
    write("trackers.json", [doomsday_tracker(), doomsday_tracker(id="f", chat=FRIEND)])
    tg = FakeTg()
    main.run(now=NOW_MORNING, tg=tg, fetch=lambda *a: api(["PVR Forum"]))
    assert {c for c, _, _ in tg.log} == {FRIEND, OWNER}
    assert "BOOKINGS OPEN" in tg.texts[0] and DOOMSDAY_BUY in tg.texts[0]
    tg2 = FakeTg()
    main.run(now=NOW_MORNING, tg=tg2, fetch=lambda *a: api(["PVR Forum"]))
    assert tg2.sent == []


def test_run_never_alerts_before_done(workdir):
    t = doomsday_tracker(confirmed=False)
    write("trackers.json", [t])
    tg = FakeTg()
    main.run(now=NOW_MORNING, tg=tg, fetch=lambda *a: api(["PVR Forum"]))
    assert tg.sent == [] and read("meta.json")["catalog"][t["id"]]["venues"] == ["PVR Forum"]
    assert read("bms_state.json") == {}                                  # nothing remembered yet
    t["confirmed"] = True                                               # Done tapped
    write("trackers.json", [t])
    main.run(now=NOW_MORNING, tg=tg, fetch=lambda *a: api(["PVR Forum"]))
    assert len(tg.sent) == 1 and "BOOKINGS OPEN" in tg.texts[0]


def test_run_drops_state_of_removed_trackers_and_restarts_on_new_epoch(workdir):
    t = doomsday_tracker()
    write("trackers.json", [t])
    write("bms_state.json", {"gone-tracker": {"shows": {"x": {}}}})
    tg = FakeTg()
    main.run(now=NOW_MORNING, tg=tg, fetch=lambda *a: api(["PVR Forum"]))
    assert list(read("bms_state.json")) == ["et00439706-bengaluru"] and len(tg.sent) == 1
    main.run(now=NOW_MORNING, tg=tg, fetch=lambda *a: api(["PVR Forum"]))
    assert len(tg.sent) == 1                                   # already alerted
    t["epoch"] = 1                                             # /resume, or Done after changing filters
    write("trackers.json", [t])
    main.run(now=NOW_MORNING, tg=tg, fetch=lambda *a: api(["PVR Forum"]))
    assert len(tg.sent) == 2 and "BOOKINGS OPEN" in tg.texts[-1]


def test_run_daily_note_once_at_8pm_per_person(workdir):
    write("trackers.json", [doomsday_tracker(), doomsday_tracker(id="f", chat=FRIEND),
                            doomsday_tracker(id="draft", confirmed=False)])
    nothing = lambda *a: api([])  # noqa: E731
    tg = FakeTg()
    main.run(now=NOW_MORNING, tg=tg, fetch=nothing)
    assert tg.sent == []                                       # 9 am: silent
    main.run(now=NOW_8PM, tg=tg, fetch=nothing)
    assert {c for c, _, _ in tg.log} == {FRIEND, OWNER}
    assert all("Still watching 1 tracker" in t for t in tg.texts)
    main.run(now=NOW_8PM.replace(minute=15), tg=tg, fetch=nothing)
    assert len(tg.sent) == 2                                   # not again the same day


def test_run_blocked_warning_once_after_three_failures(workdir):
    write("trackers.json", [doomsday_tracker()])
    tg = FakeTg()
    for _ in range(5):
        main.run(now=NOW_MORNING, tg=tg, fetch=lambda *a: None)
    assert sum("Can't read BookMyShow" in t for t in tg.texts) == 1


def test_run_district_blocked_is_not_a_bookmyshow_failure_and_alerts_when_open(workdir):
    d = {"id": "mv1-bengaluru", "source": "district", "label": "Film", "slug": "film", "city": "bengaluru",
         "mv": "MV1", "dates": [], "theatres": [], "times": [], "enabled": True}
    write("trackers.json", [d])
    tg = FakeTg()
    for _ in range(4):
        main.run(now=NOW_MORNING, tg=tg, district_check=lambda t: (False, "District blocked this network (HTTP 403)", False))
    assert tg.sent == [] and read("meta.json")["results"]["mv1-bengaluru"].startswith("District blocked")
    main.run(now=NOW_MORNING, tg=tg, district_check=lambda t: (True, "40 sessions listed", True))
    assert len(tg.sent) == 1 and "BOOKINGS OPEN on District" in tg.texts[0]


def test_run_does_not_rewrite_files_when_nothing_changed(workdir):
    write("trackers.json", [doomsday_tracker()])
    main.run(now=NOW_MORNING, tg=FakeTg(), fetch=lambda *a: api([]))
    before = {n: Path(n).stat().st_mtime_ns for n in ("trackers.json", "meta.json", "bms_state.json")}
    main.run(now=NOW_MORNING, tg=FakeTg(), fetch=lambda *a: api([]))
    assert before == {n: Path(n).stat().st_mtime_ns for n in before}


def test_run_refreshes_each_persons_card_only_when_it_changes(workdir):
    t, f = doomsday_tracker(formats=["pxl"]), doomsday_tracker(id="f", chat=FRIEND, confirmed=False)
    write("trackers.json", [t, f])
    write("chat.json", {"users": {OWNER: {"cards": {t["id"]: {"id": 7, "done": True}}},
                                  FRIEND: {"cards": {"f": {"id": 8}}}}})
    tg = FakeTg()
    main.run(now=NOW_MORNING, tg=tg, fetch=lambda *a: api([]))
    assert {(m, c) for (m, _, _), (c, _, _) in zip(tg.edited, tg.log)} == {(7, OWNER), (8, FRIEND)}
    assert chat.NOT_YET in [x for c, x, _ in tg.log if c == FRIEND][0]
    main.run(now=NOW_MORNING, tg=tg, fetch=lambda *a: api([]))
    assert len(tg.edited) == 2                                     # unchanged: no edit
    main.run(now=NOW_MORNING, tg=tg, fetch=lambda *a: api(["INOX Garuda"]))
    assert "PXL (not listed yet)" in tg.edited[-2][1] and "Bookings OPEN" in tg.edited[-2][1]


def test_run_only_checks_one_tracker_and_skips_the_daily_note(workdir):
    a, b = doomsday_tracker(), doomsday_tracker(id="other", region_slug="kolkata")
    write("trackers.json", [a, b])
    tg = FakeTg()
    main.run(now=NOW_8PM, tg=tg, fetch=lambda *a: api([]), only="other")
    assert list(read("meta.json")["results"]) == ["other"] and tg.sent == []
    assert "last_heartbeat" not in read("meta.json")
