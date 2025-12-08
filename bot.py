import os, sys, threading, asyncio, aiohttp, time, mimetypes, tempfile, subprocess, psutil, itertools
from flask import Flask
from pyrogram import Client, filters
from pyrogram.errors import FloodWait, UserNotParticipant, UserIsBlocked
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from pymongo import MongoClient

# ---- Flush Render logs instantly ----
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

emoji_cycle=itertools.cycle(["😉","😎","🤗","🥰","🤓","😜","🤩"])
def fancy_bar(name,phase,done,total,speed):
    pct=done/total*100 if total else 0
    filled=int(18*pct/100)
    bar="●"*filled+"○"*(18-filled)
    face=next(emoji_cycle)
    eta=fmt_time((total-done)/speed if speed>0 else 0)
    return(f"{phase}\n{name}\n[{bar}]\n"
           f"◌Progress{face}: {pct:.2f}%\n"
           f"Done: {fmt_size(done)} of {fmt_size(total)}\n"
           f"Speed🚀: {fmt_size(speed)}/s | ETA⏳: {eta}")

async def ensure_user(uid):
    if not users.find_one({"_id":uid}):
        users.insert_one({"_id":uid,"opt":"video","caption":""})

async def log_msg(t):
    try: await bot.send_message(LOGS_CH,t)
    except: pass
async def log_file(path,cap):
    try: return await bot.send_document(LOGS_CH,path,caption=cap)
    except: return None

# ---- START / HELP ----
@bot.on_message(filters.command("start"))
async def start(_,m):
    await ensure_user(m.from_user.id)
    kb=InlineKeyboardMarkup([
        [InlineKeyboardButton("📢 Join Channel",url=FORCE_LINK)],
        [InlineKeyboardButton("💬 Owner",url="https://t.me/technicalserena")]
    ])
    msg=("🌷 Welcome to SERENA Downloader 🌷\n\n"
         "Send a direct file or stream (.m3u8) link and watch magic happen!\n\n"
         "Use /help for full command list.")
    await m.reply_text(msg,reply_markup=kb)

@bot.on_message(filters.command("help"))
async def help_cmd(_,m):
    txt=("Commands\n"
         "/start - Welcome message\n"
         "/help - This text\n"
         "/settings - Upload & caption options\n"
         "/file <word> - Search DB\n"
         "/status - Owner system status\n"
         "/database - Mongo usage (owner)\n"
         "/clear - Reset DB (owner)\n"
         "/broadcast <text> - Message all users\n"
         "/cancel - Stop current task")
    await m.reply_text(txt)

# ---- SETTINGS (mode + caption) ----
@bot.on_message(filters.command("settings"))
async def settings(_,m):
    await ensure_user(m.from_user.id)
    u=users.find_one({"_id":m.from_user.id})
    opt=u.get("opt","video")
    cap=u.get("caption","")
    desc="Settings:\n\nSelect upload type and add optional caption."
    kb=[
        [InlineKeyboardButton("🎥 Upload as Video"+(" ✅" if opt=="video" else ""),callback_data="vid")],
        [InlineKeyboardButton("📄 Upload as Document"+(" ✅" if opt=="doc" else ""),callback_data="doc")],
        [InlineKeyboardButton("➕ Add Caption",callback_data="add_cap"),
         InlineKeyboardButton("♻️ Reset Caption",callback_data="clr_cap")]
    ]
    msg=f"{desc}\n\nCurrent caption: {cap if cap else 'None'}"
    await m.reply_text(msg,reply_markup=InlineKeyboardMarkup(kb))

@bot.on_callback_query()
async def settings_cb(_,q):
    uid=q.from_user.id; data=q.data
    await ensure_user(uid)
    if data in ("vid","doc"):
        mode="video" if data=="vid" else "doc"
        users.update_one({"_id":uid},{"$set":{"opt":mode}})
        await q.message.reply_text(f"Mode set to {mode.upper()}")
    elif data=="add_cap":
        users.update_one({"_id":uid},{"$set":{"waiting_cap":True}})
        await q.message.reply_text("Send the new caption now.")
    elif data=="clr_cap":
        users.update_one({"_id":uid},{"$set":{"caption":""}})
        await q.message.reply_text("Caption cleared.")
    await q.answer()

@bot.on_message(filters.private & filters.text)
async def caption_or_link(_,m):
    u=users.find_one({"_id":m.from_user.id})
    if u and u.get("waiting_cap"):
        users.update_one({"_id":m.from_user.id},{"$set":{"caption":m.text,"waiting_cap":False}})
        return await m.reply_text(f"Caption set → {m.text}")
    await detect(_,m)

# ---- STATUS ----
@bot.on_message(filters.command("status") & filters.user(OWNER_ID))
async def status_cmd(_,m):
    total=users.count_documents({})
    ram=psutil.virtual_memory().percent
    cpu=psutil.cpu_percent()
    disk=psutil.disk_usage("/")
    free=disk.free//(1024*1024)
    latency_start=time.time()
    await bot.send_chat_action(m.chat.id,"typing")
    latency=(time.time()-latency_start)*1000
    txt=(f"Status:\nUsers: {total}\nCPU: {cpu:.1f}% | RAM: {ram:.1f}%\n"
         f"Free: {free} MB | Ping: {int(latency)} ms")
    await m.reply_text(txt)

# ---- DATABASE & CLEAR ----
@bot.on_message(filters.command("database") & filters.user(OWNER_ID))
async def database_info(_,m):
    stats=db.command("dbstats")
    used=round(stats["fsUsedSize"]/(1024*1024),2)
    total=round(stats["fileSize"]/(1024*1024),2)
    free=round(total-used,2)
    await m.reply_text(f"Mongo usage:\nUsed: {used} MB\nFree: {free} MB\nTotal: {total} MB")

@bot.on_message(filters.command("clear") & filters.user(OWNER_ID))
async def clear_db(_,m):
    users.drop(); files.drop()
    await m.reply_text("Database cleared.")

# ---- BROADCAST ----
@bot.on_message(filters.command("broadcast") & filters.user(OWNER_ID))
async def broadcast(_,m):
    if len(m.command)<2:return await m.reply_text("Usage: /broadcast <message>")
    msg=m.text.split(" ",1)[1]
    sent=fail=0
    for u in users.find({}):
        try: await bot.send_message(u["_id"],msg); sent+=1
        except (UserIsBlocked,Exception): fail+=1
        await asyncio.sleep(0.05)
    await m.reply_text(f"Broadcast done\nSent: {sent}\nFailed: {fail}")

# ---- FILE SEARCH ----
@bot.on_message(filters.command("file"))
async def file_cmd(_,m):
    if len(m.command)<2:return await m.reply_text("Usage: /file <keyword>")
    key=m.text.split(" ",1)[1]
    data=list(files.find({"name":{"$regex":key,"$options":"i"}}))
    if not data:return await m.reply_text("No match found.")
    await m.reply_text(f"Found {len(data)} result(s).")
    for f in data:
        fid=f["file_id"]
        try:
            await bot.send_video(m.chat.id,fid,caption=f.get("caption") or f["name"])
        except Exception:
            await bot.send_document(m.chat.id,fid,caption=f.get("caption") or f["name"])
        await asyncio.sleep(1)

# ---- CANCEL ----
cancel={}
@bot.on_message(filters.command("cancel"))
async def cancel_cmd(_,m):
    cancel[m.from_user.id]=True
    await m.reply_text("Cancelled current task.")

# ---- DOWNLOAD UTILS ----
async def m3u8_to_mp4(url,out):
    cmd=f'ffmpeg -y -i \"{url}\" -c copy \"{out}\"'
    proc=await asyncio.create_subprocess_shell(cmd,stdout=asyncio.subprocess.DEVNULL,stderr=asyncio.subprocess.DEVNULL)
    await proc.communicate(); return os.path.exists(out)

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
    except Exception: return False

# ---- MAIN DOWNLOADER ----
async def process(url,m):
    uid=m.from_user.id
    user=users.find_one({"_id":uid}) or {}
    mode=user.get("opt","video")
    caption=user.get("caption","")
    tmp=tempfile.gettempdir()
    name="file.bin"; path=os.path.join(tmp,name)
    msg=await m.reply_text("Starting download …")
    try:
        if ".m3u8" in url:
            name="video.mp4"; path=os.path.join(tmp,name)
            await msg.edit_text("Fetching M3U8 stream …")
            ok=await m3u8_to_mp4(url,path)
            if not ok:return await msg.edit_text("Failed to fetch M3U8 stream.")
        elif "instagram.com" in url:
            name="insta.mp4"; path=os.path.join(tmp,name)
            await msg.edit_text("Fetching Instagram video …")
            ok=await insta_dl(url,path)
            if not ok:return await msg.edit_text("Failed to download Instagram video.")
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
                            if cancel.get(uid): await msg.edit_text("Cancelled."); return
                            f.write(chunk); done+=len(chunk)
                            now=time.time()
                            if now-last>10:
                                spd=done/max(now-start,1)
                                try: await msg.edit_text(fancy_bar(name,"Downloading",done,total,spd))
                                except FloodWait as e: await asyncio.sleep(e.value)
                                except: pass
                                last=now
        await msg.edit_text("Uploading backup to Logs …")
        fincap=(caption+"\n" if caption else "")+name
        logm=await log_file(path,f"Backup: {name}\n{fincap}")
        await msg.edit_text("Uploading to you …")
        if mode=="video":
            await bot.send_video(uid,path,caption=fincap)
        else:
            await bot.send_document(uid,path,caption=fincap)
        files.insert_one({"name":name,"file_id":logm.document.file_id,"type":mode,"caption":caption})
        await msg.delete()
    except Exception as e:
        await msg.edit_text(f"Error: {e}")
    finally:
        if os.path.exists(path): os.remove(path)
        cancel[uid]=False

# ---- WRONG COMMAND/LINK GUIDE ----
@bot.on_message(filters.text & ~filters.command(
    ["start","help","status","file","settings","clear","database","broadcast","cancel"]))
async def detect(_,m):
    txt=m.text.strip()
    for u in txt.split():
        if u.startswith("http"):
            await process(u,m); return
    await m.reply_text("That doesn't look like a valid link.\nExample: https://example.com/video.mp4\nUse /help for info.")

# ---- RUN ----
if __name__=="__main__":
    print("🚀 SERENA booting — Flask thread + polling starting now")
    threading.Thread(target=run_web,daemon=True).start()
    bot.run()
