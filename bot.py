import os, sys, threading, asyncio, aiohttp, time, mimetypes, tempfile, subprocess, psutil, itertools
from flask import Flask
from pyrogram import Client, filters
from pyrogram.errors import FloodWait, UserNotParticipant
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from pymongo import MongoClient

# Flush Render logs immediately
sys.stdout.reconfigure(line_buffering=True)
sys.stderr.reconfigure(line_buffering=True)

# ---------- CONFIG ----------
API_ID=int(os.getenv("API_ID"))
API_HASH=os.getenv("API_HASH")
BOT_TOKEN=os.getenv("BOT_TOKEN")
MONGO_URL=os.getenv("MONGO_URL")
OWNER_ID=1598576202
LOGS_CHANNEL=-1003286415377
FORCE_CH="serenaunzipbot"
FORCE_LINK="https://t.me/serenaunzipbot"
INSTA_SESSION=os.getenv("INSTA_SESSION","")
INSTA_COOKIES=os.getenv("INSTA_COOKIES","")

# ---------- DATABASE ----------
mongo=MongoClient(MONGO_URL)
db=mongo["serena"]
users=db["users"]
files=db["files"]

# ---------- BOT + FLASK ----------
bot=Client("SERENA",api_id=API_ID,api_hash=API_HASH,bot_token=BOT_TOKEN)
web=Flask(__name__)
@web.route("/",methods=["GET","HEAD"])
def home(): return "💠 SERENA alive"
def run_web(): web.run(host="0.0.0.0",port=int(os.environ.get("PORT",10000)),threaded=True)

# ---------- HELPERS ----------
def fmt_size(n):
    for u in["B","KB","MB","GB","TB"]:
        if n<1024:return f"{n:.2f}{u}"
        n/=1024
    return f"{n:.2f}PB"

def fmt_time(sec):
    if sec<=0:return "<1 s"
    m,s=divmod(int(sec),60);h,m=divmod(m,60)
    if h:return f"{h} h {m} m {s} s"
    if m:return f"{m} m {s} s"
    return f"{s} s"

emoji_cycle=itertools.cycle(["😉","😎","🤗","🥰","🤓","😜","🤩"])
def fancy_bar(name,phase,done,total,speed):
    pct=done/total*100 if total else 0
    filled=int(18*pct/100)
    bar_str="●"*filled+"○"*(18-filled)
    face=next(emoji_cycle)
    eta=fmt_time((total-done)/speed if speed>0 else 0)
    return(
        f"**{phase}**\n"
        f"**{name}**\n"
        f"to my server\n"
        f"[{bar_str}]\n"
        f"◌Progress{face}:〘 {pct:.2f}% 〙\n"
        f"Done: 〘{fmt_size(done)} of {fmt_size(total)}〙\n"
        f"◌Speed🚀: 〘{fmt_size(speed)}/s〙\n"
        f"◌Time Left⏳: 〘{eta}〙"
    )

async def ensure_user(uid):
    if not users.find_one({"_id":uid}):
        users.insert_one({"_id":uid,"opt":"video"})

async def log_msg(t):
    try: await bot.send_message(LOGS_CHANNEL,t)
    except: pass
async def log_file(path,cap):
    try: return await bot.send_document(LOGS_CHANNEL,path,caption=cap)
    except: return None

# ---------- /START ----------
@bot.on_message(filters.command("start"))
async def start(_,m):
    await ensure_user(m.from_user.id)
    kb=InlineKeyboardMarkup([
        [InlineKeyboardButton("📢 Join Channel",url=FORCE_LINK)],
        [InlineKeyboardButton("💬 Contact Owner",url="https://t.me/technicalserena")]
    ])
    await m.reply_text(
        "💎 **SERENA Downloader** 💎\n\n"
        "Send any direct URL or .m3u8 / Instagram video and watch me work 💞",
        reply_markup=kb)

# ---------- /HELP ----------
@bot.on_message(filters.command("help"))
async def help_cmd(_,m):
    txt=(
        "🌸 **How to Use SERENA**\n\n"
        "🧿 Send any *direct URL* (mp4, zip, etc.) or `.m3u8` stream link.\n"
        "🎞 I’ll show an animated ETA progress bar while downloading.\n"
        "📦 File will be sent to you and saved in my Logs channel.\n\n"
        "⚙️ Commands:\n"
        "`/start` – welcome menu\n"
        "`/help` – this guide\n"
        "`/settings` – choose upload mode\n"
        "`/status` – owner system stats\n"
        "`/file <name>` – search saved files\n"
        "`/cancel` – stop current task"
    )
    await m.reply_text(txt)

# ---------- /SETTINGS ----------
@bot.on_message(filters.command("settings"))
async def settings(_,m):
    await ensure_user(m.from_user.id)
    opt=users.find_one({"_id":m.from_user.id}).get("opt","video")
    desc=("⚙️ **SERENA Settings**\n\n"
          "Decide how I send your files:\n"
          "🎥 *Upload as Video* – everything playable.\n"
          "📄 *Upload as Document* – original form.\n\n"
          "Tap one mode below to switch 💖")
    kb=[
        [InlineKeyboardButton("🎥 Upload as Video"+(" ✅" if opt=="video" else ""),callback_data="vid")],
        [InlineKeyboardButton("📄 Upload as Document"+(" ✅" if opt=="doc" else ""),callback_data="doc")]
    ]
    await m.reply_text(desc,reply_markup=InlineKeyboardMarkup(kb))

@bot.on_callback_query(filters.regex("^(vid|doc)$"))
async def cb_mode(_,q):
    val="video" if q.data=="vid" else "doc"
    users.update_one({"_id":q.from_user.id},{"$set":{"opt":val}},upsert=True)
    await q.answer("✅ Saved !")
    text=f"✨ Mode set to {'🎥 Video' if val=='video' else '📄 Document'}"
    await q.message.reply_text(text)

# ---------- /STATUS ----------
@bot.on_message(filters.command("status") & filters.user(OWNER_ID))
async def status_cmd(_,m):
    import time
    total=users.count_documents({})
    active=total
    blocked=0
    ram, cpu = psutil.virtual_memory().percent, psutil.cpu_percent()
    disk = psutil.disk_usage("/")
    free_mb = disk.free // (1024*1024)
    t0=time.time(); pong=await m.reply_text("⏳ Checking status …")
    latency=(time.time()-t0)*1000
    speed="10 MB/SEC"
    msg=(f"📊 **#STATUS**\n\n"
         f"👤 *Total Users:* {total}\n"
         f"🟢 *Active (3 days):* {active}\n"
         f"🚫 *Blocked:* {blocked}\n"
         f"🧠 *RAM:* {ram:.1f}%\n"
         f"🖥 *CPU:* {cpu:.1f}%\n"
         f"💾 *Storage Free:* {free_mb} MB\n"
         f"⏳ *Ping:* {int(latency)} ms 😚\n"
         f"🤗 *SPEED:* {speed}")
    await pong.edit_text(msg,parse_mode="Markdown")

# ---------- /FILE SEARCH ----------
@bot.on_message(filters.command("file"))
async def file_cmd(_,m):
    if len(m.command)<2:
        return await m.reply_text("Usage: /file <keyword>")
    key=m.text.split(" ",1)[1]
    fs=list(files.find({"name":{"$regex":key,"$options":"i"}}))
    if not fs:return await m.reply_text("❌ No matches found.")
    await m.reply_text(f"📂 Found {len(fs)} result(s); sending …")
    for f in fs:
        try:
            if f.get("type")=="video":
                await bot.send_video(m.chat.id,f["file_id"],caption=f["name"])
            else:
                await bot.send_document(m.chat.id,f["file_id"],caption=f["name"])
            await asyncio.sleep(1)
        except Exception as e:
            await m.reply_text(f"⚠️ Send failed for {f.get('name')}: {e}")

# ---------- /CANCEL ----------
cancel={}
@bot.on_message(filters.command("cancel"))
async def cancel_cmd(_,m):
    cancel[m.from_user.id]=True
    await m.reply_text("🛑 Cancelling current task…")

# ---------- SPECIAL DOWNLOADERS ----------
async def m3u8_to_mp4(url,out):
    cmd=f'ffmpeg -y -i "{url}" -c copy "{out}"'
    p=await asyncio.create_subprocess_shell(cmd,stdout=asyncio.subprocess.DEVNULL,stderr=asyncio.subprocess.DEVNULL)
    await p.communicate(); return os.path.exists(out)

async def insta_dl(url,out):
    try:
        import instaloader,re
        L=instaloader.Instaloader(save_metadata=False)
        if INSTA_SESSION:L.load_session_from_file("",INSTA_SESSION)
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
        print("insta err:",e); return False

# ---------- MAIN DOWNLOADER ----------
async def process(url,m):
    uid=m.from_user.id
    mode=users.find_one({"_id":uid}).get("opt","video")
    tmp=tempfile.gettempdir()
    name="file.bin";path=os.path.join(tmp,name)
    msg=await m.reply_text("📥 Starting download …")
    try:
        if ".m3u8" in url:
            name="video.mp4";path=os.path.join(tmp,name)
            await msg.edit_text("🎞️ **Fetching M3U8 stream …**")
            ok=await m3u8_to_mp4(url,path)
            if not ok:return await msg.edit_text("⚠️ Failed to fetch stream !")
        elif "instagram.com" in url:
            name="insta.mp4";path=os.path.join(tmp,name)
            await msg.edit_text("📸 **Fetching Instagram video …**")
            ok=await insta_dl(url,path)
            if not ok:return await msg.edit_text("⚠️ Cannot download Instagram video.")
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
                            if cancel.get(uid): await msg.edit_text("🛑 Cancelled by user"); return
                            f.write(chunk); done+=len(chunk)
                            now=time.time()
                            if now-last>10:
                                spd=done/max(now-start,1)
                                try: await msg.edit_text(fancy_bar(name,"⬇️ Downloading",done,total,spd))
                                except FloodWait as e: await asyncio.sleep(e.value)
                                except: pass
                                last=now
        await msg.edit_text("📦 **Uploading backup to Logs …**")
        logm=await log_file(path,f"📦 Backup:{name}")
        await msg.edit_text("📤 **Uploading to you …**")
        # send according to chosen mode
        if mode=="video":
            await bot.send_video(uid,path,caption=f"`{name}`",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("💬 Owner",url="https://t.me/technicalserena")]]))
        else:
            await bot.send_document(uid,path,caption=f"`{name}`",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("💬 Owner",url="https://t.me/technicalserena")]]))
        files.insert_one({"name":name,"file_id":logm.document.file_id,"type":mode})
        await msg.delete()
        await log_msg(f"✅ Delivered {name} to {uid}")
    except Exception as e:
        await msg.edit_text(f"❌ Error {e}")
        await log_msg(str(e))
    finally:
        try: os.remove(path)
        except: pass
        cancel[uid]=False

# ---------- LINK DETECTOR ----------
@bot.on_message(filters.text & ~filters.command(
    ["start","help","status","file","settings","cancel"]))
async def detect(_,m):
    txt=m.text.strip()
    for url in txt.split():
        if url.startswith("http"):
            await process(url,m); return
    await m.reply_text("😅 That doesn’t look like a link .")

# ---------- RUN ----------
if __name__=="__main__":
    print("🚀 SERENA booting — Flask thread + polling starting now")
    threading.Thread(target=run_web,daemon=True).start()
    bot.run()
