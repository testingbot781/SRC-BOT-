import os, aiohttp, asyncio, math, psutil, shutil, threading
from pyrogram import Client, filters
from pyrogram.errors import FloodWait
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message
from flask import Flask

# ===== CONFIGURATION =====
API_ID = int(os.getenv("API_ID"))
API_HASH = os.getenv("API_HASH")
BOT_TOKEN = os.getenv("BOT_TOKEN")
MONGO_URL = os.getenv("MONGO_URL")

OWNER_ID = 1598576202
LOGS_CHANNEL = -1003286415377
FORCE_SUB_LINK = "https://t.me/serenaunzipbot"

# ===== SETUP =====
app = Client("direct_downloader", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

# ===== DUMMY WEB SERVER (Render Port Fix) =====
flask_app = Flask(__name__)
@flask_app.route('/')
def home():
    return "Bot running perfectly!"

def run_flask():
    port = int(os.environ.get("PORT", 8080))
    flask_app.run(host="0.0.0.0", port=port)

threading.Thread(target=run_flask).start()

# ===== UTILITIES =====
async def progress_bar(current, total):
    filled_length = int(18 * current / total)
    bar = "●" * filled_length + "○" * (18 - filled_length)
    percent = (current / total) * 100
    return bar, percent

async def humanbytes(size):
    for unit in ['B','KB','MB','GB','TB']:
        if size < 1024: return f"{size:.2f} {unit}"
        size /= 1024

async def check_fsub(client, message):
    try:
        user = await client.get_chat_member("@serenaunzipbot", message.from_user.id)
        if user.status in ["member", "administrator", "creator"]:
            return True
        return False
    except:
        buttons = [[InlineKeyboardButton("🔔 Join Updates Channel", url=FORCE_SUB_LINK)]]
        await message.reply_text("⭐ Pehle Update Channel join karo!", reply_markup=InlineKeyboardMarkup(buttons))
        return False

# ===== COMMANDS =====
@app.on_message(filters.command("start"))
async def start_(client, message):
    if not await check_fsub(client, message): return
    btn = InlineKeyboardMarkup([
        [InlineKeyboardButton("👑 Owner", url="https://t.me/technicalserena")],
        [InlineKeyboardButton("💬 Updates Channel", url=FORCE_SUB_LINK)]
    ])
    await message.reply_text(
        "Hey 👋, I'm **Direct URL Downloader Bot!**\n\n"
        "Just send me a direct download link and I’ll fetch & DM it to you.\n\n"
        "Use /help for details.",
        reply_markup=btn,
        disable_web_page_preview=True
    )

@app.on_message(filters.command("help"))
async def help_(client, message):
    if not await check_fsub(client, message): return
    await message.reply_text(
        "**Commands:**\n"
        "/help — show this help\n"
        "/status — owner only (CPU, RAM, Disk)\n\n"
        "Send any downloadable link and I’ll send the file 💫"
    )

@app.on_message(filters.command("status") & filters.user(OWNER_ID))
async def status_(client, message):
    cpu = psutil.cpu_percent()
    mem = psutil.virtual_memory().percent
    disk = psutil.disk_usage('/').percent
    await message.reply_text(f"**Bot System Status ⚙️**\nCPU: {cpu}%\nRAM: {mem}%\nDisk: {disk}%")

# ===== MAIN DOWNLOADER =====
@app.on_message(filters.private & ~filters.command(["start","help","status"]))
async def download_(client, message: Message):
    if not await check_fsub(client, message): return
    url = message.text.strip()
    msg = await message.reply_text(f"📥 Starting download...\n`{url}`")

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url) as resp:
                size = int(resp.headers.get('Content-Length', 0))
                filename = url.split("/")[-1]
                with open(filename, "wb") as f:
                    downloaded = 0
                    chunk = 1024 * 1024
                    while True:
                        data = await resp.content.read(chunk)
                        if not data:
                            break
                        f.write(data)
                        downloaded += len(data)
                        bar, per = await progress_bar(downloaded, size)
                        done_h = await humanbytes(downloaded)
                        total_h = await humanbytes(size)
                        info = (f"Downloading\n`{filename}`\n[{bar}]"
                                f"\n◌Progress😉:〘 {per:.2f}% 〙"
                                f"\nDone:〘{done_h} of {total_h}〙")
                        try:
                            await msg.edit_text(info)
                        except FloodWait as e:
                            await asyncio.sleep(e.value)
                await msg.edit_text("✅ Download completed! Uploading...")

        await client.send_document(message.from_user.id, filename, caption=f"`{filename}`")
        await client.send_message(LOGS_CHANNEL, f"📤 Sent `{filename}` to {message.from_user.mention}")
        os.remove(filename)
        await msg.delete()

    except Exception as e:
        await message.reply_text(f"❌ Error: {e}")
        await client.send_message(LOGS_CHANNEL, f"⚠️ Error: {e}")

print("✅ Bot is running and Flask web port active for Render!")
app.run()
