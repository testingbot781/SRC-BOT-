import os, aiohttp, asyncio, time, mimetypes, threading, psutil
from datetime import datetime
from pyrogram import Client, filters
from pyrogram.errors import FloodWait, UserNotParticipant, UserIsBlocked
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from flask import Flask
from pymongo import MongoClient

# ---------- CONFIG ----------
API_ID = int(os.getenv("API_ID"))
API_HASH = os.getenv("API_HASH")
BOT_TOKEN = os.getenv("BOT_TOKEN")
MONGO_URL = os.getenv("MONGO_URL")

OWNER_ID     = 1598576202
LOGS_CHANNEL = -1003286415377
FORCE_CH     = "serenaunzipbot"
FORCE_LINK   = "https://t.me/serenaunzipbot"

# ---------- DATABASE ----------
mongo = MongoClient(MONGO_URL)
db     = mongo["serena"]
users  = db["users"]
files  = db["files"]

# ---------- BOT ----------
bot = Client("SERENA", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

# ---------- KEEP RENDER PORT OPEN ----------
fl = Flask(__name__)
@fl.route("/", methods=["GET","POST"])
def home(): return "💠 SERENA alive!"
def serve(): fl.run(host="0.0.0.0", port=int(os.environ.get("PORT",8080)))

# ---------- HELPERS ----------
def fmt_size(n):
    for u in ["B","KB","MB","GB","TB"]:
        if n < 1024: return f"{n:.2f} {u}"
        n /= 1024
    return f"{n:.2f} PB"

def fmt_time(sec):
    if sec<=0: return "<1 s"
    m,s=divmod(int(sec),60);h,m=divmod(m,60)
    if h: return f"{h} h {m} m {s} s"
    if m: return f"{m} m {s} s"
    return f"{s} s"

def make_block(name,phase,done,total,speed):
    pct = done/total*100 if total else 0
    dots = int(18*pct/100)
    bar  = "●"*dots + "○"*(18-dots)
    eta  = fmt_time((total-done)/speed if speed>0 else 0)
    return (f"**{phase}**\n\n"
            f"`{name}`\n[{bar}]\n"
            f"💞 Progress {pct:.2f}%\n"
            f"✅ {fmt_size(done)} / {fmt_size(total)}\n"
            f"🚀 Speed {fmt_size(speed)}/s  ⏳ ETA {eta}")

async def log_text(txt):
    try: await bot.send_message(LOGS_CHANNEL, txt)
    except: pass

async def log_document(path, caption):
    try: return await bot.send_document(LOGS_CHANNEL, path, caption=caption)
    except Exception as e:
        await log_text(f"⚠️ Log upload failed {e}")
        return None

async def joined(uid):
    try:
        await bot.get_chat_member(FORCE_CH, uid)
        return True
    except UserNotParticipant: return False
    except: return False

async def ensure_user(uid):
    if not users.find_one({"_id":uid}):
        users.insert_one({"_id":uid,"queue":[]})

async def push_q(uid,url):
    q = users.find_one({"_id":uid}).get("queue",[])
    q.append(url)
    users.update_one({"_id":uid},{"$set":{"queue":q}})

async def pop_q(uid):
    u = users.find_one({"_id":uid})
    if not u or not u.get("queue"): return None
    url=u["queue"].pop(0)
    users.update_one({"_id":uid},{"$set":{"queue":u["queue"]}})
    return url

# ---------- STATE ----------
active=set()
cancelled={}

# ---------- COMMANDS ----------
@bot.on_message(filters.command("start"))
async def start(_,m):
    await ensure_user(m.from_user.id)
    if not await joined(m.from_user.id):
        kb=InlineKeyboardMarkup([[InlineKeyboardButton("📢 Join Channel",url=FORCE_LINK)]])
        return await m.reply_text("⚠️ Join our update channel first 🌼",reply_markup=kb)
    kb=InlineKeyboardMarkup([
        [InlineKeyboardButton("📢 Join Channel",url=FORCE_LINK)],
        [InlineKeyboardButton("💬 Contact Owner",url="https://t.me/technicalserena")]
    ])
    await m.reply_text("💎 **SERENA Downloader** 💎\n\nSend a link and watch me work my magic ✨",reply_markup=kb)

@bot.on_message(filters.command("help"))
async def help(_,m):
    await m.reply_text("🌸 **How to Use SERENA**\n"
                       "1️⃣ Send direct URL (mp4, zip etc)\n"
                       "2️⃣ ETA updates (10 s interval → no flood)\n"
                       "3️⃣ Backup → Logs Channel, Copy → You 💞\n"
                       "`/cancel` cancel current job\n"
                       "`/file <word>` search in archive")

@bot.on_message(filters.command("cancel"))
async def cancel(_,m):
    cancelled[m.from_user.id]=True
    await m.reply_text("🛑 Stopping current task dearie 💔")

@bot.on_message(filters.command("status") & filters.user(OWNER_ID))
async def status(_,m):
    t=users.count_documents({})
    cpu,ram,dsk=psutil.cpu_percent(),psutil.virtual_memory().percent,psutil.disk_usage('/').percent
    await m.reply_text(f"⚙️ Users {t}\n💻 CPU {cpu}% RAM {ram}% Disk {dsk}%")

@bot.on_message(filters.command("file"))
async def file_cmd(_,m):
    if len(m.command)<2:
        return await m.reply_text("Usage: `/file <keyword>`")
    key=m.text.split(" ",1)[1]
    data=list(files.find({"name":{"$regex":key,"$options":"i"}}))
    if not data:
        return await m.reply_text("❌ No match found — maybe uploaded before logs were recorded.")
    await m.reply_text(f"📂 Sending {len(data)} match(es)…")
    for f in data:
        try:
            await bot.send_document(m.chat.id,f["file_id"],caption=f["name"],
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("💬 Contact Owner",
                    url="https://t.me/technicalserena")]]))
        except Exception as e: await log_text(str(e))
        await asyncio.sleep(1)

# ---------- MAIN ----------
@bot.on_message(filters.private & ~filters.command(["start","help","status","file","cancel"]))
async def add_queue(_,m):
    url=m.text.strip()
    if not url.startswith("http"):
        return await m.reply_text("😅 That doesn’t look like a direct link!")
    await ensure_user(m.from_user.id)
    await push_q(m.from_user.id,url)
    if m.from_user.id in active:
        return await m.reply_text("🕐 Added to queue dear ♥")
    active.add(m.from_user.id)
    cancelled[m.from_user.id]=False
    while True:
        nxt=await pop_q(m.from_user.id)
        if not nxt: break
        await handle_download_upload(m,nxt)
        await asyncio.sleep(15)   # 💫 15 s gap after each task
    active.discard(m.from_user.id)

# ---------- CORE DOWNLOAD/UPLOAD ----------
async def handle_download_upload(m,url):
    uid=m.from_user.id
    name="file.bin"
    msg=await m.reply_text("📥 Starting download …")
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(url,allow_redirects=True) as r:
                total=int(r.headers.get("Content-Length",0))
                cd=r.headers.get("Content-Disposition")
                if cd and "filename=" in cd:
                    name=cd.split("filename=")[-1].strip('"; ')
                else:
                    ct=r.headers.get("Content-Type","")
                    ext=mimetypes.guess_extension(ct.split(";")[0].strip()) or ".bin"
                    base=os.path.basename(url.split("?")[0]) or "file"
                    name=base if "." in base else base+ext
                done, start, last = 0, time.time(), 0
                with open(name,"wb") as f:
                    async for chunk in r.content.iter_chunked(1024*512):
                        if cancelled.get(uid): 
                            await msg.edit_text("🛑 Cancelled by user during download.")
                            return
                        f.write(chunk); done+=len(chunk)
                        now=time.time()
                        if now-last>10: # 10 s interval
                            spd=done/max(now-start,1)
                            try: await msg.edit_text(make_block(name,"⬇️ Downloading",done,total,spd))
                            except FloodWait as e: await asyncio.sleep(e.value)
                            except: pass
                            last=now

        await msg.edit_text("📦 Uploading to Logs Channel …")
        start,last=time.time(),0
        cancelled[uid]=False

        async def log_prog(cur,total):
            if cancelled.get(uid): raise asyncio.CancelledError
            now=time.time()
            if now-last>10:
                spd=cur/max(now-start,1)
                try: asyncio.create_task(msg.edit_text(make_block(name,"📦 Backup Upload",cur,total,spd)))
                except: pass

        logmsg=await bot.send_document(LOGS_CHANNEL,name,caption=f"📦 Backup:{name}",progress=log_prog)
        await log_text(f"🪶 File saved → Logs ({name})")

        await msg.edit_text("📤 Uploading to User …")
        start,last=time.time(),0

        async def user_prog(cur,total):
            if cancelled.get(uid): raise asyncio.CancelledError
            now=time.time()
            if now-last>10:
                spd=cur/max(now-start,1)
                try: asyncio.create_task(msg.edit_text(make_block(name,"📤 Sending to You",cur,total,spd)))
                except: pass

        sent=await bot.send_document(uid,name,caption=f"`{name}`",
            progress=user_prog,
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("💬 Contact Owner",
                url="https://t.me/technicalserena")]]))

        # save meta
        files.insert_one({"name":name,"file_id":logmsg.document.file_id,"type":"document"})
        await msg.delete()
        await log_text(f"✅ Delivered {name} to {uid}")
    except asyncio.CancelledError:
        await msg.edit_text("🛑 Upload cancelled 💔")
    except Exception as e:
        await msg.edit_text(f"❌ Error {e}")
        await log_text(f"❌ {e}")
    finally:
        if os.path.exists(name): os.remove(name)
        cancelled[uid]=False
