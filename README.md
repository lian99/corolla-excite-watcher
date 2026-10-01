# Corolla Excite watcher

Sends you a Telegram message whenever a new used **Toyota Corolla Excite** is listed on
[Toyota Select](https://toyota-select.co.il/corolla-excite/).

## How it works

1. Every hour, Windows Task Scheduler runs `watcher.py` on your computer, in the background
   (no window pops up).
2. The script asks the Toyota Select site for the Corolla Excite listings - the same request the
   web page makes in the background, one request per check.
3. It compares the cars against `seen_cars.json`, the list of cars it has already seen.
4. For each **new** car it sends you a Telegram message with the price, year, mileage, owner
   type, region and a link. If more than 5 new cars appear at once, you get one summary instead.

If the site can't be read, or returns zero cars (which may mean the website changed), you get
**one** warning message - not one every hour - and a "working again" message when it recovers.

**Why your PC and not GitHub?** The Toyota site blocks requests from cloud servers such as
GitHub Actions (HTTP 403), but allows normal home connections. The GitHub workflow is kept in
`.github/workflows/watch.yml` but **disabled**.

**If your computer is off or asleep**, checks are skipped; the next check after it wakes up
catches every car listed in the meantime (nothing is missed, alerts just arrive later).

## Files

| File             | What it is                                                         |
| ---------------- | ------------------------------------------------------------------ |
| `watcher.py`     | The watcher. Settings and filters are at the top.                  |
| `.env`           | Your Telegram bot token and chat ID. **Private - never upload it.** |
| `seen_cars.json` | Cars already seen. Delete it to start over.                        |
| `watcher.log`    | What happened on each check (newest at the bottom).                |

## Pause, change or stop

Open **Task Scheduler** (Start menu → type "Task Scheduler") → **Task Scheduler Library** →
**Corolla Excite watcher**:

- **Pause:** right-click → **Disable**. Right-click → **Enable** to resume.
- **Check right now:** right-click → **Run**.
- **Change how often:** double-click → **Triggers** tab → **Edit…** → change
  "Repeat task every" (e.g. 30 minutes or 3 hours). Please don't go below 15 minutes.
- **Stop for good:** right-click → **Delete**.

Or simply mute or block the bot in Telegram.

## Only get alerts for some cars

At the top of `watcher.py`:

```python
MAX_PRICE = None      # e.g. 120000  (full price in ₪)
MIN_YEAR = None       # e.g. 2021
MAX_MILEAGE = None    # e.g. 80000   (km)
```

Replace `None` with a number to turn a filter on. Cars that don't match are remembered silently.

## Telegram secrets

`.env` (next to `watcher.py`) contains:

```
TELEGRAM_BOT_TOKEN=123456789:AAH...
TELEGRAM_CHAT_ID=123456789
```

If the token ever leaks, send `/revoke` to @BotFather in Telegram and put the new token in `.env`.
To find your chat ID again: `py -3.14 watcher.py --get-chat-id` (after sending your bot a message).

## Running it by hand

From this folder:

```
py -3.14 watcher.py --test      # send a test message
py -3.14 watcher.py --dry-run   # show what it would send, without sending or saving
py -3.14 watcher.py             # a normal check
```
