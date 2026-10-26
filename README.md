# BMS Ticket Notifier (Telegram)

Fork of [aviiciii/bms-ticket-notifier](https://github.com/aviiciii/bms-ticket-notifier), extended to be run
entirely from Telegram. GitHub Actions runs it every 10 minutes for free; nothing runs on a PC.

## Use it

Send the bot a **BookMyShow movie link** (or BookMyShow's share text, which carries the movie name).

1. It asks **which city** (Bengaluru first; the city in a link is just where it was shared from).
2. It shows one **setup card** with the live status from BookMyShow: open or not, which dates, how many theatres.
3. Narrow it with the buttons (dates and theatres come from what BookMyShow lists) or type it in one line:
   `6 Nov 7-8 pm 3D PXL Mani Square`, `18-20 Dec evening`, `after 9 pm`, `IMAX 3D at South City`.
   Formats (2D, 3D, IMAX 3D...) are separate listings on BookMyShow; every one is checked, and
   screens like PXL / IMAX / 4DX are format filters. Theatre names are checked against `cinemas.json`
   (every cinema per city; refresh with `uv run cinemas.py`). Typed dates and theatres are
   checked against BookMyShow ("pxl (not listed yet)" means it will alert when PXL lists shows).
4. When something matches you get a message immediately. If nothing is available you get one note a day at 8 pm IST.

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
