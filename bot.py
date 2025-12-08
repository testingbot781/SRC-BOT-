import os, sys, threading, asyncio, aiohttp, time, mimetypes, tempfile, subprocess, psutil, itertools
from flask import Flask
from pyrogram import Client, filters
from pyrogram.errors import FloodWait, UserNotParticipant, UserIsBlocked
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from pymongo import MongoClient

# ---- Render log flush ----
sys.stdout.reconfigure(line_buffering=True)
sys.stderr.reconfigure(line_buffering=True)

# ---- CONFIG ----
API_ID     = int(os.getenv("API_ID"))
API_HASH   = os.getenv("API_HASH")
BOT_TOKEN  = os.getenv("BOT_TOKEN")
MONGO_URL  = os.getenv("MONGO_URL")
OWNER_ID   = 1598576202
LOGS_CH    = -1003286415377
FORCE_CH   = "serenaunzipbot"
FORCE_LINK = "https://t.me/serenaunzipbot"
INSTA_SESSION = os.getenv("INSTA_SESSION","")
INSTA_COOKIES = os.getenv("INSTA_COOKIES","")

# ---- DATABASE ----
mongo = MongoClient(MONGO_URL)
db     = mongo["serena"]
users  = db["users"]
files  = db["files"]

# ---- BOT + FLASK ----
bot = Client("SERENA", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)
web = Flask(__name__)
@web.route("/",methods=["GET","HEAD"])
def home(): return "💠 SERENA alive"
def run_web(): web.run(host="0.0.0.0",port=int(os.environ.get("PORT",10000)),threaded=True)

# ---- HELPERS ----
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

emoji_cycle = itertools.cycle(["😉","😎","🤗","🥰","🤓","😜","🤩"])
def fancy_bar(name,phase,done,total,speed):
    pct=done/total*100 if total else 0
    filled=int(18*pct/100)
    bar="●"*filled+"○"*(18-filled)
    face=next(emoji_cycle)
    eta=fmt_time((total-done)/speed if speed>0 else 0)
    return(
        f"*{phase}*\n"
        f"*{name}*\n"
        f"[{bar}]\n"
        f"◌Progress{face}: {pct:.2f}%\n"
        f"✅ {fmt_size(done)} of {fmt_size(total)}\n"
        f"🚀 {fmt_size(speed)}/s  ⏳ {eta}"
    )

async def ensure_user(uid):
    if not users.find_one({"_id":uid}):
        users.insert_one({"_id":uid,"opt":"video","caption":""})

async def log_msg(t):
    try: await bot.send_message(LOGS_CH, t)
    except: pass
async def log_file(path,cap):
    try: return await bot.send_document(LOGS_CH, path, caption=cap)
    except: return None

# ---- START ----
@bot.on_message(filters.command("start"))
async def start(_,m):
    await ensure_user(m.from_user.id)
    kb=InlineKeyboardMarkup([
        [InlineKeyboardButton("📢 Join Update Channel",url=FORCE_LINK)],
        [InlineKeyboardButton("💬 Contact Owner",url="https://t.me/technicalserena")]
    ])
    txt=("🌷 *Welcome to SERENA Downloader!* 🌷\n\n"
         "✨ Paste any direct link or `.m3u8` stream URL — I'll fetch it for you.\n"
         "🎞 Watch my animated ETA bar while downloading!\n"
         "🧭 Use /help for commands 💖")
    await m.reply_text(txt, reply_markup=kb, parse_mode="markdownv2")

# ---- HELP ----
@bot.on_message(filters.command("help"))
async def help_cmd(_,m):
    txt=("🌸 *How to Use SERENA*\n\n"
         "🧿 Send a direct URL (mp4/zip) or `.m3u8` stream.\n"
         "🎞 Enjoy an animated ETA bar during download.\n"
         "📦 Your file is delivered and logged for backup.\n\n"
         "⚙️ Commands\n"
         "`/start` – welcome menu\n"
         "`/help` – guide\n"
         "`/settings` – upload + caption settings\n"
         "`/file <word>` – search files\n"
         "`/status` – owner system stats\n"
         "`/database` – Mongo usage (Owner)\n"
         "`/clear` – flush database (Owner)\n"
         "`/broadcast <text>` – owner mass message\n"
         "`/cancel` – stop current task")
    await m.reply_text(txt, parse_mode="markdownv2")

# ---- SETTINGS (mode + caption) ----
@bot.on_message(filters.command("settings"))
async def settings(_,m):
    await ensure_user(m.from_user.id)
    u=users.find_one({"_id":m.from_user.id})
    opt=u.get("opt","video"); cap=u.get("caption","")
    desc=("⚙️ *SERENA Settings*\n\n"
          "Choose upload mode and optionally set a caption 💖")
    kb=[
        [InlineKeyboardButton("🎥 Upload as Video"+(" ✅" if opt=="video" else ""),callback_data="vid")],
        [InlineKeyboardButton("📄 Upload as Document"+(" ✅" if opt=="doc" else ""),callback_data="doc")],
        [InlineKeyboardButton("➕ Add Caption",callback_data="add_cap"),
         InlineKeyboardButton("♻️ Reset Caption",callback_data="clr_cap")]
    ]
    msg=f"{desc}\n\n🖋 *Current Caption:* `{cap if cap else 'None'}`"
    await m.reply_text(msg, reply_markup=InlineKeyboardMarkup(kb), parse_mode="markdownv2")

@bot.on_callback_query()
async def settings_cb(_,q):
    uid=q.from_user.id
    data=q.data
    await ensure_user(uid)
    if data in ("vid","doc"):
        mode="video" if data=="vid" else "doc"
        users.update_one({"_id":uid},{"$set":{"opt":mode}})
        await q.answer("✅ Updated mode")
        await q.message.reply_text(f"✨ Mode set to {'🎥 Video' if mode=='video' else '📄 Document'}")
    elif data=="add_cap":
        users.update_one({"_id":uid},{"$set":{"waiting_cap":True}})
        await q.message.reply_text("🖋 Send me the new caption text (ex: `01\\. My Title`)", parse_mode="markdownv2")
    elif data=="clr_cap":
        users.update_one({"_id":uid},{"$set":{"caption":""}})
        await q.message.reply_text("♻️ Caption cleared.")
    await q.answer()

# ---- CAPTION INPUT ----
@bot.on_message(filters.private & filters.text)
async def get_caption(_,m):
    u=users.find_one({"_id":m.from_user.id})
    if u and u.get("waiting_cap"):
        users.update_one({"_id":m.from_user.id},{"$set":{"caption":m.text,"waiting_cap":False}})
        await m.reply_text(f"✅ Caption saved → `{m.text}`", parse_mode="markdownv2")
        return
    await detect(_,m)  # normal flow

# ---- STATUS ----
@bot.on_message(filters.command("status") & filters.user(OWNER_ID))
async def status_cmd(_,m):
    total=users.count_documents({})
    ram, cpu = psutil.virtual_memory().percent, psutil.cpu_percent()
    disk = psutil.disk_usage("/")
    free_mb = disk.free//(1024*1024)
    latency_start=time.time(); await bot.send_chat_action(m.chat.id,"typing")
    latency=(time.time()-latency_start)*1000
    msg=(f"📊 *#STATUS*\n\n"
         f"👥 *Users:* {total}\n"
         f"🧠 *RAM:* {ram:.1f}%\n"
         f"🖥 *CPU:* {cpu:.1f}%\n"
         f"💾 *Free:* {free_mb} MB\n"
         f"⏳ *Ping:* {int(latency)} ms")
    await m.reply_text(msg, parse_mode="markdownv2")

# ---- DATABASE INFO ----
@bot.on_message(filters.command("database") & filters.user(OWNER_ID))
async def database_info(_,m):
    stats=db.command("dbstats")
    used=round(stats["fsUsedSize"]/(1024*1024),2)
    total=round(stats["fileSize"]/(1024*1024),2)
    free=round(total-used,2)
    txt=(f"🗄 *Mongo DB Usage*\n\n📦 *Used:* {used} MB\n"
         f"💾 *Free:* {free} MB\n🧮 *Total:* {total} MB")
    await m.reply_text(txt, parse_mode="markdownv2")

# ---- CLEAR DB ----
@bot.on_message(filters.command("clear") & filters.user(OWNER_ID))
async def clear_db(_,m):
    users.drop(); files.drop()
    await m.reply_text("🧹 Database collections cleared successfully.")

# ---- BROADCAST ----
@bot.on_message(filters.command("broadcast") & filters.user(OWNER_ID))
async def broadcast(_,m):
    if len(m.command)<2:
        return await m.reply_text("Usage: `/broadcast <message>`", parse_mode="markdownv2")
    text=m.text.split(" ",1)[1]; sent=fail=0
    note=await m.reply_text("📣 Broadcast started …")
    for u in users.find({}):
        try:
            await bot.send_message(u["_id"],text);sent+=1
        except UserIsBlocked: fail+=1
        except Exception: fail+=1
        await asyncio.sleep(0.05)
    rep=f"✅ Broadcast done\n✨ Sent: {sent}\n🚫 Failed: {fail}"
    await note.edit_text(rep)

# ---- FILE SEARCH ----
@bot.on_message(filters.command("file"))
async def file_cmd(_,m):
    if len(m.command)<2: return await m.reply_text("Usage: /file <keyword>")
    key=m.text.split(" ",1)[1]
    fs=list(files.find({"name":{"$regex":key,"$options":"i"}}))
    if not fs: return await m.reply_text("❌ No matches found.")
    await m.reply_text(f"📂 Found {len(fs)} files, sending …")
    for f in fs:
        fid=f["file_id"]
        try:
            await bot.send_video(m.chat.id,fid,caption=f["name"])
        except Exception:
            await bot.send_document(m.chat.id,fid,caption=f["name"])
        await asyncio.sleep(1)

# ---- CANCEL ----
cancel={}
@bot.on_message(filters.command("cancel"))
async def cancel_cmd(_,m):
    cancel[m.from_user.id]=True
    await m.reply_text("🛑 Cancelling current task…")

# ---- DOWNLOAD UTILITIES ----
async def m3u8_to_mp4(url,out):
    cmd=f'ffmpeg -y -i "{url}" -c copy "{out}"'
    p=await asyncio.create_subprocess_shell(cmd,stdout=asyncio.subprocess.DEVNULL,stderr=asyncio.subprocess.DEVNULL)
    await p.communicate(); return os.path.exists(out)

async def insta_dl(url,out):
    try:
        import instaloader,re
        L=instaloader.Instaloader(save_metadata=False)
        if INSTA_SESSION: L.load_session_from_file("",INSTA_SESSION)
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
        print("insta_err:",e); return False

# ---- MAIN DOWNLOADER ----
async def process(url,m):
    uid=m.from_user.id
    data=users.find_one({"_id":uid}) or {}
    mode=data.get("opt","video"); caption=data.get("caption","")
    tmp=tempfile.gettempdir()
    name="file.bin"; path=os.path.join(tmp,name)
    msg=await m.reply_text("📥 Starting download …")
    try:
        if ".m3u8" in url:
            name="video.mp4"; path=os.path.join(tmp,name)
            await msg.edit_text("🎞️ **Fetching M3U8 stream …**")
            ok=await m3u8_to_mp4(url,path)
            if not ok: return await msg.edit_text("⚠️ Failed to fetch stream!")
        elif "instagram.com" in url:
            name="insta.mp4"; path=os.path.join(tmp,name)
            await msg.edit_text("📸 **Fetching Instagram video …**")
            ok=await insta_dl(url,path)
            if not ok: return await msg.edit_text("⚠️ Cannot download Instagram video.")
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
                            if cancel.get(uid):
                                await msg.edit_text("🛑 Cancelled by user"); return
                            f.write(chunk); done+=len(chunk)
                            now=time.time()
                            if now-last>10:
                                spd=done/max(now-start,1)
                                try: await msg.edit_text(fancy_bar(name,"⬇️ Downloading",done,total,spd), parse_mode="markdownv2")
                                except FloodWait as e: await asyncio.sleep(e.value)
                                except: pass
                                last=now
        await msg.edit_text("📦 **Uploading backup to Logs …**")
        caption_final=(caption+"\n" if caption else "")+f"`{name}`"
        logm=await log_file(path,f"📦 Backup:{name}\n\n{caption_final}")
        await msg.edit_text("📤 **Uploading to you …**")
        if mode=="video":
            await bot.send_video(uid,path,caption=caption_final)
        else:
            await bot.send_document(uid,path,caption=caption_final)
        files.insert_one({"name":name,"file_id":logm.document.file_id,"type":mode,"caption":caption})
        await msg.delete()
        await log_msg(f"✅ Delivered {name} to {uid}")
    except Exception as e:
        await msg.edit_text(f"❌ Error {e}")
        await log_msg(str(e))
    finally:
        try: os.remove(path)
        except: pass
        cancel[uid]=False

# ---- DETECTOR ----
@bot.on_message(filters.text & ~filters.command(
    ["start","help","status","file","settings","clear","database","broadcast","cancel"]))
async def detect(_,m):
    txt=m.text.strip()
    for url in txt.split():
        if url.startswith("http"):
            await process(url,m); return
    example=("❓ That doesn't look like a valid link or command.\n\n"
             "👉 *Example:* `https://example.com/video.mp4`\n\n"
             "Use */help* for instructions 🌸")
    await m.reply_text(example, parse_mode="markdownv2")

# ---- RUN ----
if __name__=="__main__":
    print("🚀 SERENA booting — Flask thread + polling starting now")
    threading.Thread(target=run_web,daemon=True).start()
    bot.run()
