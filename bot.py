import os, aiohttp, asyncio, time, psutil, shutil, threading
from datetime import datetime
from pyrogram import Client, filters, enums
from pyrogram.errors import FloodWait, UserNotParticipant, UserIsBlocked
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from flask import Flask
from pymongo import MongoClient

# ========== CONFIG ==========
API_ID = int(os.getenv("API_ID"))
API_HASH = os.getenv("API_HASH")
BOT_TOKEN = os.getenv("BOT_TOKEN")
MONGO_URL = os.getenv("MONGO_URL")

OWNER_ID = 1598576202
LOGS_CHANNEL = -1003286415377           # channel for all logs
FORCE_SUB_CHANNEL = "serenaunzipbot"    # username only (without "@")
FORCE_SUB_LINK = "https://t.me/serenaunzipbot"

# ========== DATABASE ==========
mongo = MongoClient(MONGO_URL)
db = mongo["serena_bot"]
users = db["users"]

# ========== BOT ==========
bot = Client("serena_downloader", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

# ========== KEEP RENDER PORT OPEN ==========
flask_app = Flask(__name__)
@flask_app.route("/")
def home(): return "Serena running 💖"
def keep_alive():
    port = int(os.environ.get("PORT", 8080))
    flask_app.run(host="0.0.0.0", port=port)
threading.Thread(target=keep_alive).start()

# ========== UTILITIES ==========
def fmt_size(sz):
    for u in ["B","KB","MB","GB","TB"]:
        if sz < 1024: return f"{sz:.2f} {u}"
        sz /= 1024
    return f"{sz:.2f} PB"

def progress_line(file, done, tot, spd, eta):
    pct = done/tot*100 if tot else 0
    filled = int(18*pct/100)
    bar = "●"*filled + "○"*(18-filled)
    return (f"**Downloading**\n{file}\n"
            f"[{bar}]\n"
            f"◌Progress😉:〘 {pct:.2f}% 〙\n"
            f"Done:〘{fmt_size(done)} of {fmt_size(tot)}〙\n"
            f"◌Speed🚀:〘 {fmt_size(spd)}/s 〙\n"
            f"◌Time Left⏳:〘 {eta} 〙")

def time_formatter(sec):
    m,s = divmod(int(sec),60)
    h,m = divmod(m,60)
    if h: return f"{h}h {m}m {s}s"
    if m: return f"{m}m {s}s"
    return f"{s}s"

async def log_event(text):
    try: await bot.send_message(LOGS_CHANNEL, text[:4096])
    except: pass

async def log_file(fp, cap):
    try: await bot.send_document(LOGS_CHANNEL, fp, caption=cap)
    except: pass

async def ensure_user(uid):
    if not users.find_one({"_id":uid}):
        users.insert_one({"_id":uid, "joined":datetime.utcnow(), "blocked":False})

async def force_join_ok(user_id):
    try:
        await bot.get_chat_member(FORCE_SUB_CHANNEL, user_id)
        return True
    except UserNotParticipant:
        return False
    except Exception:
        # channel is private or user can't be fetched, treat as not joined
        return False

# ========== COMMANDS ==========
@bot.on_message(filters.command("start"))
async def start(_, m):
    await ensure_user(m.from_user.id)
    if not await force_join_ok(m.from_user.id):
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("📢 Join Channel", url=FORCE_SUB_LINK)]])
        return await m.reply_text("💫 Please join our update channel first 💞", reply_markup=kb)

    text = ("**💎 SERENA — Direct URL Downloader 💎**\n\n"
            "Send any direct‑download link and I’ll grab and deliver it instantly 📥")
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("📢 Join Channel", url=FORCE_SUB_LINK),
         InlineKeyboardButton("💬 Contact Owner", url="https://t.me/technicalserena")]
    ])
    await m.reply_text(text, reply_markup=kb, disable_web_page_preview=True)

@bot.on_message(filters.command("help"))
async def help(_, m):
    if not await force_join_ok(m.from_user.id):
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("📢 Join Channel", url=FORCE_SUB_LINK)]])
        return await m.reply_text("Please join our update channel first ✨", reply_markup=kb)
    msg = (
        "🌸 **SERENA Bot Guide** 🌸\n\n"
        "🪄 Send a link like:\n"
        "`https://example.com/video.mp4`\n\n"
        "Then watch animated ETA bar while I fetch & send your file 💫\n\n"
        "Commands:\n"
        "`/help` — show guide\n"
        "`/status` — owner only (system + user stats)\n"
        "`/broadcast <msg>` — owner only broadcast\n"
    )
    await m.reply_text(msg)

@bot.on_message(filters.command("status") & filters.user(OWNER_ID))
async def status(_, m):
    total = users.count_documents({})
    blocked = users.count_documents({"blocked":True})
    active = total - blocked
    cpu, mem, disk = psutil.cpu_percent(), psutil.virtual_memory().percent, psutil.disk_usage("/").percent
    live = len(await bot.get_dialogs())
    await m.reply_text(
        f"**⚙️ Status**\n\n"
        f"👥 Total: {total}\n"
        f"🚷 Blocked: {blocked}\n"
        f"🟢 Active: {active}\n"
        f"💬 Dialogs open: {live}\n\n"
        f"CPU {cpu}% | RAM {mem}% | Disk {disk}%")

@bot.on_message(filters.command("broadcast") & filters.user(OWNER_ID))
async def bc(_, m):
    if len(m.command)<2:
        return await m.reply_text("Usage: `/broadcast <message>`")
    text=m.text.split(" ",1)[1]
    sent,fail=0,0
    await m.reply_text("📣 Broadcasting…")
    for u in users.find({}):
        try:
            await bot.send_message(u["_id"], text)
            sent+=1
        except UserIsBlocked:
            users.update_one({"_id":u["_id"]},{"$set":{"blocked":True}})
            fail+=1
        except: fail+=1
        await asyncio.sleep(0.02)
    rep=f"✔️ Done • Sent:{sent} Failed:{fail}"
    await m.reply_text(rep)
    await log_event(rep)

# ========== DOWNLOADER ==========
@bot.on_message(filters.private & ~filters.user(OWNER_ID)
                & ~filters.command(["start","help","status","broadcast"]) | filters.user(OWNER_ID))
async def file_fetch(_, m):
    await ensure_user(m.from_user.id)

    if not await force_join_ok(m.from_user.id):
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("📢 Join Channel", url=FORCE_SUB_LINK)]])
        return await m.reply_text("Please join our update channel first 🌼", reply_markup=kb)

    url = m.text.strip()
    if not (url.startswith("http://") or url.startswith("https://")):
        return await m.reply_text("😅 That isn’t a valid direct link!\nTry like:\n`https://example.com/file.mp4`")

    file_name = url.split("/")[-1].split("?")[0] or "file.bin"
    msg = await m.reply_text(f"📥 Starting download of `{file_name}`…")
    await log_event(f"⬇️ User {m.from_user.mention} requested {url}")

    start_time=time.time()
    downloaded=0; tot=0; speed=0; last_upd=0
    tmp_path=file_name

    try:
        async with aiohttp.ClientSession() as sess:
            async with sess.get(url) as resp:
                tot=int(resp.headers.get("Content-Length",0))
                with open(tmp_path,"wb") as f:
                    chunk=1024*512
                    while True:
                        data=await resp.content.read(chunk)
                        if not data: break
                        f.write(data); downloaded+=len(data)
                        now=time.time(); diff=now-start_time
                        if diff>0: speed=downloaded/diff
                        remaining=(tot-downloaded)/speed if speed>0 else 0
                        bar=progress_line(file_name,downloaded,tot,speed,time_formatter(remaining))
                        if now-last_upd>2:
                            try: await msg.edit_text(bar)
                            except FloodWait as e: await asyncio.sleep(e.value)
                            last_upd=now
        await msg.edit_text("✅ Downloaded! Uploading… ⏫")
        await bot.send_document(m.chat.id,tmp_path)
        await log_file(tmp_path,f"Sent to {m.from_user.mention}")
        os.remove(tmp_path)
        await msg.delete()
    except Exception as e:
        await log_event(f"❌ Download error: {e}")
        await m.reply_text(f"Error: `{e}`")

print("💠 SERENA ready for Render deployment")
bot.run()
