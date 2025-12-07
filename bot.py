import os, threading
from flask import Flask
from pyrogram import Client, filters

# --- Environment (Render → Environment tab) ---
API_ID     = int(os.getenv("API_ID"))
API_HASH   = os.getenv("API_HASH")
BOT_TOKEN  = os.getenv("BOT_TOKEN")

# --- Telegram client ---
bot = Client("serena_debug", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

# --- Flask tiny server to keep an HTTP port open for Render ---
web = Flask(__name__)

@web.route("/", methods=["GET", "HEAD", "POST"])
def home():
    return "💠 SERENA alive — Flask health OK"

def run_web():
    port = int(os.environ.get("PORT", 10000))
    # threaded=True lets Flask answer pings while Pyrogram runs
    web.run(host="0.0.0.0", port=port, threaded=True)

# --- Simple /start reply so you can verify connection ---
@bot.on_message(filters.command("start"))
async def start(_, message):
    await message.reply_text("✅ Hello from SERENA — bot connected and listening!")

# --- Entry point ---
if __name__ == "__main__":
    print("💠 Booting SERENA for Render: Flask + Pyrogram polling together")

    # Start Flask on a daemon thread so it doesn’t block Pyrogram
    t = threading.Thread(target=run_web, daemon=True)
    t.start()

    # Start Telegram long‑polling (this keeps the process alive)
    bot.run()
