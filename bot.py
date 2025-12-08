import os, sys, threading, asyncio, aiohttp, time, mimetypes, tempfile, subprocess
from flask import Flask
from pyrogram import Client, filters
from pyrogram.errors import FloodWait, UserNotParticipant
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from pymongo import MongoClient

# flush Render logs immediately
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
app=Flask(__name__)
@app.route("/",methods=["GET","HEAD","POST"])
def home():return "💠 SERENA alive"
def run_flask():
    port=int(os.environ.get("PORT",10000))
    app.run(host="0.0.0.0",port=port,threaded=True)

# ---------- UTIL ----------
def fmt_size(n):
    for u in["B","KB","MB","GB","TB"]:
        if n<1024:return f"{n:.2f}{u}"
        n/=1024
    return f"{n:.2f}PB"

def fmt_time(s):
    if s<=0:return "<1 s"
    m,s=divmod(int(s),60);h,m=divmod(m,60)
    if h:return f"{h} h {m} m {s} s"
    if m:return f"{m} m {s} s"
    return f"{s} s"

def bar(name,phase,done,total,speed):
    pct=done/total*100 if total else 0
    fill=int(18*pct/100)
    bars="●"*fill+"○"*(18-fill)
    eta=fmt_time((total-done)/speed if speed>0 else 0)
    return(f"**{phase}**\n\n`{name}`\n[{bars}]\n💞 {pct:.2f}% ✅ {fmt_size(done)}/{fmt_size(total)}\n🚀 {fmt_size(speed)}/s ⏳ {eta}")

async def joined(uid):
    try:
        await bot.get_chat_member(FORCE_CH,uid)
        return True
    except UserNotParticipant:return False
    except:return False

async def log_msg(t):
    try:await bot.send_message(LOGS_CHANNEL,t)
    except:pass
async def log_doc(path,cap):
    try:return await bot.send_document(LOGS_CHANNEL,path,caption=cap)
    except:return None

async def ensure_user(uid):
    if not users.find_one({"_id":uid}):
        users.insert_one({"_id":uid,"opt":"video"})

# ---------- SETTINGS ----------
@bot.on_message(filters.command("settings"))
async def settings(_,m):
    await ensure_user(m.from_user.id)
    opt=users.find_one({"_id":m.from_user.id}).get("opt","video")
    kb=[[InlineKeyboardButton("🎥 Upload as Video"+(" ✅" if opt=="video" else ""),callback_data="v"),
         InlineKeyboardButton("📄 Upload as Document"+(" ✅" if opt=="doc" else ""),callback_data="d")]]
    await m.reply_text("⚙️ Select upload preference:",reply_markup=InlineKeyboardMarkup(kb))

@bot.on_callback_query()
async def cb(_,q):
    val="video" if q.data=="v" else "doc"
    users.update_one({"_id":q.from_user.id},{"$set":{"opt":val}},upsert=True)
    await q.answer("✅ Saved")
    await settings(_,q.message)

# ---------- CANCEL ----------
cancel={}
@bot.on_message(filters.command("cancel"))
async def cancel_cmd(_,m):
    cancel[m.from_user.id]=True
    await m.reply_text("🛑 Cancelled current task")

# ---------- FILE SEARCH ----------
@bot.on_message(filters.command("file"))
async def file(_,m):
    if len(m.command)<2:return await m.reply_text("Usage: /file <keyword>")
    key=m.text.split(" ",1)[1]
    fs=list(files.find({"name":{"$regex":key,"$options":"i"}}))
    if not fs:return await m.reply_text("❌ No match found")
    await m.reply_text(f"📂 Found {len(fs)} file(s)")
    for f in fs:
        await bot.send_document(m.chat.id,f["file_id"],caption=f["name"],
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("💬 Owner",url="https://t.me/technicalserena")]]))
        await asyncio.sleep(1)

# ---------- SPECIAL DOWNLOADS ----------
async def m3u8_to_mp4(url,out):
    cmd=f'ffmpeg -y -i "{url}" -c copy "{out}"'
    p=await asyncio.create_subprocess_shell(cmd,stdout=asyncio.subprocess.DEVNULL,stderr=asyncio.subprocess.DEVNULL)
    await p.communicate();return os.path.exists(out)

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
        print("insta err:",e)
        return False

# ---------- MAIN PROCESS ----------
async def process(m,url):
    uid=m.from_user.id
    mode=users.find_one({"_id":uid}).get("opt","video")
    tmp=tempfile.gettempdir()
    name="file.bin";path=os.path.join(tmp,name)
    msg=await m.reply_text("📥 Starting download …")
    try:
        if ".m3u8" in url:
            name="video.mp4";path=os.path.join(tmp,name)
            await msg.edit_text("🎞️ Fetching M3U8 stream …")
            ok=await m3u8_to_mp4(url,path)
            if not ok:return await msg.edit_text("⚠️ Failed to fetch stream")
        elif "instagram.com" in url:
            name="insta.mp4";path=os.path.join(tmp,name)
            await msg.edit_text("📸 Fetching Instagram video …")
            ok=await insta_dl(url,path)
            if not ok:return await msg.edit_text("⚠️ Cannot download Instagram link")
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
                                await msg.edit_text("🛑 Cancelled by user")
                                return
                            f.write(chunk);done+=len(chunk)
                            now=time.time()
                            if now-last>10:
                                spd=done/max(now-start,1)
                                try:await msg.edit_text(bar(name,"⬇️ Downloading",done,total,spd))
                                except FloodWait as e:await asyncio.sleep(e.value)
                                except:pass
                                last=now
        await msg.edit_text("📦 Uploading backup to Logs …")
        logm=await log_doc(path,f"📦 Backup:{name}")
        await msg.edit_text("📤 Uploading to you …")
        # swap mode (reverse rule)
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
        try:os.remove(path)
        except:pass
        cancel[uid]=False

# ---------- MESSAGE DETECTOR ----------
@bot.on_message(filters.text & filters.incoming)
async def detect(_,m):
    if not m.text:return
    for url in m.text.split():
        if url.startswith("http"):
            asyncio.create_task(process(m,url))
            return
    await m.reply_text("😅 That doesn’t look like a link .")

# ---------- START ----------
@bot.on_message(filters.command("start"))
async def start(_,m):
    kb=InlineKeyboardMarkup([
        [InlineKeyboardButton("📢 Join Channel",url=FORCE_LINK)],
        [InlineKeyboardButton("💬 Contact Owner",url="https://t.me/technicalserena")]])
    await m.reply_text("💎 **SERENA Downloader** 💎\n\nSend a direct link or .m3u8 / Instagram video and watch her magic ✨",reply_markup=kb)

# ---------- RUN ----------
if __name__=="__main__":
    print("🚀 Reaching bot.run() — Flask thread + polling starting now")
    threading.Thread(target=run_flask,daemon=True).start()
    bot.run()
