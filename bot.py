import os, aiohttp, asyncio, time, mimetypes, threading, tempfile, psutil, subprocess
from pyrogram import Client, filters
from pyrogram.errors import FloodWait, UserNotParticipant, UserIsBlocked
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from flask import Flask
from pymongo import MongoClient

# ---------- CONFIG ----------
API_ID=int(os.getenv("API_ID"))
API_HASH=os.getenv("API_HASH")
BOT_TOKEN=os.getenv("BOT_TOKEN")
MONGO_URL=os.getenv("MONGO_URL")
OWNER_ID=1598576202
LOGS_CHANNEL=-1003286415377
FORCE_CH="serenaunzipbot"
FORCE_LINK="https://t.me/serenaunzipbot"

# ---------- DATABASE ----------
mongo=MongoClient(MONGO_URL)
db=mongo["serena"]
users=db["users"]
files=db["files"]

# ---------- BOT ----------
bot=Client("SERENA",api_id=API_ID,api_hash=API_HASH,bot_token=BOT_TOKEN)

# ---------- FLASK KEEP‑ALIVE (Render port detection) ----------
flask_app=Flask(__name__)
@flask_app.route("/",methods=["GET","POST","HEAD"])
def home(): return "💠 SERENA alive!"
def run_flask():
    port=int(os.environ.get("PORT",10000))
    flask_app.run(host="0.0.0.0",port=port)

# ---------- UTILITIES ----------
def fmt_size(n):
    for u in ["B","KB","MB","GB","TB"]:
        if n<1024:return f"{n:.2f} {u}"
        n/=1024
    return f"{n:.2f} PB"

def fmt_time(sec):
    if sec<=0:return "<1 s"
    m,s=divmod(int(sec),60);h,m=divmod(m,60)
    if h:return f"{h} h {m} m {s} s"
    if m:return f"{m} m {s} s"
    return f"{s} s"

def show_bar(name,phase,done,total,speed):
    pct=done/total*100 if total else 0
    bar="●"*int(18*pct/100)+"○"*(18-int(18*pct/100))
    eta=fmt_time((total-done)/speed if speed>0 else 0)
    return (f"**{phase}**\n\n"
            f"`{name}`\n[{bar}]\n"
            f"💞 {pct:.2f}%  ✅ {fmt_size(done)}/{fmt_size(total)}\n"
            f"🚀 {fmt_size(speed)}/s  ⏳ {eta}")

async def joined(uid):
    try:
        await bot.get_chat_member(FORCE_CH,uid)
        return True
    except UserNotParticipant: return False
    except: return False

async def ensure_user(uid):
    if not users.find_one({"_id":uid}):
        users.insert_one({"_id":uid,"ptype":"doc"})

async def log_text(t):
    try: await bot.send_message(LOGS_CHANNEL,t)
    except: pass

async def log_doc(p,c):
    try: return await bot.send_document(LOGS_CHANNEL,p,caption=c)
    except: return None

cancel_flags={}

# ---------- /settings ----------
@bot.on_message(filters.command("settings"))
async def settings(_,m):
    await ensure_user(m.from_user.id)
    u=users.find_one({"_id":m.from_user.id}) or {}
    mode=u.get("ptype","doc")
    kb=[[InlineKeyboardButton("📄 Upload as Document"+(" ✅" if mode=="doc" else ""),callback_data="mode_doc"),
         InlineKeyboardButton("🎥 Upload as Video"+(" ✅" if mode=="vid" else ""),callback_data="mode_vid")]]
    await m.reply_text("⚙️ Choose upload preference:",reply_markup=InlineKeyboardMarkup(kb))

@bot.on_callback_query(filters.regex("^mode_"))
async def set_mode(_,q):
    new="vid" if q.data.endswith("vid") else "doc"
    users.update_one({"_id":q.from_user.id},{"$set":{"ptype":new}},upsert=True)
    await q.answer("✅ Updated")
    await settings(_,q.message)

# ---------- /cancel ----------
@bot.on_message(filters.command("cancel"))
async def cancel(_,m):
    cancel_flags[m.from_user.id]=True
    await m.reply_text("🛑 Cancelling current task …")

# ---------- /file ----------
@bot.on_message(filters.command("file"))
async def file_lookup(_,m):
    if len(m.command)<2:return await m.reply_text("Usage: /file <keyword>")
    key=m.text.split(" ",1)[1]
    res=list(files.find({"name":{"$regex":key,"$options":"i"}}))
    if not res:return await m.reply_text("❌ No match found.")
    await m.reply_text(f"📂 Found {len(res)} file(s), sending …")
    for f in res:
        await bot.send_document(m.chat.id,f["file_id"],caption=f["name"],
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("💬 Contact Owner",url="https://t.me/technicalserena")]]))
        await asyncio.sleep(1)

# ---------- LINK DETECTOR (works in private & groups) ----------
@bot.on_message(filters.text & filters.incoming)
async def link_detect(_,m):
    if m.chat.type not in ("private","group","supergroup"): return
    if not m.text: return
    for url in m.text.split():
        if url.startswith("http"):
            asyncio.create_task(process_link(m,url))

# ---------- m3u8 SUPPORT ----------
async def m3u8_to_mp4(url,out):
    cmd=f'ffmpeg -y -i "{url}" -c copy "{out}"'
    proc=await asyncio.create_subprocess_shell(cmd,stdout=asyncio.subprocess.DEVNULL,stderr=asyncio.subprocess.DEVNULL)
    await proc.communicate()
    return os.path.exists(out)

# ---------- CORE ----------
async def process_link(m,url):
    uid=m.from_user.id if m.from_user else 0
    await ensure_user(uid)
    mode=users.find_one({"_id":uid},{"ptype":1})["ptype"]
    tmp=tempfile.gettempdir()
    filename="file.bin"
    msg=await m.reply_text("📥 Starting download …")
    try:
        if ".m3u8" in url:
            filename="video.mp4"
            path=os.path.join(tmp,filename)
            await msg.edit_text("🎞️ Fetching M3U8 stream …")
            ok=await m3u8_to_mp4(url,path)
            if not ok:return await msg.edit_text("⚠️ Stream download failed !")
        else:
            async with aiohttp.ClientSession() as s:
                async with s.get(url,allow_redirects=True) as r:
                    total=int(r.headers.get("Content-Length",0))
                    cd=r.headers.get("Content-Disposition")
                    if cd and "filename=" in cd:
                        filename=cd.split("filename=")[-1].strip('"; ')
                    else:
                        ct=r.headers.get("Content-Type","")
                        ext=mimetypes.guess_extension(ct.split(";")[0].strip()) or ".bin"
                        base=os.path.basename(url.split("?")[0]) or "file"
                        filename=base if "." in base else base+ext
                    path=os.path.join(tmp,filename)
                    start,done,last=time.time(),0,0
                    with open(path,"wb") as f:
                        async for chunk in r.content.iter_chunked(1024*512):
                            if cancel_flags.get(uid):
                                await msg.edit_text("🛑 Cancelled by user")
                                return
                            f.write(chunk);done+=len(chunk)
                            now=time.time()
                            if now-last>10:
                                spd=done/max(now-start,1)
                                try:await msg.edit_text(show_bar(filename,"⬇️ Downloading",done,total,spd))
                                except FloodWait as e:await asyncio.sleep(e.value)
                                except:pass
                                last=now

        # Upload to logs
        await msg.edit_text("📦 Uploading backup to Logs …")
        logm=await log_doc(path,f"📦 Backup:{filename}")
        if not logm:return await msg.edit_text("⚠️ Backup failed")
        # Send to user/group
        await msg.edit_text("📤 Uploading to chat …")
        if mode=="vid":
            await bot.send_video(m.chat.id,path,caption=f"`{filename}`",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("💬 Contact Owner",url="https://t.me/technicalserena")]]))
        else:
            await bot.send_document(m.chat.id,path,caption=f"`{filename}`",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("💬 Contact Owner",url="https://t.me/technicalserena")]]))
        files.insert_one({"name":filename,"file_id":logm.document.file_id,"type":mode})
        await msg.delete()
        await log_text(f"✅ Delivered {filename} to {m.chat.id}")
    except Exception as e:
        await msg.edit_text(f"❌ Error {e}")
        await log_text(str(e))
    finally:
        try: os.remove(os.path.join(tmp,filename))
        except: pass
        cancel_flags[uid]=False

# ---------- RUN ----------
if __name__=="__main__":
    print("💠 SERENA starting – Render port open + Telegram polling together")
    t=threading.Thread(target=run_flask,daemon=True)
    t.start()
    bot.run()
