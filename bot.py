#!/usr/bin/env python3
import os
import asyncio
import logging
import tempfile
import traceback
from pathlib import Path
from datetime import datetime

from yt_dlp import YoutubeDL
from pyrogram import Client, filters
from pyrogram.types import Message
import motor.motor_asyncio

# --- CONFIG (fill on Render env or use defaults provided) ---
BOT_TOKEN = os.environ.get("BOT_TOKEN")
API_ID = int(os.environ.get("API_ID", "0"))
API_HASH = os.environ.get("API_HASH")
MONGO_URL = os.environ.get("MONGO_URL")  # optional

OWNER_ID = int(os.environ.get("OWNER_ID", "1598576202"))  # provided
FORCE_SUB = os.environ.get("FORCE_SUB", "https://t.me/serenaunzipbot")  # provided
LOGS_CHANNEL = int(os.environ.get("LOGS_CHANNEL", "-1003286415377"))  # provided

# safety / limits
TMP_DIR = Path("/tmp/tdl_bot")
TMP_DIR.mkdir(parents=True, exist_ok=True)
YTDL_OUTPUT = str(TMP_DIR / "%(id)s.%(ext)s")
MAX_FILESIZE_BYTES = int(os.environ.get("MAX_FILESIZE_BYTES", 0))  # 0=unlimited, Render beware

# logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# --- DB (optional) ---
mongo = None
if MONGO_URL:
    try:
        mongo = motor.motor_asyncio.AsyncIOMotorClient(MONGO_URL).get_default_database()
    except Exception as e:
        logger.exception("Mongo init failed: %s", e)
        mongo = None

# --- Pyrogram client ---
app = Client(
    "direct_downloader",
    bot_token=BOT_TOKEN,
    api_id=API_ID,
    api_hash=API_HASH,
    workers=16,  # Pyrogram internal threads; does not use external worker services
)

# utility: send log to logs channel
async def send_log(text: str):
    try:
        await app.send_message(LOGS_CHANNEL, text)
    except Exception:
        logger.exception("Failed to send log to channel")

# check if user is member of FORCE_SUB (works when FORCE_SUB is a channel username/link)
async def enforce_forcesub(user_id: int) -> bool:
    # if FORCE_SUB is a full url like https://t.me/serenaunzipbot use last part
    target = FORCE_SUB.strip()
    if target.startswith("https://t.me/"):
        target = target.replace("https://t.me/", "")
    if target.startswith("@"):
        target = target[1:]
    try:
        member = await app.get_chat_member(target, user_id)
        if member and member.status not in ("left", "kicked"):
            return True
        return False
    except Exception:
        # could not verify -> treat as not subscribed
        return False

# download via yt-dlp
async def download_media(url: str, message: Message):
    ydl_opts = {
        "outtmpl": YTDL_OUTPUT,
        "format": "bestvideo+bestaudio/best",
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "merge_output_format": "mp4",
        # progress hook to optionally report
    }

    def progress_hook(d):
        # limited hook: send update logs to logs channel
        status = d.get("status")
        if status == "downloading":
            pct = d.get("_percent_str", "").strip()
            speed = d.get("_speed_str", "").strip()
            eta = d.get("_eta_str", "").strip()
            asyncio.get_event_loop().create_task(
                send_log(
                    f"Downloading {url}\n{pct} {speed} ETA {eta}\nUser: {message.from_user.id}"
                )
            )
        elif status == "finished":
            asyncio.get_event_loop().create_task(
                send_log(f"Finished download: {d.get('filename')}")
            )

    ydl_opts["progress_hooks"] = [progress_hook]

    loop = asyncio.get_event_loop()
    try:
        with YoutubeDL(ydl_opts) as ydl:
            info = await loop.run_in_executor(None, lambda: ydl.extract_info(url, download=True))
            # info may be dict for single or playlist; since noplaylist True expect dict
            if "entries" in info:
                info = info["entries"][0]
            filename = ydl.prepare_filename(info)
            return filename, info
    except Exception as e:
        raise

# send file and cleanup
async def send_and_cleanup(chat_id: int, file_path: str, caption: str = ""):
    try:
        if MAX_FILESIZE_BYTES and Path(file_path).stat().st_size > MAX_FILESIZE_BYTES:
            raise ValueError("File exceeds configured max filesize.")

        await app.send_document(
            chat_id,
            file_path,
            caption=caption,
            progress=lambda d, t: None,  # minimal; logs already go to logs channel
        )
    finally:
        try:
            Path(file_path).unlink(missing_ok=True)
        except Exception:
            logger.exception("Cleanup failed for %s", file_path)

# handlers
@app.on_message(filters.private & filters.command("start"))
async def start_cmd(_, m: Message):
    await m.reply_text("Send me a direct or stream/download link. I will download & send you the video.")

@app.on_message(filters.private & ~filters.service)
async def handle_link(_, m: Message):
    user_id = m.from_user.id
    text = (m.text or m.caption or "").strip()
    if not text:
        await m.reply_text("Please send a link (URL).")
        return

    # enforce force sub
    ok = await enforce_forcesub(user_id)
    if not ok:
        try:
            await m.reply_text(
                f"आपको पहले हमारे चैनल/बोट को join करना होगा:\n{FORCE_SUB}\nJoin करके फिर से कोशिश करें."
            )
        except Exception:
            pass
        return

    await m.reply_text("Processing your link...")

    # log start
    await send_log(f"User {user_id} requested: {text} at {datetime.utcnow().isoformat()}")

    # download
    try:
        path, info = await download_media(text, m)
    except Exception as e:
        traceback_str = traceback.format_exc()
        await send_log(f"Download failed for {text}\nError: {e}\n{traceback_str}")
        await m.reply_text("Download failed. Check logs or try another link.")
        return

    # optional DB increment
    try:
        if mongo:
            await mongo.downloads.update_one(
                {"user_id": user_id}, {"$inc": {"count": 1}, "$set": {"last": datetime.utcnow()}}, upsert=True
            )
    except Exception:
        logger.exception("Mongo write failed")

    # send file
    try:
        size = Path(path).stat().st_size
        caption = f"{info.get('title','file')} — {round(size/1024/1024,2)} MB"
        await send_and_cleanup(user_id, path, caption=caption)
        await send_log(f"Sent file to {user_id}: {path}")
    except Exception as e:
        traceback_str = traceback.format_exc()
        await send_log(f"Send failed for {path}\nError: {e}\n{traceback_str}")
        await m.reply_text("Failed to send file. Check logs.")
        # cleanup
        try:
            Path(path).unlink(missing_ok=True)
        except Exception:
            pass

# admin: /stats
@app.on_message(filters.user(OWNER_ID) & filters.command("stats"))
async def stats(_, m: Message):
    try:
        doc = await mongo.downloads.count_documents({}) if mongo else "no-mongo"
        await m.reply_text(f"DB downloads docs: {doc}")
    except Exception:
        await m.reply_text("No DB available or error.")

# start
if __name__ == "__main__":
    logger.info("Starting bot...")
    loop = asyncio.get_event_loop()
    try:
        app.run()
    except Exception:
        logger.exception("Bot crashed")
