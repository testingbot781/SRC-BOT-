import os, aiohttp, asyncio, time, mimetypes, threading, tempfile, subprocess
from pyrogram import Client, filters
from pyrogram.errors import FloodWait, UserNotParticipant
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from flask import Flask
from pymongo import MongoClient

# ---------- CONFIG ----------
API_ID = int(os.getenv("API_ID"))
API_HASH = os.getenv("API_HASH")
BOT_TOKEN = os.getenv("BOT_TOKEN")
MONGO_URL = os.getenv("MONGO_URL")
OWNER_ID = 1598576202
LOGS_CHANNEL = -1003286415377
FORCE_CH = "serenaunzipbot"
FORCE_LINK = "https://t.me/serenaunzipbot"
INSTA_SESSION = os.getenv("INSTA_SESSION", "")
INSTA_COOKIES = os.getenv("INSTA_COOKIES", "")

# ---------- DATABASE ----------
mongo = MongoClient(MONGO_URL)
db = mongo["serena"]
users = db["users"]
files = db["files"]

# ---------- BOT ----------
bot = Client("SERENA", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

# ---------- FLASK ----------
flask_app = Flask(__name__)
@flask_app.route("/", methods=["GET","POST","HEAD"])
def home(): return "💠 SERENA port open"
def run_flask():
    port = int(os.environ.get("PORT", 10000))
    flask_app.run(host="0.0.0.0", port=port, threaded=True)

# ---------- HELPERS ----------
def size_fmt(n):
    for u in ["B","KB","MB","GB","TB"]:
        if n < 1024: return f"{n:.2f} {u}"
        n /= 1024
    return f"{n:.2f} PB"

def time_fmt(sec):
    if sec <= 0: return "<1 s"
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
            f"💞 {pct:.2f}% ✅ {size_fmt(done)}/{size_fmt(total)}\n"
            f"🚀 {size_fmt(speed)}/s ⏳ {eta}")

async def ensure_user(uid):
    if not users.find_one({"_id":uid}):
        users.insert_one({"_id":uid,"queue":[],"opt":"video"})   # default mode video

async def joined(uid):
    try:
        await bot.get_chat_member(FORCE_CH, uid)
        return True
    except UserNotParticipant: return False
    except: return False

async def log_text(t):
    try: await bot.send_message(LOGS_CHANNEL,t)
    except: pass
async def log_file(path,cap):
    try: return await bot.send_document(LOGS_CHANNEL,path,caption=cap)
    except: return None

# ---------- GLOBAL ----------
active=set()
cancel_flag={}

# ---------- SETTINGS ----------
@bot.on_message(filters.command("settings"))
async def settings(_,m):
    await ensure_user(m.from_user.id)
    u=users.find_one({"_id":m.from_user.id})
    opt=u.get("opt","video")
    kb=[[
        InlineKeyboardButton("🎥 Upload as Video"+(" ✅" if opt=="video" else ""), callback_data="as_video"),
        InlineKeyboardButton("📄 Upload as Document"+(" ✅" if opt=="doc" else ""), callback_data="as_doc")
    ]]
    await m.reply_text("⚙️ Choose Upload Preference:",reply_markup=InlineKeyboardMarkup(kb))

@bot.on_callback_query(filters.regex("^as_"))
async def mode_toggle(_,q):
    value="video" if q.data=="as_video" else "doc"
    users.update_one({"_id":q.from_user.id},{"$set":{"opt":value}},upsert=True)
    await q.answer("✅ Updated")
    await settings(_,q.message)

# ---------- CANCEL ----------
@bot.on_message(filters.command("cancel"))
async def cancel(_,m):
    cancel_flag[m.from_user.id]=True
    await m.reply_text("🛑 Cancelling current job …")

# ---------- M3U8 ----------
async def download_m3u8(url,out):
    cmd=f'ffmpeg -y -i "{url}" -c copy "{out}"'
    p=await asyncio.create_subprocess_shell(cmd,stdout=asyncio.subprocess.DEVNULL,stderr=asyncio.subprocess.DEVNULL)
    await p.communicate()
    return os.path.exists(out)

# ---------- INSTAGRAM ----------
async def insta_download(url,out):
    try:
        import instaloader,re
        L=instaloader.Instaloader(save_metadata=False,download_comments=False)
        if INSTA_SESSION: L.load_session_from_file("", INSTA_SESSION)
        sc=re.search(r"/p/([^/?]+)/",url)
        if not sc:return False
        post=instaloader.Post.from_shortcode(L.context,sc.group(1))
        L.download_post(post,target=os.path.dirname(out))
        for f in os.listdir(os.path.dirname(out)):
            if f.endswith(".mp4"):
                os.rename(os.path.join(os.path.dirname(out),f),out)
                return True
        return False
    except Exception as e:
        print("instaloader error:",e)
        return False

# ---------- QUEUE HANDLER ----------
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

@bot.on_message(filters.private & ~filters.command(["start","help","file","cancel","settings"]))
async def queue_handle(_,m):
    url=m.text.strip()
    if not url.startswith("http"): 
        return await m.reply_text("😅 That doesn’t look like a link.")
    await ensure_user(m.from_user.id)
    await push_q(m.from_user.id,url)
    if m.from_user.id in active:
        return await m.reply_text("🕐 Added to queue 💞")
    active.add(m.from_user.id)
    cancel_flag[m.from_user.id]=False
    while True:
        nxt=await pop_q(m.from_user.id)
        if not nxt: break
        await process(m,nxt)
        await asyncio.sleep(15)
    active.discard(m.from_user.id)

# ---------- CORE ----------
async def process(m,url):
    uid=m.from_user.id
    cfg=users.find_one({"_id":uid}) or {}
    mode=cfg.get("opt","video")
    msg=await m.reply_text("📥 Starting download …")
    tmp=tempfile.gettempdir()
    name="file.bin";path=os.path.join(tmp,name)
    try:
        # --- handle m3u8 or insta or normal ---
        if ".m3u8" in url:
            name="video.mp4";path=os.path.join(tmp,name)
            await msg.edit_text("🎞️ Fetching M3U8 stream …")
            ok=await download_m3u8(url,path)
            if not ok:return await msg.edit_text("⚠️ Failed to fetch M3U8 stream")
        elif "instagram.com" in url:
            name="insta.mp4";path=os.path.join(tmp,name)
            await msg.edit_text("📸 Fetching Instagram video …")
            ok=await insta_download(url,path)
            if not ok:return await msg.edit_text("⚠️ Unable to download Instagram video")
        else:
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
                    path=os.path.join(tmp,name)
                    done,start,last=0,time.time(),0
                    with open(path,"wb") as f:
                        async for chunk in r.content.iter_chunked(1024*512):
                            if cancel_flag.get(uid):
                                await msg.edit_text("🛑 Cancelled by user")
                                return
                            f.write(chunk);done+=len(chunk)
                            now=time.time()
                            if now-last>10:
                                spd=done/max(now-start,1)
                                try: await msg.edit_text(make_block(name,"⬇️ Downloading",done,total,spd))
                                except FloodWait as e: await asyncio.sleep(e.value)
                                except: pass
                                last=now
        # --- backup to logs ---
        await msg.edit_text("📦 Uploading backup …")
        logm=await log_file(path,f"📦 Backup:{name}")
        # --- send in opposite mode ---
        await msg.edit_text("📤 Uploading to you …")
        doc_ext=(".zip",".rar",".7z",".txt",".pdf",".apk",".iso",".gz",".docx",".xlsx",".pptx",".csv",".json",".xml",".html",".mp3",".m4a",".wav",".ogg",".flac",".mkv")
        # if mode is video => make docs & videos appear as videos
        if mode=="video":
            await bot.send_video(uid,path,caption=f"`{name}`",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("💬 Contact Owner",url="https://t.me/technicalserena")]]))
        else:
            await bot.send_document(uid,path,caption=f"`{name}`",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("💬 Contact Owner",url="https://t.me/technicalserena")]]))
        files.insert_one({"name":name,"file_id":logm.document.file_id,"type":mode})
        await msg.delete()
        await log_text(f"✅ Delivered {name} to {uid}")
    except Exception as e:
        await msg.edit_text(f"❌ Error {e}")
        await log_text(str(e))
    finally:
        try: os.remove(path)
        except: pass
        cancel_flag[uid]=False

# ---------- ENTRY ----------
if __name__=="__main__":
    print("💠 SERENA starting – Flask for Render + polling active")
    threading.Thread(target=run_flask,daemon=True).start()
    bot.run()
