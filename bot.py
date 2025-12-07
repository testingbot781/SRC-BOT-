import os, aiohttp, asyncio, time, psutil, shutil, threading, mimetypes
from datetime import datetime
from pyrogram import Client, filters, enums
from pyrogram.errors import FloodWait, UserNotParticipant, UserIsBlocked
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from flask import Flask, request
from pymongo import MongoClient

# === CONFIG ===
API_ID = int(os.getenv("API_ID"))
API_HASH = os.getenv("API_HASH")
BOT_TOKEN = os.getenv("BOT_TOKEN")
MONGO_URL = os.getenv("MONGO_URL")

OWNER_ID = 1598576202
LOGS_CHANNEL = -1003286415377
FORCE_SUB_CHANNEL = "serenaunzipbot"
FORCE_SUB_LINK = "https://t.me/serenaunzipbot"

# === DB ===
mongo = MongoClient(MONGO_URL)
db = mongo["serena_bot"]
users = db["users"]

# === BOT ===
bot = Client("serena_downloader", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

# === RENDER FIX SERVER ===
flask_app = Flask(__name__)

@flask_app.route("/", methods=["GET","POST"])
def home():
    # also handle POST to stop 405
    return "SERENA up 💖"

def web_keepalive():
    port = int(os.environ.get("PORT",8080))
    flask_app.run(host="0.0.0.0", port=port)

threading.Thread(target=web_keepalive).start()

# === UTILITIES ===
def fmt_size(sz):
    for u in ["B","KB","MB","GB","TB"]:
        if sz < 1024: return f"{sz:.2f} {u}"
        sz /= 1024
    return f"{sz:.2f} PB"

def time_fmt(sec):
    m,s=divmod(int(sec),60);h,m=divmod(m,60)
    if h: return f"{h}h {m}m {s}s"
    if m: return f"{m}m {s}s"
    return f"{s}s"

def progress_frame(name, done, total, speed, eta):
    pct = done/total*100 if total else 0
    filled = int(18*pct/100)
    bar = "●"*filled + "○"*(18-filled)
    return (f"Downloading\n{name}\nto my server\n"
            f"[{bar}]\n"
            f"◌Progress😉:〘 {pct:.2f}% 〙\n"
            f"Done:〘{fmt_size(done)} of {fmt_size(total)}〙\n"
            f"◌Speed🚀:〘{fmt_size(speed)}/s〙\n"
            f"◌Time Left⏳:〘{eta}〙")

async def ensure_user(uid):
    if not users.find_one({"_id":uid}):
        users.insert_one({"_id":uid,"joined":datetime.utcnow(),"blocked":False})

async def log_msg(text):
    try: await bot.send_message(LOGS_CHANNEL,text[:4096])
    except: pass
async def log_doc(path,cap):
    try: await bot.send_document(LOGS_CHANNEL,path,caption=cap)
    except: pass

async def force_join_ok(uid):
    try:
        await bot.get_chat_member(FORCE_SUB_CHANNEL, uid)
        return True
    except UserNotParticipant:
        return False
    except Exception:
        return False

# === COMMANDS ===
@bot.on_message(filters.command("start"))
async def start(_,m):
    await ensure_user(m.from_user.id)
    if not await force_join_ok(m.from_user.id):
        kb=InlineKeyboardMarkup([[InlineKeyboardButton("📢 Join Channel",url=FORCE_SUB_LINK)]])
        return await m.reply_text("Please join update channel first 💞",reply_markup=kb)
    txt=("**💎 SERENA — Direct URL Downloader 💎**\n\n"
         "Send any direct download link and I'll fetch + DM the file to you 🌸")
    kb=InlineKeyboardMarkup([
        [InlineKeyboardButton("📢 Join Channel",url=FORCE_SUB_LINK),
         InlineKeyboardButton("💬 Contact Owner",url="https://t.me/technicalserena")]
    ])
    await m.reply_text(txt,reply_markup=kb,disable_web_page_preview=True)

@bot.on_message(filters.command("help"))
async def help(_,m):
    if not await force_join_ok(m.from_user.id):
        kb=InlineKeyboardMarkup([[InlineKeyboardButton("📢 Join Channel",url=FORCE_SUB_LINK)]])
        return await m.reply_text("Please join update channel first ✨",reply_markup=kb)
    txt=("🌈 **How to use SERENA:**\n\n"
         "1️⃣ Copy any *direct* downloadable link (e.g. https://example.com/video.mp4)\n"
         "2️⃣ Paste it here 💫\n"
         "3️⃣ Watch me work with animated ETA bar ⏳\n"
         "4️⃣ I DM the finished file to you 📥\n\n"
         "⚙️ Commands:\n"
         "`/help` — show info\n"
         "`/status` — owner only\n"
         "`/broadcast <msg>` — owner only")
    await m.reply_text(txt)

@bot.on_message(filters.command("status") & filters.user(OWNER_ID))
async def status(_,m):
    total=users.count_documents({})
    blocked=users.count_documents({"blocked":True})
    act=total-blocked
    cpu,mem,disk=psutil.cpu_percent(),psutil.virtual_memory().percent,psutil.disk_usage("/").percent
    await m.reply_text(f"**⚙️ System + User Stats**\n"
                       f"🧍‍♂️Total {total}\n🚷Blocked {blocked}\n🟢Active {act}\n\n"
                       f"CPU {cpu}% | RAM {mem}% | Disk {disk}%")

@bot.on_message(filters.command("broadcast") & filters.user(OWNER_ID))
async def bc(_,m):
    if len(m.command)<2: return await m.reply_text("Usage: `/broadcast <text>`")
    text=m.text.split(" ",1)[1]
    sent,fail=0,0
    await m.reply_text("📣 Broadcasting…")
    for u in users.find({}):
        try:
            await bot.send_message(u["_id"],text)
            sent+=1
        except UserIsBlocked:
            users.update_one({"_id":u["_id"]},{"$set":{"blocked":True}})
            fail+=1
        except: fail+=1
        await asyncio.sleep(0.03)
    await m.reply_text(f"✔️ Done Sent:{sent} Failed:{fail}")
    await log_msg(f"Broadcast → sent {sent} failed {fail}")

# === DOWNLOADER ===
@bot.on_message(filters.private & ~filters.command(["start","help","status","broadcast"]))
async def download_file(_,m):
    await ensure_user(m.from_user.id)
    if not await force_join_ok(m.from_user.id):
        kb=InlineKeyboardMarkup([[InlineKeyboardButton("📢 Join Channel",url=FORCE_SUB_LINK)]])
        return await m.reply_text("Join our update channel first 🌼",reply_markup=kb)

    url=m.text.strip()
    if not (url.startswith("http://") or url.startswith("https://")):
        return await m.reply_text("😅 That isn't a valid link.\nExample:\n`https://example.com/file.mp4`")

    tmp_name="file.bin"
    msg=await m.reply_text("📥 Starting download…")
    await log_msg(f"⬇️ Request from {m.from_user.mention} → {url}")

    t0=time.time(); dl=0; tot=0; last=0
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(url,allow_redirects=True) as r:
                tot=int(r.headers.get("Content-Length",0))
                # check header name
                cd=r.headers.get("Content-Disposition")
                if cd and "filename=" in cd:
                    tmp_name=cd.split("filename=")[-1].strip('"; ')
                else:
                    # guess from mime if possible
                    mt=r.headers.get("Content-Type","")
                    ext=mimetypes.guess_extension(mt.split(";")[0].strip()) or ""
                    base=os.path.basename(url.split("?")[0]) or "file"
                    tmp_name=base if "." in base else base+ext
                with open(tmp_name,"wb") as f:
                    async for chunk in r.content.iter_chunked(1024*512):
                        if not chunk: break
                        f.write(chunk); dl+=len(chunk)
                        now=time.time(); diff=now-t0
                        spd=dl/diff if diff else 0
                        remain=(tot-dl)/spd if spd>0 else 0
                        if now-last>2:
                            frame=progress_frame(tmp_name,dl,tot,spd,time_fmt(remain))
                            try: await msg.edit_text(frame)
                            except FloodWait as e: await asyncio.sleep(e.value)
                            last=now
        await msg.edit_text("✅ Download complete — uploading…")
        await bot.send_document(m.chat.id,tmp_name,caption=f"`{tmp_name}`")
        await log_doc(tmp_name,f"📤 {tmp_name} sent to {m.from_user.mention}")
        os.remove(tmp_name)
        await msg.delete()
    except Exception as e:
        await log_msg(f"❌ Error {e}")
        await m.reply_text(f"Error while downloading:\n`{e}`")

print("💠 SERENA up and ready for Render deployment")
bot.run()
