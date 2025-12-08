import os, sys, threading, asyncio, aiohttp, time, mimetypes, tempfile, subprocess, psutil, itertools
from flask import Flask
from pyrogram import Client, filters
from pyrogram.errors import FloodWait, UserNotParticipant, UserIsBlocked
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from pymongo import MongoClient

# ---- instant Render log flush ----
sys.stdout.reconfigure(line_buffering=True)
sys.stderr.reconfigure(line_buffering=True)

# ---- CONFIG ----
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

# ---- DATABASE ----
mongo=MongoClient(MONGO_URL)
db=mongo["serena"]
users=db["users"]
files=db["files"]

# ---- BOT + FLASK ----
bot=Client("SERENA", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)
web=Flask(__name__)
@web.route("/",methods=["GET","HEAD"])
def home(): return "💠 SERENA alive"
def run_web(): web.run(host="0.0.0.0",port=int(os.environ.get("PORT",10000)),threaded=True)

# ---- HELPERS ----
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
    bar="●"*filled+"○"*(18-filled)
    face=next(emoji_cycle)
    eta=fmt_time((total-done)/speed if speed>0 else 0)
    return(
        f"**{phase}**\n"
        f"**{name}**\n"
        f"[{bar}]\n"
        f"◌Progress{face}:〘 {pct:.2f}% 〙\n"
        f"Done: 〘{fmt_size(done)} of {fmt_size(total)}〙\n"
        f"◌Speed🚀: 〘{fmt_size(speed)}/s〙\n"
        f"◌Time Left⏳: 〘{eta}〙"
    )

async def ensure_user(uid):
    if not users.find_one({"_id":uid}):
        users.insert_one({"_id":uid,"opt":"video","caption":""})

async def log_msg(t):
    try: await bot.send_message(LOGS_CHANNEL,t)
    except: pass
async def log_file(path,cap):
    try: return await bot.send_document(LOGS_CHANNEL,path,caption=cap)
    except: return None

# ---- /START ----
@bot.on_message(filters.command("start"))
async def start(_,m):
    await ensure_user(m.from_user.id)
    kb=InlineKeyboardMarkup([
        [InlineKeyboardButton("📢 Join Update Channel",url=FORCE_LINK)],
        [InlineKeyboardButton("💬 Contact Owner",url="https://t.me/technicalserena")]
    ])
    txt=("🌷 **Welcome to SERENA Downloader!**\n\n"
         "✨ Send any direct file link or an `.m3u8` stream link — I'll grab it for you and show you my lovely ETA progress bar.\n"
         "📦 Each file is also safely stored in my Logs.\n\n"
         "🧭 Type `/help` for command list 💖")
    await m.reply_text(txt,reply_markup=kb)

# ---- /HELP ----
@bot.on_message(filters.command("help"))
async def help_cmd(_,m):
    txt=("🌸 **How to Use SERENA**\n\n"
         "🧿 Send *direct URL* (mp4/zip/etc.) or `.m3u8` stream link.\n"
         "🎞 Watch animated ETA progress bar.\n"
         "📦 Files are sent to you + saved in Logs.\n\n"
         "⚙️ Commands:\n"
         "`/start` – welcome menu\n"
         "`/help` – this guide\n"
         "`/settings` – upload & caption mode\n"
         "`/file <word>` – search saved files\n"
         "`/status` – owner system stats\n"
         "`/database` – Mongo usage (Owner)\n"
         "`/clear` – reset database (Owner)\n"
         "`/broadcast <text>` – owner mass message\n"
         "`/cancel` – stop current task")
    await m.reply_text(txt)

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

# caption input catcher
@bot.on_message(filters.private & filters.text)
async def get_user_caption(_,m):
    u=users.find_one({"_id":m.from_user.id})
    if u and u.get("waiting_cap"):
        users.update_one({"_id":m.from_user.id},{"$set":{"caption":m.text,"waiting_cap":False}})
        await m.reply_text(f"✅ Caption saved → `{m.text}`",parse_mode="markdown")
        return
    # fall through to detector below
    await detect(_,m)

# ---- /STATUS ----
@bot.on_message(filters.command("status") & filters.user(OWNER_ID))
async def status_cmd(_,m):
    total=users.count_documents({})
    active=total; blocked=0
    ram=psutil.virtual_memory().percent
    cpu=psutil.cpu_percent()
    disk=psutil.disk_usage('/')
    free_mb=disk.free//(1024*1024)
    ping_start=time.time(); await bot.send_chat_action(m.chat.id,"typing")
    latency=(time.time()-ping_start)*1000
    speed="10 MB/SEC"
    text=(f"📊 **#STATUS**\n\n"
          f"👤 *Total Users:* {total}\n"
          f"🟢 *Active (3 days):* {active}\n"
          f"🚫 *Blocked:* {blocked}\n"
          f"🧠 *RAM:* {ram:.1f}%\n"
          f"🖥 *CPU:* {cpu:.1f}%\n"
          f"💾 *Storage Free:* {free_mb} MB\n"
          f"⏳ *Ping:* {int(latency)} ms 😚\n"
          f"🤗 *SPEED:* {speed}")
    await m.reply_text(text,parse_mode="markdown")

# ---- /DATABASE ----
@bot.on_message(filters.command("database") & filters.user(OWNER_ID))
async def db_status(_,m):
    stats=db.command("dbstats")
    used=round(stats["fsUsedSize"]/(1024*1024),2)
    total=round(stats["fileSize"]/(1024*1024),2)
    free=round(total-used,2)
    await m.reply_text(
        f"🗄 **Mongo DB Usage**\n\n"
        f"📦 Used : {used} MB\n💾 Free : {free} MB\n🧮 Total File : {total} MB",
        parse_mode="markdown")

# ---- /CLEAR ----
@bot.on_message(filters.command("clear") & filters.user(OWNER_ID))
async def clear_db(_,m):
    files.drop()
    users.drop()
    await m.reply_text("🧹 All MongoDB collections cleared successfully !")

# ---- /BROADCAST ----
@bot.on_message(filters.command("broadcast") & filters.user(OWNER_ID))
async def broadcast(_,m):
    if len(m.command)<2:
        return await m.reply_text("Usage: `/broadcast <message>`",parse_mode="markdown")
    text=m.text.split(" ",1)[1]
    sent=fail=0
    await m.reply_text("📣 Broadcast started …")
    for u in users.find({}):
        try:
            await bot.send_message(u["_id"],text);sent+=1
        except UserIsBlocked: fail+=1
        except Exception: fail+=1
        await asyncio.sleep(0.05)
    rep=f"✅ Broadcast done\n✨ Sent: {sent}\n🚫 Failed: {fail}"
    await m.reply_text(rep)
    await log_msg(rep)

# ---- /FILE ----
@bot.on_message(filters.command("file"))
async def file_cmd(_,m):
    if len(m.command)<2:return await m.reply_text("Usage: /file <keyword>")
    key=m.text.split(" ",1)[1]
    fs=list(files.find({"name":{"$regex":key,"$options":"i"}}))
    if not fs:return await m.reply_text("❌ No matches found.")
    await m.reply_text(f"📂 Found {len(fs)} file(s), sending …")
    for f in fs:
        fid=f["file_id"]
        try:
            await bot.send_video(m.chat.id,fid,caption=f["name"])
        except Exception:
            await bot.send_document(m.chat.id,fid,caption=f["name"])
        await asyncio.sleep(1)

# ---- /CANCEL ----
cancel={}
@bot.on_message(filters.command("cancel"))
async def cancel_cmd(_,m):
    cancel[m.from_user.id]=True
    await m.reply_text("🛑 Cancelling current task…")

# ---- DOWNLOADER HELPERS ----
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

# ---- MAIN DOWNLOADER ----
async def process(url,m):
    uid=m.from_user.id
    data=users.find_one({"_id":uid}) or {}
    mode=data.get("opt","video")
    caption=data.get("caption","")
    tmp=tempfile.gettempdir()
    name="file.bin";path=os.path.join(tmp,name)
    msg=await m.reply_text("📥 Starting download …")
    try:
        if ".m3u8" in url:
            name="video.mp4";path=os.path.join(tmp,name)
            await msg.edit_text("🎞️ **Fetching M3U8 stream …**")
            ok=await m3u8_to_mp4(url,path)
            if not ok:return await msg.edit_text("⚠️ Failed to fetch stream!")
        elif "instagram.com" in url:
            name="insta.mp4";path=os.path.join(tmp,name)
            await msg.edit_text("📸 **Fetching Instagram video…**")
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
                            if cancel.get(uid):await msg.edit_text("🛑 Cancelled by user");return
                            f.write(chunk);done+=len(chunk)
                            now=time.time()
                            if now-last>10:
                                spd=done/max(now-start,1)
                                try:await msg.edit_text(fancy_bar(name,"⬇️ Downloading",done,total,spd))
                                except FloodWait as e:await asyncio.sleep(e.value)
                                except:pass
                                last=now
        await msg.edit_text("📦 **Uploading backup to Logs…**")
        caption_final = (caption + "\n" if caption else "") + f"`{name}`"
        logm=await log_file(path,f"📦 Backup:{name}\n\n{caption_final}")
        await msg.edit_text("📤 **Uploading to you…**")
        if mode=="video":
            await bot.send_video(uid,path,caption=caption_final,
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("💬 Owner",url="https://t.me/technicalserena")]]))
        else:
            await bot.send_document(uid,path,caption=caption_final,
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("💬 Owner",url="https://t.me/technicalserena")]]))
        files.insert_one({"name":name,"file_id":logm.document.file_id,"type":mode,"caption":caption})
        await msg.delete()
        await log_msg(f"✅ Delivered {name} to {uid}")
    except Exception as e:
        await msg.edit_text(f"❌ Error {e}")
        await log_msg(str(e))
    finally:
        try: os.remove(path)
        except: pass
        cancel[uid]=False

# ---- DETECTOR (wrong-link guide) ----
@bot.on_message(filters.text & ~filters.command(
    ["start","help","status","file","settings","clear","database","broadcast","cancel"]))
async def detect(_,m):
    txt=m.text.strip()
    for url in txt.split():
        if url.startswith("http"):
            await process(url,m); return
    example=("😅 That doesn’t look like a valid link or command.\n\n"
             "👉 *Example:*\n`https://example.com/video.mp4`\n\n"
             "Use `/help` for instructions 🌸")
    await m.reply_text(example,parse_mode="markdown")

# ---- RUN ----
if __name__=="__main__":
    print("🚀 SERENA booting — Flask thread + polling starting now")
    threading.Thread(target=run_web,daemon=True).start()
    bot.run()
