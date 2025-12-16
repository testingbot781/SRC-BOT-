import os
import time
import asyncio
import threading
import os
import time
import aiohttp
# ...

PROGRESS_INTERVAL = int(os.environ.get("PROGRESS_INTERVAL", "5"))
from datetime import date
from urllib.parse import urlparse, urljoin

import aiohttp
import psutil
import m3u8
import google.generativeai as genai
from flask import Flask
from pymongo import MongoClient
from yt_dlp import YoutubeDL
from yt_dlp.utils import DownloadError
from pyrogram import Client, filters, enums
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from pyrogram.errors import FloodWait, UserIsBlocked, InputUserDeactivated, RPCError

# ===== ENV =====
API_ID = int(os.environ["API_ID"])
API_HASH = os.environ["API_HASH"]
BOT_TOKEN = os.environ["BOT_TOKEN"]
MONGO_URL = os.environ["MONGO_URL"]

OWNER_IDS = {1598576202, 6518065496}
LOGS_CHANNEL = -1003286415377
FORCE_CH = "serenaunzipbot"
FORCE_LINK = "https://t.me/serenaunzipbot"
OWNER_CONTACT = "https://t.me/technicalserena"
DAILY_FREE_LIMIT = 5

# Gemini (GF chat) env
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "").strip()
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-1.5-flash")
if GEMINI_API_KEY:
    genai.configure(api_key=GEMINI_API_KEY)

# YouTube cookies (Netscape format text)
YT_COOKIES_STR = os.environ.get("YT_COOKIES", "").strip()
YT_COOKIE_FILE = None
if YT_COOKIES_STR:
    YT_COOKIE_FILE = "yt_cookies.txt"
    with open(YT_COOKIE_FILE, "w", encoding="utf-8") as f:
        f.write(YT_COOKIES_STR)

DOWNLOAD_DIR = "downloads"
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

# ===== DB =====
mongo = MongoClient(MONGO_URL)
db = mongo["serena"]
users = db["users"]
files = db["files"]

# ===== BOT =====
bot = Client(
    "serena-url-bot",
    api_id=API_ID,
    api_hash=API_HASH,
    bot_token=BOT_TOKEN,
    parse_mode=enums.ParseMode.MARKDOWN,
)

# ===== FLASK (for Render) =====
app = Flask(__name__)


@app.route("/")
def home():
    return "Serena URL bot is running"


def run_flask():
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)


ACTIVE_TASKS = {}       # user_id -> bool
AWAITING_CAPTION = set()


# ===== HELPERS =====

def owner_filter():
    return filters.user(list(OWNER_IDS))


def main_buttons():
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("📢 Join Channel", url=FORCE_LINK)],
            [InlineKeyboardButton("💬 Contact Owner", url=OWNER_CONTACT)],
        ]
    )


def owner_button():
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton("💬 Contact Owner", url=OWNER_CONTACT)]]
    )


def settings_keyboard(mode: str):
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    f"🎥 Upload as Video{' ✅' if mode == 'video' else ''}",
                    callback_data="set_vid",
                )
            ],
            [
                InlineKeyboardButton(
                    f"📄 Upload as Document{' ✅' if mode == 'doc' else ''}",
                    callback_data="set_doc",
                )
            ],
            [
                InlineKeyboardButton("➕ Add Caption", callback_data="add_cap"),
                InlineKeyboardButton("♻️ Reset Caption", callback_data="clr_cap"),
            ],
        ]
    )


def get_user(uid: int):
    now = int(time.time())
    u = users.find_one({"_id": uid})
    if not u:
        u = {
            "_id": uid,
            "joined_at": now,
            "last_seen": now,
            "premium_until": None,
            "daily_date": None,
            "daily_used": 0,
            "total_tasks": 0,
            "upload_mode": "video",
            "caption": "",
            "blocked": False,
            "gf_mode": False,
            "gender": "unknown",  # 'male' / 'female' / 'other'
        }
        users.insert_one(u)
    else:
        users.update_one({"_id": uid}, {"$set": {"last_seen": now}})
    return u


def refresh_quota(u: dict):
    today = date.today().isoformat()
    if u.get("daily_date") != today:
        u["daily_date"] = today
        u["daily_used"] = 0
        users.update_one(
            {"_id": u["_id"]},
            {"$set": {"daily_date": today, "daily_used": 0}},
        )


def is_premium(u: dict) -> bool:
    until = u.get("premium_until")
    return bool(until and until > int(time.time()))


async def log_text(text: str):
    try:
        await bot.send_message(LOGS_CHANNEL, text)
    except Exception:
        pass


async def ensure_subscribed(client: Client, m):
    if m.chat.type != enums.ChatType.PRIVATE:
        return True
    try:
        member = await client.get_chat_member(FORCE_CH, m.from_user.id)
        if member.status in (
            enums.ChatMemberStatus.LEFT,
            enums.ChatMemberStatus.BANNED,
        ):
            raise RPCError("not joined")
        return True
    except Exception:
        kb = main_buttons()
        await m.reply_text(
            "⚠️ Bot use karne se pehle hamare channel ko join karein.\n\n"
            f"Channel: @{FORCE_CH}",
            reply_markup=kb,
        )
        return False


def is_url(text: str) -> bool:
    return text.startswith("http://") or text.startswith("https://")


def url_type(url: str) -> str:
    u = url.lower()
    path = urlparse(u).path
    if "youtube.com" in u or "youtu.be" in u:
        return "yt"
    if "mega.nz" in u:
        return "mega"
    if ".m3u8" in path or ".m3u8" in u:
        return "m3u8"
    return "direct"


def make_name(url: str, default_ext="mp4") -> str:
    path = urlparse(url).path
    name = os.path.basename(path).split("?")[0].split("#")[0]
    if not name:
        name = f"file_{int(time.time())}.{default_ext}"
    if "." not in name:
        name = f"{name}.{default_ext}"
    return name


def sizeof_fmt(num: int) -> str:
    if num <= 0:
        return "0 MB"
    return f"{num / (1024 * 1024):.2f} MB"


def time_fmt(sec: float) -> str:
    sec = int(sec)
    if sec <= 0:
        return "0s"
    m, s = divmod(sec, 60)
    h, m = divmod(m, 60)
    if h > 0:
        return f"{h}h, {m}m"
    if m > 0:
        return f"{m}m, {s}s"
    return f"{s}s"


def progress_text(title: str, current: int, total: int | None,
                  start_time: float, stage: str) -> str:
    now = time.time()
    elapsed = max(1e-3, now - start_time)
    speed = current / (1024 * 1024 * elapsed)

    if total and total > 0:
        pct = current * 100 / total
        bar_len = 20
        filled = int(bar_len * pct / 100)
        bar = "●" * filled + "○" * (bar_len - filled)
        done_str = f"{sizeof_fmt(current)} of  {sizeof_fmt(total)}"
        remain = max(0, total - current)
        eta = remain / max(1, current) * elapsed
        eta_str = time_fmt(eta)
    else:
        pct = 0
        bar = "●○" * 10
        done_str = f"{sizeof_fmt(current)} of  ?"
        eta_str = "calculating..."

    return (
        "➵⋆🪐ᴛᴇᴄʜɴɪᴄᴀʟ_sᴇʀᴇɴᴀ𓂃\n\n"
        f"{title}\n"
        f"{stage}\n"
        f" [{bar}] \n"
        f"◌Progress😉:〘 {pct:.2f}% 〙\n"
        f"Done: 〘{done_str}〙\n"
        f"◌Speed🚀:〘 {speed:.2f} MB/s 〙\n"
        f"◌Time Left⏳:〘 {eta_str} 〙"
                                        )
# ===== DOWNLOADERS =====

async def download_direct(url, dest, status_msg, uid, title, headers=None):
    start = time.time()
    session_headers = headers or {}
    async with aiohttp.ClientSession(headers=session_headers) as sess:
        async with sess.get(url) as resp:
            if resp.status == 403:
                raise Exception(
                    "Direct link HTTP 403 (forbidden) – server ne access block kar diya.\n"
                    "Yeh login/geo-protected file ho sakti hai."
                )
            if resp.status != 200:
                raise Exception(f"HTTP {resp.status}")
            total = int(resp.headers.get("Content-Length", 0))
            done = 0
            last = 0
            with open(dest, "wb") as f:
                async for chunk in resp.content.iter_chunked(1024 * 1024):
                    if not ACTIVE_TASKS.get(uid):
                        raise Exception("Task cancelled")
                    if not chunk:
                        continue
                    f.write(chunk)
                    done += len(chunk)
                    now = time.time()
                    if total and now - last > PROGRESS_INTERVAL:
                        txt = progress_text(title, done, total, start, "to my server")
                        try:
                            await status_msg.edit_text(txt)
                        except Exception:
                            pass
                        last = now
    if total:
        txt = progress_text(title, total, total, start, "to my server")
        try:
            await status_msg.edit_text(txt)
        except Exception:
            pass
    return dest


async def download_m3u8(url, dest, status_msg, uid, title):
    start = time.time()
    async with aiohttp.ClientSession() as session:
        async with session.get(url) as resp:
            if resp.status == 403:
                raise Exception(
                    "m3u8 HTTP 403 (forbidden) – yeh stream login/DRM/geo protected lagti hai.\n"
                    "Isko bot se download nahi kar sakte."
                )
            if resp.status != 200:
                raise Exception(f"m3u8 HTTP {resp.status}")
            text = await resp.text()

    if "#EXTM3U" not in text:
        raise Exception(
            "Yeh valid .m3u8 playlist nahi lagti (missing #EXTM3U).\n"
            "Sahi media/master .m3u8 link bhejo (DRM ya login-protected stream nahi chalega)."
        )

    pl = m3u8.loads(text)
    base_url = url

    if pl.is_variant and pl.playlists:
        best = max(pl.playlists, key=lambda p: (p.stream_info.bandwidth or 0))
        media_url = urljoin(url, best.uri)
        async with aiohttp.ClientSession() as session:
            async with session.get(media_url) as resp:
                if resp.status == 403:
                    raise Exception(
                        "variant m3u8 HTTP 403 – stream login/DRM protected lagti hai."
                    )
                if resp.status != 200:
                    raise Exception(f"variant m3u8 HTTP {resp.status}")
                text = await resp.text()
        pl = m3u8.loads(text)
        base_url = media_url

    segments = list(pl.segments)

    if not segments:
        lines = [
            l.strip()
            for l in text.splitlines()
            if l.strip() and not l.strip().startswith("#")
        ]
        if not lines:
            raise Exception(
                "Is m3u8 file me koi segments nahi mile.\n"
                "Yeh DRM/login protected stream ya galat link ho sakta hai."
            )
        segment_urls = [urljoin(base_url, l) for l in lines]
    else:
        segment_urls = [urljoin(base_url, s.uri) for s in segments]

    total_seg = len(segment_urls)
    downloaded = 0
    last = 0

    with open(dest, "wb") as out:
        async with aiohttp.ClientSession() as session:
            for idx, seg_url in enumerate(segment_urls, start=1):
                if not ACTIVE_TASKS.get(uid):
                    raise Exception("Task cancelled")
                async with session.get(seg_url) as resp:
                    if resp.status == 403:
                        raise Exception(
                            "Segment HTTP 403 – yeh stream login/DRM protected lagti hai."
                        )
                    if resp.status != 200:
                        raise Exception(f"segment HTTP {resp.status}")
                    async for chunk in resp.content.iter_chunked(512 * 1024):
                        if not chunk:
                            continue
                        out.write(chunk)
                        downloaded += len(chunk)
                now = time.time()
                if now - last > 2:
                    pct = idx * 100 / total_seg
                    txt = progress_text(
                        title, downloaded, None, start, "to my server"
                    ) + f"\n(segments: {idx}/{total_seg}, ~{pct:.1f}%)"
                    try:
                        await status_msg.edit_text(txt)
                    except Exception:
                        pass
                    last = now

    return dest


async def get_youtube_direct(url: str):
    loop = asyncio.get_running_loop()

    def _extract():
        ydl_opts = {
            "quiet": True,
            "skip_download": True,
            "noplaylist": True,
        }
        if YT_COOKIE_FILE:
            ydl_opts["cookiefile"] = YT_COOKIE_FILE
        with YoutubeDL(ydl_opts) as ydl:
            return ydl.extract_info(url, download=False)

    try:
        info = await loop.run_in_executor(None, _extract)
    except Exception as e:
        msg = str(e)
        if ("Sign in to confirm you’re not a bot" in msg) or \
           ("Sign in to confirm you're not a bot" in msg):
            raise Exception(
                "Ye YouTube video login/cookies ke bina download nahi ho sakta.\n"
                "Agar owner ne YT_COOKIES env set kiya ho to hi aise videos aayenge."
            )
        if "Requested format is not available" in msg:
            raise Exception(
                "Is YouTube video ke liye requested format available nahi hai.\n"
                "Koi aur quality/normal public video try karo."
            )
        raise Exception(f"YouTube se info nahi mil paayi: {msg}")

    formats = info.get("formats") or []
    if not formats:
        raise Exception("YouTube ne koi formats return nahi kiye.")

    best = None  # (score, format_dict)
    for f in formats:
        url_f = f.get("url")
        if not url_f:
            continue

        score = 0
        if f.get("acodec") != "none" and f.get("vcodec") != "none":
            score += 1000
        if f.get("ext") == "mp4":
            score += 100
        try:
            score += int(f.get("height") or 0)
        except Exception:
            pass

        if (best is None) or (score > best[0]):
            best = (score, f)

    if not best:
        raise Exception("YouTube ka koi usable direct format nahi mila.")

    f = best[1]
    direct_url = f["url"]
    headers = f.get("http_headers") or info.get("http_headers") or {}

    ext = f.get("ext") or "mp4"
    title = info.get("title") or "YouTube_Video"
    safe_title = "".join(c for c in title if c not in r'\/:*?\"<>|')
    filename = f"{safe_title}.{ext}"

    return direct_url, filename, headers


async def download_media(url, status_msg, uid):
    kind = url_type(url)
    fname = make_name(url)
    dest = os.path.join(DOWNLOAD_DIR, fname)
    is_video = os.path.splitext(fname)[1].lower() in [".mp4", ".mkv", ".webm", ".mov"]

    if kind == "mega":
        raise Exception("Mega links abhi supported nahi hain.")

    if kind == "m3u8":
        title = os.path.basename(dest) or "m3u8_video.mp4"
        dest = os.path.join(DOWNLOAD_DIR, f"m3u8_{int(time.time())}.mp4")
        dest = await download_m3u8(url, dest, status_msg, uid, title)
        is_video = True
        fname = os.path.basename(dest)

    elif kind == "yt":
        yt_url, yt_name, yt_headers = await get_youtube_direct(url)
        dest = os.path.join(DOWNLOAD_DIR, yt_name)
        title = yt_name
        dest = await download_direct(
            yt_url,
            dest,
            status_msg,
            uid,
            title,
            headers=yt_headers,
        )
        is_video = True
        fname = yt_name

    else:
        title = fname
        dest = await download_direct(url, dest, status_msg, uid, title)

    return dest, fname, is_video


async def upload_media(client, m, path, title, is_video, u, status_msg):
    start = time.time()

 start = time.time()
last_edit = {"t": 0.0}  # closure ke liye dict

async def progress(current, total):
    now = time.time()
    # Agar last edit se PROGRESS_INTERVAL second nahi hue, to skip karo
    if now - last_edit["t"] < PROGRESS_INTERVAL:
        return
    last_edit["t"] = now

    txt = progress_text(
        title,
        current,
        total,
        start,
        "to Telegram",
    )
    try:
        await status_msg.edit_text(txt)
    except Exception:
        pass

caption = title
extra = u.get("caption") or ""
if extra:
    caption += f"\n{extra}"

mode = u.get("upload_mode", "video")
as_video = is_video and mode == "video"
if as_video:
    sent = await m.reply_video(
        path,
        caption=caption,
        progress=progress,
        reply_markup=owner_button(),
    )
else:
    sent = await m.reply_document(
        path,
        caption=caption,
        progress=progress,
        reply_markup=owner_button(),
    )
return sent


async def save_and_log(m, path, title, url, sent):
    try:
        media = sent.video or sent.document
        fid = media.file_id if media else None
        size = media.file_size if media else None
        mime = media.mime_type if media else None
        files.insert_one(
            {
                "title": title,
                "file_id": fid,
                "size": size,
                "mime_type": mime,
                "uploader": m.from_user.id,
                "time": int(time.time()),
                "is_video": bool(sent.video),
                "url": url,
            }
        )
        cap = (
            "📥 **New Download**\n"
            f"👤 [{m.from_user.first_name}](tg://user?id={m.from_user.id})\n"
            f"📝 `{title}`\n"
            f"🔗 `{url}`\n"
            f"📦 {size} bytes"
        )
        if fid:
            if sent.video:
                await bot.send_video(
                    LOGS_CHANNEL,
                    fid,
                    caption=cap,
                    reply_markup=owner_button(),
                )
            else:
                await bot.send_document(
                    LOGS_CHANNEL,
                    fid,
                    caption=cap,
                    reply_markup=owner_button(),
                )
        else:
            await bot.send_message(LOGS_CHANNEL, cap)
    except Exception as e:
        print("log error:", e)
        await log_text(f"Log error: {e}")


async def wrong_link(m):
    await m.reply_text(
        "❌ Yeh link support nahi hai.\n\n"
        "✅ Supported:\n"
        "• Direct HTTP/HTTPS (mp4, mkv, zip, etc.)\n"
        "• `.m3u8` HLS stream links (non-DRM)\n"
        "• YouTube links (public / cookie-supported)\n\n"
        "⚠️ Login-required / DRM / HTML pages mat bhejo."
    )


async def handle_url(client, m):
    if not await ensure_subscribed(client, m):
        return

    url = m.text.strip()
    if not is_url(url):
        return await wrong_link(m)

    uid = m.from_user.id
    u = get_user(uid)
    refresh_quota(u)

    if ACTIVE_TASKS.get(uid):
        return await m.reply_text(
            "⏳ Pehle wala task chal raha hai.\n"
            "Agar rokna hai to /cancel likho."
        )

    if not is_premium(u) and u.get("daily_used", 0) >= DAILY_FREE_LIMIT:
        return await m.reply_text(
            "🛑 Aaj ka free limit (5 downloads) khatam ho gaya.\n\n"
            "💎 Premium log unlimited download kar sakte hain.\n"
            "Apna plan dekhne ke liye `/plan` use karo."
        )

    ACTIVE_TASKS[uid] = True
    status = await m.reply_text("🔁 Link check ho raha hai…")
    path = None

    try:
        path, title, is_video = await download_media(url, status, uid)
        if not ACTIVE_TASKS.get(uid):
            raise Exception("Task cancelled")

        await status.edit_text("📤 Telegram pe upload ho raha hai…")
        u = get_user(uid)
        sent = await upload_media(client, m, path, title, is_video, u, status)
        await save_and_log(m, path, title, url, sent)

        now = int(time.time())
        upd = {
            "last_seen": now,
            "total_tasks": u.get("total_tasks", 0) + 1,
        }
        if not is_premium(u):
            upd["daily_used"] = u.get("daily_used", 0) + 1
        users.update_one({"_id": uid}, {"$set": upd})

        await status.edit_text(
            "✅ Task complete.\n\n"
            "Flood se bachne ke liye agla task 15 sec baad shuru karein."
        )
        await asyncio.sleep(15)
        await status.delete()
    except Exception as e:
        print("process error:", e)
        await log_text(f"Error for {m.from_user.id}: {e}")
        try:
            await status.edit_text(f"❌ Error: `{e}`")
        except Exception:
            pass
    finally:
        ACTIVE_TASKS.pop(uid, None)
        if path and os.path.exists(path):
            try:
                os.remove(path)
            except Exception:
                pass

# ===== GF CHAT (Gemini) =====

async def ai_gf_reply(user_gender: str, text: str) -> str:
    if not GEMINI_API_KEY:
        raise Exception("GF chat disabled by owner (GEMINI_API_KEY set nahi hai).")

    if user_gender == "male":
        persona = (
            "a cute, romantic, caring GIRLFRIEND talking to her boyfriend. "
            "She is female, pyari, thodi flirty, but respectful."
        )
    elif user_gender == "female":
        persona = (
            "a charming, romantic, caring BOYFRIEND talking to his girlfriend. "
            "He is male, sweet, thoda flirty, but respectful."
        )
    else:
        persona = (
            "a romantic, caring, respectful partner. "
            "Tone is sweet, thoda flirty, but always safe and within rules."
        )

    prompt = (
        f"Tum {persona}\n"
        "Hinglish (Hindi + thoda English) me short replies do.\n"
        "No explicit adult content, no abuse, no illegal topics.\n"
        "Thoda romantic, thoda naughty, but safe and positive.\n\n"
        f"User: {text}\n"
        "Reply (GF/BF):"
    )

    loop = asyncio.get_running_loop()

    def _call():
        model = genai.GenerativeModel(GEMINI_MODEL)
        resp = model.generate_content(prompt)
        # google-generativeai response
        if hasattr(resp, "text") and resp.text:
            return resp.text.strip()
        return "Aaj thoda network slow hai, baad me try karo na jaanu 💕"

    return await loop.run_in_executor(None, _call)


# ===== COMMANDS =====

@bot.on_message(filters.command("start") & filters.private)
async def start_cmd(client, m):
    if not await ensure_subscribed(client, m):
        return
    get_user(m.from_user.id)
    await log_text(f"#START from {m.from_user.id} ({m.from_user.first_name})")
    txt = (
        "🌸 **Welcome to SERENA URL Downloader**\n\n"
        "🔗 Mujhe koi bhi direct URL, `.m3u8` ya YouTube link bhejo,\n"
        "main usse download karke Telegram file/video bana dungi.\n\n"
        f"• Free Users: {DAILY_FREE_LIMIT} downloads / day\n"
        "• Premium Users: Unlimited\n\n"
        "💞 GF/BF Chat mode bhi hai (Gemini API se).\n"
        "Use: `/gfon`, `/gfoff`, `/gfhelp`, `/gender`.\n\n"
        "ℹ️ Full guide ke liye `/help` dekho."
    )
    await m.reply_text(txt, reply_markup=main_buttons())


@bot.on_message(filters.command("help") & filters.private)
async def help_cmd(client, m):
    if not await ensure_subscribed(client, m):
        return
    txt = (
        "🌸 **SERENA – URL Downloader + GF/BF Chat (Gemini)**\n\n"
        "🧿 **Main Kya Kar Sakti Hoon**\n"
        "• Direct downloadable links (mp4, mkv, zip, etc.) se file laati hoon.\n"
        "• `.m3u8` HLS streams ko MP4 video me convert karti hoon (non‑DRM).\n"
        "• YouTube videos ko best possible quality me download karti hoon "
        "(jitna YouTube allow kare – kuch videos 403/blocked bhi ho sakte hain).\n"
        "• Har upload database me save hota hai, baad me `/file` se mil jata hai.\n"
        "• Optional: GF/BF style chat mode (Gemini API se).\n\n"
        "💫 **Limits & Premium**\n"
        f"• Free Users: {DAILY_FREE_LIMIT} downloads / day\n"
        "• Premium Users: Unlimited downloads\n"
        "• Owner `/premium 123456789 12` kare to user ko 22 din premium milta hai.\n\n"
        "🛠 **User Commands**\n"
        "`/start` – welcome menu\n"
        "`/help` – yeh guide\n"
        "`/settings` – upload mode + extra caption set karo\n"
        "`/file Avengers` – title se saved files search karo\n"
        "`/plan` – apna plan, limit aur total tasks dekho\n"
        "`/cancel` – current download/ upload ko roko\n"
        "`/gender male` / `female` / `other` – apna gender set karo GF/BF chat ke liye\n"
        "`/gfon` – GF/BF chat mode ON (text pe baat-cheet)\n"
        "`/gfoff` – GF/BF chat mode OFF\n"
        "`/gfhelp` – GF/BF chat ka short guide\n\n"
        "👑 **Owner Commands**\n"
        "`/status` – system + users stats\n"
        "`/database` – MongoDB usage\n"
        "`/clear` – saari saved files clear\n"
        "`/broadcast Hello sabko` – mass message\n"
        "`/premium 123456789 12` – user ko 22 din premium\n\n"
        "🔰 **Use Examples**\n"
        "• Direct: `https://example.com/video.mp4`\n"
        "• YouTube: `https://youtu.be/abc123`\n"
        "• Stream: `https://site.com/hls/index.m3u8`\n\n"
        "GF/BF chat: `/gender male` (ya female), phir `/gfon` likho, "
        "ab normal text bhejo (URL nahi), main romantic style me reply dungi 😚"
    )
    await m.reply_text(txt)


@bot.on_message(filters.command("gfhelp") & filters.private)
async def gfhelp_cmd(client, m):
    if not await ensure_subscribed(client, m):
        return
    if not GEMINI_API_KEY:
        return await m.reply_text(
            "GF/BF chat mode abhi disabled hai (owner ne GEMINI_API_KEY set nahi kiya)."
        )
    await m.reply_text(
        "💞 **GF/BF Chat Mode (Gemini)**\n\n"
        "1️⃣ `/gender male` – agar aap ladke ho (bot GF banega)\n"
        "2️⃣ `/gender female` – agar aap ladki ho (bot BF banega)\n"
        "3️⃣ `/gfon` – GF/BF chat mode ON\n"
        "4️⃣ Normal text bhejo (URL nahi), main romantic reply dungi.\n"
        "5️⃣ `/gfoff` – mode OFF.\n\n"
        "Note: Koi bhi explicit adult / illegal / abusive content allowed nahi hai."
    )


@bot.on_message(filters.command("gender") & filters.private)
async def gender_cmd(client, m):
    if not await ensure_subscribed(client, m):
        return
    if len(m.command) < 2:
        return await m.reply_text(
            "Use: `/gender male` ya `/gender female` ya `/gender other`"
        )
    val = m.command[1].lower()
    if val not in ["male", "female", "other"]:
        return await m.reply_text(
            "Invalid gender.\nAllowed: `male`, `female`, `other`"
        )
    users.update_one({"_id": m.from_user.id}, {"$set": {"gender": val}})
    await m.reply_text(f"✅ Gender set to **{val}**.")


@bot.on_message(filters.command("gfon") & filters.private)
async def gfon_cmd(client, m):
    if not await ensure_subscribed(client, m):
        return
    if not GEMINI_API_KEY:
        return await m.reply_text(
            "GF/BF chat mode abhi disabled hai (owner ne GEMINI_API_KEY set nahi kiya)."
        )
    users.update_one({"_id": m.from_user.id}, {"$set": {"gf_mode": True}})
    await m.reply_text(
        "💞 GF/BF chat mode **ON**.\n\n"
        "Apna gender `/gender` se set karo (male/female),\n"
        "phir normal text bhejo (URL nahi)."
    )


@bot.on_message(filters.command("gfoff") & filters.private)
async def gfoff_cmd(client, m):
    if not await ensure_subscribed(client, m):
        return
    users.update_one({"_id": m.from_user.id}, {"$set": {"gf_mode": False}})
    await m.reply_text("GF/BF chat mode **OFF** kar diya gaya.")


@bot.on_message(filters.command("settings") & filters.private)
async def settings_cmd(client, m):
    if not await ensure_subscribed(client, m):
        return
    u = get_user(m.from_user.id)
    mode = u.get("upload_mode", "video")
    cap = u.get("caption") or "_No custom caption set._"
    await m.reply_text(
        f"⚙️ **Upload Settings**\n\n"
        f"Mode: **{'Video' if mode == 'video' else 'Document'}**\n"
        f"Caption:\n{cap}",
        reply_markup=settings_keyboard(mode),
    )


@bot.on_callback_query()
async def callbacks(client, cq):
    uid = cq.from_user.id
    get_user(uid)
    data = cq.data

    if data == "set_vid":
        users.update_one({"_id": uid}, {"$set": {"upload_mode": "video"}})
        await cq.message.edit_reply_markup(settings_keyboard("video"))
        await cq.answer("✅ Ab sab uploads Video format me aayenge.")
    elif data == "set_doc":
        users.update_one({"_id": uid}, {"$set": {"upload_mode": "doc"}})
        await cq.message.edit_reply_markup(settings_keyboard("doc"))
        await cq.answer("✅ Ab sab uploads Document format me aayenge.")
    elif data == "add_cap":
        AWAITING_CAPTION.add(uid)
        await cq.answer()
        await cq.message.reply_text(
            "✏️ Apna custom caption bhejo (normal message)."
        )
    elif data == "clr_cap":
        users.update_one({"_id": uid}, {"$set": {"caption": ""}})
        await cq.answer("✅ Caption reset.")
        await cq.message.reply_text("Caption hata diya gaya.")
    else:
        await cq.answer()


@bot.on_message(filters.command(["file", "files"]) & filters.private)
async def file_cmd(client, m):
    if not await ensure_subscribed(client, m):
        return
    if len(m.command) < 2:
        return await m.reply_text("❌ Galat use.\nExample: `/file Avengers`")
    query = " ".join(m.command[1:]).strip()
    if not query:
        return await m.reply_text("❌ Galat use.\nExample: `/file Avengers`")

    results = list(
        files.find({"title": {"$regex": query, "$options": "i"}}).limit(30)
    )
    if not results:
        return await m.reply_text(
            "❌ File not found in database.\n\n"
            "Try:\n"
            "• Aur chhota / alag keyword\n"
            "• Spelling check karo\n"
            "Ho sakta hai file abhi upload na hui ho."
        )

    await m.reply_text(f"📂 {len(results)} file(s) mili, bhej raha hoon…")

    for doc in results:
        fid = doc.get("file_id")
        if not fid:
            continue
        cap = doc.get("title", "")
        try:
            if doc.get("is_video"):
                await m.reply_video(
                    fid,
                    caption=cap,
                    reply_markup=owner_button(),
                )
            else:
                await m.reply_document(
                    fid,
                    caption=cap,
                    reply_markup=owner_button(),
                )
        except Exception as e:
            print("send db error:", e)
            await log_text(f"DB send error: {e}")
        await asyncio.sleep(1)


@bot.on_message(filters.command("cancel") & filters.private)
async def cancel_cmd(client, m):
    uid = m.from_user.id
    if not ACTIVE_TASKS.get(uid):
        return await m.reply_text(
            "❌ Koi active task nahi hai.\n"
            "Example: URL bhejne ke baad agar rokna ho to /cancel likho."
        )
    ACTIVE_TASKS[uid] = False
    await m.reply_text("⛔ Current task cancel kar diya gaya.")


@bot.on_message(filters.command("plan") & filters.private)
async def plan_cmd(client, m):
    if not await ensure_subscribed(client, m):
        return
    u = get_user(m.from_user.id)
    refresh_quota(u)
    prem = is_premium(u)

    if prem:
        left = u["premium_until"] - int(time.time())
        if left < 0:
            left = 0
        days = left // 86400
        hours = (left % 86400) // 3600
        remain = f"{days}d {hours}h"
    else:
        remain = "No premium"

    used = u.get("daily_used", 0)
    total = u.get("total_tasks", 0)

    txt = (
        "📊 **Your Plan**\n\n"
        f"Type: **{'Premium' if prem else 'Free'}**\n"
        f"Premium Left: {remain}\n"
        f"Daily Limit: {'Unlimited' if prem else f'{used}/{DAILY_FREE_LIMIT}'}\n"
        f"Total Tasks: {total}"
    )
    await m.reply_text(txt)


@bot.on_message(filters.command("premium") & owner_filter() & filters.private)
async def premium_cmd(client, m):
    if len(m.command) < 3:
        return await m.reply_text(
            "Use: `/premium <user_id> <days>`\n"
            "Example: `/premium 123456789 12`"
        )
    try:
        target = int(m.command[1])
        base_days = int(m.command[2])
    except Exception:
        return await m.reply_text(
            "User id ya days number sahi do.\n"
            "Example: `/premium 123456789 12`"
        )
    extra = 10
    total_days = base_days + extra
    until = int(time.time()) + total_days * 86400
    get_user(target)
    users.update_one(
        {"_id": target}, {"$set": {"premium_until": until}}, upsert=True
    )
    await m.reply_text(f"✅ `{target}` ko {total_days} din ka Premium de diya.")


@bot.on_message(filters.command("status") & owner_filter() & filters.private)
async def status_cmd(client, m):
    start = time.time()
    msg = await m.reply_text("⏳ Status nikal raha hoon…")
    latency = (time.time() - start) * 1000

    total = users.count_documents({})
    three_days_ago = int(time.time()) - 3 * 86400
    active = users.count_documents({"last_seen": {"$gte": three_days_ago}})
    blocked = users.count_documents({"blocked": True})

    ram = psutil.virtual_memory().percent
    cpu = psutil.cpu_percent(interval=1.0)
    free_mb = psutil.disk_usage("/").free // (1024 * 1024)
    speed = (1000 / latency) if latency > 0 else 0

    txt = (
        "📊 **#STATUS**\n\n"
        f"👤 *Total Users:* {total}\n"
        f"🟢 *Active (3 days):* {active}\n"
        f"🚫 *Blocked:* {blocked}\n"
        f"🧠 *RAM:* {ram:.1f}%\n"
        f"🖥 *CPU:* {cpu:.1f}%\n"
        f"💾 *Storage Free:* {free_mb} MB\n"
        f"⏳ *Ping:* {int(latency)} ms 😚\n"
        f"🤗 *SPEED:* {speed:.2f} req/s"
    )
    await msg.edit_text(txt)


@bot.on_message(filters.command("database") & owner_filter() & filters.private)
async def database_cmd(client, m):
    stats = db.command("dbstats")
    used_mb = stats.get("storageSize", 0) / (1024 * 1024)
    total_files = files.count_documents({})
    free_mb = max(0, 512 - used_mb)
    txt = (
        "🗄 **Mongo DB Usage**\n\n"
        f"📦 Used : {used_mb:.2f} MB\n"
        f"💾 Free : {free_mb:.2f} MB (approx)\n"
        f"🧮 Total File : {total_files}"
    )
    await m.reply_text(txt)


@bot.on_message(filters.command("clear") & owner_filter() & filters.private)
async def clear_cmd(client, m):
    files.delete_many({})
    await m.reply_text("🧹 Files collection clear ho gaya.")


@bot.on_message(filters.command("broadcast") & owner_filter() & filters.private)
async def broadcast_cmd(client, m):
    if m.reply_to_message:
        content = m.reply_to_message.text or m.reply_to_message.caption
    elif len(m.command) > 1:
        content = m.text.split(" ", 1)[1]
    else:
        return await m.reply_text(
            "Use: `/broadcast <text>`\n"
            "Ya kisi message ko reply karke `/broadcast` likho."
        )

    if not content:
        return await m.reply_text("Khaali message broadcast nahi hoga.")

    msg = await m.reply_text("📣 Broadcast start ho gaya…")
    cur = users.find({}, {"_id": 1})
    sent = failed = 0

    for doc in cur:
        uid = doc["_id"]
        try:
            await client.send_message(uid, content)
            sent += 1
        except (UserIsBlocked, InputUserDeactivated):
            users.update_one({"_id": uid}, {"$set": {"blocked": True}})
            failed += 1
        except FloodWait as e:
            await asyncio.sleep(e.value)
        except Exception:
            failed += 1
        await asyncio.sleep(0.05)

    await msg.edit_text(f"✅ Broadcast done.\nSent: {sent}\nFailed: {failed}")


@bot.on_message(
    filters.private
    & filters.text
    & ~filters.command(
        [
            "start",
            "help",
            "settings",
            "file",
            "files",
            "cancel",
            "plan",
            "premium",
            "status",
            "database",
            "clear",
            "broadcast",
            "gfon",
            "gfoff",
            "gfhelp",
            "gender",
        ]
    )
)
async def text_handler(client, m):
    if not await ensure_subscribed(client, m):
        return

    uid = m.from_user.id
    text = m.text.strip()

    if uid in AWAITING_CAPTION:
        users.update_one({"_id": uid}, {"$set": {"caption": text}})
        AWAITING_CAPTION.discard(uid)
        return await m.reply_text("✅ Caption save ho gaya.")

    if is_url(text):
        return await handle_url(client, m)

    u = get_user(uid)
    if GEMINI_API_KEY and u.get("gf_mode"):
        try:
            reply = await ai_gf_reply(u.get("gender", "unknown"), text)
            await m.reply_text(reply)
        except Exception:
            await m.reply_text(
                "GF/BF chat abhi available nahi hai.\n"
                "Owner ka Gemini API key / billing issue ho sakta hai."
            )
        return

    await wrong_link(m)


if __name__ == "__main__":
    threading.Thread(target=run_flask).start()
    bot.run()
