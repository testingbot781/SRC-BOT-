import os, sys, threading
from flask import Flask
from pyrogram import Client, filters

# flush output instantly so Render shows errors
sys.stdout.reconfigure(line_buffering=True)
sys.stderr.reconfigure(line_buffering=True)

API_ID     = int(os.getenv("API_ID"))
API_HASH   = os.getenv("API_HASH")
BOT_TOKEN  = os.getenv("BOT_TOKEN")

bot = Client("serena_test", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

web = Flask(__name__)
@web.route("/", methods=["GET","HEAD","POST"])
def home(): return "OK"

def run_web():
    port = int(os.environ.get("PORT", 10000))
    web.run(host="0.0.0.0", port=port, threaded=True)

@bot.on_message(filters.command("start"))
async def hi(_, m):
    await m.reply_text("✅ SERENA here — Telemetry alive!")

if __name__ == "__main__":
    print("🚀 Reaching bot.run() — Flask thread + polling starting now")
    t = threading.Thread(target=run_web, daemon=True)
    t.start()
    bot.run()
