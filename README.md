# Corolla Excite watcher

Sends you a Telegram message whenever a new used **Toyota Corolla Excite** is listed on
[Toyota Select](https://toyota-select.co.il/corolla-excite/).

## How it works

1. Every hour, GitHub Actions runs `watcher.py` on GitHub's computers (yours can be off).
2. The script asks the Toyota Select site for the Corolla Excite listings (the same request the
   web page makes in the background - one request per check).
3. It compares the cars against `seen_cars.json`, the list of cars it has already seen.
4. For each **new** car it sends you a Telegram message with the price, year, mileage,
   owner type, region and a link. If more than 5 new cars appear at once, you get one summary
   message instead.
5. It saves the updated `seen_cars.json` back into this repo, so the next run remembers.

If the site can't be read, or returns zero cars (which may mean the website changed), you get
**one** warning message - not one every hour - and a "working again" message when it recovers.

## Secrets

The bot token and chat ID are stored as GitHub secrets, never in the code:
**Settings → Secrets and variables → Actions**

| Secret               | What it is                                        |
| -------------------- | ------------------------------------------------- |
| `TELEGRAM_BOT_TOKEN` | The token @BotFather gave you                     |
| `TELEGRAM_CHAT_ID`   | Your chat ID with the bot (`python watcher.py --get-chat-id`) |

If the token ever leaks, send `/revoke` to @BotFather in Telegram, then update the secret.

## Run it now

**Actions** tab → **Corolla Excite watcher** → **Run workflow** → **Run workflow**.

## Change how often it checks

Edit `.github/workflows/watch.yml` (on GitHub: open the file, click the pencil icon) and change
the `cron` line. The five fields are *minute hour day month weekday*, in **UTC** (Israel is UTC+2,
or UTC+3 in summer).

| You want                       | cron line            |
| ------------------------------ | -------------------- |
| Every hour (current)           | `"17 * * * *"`       |
| Every 30 minutes               | `"*/30 * * * *"`     |
| Every 3 hours                  | `"17 */3 * * *"`     |
| Hourly, 06:00–22:00 Israel time | `"17 4-20 * * *"`    |

GitHub may start scheduled runs a few minutes late; that's normal. Please don't go below every
15 minutes - be polite to the site.

## Only get alerts for some cars

At the top of `watcher.py`:

```python
MAX_PRICE = None      # e.g. 120000  (full price in ₪)
MIN_YEAR = None       # e.g. 2021
MAX_MILEAGE = None    # e.g. 80000   (km)
```

Replace `None` with a number to turn a filter on. Cars that don't match are remembered silently.

## Pause or stop it

- **Pause:** Actions tab → Corolla Excite watcher → **"…"** (top right) → **Disable workflow**.
  Click **Enable workflow** to resume.
- **Mute:** mute the bot's chat in Telegram.
- **Stop for good:** delete this repo, or block the bot in Telegram.

## Run it on your own computer

Needs Python 3.8+ and nothing else. Create a file named `.env` next to `watcher.py`
(it's ignored by git, so it's never uploaded):

```
TELEGRAM_BOT_TOKEN=123456789:AAH...
TELEGRAM_CHAT_ID=123456789
```

Then:

```
python watcher.py --test      # send a test message
python watcher.py --dry-run   # show what it would send, without sending or saving
python watcher.py             # a normal check
```

To start over (e.g. get the "watcher is active" message again), delete `seen_cars.json`.
