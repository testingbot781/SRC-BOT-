import os, aiohttp, asyncio, time, mimetypes, threading, psutil
from datetime import datetime
from pyrogram import Client, filters
from pyrogram.errors import FloodWait, UserNotParticipant, UserIsBlocked
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from flask import Flask
from pymongo import MongoClient

# ========= CONFIG =========
API_ID       = int(os.getenv("API_ID"))
API_HASH     = os.getenv("API_HASH")
BOT_TOKEN    = os.getenv("BOT_TOKEN")
MONGO_URL    = os.getenv("MONGO_URL")

OWNER_ID     = 1598576202
LOGS_CHANNEL = -1003286415377
FORCE_CH     = "serenaunzipbot"
FORCE_LINK   = "https://t.me/serenaunzipbot"

# ========= DATABASE =========
mongo = MongoClient(MONGO_URL)
db    = mongo["serena"]
users = db["users"]
files = db["files"]

# ========= PYROGRAM CLIENT =========
bot = Client("SERENA", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

# ========= FLASK PORT FOR RENDER =========
web = Flask(__name__)
@web.route("/", methods=["GET","POST"])
def home(): return "💠 SERENA alive!"
def keepalive(): web.run(host="0.0.0.0", port=int(os.environ.get("PORT",8080)))

# ========= UTILS =========
def size_fmt(n):
    for u in ["B","KB","MB","GB","TB"]:
        if n < 1024: return f"{n:.2f} {u}"
        n /= 1024
    return f"{n:.2f} PB"

def time_fmt(sec):
    if sec<=0: return "<1 s"
    m,s=divmod(int(sec),60);h,m=divmod(m,60)
    if h: return f"{h} h {m} m {s} s"
    if m: return f"{m} m {s} s"
    return f"{s} s"

def eta_block(name,phase,done,total,speed):
    pct = done/total*100 if total else 0
    filled = int(18*pct/100)
    bar = "●"*filled+"○"*(18-filled)
    eta = time_fmt((total-done)/speed) if speed>0 else "--"
    return (f"**{phase}**\n`{name}`\n[{bar}]\n"
            f"◌Progress😉 {pct:.2f}%\n"
            f"✅ {size_fmt(done)} of {size_fmt(total)}\n"
            f"🚀 {size_fmt(speed)}/s  | ⏳ ETA {eta}")

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
    url = u["queue"].pop(0)
    users.update_one({"_id":uid},{"$set":{"queue":u["queue"]}})
    return url

async def joined(uid):
    try:
        await bot.get_chat_member(FORCE_CH,uid)
        return True
    except UserNotParticipant: return False
    except: return False

async def log_text(t):
    try: await bot.send_message(LOGS_CHANNEL,t)
    except: pass

# ========= COMMANDS =========
@bot.on_message(filters.command("start"))
async def start(_,m):
    await ensure_user(m.from_user.id)
    if not await joined(m.from_user.id):
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("📢 Join Channel",url=FORCE_LINK)]])
        return await m.reply_text("⚠️ Join our update channel first 🌼",reply_markup=kb)
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("📢 Join Channel",url=FORCE_LINK)],
        [InlineKeyboardButton("💬 Contact Owner",url="https://t.me/technicalserena")]
    ])
    await m.reply_text(
        "💎 **SERENA Downloader** 💎\n\n"
        "Send a direct download link (mp4/zip/etc)\n"
        "and watch the animated progress while I fetch your file 💫",
        reply_markup=kb)

@bot.on_message(filters.command("help"))
async def help(_,m):
    await m.reply_text(
        "🌈 **SERENA Usage**\n"
        "1️⃣ Send any direct download URL\n"
        "2️⃣ Watch live download & upload ETA\n"
        "3️⃣ Receive file + backup sent to Logs Channel\n\n"
        "`/status` — owner stats\n`/broadcast` <text>\n`/file` <keyword> — search archive")

@bot.on_message(filters.command("status") & filters.user(OWNER_ID))
async def status(_,m):
    total = users.count_documents({})
    cpu,ram,dsk = psutil.cpu_percent(), psutil.virtual_memory().percent, psutil.disk_usage("/").percent
    await m.reply_text(f"⚙️ **System Stats**\n👥 Users:{total}\n💻 CPU {cpu}% RAM {ram}% Disk {dsk}%")
    await log_text("📊 Owner checked status")

@bot.on_message(filters.command("broadcast") & filters.user(OWNER_ID))
async def bc(_,m):
    if len(m.command)<2: return await m.reply_text("Usage: `/broadcast <text>`")
    text=m.text.split(" ",1)[1]; sent=fail=0
    for u in users.find({}):
        try: await bot.send_message(u["_id"],text); sent+=1
        except UserIsBlocked: fail+=1
        except: fail+=1
        await asyncio.sleep(0.05)
    rep=f"✅ Broadcast done Sent:{sent} Failed:{fail}"
    await m.reply_text(rep); await log_text(rep)

@bot.on_message(filters.command("file"))
async def file_cmd(_,m):
    if len(m.command)<2: return await m.reply_text("Usage: /file <keyword>")
    key=m.text.split(" ",1)[1]
    data=list(files.find({"name":{"$regex":key,"$options":"i"}}))
    if not data: return await m.reply_text("❌ No match found.")
    await m.reply_text(f"📂 Found {len(data)} matches; sending…")
    for f in data:
        try:
            await bot.send_document(m.chat.id,f["file_id"],caption=f["name"],
                                    reply_markup=InlineKeyboardMarkup(
                                        [[InlineKeyboardButton("💬 Contact Owner",
                                          url="https://t.me/technicalserena")]]))
        except Exception as e: await log_text(str(e))
        await asyncio.sleep(1)

# ========= QUEUE MANAGEMENT =========
active=set()

@bot.on_message(filters.private & ~filters.command(["start","help","status","broadcast","file"]))
async def queue_handle(_,m):
    url=m.text.strip()
    if not url.startswith("http"): 
        return await m.reply_text("😅 That doesn’t look like a valid link!")
    await ensure_user(m.from_user.id)
    await push_q(m.from_user.id,url)
    if m.from_user.id in active:
        return await m.reply_text("🕐 Added to queue, will start soon.")
    active.add(m.from_user.id)
    while True:
        nxt=await pop_q(m.from_user.id)
        if not nxt: break
        await download_and_upload(m,nxt)
    active.discard(m.from_user.id)

# ========= CORE TASK =========
async def download_and_upload(m,url):
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
                done=0; start=time.time(); last=0
                with open(name,"wb") as f:
                    async for chunk in r.content.iter_chunked(1024*512):
                        f.write(chunk); done+=len(chunk)
                        if time.time()-last>2:
                            spd=done/max(time.time()-start,1)
                            try: await msg.edit_text(eta_block(name,"Downloading",done,total,spd))
                            except FloodWait as e: await asyncio.sleep(e.value)
                            last=time.time()

        # ===== Upload to Logs =====
        await msg.edit_text("📦 Uploading to Logs Channel …")
        start=time.time()
        async def log_prog(c,t):
            spd=c/max(time.time()-start,1)
            try: asyncio.create_task(msg.edit_text(eta_block(name,"Uploading Backup",c,t,spd)))
            except: pass
        logmsg=await bot.send_document(LOGS_CHANNEL,name,caption=f"📦 Backup:{name}",progress=log_prog)
        await log_text(f"📁 Backup logged {url}")

        # ===== Upload to User =====
        await msg.edit_text("📤 Uploading to User …")
        start=time.time()
        async def user_prog(c,t):
            spd=c/max(time.time()-start,1)
            try: asyncio.create_task(msg.edit_text(eta_block(name,"Uploading to User",c,t,spd)))
            except: pass
        sent=await bot.send_document(uid,name,caption=f"`{name}`",
            progress=user_prog,
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton("💬 Contact Owner",url="https://t.me/technicalserena")]]))
        files.insert_one({"name":name,"file_id":logmsg.document.file_id,"type":"document"})
        await msg.delete()
        await log_text(f"✅ Delivered {name} to {uid}")
    except Exception as e:
        await msg.edit_text(f"❌ Error: {e}")
        await log_text(f"❌ {e}")
    finally:
        if os.path.exists(name): os.remove(name)

# ========= ENTRY POINT =========
if __name__ == "__main__":
    print("💠 SERENA starting…")
    threading.Thread(target=keepalive).start()
    bot.run()
