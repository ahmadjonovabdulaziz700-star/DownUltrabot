import telebot
import requests
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
import os

TOKEN = os.getenv("BOT_TOKEN")
bot = telebot.TeleBot(TOKEN)

# Universal yuklab beruvchi API
def download_media(url):
    api = f"https://api.ryzendesu.vip/downloader?url={url}"
    try:
        r = requests.get(api, timeout=20).json()
        return r["result"][0]["url"]
    except:
        return None


@bot.message_handler(commands=['start'])
def start(message):
    bot.send_message(
        message.chat.id,
        "👋 *DownUltra Botga xush kelibsiz!*\n\n"
        "🎥 Video, 🎵 Qo‘shiq yoki link tashlang — men sizga yuklab beraman.\n\n"
        "⚡ *Eng tez va cheksiz yuklab beruvchi bot!*",
        parse_mode="Markdown"
    )


@bot.message_handler(func=lambda m: True)
def handle_message(message):
    url = message.text.strip()

    # kuting
    loading = bot.reply_to(message, "⏳ Yuklanmoqda...")

    file_url = download_media(url)

    if not file_url:
        bot.delete_message(message.chat.id, loading.message_id)
        bot.send_message(message.chat.id, "❌ *Yuklab bo‘lmadi. Linkni tekshiring!*", parse_mode="Markdown")
        return

    # Share / Bizning bot knopkasi
    markup = InlineKeyboardMarkup()
    markup.add(
        InlineKeyboardButton("🔗 Do‘stlarga ulashish", switch_inline_query=url),
        InlineKeyboardButton("🤖 Bizning bot", url="https://t.me/DownUltrabot")
    )

    bot.delete_message(message.chat.id, loading.message_id)

    # Video yoki audio bo‘lishi mumkin — telebot avtomatik tanlaydi
    try:
        bot.send_video(
            message.chat.id,
            file_url,
            caption="✅ *Tayyor!*\n\n@DownUltraBot orqali yuklandi.",
            reply_markup=markup,
            parse_mode="Markdown"
        )
    except:
        bot.send_audio(
            message.chat.id,
            file_url,
            caption="✅ *Tayyor!*",
            reply_markup=markup,
            parse_mode="Markdown"
        )


bot.infinity_polling()
import os
from flask import Flask

app = Flask(name)

@app.route('/')
def home():
    return "Bot is running!"

if name == "main":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
