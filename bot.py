import os, sys, threading, asyncio, aiohttp, time, mimetypes, tempfile, subprocess
from flask import Flask
from pyrogram import Client, filters
from pyrogram.errors import FloodWait, UserNotParticipant
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton

# --- make logs flush immediately so Render shows real errors ---
sys.stderr.reconfigure(line_buffering=True)
sys.stdout.reconfigure(line_buffering=True)

# ---------- ENV CONFIG ----------
API_ID     = int(os.getenv("API_ID"))
API_HASH   = os.getenv("API_HASH")
BOT_TOKEN  = os.getenv("BOT_TOKEN")
MONGO_URL  = os.getenv("MONGO_URL", "")
INSTA_SESSION = os.getenv("INSTA_SESSION", "")
INSTA_COOKIES = os.getenv("INSTA_COOKIES", "")

OWNER_ID   = 1598576202
LOGS_CH    = -1003286415377
FORCE_CH   = "serenaunzipbot"
FORCE_LINK = "https://t.me/serenaunzipbot"

# Try importing optional packages gracefully
try:
    from pymongo import MongoClient
    mongo = MongoClient(MONGO_URL) if MONGO_URL else None
    db = mongo["serena"] if mongo else None
    users = db["users"] if db else {}
    files = db["files"] if db else {}
except Exception as e:
    print("⚠️ Mongo disabled:", e)
    mongo = db = users = files = None

try:
    import instaloader
except Exception:
    instaloader = None
    print("⚠️ Instaloader unavailable – Instagram links disabled")

# ---------- BOT + FLASK ----------
bot = Client("SERENA", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)
app = Flask(__name__)

@app.route("/", methods=["GET", "HEAD", "POST"])
def home(): return "💠 SERENA running"

def run_web():
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port, threaded=True)

# ---------- SMALL HELPERS ----------
def fmt_size(n):
    for u in ["B","KB","MB","GB","TB"]:
        if n < 1024: return f"{n:.2f}{u}"
        n /= 1024
    return f"{n:.2f}PB"

def fmt_time(sec):
    if sec <= 0: return "<1 s"
    m, s = divmod(int(sec), 60); h, m = divmod(m, 60)
    if h: return f"{h} h {m} m {s} s"
    if m: return f"{m} m {s} s"
    return f"{s} s"

def progress_bar(name, phase, done, total, speed):
    pct = done/total*100 if total else 0
    fill = int(18*pct/100)
    bar = "●"*fill + "○"*(18-fill)
    eta = fmt_time((total-done)/speed if speed>0 else 0)
    return (f"**{phase}**\n\n"
            f"`{name}`\n[{bar}]\n"
            f"💞 {pct:.2f}% ✅ {fmt_size(done)}/{fmt_size(total)}\n"
            f"🚀 {fmt_size(speed)}/s ⏳ {eta}")

async def ensure_user(uid):
    if not users: return
    if not users.find_one({"_id": uid}):
        users.insert_one({"_id": uid, "ptype": "doc"})

async def user_pref(uid):
    if not users: return "doc"
    u = users.find_one({"_id": uid}) or {}
    return u.get("ptype", "doc")

async def log_msg(txt):
    try: await bot.send_message(LOGS_CH, txt)
    except: pass
async def log_doc(path, cap):
    try: return await bot.send_document(LOGS_CH, path, caption=cap)
    except: return None

async def joined(uid):
    try:
        await bot.get_chat_member(FORCE_CH, uid)
        return True
    except UserNotParticipant: return False
    except: return False

cancel_flags = {}

# ---------- SETTINGS ----------
@bot.on_message(filters.command("settings"))
async def settings(_, m):
    await ensure_user(m.from_user.id)
    mode = await user_pref(m.from_user.id)
    kb = [[
        InlineKeyboardButton("📄 Upload as Document"+(" ✅" if mode=="doc" else ""), callback_data="mdoc"),
        InlineKeyboardButton("🎥 Upload as Video"+(" ✅" if mode=="vid" else ""), callback_data="mvid")
    ]]
    await m.reply_text("⚙️ Upload mode:", reply_markup=InlineKeyboardMarkup(kb))

@bot.on_callback_query()
async def cb(_, q):
    if q.data in ("mdoc","mvid") and users:
        mode = "vid" if q.data=="mvid" else "doc"
        users.update_one({"_id": q.from_user.id},{"$set":{"ptype":mode}},upsert=True)
        await q.answer("✅ Saved")
        await settings(_, q.message)

# ---------- CANCEL ----------
@bot.on_message(filters.command("cancel"))
async def cancel(_, m):
    cancel_flags[m.from_user.id] = True
    await m.reply_text("🛑 Cancelling current task …")

# ---------- SIMPLE REPLACEMENT FOR DATABASE SEARCH ----------
@bot.on_message(filters.command("file"))
async def file_cmd(_, m):
    await m.reply_text("ℹ️ Database search not active on this build (but links work fine).")

# ---------- LINK DETECTOR ----------
@bot.on_message(filters.text & filters.incoming)
async def link_detect(_, m):
    if not m.text: return
    for url in m.text.split():
        if url.startswith("http"):
            asyncio.create_task(download_and_send(m, url))

# ---------- SPECIAL HANDLERS ----------
async def m3u8_to_mp4(url, out):
    cmd = f'ffmpeg -y -i "{url}" -c copy "{out}"'
    proc = await asyncio.create_subprocess_shell(cmd,
              stdout=asyncio.subprocess.DEVNULL,
              stderr=asyncio.subprocess.DEVNULL)
    await proc.communicate()
    return os.path.exists(out)

async def insta_dl(url, out):
    if not instaloader: return False
    try:
        import re
        L = instaloader.Instaloader(save_metadata=False)
        shortcode = re.search(r"/p/([^/?]+)/", url)
        if not shortcode: return False
        post = instaloader.Post.from_shortcode(L.context, shortcode.group(1))
        L.download_post(post, target=os.path.dirname(out))
        for f in os.listdir(os.path.dirname(out)):
            if f.endswith(".mp4"):
                os.rename(os.path.join(os.path.dirname(out), f), out)
                return True
        return False
    except Exception as e:
        print("insta_dl error:", e)
        return False

# ---------- DOWNLOAD & SEND ----------
async def download_and_send(m, url):
    uid = m.from_user.id if m.from_user else 0
    await ensure_user(uid)
    pref = await user_pref(uid)
    temp = tempfile.gettempdir()
    name, path = "file.bin", os.path.join(temp,"file.bin")
    msg = await m.reply_text("📥 Starting download …")
    try:
        # --- handle different link types ---
        if ".m3u8" in url:
            name="video.mp4"; path=os.path.join(temp,name)
            await msg.edit_text("🎞️ Fetching M3U8 stream …")
            ok = await m3u8_to_mp4(url, path)
            if not ok: return await msg.edit_text("⚠️ Stream download failed !")
        elif "instagram.com" in url:
            name="insta.mp4"; path=os.path.join(temp,name)
            await msg.edit_text("📸 Fetching Instagram video …")
            ok = await insta_dl(url, path)
            if not ok: return await msg.edit_text("⚠️ Unable to download Instagram link.")
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
                        name = base if "." in base else base+ext
                    path=os.path.join(temp,name)
                    done,start,last=0,time.time(),0
                    with open(path,"wb") as f:
                        async for chunk in r.content.iter_chunked(1024*512):
                            if cancel_flags.get(uid):
                                await msg.edit_text("🛑 Cancelled by user")
                                return
                            f.write(chunk); done+=len(chunk)
                            now=time.time()
                            if now-last>10:
                                spd=done/max(now-start,1)
                                try: await msg.edit_text(progress_bar(name,"⬇️ Downloading",done,total,spd))
                                except FloodWait as e: await asyncio.sleep(e.value)
                                except: pass
                                last=now
        # --- upload to logs ---
        await msg.edit_text("📦 Uploading backup to Logs …")
        logm = await log_doc(path, f"📦 Backup:{name}")
        # --- send to chat ---
        await msg.edit_text("📤 Uploading to chat …")
        if pref=="vid":
            await bot.send_video(m.chat.id, path, caption=f"`{name}`",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("💬 Contact Owner",
                    url="https://t.me/technicalserena")]]))
        else:
            await bot.send_document(m.chat.id, path, caption=f"`{name}`",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("💬 Contact Owner",
                    url="https://t.me/technicalserena")]]))
        if files and logm:
            files.insert_one({"name":name,"file_id":logm.document.file_id,"type":pref})
        await msg.delete()
        await log_msg(f"✅ Delivered {name} to {m.chat.id}")
    except Exception as e:
        await msg.edit_text(f"❌ Error {e}")
        await log_msg(str(e))
    finally:
        try: os.remove(path)
        except: pass
        cancel_flags[uid]=False

# ---------- /start ----------
@bot.on_message(filters.command("start"))
async def start(_,m):
    kb=InlineKeyboardMarkup([
        [InlineKeyboardButton("📢 Join Channel",url=FORCE_LINK)],
        [InlineKeyboardButton("💬 Contact Owner",url="https://t.me/technicalserena")]
    ])
    await m.reply_text("💎 SERENA Downloader 💎\n\nSend any direct URL or .m3u8 link & I'll fetch it for you 😘",reply_markup=kb)

# ---------- RUN ----------
if __name__ == "__main__":
    print("🚀 SERENA booting — Flask + Pyrogram together")
    threading.Thread(target=run_web, daemon=True).start()
    bot.run()
