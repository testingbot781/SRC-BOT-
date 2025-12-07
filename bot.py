import os, aiohttp, asyncio, time, mimetypes, threading, psutil
from datetime import datetime
from pyrogram import Client, filters
from pyrogram.errors import FloodWait, UserNotParticipant, UserIsBlocked
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from flask import Flask
from pymongo import MongoClient

# ========= BASIC CONFIG =========
API_ID       = int(os.getenv("API_ID"))
API_HASH     = os.getenv("API_HASH")
BOT_TOKEN    = os.getenv("BOT_TOKEN")
MONGO_URL    = os.getenv("MONGO_URL")
OWNER_ID     = 1598576202
LOGS_CHANNEL = -1003286415377
FORCE_SUB    = "serenaunzipbot"
FORCE_LINK   = "https://t.me/serenaunzipbot"

# ========= DB =========
mongo = MongoClient(MONGO_URL)
db    = mongo["serena"]
users = db["users"]
files = db["files"]

# ========= CLIENT =========
bot = Client("SERENA", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

# ========= KEEP PORT OPEN (Render) =========
flask_app = Flask(__name__)
@flask_app.route("/", methods=["GET","POST"])
def ok(): return "💠 SERENA active!"
def run_web(): flask_app.run(host="0.0.0.0", port=int(os.environ.get("PORT",8080)))
threading.Thread(target=run_web).start()

# ========= UTILITIES =========
def fmt_size(sz):
    for u in ["B","KB","MB","GB","TB"]:
        if sz < 1024: return f"{sz:.2f} {u}"
        sz /= 1024
    return f"{sz:.2f} PB"

def fmt_time(sec):
    if sec<1: return "<1 s"
    m,s=divmod(int(sec),60);h,m=divmod(m,60)
    if h: return f"{h}h {m}m {s}s"
    if m: return f"{m}m {s}s"
    return f"{s}s"

def bar_block(name, done, tot, spd, eta, phase="Download"):
    pct=done/tot*100 if tot else 0
    filled=int(18*pct/100)
    bar="●"*filled+"○"*(18-filled)
    return (f"⚙️ {phase} in progress...\n`{name}`\n"
            f"[{bar}]\n"
            f"◌Progress😉: 〘{pct:.2f}%〙\n"
            f"Done: 〘{fmt_size(done)} of {fmt_size(tot)}〙\n"
            f"🚀 Speed: 〘{fmt_size(spd)}/s〙\n"
            f"⏳ ETA: 〘{fmt_time(eta)}〙")

async def ensure_user(uid):
    if not users.find_one({"_id":uid}):
        users.insert_one({"_id":uid,"pref":{"video":True,"caption":""}})
async def joined(uid):
    try:
        await bot.get_chat_member(FORCE_SUB, uid)
        return True
    except UserNotParticipant: return False
    except: return False

async def log_text(t): 
    try: await bot.send_message(LOGS_CHANNEL,t)
    except: pass
async def log_doc(path,cap): 
    try: return await bot.send_document(LOGS_CHANNEL,path,caption=cap)
    except: return None

# ========= COMMANDS =========
@bot.on_message(filters.command("start"))
async def start(_,m):
    await ensure_user(m.from_user.id)
    if not await joined(m.from_user.id):
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("📢 Join Channel",url=FORCE_LINK)]])
        return await m.reply_text("⚠️ Please join our Updates channel first 🌼",reply_markup=kb)
    txt=("💎 **Welcome to SERENA Downloader** 💎\n\n"
         "✨ Send me any direct video/file URL\n"
         "and I’ll download + upload it with fancy progress bars! 🌸\n\n"
         "⚙️ Use /help or /setting to configure.")
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("📢 Join Channel",url=FORCE_LINK)],
        [InlineKeyboardButton("💬 Contact Owner",url="https://t.me/technicalserena")]
    ])
    await m.reply_text(txt, reply_markup=kb, disable_web_page_preview=True)

@bot.on_message(filters.command("help"))
async def help(_,m):
    await m.reply_text(
        "🌈 **How to use SERENA:**\n"
        "1️⃣ Send a direct download link (mp4/zip/etc)\n"
        "2️⃣ Watch Fabulous ETA during download + upload\n"
        "3️⃣ Receive file and relax 💆‍♀️\n\n"
        "Commands:\n"
        "`/setting` — personal preferences\n"
        "`/file <name>` — fetch matching file from archive"
    )

@bot.on_message(filters.command("setting"))
async def setting(_,m):
    u=users.find_one({"_id":m.from_user.id})
    pref=u["pref"] if u else {"video":True,"caption":""}
    vtxt="🎥 Upload as Video ✅" if pref.get("video") else "📄 Upload as Document ✅"
    kb=InlineKeyboardMarkup([
        [InlineKeyboardButton("🎥 Upload as Video",callback_data="set_video"),
         InlineKeyboardButton("📄 Upload as Document",callback_data="set_doc")],
        [InlineKeyboardButton("📝 Add/Change Caption",callback_data="set_cap")]
    ])
    await m.reply_text(f"⚙️ **Your Current Mode:**\n{vtxt}\n🖋️ Caption: {pref.get('caption','')}",reply_markup=kb)

@bot.on_callback_query()
async def cbh(_,q):
    uid=q.from_user.id
    await ensure_user(uid)
    pref=users.find_one({"_id":uid})["pref"]
    if q.data=="set_video": pref["video"]=True
    elif q.data=="set_doc":  pref["video"]=False
    elif q.data=="set_cap":
        await q.message.edit("✍️ Send new caption text now (or `none` to clear):")
        users.update_one({"_id":uid},{"$set":{"await_cap":True}})
        return
    users.update_one({"_id":uid},{"$set":{"pref":pref}})
    await q.answer("✅ Updated!")
    await setting(_,q.message)

@bot.on_message(filters.private & filters.text & ~filters.command(["start","help","setting","file"]))
async def maybe_caption(_,m):
    u=users.find_one({"_id":m.from_user.id})
    if u and u.get("await_cap"):
        txt=m.text if m.text.lower()!="none" else ""
        users.update_one({"_id":m.from_user.id},{"$set":{"pref.caption":txt,"await_cap":False}})
        return await m.reply_text("✅ Caption updated!")
    await downloader(_,m)  # treat as link otherwise

# ========= FILE SEARCH =========
@bot.on_message(filters.command("file"))
async def find_file(_,m):
    if len(m.command)<2: return await m.reply_text("Usage: `/file keyword`")
    key=m.text.split(" ",1)[1].lower()
    data=list(files.find({"name":{"$regex":key,"$options":"i"}}))
    if not data: return await m.reply_text("❌ No match found.")
    await m.reply_text(f"📂 Found {len(data)} file(s), sending…")
    for f in data:
        try:
            if f["type"]=="video":
                await bot.send_video(m.chat.id,f["file_id"],caption=f["name"])
            else:
                await bot.send_document(m.chat.id,f["file_id"],caption=f["name"])
        except Exception as e:
            print(e)
        await asyncio.sleep(1)

# ========= DOWNLOADER =========
@bot.on_message(filters.private & ~filters.command(["start","help","setting","file"]))
async def downloader(_,m):
    if not await joined(m.from_user.id):
        kb=InlineKeyboardMarkup([[InlineKeyboardButton("📢 Join Channel",url=FORCE_LINK)]])
        return await m.reply_text("⚠️ Join update channel first 🌼",reply_markup=kb)

    url=m.text.strip()
    if not (url.startswith("http://") or url.startswith("https://")):
        return await m.reply_text("😅 That’s not a valid link!")

    pref=users.find_one({"_id":m.from_user.id})["pref"]
    fname="file.bin"; msg=await m.reply_text("📥 Starting download…")
    start=time.time();done=0;tot=0;last=0
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(url,allow_redirects=True) as r:
                tot=int(r.headers.get("Content-Length",0))
                cd=r.headers.get("Content-Disposition")
                if cd and "filename=" in cd:
                    fname=cd.split("filename=")[-1].strip('"; ')
                else:
                    mt=r.headers.get("Content-Type","")
                    ext=mimetypes.guess_extension(mt.split(";")[0].strip()) or ".bin"
                    base=os.path.basename(url.split("?")[0]) or "file"
                    fname=base if "." in base else base+ext
                with open(fname,"wb") as f:
                    async for chunk in r.content.iter_chunked(1024*512):
                        f.write(chunk); done+=len(chunk)
                        now=time.time()
                        if now-last>2:
                            spd=done/max((now-start),1)
                            eta=(tot-done)/spd if spd>0 else 0
                            await msg.edit_text(bar_block(fname,done,tot,spd,eta,"Downloading"))
                            last=now
        # Upload to logs first (archive copy)
        await msg.edit_text("✅ Download done — archiving to logs…")
        logmsg=await log_doc(fname,f"📦 Backup: {fname}")
        if not logmsg: raise Exception("No log copy!")
        # Send to user with upload ETA
        st=time.time(); fsize=os.path.getsize(fname)
        sent=None
        async def progress(current,total):
            elapsed=time.time()-st
            spd=current/max(elapsed,1)
            eta=(total-current)/spd if spd>0 else 0
            try: asyncio.create_task(msg.edit_text(bar_block(fname,current,total,spd,eta,"Uploading")))
            except: pass
        if pref.get("video"):
            sent=await bot.send_video(m.chat.id,fname,caption=pref.get("caption",""),
                                      progress=progress)
            ftype="video"
        else:
            sent=await bot.send_document(m.chat.id,fname,caption=pref.get("caption",""),
                                         progress=progress)
            ftype="document"
        btn=InlineKeyboardMarkup([[InlineKeyboardButton("💬 Contact Owner",url="https://t.me/technicalserena")]])
        await sent.edit_reply_markup(btn)
        await msg.delete()
        files.insert_one({"name":fname,"file_id":logmsg.document.file_id,"type":ftype})
        await log_text(f"📤 Delivered {fname} to {m.from_user.mention}")
    except Exception as e:
        await msg.edit_text(f"❌ Error: `{e}`"); await log_text(f"⚠️ {e}")
    finally:
        if os.path.exists(fname): os.remove(fname)

print("💠 SERENA ready for Render deployment")
bot.run()
