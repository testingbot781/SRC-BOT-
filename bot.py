import os, aiohttp, asyncio, math, psutil, shutil, threading, random
from pyrogram import Client, filters, enums
from pyrogram.errors import FloodWait, UserIsBlocked
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message
from flask import Flask
from pymongo import MongoClient
from datetime import datetime

# ========= CONFIG ===========
API_ID = int(os.getenv("API_ID"))
API_HASH = os.getenv("API_HASH")
BOT_TOKEN = os.getenv("BOT_TOKEN")
MONGO_URL = os.getenv("MONGO_URL")
OWNER_ID = 1598576202
LOGS_CHANNEL = -1003286415377
FORCE_SUB_LINK = "https://t.me/serenaunzipbot"

# ========= DB ===========
mongo = MongoClient(MONGO_URL)
db = mongo["serena_bot"]
users_col = db["users"]

# ========= BOT CLIENT ===========
app = Client(
    "serena_direct_downloader",
    api_id=API_ID,
    api_hash=API_HASH,
    bot_token=BOT_TOKEN,
    parse_mode=enums.ParseMode.MARKDOWN
)

# ========= RENDER PORT FIX ===========
flask_app = Flask(__name__)
@flask_app.route('/')
def index(): return "SERENA bot running smooth ✨"
def flask_run():
    port = int(os.environ.get("PORT", 8080))
    flask_app.run(host="0.0.0.0", port=port)
threading.Thread(target=flask_run).start()

# ========= UTILITIES ===========
def fancy_eta_bar(pct):
    total = 17
    done = int(total * pct / 100)
    bar = "".join(["●" if i < done else "○" for i in range(total)])
    face = random.choice(["😉","🤓","😎","✨"])
    return f"[{bar}]\n◌Progress{face}:〘 {pct:.2f}% 〙"

def fmt_size(size):
    for unit in ['B','KB','MB','GB']:
        if size < 1024: return f"{size:.2f} {unit}"
        size /= 1024

async def ensure_user(uid):
    # Ensure user is in DB
    if not users_col.find_one({"_id": uid}):
        users_col.insert_one({"_id": uid, "joined": datetime.utcnow(), "blocked": False})

async def send_log(text):
    try: await app.send_message(LOGS_CHANNEL, text[:4096])
    except: pass

async def check_fsub(client, message):
    try:
        user = await client.get_chat_member("@serenaunzipbot", message.from_user.id)
        if user.status in ["member", "administrator", "creator"]:
            return True
        raise Exception
    except:
        kb = InlineKeyboardMarkup(
            [[InlineKeyboardButton("🔔 Join Updates Channel", url=FORCE_SUB_LINK)]])
        await message.reply_text(
            "💫 First join our update channel to continue, sweetheart 💝",
            reply_markup=kb)
        return False

# ========= COMMANDS ===========

@app.on_message(filters.command("start"))
async def start_(c, m):
    if not await check_fsub(c, m): return
    await ensure_user(m.from_user.id)
    brand = "**💎 SERENA — Direct URL Downloader Bot 💎**"
    caption = (f"{brand}\n\n"
               "Send me any **direct download link** (mp4, zip, etc) and "
               "I’ll fetch + DM the file to you 🌸")
    btn = InlineKeyboardMarkup([
        [InlineKeyboardButton("📢 Join Channel", url=FORCE_SUB_LINK)],
        [InlineKeyboardButton("💬 Contact Owner", url="https://t.me/technicalserena")]
    ])
    await m.reply_text(caption, reply_markup=btn, disable_web_page_preview=True)

@app.on_message(filters.command("help"))
async def help_(c, m):
    if not await check_fsub(c, m): return
    text = (
        "🌈 **How to use SERENA:**\n\n"
        "1️⃣ Copy any *direct* downloadable link (e.g. https://example.com/video.mp4)\n"
        "2️⃣ Paste it here 🪄 and relax\n"
        "3️⃣ Bot will show fancy ETA progress bar while downloading ⏳\n"
        "4️⃣ File will land in your DM automatically 📥\n\n"
        "⚙️ Commands:\n"
        "`/help` — show this guide 📘\n"
        "`/status` — owner system + user stats 🧮\n"
        "`/broadcast` — send message to all (active users only)\n\n"
        "Have fun & stay awesome 💖"
    )
    await m.reply_text(text, disable_web_page_preview=True)

@app.on_message(filters.command("status") & filters.user(OWNER_ID))
async def status_(c, m):
    total = users_col.count_documents({})
    blocked = users_col.count_documents({"blocked": True})
    active = total - blocked
    cpu = psutil.cpu_percent()
    mem = psutil.virtual_memory().percent
    disk = psutil.disk_usage('/').percent
    await m.reply_text(
        f"**⚙️ System & User Status**\n\n"
        f"🧍‍♂️Total Users : {total}\n"
        f"🚷Blocked : {blocked}\n"
        f"🟢Active : {active}\n\n"
        f"💻CPU : {cpu}%\n"
        f"💾RAM : {mem}%\n"
        f"💽Disk : {disk}%\n"
    )

@app.on_message(filters.command("broadcast") & filters.user(OWNER_ID))
async def broadcast_(c, m):
    if len(m.command) < 2:
        return await m.reply_text("Usage: `/broadcast <message>`")
    text = m.text.split(" ", 1)[1]
    users = list(users_col.find({}))
    sent, dead = 0, 0
    await m.reply_text("📣 Broadcast started…")
    for usr in users:
        try:
            await c.send_message(usr["_id"], text)
            sent += 1
        except UserIsBlocked:
            users_col.update_one({"_id": usr["_id"]}, {"$set": {"blocked": True}})
            dead += 1
        except Exception:
            dead += 1
        await asyncio.sleep(0.05)
    await m.reply_text(f"✅ Broadcast complete!\nDelivered to {sent} users\nFailed/Blocked {dead}")
    await send_log(f"Broadcast summary — Sent:{sent} Failed:{dead}")

# ========= DOWNLOADER ===========

@app.on_message(filters.private & ~filters.command(["start","help","status","broadcast"]))
async def grab_link(c, m: Message):
    if not await check_fsub(c, m): return
    await ensure_user(m.from_user.id)
    url = m.text.strip()
    if not (url.startswith("http://") or url.startswith("https://")):
        return await m.reply_text(
            "😅 That doesn’t look like a valid link!\n"
            "Please send a *direct* downloadable URL 🪄\n\n"
            "Example:\n`https://example.com/MyVideo.mp4`")
    progress = await m.reply_text("📥 Starting download…")
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(url) as r:
                size = int(r.headers.get("Content-Length", 0))
                filename = url.split("/")[-1]
                with open(filename, "wb") as f:
                    done = 0
                    chunk = 1024 * 1024
                    while True:
                        data = await r.content.read(chunk)
                        if not data: break
                        f.write(data); done += len(data)
                        pct = done / size * 100 if size else 0
                        bar = fancy_eta_bar(pct)
                        done_h, total_h = fmt_size(done), fmt_size(size)
                        txt = (f"**Downloading** \n`{filename}`\n{bar}\n"
                               f"Done:〘{done_h} / {total_h}〙")
                        try: await progress.edit_text(txt)
                        except FloodWait as e: await asyncio.sleep(e.value)
        await progress.edit_text("✅ Download finished — uploading to you 💨")
        await c.send_document(m.from_user.id, filename)
        await send_log(f"📤 Sent `{filename}` to {m.from_user.mention}")
        os.remove(filename)
        await progress.delete()
    except Exception as e:
        await send_log(f"❌ Error: {e}")
        await m.reply_text(f"Oops 😢\nError encountered:\n`{e}`")

# ========= SAFETY FALLBACK ===========
@app.on_message(filters.command(""))
async def unknown(c, m):
    await m.reply_text("Use /help to know correct commands 💬")

@app.on_message(filters.private & filters.text & ~filters.command(["start","help","status","broadcast"]))
async def guard(c,m):
    # Fallback handled in main section; this duplicates check to ensure friendly msg
    if not (m.text.startswith("http://") or m.text.startswith("https://")):
        await m.reply_text("🌸 Oops dear, wrong input!\nSend a proper download link like:\n"
                           "`https://example.com/file.zip`")

# ========= RUN ===========
print("💠 SERENA bot is alive and Web port active for Render!")
app.run()
