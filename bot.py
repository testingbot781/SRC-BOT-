# testbot.py
import os, threading
from flask import Flask
from pyrogram import Client, filters

API_ID     = int(os.getenv("API_ID"))
API_HASH   = os.getenv("API_HASH")
BOT_TOKEN  = os.getenv("BOT_TOKEN")

bot = Client("check", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

app = Flask(__name__)
@app.route("/", methods=["GET","POST"])
def home(): return "Flask OK"

def keep_alive():
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)

@bot.on_message(filters.command("start"))
async def start(_, m):
    await m.reply_text("✅ It works — Pyrogram is alive!")

if __name__ == "__main__":
    print("💠 Starting test bot…")
    threading.Thread(target=keep_alive).start()
    bot.run()
