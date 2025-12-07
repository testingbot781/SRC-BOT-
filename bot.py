import os, aiohttp, asyncio, time, mimetypes, tempfile, threading, psutil, subprocess
from pyrogram import Client, filters
from pyrogram.errors import FloodWait, UserNotParticipant
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from flask import Flask
from pymongo import MongoClient

# ------------ CONFIG ------------
API_ID     = int(os.getenv("API_ID"))
API_HASH   = os.getenv("API_HASH")
BOT_TOKEN  = os.getenv("BOT_TOKEN")
MONGO_URL  = os.getenv("MONGO_URL")

OWNER_ID   = 1598576202
LOGS_CH    = -1003286415377
FORCE_CH   = "serenaunzipbot"
FORCE_LINK = "https://t.me/serenaunzipbot"

# optional Instagram credentials (safe to omit)
INSTA_SESSION = os.getenv("INSTA_SESSION", "")
INSTA_COOKIES = os.getenv("INSTA_COOKIES", "")

# ------------ DATABASE ------------
mongo = MongoClient(MONGO_URL)
db = mongo["serena"]
users = db["users"]
files = db["files"]

# ------------ BOT ------------
bot = Client("SERENA", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

# ------------ FLASK keep‑alive ------------
app = Flask(__name__)
@app.route("/", methods=["GET","HEAD","POST"])
def home(): return "💠 SERENA alive"
def run_flask():
    port=int(os.environ.get("PORT",10000))
    app.run(host="0.0.0.0", port=port, threaded=True)

# ------------ HELPERS ------------
def fmt_size(n):
    for u in ["B","KB","MB","GB","TB"]:
        if n<1024:return f"{n:.2f}{u}"
        n/=1024
    return f"{n:.2f}PB"

def fmt_time(sec):
    if sec<=0:return "<1 s"
    m,s=divmod(int(sec),60);h,m=divmod(m,60)
    if h:return f"{h} h {m} m {s} s"
    if m:return f"{m} m {s} s"
    return f"{s} s"

def make_bar(name,phase,done,total,speed):
    pct=done/total*100 if total else 0
    bar="●"*int(18*pct/100)+"○"*(18-int(18*pct/100))
    eta=fmt_time((total-done)/speed if speed>0 else 0)
    return f"**{phase}**\n\n`{name}`\n[{bar}]\n💞 {pct:.2f}% ✅ {fmt_size(done)}/{fmt_size(total)}\n🚀 {fmt_size(speed)}/s ⏳ {eta}"

async def joined(uid):
    try:
        await bot.get_chat_member(FORCE_CH, uid)
        return True
    except UserNotParticipant: return False
    except: return False

async def log_msg(txt): 
    try: await bot.send_message(LOGS_CH, txt)
    except: pass
async def log_doc(path,cap): 
    try: return await bot.send_document(LOGS_CH,path,caption=cap)
    except: return None

# ------------ UTIL users ------------
async def ensure_user(uid):
    if not users.find_one({"_id":uid}):
        users.insert_one({"_id":uid,"ptype":"doc"})

# ------------ SETTINGS COMMAND ------------
@bot.on_message(filters.command("settings"))
async def settings(_,m):
    await ensure_user(m.from_user.id)
    mode=users.find_one({"_id":m.from_user.id}).get("ptype","doc")
    kb=[[InlineKeyboardButton("📄 Upload as Document"+(" ✅" if mode=="doc" else ""),
                              callback_data="m_doc"),
         InlineKeyboardButton("🎥 Upload as Video"+(" ✅" if mode=="vid" else ""),
                              callback_data="m_vid")]]
    await m.reply_text("⚙️ Choose upload mode:",reply_markup=InlineKeyboardMarkup(kb))

@bot.on_callback_query(filters.regex("^m_"))
async def changemode(_,q):
    mode="vid" if q.data.endswith("vid") else "doc"
    users.update_one({"_id":q.from_user.id},{"$set":{"ptype":mode}},upsert=True)
    await q.answer("✅ Saved")
    await settings(_,q.message)

# ------------ CANCEL ------------
cancel_flags={}
@bot.on_message(filters.command("cancel"))
async def cancel(_,m):
    cancel_flags[m.from_user.id]=True
    await m.reply_text("🛑 Cancelling current download …")

# ------------ FILE SEARCH ------------
@bot.on_message(filters.command("file"))
async def search_file(_,m):
    if len(m.command)<2:return await m.reply_text("Usage: /file <keyword>")
    key=m.text.split(" ",1)[1]
    docs=list(files.find({"name":{"$regex":key,"$options":"i"}}))
    if not docs:return await m.reply_text("❌ No match found.")
    await m.reply_text(f"📂 Found {len(docs)} file(s), sending …")
    for f in docs:
        await bot.send_document(m.chat.id,f["file_id"],caption=f["name"],
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("💬 Contact Owner",
                url="https://t.me/technicalserena")]]))
        await asyncio.sleep(1)

# ------------ LINK DETECTOR (private + groups) ------------
@bot.on_message(filters.text & filters.incoming)
async def detect(_,m):
    if not m.text: return
    for url in m.text.split():
        if url.startswith("http"):
            asyncio.create_task(process_link(m,url))

# ------------ M3U8 & Instagram ------------
async def m3u8_dl(url,path):
    cmd=f'ffmpeg -y -i "{url}" -c copy "{path}"'
    p=await asyncio.create_subprocess_shell(cmd,stdout=asyncio.subprocess.DEVNULL,stderr=asyncio.subprocess.DEVNULL)
    await p.communicate()
    return os.path.exists(path)

async def insta_dl(url,path):
    try:
        import instaloader, re
        L = instaloader.Instaloader(save_metadata=False,download_comments=False,compress_json=False,
                                    dirname_pattern=os.path.dirname(path))
        if INSTA_SESSION: L.load_session_from_file("", INSTA_SESSION)
        if INSTA_COOKIES: L.context.load_session_from_cookies_string(INSTA_COOKIES)
        shortcode = re.search(r"/p/([^/?]+)/", url)
        if not shortcode: return False
        L.download_post(instaloader.Post.from_shortcode(L.context, shortcode.group(1)), target=os.path.dirname(path))
        # find first mp4 in folder
        for f in os.listdir(os.path.dirname(path)):
            if f.endswith(".mp4"): os.rename(os.path.join(os.path.dirname(path),f),path); break
        return os.path.exists(path)
    except Exception as e:
        print("insta_dl error:", e)
        return False

# ------------ CORE PROCESS ------------
async def process_link(m,url):
    uid=m.from_user.id if m.from_user else 0
    await ensure_user(uid)
    pref=users.find_one({"_id":uid}).get("ptype","doc")
    tmp=tempfile.gettempdir()
    name="file.bin"
    msg=await m.reply_text("📥 Starting download …")
    try:
        if ".m3u8" in url:
            name="video.mp4"; path=os.path.join(tmp,name)
            await msg.edit_text("🎞️ Fetching M3U8 stream …")
            ok=await m3u8_dl(url,path)
            if not ok:return await msg.edit_text("⚠️ Failed to fetch stream !")
        elif "instagram.com" in url:
            name="insta.mp4"; path=os.path.join(tmp,name)
            await msg.edit_text("📸 Fetching Instagram video …")
            ok=await insta_dl(url,path)
            if not ok:return await msg.edit_text("⚠️ Unable to download Instagram link.")
        else:
            async with aiohttp.ClientSession() as s:
                async with s.get(url,allow_redirects=True) as r:
                    total=int(r.headers.get("Content-Length",0))
                    cd=r.headers.get("Content-Disposition")
                    if cd and "filename=" in cd:
                        name=cd.split("filename=")[-1].strip('\"; ')
                    else:
                        ct=r.headers.get("Content-Type","")
                        ext=mimetypes.guess_extension(ct.split(";")[0].strip()) or ".bin"
                        base=os.path.basename(url.split("?")[0]) or "file"
                        name=base if "." in base else base+ext
                    path=os.path.join(tmp,name)
                    done,start,last=0,time.time(),0
                    with open(path,"wb") as f:
                        async for chunk in r.content.iter_chunked(1024*512):
                            if cancel_flags.get(uid): 
                                await msg.edit_text("🛑 Cancelled.")
                                return
                            f.write(chunk);done+=len(chunk)
                            now=time.time()
                            if now-last>10:
                                spd=done/max(now-start,1)
                                try:await msg.edit_text(make_bar(name,"⬇️ Downloading",done,total,spd))
                                except FloodWait as e: await asyncio.sleep(e.value)
                                except: pass
                                last=now
        # upload to logs
        await msg.edit_text("📦 Uploading backup …")
        logm=await log_doc(path,f"📦 Backup:{name}")
        if not logm:return await msg.edit_text("⚠️ Backup failed.")
        # send to user/group
        await msg.edit_text("📤 Uploading to chat …")
        if pref=="vid":
            await bot.send_video(m.chat.id,path,caption=f"`{name}`",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("💬 Contact Owner",url="https://t.me/technicalserena")]]))
        else:
            await bot.send_document(m.chat.id,path,caption=f"`{name}`",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("💬 Contact Owner",url="https://t.me/technicalserena")]]))
        files.insert_one({"name":name,"file_id":logm.document.file_id,"type":pref})
        await msg.delete()
        await log_msg(f"✅ Delivered {name} to {m.chat.id}")
    except Exception as e:
        await msg.edit_text(f"❌ Error {e}")
        await log_msg(str(e))
    finally:
        try: os.remove(os.path.join(tmp,name))
        except: pass
        cancel_flags[uid]=False

# ------------ RUN ------------
if __name__=="__main__":
    print("💠 SERENA booting — Flask + Pyrogram working together")
    threading.Thread(target=run_flask,daemon=True).start()
    bot.run()
