import os
import time
import math
import asyncio
import psutil
import subprocess
from datetime import datetime, timedelta, date
from urllib.parse import urlparse

import aiohttp
from pymongo import MongoClient
from pyrogram import Client, filters, enums
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from pyrogram.errors import FloodWait, UserIsBlocked, InputUserDeactivated, RPCError

API_ID = int(os.environ["API_ID"])
API_HASH = os.environ["API_HASH"]
BOT_TOKEN = os.environ["BOT_TOKEN"]
MONGO_URL = os.environ["MONGO_URL"]

OWNER_IDS = {1598576202, 6518065496}
LOGS_CHANNEL = -1003286415377
FORCE_CH = "serenaunzipbot"
FORCE_LINK = "https://t.me/serenaunzipbot"
OWNER_CONTACT = "https://t.me/technicalserena"

mongo = MongoClient(MONGO_URL)
db = mongo["serena"]
users = db["users"]
files = db["files"]

DOWNLOAD_DIR = "downloads"
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

bot = Client(
    "serena-url-bot",
    api_id=API_ID,
    api_hash=API_HASH,
    bot_token=BOT_TOKEN,
    parse_mode=enums.ParseMode.MARKDOWN
)

ACTIVE_TASKS = {}
AWAITING_CAPTION = set()


def owner_only():
    return filters.user(list(OWNER_IDS))


def owner_button():
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton("💬 Contact Owner", url=OWNER_CONTACT)]]
    )


def settings_keyboard(mode: str):
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "🎥 Upload as Video" + (" ✅" if mode == "video" else ""),
                    callback_data="set_vid"
                )
            ],
            [
                InlineKeyboardButton(
                    "📄 Upload as Document" + (" ✅" if mode == "doc" else ""),
                    callback_data="set_doc"
                )
            ],
            [
                InlineKeyboardButton("➕ Add Caption", callback_data="add_cap"),
                InlineKeyboardButton("♻️ Reset Caption", callback_data="clr_cap")
            ]
        ]
    )


def get_or_create_user(user_id: int):
    u = users.find_one({"_id": user_id})
    now = int(time.time())
    if not u:
        u = {
            "_id": user_id,
            "joined_at": now,
            "last_seen": now,
            "premium_until": None,
            "daily_date": None,
            "daily_used": 0,
            "total_tasks": 0,
            "upload_mode": "video",
            "caption": "",
            "blocked": False
        }
        users.insert_one(u)
    else:
        users.update_one({"_id": user_id}, {"$set": {"last_seen": now}})
    return u


def refresh_daily_quota(u: dict):
    today = date.today().isoformat()
    if u.get("daily_date") != today:
        u["daily_date"] = today
        u["daily_used"] = 0
        users.update_one(
            {"_id": u["_id"]},
            {"$set": {"daily_date": today, "daily_used": 0}}
        )


def is_premium(u: dict) -> bool:
    until = u.get("premium_until")
    if not until:
        return False
    return until > int(time.time())


async def ensure_subscribed(client: Client, m):
    if m.chat.type != enums.ChatType.PRIVATE:
        return True
    try:
        member = await client.get_chat_member(FORCE_CH, m.from_user.id)
        if member.status in (
            enums.ChatMemberStatus.LEFT,
            enums.ChatMemberStatus.BANNED
        ):
            raise RPCError("not joined")
        return True
    except Exception:
        btn = InlineKeyboardMarkup(
            [[InlineKeyboardButton("📢 Join Channel", url=FORCE_LINK)]]
        )
        await m.reply_text(
            "⚠️ Bot use karne se pehle hamare channel ko join karein.",
            reply_markup=btn
        )
        return False


def is_url(text: str) -> bool:
    return text.startswith("http://") or text.startswith("https://")


def classify_url(url: str) -> str:
    u = url.lower()
    path = urlparse(u).path
    if "youtube.com" in u or "youtu.be" in u:
        return "youtube"
    if "mega.nz" in u:
        return "mega"
    if ".m3u8" in path or ".m3u8" in u:
        return "m3u8"
    return "direct"


def make_filename_from_url(url: str, default_ext="mp4") -> str:
    path = urlparse(url).path
    name = os.path.basename(path).split("?")[0].split("#")[0]
    if not name:
        name = f"file_{int(time.time())}.{default_ext}"
    if "." not in name:
        name = f"{name}.{default_ext}"
    return name


async def download_direct(url: str, dest: str, status_msg, user_id: int):
    async with aiohttp.ClientSession() as session:
        async with session.get(url) as resp:
            if resp.status != 200:
                raise Exception(f"HTTP {resp.status}")
            total = int(resp.headers.get("Content-Length", 0))
            downloaded = 0
            chunk_size = 1024 * 1024
            last_edit = time.time()
            with open(dest, "wb") as f:
                async for chunk in resp.content.iter_chunked(chunk_size):
                    if not ACTIVE_TASKS.get(user_id):
                        raise Exception("Task cancelled")
                    if not chunk:
                        continue
                    f.write(chunk)
                    downloaded += len(chunk)
                    if total > 0 and time.time() - last_edit > 3:
                        percent = int(downloaded * 100 / total)
                        filled = percent // 10
                        bar = "█" * filled + "░" * (10 - filled)
                        mb_done = downloaded / (1024 * 1024)
                        mb_total = total / (1024 * 1024)
                        await status_msg.edit_text(
                            f"⬇️ Downloading {percent}%\n"
                            f"[{bar}] {mb_done:.1f}/{mb_total:.1f} MB"
                        )
                        last_edit = time.time()
    return dest


async def run_ffmpeg_m3u8(url: str, dest: str, status_msg, user_id: int):
    await status_msg.edit_text("🎞 Downloading `.m3u8` stream via ffmpeg…")
    cmd = [
        "ffmpeg",
        "-i",
        url,
        "-c",
        "copy",
        "-bsf:a",
        "aac_adtstoasc",
        dest
    ]
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE
    )
    await proc.communicate()
    if not ACTIVE_TASKS.get(user_id):
        raise Exception("Task cancelled")
    if proc.returncode != 0 or not os.path.exists(dest):
        raise Exception("ffmpeg failed")
    return dest


async def run_ytdlp(url: str, dest: str, status_msg, user_id: int):
    await status_msg.edit_text("🎬 Downloading YouTube video via yt-dlp…")
    cmd = [
        "yt-dlp",
        "-f",
        "best",
        "-o",
        dest,
        url
    ]
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE
    )
    await proc.communicate()
    if not ACTIVE_TASKS.get(user_id):
        raise Exception("Task cancelled")
    if proc.returncode != 0 or not os.path.exists(dest):
        raise Exception("yt-dlp failed")
    return dest


async def download_media(url: str, status_msg, user_id: int):
    kind = classify_url(url)
    fname = make_filename_from_url(url)
    ext = os.path.splitext(fname)[1].lower()
    is_video = ext in [".mp4", ".mkv", ".webm", ".mov"]
    dest = os.path.join(DOWNLOAD_DIR, fname)

    if kind == "mega":
        raise Exception("Mega links abhi supported nahi hain.")
    if kind == "m3u8":
        dest = os.path.join(DOWNLOAD_DIR, f"m3u8_{int(time.time())}.mp4")
        dest = await run_ffmpeg_m3u8(url, dest, status_msg, user_id)
        is_video = True
    elif kind == "youtube":
        dest = os.path.join(DOWNLOAD_DIR, f"yt_{int(time.time())}.mp4")
        dest = await run_ytdlp(url, dest, status_msg, user_id)
        is_video = True
        fname = os.path.basename(dest)
    else:
        await status_msg.edit_text("⬇️ Starting direct download…")
        dest = await download_direct(url, dest, status_msg, user_id)

    return dest, os.path.basename(dest), is_video


async def upload_with_progress(
    client: Client,
    m,
    path: str,
    title: str,
    is_video: bool,
    user_settings: dict,
    status_msg
):
    start = time.time()
    last_edit = 0

    async def progress(current, total):
        nonlocal last_edit
        now = time.time()
        if now - last_edit < 3:
            return
        last_edit = now
        if total == 0:
            return
        percent = int(current * 100 / total)
        filled = percent // 10
        bar = "█" * filled + "░" * (10 - filled)
        mb_done = current / (1024 * 1024)
        mb_total = total / (1024 * 1024)
        speed = current / (1024 * 1024 * max(1e-3, now - start))
        eta = (total - current) / (max(1, current) / max(1e-3, now - start))
        try:
            await status_msg.edit_text(
                f"📤 Uploading {percent}%\n"
                f"[{bar}] {mb_done:.1f}/{mb_total:.1f} MB\n"
                f"⚡ {speed:.1f} MB/s | ⏳ {int(eta)}s"
            )
        except Exception:
            pass

    cap_extra = user_settings.get("caption") or ""
    final_caption = title
    if cap_extra:
        final_caption += f"\n{cap_extra}"

    ext = os.path.splitext(path)[1].lower()
    as_video = user_settings.get("upload_mode", "video") == "video" and is_video

    if as_video:
        sent = await m.reply_video(
            path,
            caption=final_caption,
            progress=progress,
            reply_markup=owner_button()
        )
    else:
        sent = await m.reply_document(
            path,
            caption=final_caption,
            progress=progress,
            reply_markup=owner_button()
        )
    return sent


async def log_download(m, path: str, title: str, url: str, sent_msg):
    try:
        doc = sent_msg.document or sent_msg.video
        fid = doc.file_id if doc else None
        size = doc.file_size if doc else None
        mime = doc.mime_type if doc else None
        files.insert_one(
            {
                "title": title,
                "file_id": fid,
                "size": size,
                "mime_type": mime,
                "uploader": m.from_user.id,
                "time": int(time.time()),
                "is_video": bool(sent_msg.video),
                "url": url
            }
        )
        caption = (
            f"📥 **New Download**\n"
            f"👤 User: [{m.from_user.first_name}](tg://user?id={m.from_user.id})\n"
            f"📝 Title: `{title}`\n"
            f"🔗 URL: `{url}`\n"
            f"📦 Size: {size} bytes\n"
        )
        if fid:
            if sent_msg.video:
                await bot.send_video(
                    LOGS_CHANNEL,
                    fid,
                    caption=caption,
                    reply_markup=owner_button()
                )
            else:
                await bot.send_document(
                    LOGS_CHANNEL,
                    fid,
                    caption=caption,
                    reply_markup=owner_button()
                )
        else:
            await bot.send_message(LOGS_CHANNEL, caption)
    except Exception as e:
        print("Log error:", e)


async def wrong_link_guide(m):
    await m.reply_text(
        "❌ Yeh link support nahi hai.\n\n"
        "✅ Supported links:\n"
        "• Direct downloadable HTTP/HTTPS (mp4, mkv, zip, etc.)\n"
        "• `.m3u8` HLS stream links\n"
        "• YouTube links (yt-dlp required)\n\n"
        "⚠️ Login-required pages, HTML pages ya ad-shortener links mat bhejo."
    )


async def process_url(client: Client, m):
    if not await ensure_subscribed(client, m):
        return

    url = m.text.strip()
    if not is_url(url):
        return await wrong_link_guide(m)

    u = get_or_create_user(m.from_user.id)
    refresh_daily_quota(u)

    if ACTIVE_TASKS.get(m.from_user.id):
        return await m.reply_text("⏳ Pehle wala task chal raha hai. /cancel use karein ya wait karein.")

    if not is_premium(u):
        used = u.get("daily_used", 0)
        if used >= 5:
            return await m.reply_text(
                f"🛑 Free limit khatam ho gaya.\n\n"
                f"📅 Aaj ke liye 5/5 downloads use ho chuke hain.\n"
                f"💎 Premium ke liye owner se contact karein."
            )

    ACTIVE_TASKS[m.from_user.id] = True
    status = await m.reply_text("🔁 Link process ho raha hai…")
    path = None
    try:
        path, title, is_video = await download_media(url, status, m.from_user.id)
        if not ACTIVE_TASKS.get(m.from_user.id):
            if path and os.path.exists(path):
                os.remove(path)
            return await status.edit_text("⛔ Task cancel kar diya gaya.")
        await status.edit_text("📤 Uploading to Telegram…")

        u = get_or_create_user(m.from_user.id)
        sent = await upload_with_progress(
            client, m, path, title, is_video, u, status
        )

        await log_download(m, path, title, url, sent)

        now_ts = int(time.time())
        if not is_premium(u):
            refresh_daily_quota(u)
            users.update_one(
                {"_id": u["_id"]},
                {
                    "$inc": {"daily_used": 1, "total_tasks": 1},
                    "$set": {"last_seen": now_ts}
                }
            )
        else:
            users.update_one(
                {"_id": u["_id"]},
                {
                    "$inc": {"total_tasks": 1},
                    "$set": {"last_seen": now_ts}
                }
            )

        await status.edit_text("✅ Task complete. Next task ke liye 15 sec wait karein.")
        await asyncio.sleep(15)
        await status.delete()
    except Exception as e:
        print("Process error:", e)
        try:
            await status.edit_text(f"❌ Error: {e}")
        except Exception:
            pass
    finally:
        ACTIVE_TASKS.pop(m.from_user.id, None)
        if path and os.path.exists(path):
            try:
                os.remove(path)
            except Exception:
                pass


@bot.on_message(filters.command("start") & filters.private)
async def start_cmd(client, m):
    if not await ensure_subscribed(client, m):
        return
    u = get_or_create_user(m.from_user.id)
    txt = (
        "🌸 **Welcome to SERENA URL Downloader**\n\n"
        "🧿 Mujhe direct URL (mp4/zip/etc.) ya `.m3u8` stream link bhejo.\n"
        "🎞 Main file download karke Telegram pe upload karungi.\n"
        "📦 Har file Logs me bhi save hoti hai.\n\n"
        "ℹ️ Guide ke liye /help use karein."
    )
    await m.reply_text(txt)


@bot.on_message(filters.command("help") & filters.private)
async def help_cmd(client, m):
    if not await ensure_subscribed(client, m):
        return
    txt = (
        "🌸 **How to Use SERENA**\n\n"
        "🧿 Send *direct URL* (mp4/zip/etc.) or `.m3u8` stream link.\n"
        "🎞 Watch animated ETA progress bar.\n"
        "📦 Files are sent to you + saved in Logs.\n\n"
        "⚙️ Commands:\n"
        "`/start` – welcome menu\n"
        "`/help` – this guide\n"
        "`/settings` – upload & caption mode\n"
        "`/file <word>` – search saved files\n"
        "`/status` – owner system stats\n"
        "`/database` – Mongo usage (Owner)\n"
        "`/clear` – reset database (Owner)\n"
        "`/broadcast <text>` – owner mass message\n"
        "`/cancel` – stop current task\n"
        "`/plan` – apna plan & limit dekho\n"
        "`/premium <user_id> <days>` – premium set (Owner)"
    )
    await m.reply_text(txt)


@bot.on_message(filters.command("settings") & filters.private)
async def settings_cmd(client, m):
    if not await ensure_subscribed(client, m):
        return
    u = get_or_create_user(m.from_user.id)
    mode = u.get("upload_mode", "video")
    cap = u.get("caption") or "_No custom caption set._"
    await m.reply_text(
        f"⚙️ **Upload Settings**\n\n"
        f"Mode: {'Video' if mode == 'video' else 'Document'}\n"
        f"Caption:\n{cap}",
        reply_markup=settings_keyboard(mode)
    )


@bot.on_callback_query()
async def cb_handler(client, cq):
    uid = cq.from_user.id
    u = get_or_create_user(uid)
    data = cq.data

    if data == "set_vid":
        users.update_one({"_id": uid}, {"$set": {"upload_mode": "video"}})
        await cq.message.edit_reply_markup(settings_keyboard("video"))
        await cq.answer("Upload as Video selected.")
    elif data == "set_doc":
        users.update_one({"_id": uid}, {"$set": {"upload_mode": "doc"}})
        await cq.message.edit_reply_markup(settings_keyboard("doc"))
        await cq.answer("Upload as Document selected.")
    elif data == "add_cap":
        AWAITING_CAPTION.add(uid)
        await cq.answer()
        await cq.message.reply_text("✏️ Apna custom caption bhejo. Yeh har file ke niche add hoga.")
    elif data == "clr_cap":
        users.update_one({"_id": uid}, {"$set": {"caption": ""}})
        await cq.answer("Caption reset ho gaya.")
        await cq.message.reply_text("✅ Caption clear kar diya gaya.")
    else:
        await cq.answer()


@bot.on_message(filters.command(["file", "files"]) & filters.private)
async def file_search(client, m):
    if not await ensure_subscribed(client, m):
        return
    if len(m.command) < 2:
        return await m.reply_text("Use: `/file <title>`", quote=True)
    query = " ".join(m.command[1:]).strip()
    if not query:
        return await m.reply_text("Use: `/file <title>`", quote=True)

    results = list(
        files.find({"title": {"$regex": query, "$options": "i"}}).limit(30)
    )
    if not results:
        return await m.reply_text(
            "❌ File not found in database.\n\n"
            "🔎 Try another title or different spelling.\n"
            "Maybe this file is not available yet."
        )

    await m.reply_text(f"📂 {len(results)} file(s) mil gayi, bhej raha hoon…")

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
                    reply_markup=owner_button()
                )
            else:
                await m.reply_document(
                    fid,
                    caption=cap,
                    reply_markup=owner_button()
                )
        except Exception as e:
            print("Send from DB error:", e)
        await asyncio.sleep(1)


@bot.on_message(filters.command("cancel") & filters.private)
async def cancel_cmd(client, m):
    uid = m.from_user.id
    if not ACTIVE_TASKS.get(uid):
        return await m.reply_text("❌ Koi active task nahi hai.")
    ACTIVE_TASKS[uid] = False
    await m.reply_text("⛔ Current task cancel kar diya gaya.")


@bot.on_message(filters.command("plan") & filters.private)
async def plan_cmd(client, m):
    if not await ensure_subscribed(client, m):
        return
    u = get_or_create_user(m.from_user.id)
    refresh_daily_quota(u)
    prem = is_premium(u)
    now = int(time.time())
    if prem:
        remain = u["premium_until"] - now
        if remain < 0:
            remain = 0
        days = remain // 86400
        hours = (remain % 86400) // 3600
        rem_txt = f"{days}d {hours}h"
    else:
        rem_txt = "No premium (Free Plan)"

    used = u.get("daily_used", 0)
    total_tasks = u.get("total_tasks", 0)

    txt = (
        "📊 **Your Plan**\n\n"
        f"👤 User: [{m.from_user.first_name}](tg://user?id={m.from_user.id})\n"
        f"💠 Type: {'Premium' if prem else 'Free'}\n"
        f"⏳ Remaining Premium: {rem_txt}\n"
        f"🎬 Daily Limit: {'Unlimited' if prem else f'{used}/5'}\n"
        f"⚙️ Total Tasks: {total_tasks}"
    )
    await m.reply_text(txt)


@bot.on_message(filters.command("clear") & owner_only() & filters.private)
async def clear_cmd(client, m):
    files.delete_many({})
    await m.reply_text("🧹 Files collection clear kar diya gaya.")


@bot.on_message(filters.command("broadcast") & owner_only() & filters.private)
async def broadcast_cmd(client, m):
    if len(m.command) < 2 and not m.reply_to_message:
        return await m.reply_text("Use: `/broadcast <text>` ya kisi message ko reply karke `/broadcast`.")
    if m.reply_to_message:
        text = m.reply_to_message.text or m.reply_to_message.caption
    else:
        text = m.text.split(" ", 1)[1]
    if not text:
        return await m.reply_text("Khaali message broadcast nahi ho sakta.")

    msg = await m.reply_text("📣 Broadcast started…")
    ids = users.find({}, {"_id": 1})
    sent = 0
    failed = 0
    for doc in ids:
        uid = doc["_id"]
        try:
            await client.send_message(uid, text)
            sent += 1
            await asyncio.sleep(0.05)
        except (UserIsBlocked, InputUserDeactivated):
            users.update_one({"_id": uid}, {"$set": {"blocked": True}})
            failed += 1
        except FloodWait as e:
            await asyncio.sleep(e.value)
        except Exception:
            failed += 1
    await msg.edit_text(f"✅ Broadcast complete.\nSent: {sent}\nFailed: {failed}")

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
            "status",
            "database",
            "clear",
            "broadcast",
            "cancel",
            "premium",
            "plan"
        ]
    )
)
async def text_handler(client, m):
    if not await ensure_subscribed(client, m):
        return

    uid = m.from_user.id
    if uid in AWAITING_CAPTION:
        cap = m.text.strip()
        users.update_one({"_id": uid}, {"$set": {"caption": cap}})
        AWAITING_CAPTION.discard(uid)
        return await m.reply_text("✅ Caption save ho gaya.")
    if is_url(m.text.strip()):
        return await process_url(client, m)
    await wrong_link_guide(m)


if __name__ == "__main__":
    bot.run()
