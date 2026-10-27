"""Telegram webhook relay: answers chat instantly and stores trackers in this GitHub repo.

Runs as a Vercel function (api/telegram.py, deployed by deploy-relay.sh). It owns
trackers.json and chat.json and only reads meta.json (for /list). The GitHub Actions
checker (main.py) owns meta.json and bms_state.json, so the two never edit the same file.

New trackers (and date/city changes) start a one-tracker checker run right away
(workflow_dispatch), because BookMyShow doesn't answer Vercel's servers; that run fills in the
setup card. The token needs Contents: write and Actions: write on this repo.

Env: TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, WEBHOOK_SECRET, GITHUB_TOKEN, GITHUB_REPO.
"""
from __future__ import annotations

import base64
import hmac
import json
import os
from datetime import datetime

import requests

from chat import Chat, Telegram, slug_label
from main import IST, REGION_MAP


class Conflict(Exception):
    """Someone else wrote the file between our read and our write."""


class GitHubStore:
    def __init__(self, repo: str, token: str, branch: str = "main"):
        self.url = f"https://api.github.com/repos/{repo}/contents"
        self.branch = branch
        self.headers = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
                        "X-GitHub-Api-Version": "2022-11-28"}

    def get(self, path: str, default):
        r = requests.get(f"{self.url}/{path}", params={"ref": self.branch}, headers=self.headers, timeout=15)
        if r.status_code == 404:
            return default, None
        r.raise_for_status()
        body = r.json()
        return json.loads(base64.b64decode(body["content"]).decode("utf-8") or "null") or default, body["sha"]

    def put(self, path: str, data, sha: str | None, message: str) -> str:
        payload = {"message": message, "branch": self.branch, "content": base64.b64encode(
            (json.dumps(data, indent=2, ensure_ascii=False) + "\n").encode("utf-8")).decode("ascii")}
        if sha:
            payload["sha"] = sha
        r = requests.put(f"{self.url}/{path}", json=payload, headers=self.headers, timeout=15)
        if r.status_code in (409, 422):
            raise Conflict(path)
        r.raise_for_status()
        return r.json()["content"]["sha"]

    def dispatch(self, tracker_id: str) -> bool:
        """Start a checker run for one tracker now. False if the token can't (needs Actions: write)."""
        try:
            r = requests.post(f"{self.url.rsplit('/contents', 1)[0]}/actions/workflows/bms-checker.yml/dispatches",
                              json={"ref": self.branch, "inputs": {"only": tracker_id}},
                              headers=self.headers, timeout=10)
            return r.status_code == 204
        except requests.RequestException:
            return False


class Buffered:
    """Holds replies until the new state is saved, so a retried update never double-replies."""

    def __init__(self, chat_id: str):
        self.chat_id = chat_id
        self.calls: list[tuple[str, tuple]] = []

    def send(self, text, buttons=None, on_id=None):
        self.calls.append(("send", (text, buttons, on_id)))

    def edit(self, message_id, text, buttons=None):
        self.calls.append(("edit", (message_id, text, buttons)))
        return True

    def edit_buttons(self, message_id, buttons):
        self.calls.append(("edit_buttons", (message_id, buttons)))

    def answer(self, callback_id, text=""):
        self.calls.append(("answer", (callback_id,)))

    def flush(self, tg) -> None:
        for name, args in self.calls:
            getattr(tg, name)(*args)


def _dump(x) -> str:
    return json.dumps(x, sort_keys=True)


def process(update: dict, store, tg, today, label_fn=None, attempts: int = 3) -> None:
    for attempt in range(attempts):
        trackers, t_sha = store.get("trackers.json", [])
        chat_state, c_sha = store.get("chat.json", {})
        meta, _ = store.get("meta.json", {})
        before_t, before_c = _dump(trackers), _dump(chat_state)
        out = Buffered(tg.chat_id)
        # BookMyShow pages don't answer Vercel either; the checker fills in the real name later.
        kw = {"label_fn": label_fn or (lambda link: slug_label(link["slug"]))}
        chat = Chat(out, trackers, chat_state, today, results=meta.get("results", {}),
                    catalog=meta.get("catalog", {}), region_ok=lambda slug: slug in REGION_MAP, **kw)
        chat.handle(update)
        try:
            if _dump(trackers) != before_t:
                store.put("trackers.json", trackers, t_sha, "chat: update trackers [skip ci]")
            if _dump(chat_state) != before_c:
                c_sha = store.put("chat.json", chat_state, c_sha, "chat: update conversation [skip ci]")
        except Conflict:
            if attempt == attempts - 1:
                raise
            continue
        if chat.lookups and hasattr(store, "dispatch"):
            store.dispatch(chat.lookups[-1])
        saved = _dump(chat_state)
        out.flush(tg)                          # may record new setup-card message ids
        if _dump(chat_state) != saved:
            try:
                store.put("chat.json", chat_state, c_sha, "chat: remember setup card [skip ci]")
            except Conflict:
                pass                           # a newer chat.json already won; the next card fixes it
        return


def handle_webhook(secret_header: str, raw: bytes, env=os.environ, store=None, tg=None,
                   label_fn=None) -> int:
    """HTTP status to return. Always 200 for real Telegram calls, so Telegram doesn't retry forever."""
    expected = env.get("WEBHOOK_SECRET", "")
    if not expected or not hmac.compare_digest(secret_header or "", expected):
        return 403
    try:
        update = json.loads(raw or b"{}")
    except ValueError:
        return 400
    tg = tg or Telegram(env["TELEGRAM_BOT_TOKEN"], env["TELEGRAM_CHAT_ID"])
    store = store or GitHubStore(env["GITHUB_REPO"], env["GITHUB_TOKEN"])
    try:
        process(update, store, tg, datetime.now(IST).date(), label_fn=label_fn)
    except Exception as e:  # noqa: BLE001
        print(f"relay error: {e!r}")
        msg = update.get("message") or (update.get("callback_query") or {}).get("message") or {}
        if str((msg.get("chat") or {}).get("id", "")) == str(tg.chat_id):
            tg.send(f"⚠️ Couldn't save that ({type(e).__name__}). Please try again.")
    return 200


def handle_tick(key: str, env=os.environ, store=None) -> int:
    """GET /api/telegram?tick=<TICK_SECRET>, called every 10 minutes by an outside cron
    (cron-job.org): starts a full checker run. GitHub's own schedule fires only now and then."""
    expected = env.get("TICK_SECRET", "")
    if not expected or not hmac.compare_digest(key or "", expected):
        return 403
    store = store or GitHubStore(env["GITHUB_REPO"], env["GITHUB_TOKEN"])
    return 200 if store.dispatch("") else 502
