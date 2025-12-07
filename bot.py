import os, aiohttp, asyncio, time, mimetypes, threading, psutil
from datetime import datetime
from pyrogram import Client, filters
from pyrogram.errors import FloodWait, UserNotParticipant, UserIsBlocked
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from flask import Flask
from pymongo import MongoClient

# ============ CONFIG ============
API_ID = int(os.getenv("API_ID"))
API_HASH = os.getenv("API_HASH")
BOT_TOKEN = os.getenv("BOT_TOKEN")
MONGO_URL = os.getenv("MONGO_URL")

OWNER_ID = 1598576202
LOGS_CHANNEL = -1003286415377
FORCE_CHANNEL = "serenaunzipbot"
FORCE_LINK = "https://t.me/serenaunzipbot"

# ============ DATABASE ============
mongo = MongoClient(MONGO_URL)
db = mongo["serena"]
users = db["users"]
files = db["files"]

# ============ BOT INIT ============
bot = Client("SERENA_downloader", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

# ============ KEEP RENDER PORT OPEN ============
flask_app = Flask(__name__)
@flask_app.route("/", methods=["GET","POST"])
def home(): return "💠 SERENA alive!"
def keepalive(): flask_app.run(host="0.0.0.0", port=int(os.environ.get("PORT",8080)))
threading.Thread(target=keepalive).start()

# ============ HELPERS ============
def fmt_size(n):
    for u in ["B","KB","MB","GB","TB"]:
        if n < 1024: return f"{n:.2f} {u}"
        n /= 1024
    return f"{n:.2f} PB"

def fmt_time(s):
    if s <= 0: return "<1 s"
    m, s = divmod(int(s), 60); h, m = divmod(m, 60)
    if h: return f"{h} h {m} m {s} s"
    if m: return f"{m} m {s} s"
    return f"{s} s"

def draw_bar(name, phase, done, total, speed):
    pct = done/total*100 if total else 0
    fill = int(18*pct/100)
    bar = "●"*fill + "○"*(18-fill)
    eta = fmt_time((total-done)/speed) if speed > 0 else "--"
    return (f"**{phase}**\n`{name}`\n[{bar}]\n"
            f"◌Progress😉 {pct:.2f}%\n"
            f"✅ {fmt_size(done)} of {fmt_size(total)}\n"
            f"🚀 {fmt_size(speed)}/s  ⏳ ETA {eta}")

async def joined(uid):
    try:
        await bot.get_chat_member(FORCE_CHANNEL, uid)
        return True
    except UserNotParticipant: return False
    except: return False

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
    q = u.get("queue", []); q.append(url)
    users.update_one({"_id": uid}, {"$set": {"queue": q}})

async def pop_q(uid):
    u = users.find_one({"_id": uid})
    if not u or not u.get("queue"): return None
    url = u["queue"].pop(0)
    users.update_one({"_id": uid}, {"$set": {"queue": u["queue"]}})
    return url

# ============ COMMANDS ============
@bot.on_message(filters.command("start"))
async def start(_, m):
    await ensure_user(m.from_user.id)
    if not await joined(m.from_user.id):
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("📢 Join Channel", url=FORCE_LINK)]])
        return await m.reply_text("⚠️ Please join our Updates channel first 🌼", reply_markup=kb)
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("📢 Join Channel", url=FORCE_LINK)],
        [InlineKeyboardButton("💬 Contact Owner", url="https://t.me/technicalserena")]
    ])
    await m.reply_text("💎 Welcome to **SERENA Downloader** 💎\n\n"
                       "📥 Send a direct file/video link\n"
                       "and I’ll download + upload with live status!", reply_markup=kb)

@bot.on_message(filters.command("help"))
async def help(_, m):
    await m.reply_text("🌸 **How to Use SERENA**\n"
                       "1️⃣ Send direct download link\n"
                       "2️⃣ Watch ETA during download and upload\n"
                       "3️⃣ Done → file arrives to you 🎁\n\n"
                       "Commands:\n`/status` owner only • `/file keyword` to search archive • `/broadcast msg` owner")

@bot.on_message(filters.command("status") & filters.user(OWNER_ID))
async def status(_, m):
    total = users.count_documents({})
    cpu, ram, disk = psutil.cpu_percent(), psutil.virtual_memory().percent, psutil.disk_usage("/").percent
    await m.reply_text(f"⚙️ Bot Status\n👥 Users {total}\n💻 CPU {cpu}% | RAM {ram}% | Disk {disk}%")

@bot.on_message(filters.command("broadcast") & filters.user(OWNER_ID))
async def broadcast(_, m):
    if len(m.command) < 2:
        return await m.reply_text("Usage: `/broadcast <message>`")
    text = m.text.split(" ", 1)[1]; sent = fail = 0
    for u in users.find({}):
        try: await bot.send_message(u["_id"], text); sent += 1
        except UserIsBlocked: fail += 1
        except: fail += 1
        await asyncio.sleep(0.05)
    rep = f"✅ Broadcast done Sent:{sent} Failed:{fail}"
    await m.reply_text(rep); await log_text(rep)

@bot.on_message(filters.command("file"))
async def file_cmd(_, m):
    if len(m.command) < 2:
        return await m.reply_text("Usage: /file <keyword>")
    key = m.text.split(" ", 1)[1]
    data = list(files.find({"name": {"$regex": key, "$options": "i"}}))
    if not data: return await m.reply_text("❌ No matching file found.")
    await m.reply_text(f"📂Found {len(data)} matches; sending …")
    for f in data:
        try:
            await bot.send_document(m.chat.id, f["file_id"], caption=f["name"],
                                    reply_markup=InlineKeyboardMarkup(
                                        [[InlineKeyboardButton("💬 Contact Owner", url="https://t.me/technicalserena")]]))
        except Exception as e:
            await log_text(f"FileSendError {e}")
        await asyncio.sleep(1)

# ============ PER‑USER QUEUE ============
active = set()

@bot.on_message(filters.private & ~filters.command(["start","help","status","broadcast","file"]))
async def add_queue(_, m):
    url = m.text.strip()
    if not url.startswith("http"):
        return await m.reply_text("😅 That isn’t a link!")
    await ensure_user(m.from_user.id)
    await push_q(m.from_user.id, url)
    if m.from_user.id in active:
        return await m.reply_text("🕐 Added to queue.")
    active.add(m.from_user.id)
    while True:
        nxt = await pop_q(m.from_user.id)
        if not nxt: break
        await handle_download(m, nxt)
    active.discard(m.from_user.id)

# ============ CORE DOWN/UPLOAD ============
async def handle_download(m, url):
    uid = m.from_user.id
    progress = await m.reply_text("⏳ Preparing download…")
    name = "file.bin"
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(url, allow_redirects=True) as r:
                total = int(r.headers.get("Content-Length", 0))
                cd = r.headers.get("Content-Disposition")
                if cd and "filename=" in cd:
                    name = cd.split("filename=")[-1].strip('"; ')
                else:
                    ct = r.headers.get("Content-Type", "")
                    ext = mimetypes.guess_extension(ct.split(";")[0].strip()) or ".bin"
                    base = os.path.basename(url.split("?")[0]) or "file"
                    name = base if "." in base else base + ext
                done = 0; start = time.time(); last = 0
                with open(name, "wb") as f:
                    async for chunk in r.content.iter_chunked(1024*512):
                        f.write(chunk); done += len(chunk)
                        now = time.time()
                        if now - last > 2:
                            spd = done/max(now-start,1)
                            txt = draw_bar(name,"Downloading",done,total,spd)
                            try: await progress.edit_text(txt)
                            except FloodWait as e: await asyncio.sleep(e.value)
                            last = now

        # → upload to logs first
        await progress.edit_text("📦 Uploading to Logs Channel …")
        size = os.path.getsize(name)
        start = time.time(); last = 0
        async def up_prog(c,t):
            now = time.time(); spd = c/max(now-start,1)
            txt = draw_bar(name,"Uploading Backup",c,t,spd)
            if now-last>2:
                try: asyncio.create_task(progress.edit_text(txt))
                except: pass
        logmsg = await bot.send_document(LOGS_CHANNEL, name, caption=f"📦 Backup:{name}", progress=up_prog)
        await log_text(f"⭐ Logged {url}")

        # → upload to user
        await progress.edit_text("📤 Uploading to User …")
        start = time.time(); last = 0
        async def user_prog(c,t):
            now = time.time(); spd = c/max(now-start,1)
            txt = draw_bar(name,"Uploading to User",c,t,spd)
            if now-last>2:
                try: asyncio.create_task(progress.edit_text(txt))
                except: pass
        sent = await bot.send_document(uid, name, caption=f"`{name}`",
                                       progress=user_prog,
                                       reply_markup=InlineKeyboardMarkup(
                                           [[InlineKeyboardButton("💬 Contact Owner",
                                                                  url="https://t.me/technicalserena")]]))
        # record metadata
        files.insert_one({"name": name, "file_id": logmsg.document.file_id, "type": "document"})
        await progress.delete()
        await log_text(f"📤 Delivered {name} to {uid}")
    except Exception as e:
        await progress.edit_text(f"❌ Error: {e}")
        await log_text(f"Error {e}")
    finally:
        if os.path.exists(name):
            os.remove(name)
