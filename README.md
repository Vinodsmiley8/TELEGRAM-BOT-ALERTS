# Telegram MT5 Alerts Bot

**Simple Telegram bot that creates trading alerts from Telegram messages and watches MetaTrader 5 ticks to notify users when targets are hit.**

> File included: `src/TELEGRAM BOT ALERTS.py` (original code, unchanged)

---

## Table of contents

* [Overview](#overview)
* [Features](#features)
* [Design & Logic (how it works)](#design--logic-how-it-works)
* [Message / Command Examples](#message--command-examples)
* [Configuration (.env)](#configuration-env)
* [Installation](#installation)
* [Running](#running)
* [Deployment suggestions](#deployment-suggestions)
* [Persistence & Improvements](#persistence--improvements)
* [Troubleshooting](#troubleshooting)
* [License](#license)

---

## Overview

This bot allows authorized Telegram users to create price-based and structural alerts (e.g. "SharpTurn"-style alerts). The bot monitors MetaTrader 5 (MT5) for incoming ticks and compares them against the active alerts; when an alert condition is met it sends a Telegram notification to the creator.

---

## Features

* Accepts alert definitions from authorized Telegram users.
* Price alerts for instruments/symbols (buy / sell / target price notifications).
* A “SharpTurn” type alert (timeframe + two price points) — used for detecting specific patterns or turns.
* Thread-safe in-memory storage of alerts and pending user flows (multi-step alert creation).
* MT5 integration to fetch ticks and symbol info.
* Simple authorization by Telegram user ID.

---

## Design & Logic (how it works)

This section describes the internal flow and main data structures so you — or another developer — can understand or extend the bot.

### Main data structures

* `price_alerts` — map of user → list of price alerts. Each price alert typically contains: symbol, target_price, alert_type (BUY/SELL), metadata (timestamp, id).
* `sharpturn_alerts` — map of user → list of SharpTurn alerts (timeframe, price1, price2, symbol).
* `symbol_alerts` — map of symbol → list of alerts for quick matching when a tick arrives.
* `pending_flows` — per-user deque storing the current multi-step flow state (e.g., when creating an alert the bot prompts for symbol → price → confirm).

All writes/reads to these in-memory maps are protected by a lock to keep threads safe.

### High-level flow

1. **Start & init**

   * On startup the bot initializes the MT5 connection (if available) and the Telegram bot client.
   * Loads authorized user list from config.

2. **Authorization**

   * Every incoming Telegram message is checked against the allowed users list. Unauthorized users get a polite rejection.

3. **Command/Message parsing**

   * The bot either recognizes a direct command (single-line alert) or begins a multi-step “flow”.
   * Example multi-step flow: user types `/new_alert` (or a trigger), bot asks for symbol → user replies → bot asks for price → user replies → bot confirms and saves alert.

4. **Store alert**

   * When an alert is created the bot saves it to `price_alerts` and also indexes it in `symbol_alerts` so ticks can be matched quickly.

5. **MT5 tick monitoring**

   * A background thread polls MT5 ticks (or subscribes) for relevant symbols. For each tick:

     * Look up `symbol_alerts[tick.symbol]`
     * Check each alert condition (price threshold, direction, SharpTurn pattern)
     * If matched, send a Telegram notification to the alert owner and remove or mark the alert as triggered (depending on your policy).

6. **Notifications & housekeeping**

   * After a notification is sent the alert can be deleted or archived (current code uses in-memory deletion).
   * Pending flows time out or can be cancelled by user command.

---

## Message / Command Examples

The original script accepts alerts via message and multi-step flows. Below are **suggested** example formats you can follow or adapt to the bot’s exact implementation.

### Single-line quick alert (example)

```
/alert BUY EURUSD 1.09870
```

Creates a price alert that notifies when EURUSD reaches 1.09870 (BUY-side target).

### Multi-step flow

1. `/new_alert`
2. Bot → `Which symbol?`
3. User → `EURUSD`
4. Bot → `Type BUY or SELL:`
5. User → `SELL`
6. Bot → `Target price?`
7. User → `1.10000`
8. Bot → `Confirm: SELL EURUSD 1.10000 — Confirm? (yes/no)`
9. User → `yes` → Bot saves alert

### SharpTurn example (illustrative)

```
/sharpturn EURUSD H1 1.0950 1.1000
```

Meaning: watch EURUSD on 1-hour timeframe for a “turn” between 1.0950 and 1.1000. (Exact detection rules depend on the implementation in your script.)

> Note: If the uploaded script uses different commands, adapt these examples accordingly. If you want, I can extract the exact command strings from your file and update the README to match.

---

## Configuration (.env)

Create a `.env` in the project root (do **not** commit secrets to git). Example:

```
BOT_TOKEN=123456789:ABCDEFGHIJKLMNOPQRSTUVWXYZ
ALLOWED_USERS=123456789,987654321
MT5_PATH= # optional path or leave blank if using default
```

* `BOT_TOKEN` — your Telegram bot token.
* `ALLOWED_USERS` — comma-separated Telegram user IDs allowed to use the bot.
* Add other variables used by your script (MT5 server, broker credentials) if needed.

---

## Installation

1. Create a Python virtual environment:

```bash
python -m venv .venv
source .venv/bin/activate   # on Windows: .venv\Scripts\activate
```

2. Install dependencies (example `requirements.txt`):

```
pyTelegramBotAPI==4.12.0
MetaTrader5==5.0.43
python-dotenv==1.0.0
```

Install with:

```bash
pip install -r requirements.txt
```

3. Add your `.env` file and ensure MT5 terminal is running & plugin/integration enabled (if MT5 is used).

---

## Running

Run the script:

```bash
python src/TELEGRAM\ BOT\ ALERTS.py
```

(If the file name contains spaces you may want to rename it to `telegram_bot_alerts.py`.)

Or if you used the refactored module version:

```bash
python -m src.bot
```

---

## Deployment suggestions

* **Systemd** (Linux): create a systemd unit to run the bot as a service with restart=always.
* **Docker**: build a small Docker image (install Python, copy code, set env vars, run).
* **Supervisor / pm2**: any process manager that restarts the bot on crash is fine.

**Example systemd unit (simple)**:

```ini
[Unit]
Description=Telegram MT5 Alerts Bot
After=network.target

[Service]
Type=simple
WorkingDirectory=/opt/telegram_mt5
ExecStart=/opt/telegram_mt5/.venv/bin/python src/telegram_bot_alerts.py
Restart=always
EnvironmentFile=/opt/telegram_mt5/.env

[Install]
WantedBy=multi-user.target
```

---

## Persistence & Improvements

Current code stores alerts in-memory — they are lost on restart. Recommended improvements:

* Persist alerts to a small database (SQLite) or Redis so alerts survive restarts.
* Add a web dashboard to view / cancel alerts.
* Add logging with rotation (e.g., `logging` module + `RotatingFileHandler`).
* Add rate-limiting or throttling for notifications to avoid message storms.
* Add unit tests for the alert matching logic and flows.

---

## Troubleshooting

* **MT5 not initializing**: ensure MT5 terminal is running and Python integration is enabled. Check the terminal logs.
* **Bot not responding**: verify `BOT_TOKEN` and that the bot is not blocked by Telegram (correct token).
* **Unauthorized users**: add your Telegram user ID to `ALLOWED_USERS`.
* **Alert never triggers**: check indexing in `symbol_alerts`, ensure the tick polling loop is running and symbol naming matches MT5 (e.g., some brokers use different symbol names like `EURUSD.m`).

---

## Security & Best practices

* Never commit your `.env` with real tokens.
* Restrict `ALLOWED_USERS` to trusted Telegram IDs.
* Run the bot on a secure server and keep dependencies up to date.
* If deploying publicly, consider adding additional auth flows or a separate admin-only command set.

---

## Contributing

If you want me to:

* Create a `requirements.txt` by scanning imports,
* Rename the script to `telegram_bot_alerts.py` (without spaces),
* Add a `Dockerfile` and `docker-compose.yml`,
* Add persistence using SQLite and automatic migration,
  I can prepare them and provide an updated zip.

---





