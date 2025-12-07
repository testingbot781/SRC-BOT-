#!/usr/bin/env python3
import os
import asyncio
import tempfile
from pathlib import Path
from datetime import datetime
from yt_dlp import YoutubeDL
from pyrogram import Client, filters
import motor.motor_asyncio

BOT_TOKEN = os.environ.get("BOT_TOKEN")
API_ID = int(os.environ.get("API_ID"))
API_HASH = os.environ.get("API_HASH")
MONGO_URL = os.environ.get("MONGO_URL")

OWNER_ID = 1598576202
FORCE_SUB = "serenaunzipbot"
LOGS_CHANNEL = -1003286415377

TMP = Path("/tmp/dl")
TMP.mkdir(exist_ok=True)

mongo = motor.motor_asyncio.AsyncIOMotorClient(MONGO_URL).get_default_database()

app = Client("dlbot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)


async def log(text):
    try:
        await app.send_message(LOGS_CHANNEL, text)
    except:
        pass


async def force_check(uid):
    try:
        m = await app.get_chat_member(FORCE_SUB, uid)
        return m.status not in ("left", "kicked")
    except:
        return False


async def dl(url):
    out = str(TMP / "%(id)s.%(ext)s")
    y = YoutubeDL({"outtmpl": out, "format": "best", "quiet": True, "noplaylist": True})
    info = y.extract_info(url, download=True)
    if "entries" in info:
        info = info["entries"][0]
    return y.prepare_filename(info), info


@app.on_message(filters.command("start") & filters.private)
async def start(_, m):
    await m.reply("Send link.")


@app.on_message(filters.private & ~filters.service)
async def handle(_, m):
    uid = m.from_user.id
    url = m.text

    if not await force_check(uid):
        return await m.reply(f"Join first: https://t.me/{FORCE_SUB}")

    await log(f"{uid} -> {url}")

    try:
        fp, info = await asyncio.get_event_loop().run_in_executor(None, lambda: asyncio.run(dl(url)))
    except:
        await log("Download error")
        return await m.reply("Failed.")

    size = Path(fp).stat().st_size
    cap = f"{info.get('title','file')} — {round(size/1024/1024,2)}MB"

    try:
        await app.send_document(uid, fp, caption=cap)
        await log(f"Sent {fp}")
    except:
        await log("Send error")
        await m.reply("Send failed.")
    finally:
        Path(fp).unlink(missing_ok=True)

    try:
        await mongo.logs.update_one(
            {"uid": uid},
            {"$inc": {"count": 1}, "$set": {"last": datetime.utcnow()}},
            upsert=True,
        )
    except:
        pass


if __name__ == "__main__":
    app.run()
