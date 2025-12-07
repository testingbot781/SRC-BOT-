import os, aiohttp, asyncio, time, mimetypes, threading, psutil
from datetime import datetime
from pyrogram import Client, filters
from pyrogram.errors import FloodWait, UserNotParticipant, UserIsBlocked
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton, Message
from flask import Flask
from pymongo import MongoClient

# ========= CONFIG =========
API_ID = int(os.getenv("API_ID"))
API_HASH = os.getenv("API_HASH")
BOT_TOKEN = os.getenv("BOT_TOKEN")
MONGO_URL = os.getenv("MONGO_URL")

OWNER_ID = 1598576202
LOGS_CHANNEL = -1003286415377
FORCE_CH = "serenaunzipbot"
FORCE_LINK = "https://t.me/serenaunzipbot"

# ========= DATABASE =========
mongo = MongoClient(MONGO_URL)
db = mongo["serena"]
users = db["users"]
files = db["files"]

# ========= BOT =========
bot = Client("SERENA", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

# ========= KEEP PORT OPEN (Render) =========
web = Flask(__name__)
@web.route("/", methods=["GET","POST"])
def home(): return "💠 SERENA alive!"
def keepalive(): web.run(host="0.0.0.0", port=int(os.environ.get("PORT",8080)))

# ========= HELPERS =========
def size_fmt(b):
    for u in ["B","KB","MB","GB","TB"]:
        if b < 1024: return f"{b:.2f} {u}"
        b /= 1024
    return f"{b:.2f} PB"

def time_fmt(sec):
    if sec <= 0: return "<1 s"
    m,s=divmod(int(sec),60);h,m=divmod(m,60)
    if h: return f"{h} h {m} m {s} s"
    if m: return f"{m} m {s} s"
    return f"{s} s"

def show_bar(name,phase,done,total,speed):
    pct = done/total*100 if total else 0
    fill = int(18*pct/100)
    bar = "●"*fill + "○"*(18-fill)
    eta = time_fmt((total-done)/speed if speed>0 else 0)
    return (f"**{phase}**\n`{name}`\n[{bar}]\n"
            f"◌Progress😉 {pct:.2f}%\n"
            f"✅ {size_fmt(done)} of {size_fmt(total)}\n"
            f"🚀 {size_fmt(speed)}/s ⏳ ETA {eta}")

async def log_text(txt):
    try: await bot.send_message(LOGS_CHANNEL, txt)
    except: pass

async def log_file(path, caption):
    try: return await bot.send_document(LOGS_CHANNEL, path, caption=caption)
    except: return None

async def ensure_user(uid):
    if not users.find_one({"_id": uid}):
        users.insert_one({"_id": uid, "queue": []})

async def push_q(uid, url):
    u = users.find_one({"_id": uid}) or {"queue": []}
    q = u.get("queue", [])
    q.append(url)
    users.update_one({"_id": uid}, {"$set": {"queue": q}})

async def pop_q(uid):
    u = users.find_one({"_id": uid})
    if not u or not u.get("queue"): return None
    url = u["queue"].pop(0)
    users.update_one({"_id": uid}, {"$set": {"queue": u["queue"]}})
    return url

active = set()
cancel_flags = {}  # uid: bool

async def joined(uid):
    try:
        await bot.get_chat_member(FORCE_CH, uid)
        return True
    except UserNotParticipant: return False
    except: return False

# ========= COMMANDS =========
@bot.on_message(filters.command("start"))
async def start(_, m):
    await ensure_user(m.from_user.id)
    if not await joined(m.from_user.id):
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("📢 Join Channel", url=FORCE_LINK)]])
        return await m.reply_text("⚠️ Please join our update channel first 🌼", reply_markup=kb)
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("📢 Join Channel", url=FORCE_LINK)],
        [InlineKeyboardButton("💬 Contact Owner", url="https://t.me/technicalserena")]
    ])
    await m.reply_text("💎 **SERENA Downloader** 💎\n\n"
                       "Send me any direct URL and watch the smooth progress bars 💞",
                       reply_markup=kb)

@bot.on_message(filters.command("help"))
async def help(_, m):
    await m.reply_text("🌸 **How to Use SERENA**\n\n"
                       "1️⃣ Send a direct download link (mp4/zip/etc)\n"
                       "2️⃣ Watch download & upload ETA bars (5 s updates → no flood)\n"
                       "3️⃣ Files saved to Logs channel 📦\n\n"
                       "`/cancel` — stop current job\n"
                       "`/file <word>` — search archive\n"
                       "`/status` — show bot health")

@bot.on_message(filters.command("cancel"))
async def cancel(_, m):
    cancel_flags[m.from_user.id] = True
    await m.reply_text("🛑 Okay dear, cancelling your current download …")

@bot.on_message(filters.command("status") & filters.user(OWNER_ID))
async def status(_, m):
    tot = users.count_documents({})
    cpu, ram, dsk = psutil.cpu_percent(), psutil.virtual_memory().percent, psutil.disk_usage('/').percent
    await m.reply_text(f"⚙️ System Status\n👥 Users {tot}\n💻 CPU {cpu}% RAM {ram}% Disk {dsk}%")
    await log_text("📊 Status checked")

@bot.on_message(filters.command("file"))
async def file_cmd(_, m):
    if len(m.command) < 2:
        return await m.reply_text("Usage: /file <keyword>")
    key = m.text.split(" ", 1)[1]
    data = list(files.find({"name":{"$regex":key,"$options":"i"}}))
    if not data:
        return await m.reply_text("❌ No match found.")
    await m.reply_text(f"📂 Found {len(data)} file(s), sending …")
    for f in data:
        try:
            await bot.send_document(m.chat.id, f["file_id"], caption=f["name"],
                reply_markup=InlineKeyboardMarkup(
                    [[InlineKeyboardButton("💬 Contact Owner", url="https://t.me/technicalserena")]]))
        except Exception as e: await log_text(str(e))
        await asyncio.sleep(1)

# ========= MAIN HANDLER =========
@bot.on_message(filters.private & ~filters.command(["start","help","status","file","cancel"]))
async def queue_handler(_, m):
    url = m.text.strip()
    if not url.startswith("http"):
        return await m.reply_text("😅 That doesn’t look like a valid link!")
    await ensure_user(m.from_user.id)
    await push_q(m.from_user.id, url)
    if m.from_user.id in active:
        return await m.reply_text("🕐 Added to queue baby, please wait 💞")
    active.add(m.from_user.id)
    cancel_flags[m.from_user.id] = False
    while True:
        nxt = await pop_q(m.from_user.id)
        if not nxt: break
        await process_link(m, nxt)
    active.discard(m.from_user.id)

# ========= CORE PROCESS =========
async def process_link(m: Message, url: str):
    uid = m.from_user.id
    filename = "file.bin"
    msg = await m.reply_text("📥 Downloading has started …")
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(url, allow_redirects=True) as r:
                total = int(r.headers.get("Content-Length", 0))
                cd = r.headers.get("Content-Disposition")
                if cd and "filename=" in cd:
                    filename = cd.split("filename=")[-1].strip('"; ')
                else:
                    ct = r.headers.get("Content-Type","")
                    ext = mimetypes.guess_extension(ct.split(";")[0].strip()) or ".bin"
                    base = os.path.basename(url.split("?")[0]) or "file"
                    filename = base if "." in base else base + ext
                done, start, last = 0, time.time(), 0
                with open(filename, "wb") as f:
                    async for chunk in r.content.iter_chunked(1024*512):
                        if cancel_flags.get(uid): 
                            await msg.edit_text("🛑 Cancelled during download …")
                            return
                        f.write(chunk); done += len(chunk)
                        now = time.time()
                        if now - last > 5:
                            spd = done / max(now - start, 1)
                            try: await msg.edit_text(show_bar(filename,"Downloading",done,total,spd))
                            except: pass
                            last = now

        # ---- Upload to Logs ----
        await msg.edit_text("📦 Uploading to Logs Channel …")
        start, last = time.time(), 0
        async def log_prog(cur,total):
            if cancel_flags.get(uid):
                raise asyncio.CancelledError
            now=time.time()
            if now-last>5:
                spd=cur/max(now-start,1)
                try: asyncio.create_task(msg.edit_text(show_bar(filename,"Uploading Backup",cur,total,spd)))
                except: pass

        logmsg = await bot.send_document(LOGS_CHANNEL, filename, caption=f"📦 Backup:{filename}", progress=log_prog)

        # ---- Upload to User ----
        await msg.edit_text("📤 Uploading to User …")
        start, last = time.time(), 0
        async def user_prog(cur,total):
            if cancel_flags.get(uid):
                raise asyncio.CancelledError
            now=time.time()
            if now-last>5:
                spd=cur/max(now-start,1)
                try: asyncio.create_task(msg.edit_text(show_bar(filename,"Uploading to User",cur,total,spd)))
                except: pass

        sent = await bot.send_document(uid, filename, caption=f"`{filename}`",
            progress=user_prog,
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("💬 Contact Owner", url="https://t.me/technicalserena")]]))

        files.insert_one({"name":filename,"file_id":logmsg.document.file_id,"type":"document"})
        await msg.delete()
        await log_text(f"✅ Delivered {filename} to {uid}")
    except asyncio.CancelledError:
        await msg.edit_text("🛑 Upload cancelled ❤")
        await log_text(f"User {uid} cancelled task {filename}")
    except Exception as e:
        await msg.edit_text(f"❌ Error {e}")
        await log_text(f"❌ Error {e}")
    finally:
        if os.path.exists(filename): os.remove(filename)
        cancel_flags[uid] = False

# ========= RUN =========
if __name__ == "__main__":
    print("💠 SERENA starting…")
    threading.Thread(target=keepalive).start()
    bot.run()
