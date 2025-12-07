import os, aiohttp, asyncio, math, psutil, shutil
from pyrogram import Client, filters
from pyrogram.errors import FloodWait
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message

# ========= CONFIG ===========
API_ID = int(os.getenv("API_ID"))
API_HASH = os.getenv("API_HASH")
BOT_TOKEN = os.getenv("BOT_TOKEN")
MONGO_URL = os.getenv("MONGO_URL")

OWNER_ID = 1598576202
LOGS_CHANNEL = -1003286415377
FORCE_SUB_LINK = "https://t.me/serenaunzipbot"

# ========= CLIENT ===========
app = Client("direct_url_downloader", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

# ========= UTILITIES =========
async def progress_bar(current, total):
    filled_length = int(18 * current / total)
    bar = "●" * filled_length + "○" * (18 - filled_length)
    percent = (current / total) * 100
    return bar, percent

async def humanbytes(size):
    # nicely formatted
    for unit in ['B','KB','MB','GB','TB']:
        if size < 1024: return f"{size:.2f} {unit}"
        size /= 1024

async def check_fsub(client, message):
    try:
        user = await client.get_chat_member("@serenaunzipbot", message.from_user.id)
        if user.status in ["member","administrator","creator"]:
            return True
        else:
            return False
    except:
        keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("🔔 Join Updates Channel", url=FORCE_SUB_LINK)]])
        await message.reply_text("⭐ Bot use karne se pehle join karlo Update Channel!", reply_markup=keyboard)
        return False

# ============== COMMANDS ==================
@app.on_message(filters.command("start"))
async def start_cmd(client, message):
    if not await check_fsub(client, message): return
    text = (
        "Hey 👋, I'm your **Direct URL Downloader Bot!**\n\n"
        "Just send me any direct stream/download link & I'll fetch and send the file to you.\n"
        "Use /help for instructions."
    )
    btn = InlineKeyboardMarkup([
        [InlineKeyboardButton("👑 Owner", url="https://t.me/technicalserena")],
        [InlineKeyboardButton("💬 Updates Channel", url=FORCE_SUB_LINK)]
    ])
    await message.reply_text(text, disable_web_page_preview=True, reply_markup=btn)

@app.on_message(filters.command("help"))
async def help_cmd(client, message):
    if not await check_fsub(client, message): return
    await message.reply_text(
        "**Usage:**\n\n"
        "🔗 Send me a direct downloadable URL (mp4, zip, etc)\n"
        "📥 I’ll download it and send back to you privately.\n\n"
        "**Commands:**\n"
        "/help - Show this help\n"
        "/status - Owner only (Bot CPU, RAM, Disk)\n",
        disable_web_page_preview=True)

@app.on_message(filters.command("status") & filters.user(OWNER_ID))
async def status_cmd(client, message):
    cpu = psutil.cpu_percent()
    mem = psutil.virtual_memory().percent
    disk = psutil.disk_usage('/').percent
    uptime = shutil.disk_usage("/")
    await message.reply_text(
        f"**System Status ⚙️**\n"
        f"CPU: {cpu}%\n"
        f"RAM: {mem}% used\n"
        f"Disk: {disk}% used\n"
    )

# ============== MAIN DOWNLOADER ===============
@app.on_message(filters.private & ~filters.command(["start","help","status"]))
async def downloader(client, message: Message):
    if not await check_fsub(client, message): return
    url = message.text.strip()
    msg = await message.reply_text(f"📥 Starting Download...\n`{url}`")

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url) as resp:
                size = int(resp.headers.get('Content-Length', 0))
                file_name = url.split("/")[-1]
                with open(file_name, "wb") as f:
                    downloaded = 0
                    chunk = 1024 * 1024
                    while True:
                        data = await resp.content.read(chunk)
                        if not data: break
                        f.write(data)
                        downloaded += len(data)
                        bar, percent = await progress_bar(downloaded, size)
                        done_mb = await humanbytes(downloaded)
                        total_mb = await humanbytes(size)
                        sp = f"Downloading\n`{file_name}`\n[{bar}] \n◌Progress😉:〘 {percent:.2f}% 〙\nDone:〘{done_mb} of {total_mb}〙"
                        try:
                            await msg.edit_text(sp)
                        except FloodWait as e:
                            await asyncio.sleep(e.value)
                await msg.edit_text("✅ Downloaded Successfully. Uploading...")

        send_msg = await client.send_document(
            message.from_user.id,
            file_name,
            caption=f"File: `{file_name}`"
        )

        os.remove(file_name)
        await msg.delete()
        await client.send_message(LOGS_CHANNEL, f"📤 File Sent to {message.from_user.mention} — `{file_name}`")

    except Exception as e:
        await message.reply_text(f"❌ Error: `{e}`")
        await client.send_message(LOGS_CHANNEL, f"⚠️ Exception: {e}")

# ==============================================
print("🤖 Bot Running...")
app.run()
