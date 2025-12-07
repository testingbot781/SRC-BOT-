import os, threading
from flask import Flask
from pyrogram import Client, filters

API_ID = int(os.getenv("API_ID"))
API_HASH = os.getenv("API_HASH")
BOT_TOKEN = os.getenv("BOT_TOKEN")

bot = Client("serena_test", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

web = Flask(__name__)
@web.route("/", methods=["GET", "HEAD"]) 
def home(): 
    return "OK"

def run_web(): 
    web.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)), threaded=True)

@bot.on_message(filters.command("start"))
async def hi(_, msg):
    await msg.reply_text("✅ Alive and responding!")

if __name__ == "__main__":
    print("Booting SERENA...")
    threading.Thread(target=run_web, daemon=True).start()
    bot.run()
