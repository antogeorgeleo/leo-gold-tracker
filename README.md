# LEO GOLD TRACKER — Telegram Automation

This project sends an India/Vietnam gold spot-equivalent update every 15 minutes, sends a daily PDF, and supports percentage-movement alerts.

## Important pricing limitation

The project uses:
- Gold-API live XAU spot price in USD per troy ounce.
- Frankfurter USD/INR and USD/VND reference exchange rates.

It calculates 24K spot-equivalent prices and estimates 22K as 22/24 of 24K. It is **not** a live retail quote from SJC, DOJI, PNJ, MCX, IBJA or an Indian/Vietnamese jeweller. It does not include local premiums, taxes, making charges, spreads or product differences. Frankfurter FX rates are reference rates updated through the day, not intraday trading quotes. Check the source timestamps before making a purchase decision.

## Features

- `/start` subscribes the chat.
- Automatic price message every 15 minutes.
- Daily PDF at 19:00 Vietnam time by default.
- `/price` sends the latest calculation.
- `/report` generates and sends a PDF immediately.
- `/alert 1.0` sets a movement alert threshold (percentage).
- `/status` shows current settings.
- `/stop` unsubscribes.

The alert compares the India 24K INR-per-gram estimate with the last alert reference. First scheduled update establishes the baseline.

## 1. Create the Telegram bot

1. Open Telegram and search for **@BotFather**.
2. Send `/newbot`.
3. Choose a display name, e.g. `LEO GOLD TRACKER`.
4. Choose a unique username ending in `bot`, e.g. `LeoGoldTrackerBot`.
5. Copy the bot token. Treat it like a password; never post it publicly.
6. Open your new bot and press **Start**.

## 2. Run on Windows first

Install Python 3.11 or newer.

Open PowerShell:
```powershell
cd C:\
mkdir LEO_GOLD_TRACKER
cd C:\LEO_GOLD_TRACKER
```

Extract this project ZIP into `C:\LEO_GOLD_TRACKER`, then:
```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
Copy-Item .env.example .env
notepad .env
```

In `.env`, replace `PASTE_YOUR_BOT_TOKEN_HERE` with the token from BotFather. Save and close Notepad.

Start the bot:
```powershell
python bot.py
```

Open Telegram, press Start on your bot, then send `/price`. Test `/report`, `/alert 1.0`, and `/status`.

Keep the PowerShell process running for automation to continue. Closing the terminal stops the bot.

## 3. Deploy 24/7 to Render (cloud)

A Render **Background Worker** is recommended because the bot uses Telegram long polling and runs continuously. Render workers are paid compute; free web services can sleep and are not suitable for a dependable always-on polling bot.

1. Create a private GitHub repository named `leo-gold-tracker`.
2. Upload the files from this project ZIP to the repository. Do not upload `.env` or your bot token.
3. Sign in to Render and choose **New → Background Worker**.
4. Connect the GitHub repository.
5. Choose Docker as runtime (or use the included `Dockerfile`).
6. Add environment variables:
   - `TELEGRAM_BOT_TOKEN` = your BotFather token
   - `UPDATE_MINUTES` = `15`
   - `DAILY_REPORT_HOUR` = `19`
   - `TIMEZONE` = `Asia/Ho_Chi_Minh`
   - `DB_PATH` = `/var/data/gold_tracker.db`
   - `REPORT_DIR` = `/var/data/reports`
   - `ALERT_DEFAULT_PERCENT` = `1.0`
7. Add a persistent disk mounted at `/var/data` if you want subscriber and alert baseline data to survive restarts/redeploys. Render persistent disks are a paid feature.
8. Deploy. Open the service logs and confirm `Starting polling...`.
9. In Telegram, send `/status` and `/price`.

You can also use the included `render.yaml` as a starting Blueprint, but add the persistent disk through the Render dashboard and verify the worker plan before deploying.

## 4. Common problems

**Bot does not reply**
- Confirm the token is correct.
- Confirm the Render worker is running.
- Send `/start` to the bot in a private chat.
- Do not run the same bot token in two polling processes at once.

**Invalid token**
- Create a new token with BotFather and update the cloud environment variable. Never paste your token into a public repository.

**Price API failure**
- Check whether `https://api.gold-api.com/price/XAU` is responding.
- Check the provider's current API documentation and status.

**PDF not sent**
- Check Render logs and ensure `REPORT_DIR` exists and is writable.

**No 15-minute schedule**
- This is an in-process scheduler. The worker must remain running. Do not deploy it as a Render Cron Job; the bot needs a continuously running process to receive commands.

## Data and security

- The bot stores chat IDs, alert thresholds, and recent price snapshots in SQLite.
- The token belongs in environment variables only.
- Do not share the `.env` file or token.
- Users must start the bot before it can message them.
- This is informational only, not financial advice.
