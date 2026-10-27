# BMS Ticket Notifier (Telegram)

Fork of [aviiciii/bms-ticket-notifier](https://github.com/aviiciii/bms-ticket-notifier), extended to be run
entirely from Telegram. GitHub Actions runs it every 10 minutes for free; nothing runs on a PC.

## Use it

Send the bot a **BookMyShow movie link** (or BookMyShow's share text). Details can go in the same
message or the next one, e.g. `kolkata 6, 7 Nov 3D PXL Mani Square evening`.

1. If it can't tell the **city** it asks (the city in a link is just where it was shared from).
2. It shows one **setup card** with the live status from BookMyShow: open or not, which dates and
   theatres, which formats (2D, 3D, IMAX 3D...) and screens (PXL, IMAX...).
3. Adjust with the buttons or by typing (`6, 7 Nov`, `7 and 8`, `fri`, `this weekend`, `after 9 pm`,
   `IMAX 3D at South City`). Typed dates, theatres and formats are checked against BookMyShow and
   against `cinemas.json` (every cinema per city).
4. Tap **✅ Done** to start watching. Nothing alerts before that. If bookings are already open and no
   dates are picked, Done first asks which dates. Done always runs a fresh check.
5. You get a message the moment matching shows appear, and one note a day at 8 pm IST.

**Other people** can use the same bot: when someone new messages it, the owner gets an Allow button.
Each person has their own trackers and alerts. Owner commands: `/users`, `/revoke ID`.

| Command | What it does |
|---|---|
| `/list` | everything being tracked, with the last result |
| `/edit N` | bring the setup card back |
| `/pause N`, `/resume N`, `/remove N` | N is the number from `/list` |

Replies are instant: Telegram calls `relay.py` on Vercel (`deploy-relay.sh`), which stores trackers in this repo.
BookMyShow doesn't answer Vercel, so for a new tracker the relay starts a one-tracker checker run
(GitHub Actions), which fills in the card within about a minute.

**District links** are understood (`district.in/movies/...-movie-tickets-in-<city>-MV...`) but district.in
returns 403 to GitHub's servers, so those trackers show as blocked unless the watcher runs from another network.

## Setup

1. Fork this repo. Create a bot with @BotFather.
2. Repo secrets: `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`. Only that chat can control the bot.
3. Enable the workflow under Actions. Optional email alerts: `RESEND_API_KEY`, `RESEND_TO_EMAIL`, `RESEND_FROM_EMAIL`.

## Files

| File | Purpose |
|---|---|
| `main.py` | BookMyShow API, change detection, the run loop |
| `chat.py` | Telegram conversation, link/date parsing |
| `district.py` | District link parsing and page check |
| `trackers.json`, `bms_state.json`, `meta.json` | what is tracked, last seen showtimes, bookkeeping (committed by the workflow) |

Supported cities: see `REGION_MAP` in `main.py`. Tests: `python -m pytest -q`.
