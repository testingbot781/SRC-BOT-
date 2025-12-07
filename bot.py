import os, aiohttp, asyncio, time, mimetypes, threading
from datetime import datetime
from pyrogram import Client, filters
from pyrogram.errors import FloodWait, UserNotParticipant, UserIsBlocked
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from flask import Flask
from pymongo import MongoClient
import psutil

# ========== BASIC CONFIG ==========
API_ID=int(os.getenv("API_ID"))
API_HASH=os.getenv("API_HASH")
BOT_TOKEN=os.getenv("BOT_TOKEN")
MONGO_URL=os.getenv("MONGO_URL")
OWNER_ID=1598576202
LOGS_CHANNEL=-1003286415377
FORCE_SUB="serenaunzipbot"
FORCE_LINK="https://t.me/serenaunzipbot"

# ========== DB ==========
mongo=MongoClient(MONGO_URL)
db=mongo["serena"]
users=db["users"]
files=db["files"]

# ========== BOT ==========
bot=Client("serena_master",api_id=API_ID,api_hash=API_HASH,bot_token=BOT_TOKEN)

# ========== RENDER PORT ==========
flask_app=Flask(__name__)
@flask_app.route("/",methods=["GET","POST"])
def home(): return "💠 SERENA running!"
def keepalive(): flask_app.run(host="0.0.0.0",port=int(os.environ.get("PORT",8080)))
threading.Thread(target=keepalive).start()

# ========== UTILS ==========
def fmt_size(s):
    for u in ["B","KB","MB","GB","TB"]:
        if s<1024: return f"{s:.2f} {u}"
        s/=1024
    return f"{s:.2f} PB"

def fmt_time(sec):
    if sec<=0: return "<1 s"
    m,s=divmod(int(sec),60);h,m=divmod(m,60)
    if h: return f"{h} h {m} m {s} s"
    if m: return f"{m} m {s} s"
    return f"{s} s"

def show_bar(name,phase,done,total,speed,start):
    pct=done/total*100 if total else 0
    filled=int(18*pct/100)
    bar="●"*filled+"○"*(18-filled)
    left=fmt_time((total-done)/speed) if speed>0 else "--"
    return (f"**{phase}**\n`{name}`\n[{bar}]\n"
            f"◌Progress😉: {pct:.2f}%\n"
            f"✅Done: {fmt_size(done)} of {fmt_size(total)}\n"
            f"🚀Speed: {fmt_size(speed)}/s\n"
            f"⏳ETA: {left}")

async def ensure_user(uid):
    if not users.find_one({"_id":uid}):
        users.insert_one({"_id":uid,"queue":[]})

async def push_queue(uid,url):
    user=users.find_one({"_id":uid}) or {"queue":[]}
    q=user.get("queue",[])
    q.append(url)
    users.update_one({"_id":uid},{"$set":{"queue":q}})

async def pop_queue(uid):
    user=users.find_one({"_id":uid})
    if not user or not user.get("queue"): return None
    url=user["queue"].pop(0)
    users.update_one({"_id":uid},{"$set":{"queue":user["queue"]}})
    return url

async def joined(uid):
    try:
        await bot.get_chat_member(FORCE_SUB,uid)
        return True
    except UserNotParticipant: return False
    except: return False

async def log_text(msg):
    try: await bot.send_message(LOGS_CHANNEL,msg)
    except: pass

async def log_file(path,cap):
    try: return await bot.send_document(LOGS_CHANNEL,path,caption=cap)
    except Exception as e:
        await log_text(f"⚠️log_upload_failed {e}")
        return None

# ========== COMMANDS ==========
@bot.on_message(filters.command("start"))
async def start(_,m):
    await ensure_user(m.from_user.id)
    if not await joined(m.from_user.id):
        kb=InlineKeyboardMarkup([[InlineKeyboardButton("📢 Join Channel",url=FORCE_LINK)]])
        return await m.reply_text("⚠️ Join update channel first 🌼",reply_markup=kb)
    kb=InlineKeyboardMarkup([
        [InlineKeyboardButton("📢 Join Channel",url=FORCE_LINK)],
        [InlineKeyboardButton("💬 Contact Owner",url="https://t.me/technicalserena")]
    ])
    await m.reply_text("💎Welcome to **SERENA Downloader**💎\n\n"
                       "📥Send any direct stream/download link\n"
                       "and watch the animated ETA status!",reply_markup=kb)

@bot.on_message(filters.command("help"))
async def help(_,m):
    await m.reply_text(
        "🩵**Usage**🩵\n\n"
        "1️⃣Send a direct URL\n"
        "2️⃣Watch download & upload ETA\n"
        "3️⃣Done — file arrives to you 🎁\n\n"
        "Admin: /status  /broadcast <msg> \nnormal: /file <word>\n")

@bot.on_message(filters.command("status") & filters.user(OWNER_ID))
async def stat(_,m):
    t=users.count_documents({})
    cpu,ram,dsk=psutil.cpu_percent(),psutil.virtual_memory().percent,psutil.disk_usage('/').percent
    await m.reply_text(f"⚙️Status\nUsers:{t}\nCPU:{cpu}% RAM:{ram}% Disk:{dsk}%")
    await log_text("📊status-check")

@bot.on_message(filters.command("broadcast") & filters.user(OWNER_ID))
async def bc(_,m):
    if len(m.command)<2: return await m.reply_text("Usage: /broadcast <text>")
    text=m.text.split(" ",1)[1];sent=fail=0
    await m.reply_text("📣Broadcast starting…")
    for u in users.find({}):
        try:
            await bot.send_message(u["_id"],text);sent+=1
        except UserIsBlocked:
            fail+=1
        except: fail+=1
        await asyncio.sleep(0.02)
    rep=f"✅Done: sent {sent}, fail {fail}"
    await m.reply_text(rep);await log_text(rep)

@bot.on_message(filters.command("file"))
async def file_search(_,m):
    if len(m.command)<2:return await m.reply_text("Usage: /file <keyword>")
    key=m.text.split(" ",1)[1]
    data=list(files.find({"name":{"$regex":key,"$options":"i"}}))
    if not data:return await m.reply_text("❌Not found in archive!")
    await m.reply_text(f"📂Found {len(data)} match(es)")
    for f in data:
        try:
            if f["type"]=="video":
                await bot.send_video(m.chat.id,f["file_id"],caption=f["name"])
            else:
                await bot.send_document(m.chat.id,f["file_id"],caption=f["name"])
        except Exception as e: await log_text(str(e))
        await asyncio.sleep(1)

# ========== MULTI‑QUEUE HANDLER ==========
user_running=set()

@bot.on_message(filters.private & ~filters.command(["start","help","status","broadcast","file"]))
async def queue_download(_,m):
    url=m.text.strip()
    if not url.startswith("http"): 
        return await m.reply_text("🌸Invalid link.")
    await ensure_user(m.from_user.id)
    await push_queue(m.from_user.id,url)
    if m.from_user.id in user_running:
        return await m.reply_text("🕐 Queued! I'll start after current download.")
    user_running.add(m.from_user.id)
    while True:
        nxt=await pop_queue(m.from_user.id)
        if not nxt: break
        await process_file(m,nxt)
    user_running.discard(m.from_user.id)

# ========== CORE PROCESS ==========
async def process_file(msg,url):
    uid=msg.from_user.id
    name="file.bin";progress=await msg.reply_text("📥Starting...")
    try:
        # 1. Download with ETA
        start=time.time();done=0;tot=0;last=0
        async with aiohttp.ClientSession() as s:
            async with s.get(url,allow_redirects=True) as r:
                tot=int(r.headers.get("Content-Length",0))
                cd=r.headers.get("Content-Disposition")
                if cd and "filename=" in cd:
                    name=cd.split("filename=")[-1].strip('"; ')
                else:
                    ct=r.headers.get("Content-Type","")
                    ext=mimetypes.guess_extension(ct.split(";")[0].strip()) or ".bin"
                    base=os.path.basename(url.split("?")[0]) or "file"
                    name=base if "." in base else base+ext
                with open(name,"wb") as f:
                    async for chunk in r.content.iter_chunked(1024*512):
                        f.write(chunk);done+=len(chunk)
                        if time.time()-last>2:
                            spd=done/max(time.time()-start,1)
                            text=show_bar(name,"Downloading",done,tot,spd,start)
                            try: await progress.edit_text(text)
                            except FloodWait as e: await asyncio.sleep(e.value)
                            last=time.time()
        # 2. Upload to logs + user sequentially with upload ETA
        size=os.path.getsize(name)
        start=time.time();last=0;sent=None
        async def upl_prog(cur,total):
            now=time.time();spd=cur/max(now-start,1)
            tleft=(total-cur)/spd if spd>0 else 0
            if now-last>2:
                txt=show_bar(name,"Uploading",cur,total,spd,start)
                try: asyncio.create_task(progress.edit_text(txt))
                except: pass

        # →Logs backup
        await progress.edit_text("📦 Uploading to Logs Channel…")
        logmsg=await bot.send_document(LOGS_CHANNEL,name,caption=f"📦 Backup:{name}",progress=upl_prog)
        await log_text(f"{name} logged from {msg.from_user.id}")
        # →User delivery
        await progress.edit_text("📤 Uploading to User…")
        sent=await bot.send_document(uid,name,caption=f"`{name}`",progress=upl_prog,
                                     reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("💬 Contact Owner",url="https://t.me/technicalserena")]]))
        # record
        files.insert_one({"name":name,"file_id":logmsg.document.file_id,"type":"document"})
        await log_text(f"📤 Delivered {name} to {msg.from_user.id}")
    except Exception as e:
        await progress.edit_text(f"❌Error {e}")
        await log_text(str(e))
    finally:
        if os.path.exists(name):
            os.remove(name)
        await progress.delete()
