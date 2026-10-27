"""Relay tests: fake GitHub store + fake Telegram."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import relay  # noqa: E402
from test_bot import DOOMSDAY, TODAY, FakeTg, msg, tap  # noqa: E402

ENV = {"WEBHOOK_SECRET": "s3cret", "TELEGRAM_BOT_TOKEN": "x", "TELEGRAM_CHAT_ID": "42",
       "GITHUB_TOKEN": "x", "GITHUB_REPO": "o/r"}
LABEL = lambda link: "Avengers: Doomsday"  # noqa: E731
TID = "et00439706-bengaluru"


class FakeStore:
    def __init__(self, files=None, conflicts=0):
        self.files = {k: (v, f"sha-{k}") for k, v in (files or {}).items()}
        self.writes = []
        self.dispatched = []
        self.conflicts = conflicts

    def get(self, path, default):
        data, sha = self.files.get(path, (default, None))
        return json.loads(json.dumps(data)), sha

    def put(self, path, data, sha, message):
        if self.conflicts:
            self.conflicts -= 1
            raise relay.Conflict(path)
        assert sha == self.files.get(path, (None, None))[1]
        new_sha = f"sha-{path}-{len(self.writes)}"
        self.files[path] = (json.loads(json.dumps(data)), new_sha)
        self.writes.append(path)
        return new_sha

    def dispatch(self, tracker_id):
        self.dispatched.append(tracker_id)
        return True


def start(store, tg):
    """Link, then pick Bengaluru on the city buttons."""
    relay.process(msg(1, DOOMSDAY), store, tg, TODAY, label_fn=LABEL)
    relay.process(tap(2, "nc:bengaluru"), store, tg, TODAY)


def test_link_asks_city_then_creates_tracker_and_starts_a_check():
    store, tg = FakeStore({"meta.json": {"results": {}}}), FakeTg()
    relay.process(msg(1, DOOMSDAY), store, tg, TODAY, label_fn=LABEL)
    assert store.writes == ["chat.json"] and "Which city" in tg.last
    relay.process(tap(2, "nc:bengaluru"), store, tg, TODAY)
    assert store.files["trackers.json"][0][0]["id"] == TID
    assert store.dispatched == [TID] and "Checking BookMyShow" in tg.last
    assert store.files["chat.json"][0]["users"]["42"]["cards"][TID]["id"] == 99   # the tapped message is the card


def test_typed_text_round_trip_and_new_card_id_is_saved():
    store, tg = FakeStore(), FakeTg()
    start(store, tg)
    relay.process(msg(3, "18, 19 Dec 7 pm"), store, tg, TODAY)
    t = store.files["trackers.json"][0][0]
    assert t["dates"] == ["20261218", "20261219"] and t["times"] == ["1900-2000"]
    assert store.files["chat.json"][0]["users"]["42"]["cards"][TID]["id"] == tg.n   # fresh card remembered
    relay.process(tap(4, f"set:{TID}:done"), store, tg, TODAY)
    assert store.files["trackers.json"][0][0]["confirmed"] is True
    assert store.dispatched[-1] == TID                                    # Done: check again now


def test_list_reads_results_written_by_the_checker():
    store = FakeStore({"trackers.json": [], "meta.json": {"results": {TID: "no showtimes listed yet"}}})
    tg = FakeTg()
    start(store, tg)
    relay.process(msg(3, "/list"), store, tg, TODAY)
    assert "no showtimes listed yet" in tg.last
    assert "meta.json" not in store.writes                      # never written by the relay


def test_conflict_is_retried_without_double_replies():
    store, tg = FakeStore(conflicts=1), FakeTg()
    relay.process(msg(1, DOOMSDAY), store, tg, TODAY, label_fn=LABEL)
    assert len(tg.sent) == 1 and store.writes == ["chat.json"]


def test_webhook_rejects_wrong_secret_and_ignores_strangers():
    store, tg = FakeStore(), FakeTg()
    raw = json.dumps(msg(1, DOOMSDAY)).encode()
    assert relay.handle_webhook("wrong", raw, env=ENV, store=store, tg=tg) == 403
    assert relay.handle_webhook("", raw, env={**ENV, "WEBHOOK_SECRET": ""}, store=store, tg=tg) == 403
    assert relay.handle_webhook("s3cret", b"not json", env=ENV, store=store, tg=tg) == 400
    stranger = json.dumps(msg(1, DOOMSDAY, chat_id=666)).encode()
    assert relay.handle_webhook("s3cret", stranger, env=ENV, store=store, tg=tg) == 200
    assert "trackers.json" not in store.files and "private bot" in tg.last_to("666")   # asks the owner
    assert relay.handle_webhook("s3cret", raw, env=ENV, store=store, tg=tg, label_fn=LABEL) == 200
    assert "Which city" in tg.last_to("42")


def test_webhook_reports_save_failure_to_the_owner():
    class Broken(FakeStore):
        def put(self, *a):
            raise RuntimeError("github down")
    tg = FakeTg()
    status = relay.handle_webhook("s3cret", json.dumps(msg(1, DOOMSDAY)).encode(), env=ENV,
                                  store=Broken(), tg=tg, label_fn=LABEL)
    assert status == 200 and tg.texts == ["⚠️ Couldn't save that (RuntimeError). Please try again."]


def test_new_person_gets_an_answer_and_the_owner_an_allow_button():
    store, tg = FakeStore(), FakeTg()
    raw = json.dumps(msg(1, DOOMSDAY, chat_id="77", first_name="Tanu")).encode()
    assert relay.handle_webhook("s3cret", raw, env=ENV, store=store, tg=tg) == 200
    assert "private bot" in tg.last_to("77") and "Tanu wants to use" in tg.last_to("42")
    assert store.files["chat.json"][0]["pending"] == {"77": "Tanu"}


def test_tick_starts_a_full_run_only_with_the_secret():
    store = FakeStore()
    assert relay.handle_tick("wrong", env={**ENV, "TICK_SECRET": "t1ck"}, store=store) == 403
    assert relay.handle_tick("", env=ENV, store=store) == 403                  # no secret configured
    assert relay.handle_tick("t1ck", env={**ENV, "TICK_SECRET": "t1ck"}, store=store) == 200
    assert store.dispatched == [""]
