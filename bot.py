import os, aiohttp, asyncio, time, mimetypes, tempfile, threading, subprocess
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
LOGS_CH = -1003286415377
FORCE_CH = "serenaunzipbot"
FORCE_LINK = "https://t.me/serenaunzipbot"

# optional Instagram credentials  (safe if left blank)
INSTA_SESSION = os.getenv("INSTA_SESSION", "")
INSTA_COOKIES = os.getenv("INSTA_COOKIES", "")

# ---------- DATABASE ----------
mongo = MongoClient(MONGO_URL)
db = mongo["serena"]
users = db["users"]
files = db["files"]

# ---------- BOT ----------
bot = Client("SERENA", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

# ---------- FLASK (Render health port) ----------
web = Flask(__name__)
@web.route("/", methods=["GET", "HEAD", "POST"])
def home(): return "💠 SERENA alive"
def run_web():
    port = int(os.environ.get("PORT", 10000))
    web.run(host="0.0.0.0", port=port, threaded=True)

# ---------- HELPERS ----------
def fmt_size(b):
    for u in ["B","KB","MB","GB","TB"]:
        if b < 1024: return f"{b:.2f}{u}"
        b /= 1024
    return f"{b:.2f}PB"

def fmt_time(sec):
    if sec <= 0: return "<1 s"
    m, s = divmod(int(sec), 60); h, m = divmod(m, 60)
    if h: return f"{h} h {m} m {s} s"
    if m: return f"{m} m {s} s"
    return f"{s} s"

def bar(name,phase,done,total,speed):
    pct = done/total*100 if total else 0
    filled = int(18*pct/100)
    bar_str = "●"*filled + "○"*(18-filled)
    eta = fmt_time((total-done)/speed if speed>0 else 0)
    return (f"**{phase}**\n\n"
            f"`{name}`\n[{bar_str}]\n"
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
async def log_doc(path, caption):
    try: return await bot.send_document(LOGS_CH, path, caption=caption)
    except: return None

# ---------- USERS ----------
async def ensure_user(uid):
    if not users.find_one({"_id": uid}):
        users.insert_one({"_id": uid, "ptype":"doc"})

# ---------- SETTINGS ----------
@bot.on_message(filters.command("settings"))
async def settings(_, m):
    await ensure_user(m.from_user.id)
    mode = users.find_one({"_id":m.from_user.id}).get("ptype","doc")
    kb = [[
        InlineKeyboardButton("📄 Upload as Document"+(" ✅" if mode=="doc" else ""), callback_data="m_doc"),
        InlineKeyboardButton("🎥 Upload as Video"+(" ✅" if mode=="vid" else ""), callback_data="m_vid")
    ]]
    await m.reply_text("⚙️ Choose your upload mode:", reply_markup=InlineKeyboardMarkup(kb))

@bot.on_callback_query(filters.regex("^m_"))
async def mode_cb(_, q):
    mode = "vid" if q.data.endswith("vid") else "doc"
    users.update_one({"_id": q.from_user.id}, {"$set": {"ptype": mode}}, upsert=True)
    await q.answer("✅ Updated!")
    await settings(_, q.message)

# ---------- CANCEL ----------
cancel_flags = {}
@bot.on_message(filters.command("cancel"))
async def cancel(_, m):
    cancel_flags[m.from_user.id] = True
    await m.reply_text("🛑 Cancelling current task …")

# ---------- FILE SEARCH ----------
@bot.on_message(filters.command("file"))
async def file_search(_, m):
    if len(m.command) < 2:
        return await m.reply_text("Usage: /file <keyword>")
    key = m.text.split(" ", 1)[1]
    data = list(files.find({"name":{"$regex":key,"$options":"i"}}))
    if not data: return await m.reply_text("❌ No match found.")
    await m.reply_text(f"📂 Found {len(data)} file(s); sending …")
    for f in data:
        await bot.send_document(m.chat.id, f["file_id"], caption=f["name"],
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("💬 Contact Owner",
                url="https://t.me/technicalserena")]]))
        await asyncio.sleep(1)

# ---------- LINK DETECTOR ----------
@bot.on_message(filters.text & filters.incoming)
async def link_detect(_, m):
    if not m.text: return
    for url in m.text.split():
        if url.startswith("http"):
            asyncio.create_task(download_and_send(m, url))

# ---------- M3U8 & INSTAGRAM ----------
async def m3u8_to_mp4(url, out):
    cmd = f'ffmpeg -y -i "{url}" -c copy "{out}"'
    proc = await asyncio.create_subprocess_shell(cmd,
              stdout=asyncio.subprocess.DEVNULL,
              stderr=asyncio.subprocess.DEVNULL)
    await proc.communicate()
    return os.path.exists(out)

async def insta_download(url, out):
    try:
        import instaloader, re
        L = instaloader.Instaloader(save_metadata=False, download_comments=False, compress_json=False)
        if INSTA_SESSION:
            L.load_session_from_file("", INSTA_SESSION)
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
        print("Instagram error:", e)
        return False

# ---------- MAIN DOWNLOADER ----------
async def download_and_send(m, url):
    uid = m.from_user.id if m.from_user else 0
    await ensure_user(uid)
    pref = users.find_one({"_id":uid}).get("ptype","doc")
    tmp = tempfile.gettempdir()
    filename = "file.bin"
    msg = await m.reply_text("📥 Starting download …")
    try:
        # --- Detect type ---
        if ".m3u8" in url:
            filename = "video.mp4"
            path = os.path.join(tmp, filename)
            await msg.edit_text("🎞️ Fetching M3U8 stream …")
            ok = await m3u8_to_mp4(url, path)
            if not ok: return await msg.edit_text("⚠️ Failed to fetch M3U8 stream !")
        elif "instagram.com" in url:
            filename = "insta.mp4"
            path = os.path.join(tmp, filename)
            await msg.edit_text("📸 Fetching Instagram video …")
            ok = await insta_download(url, path)
            if not ok: return await msg.edit_text("⚠️ Unable to download Instagram link.")
        else:
            async with aiohttp.ClientSession() as s:
                async with s.get(url, allow_redirects=True) as r:
                    total = int(r.headers.get("Content-Length", 0))
                    cd = r.headers.get("Content-Disposition")
                    if cd and "filename=" in cd:
                        filename = cd.split("filename=")[-1].strip('\"; ')
                    else:
                        ct = r.headers.get("Content-Type","")
                        ext = mimetypes.guess_extension(ct.split(";")[0].strip()) or ".bin"
                        base = os.path.basename(url.split("?")[0]) or "file"
                        filename = base if "." in base else base+ext
                    path = os.path.join(tmp, filename)
                    start, done, last = time.time(), 0, 0
                    with open(path, "wb") as f:
                        async for chunk in r.content.iter_chunked(1024*512):
                            if cancel_flags.get(uid):
                                await msg.edit_text("🛑 Cancelled by user 💔")
                                return
                            f.write(chunk); done += len(chunk)
                            now=time.time()
                            if now-last>10:
                                spd=done/max(now-start,1)
                                try: await msg.edit_text(bar(filename,"⬇️ Downloading",done,total,spd))
                                except FloodWait as e: await asyncio.sleep(e.value)
                                except: pass
                                last=now
        # --- Upload to logs ---
        await msg.edit_text("📦 Uploading backup to Logs …")
        logm = await log_doc(path, f"📦 Backup:{filename}")
        if not logm: return await msg.edit_text("⚠️ Backup failed.")
        # --- Send to chat ---
        await msg.edit_text("📤 Uploading to chat …")
        if pref == "vid":
            await bot.send_video(m.chat.id, path, caption=f"`{filename}`",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("💬 Contact Owner",
                    url="https://t.me/technicalserena")]]))
        else:
            await bot.send_document(m.chat.id, path, caption=f"`{filename}`",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("💬 Contact Owner",
                    url="https://t.me/technicalserena")]]))
        files.insert_one({"name":filename,"file_id":logm.document.file_id,"type":pref})
        await msg.delete()
        await log_msg(f"✅ Delivered {filename} to {m.chat.id}")
    except Exception as e:
        await msg.edit_text(f"❌ Error: {e}")
        await log_msg(str(e))
    finally:
        try: os.remove(os.path.join(tmp, filename))
        except: pass
        cancel_flags[uid]=False

# ---------- RUN ----------
if __name__ == "__main__":
    print("💠 SERENA starting — Flask + Pyrogram running together 💞")
    threading.Thread(target=run_web, daemon=True).start()
    bot.run()
