"""
Ko'p platformali media yuklab beruvchi Telegram bot
"""

import os
import time
import asyncio
import logging
import tempfile
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)
import yt_dlp

# ============ SOZLAMALAR ============
BOT_TOKEN = "8581347222:AAG7Z9QyXhwZ3PY2oULeCbXIzhomZitmKgc"

USE_LOCAL_SERVER = False
LOCAL_SERVER_URL = "http://localhost:8081"

MAX_FILE_SIZE_MB = 2000 if USE_LOCAL_SERVER else 50
RATE_LIMIT_SECONDS = 20
MAX_CONCURRENT_DOWNLOADS = 3

COOKIES_FILE = None
# =====================================

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(name)

user_links = {}
last_request_time = {}
download_semaphore = asyncio.Semaphore(MAX_CONCURRENT_DOWNLOADS)

SUPPORTED_DOMAINS = [
    "youtube.com", "youtu.be",
    "instagram.com",
    "tiktok.com",
    "facebook.com", "fb.watch",
    "pinterest.com", "pin.it",
    "twitter.com", "x.com",
]


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Salom! Menga YouTube, Instagram (post/reels/stories), TikTok, "
        "Facebook, Pinterest yoki Twitter/X link yuboring, "
        "men video yoki audio (mp3) holida yuklab beraman."
    )


async def handle_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    url = update.message.text.strip()
    user_id = update.effective_user.id

    now = time.time()
    last = last_request_time.get(user_id, 0)
    if now - last < RATE_LIMIT_SECONDS:
        wait = int(RATE_LIMIT_SECONDS - (now - last))
        await update.message.reply_text(
            f"⏱ Juda tez-tez so'rov yubordingiz. Iltimos, {wait} soniya kuting."
        )
        return
    last_request_time[user_id] = now

    if not any(domain in url for domain in SUPPORTED_DOMAINS):
        await update.message.reply_text(
            "Iltimos, YouTube, Instagram, TikTok, Facebook, Pinterest yoki "
            "Twitter/X linkini yuboring."
        )
        return

    user_links[user_id] = url

    keyboard = [
        [
            InlineKeyboardButton("🎬 Video", callback_data="video"),
            InlineKeyboardButton("🎵 Audio (mp3)", callback_data="audio"),
        ]
    ]
    await update.message.reply_text(
        "Qaysi formatda yuklab beray?", reply_markup=InlineKeyboardMarkup(keyboard)
    )


async def handle_choice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    user_id = query.from_user.id
    url = user_links.get(user_id)

    if not url:
        await query.edit_message_text("Link topilmadi, qaytadan yuboring.")
        return

    choice = query.data

    if download_semaphore.locked():
        await query.edit_message_text(
            "⏳ Hozir boshqa yuklashlar ketyapti, navbatda turibsiz..."
        )
    else:
        await query.edit_message_text("⏳ Yuklab olinmoqda, biroz kuting...")

    async with download_semaphore:
        await do_download(query, url, choice)

    user_links.pop(user_id, None)


async def do_download(query, url, choice):
    with tempfile.TemporaryDirectory() as tmpdir:
        outtmpl = os.path.join(tmpdir, "%(title).80s.%(ext)s")

        if choice == "audio":
            ydl_opts = {
                "format": "bestaudio/best",
                "outtmpl": outtmpl,
                "postprocessors": [
                    {
                        "key": "FFmpegExtractAudio",
                        "preferredcodec": "mp3",
                        }
                ],
                "quiet": True,
                "no_warnings": True,
            }
        else:
            if USE_LOCAL_SERVER:
                ydl_opts = {
                    "format": f"best[filesize<{MAX_FILE_SIZE_MB}M]/best",
                    "outtmpl": outtmpl,
                    "quiet": True,
                    "no_warnings": True,
                }
            else:
                ydl_opts = {
                    "format": (
                        f"best[height<=720][filesize<{MAX_FILE_SIZE_MB}M]/"
                        f"best[height<=480][filesize<{MAX_FILE_SIZE_MB}M]/"
                        f"best[height<=360][filesize<{MAX_FILE_SIZE_MB}M]/"
                        "worst"
                    ),
                    "outtmpl": outtmpl,
                    "quiet": True,
                    "no_warnings": True,
                }

        if COOKIES_FILE:
            ydl_opts["cookiefile"] = COOKIES_FILE

        try:
            loop = asyncio.get_running_loop()

            def run_download():
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    info = ydl.extract_info(url, download=True)
                    fname = ydl.prepare_filename(info)
                    return fname

            filename = await loop.run_in_executor(None, run_download)

            if choice == "audio":
                base, _ = os.path.splitext(filename)
                filename = base + ".mp3"

            file_size_mb = os.path.getsize(filename) / (1024 * 1024)
            if file_size_mb > MAX_FILE_SIZE_MB:
                await query.message.reply_text(
                    f"❌ Fayl juda katta ({file_size_mb:.1f}MB). "
                    f"Telegram bot orqali {MAX_FILE_SIZE_MB}MB dan katta "
                    "fayl yuborib bo'lmaydi."
                )
                return

            if choice == "audio":
                await query.message.reply_audio(audio=open(filename, "rb"))
            else:
                await query.message.reply_video(video=open(filename, "rb"))

        except Exception as e:
            logger.error(f"Xatolik: {e}")
            hint = ""
            if "instagram.com/stories" in url and not COOKIES_FILE:
                hint = (
                    "\n\n💡 Stories yuklash uchun odatda login (cookie) kerak "
                    "bo'ladi — COOKIES_FILE sozlamasini to'ldiring."
                )
            await query.message.reply_text(
                "❌ Yuklab bo'lmadi. Link noto'g'ri yoki video himoyalangan bo'lishi mumkin."
                + hint
            )


def main():
    port = int(os.environ.get("PORT", 10000))

    class HealthHandler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"Bot ishlayapti")

        def log_message(self, format, *args):
            pass

    def run_health_server():
        server = HTTPServer(("0.0.0.0", port), HealthHandler)
        server.serve_forever()

    threading.Thread(target=run_health_server, daemon=True).start()

    builder = ApplicationBuilder().token(BOT_TOKEN)

    if USE_LOCAL_SERVER:
        builder = builder.base_url(f"{LOCAL_SERVER_URL}/bot").base_file_url(
            f"{LOCAL_SERVER_URL}/file/bot"
        ).local_mode(True)

    app = builder.build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_link))
    app.add_handler(CallbackQueryHandler(handle_choice))

    logger.info("Bot ishga tushdi...")
    app.run_polling()


if name == "main":
    main()
    
                        "preferredquality": "192",
