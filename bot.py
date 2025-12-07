import os, sys, threading, asyncio, aiohttp, time, mimetypes, tempfile, subprocess
from flask import Flask
from pyrogram import Client, filters
from pyrogram.errors import FloodWait, UserNotParticipant
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton

# Flush logs instantly on Render
sys.stdout.reconfigure(line_buffering=True)
sys.stderr.reconfigure(line_buffering=True)

# ---------- CONFIG ----------
API_ID     = int(os.getenv("API_ID"))
API_HASH   = os.getenv("API_HASH")
BOT_TOKEN  = os.getenv("BOT_TOKEN")
MONGO_URL  = os.getenv("MONGO_URL", "")
OWNER_ID   = 1598576202
LOGS_CH    = -1003286415377
FORCE_CH   = "serenaunzipbot"
FORCE_LINK = "https://t.me/serenaunzipbot"
INSTA_SESSION = os.getenv("INSTA_SESSION", "")
INSTA_COOKIES = os.getenv("INSTA_COOKIES", "")

# ---------- SAFE DATABASE INIT ----------
try:
    from pymongo import MongoClient
    mongo = MongoClient(MONGO_URL) if MONGO_URL else None
    db = mongo["serena"] if mongo is not None else None
    users = db["users"] if db is not None else None
    files = db["files"] if db is not None else None
except Exception as e:
    print("⚠️ Mongo disabled:", e)
    mongo = db = users = files = None

# ---------- OPTIONAL Instagram ----------
try:
    import instaloader
except Exception as e:
    instaloader = None
    print("⚠️ Instaloader module missing:", e)

# ---------- BOT + FLASK ----------
bot = Client("SERENA", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

app = Flask(__name__)
@app.route("/", methods=["GET","HEAD","POST"])
def home(): return "💠 SERENA alive"
def run_web(): 
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port, threaded=True)

# ---------- HELPERS ----------
def fmt_size(n):
    for u in ["B","KB","MB","GB","TB"]:
        if n<1024:return f"{n:.2f}{u}"
        n/=1024
    return f"{n:.2f}PB"

def fmt_time(s):
    if s<=0:return "<1 s"
    m,s=divmod(int(s),60);h,m=divmod(m,60)
    if h:return f"{h} h {m} m {s} s"
    if m:return f"{m} m {s} s"
    return f"{s} s"

def progress_bar(name,phase,done,total,speed):
    pct=done/total*100 if total else 0
    bar="●"*int(18*pct/100)+"○"*(18-int(18*pct/100))
    eta=fmt_time((total-done)/speed if speed>0 else 0)
    return (f"**{phase}**\n\n"
            f"`{name}`\n[{bar}]\n"
            f"💞 {pct:.2f}% ✅ {fmt_size(done)}/{fmt_size(total)}\n"
            f"🚀 {fmt_size(speed)}/s ⏳ {eta}")

async def joined(uid):
    try:
        await bot.get_chat_member(FORCE_CH, uid)
        return True
    except UserNotParticipant: return False
    except: return False

async def log_msg(t):
    try: await bot.send_message(LOGS_CH, t)
    except: pass
async def log_doc(path, cap):
    try: return await bot.send_document(LOGS_CH, path, caption=cap)
    except: return None

async def ensure_user(uid):
    if users is None: return
    if users.find_one({"_id":uid}) is None:
        users.insert_one({"_id":uid,"ptype":"doc"})

async def get_mode(uid):
    if users is None: return "doc"
    u=users.find_one({"_id":uid}) or {}
    return u.get("ptype","doc")

# ---------- SETTINGS ----------
@bot.on_message(filters.command("settings"))
async def settings(_,m):
    await ensure_user(m.from_user.id)
    mode = await get_mode(m.from_user.id)
    kb = [[
        InlineKeyboardButton("📄 Upload as Document"+(" ✅" if mode=="doc" else ""),callback_data="mdoc"),
        InlineKeyboardButton("🎥 Upload as Video"+(" ✅" if mode=="vid" else ""),callback_data="mvid")
    ]]
    await m.reply_text("⚙️ Choose upload mode:",reply_markup=InlineKeyboardMarkup(kb))

@bot.on_callback_query()
async def cb_mode(_,q):
    if q.data in ("mdoc","mvid") and users is not None:
        mode="vid" if q.data=="mvid" else "doc"
        users.update_one({"_id":q.from_user.id},{"$set":{"ptype":mode}},upsert=True)
        await q.answer("✅ Saved.")
        await settings(_,q.message)

# ---------- CANCEL ----------
cancel_flags={}
@bot.on_message(filters.command("cancel"))
async def cancel(_,m):
    cancel_flags[m.from_user.id]=True
    await m.reply_text("🛑 Cancelling current task …")

# ---------- LINK DETECTOR ----------
@bot.on_message(filters.text & filters.incoming)
async def link(_,m):
    if not m.text: return
    for url in m.text.split():
        if url.startswith("http"):
            asyncio.create_task(process_link(m,url))

# ---------- SPECIAL ----------
async def m3u8_to_mp4(url,out):
    cmd=f'ffmpeg -y -i "{url}" -c copy "{out}"'
    p=await asyncio.create_subprocess_shell(cmd,stdout=asyncio.subprocess.DEVNULL,stderr=asyncio.subprocess.DEVNULL)
    await p.communicate()
    return os.path.exists(out)

async def insta_download(url,out):
    if not instaloader: return False
    try:
        import re
        L=instaloader.Instaloader(save_metadata=False)
        sc=re.search(r"/p/([^/?]+)/",url)
        if not sc: return False
        post=instaloader.Post.from_shortcode(L.context,sc.group(1))
        L.download_post(post,target=os.path.dirname(out))
        for f in os.listdir(os.path.dirname(out)):
            if f.endswith(".mp4"):
                os.rename(os.path.join(os.path.dirname(out),f),out)
                return True
        return False
    except Exception as e:
        print("insta error:",e)
        return False

# ---------- CORE ----------
async def process_link(m,url):
    uid=m.from_user.id if m.from_user else 0
    await ensure_user(uid)
    mode=await get_mode(uid)
    tmp=tempfile.gettempdir()
    name,path="file.bin",os.path.join(tmp,"file.bin")
    msg=await m.reply_text("📥 Starting download …")
    try:
        if ".m3u8" in url:
            name="video.mp4"; path=os.path.join(tmp,name)
            await msg.edit_text("🎞️ Fetching M3U8 stream …")
            ok=await m3u8_to_mp4(url,path)
            if not ok:return await msg.edit_text("⚠️ Failed to fetch stream !")
        elif "instagram.com" in url:
            name="insta.mp4"; path=os.path.join(tmp,name)
            await msg.edit_text("📸 Downloading Instagram video …")
            ok=await insta_download(url,path)
            if not ok:return await msg.edit_text("⚠️ Cannot download Instagram link.")
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
                                await msg.edit_text("🛑 Cancelled by user")
                                return
                            f.write(chunk);done+=len(chunk)
                            now=time.time()
                            if now-last>10:
                                spd=done/max(now-start,1)
                                try:await msg.edit_text(progress_bar(name,"⬇️ Downloading",done,total,spd))
                                except FloodWait as e:await asyncio.sleep(e.value)
                                except:pass
                                last=now
        await msg.edit_text("📦 Uploading backup to Logs …")
        logm=await log_doc(path,f"📦 Backup:{name}")
        await msg.edit_text("📤 Uploading to chat …")
        if mode=="vid":
            await bot.send_video(m.chat.id,path,caption=f"`{name}`",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("💬 Contact Owner",url="https://t.me/technicalserena")]]))
        else:
            await bot.send_document(m.chat.id,path,caption=f"`{name}`",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("💬 Contact Owner",url="https://t.me/technicalserena")]]))
        if files is not None and logm:
            files.insert_one({"name":name,"file_id":logm.document.file_id,"type":mode})
        await msg.delete()
        await log_msg(f"✅ Delivered {name} to {m.chat.id}")
    except Exception as e:
        await msg.edit_text(f"❌ Error {e}")
        await log_msg(str(e))
    finally:
        try: os.remove(path)
        except: pass
        cancel_flags[uid]=False

# ---------- START ----------
@bot.on_message(filters.command("start"))
async def start(_,m):
    kb=InlineKeyboardMarkup([
        [InlineKeyboardButton("📢 Join Channel",url=FORCE_LINK)],
        [InlineKeyboardButton("💬 Owner",url="https://t.me/technicalserena")]
    ])
    await m.reply_text("💎 **SERENA Downloader** 💎\n\nSend me any direct URL or .m3u8 link and I’ll fetch it with love 💞",reply_markup=kb)

# ---------- RUN ----------
if __name__=="__main__":
    print("💠 SERENA booting — Flask + Pyrogram together ✔️")
    threading.Thread(target=run_web, daemon=True).start()
    bot.run()
