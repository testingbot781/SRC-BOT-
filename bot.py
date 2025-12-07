import os, aiohttp, asyncio, time, mimetypes, threading, psutil, tempfile
from pyrogram import Client, filters
from pyrogram.errors import FloodWait, UserNotParticipant, UserIsBlocked
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton, Message
from flask import Flask
from pymongo import MongoClient
import subprocess, shlex   # for m3u8->mp4

# ---------- CONFIG ----------
API_ID=int(os.getenv("API_ID"))
API_HASH=os.getenv("API_HASH")
BOT_TOKEN=os.getenv("BOT_TOKEN")
MONGO_URL=os.getenv("MONGO_URL")
OWNER_ID=1598576202
LOGS_CHANNEL=-1003286415377
FORCE_CH="serenaunzipbot"
FORCE_LINK="https://t.me/serenaunzipbot"

# ---------- DB ----------
mongo=MongoClient(MONGO_URL)
db=mongo["serena"]
users=db["users"]
files=db["files"]

# ---------- BOT ----------
bot=Client("SERENA",api_id=API_ID,api_hash=API_HASH,bot_token=BOT_TOKEN)

# ---------- FLASK for Render ----------
flask_app=Flask(__name__)
@flask_app.route("/",methods=["GET","POST","HEAD"])
def home(): return "💠 SERENA active"
def keepalive(): flask_app.run(host="0.0.0.0",port=int(os.environ.get("PORT",10000)))

# ---------- UTILITIES ----------
def fmt_size(n):
    for u in ["B","KB","MB","GB","TB"]:
        if n<1024: return f"{n:.2f} {u}"
        n/=1024
    return f"{n:.2f} PB"

def fmt_time(sec):
    if sec<=0: return "<1 s"
    m,s=divmod(int(sec),60);h,m=divmod(m,60)
    if h: return f"{h} h {m} m {s} s"
    if m: return f"{m} m {s} s"
    return f"{s} s"

def show_bar(name,phase,done,total,speed):
    pct=done/total*100 if total else 0
    dots=int(18*pct/100)
    bar="●"*dots+"○"*(18-dots)
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
        users.insert_one({"_id":uid,"queue":[],"ptype":"doc"})   # default upload‑as‑document

async def log_text(t):
    try: await bot.send_message(LOGS_CHANNEL,t)
    except: pass
async def log_doc(p,c):
    try: return await bot.send_document(LOGS_CHANNEL,p,caption=c)
    except: return None

# ---------- SETTINGS ----------
@bot.on_message(filters.command("settings"))
async def settings(_,m):
    await ensure_user(m.from_user.id)
    u=users.find_one({"_id":m.from_user.id})
    sel=u.get("ptype","doc")
    btns=[[InlineKeyboardButton("📄 Upload as Document" + (" ✅" if sel=="doc" else ""),callback_data="set_doc"),
           InlineKeyboardButton("🎥 Upload as Video" + (" ✅" if sel=="vid" else ""),callback_data="set_vid")]]
    await m.reply_text("⚙️ Choose your upload mode:",reply_markup=InlineKeyboardMarkup(btns))

@bot.on_callback_query(filters.regex("^set_"))
async def set_mode(_,q):
    mode="vid" if q.data=="set_vid" else "doc"
    users.update_one({"_id":q.from_user.id},{"$set":{"ptype":mode}})
    await q.answer("✅ Updated preference")
    # refresh buttons
    await settings(_,q.message)

# ---------- CANCEL ----------
cancel_flags={}
@bot.on_message(filters.command("cancel"))
async def cancel(_,m):
    cancel_flags[m.from_user.id]=True
    await m.reply_text("🛑 Cancelled current running task !")

# ---------- FILE SEARCH (works also in groups) ----------
@bot.on_message(filters.command("file"))
async def file(_,m):
    if len(m.command)<2: return await m.reply_text("Usage: /file <keyword>")
    key=m.text.split(" ",1)[1]
    found=list(files.find({"name":{"$regex":key,"$options":"i"}}))
    if not found: return await m.reply_text("❌ No match found.")
    await m.reply_text(f"📂 Found {len(found)} file(s), sending …")
    for f in found:
        await bot.send_document(m.chat.id,f["file_id"],caption=f["name"],
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("💬 Contact Owner",url="https://t.me/technicalserena")]]))
        await asyncio.sleep(1)

# ---------- GROUP + PRIVATE LINK DETECTOR ----------
@bot.on_message((filters.regex(r"https?://\S+") | filters.text) & filters.incoming)
async def link_detector(_,m):
    # accept only private or group messages, skip channels
    if m.chat.type not in ("private","group","supergroup"): return
    for url in m.text.split():
        if url.startswith("http"):
            asyncio.create_task(download_and_send(m,url))

# ---------- M3U8 DOWNLOAD CONVERT ----------
async def fetch_m3u8_to_mp4(url,outfile):
    # use ffmpeg available on Render; -y overwrite output
    command=f'ffmpeg -y -i "{url}" -c copy "{outfile}"'
    proc=await asyncio.create_subprocess_shell(command,stdout=asyncio.subprocess.DEVNULL,stderr=asyncio.subprocess.DEVNULL)
    await proc.communicate()
    return os.path.exists(outfile)

# ---------- MAIN DOWN/UPLOAD ----------
async def download_and_send(m,url):
    uid=m.from_user.id if m.from_user else 0
    await ensure_user(uid or 0)
    if not await joined(uid or m.chat.id): 
        kb=InlineKeyboardMarkup([[InlineKeyboardButton("📢 Join Channel",url=FORCE_LINK)]])
        return await m.reply_text("⚠️ Join update channel first 🌼",reply_markup=kb)
    userpref=users.find_one({"_id":uid},{"ptype":1}) or {"ptype":"doc"}
    ptype=userpref.get("ptype","doc")

    fname="file.bin"
    pmsg=await m.reply_text("📥 Starting download …")
    tmpdir=tempfile.gettempdir()
    try:
        # ----- direct M3U8 handling -----
        if ".m3u8" in url:
            fname="video.mp4"
            out=os.path.join(tmpdir,fname)
            await pmsg.edit_text("🎞️ Fetching M3U8 stream …")
            ok=await fetch_m3u8_to_mp4(url,out)
            if not ok: return await pmsg.edit_text("⚠️ Failed to fetch stream !")
        else:
            async with aiohttp.ClientSession() as s:
                async with s.get(url,allow_redirects=True) as r:
                    total=int(r.headers.get("Content-Length",0))
                    cd=r.headers.get("Content-Disposition")
                    if cd and "filename=" in cd:
                        fname=cd.split("filename=")[-1].strip('"; ')
                    else:
                        ct=r.headers.get("Content-Type","")
                        ext=mimetypes.guess_extension(ct.split(";")[0].strip()) or ".bin"
                        base=os.path.basename(url.split("?")[0]) or "file"
                        fname=base if "." in base else base+ext
                    path=os.path.join(tmpdir,fname)
                    start,done,last=time.time(),0,0
                    with open(path,"wb") as f:
                        async for chunk in r.content.iter_chunked(1024*512):
                            f.write(chunk);done+=len(chunk)
                            if cancel_flags.get(uid): 
                                await pmsg.edit_text("🛑 Cancelled during download")
                                return
                            now=time.time()
                            if now-last>10:
                                spd=done/max(now-start,1)
                                try: await pmsg.edit_text(show_bar(fname,"⬇️ Downloading",done,total,spd))
                                except FloodWait as e: await asyncio.sleep(e.value)
                                except: pass
                                last=now
            out=path

        # ---- upload to Logs ----
        await pmsg.edit_text("📦 Uploading backup to Logs …")
        logm=await log_doc(out,f"📦 Backup:{fname}")
        if not logm: return await pmsg.edit_text("⚠️ Backup failed.")
        # ---- upload to user/group ----
        await pmsg.edit_text("📤 Uploading to you …")
        if ptype=="vid":
            await bot.send_video(m.chat.id,out,caption=f"`{fname}`",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("💬 Contact Owner",
                    url="https://t.me/technicalserena")]]))
        else:
            await bot.send_document(m.chat.id,out,caption=f"`{fname}`",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("💬 Contact Owner",
                    url="https://t.me/technicalserena")]]))
        files.insert_one({"name":fname,"file_id":logm.document.file_id,"type":ptype})
        await pmsg.delete()
        await log_text(f"✅ Delivered {fname} to {m.chat.id}")
    except Exception as e:
        await pmsg.edit_text(f"❌ Error {e}")
        await log_text(str(e))
    finally:
        try: os.remove(os.path.join(tmpdir,fname))
        except: pass
        cancel_flags[uid]=False

# ---------- START ----------
if __name__=="__main__":
    print("💠 SERENA booting — Flask health + Telegram polling")
    threading.Thread(target=keepalive).start()
    bot.run()
