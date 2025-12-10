import os, aiohttp, asyncio, time, mimetypes, threading, psutil
from pyrogram import Client, filters
from pyrogram.errors import FloodWait, UserNotParticipant, UserIsBlocked
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from flask import Flask
from pymongo import MongoClient

# ---------- CONFIG ----------
API_ID=int(os.getenv("API_ID"))
API_HASH=os.getenv("API_HASH")
BOT_TOKEN=os.getenv("BOT_TOKEN")
MONGO_URL=os.getenv("MONGO_URL")
OWNER_ID=1598576202
LOGS_CHANNEL=-1003286415377
FORCE_CH="serenaunzipbot"
FORCE_LINK="https://t.me/serenaunzipbot"

# ---------- DATABASE ----------
mongo=MongoClient(MONGO_URL)
db=mongo["serena"]
users=db["users"]
files=db["files"]

# ---------- BOT ----------
bot=Client("SERENA",api_id=API_ID,api_hash=API_HASH,bot_token=BOT_TOKEN)

# ---------- FLASK (Render keep‑alive) ----------
flask_app=Flask(__name__)
@flask_app.route("/",methods=["GET","POST","HEAD"])
def home(): return "💠 SERENA port open"
def run_flask():
    port=int(os.environ.get("PORT",10000))
    flask_app.run(host="0.0.0.0",port=port)

# ---------- HELPERS ----------
def size_fmt(n):
    for u in ["B","KB","MB","GB","TB"]:
        if n<1024:return f"{n:.2f} {u}"
        n/=1024
    return f"{n:.2f} PB"

def time_fmt(sec):
    if sec<=0:return "<1 s"
    m,s=divmod(int(sec),60);h,m=divmod(m,60)
    if h:return f"{h} h {m} m {s} s"
    if m:return f"{m} m {s} s"
    return f"{s} s"

def make_block(name,phase,done,total,speed):
    pct=done/total*100 if total else 0
    bar="●"*int(18*pct/100)+"○"*(18-int(18*pct/100))
    eta=time_fmt((total-done)/speed if speed>0 else 0)
    return (f"**{phase}**\n\n"
            f"`{name}`\n[{bar}]\n"
            f"💞 {pct:.2f}%  ✅ {size_fmt(done)}/{size_fmt(total)}\n"
            f"🚀 {size_fmt(speed)}/s  ⏳ {eta}")

async def ensure_user(uid):
    if not users.find_one({"_id":uid}):
        users.insert_one({"_id":uid,"queue":[]})

async def joined(uid):
    try:
        await bot.get_chat_member(FORCE_CH,uid)
        return True
    except UserNotParticipant: return False
    except: return False

async def log_text(t):
    try: await bot.send_message(LOGS_CHANNEL,t)
    except: pass
async def log_file(path,cap):
    try: return await bot.send_document(LOGS_CHANNEL,path,caption=cap)
    except: return None

# ---------- GLOBAL STATE ----------
active=set()
cancel_flag={}
# ---------- COMMANDS ----------
@bot.on_message(filters.command("start"))
async def start(_,m):
    await ensure_user(m.from_user.id)
    if not await joined(m.from_user.id):
        kb=InlineKeyboardMarkup([[InlineKeyboardButton("📢 Join Channel",url=FORCE_LINK)]])
        return await m.reply_text("⚠️ Join our Updates Channel first 🌼",reply_markup=kb)
    kb=InlineKeyboardMarkup([
        [InlineKeyboardButton("📢 Join Channel",url=FORCE_LINK)],
        [InlineKeyboardButton("💬 Contact Owner",url="https://t.me/technicalserena")]
    ])
    await m.reply_text("💎 **SERENA Downloader** 💎\n\nSend me a link and watch the animated progress! 💞",reply_markup=kb)

@bot.on_message(filters.command("help"))
async def help(_,m):
    msg=("🌸 **How to use**\n"
         "1️⃣ Send direct URL (mp4, zip, etc)\n"
         "2️⃣ Watch ETA bars (10 s interval → no flood)\n"
         "3️⃣ Wait 15 s between tasks\n\n"
         "`/cancel` – stop current download \n"
         "`/file <word>` – search in archive")
    await m.reply_text(msg)

@bot.on_message(filters.command("cancel"))
async def cancel(_,m):
    cancel_flag[m.from_user.id]=True
    await m.reply_text("🛑 Cancelling current job …")


# ---- /SETTINGS ----
@bot.on_message(filters.command("settings"))
async def settings(_,m):
    await ensure_user(m.from_user.id)
    u=users.find_one({"_id":m.from_user.id})
    opt=u.get("opt","video"); cap=u.get("caption","")
    desc=("⚙️ **SERENA Settings**\n\n"
          "Choose upload mode and set optional caption for future downloads 💖")
    kb=[
        [InlineKeyboardButton("🎥 Upload as Video"+(" ✅" if opt=="video" else ""),callback_data="vid")],
        [InlineKeyboardButton("📄 Upload as Document"+(" ✅" if opt=="doc" else ""),callback_data="doc")],
        [InlineKeyboardButton("➕ Add Caption",callback_data="add_cap"),
         InlineKeyboardButton("♻️ Reset Caption",callback_data="clr_cap")]
    ]
    msg=desc+f"\n\n🖋 Current Caption: `{cap if cap else 'None'}`"
    await m.reply_text(msg,reply_markup=InlineKeyboardMarkup(kb))

@bot.on_callback_query()
async def settings_cb(_,q):
    data=q.data; uid=q.from_user.id
    await ensure_user(uid)
    if data=="vid" or data=="doc":
        mode="video" if data=="vid" else "doc"
        users.update_one({"_id":uid},{"$set":{"opt":mode}})
        await q.answer("✅ Updated mode")
        await q.message.reply_text(f"✨ Mode set to {'🎥 Video' if mode=='video' else '📄 Document'}")
    elif data=="add_cap":
        users.update_one({"_id":uid},{"$set":{"waiting_cap":True}})
        await q.message.reply_text("🖋 Send me the new caption text now (ex: `01. My Title`) ⬇️",parse_mode="markdown")
    elif data=="clr_cap":
        users.update_one({"_id":uid},{"$set":{"caption":""}})
        await q.message.reply_text("♻️ Caption cleared successfully !")
    await q.answer()

@bot.on_message(filters.command("file"))
async def file(_,m):
    if len(m.command)<2: 
        return await m.reply_text("Use /file <keyword>")
    key=m.text.split(" ",1)[1]
    found=list(files.find({"name":{"$regex":key,"$options":"i"}}))
    if not found:return await m.reply_text("❌ No match found in archive.")
    await m.reply_text(f"📂 Found {len(found)} match(es) – sending …")
    for f in found:
        await bot.send_document(m.chat.id,f["file_id"],caption=f["name"],
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("💬 Contact Owner",url="https://t.me/technicalserena")]]))
        await asyncio.sleep(1)

# ---------- HANDLERS ----------
@bot.on_message(filters.private & ~filters.command(["start","help","file","cancel"]))
async def queue_handle(_,m):
    url=m.text.strip()
    if not url.startswith("http"): return await m.reply_text("😅 Not a valid link.")
    await ensure_user(m.from_user.id)
    await push_q(m.from_user.id,url)
    if m.from_user.id in active: 
        return await m.reply_text("🕐 Added to queue dear ♥")
    active.add(m.from_user.id)
    cancel_flag[m.from_user.id]=False
    while True:
        nxt=await pop_q(m.from_user.id)
        if not nxt: break
        await process(m,nxt)
        await asyncio.sleep(15) # gap between tasks
    active.discard(m.from_user.id)

async def push_q(uid,url):
    u=users.find_one({"_id":uid}) or {"queue":[]}
    q=u.get("queue",[]);q.append(url)
    users.update_one({"_id":uid},{"$set":{"queue":q}})

async def pop_q(uid):
    u=users.find_one({"_id":uid})
    if not u or not u.get("queue"):return None
    url=u["queue"].pop(0)
    users.update_one({"_id":uid},{"$set":{"queue":u["queue"]}})
    return url

# ---------- CORE ----------
async def process(m,url):
    uid=m.from_user.id
    msg=await m.reply_text("📥 Starting download …")
    name="file.bin"
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
                done,start,last=0,time.time(),0
                with open(name,"wb") as f:
                    async for chunk in r.content.iter_chunked(1024*512):
                        if cancel_flag.get(uid): 
                            await msg.edit_text("🛑 Cancelled.")
                            return
                        f.write(chunk); done+=len(chunk)
                        now=time.time()
                        if now-last>10: # 10 s interval
                            spd=done/max(now-start,1)
                            try: await msg.edit_text(make_block(name,"⬇️ Downloading",done,total,spd))
                            except FloodWait as e: await asyncio.sleep(e.value)
                            except: pass
                            last=now
        # upload to logs
        await msg.edit_text("📦 Uploading backup …")
        start=time.time()
        async def prog(c,t):
            if cancel_flag.get(uid): raise asyncio.CancelledError
            if (time.time()-start)%10<1:
                spd=c/max(time.time()-start,1)
                try: asyncio.create_task(msg.edit_text(make_block(name,"📦 Backup Upload",c,t,spd)))
                except: pass
        logmsg=await bot.send_document(LOGS_CHANNEL,name,caption=f"📦 Backup:{name}",progress=prog)
        # upload to user
        await msg.edit_text("📤 Sending to you …")
        start=time.time()
        async def uprog(c,t):
            if cancel_flag.get(uid): raise asyncio.CancelledError
            if (time.time()-start)%10<1:
                spd=c/max(time.time()-start,1)
                try: asyncio.create_task(msg.edit_text(make_block(name,"📤 Uploading to User",c,t,spd)))
                except: pass
        await bot.send_document(uid,name,caption=f"`{name}`",
            progress=uprog,
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("💬 Contact Owner",url="https://t.me/technicalserena")]]))
        files.insert_one({"name":name,"file_id":logmsg.document.file_id,"type":"document"})
        await msg.delete()
        await log_text(f"✅ Delivered {name} to {uid}")
    except Exception as e:
        await msg.edit_text(f"❌ Error {e}")
        await log_text(str(e))
    finally:
        if os.path.exists(name): os.remove(name)
        cancel_flag[uid]=False

# ---------- ENTRY ----------
if __name__=="__main__":
    print("💠 SERENA starting – Flask for Render + polling active")
    threading.Thread(target=run_flask).start()
    bot.run()
