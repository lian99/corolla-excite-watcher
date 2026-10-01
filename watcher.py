#!/usr/bin/env python3
"""
Toyota Select - Corolla Excite watcher.

Checks https://toyota-select.co.il/corolla-excite/ for newly listed cars and
sends a Telegram message (from your own Telegram bot) for each new one.

Uses only the Python standard library - nothing to pip install.

Secrets come from environment variables (GitHub secrets in Actions, or a
local .env file that is never committed):
    TELEGRAM_BOT_TOKEN  the token @BotFather gave you, e.g. 123456789:AAH...
    TELEGRAM_CHAT_ID    your chat ID with the bot (find it with --get-chat-id)

Usage:
    python watcher.py                normal run
    python watcher.py --dry-run      print messages instead of sending; don't save state
    python watcher.py --test         send one test Telegram message and exit
    python watcher.py --get-chat-id  show the chat ID of whoever last messaged your bot
"""

# =============================================================================
# CONFIG - edit these to taste
# =============================================================================

# Optional filters. Leave as None to be alerted about every new car.
# Cars that don't match are still recorded as "seen", so they won't trigger
# an alert later if you loosen the filters.
MAX_PRICE = None      # e.g. 120000  (full price in ₪)
MIN_YEAR = None       # e.g. 2021
MAX_MILEAGE = None    # e.g. 80000   (km)

# If more than this many new cars appear in one run, send one summary message
# instead of one message per car.
MAX_INDIVIDUAL_MESSAGES = 5

# Seconds to wait between Telegram messages (Telegram allows ~1/second per chat).
SECONDS_BETWEEN_MESSAGES = 2

# Seconds to wait between requests to the Toyota site (be polite).
SECONDS_BETWEEN_SITE_REQUESTS = 2

# =============================================================================

import argparse
import html
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

CATALOG_URL = "https://toyota-select.co.il/corolla-excite/"
AJAX_URL = "https://toyota-select.co.il/wp-admin/admin-ajax.php"
CATEGORY_ID = "440"  # Corolla Excite category on toyota-select.co.il
TELEGRAM_API = "https://api.telegram.org/bot{token}/{method}"

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36"
)

HERE = Path(__file__).resolve().parent
DEFAULT_STATE_FILE = HERE / "seen_cars.json"

# Windows consoles default to a code page that can't print Hebrew.
try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except AttributeError:
    pass


def log(msg):
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}", flush=True)


class SiteError(Exception):
    """The site couldn't be fetched or its response no longer looks right."""


# -----------------------------------------------------------------------------
# Fetching & parsing
# -----------------------------------------------------------------------------

def http_request(url, data=None, headers=None, timeout=30):
    hdrs = {"User-Agent": USER_AGENT, "Accept-Language": "he-IL,he;q=0.9,en;q=0.8"}
    hdrs.update(headers or {})
    body = urllib.parse.urlencode(data).encode() if data is not None else None
    req = urllib.request.Request(url, data=body, headers=hdrs)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", errors="replace")


def clean_text(fragment):
    """Strip tags, decode entities, collapse whitespace."""
    text = re.sub(r"<[^>]+>", " ", fragment)
    text = html.unescape(text).replace("\xa0", " ")
    return re.sub(r"\s+", " ", text).strip()


def to_int(text):
    digits = re.sub(r"[^\d]", "", text or "")
    return int(digits) if digits else None


def parse_card(card_html):
    """Extract the fields we care about from one listing card's HTML."""
    car = {}

    m = re.search(r'data-product_id="(\d+)"', card_html)
    if not m:
        return None
    car["id"] = m.group(1)

    m = re.search(r'href="(https://toyota-select\.co\.il/cars/[^"]+)"', card_html)
    car["link"] = m.group(1) if m else f"https://toyota-select.co.il/?p={car['id']}"

    m = re.search(r'class="car-item-title">(.*?)</h3>', card_html, re.S)
    car["title"] = clean_text(m.group(1)) if m else "קורולה אקסייט"

    # Each detail is a <div class="car-item-detail"> with an icon telling us what it is.
    for icon, value in re.findall(
        r'class="car-item-detail">\s*<img[^>]*src="[^"]*/([\w-]+)\.svg"[^>]*>(.*?)</div>',
        card_html, re.S,
    ):
        value = clean_text(value)
        if icon == "catalog-agency":
            car["dealer"] = value
        elif icon == "scala":
            car["mileage"] = to_int(value)
        elif icon == "cal":
            car["year"] = to_int(value)
        elif icon == "owner_number":
            car["hand"] = value  # e.g. "יד 1"

    # Full price: the last amount in the block labelled "מחיר מלא"
    # (if there's a discount, the last one is the current price).
    idx = card_html.find("מחיר מלא")
    if idx != -1:
        amounts = re.findall(r"<bdi>([\d,]+)", card_html[idx:])
        car["price"] = to_int(amounts[-1]) if amounts else None
    else:
        car["price"] = None

    return car


def fetch_listings():
    """Return a list of car dicts currently listed. Raises SiteError on problems."""
    last_error = None
    for attempt in (1, 2):
        try:
            raw = http_request(
                AJAX_URL,
                data={"action": "filter_product_catalog", "data": f"category={CATEGORY_ID}"},
                headers={
                    "X-Requested-With": "XMLHttpRequest",
                    "Referer": CATALOG_URL,
                    "Origin": "https://toyota-select.co.il",
                    "Accept": "application/json, text/javascript, */*; q=0.01",
                },
            )
            break
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            last_error = e
            log(f"Fetch attempt {attempt} failed: {e}")
            if attempt == 1:
                time.sleep(15)
    else:
        raise SiteError(f"Could not reach the site: {last_error}")

    try:
        payload = json.loads(raw)
        results = payload["data"]["results"]
    except (ValueError, KeyError, TypeError):
        raise SiteError(f"Unexpected response (not the usual JSON): {raw[:200]!r}")

    if not payload.get("success") and results:
        raise SiteError(f"Site returned success=false: {raw[:200]!r}")

    cars = []
    for item in results:
        car = parse_card(item.get("product_html", ""))
        if car is None:
            raise SiteError("A listing card had no car ID - the page layout probably changed")
        cars.append(car)
    return cars


def fetch_details(car):
    """Add ownership type and region (dealer city) from the car's own page. Never raises."""
    try:
        page = http_request(car["link"])
    except Exception as e:
        log(f"Could not fetch details for car {car['id']}: {e}")
        return car

    # "בעלים/יד" -> "1 / חברה"  (hand / ownership type)
    m = re.search(r'בעלים/יד\s*</div>\s*<div class="car-details-box-val">(.*?)</div>', page, re.S)
    if m:
        value = clean_text(m.group(1))
        car["owners"] = value
        if "/" in value:
            car["ownership"] = value.split("/", 1)[1].strip()

    # Dealer address, e.g. "המוסכים פינת המחצבה, נצרת" -> region "נצרת"
    m = re.search(r"agency-address-pin\.svg[^>]*>\s*</div>(.*?)</div>", page, re.S)
    if m:
        address = clean_text(m.group(1))
        if address:
            car["address"] = address
            car["region"] = address.split(",")[-1].strip()
    return car


def matches_filters(car):
    if MAX_PRICE is not None and car.get("price") and car["price"] > MAX_PRICE:
        return False
    if MIN_YEAR is not None and car.get("year") and car["year"] < MIN_YEAR:
        return False
    if MAX_MILEAGE is not None and car.get("mileage") is not None and car["mileage"] > MAX_MILEAGE:
        return False
    return True


# -----------------------------------------------------------------------------
# Telegram
# -----------------------------------------------------------------------------

def load_dotenv():
    """Read KEY=VALUE lines from a local .env file (for running on your own computer)."""
    env_file = HERE / ".env"
    if not env_file.exists():
        return
    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def telegram_call(token, method, params):
    """Call a Telegram Bot API method. Returns the parsed JSON reply (even for errors)."""
    url = TELEGRAM_API.format(token=token, method=method)
    try:
        return json.loads(http_request(url, data=params))
    except urllib.error.HTTPError as e:
        # Telegram explains errors in a JSON body, e.g. {"ok":false,"description":"chat not found"}
        try:
            return json.loads(e.read().decode("utf-8", errors="replace"))
        except ValueError:
            return {"ok": False, "description": f"HTTP {e.code}"}
    except Exception as e:
        return {"ok": False, "description": str(e)}


class Notifier:
    def __init__(self, dry_run=False):
        self.dry_run = dry_run
        self.token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
        self.chat_id = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
        self.last_sent = 0.0
        self.failures = 0
        if not dry_run and not (self.token and self.chat_id):
            log("ERROR: TELEGRAM_BOT_TOKEN and/or TELEGRAM_CHAT_ID are not set - cannot send Telegram messages.")

    def send(self, text):
        """Send one message. Returns True on success. Retries once, then logs the error."""
        if self.dry_run:
            print("----- Telegram message (dry run, not sent) -----")
            print(text)
            print("------------------------------------------------")
            return True
        if not (self.token and self.chat_id):
            self.failures += 1
            log("ERROR: message NOT sent (missing Telegram secrets):\n" + text)
            return False

        # Sent as a UTF-8 form POST, so Hebrew, emoji and symbols need no special handling.
        for attempt in (1, 2):
            wait = SECONDS_BETWEEN_MESSAGES - (time.time() - self.last_sent)
            if wait > 0:
                time.sleep(wait)
            reply = telegram_call(self.token, "sendMessage", {"chat_id": self.chat_id, "text": text})
            self.last_sent = time.time()
            if reply.get("ok"):
                log("Telegram message sent.")
                return True
            log(f"Telegram attempt {attempt} failed: {reply.get('description', reply)}")
            # Telegram tells us how long to wait if we hit its rate limit.
            retry_after = (reply.get("parameters") or {}).get("retry_after")
            if retry_after and attempt == 1:
                time.sleep(min(int(retry_after), 60))

        self.failures += 1
        log("ERROR: giving up on this Telegram message after 2 attempts. Message was:\n" + text)
        return False


def print_chat_ids():
    """Show who has messaged the bot recently, so you can find your TELEGRAM_CHAT_ID."""
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        print("Set TELEGRAM_BOT_TOKEN first (in .env or as an environment variable).")
        return False
    reply = telegram_call(token, "getUpdates", {})
    if not reply.get("ok"):
        print(f"Telegram error: {reply.get('description')}  (is the token correct?)")
        return False
    chats = {}
    for update in reply.get("result", []):
        msg = update.get("message") or update.get("edited_message") or {}
        chat = msg.get("chat")
        if chat:
            name = " ".join(filter(None, [chat.get("first_name"), chat.get("last_name")])) or chat.get("title", "")
            chats[chat["id"]] = name
    if not chats:
        print("No messages found. Open your bot in Telegram, press Start (or send it 'hi'), then run this again.")
        return False
    for chat_id, name in chats.items():
        print(f"TELEGRAM_CHAT_ID={chat_id}   ({name})")
    return True


# -----------------------------------------------------------------------------
# Messages
# -----------------------------------------------------------------------------

def fmt_num(n):
    return f"{n:,}" if isinstance(n, int) else "?"


def car_message(car):
    lines = [
        "🚗 New Corolla Excite on Toyota Select",
        f"Price: {fmt_num(car.get('price'))} ₪",
        f"Year: {car.get('year') or '?'} · {fmt_num(car.get('mileage'))} km",
    ]
    owner_bits = [b for b in (car.get("hand"), car.get("ownership")) if b]
    where = car.get("region") or car.get("dealer")
    extra = " / ".join(owner_bits)
    if where:
        extra = f"{extra} · {where}" if extra else where
    if extra:
        lines.append(extra)
    lines.append(car["link"])
    return "\n".join(lines)


def summary_message(cars):
    lines = [f"🚗 {len(cars)} new Corolla Excite cars on Toyota Select:"]
    for car in cars[:10]:
        lines.append(f"• {fmt_num(car.get('price'))} ₪ · {car.get('year') or '?'} · "
                     f"{fmt_num(car.get('mileage'))} km")
    if len(cars) > 10:
        lines.append(f"…and {len(cars) - 10} more")
    lines.append(CATALOG_URL)
    return "\n".join(lines)


# -----------------------------------------------------------------------------
# State
# -----------------------------------------------------------------------------

def load_state(path):
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return None


def save_state(path, state):
    path.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def now_iso():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def record(state, car):
    # No "last seen" timestamp on purpose: the file only changes when a car is
    # added or its details change, so GitHub doesn't get a commit every hour.
    entry = state["cars"].setdefault(car["id"], {"first_seen": now_iso()})
    for key in ("title", "price", "year", "mileage", "hand", "ownership", "region", "dealer", "link"):
        if car.get(key) is not None:
            entry[key] = car[key]


# -----------------------------------------------------------------------------
# Main
# -----------------------------------------------------------------------------

def run(state_path, dry_run):
    notifier = Notifier(dry_run=dry_run)
    state = load_state(state_path)
    first_run = state is None
    if first_run:
        state = {"cars": {}, "problem": None}

    # --- Fetch -------------------------------------------------------------
    problem = None
    try:
        cars = fetch_listings()
        if not cars:
            problem = ("zero_cars",
                       "⚠️ Corolla Excite watcher: the site returned 0 cars. Either nothing is "
                       "listed right now, or the website changed and the watcher needs fixing.\n"
                       + CATALOG_URL)
    except SiteError as e:
        cars = []
        problem = ("fetch_failed",
                   f"⚠️ Corolla Excite watcher couldn't read the site:\n{str(e)[:300]}\n"
                   "I'll keep trying every run (no more warnings until it's fixed).")

    if problem:
        kind, text = problem
        log(f"Problem: {kind}")
        if state.get("problem") != kind:
            if notifier.send(text):
                state["problem"] = kind
        else:
            log("Same problem as last run - not repeating the warning.")
        if not dry_run and not first_run:
            save_state(state_path, state)
        return notifier.failures == 0

    if state.get("problem"):
        log("Site is readable again.")
        notifier.send(f"✅ Corolla Excite watcher is working again ({len(cars)} cars listed).")
        state["problem"] = None

    log(f"Found {len(cars)} car(s) listed:")
    for car in cars:
        log(f"  #{car['id']}  {fmt_num(car.get('price'))} ₪  {car.get('year')}  "
            f"{fmt_num(car.get('mileage'))} km  {car.get('hand', '')}  {car.get('dealer', '')}  {car['link']}")

    # --- First run: record everything, send one "I'm alive" message --------
    if first_run:
        for car in cars:
            record(state, car)
        notifier.send(f"✅ Corolla Excite watcher is active.\n"
                f"{len(cars)} car(s) currently listed. You'll get a message for each new one.\n"
                f"{CATALOG_URL}")
        if not dry_run:
            save_state(state_path, state)
        return notifier.failures == 0

    # --- Later runs: alert on new cars ---------------------------------------
    new_cars = [c for c in cars if c["id"] not in state["cars"]]
    for car in cars:
        if car["id"] in state["cars"]:
            record(state, car)  # keep price etc. up to date

    wanted = [c for c in new_cars if matches_filters(c)]
    for car in new_cars:
        if car not in wanted:
            log(f"New car #{car['id']} doesn't match filters - recording silently.")
            record(state, car)

    log(f"{len(new_cars)} new car(s), {len(wanted)} matching filters.")

    if len(wanted) > MAX_INDIVIDUAL_MESSAGES:
        if notifier.send(summary_message(wanted)):
            for car in wanted:
                record(state, car)
    else:
        for car in wanted:
            time.sleep(SECONDS_BETWEEN_SITE_REQUESTS)
            fetch_details(car)
            # Only mark as seen once the alert went out, so a failed send is retried next run.
            if notifier.send(car_message(car)):
                record(state, car)

    if not dry_run:
        save_state(state_path, state)
    return notifier.failures == 0


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true", help="print messages instead of sending; don't save state")
    parser.add_argument("--test", action="store_true", help="send one test Telegram message and exit")
    parser.add_argument("--get-chat-id", action="store_true", help="show the chat ID of whoever messaged your bot")
    parser.add_argument("--state", type=Path, default=DEFAULT_STATE_FILE, help="path to the seen-cars JSON file")
    args = parser.parse_args()

    load_dotenv()

    if args.get_chat_id:
        sys.exit(0 if print_chat_ids() else 1)

    if args.test:
        ok = Notifier().send("✅ בדיקה / Test: Corolla Excite watcher can reach your Telegram. "
                             "Hebrew & symbols OK: קורולה אקסייט, 109,000 ₪ & 100% + more")
        sys.exit(0 if ok else 1)

    ok = run(args.state, args.dry_run)
    # Non-zero exit only when Telegram sending failed, so GitHub emails you about it.
    # (Site problems are reported once via Telegram instead of failing every hour.)
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
