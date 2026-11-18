import os
import io
import json
import logging
import sqlite3
from datetime import datetime, time
from decimal import Decimal, ROUND_HALF_UP
from zoneinfo import ZoneInfo

import requests
from dotenv import load_dotenv
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import (
    Application, CommandHandler, ContextTypes
)

load_dotenv()

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
GOLD_API_BASE = os.getenv("GOLD_API_BASE", "https://api.gold-api.com").rstrip("/")
FX_API_BASE = os.getenv("FX_API_BASE", "https://api.frankfurter.dev/v2").rstrip("/")
UPDATE_MINUTES = int(os.getenv("UPDATE_MINUTES", "15"))
DAILY_REPORT_HOUR = int(os.getenv("DAILY_REPORT_HOUR", "19"))
TIMEZONE = os.getenv("TIMEZONE", "Asia/Ho_Chi_Minh")
ALERT_DEFAULT_PERCENT = Decimal(os.getenv("ALERT_DEFAULT_PERCENT", "1.0"))
DB_PATH = os.getenv("DB_PATH", "gold_tracker.db")
REPORT_DIR = os.getenv("REPORT_DIR", "reports")
OUNCE_GRAMS = Decimal("31.1034768")
PAVAN_GRAMS = Decimal("8")  # Kerala commonly uses 1 pavan = 8 grams
LOCAL_TZ = ZoneInfo(TIMEZONE)

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
log = logging.getLogger("leo-gold-tracker")
os.makedirs(REPORT_DIR, exist_ok=True)

def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    with db() as conn:
        conn.execute("""CREATE TABLE IF NOT EXISTS subscribers (
            chat_id INTEGER PRIMARY KEY,
            alert_percent TEXT NOT NULL DEFAULT '1.0',
            last_alert_price TEXT,
            created_at TEXT NOT NULL
        )""")
        conn.execute("""CREATE TABLE IF NOT EXISTS price_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            price_usd_oz TEXT NOT NULL,
            price_inr_g TEXT NOT NULL,
            price_vnd_g TEXT NOT NULL,
            fx_inr TEXT NOT NULL,
            fx_vnd TEXT NOT NULL,
            source_time TEXT,
            recorded_at TEXT NOT NULL
        )""")

def http_get_json(url, params=None):
    response = requests.get(url, params=params, timeout=20, headers={"User-Agent": "LEO-GOLD-TRACKER/1.0"})
    response.raise_for_status()
    return response.json()

def get_gold_price_usd_oz():
    # Gold API free live spot endpoint. The API's returned price is per troy ounce.
    data = http_get_json(f"{GOLD_API_BASE}/price/XAU")
    value = data.get("price")
    if value is None:
        raise RuntimeError(f"Gold API response did not contain 'price': {data}")
    updated = data.get("updatedAt") or data.get("updated_at") or data.get("timestamp")
    return Decimal(str(value)), str(updated or "Not supplied by provider")

def get_fx_rates():
    # Frankfurter rates are reference FX rates, not intraday trading quotes.
    data = http_get_json(f"{FX_API_BASE}/rates", params={"base": "USD", "quotes": "INR,VND"})
    rates = {row["quote"]: Decimal(str(row["rate"])) for row in data}
    if "INR" not in rates or "VND" not in rates:
        raise RuntimeError(f"FX API did not return INR and VND: {data}")
    return rates["INR"], rates["VND"], data[0].get("date", "unknown")

def money(value, decimals=2):
    q = Decimal("1").scaleb(-decimals)
    return value.quantize(q, rounding=ROUND_HALF_UP)

def get_snapshot():
    usd_oz, source_time = get_gold_price_usd_oz()
    usd_inr, usd_vnd, fx_date = get_fx_rates()
    usd_g = usd_oz / OUNCE_GRAMS
    return {
        "usd_oz": usd_oz,
        "inr_g_24": usd_g * usd_inr,
        "vnd_g_24": usd_g * usd_vnd,
        "inr_g_22": usd_g * usd_inr * Decimal("22") / Decimal("24"),
        "vnd_g_22": usd_g * usd_vnd * Decimal("22") / Decimal("24"),
        "fx_inr": usd_inr,
        "fx_vnd": usd_vnd,
        "fx_date": fx_date,
        "source_time": source_time,
        "recorded_at": datetime.now(LOCAL_TZ).isoformat(timespec="seconds"),
    }

def save_snapshot(s):
    with db() as conn:
        conn.execute(
            """INSERT INTO price_history
            (price_usd_oz, price_inr_g, price_vnd_g, fx_inr, fx_vnd, source_time, recorded_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (str(s["usd_oz"]), str(s["inr_g_24"]), str(s["vnd_g_24"]),
             str(s["fx_inr"]), str(s["fx_vnd"]), s["source_time"], s["recorded_at"])
        )

def format_report(s):
    inr_pavan = s["inr_g_24"] * PAVAN_GRAMS
    vnd_pavan = s["vnd_g_24"] * PAVAN_GRAMS
    return (
        "🟡 <b>LEO GOLD TRACKER — SPOT ESTIMATE</b>\n"
        f"🕒 Updated: <code>{s['recorded_at']}</code>\n\n"
        "<b>International spot converted to local currency</b>\n"
        f"🇮🇳 India — 24K: <b>₹{money(s['inr_g_24']):,}</b> / gram\n"
        f"🇮🇳 India — 22K: <b>₹{money(s['inr_g_22']):,}</b> / gram\n"
        f"🇮🇳 India — 24K: <b>₹{money(inr_pavan):,}</b> / pavan (8g)\n\n"
        f"🇻🇳 Vietnam — 24K: <b>{money(s['vnd_g_24'],0):,} VND</b> / gram\n"
        f"🇻🇳 Vietnam — 22K: <b>{money(s['vnd_g_22'],0):,} VND</b> / gram\n"
        f"🇻🇳 Vietnam — 24K: <b>{money(vnd_pavan,0):,} VND</b> / 8g\n\n"
        f"🌍 Gold spot: ${money(s['usd_oz']):,} / troy oz\n"
        f"💱 USD/INR: {money(s['fx_inr'],4)} | USD/VND: {money(s['fx_vnd'],2)}\n"
        f"FX reference date: {s['fx_date']}\n"
        f"Gold provider timestamp: {s['source_time']}\n\n"
        "⚠️ <i>These are calculated spot-price equivalents, not shop quotes. "
        "They exclude local premiums, taxes, making charges, dealer spreads and product-specific "
        "pricing. Vietnam SJC/DOJI/PNJ and Indian jewellery/retail rates can differ materially.</i>"
    )

def build_pdf(s):
    filename = os.path.join(REPORT_DIR, f"LEO_GOLD_{datetime.now(LOCAL_TZ):%Y%m%d_%H%M}.pdf")
    doc = SimpleDocTemplate(filename, pagesize=A4, rightMargin=18*mm, leftMargin=18*mm, topMargin=18*mm, bottomMargin=18*mm)
    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle(name="SmallNote", parent=styles["BodyText"], fontSize=8, leading=11, textColor=colors.HexColor("#555555")))
    story = [
        Paragraph("LEO GOLD TRACKER", styles["Title"]),
        Paragraph("India & Vietnam — Automatic Gold Spot Comparison", styles["Heading2"]),
        Paragraph(f"Generated: {s['recorded_at']} ({TIMEZONE})", styles["BodyText"]),
        Spacer(1, 8*mm),
        Paragraph("Calculated spot-price equivalents", styles["Heading2"]),
    ]
    rows = [
        ["Market / Purity", "Per gram", "Per 8 grams"],
        ["India 24K", f"INR {money(s['inr_g_24']):,}", f"INR {money(s['inr_g_24']*8):,}"],
        ["India 22K", f"INR {money(s['inr_g_22']):,}", f"INR {money(s['inr_g_22']*8):,}"],
        ["Vietnam 24K", f"VND {money(s['vnd_g_24'],0):,}", f"VND {money(s['vnd_g_24']*8,0):,}"],
        ["Vietnam 22K", f"VND {money(s['vnd_g_22'],0):,}", f"VND {money(s['vnd_g_22']*8,0):,}"],
    ]
    table = Table(rows, colWidths=[55*mm, 55*mm, 55*mm])
    table.setStyle(TableStyle([
        ("BACKGROUND", (0,0), (-1,0), colors.HexColor("#1F4E78")),
        ("TEXTCOLOR", (0,0), (-1,0), colors.white),
        ("FONTNAME", (0,0), (-1,0), "Helvetica-Bold"),
        ("GRID", (0,0), (-1,-1), 0.4, colors.grey),
        ("BACKGROUND", (0,1), (-1,-1), colors.HexColor("#F2F6FA")),
        ("PADDING", (0,0), (-1,-1), 8),
    ]))
    story += [table, Spacer(1, 7*mm), Paragraph("Market data", styles["Heading2"])]
    story.append(Paragraph(
        f"Gold spot: USD {money(s['usd_oz']):,} per troy ounce. "
        f"USD/INR: {money(s['fx_inr'],4)}. USD/VND: {money(s['fx_vnd'],2)}. "
        f"FX reference date: {s['fx_date']}. Gold timestamp: {s['source_time']}.",
        styles["BodyText"]))
    story += [Spacer(1, 6*mm), Paragraph(
        "Important: This report converts international spot gold into INR and VND using reference FX rates. "
        "It is not a quotation from SJC, DOJI, PNJ, MCX, IBJA or a local jeweller. Retail prices may include "
        "premiums, taxes, spreads, workmanship and local supply/demand effects. 22K is calculated as 22/24 "
        "of the 24K spot-equivalent value. Pavan is calculated as 8 grams.",
        styles["SmallNote"])]
    doc.build(story)
    return filename

async def safe_send(context, chat_id, text):
    await context.bot.send_message(chat_id=chat_id, text=text, parse_mode=ParseMode.HTML, disable_web_page_preview=True)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    with db() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO subscribers(chat_id, alert_percent, created_at) VALUES (?, ?, ?)",
            (chat_id, str(ALERT_DEFAULT_PERCENT), datetime.now(LOCAL_TZ).isoformat())
        )
    await update.message.reply_text(
        "Welcome to LEO GOLD TRACKER 🟡\n\n"
        "I will send gold spot-equivalent updates every 15 minutes and a daily PDF report.\n\n"
        "/price — current comparison\n"
        "/report — create and send PDF now\n"
        "/alert 1.0 — alert when price moves 1% from the last alert reference\n"
        "/status — automation status\n"
        "/help — commands\n\n"
        "Please keep this bot chat open and press Start. The bot only sends updates to chats that have started it."
    )

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Commands:\n/start — subscribe\n/price — latest comparison\n/report — PDF now\n"
        "/alert PERCENT — set alert threshold (example: /alert 1.5)\n"
        "/status — show settings\n/stop — stop automatic messages\n"
        "Alerts compare the latest spot estimate with the last alert reference price."
    )

async def price_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        s = get_snapshot()
        save_snapshot(s)
        await update.message.reply_text(format_report(s), parse_mode=ParseMode.HTML)
    except Exception as e:
        log.exception("Price fetch failed")
        await update.message.reply_text(f"Could not fetch current prices: {e}")

async def report_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        s = get_snapshot()
        save_snapshot(s)
        path = build_pdf(s)
        with open(path, "rb") as f:
            await update.message.reply_document(document=f, caption="LEO GOLD TRACKER — detailed spot comparison")
    except Exception as e:
        log.exception("PDF generation failed")
        await update.message.reply_text(f"Could not create report: {e}")

async def alert_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Usage: /alert 1.0  (alert on 1% movement)")
        return
    try:
        pct = Decimal(context.args[0])
        if pct <= 0 or pct > 25:
            raise ValueError()
        with db() as conn:
            conn.execute(
                "INSERT INTO subscribers(chat_id, alert_percent, created_at) VALUES (?, ?, ?) "
                "ON CONFLICT(chat_id) DO UPDATE SET alert_percent=excluded.alert_percent",
                (update.effective_chat.id, str(pct), datetime.now(LOCAL_TZ).isoformat())
            )
        await update.message.reply_text(f"Price movement alerts set to {pct}%")
    except Exception:
        await update.message.reply_text("Enter a percentage between 0.01 and 25. Example: /alert 1.0")

async def status_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    with db() as conn:
        row = conn.execute("SELECT alert_percent FROM subscribers WHERE chat_id=?", (update.effective_chat.id,)).fetchone()
    if not row:
        await update.message.reply_text("You are not subscribed. Send /start.")
        return
    await update.message.reply_text(
        f"LEO GOLD TRACKER is active.\nUpdate interval: {UPDATE_MINUTES} minutes\n"
        f"Daily PDF: {DAILY_REPORT_HOUR:02d}:00 ({TIMEZONE})\nAlert threshold: {row['alert_percent']}%\n"
        f"Data: international gold spot + reference FX rates."
    )

async def stop_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    with db() as conn:
        conn.execute("DELETE FROM subscribers WHERE chat_id=?", (update.effective_chat.id,))
    await update.message.reply_text("Automatic updates stopped. Send /start to subscribe again.")

async def broadcast_prices(context: ContextTypes.DEFAULT_TYPE):
    try:
        s = get_snapshot()
        save_snapshot(s)
        msg = format_report(s)
        with db() as conn:
            subs = conn.execute("SELECT chat_id, alert_percent, last_alert_price FROM subscribers").fetchall()
        for sub in subs:
            chat_id = sub["chat_id"]
            await safe_send(context, chat_id, msg)
            old = Decimal(sub["last_alert_price"]) if sub["last_alert_price"] else s["inr_g_24"]
            change_pct = abs((s["inr_g_24"] - old) / old * 100) if old else Decimal("0")
            threshold = Decimal(sub["alert_percent"])
            if change_pct >= threshold:
                direction = "increased" if s["inr_g_24"] > old else "decreased"
                await safe_send(
                    context, chat_id,
                    f"🚨 <b>GOLD PRICE ALERT</b>\nIndia 24K spot-equivalent {direction} "
                    f"by {money(change_pct)}% since the previous alert reference.\n"
                    f"Previous reference: ₹{money(old):,}/g\nCurrent: ₹{money(s['inr_g_24']):,}/g"
                )
                with db() as conn:
                    conn.execute("UPDATE subscribers SET last_alert_price=? WHERE chat_id=?", (str(s["inr_g_24"]), chat_id))
            elif not sub["last_alert_price"]:
                with db() as conn:
                    conn.execute("UPDATE subscribers SET last_alert_price=? WHERE chat_id=?", (str(s["inr_g_24"]), chat_id))
    except Exception:
        log.exception("Scheduled price broadcast failed")

async def daily_pdf(context: ContextTypes.DEFAULT_TYPE):
    try:
        s = get_snapshot()
        save_snapshot(s)
        path = build_pdf(s)
        with db() as conn:
            subs = conn.execute("SELECT chat_id FROM subscribers").fetchall()
        for sub in subs:
            with open(path, "rb") as f:
                await context.bot.send_document(
                    chat_id=sub["chat_id"], document=f,
                    caption=f"📊 Daily LEO GOLD TRACKER report — {datetime.now(LOCAL_TZ):%d %b %Y}"
                )
    except Exception:
        log.exception("Daily PDF job failed")

async def post_init(application: Application):
    # Send a startup notice only to subscribed users, without failing startup.
    log.info("LEO GOLD TRACKER started. Interval=%s minutes timezone=%s", UPDATE_MINUTES, TIMEZONE)

def main():
    if not BOT_TOKEN:
        raise SystemExit("Missing TELEGRAM_BOT_TOKEN. Add it to your .env file or cloud environment variables.")
    init_db()
    app = Application.builder().token(BOT_TOKEN).post_init(post_init).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("price", price_cmd))
    app.add_handler(CommandHandler("report", report_cmd))
    app.add_handler(CommandHandler("alert", alert_cmd))
    app.add_handler(CommandHandler("status", status_cmd))
    app.add_handler(CommandHandler("stop", stop_cmd))

    jq = app.job_queue
    if jq is None:
        raise SystemExit("JobQueue missing. Install python-telegram-bot[job-queue].")
    jq.run_repeating(broadcast_prices, interval=UPDATE_MINUTES * 60, first=20, name="price_updates")
    jq.run_daily(daily_pdf, time=time(hour=DAILY_REPORT_HOUR, minute=0, tzinfo=LOCAL_TZ), name="daily_pdf")
    log.info("Starting polling...")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
