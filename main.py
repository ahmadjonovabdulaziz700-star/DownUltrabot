#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Universal 24/7 Downloader Bot (main.py)
Features:
- YouTube/Instagram/TikTok/Facebook/X/Pinterest via yt-dlp or helper APIs
- MP4 / MP3 options, inline buttons
- Multilingual (uz/ru/en)
- Admin panel: stats, broadcast, ban/unban
- Big file support: S3/R2 optional or transfer.sh fallback
- Webhook-ready (Flask) and polling fallback
- Simple JSON storage for users/ban/lang
"""

import os
import json
import logging
import tempfile
import shutil
from functools import wraps
from flask import Flask, request, abort
import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup, KeyboardButton
from yt_dlp import YoutubeDL
import requests

# ============================
# ======  O'ZGARTIRILADIGAN JOY ======
# ============================
BOT_TOKEN = "8581347222:AAGqbvDRflviOVKhwdwGkejLEy5LaDgld-o"      # <<< --- BU YERGA O'Z TOKENINGIZNI QO'YING (MISOL UCHUN)
ADMIN_IDS = [6585473272]                # <<< --- BU YERGA O'Z ADMIN ID (raqam) qo'ying, kerak bo'lsa vergul bilan ko'p ID

# (Ixtiyoriy) S3 / Cloudflare R2 konfiguratsiyasi (katta fayllar uchun)
S3_ENDPOINT = ""       # misol: "https://<account>.r2.cloudflarestorage.com"
S3_ACCESS_KEY = ""
S3_SECRET_KEY = ""
S3_BUCKET = ""
# ============================
# =================================

# Basic settings
MAX_TELEGRAM_BYTES = 49 * 1024 * 1024  # ~49MB limit for direct Telegram upload
DATA_FILE = "bot_data.json"
WEBHOOK_BASE = os.environ.get("WEBHOOK_BASE", "")  # set this on your host if webhook desired
SECRET_PATH = os.environ.get("SECRET_PATH", "secret_path_example")
WEBHOOK_PATH = f"/{SECRET_PATH}/{BOT_TOKEN.split(':')[0]}"

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("universal_bot")

# load or init data
if os.path.exists(DATA_FILE):
    try:
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            DATA = json.load(f)
    except Exception:
        DATA = {"users": {}, "banned": [], "langs": {}, "current_links": {}}
else:
    DATA = {"users": {}, "banned": [], "langs": {}, "current_links": {}}

def save_data():
    try:
        with open(DATA_FILE, "w", encoding="utf-8") as f:
            json.dump(DATA, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.exception("save_data error: %s", e)

# initialize bot & optional flask
bot = telebot.TeleBot(BOT_TOKEN, parse_mode="HTML")
app = Flask(name)

# yt-dlp defaults
YTDL_COMMON = {
    "noplaylist": True,
    "quiet": True,
    "no_warnings": True,
    "cachedir": False,
}

# optional S3 client lazy
s3_client = None
if S3_ENDPOINT and S3_ACCESS_KEY and S3_SECRET_KEY:
    try:
        import boto3
        s3_client = boto3.client(
            "s3",
            endpoint_url=S3_ENDPOINT,
            aws_access_key_id=S3_ACCESS_KEY,
            aws_secret_access_key=S3_SECRET_KEY
        )
        logger.info("S3 client initialized")
    except Exception as e:
        logger.exception("S3 init failed: %s", e)
        s3_client = None

# i18n strings
STRINGS = {
    "start_uz": "👋 Assalomu alaykum!\nLink yuboring (YouTube/Instagram/TikTok...)\nTilni tanlang:",
    "start_ru": "👋 Привет!\nОтправьте ссылку (YouTube/Instagram/TikTok...)\nВыберите язык:",
    "start_en": "👋 Hi!\nSend a link (YouTube/Instagram/TikTok...)\nChoose language:",

    "choose_format_uz": "Link qabul qilindi ✅\nQaysi formatni xohlaysiz?",
    "choose_format_ru": "Ссылка принята ✅\nКакой формат хотите?",
    "choose_format_en": "Link received ✅\nWhich format do you want?",

    "downloading_uz": "⏳ Yuklanmoqda — biroz kuting...",
    "downloading_ru": "⏳ Загружаю — подождите...",
    "downloading_en": "⏳ Downloading — please wait...",

    "no_link_uz": "Iltimos, toʻliq link yuboring (https://...).",
    "no_link_ru": "Пожалуйста, отправьте ссылку (https://...).",
    "no_link_en": "Please send a full link (https://...).",

    "not_admin_uz": "Siz admin emassiz.",
    "not_admin_ru": "Вы не админ.",
    "not_admin_en": "You are not an admin."
}

def tr(key, lang="uz", *args):
    txt = STRINGS.get(f"{key}_{lang}") or STRINGS.get(f"{key}_uz") or "..."
    if args:
        try:
            return txt.format(*args)
        except:
            return txt
    return txt

# helpers
def looks_like_url(text: str) -> bool:
    return text.startswith("http://") or text.startswith("https://")

def safe_filename(s: str) -> str:
    return "".join(c if c.isalnum() or c in " ._-()" else "_" for c in s)[:200]

def download_with_yt_dlp(url: str, mode: str="video", outdir: str=None):
    opts = YTDL_COMMON.copy()
    if mode == "audio":
        opts.update({
            "format": "bestaudio/best",
            "postprocessors": [{
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
                "preferredquality": "192",
            }],
        })
        outtmpl = "%(id)s.%(ext)s"
    else:
        opts.update({
            "format": "bestvideo+bestaudio/best",
            "merge_output_format": "mp4",
        })
        outtmpl = "%(id)s.%(ext)s"

    if outdir:
        opts["outtmpl"] = os.path.join(outdir, outtmpl)
    else:
        opts["outtmpl"] = outtmpl

    with YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=True)
        # try locate downloaded file
        if outdir:
            for f in os.listdir(outdir):
                full = os.path.join(outdir, f)
                if os.path.isfile(full):
                    return full, info
        ext = info.get("ext", "mp4")
        name = f"{info.get('id')}.{ext}"
        if os.path.exists(name):
            return name, info
        return None, info

def upload_file_to_s3(filepath: str, key: str):
    if not s3_client:
        raise RuntimeError("S3 client not configured.")
    s3_client.upload_file(filepath, S3_BUCKET, key)
    url = s3_client.generate_presigned_url('get_object', Params={'Bucket': S3_BUCKET, 'Key': key}, ExpiresIn=7*24*3600)
    return url

def upload_to_transfersh(filepath: str):
    name = os.path.basename(filepath)
    try:
        with open(filepath, "rb") as f:
            r = requests.put(f"https://transfer.sh/{name}", data=f, timeout=300)
        if r.status_code in (200,201):
            return r.text.strip()
    except Exception as e:
        logger.exception("transfer.sh upload failed: %s", e)
    return None

# keyboards
def lang_keyboard():
    kb = InlineKeyboardMarkup(row_width=3)
    kb.add(
        InlineKeyboardButton("🇺🇿 O'zbek", callback_data="lang_uz"),
        InlineKeyboardButton("🇷🇺 Русский", callback_data="lang_ru"),
        InlineKeyboardButton("🇬🇧 English", callback_data="lang_en"),
    )
    return kb

def format_keyboard(lang="uz"):
    kb = InlineKeyboardMarkup()
    kb.add(InlineKeyboardButton("📹 MP4", callback_data="format_mp4"))
    kb.add(InlineKeyboardButton("🎵 MP3", callback_data="format_mp3"))
    kb.add(InlineKeyboardButton("🔗 Share link", callback_data="share_link"))
    kb.add(InlineKeyboardButton("🌐 Change language", callback_data="change_lang"))
    return kb

def main_reply_keyboard(lang="uz"):
    kb = ReplyKeyboardMarkup(resize_keyboard=True)
    if lang == "ru":
        kb.add(KeyboardButton("📥 Send link"), KeyboardButton("🧑‍💻 Admin panel"))
    elif lang == "en":
        kb.add(KeyboardButton("📥 Send link"), KeyboardButton("🧑‍💻 Admin panel"))
    else:
        kb.add(KeyboardButton("📥 Link yuboring"), KeyboardButton("🧑‍💻 Admin panel"))
    return kb

# admin decorator
def admin_only(fn):
    @wraps(fn)
    def wrapper(message, *args, **kwargs):
        uid = getattr(message, "from_user", None)
        uid = uid.id if uid else (message.chat.id if hasattr(message, "chat") else None)
        if uid not in ADMIN_IDS:
            lang = DATA["langs"].get(str(uid), "uz")
            bot.send_message(uid, tr("not_admin", lang))
            return
        return fn(message, *args, **kwargs)
    return wrapper

# Handlers
@bot.message_handler(commands=["start", "help"])
def handle_start(message):
    uid = message.from_user.id
    DATA["users"].setdefault(str(uid), {"first_name": message.from_user.first_name or "", "id": uid})
    save_data()
    lang = DATA["langs"].get(str(uid), "uz")
    bot.send_message(uid, tr("start", lang), reply_markup=lang_keyboard())

@bot.callback_query_handler(func=lambda c: c.data and c.data.startswith("lang_"))
def callback_set_lang(call):
    code = call.data.split("_",1)[1]
    uid = call.from_user.id
    DATA["langs"][str(uid)] = code
    save_data()
    bot.answer_callback_query(call.id, "Language saved")
    bot.send_message(uid, tr("start", code), reply_markup=main_reply_keyboard(code))

@bot.message_handler(func=lambda m: True)
def main_text(m):
    uid = m.from_user.id
    if str(uid) in DATA.get("banned", []):
        return
    text = (m.text or "").strip()
    lang = DATA["langs"].get(str(uid), "uz")

    # Admin panel trigger
    if text.lower().startswith("/admin") or text == "🧑‍💻 Admin panel":
        if uid not in ADMIN_IDS:
            return bot.send_message(uid, tr("not_admin", lang))
        kb = InlineKeyboardMarkup()
        kb.add(InlineKeyboardButton("📊 Stats", callback_data="adm_stats"))
        kb.add(InlineKeyboardButton("📢 Broadcast", callback_data="adm_broadcast"))
        kb.add(InlineKeyboardButton("🚫 Ban/Unban", callback_data="adm_ban"))
        bot.send_message(uid, "Admin panel:", reply_markup=kb)
        return

    # URL received -> ask format
    if looks_like_url(text):
        DATA["current_links"][str(uid)] = text
        save_data()
        bot.send_message(uid, tr("choose_format", lang), reply_markup=format_keyboard(lang))
        return

    # fallback
    bot.send_message(uid, tr("no_link", lang))

@bot.callback_query_handler(func=lambda c: True)
def callback_all(call):
    uid = call.from_user.id
    data = call.data
    lang = DATA["langs"].get(str(uid), "uz")

    # Admin callbacks
    if data.startswith("adm_"):
        if uid not in ADMIN_IDS:
            return bot.answer_callback_query(call.id, tr("not_admin", lang))
        if data == "adm_stats":
            users_count = len(DATA.get("users", {}))
            banned_count = len(DATA.get("banned", []))
            bot.send_message(uid, f"Users: {users_count}\nBanned: {banned_count}")
        elif data == "adm_broadcast":
            bot.send_message(uid, "Send /broadcast <message> to send to everyone")
        elif data == "adm_ban":
            bot.send_message(uid, "Use /ban <user_id> and /unban <user_id>")
        return

    # change language
    if data == "change_lang":
        bot.answer_callback_query(call.id, "Choose language")
        bot.send_message(uid, "Choose language:", reply_markup=lang_keyboard())
        return

    # share link
    if data == "share_link":
        link = DATA.get("current_links", {}).get(str(uid), "")
        if not link:
            bot.answer_callback_query(call.id, "Send link first")
            return
        caption = f"📲 I found: {link}\n@YourBotUsername"
        bot.send_message(uid, f"Share this (copy & send):\n\n{caption}")
        bot.answer_callback_query(call.id, "Copied")
        return

    # format selection
    if data in ("format_mp4", "format_mp3"):
        link = DATA.get("current_links", {}).get(str(uid))
        if not link:
            bot.answer_callback_query(call.id, "Send link first")
            return
        bot.edit_message_text(tr("downloading", lang), uid, call.message.message_id)
        tmpdir = tempfile.mkdtemp(prefix="dl_")
        try:
            mode = "audio" if data == "format_mp3" else "video"
            filepath, info = download_with_yt_dlp(link, mode=mode, outdir=tmpdir)
            if not filepath or not os.path.exists(filepath):

bot.edit_message_text(tr("error", lang, "Download failed"), uid, call.message.message_id)
                return
            size = os.path.getsize(filepath)
            title = info.get("title") or os.path.basename(filepath)
            if size <= MAX_TELEGRAM_BYTES:
                with open(filepath, "rb") as f:
                    if mode == "audio":
                        bot.send_audio(uid, f, title=title)
                    else:
                        bot.send_document(uid, f, caption=title)
                bot.delete_message(uid, call.message.message_id)
            else:
                bot.edit_message_text("📤 File too big — creating download link...", uid, call.message.message_id)
                link_to_send = None
                # try S3
                if s3_client and S3_BUCKET:
                    try:
                        key = f"{info.get('id')}.{info.get('ext','mp4')}"
                        link_to_send = upload_file_to_s3(filepath, key)
                    except Exception as e:
                        logger.exception("S3 upload failed: %s", e)
                if not link_to_send:
                    link_to_send = upload_to_transfersh(filepath)
                if link_to_send:
                    bot.send_message(uid, f"🔗 Download link:\n{link_to_send}")
                else:
                    bot.send_message(uid, tr("error", lang, "Upload failed"))
                bot.delete_message(uid, call.message.message_id)
        except Exception as ex:
            logger.exception("processing failed")
            bot.edit_message_text(tr("error", lang, str(ex)), uid, call.message.message_id)
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)
        return

# Admin text commands
@bot.message_handler(commands=['broadcast'])
def cmd_broadcast(m):
    if m.from_user.id not in ADMIN_IDS:
        return bot.send_message(m.chat.id, tr("not_admin", DATA["langs"].get(str(m.from_user.id),"uz")))
    text = m.text.partition(" ")[2].strip()
    if not text:
        return bot.send_message(m.chat.id, "Usage: /broadcast Your message here")
    cnt = 0
    for uid_str in list(DATA.get("users", {}).keys()):
        try:
            bot.send_message(int(uid_str), text)
            cnt += 1
        except Exception:
            pass
    bot.send_message(m.chat.id, f"Sent to {cnt} users.")

@bot.message_handler(commands=['ban'])
def cmd_ban(m):
    if m.from_user.id not in ADMIN_IDS:
        return bot.send_message(m.chat.id, tr("not_admin", DATA["langs"].get(str(m.from_user.id),"uz")))
    parts = m.text.split()
    if len(parts) < 2:
        return bot.send_message(m.chat.id, "Usage: /ban <user_id>")
    uid = parts[1].strip()
    if uid not in DATA.get("banned", []):
        DATA.setdefault("banned", []).append(uid)
        save_data()
    bot.send_message(m.chat.id, f"User {uid} banned.")

@bot.message_handler(commands=['unban'])
def cmd_unban(m):
    if m.from_user.id not in ADMIN_IDS:
        return bot.send_message(m.chat.id, tr("not_admin", DATA["langs"].get(str(m.from_user.id),"uz")))
    parts = m.text.split()
    if len(parts) < 2:
        return bot.send_message(m.chat.id, "Usage: /unban <user_id>")
    uid = parts[1].strip()
    if uid in DATA.get("banned", []):
        DATA["banned"].remove(uid)
        save_data()
    bot.send_message(m.chat.id, f"User {uid} unbanned.")

@bot.message_handler(commands=['stats'])
def cmd_stats(m):
    if m.from_user.id not in ADMIN_IDS:
        return bot.send_message(m.chat.id, tr("not_admin", DATA["langs"].get(str(m.from_user.id),"uz")))
    users_count = len(DATA.get("users", {}))
    banned_count = len(DATA.get("banned", []))
    bot.send_message(m.chat.id, f"Users: {users_count}\nBanned: {banned_count}")

# Flask webhook endpoint

@app.route(WEBHOOK_PATH, methods=['POST'])
def webhook():
    if request.headers.get("content-type") == "application/json":
        json_string = request.get_data().decode("utf-8")
        update = telebot.types.Update.de_json(json_string)
        bot.process_new_updates([update])
        return "", 200
    else:
        abort(403)

@app.route("/")
def index():
    return "Universal Downloader Bot is running!"

# start
if name == "main":
    # warn if token still sample
    if BOT_TOKEN == "YOUR_BOT_TOKEN_HERE":
        logger.warning("WARNING: BOT_TOKEN is sample placeholder — replace with your real token before production!")

    if WEBHOOK_BASE:
        webhook_url = WEBHOOK_BASE.rstrip("/") + WEBHOOK_PATH
        try:
            bot.remove_webhook()
        except:
            pass
        try:
            bot.set_webhook(url=webhook_url)
            logger.info("Webhook set to %s", webhook_url)
        except Exception as e:
            logger.exception("Webhook set failed: %s", e)
    try:
        bot.infinity_polling(timeout=60, long_polling_timeout=60)
    except KeyboardInterrupt:
        pass
