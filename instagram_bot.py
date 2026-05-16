from __future__ import annotations
import asyncio
import base64
import os
import random
import logging
from contextlib import asynccontextmanager
from datetime import datetime
from zoneinfo import ZoneInfo

import httpx
from fastapi import FastAPI
from supabase import create_client, Client

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

SUPABASE_URL     = os.getenv("SUPABASE_URL", "")
SUPABASE_KEY     = os.getenv("SUPABASE_SERVICE_KEY", "")
DEEPSEEK_KEY     = os.getenv("DEEPSEEK_API_KEY", "")
GOOGLE_KEY       = os.getenv("GOOGLE_API_KEY", "")
TG_TOKEN         = os.getenv("INSTAGRAM_BOT_TOKEN", "")
TG_OWNER_ID      = int(os.getenv("TELEGRAM_USER_ID", "7255533143"))
IG_ACCESS_TOKEN  = os.getenv("INSTAGRAM_ACCESS_TOKEN", "")
IG_ACCOUNT_ID    = os.getenv("INSTAGRAM_BUSINESS_ACCOUNT_ID", "")
BKK              = ZoneInfo("Asia/Bangkok")

sb: Client = None
pending: dict = {}
tg_offset: int = 0
last_posted_date: str = ""

def init_supabase():
    global sb
    sb = create_client(SUPABASE_URL, SUPABASE_KEY)
    log.info("Supabase ready")

def upload_image(image_bytes: bytes) -> str | None:
    try:
        filename = f"post_{datetime.now(BKK).strftime('%Y%m%d_%H%M%S')}.png"
        sb.storage.from_("instagram").upload(
            path=filename, file=image_bytes,
            file_options={"content-type": "image/png"},
        )
        return sb.storage.from_("instagram").get_public_url(filename)
    except Exception as e:
        log.error(f"Storage upload failed: {e}")
        return None

async def generate_image(prompt: str) -> tuple[bytes | None, str | None]:
    if not GOOGLE_KEY:
        return None, None
    try:
        url = (
            "https://generativelanguage.googleapis.com/v1beta/models/"
            f"imagen-3.0-generate-002:predict?key={GOOGLE_KEY}"
        )
        payload = {
            "instances": [{"prompt": prompt + ", Instagram-ready, no text overlay"}],
            "parameters": {"sampleCount": 1, "aspectRatio": "1:1", "safetyFilterLevel": "BLOCK_ONLY_HIGH"},
        }
        async with httpx.AsyncClient() as client:
            r = await client.post(url, json=payload, timeout=90)
            r.raise_for_status()
            b64 = r.json()["predictions"][0]["bytesBase64Encoded"]
            img = base64.b64decode(b64)
            log.info("Image generated via Google Imagen 3")
        return img, upload_image(img)
    except Exception as e:
        log.error(f"Google Imagen failed: {e}")
        return None, None

PROMPTS = {
    "meme": (
        "You are a meme creator for Lighthouse — a premium cannabis shop on Koh Samui.\n"
        "Write SHORT punchy Instagram captions (1-3 sentences, 2-3 emojis).\n"
        "Break the 'weed=lazy' stereotype — show athletes, creatives, productive people.\n"
        "Hashtags at end. English only."
    ),
    "active": (
        "You are a content creator for Lighthouse — cannabis shop on Koh Samui.\n"
        "Write inspiring captions about cannabis + active lifestyle (yoga, swim, gym, run).\n"
        "2-3 sentences, 3-4 emojis. Hashtags at end. English only."
    ),
    "educational": (
        "You are educational content creator for Lighthouse — cannabis shop on Koh Samui.\n"
        "Teach one cannabis fact (strains, terpenes, effects). 3-4 sentences, 2-3 emojis.\n"
        "Hashtags at end. English only."
    ),
}

async def generate_caption(theme: dict, feedback: str = "") -> str:
    if not DEEPSEEK_KEY:
        return theme.get("description", "")
    cat = theme.get("category", "educational")
    system = PROMPTS.get(cat, PROMPTS["educational"])
    user_msg = f"Theme: {theme['theme']}\nDescription: {theme['description']}\nHashtags: {theme['hashtags']}"
    if feedback:
        user_msg += f"\n\nREJECTED. Feedback: {feedback}\nRewrite accordingly."
    try:
        async with httpx.AsyncClient() as client:
            r = await client.post(
                "https://api.deepseek.com/chat/completions",
                headers={"Authorization": f"Bearer {DEEPSEEK_KEY}"},
                json={"model": "deepseek-chat", "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user_msg},
                ], "temperature": 0.9, "max_tokens": 250},
                timeout=30,
            )
            r.raise_for_status()
            return r.json()["choices"][0]["message"]["content"].strip()
    except Exception as e:
        log.error(f"DeepSeek failed: {e}")
        return theme.get("description", "")

async def tg(method: str, **kwargs) -> dict:
    async with httpx.AsyncClient() as client:
        r = await client.post(
            f"https://api.telegram.org/bot{TG_TOKEN}/{method}",
            json=kwargs, timeout=30,
        )
        return r.json()

async def tg_photo_bytes(image_bytes: bytes, caption: str, keyboard: dict) -> int | None:
    import json
    async with httpx.AsyncClient() as client:
        r = await client.post(
            f"https://api.telegram.org/bot{TG_TOKEN}/sendPhoto",
            data={"chat_id": str(TG_OWNER_ID), "caption": caption[:1024],
                  "parse_mode": "HTML", "reply_markup": json.dumps(keyboard)},
            files={"photo": ("post.png", image_bytes, "image/png")},
            timeout=60,
        )
        return r.json().get("result", {}).get("message_id")

KEYBOARD = {"inline_keyboard": [[
    {"text": "✅ Опубликовать", "callback_data": "approve"},
    {"text": "♻️ Переделать",  "callback_data": "redo"},
    {"text": "⏭ Пропустить",   "callback_data": "skip"},
]]}
CAT_EMOJI = {"meme": "😂", "active": "🏃", "educational": "📚"}

async def send_preview(theme: dict, caption: str, img_bytes: bytes | None, img_url: str | None) -> int | None:
    emoji = CAT_EMOJI.get(theme["category"], "📸")
    text = (
        f"{emoji} <b>Instagram Preview</b>\n\n"
        f"<b>Тема:</b> {theme['theme']}\n"
        f"<b>Тип:</b> {theme['category'].upper()}\n\n"
        f"<b>Caption:</b>\n{caption}"
    )
    if img_bytes:
        return await tg_photo_bytes(img_bytes, text, KEYBOARD)
    text += "\n\n⚠️ <i>Картинка не сгенерирована</i>"
    r = await tg("sendMessage", chat_id=TG_OWNER_ID, text=text, parse_mode="HTML", reply_markup=KEYBOARD)
    return r.get("result", {}).get("message_id")

async def post_to_instagram(caption: str, image_url: str) -> str | None:
    if not IG_ACCESS_TOKEN or not IG_ACCOUNT_ID or not image_url:
        return None
    try:
        base = f"https://graph.instagram.com/v18.0/{IG_ACCOUNT_ID}"
        async with httpx.AsyncClient() as client:
            r = await client.post(f"{base}/media", json={
                "image_url": image_url, "caption": caption, "access_token": IG_ACCESS_TOKEN,
            }, timeout=30)
            r.raise_for_status()
            r2 = await client.post(f"{base}/media_publish", json={
                "creation_id": r.json()["id"], "access_token": IG_ACCESS_TOKEN,
            }, timeout=30)
            r2.raise_for_status()
            return r2.json()["id"]
    except Exception as e:
        log.error(f"Instagram post failed: {e}")
        return None

def log_post(theme_id: int, caption: str, img_url: str | None, ig_id: str | None, status: str):
    try:
        sb.table("instagram_posts_log").insert({
            "theme_id": theme_id, "caption": caption,
            "image_url": img_url or "", "instagram_post_id": ig_id or "",
            "posted_at": datetime.now(BKK).isoformat(), "status": status,
        }).execute()
    except Exception as e:
        log.error(f"DB log failed: {e}")

async def run_workflow(feedback: str = ""):
    global pending
    if feedback and pending.get("theme"):
        theme = pending["theme"]
    else:
        res = sb.table("instagram_content").select("*").eq("is_active", True).execute()
        if not res.data:
            await tg("sendMessage", chat_id=TG_OWNER_ID, text="❌ Нет активных тем!")
            return
        theme = random.choice(res.data)
    log.info(f"Workflow: '{theme['theme']}' feedback='{feedback or 'none'}'")
    caption = await generate_caption(theme, feedback)
    img_bytes, img_url = await generate_image(theme["prompt"])
    msg_id = await send_preview(theme, caption, img_bytes, img_url)
    pending.update({"theme": theme, "caption": caption, "image_url": img_url,
                    "message_id": msg_id, "awaiting_feedback": False})

async def handle_callback(cb: dict):
    global pending
    cb_id = cb["id"]
    data  = cb.get("data", "")
    mid   = cb["message"]["message_id"]
    if not pending or pending.get("message_id") != mid:
        await tg("answerCallbackQuery", callback_query_id=cb_id, text="Нет активного поста")
        return
    await tg("answerCallbackQuery", callback_query_id=cb_id)
    if data == "approve":
        ig_id = await post_to_instagram(pending["caption"], pending["image_url"] or "")
        if ig_id:
            log_post(pending["theme"]["id"], pending["caption"], pending["image_url"], ig_id, "published")
            await tg("sendMessage", chat_id=TG_OWNER_ID,
                     text=f"✅ <b>Опубликовано!</b> Post ID: <code>{ig_id}</code>", parse_mode="HTML")
        else:
            log_post(pending["theme"]["id"], pending["caption"], pending["image_url"], None, "approved_manual")
            await tg("sendMessage", chat_id=TG_OWNER_ID,
                     text=f"⚠️ Instagram не настроен.\n\n<b>Caption:</b>\n{pending['caption']}", parse_mode="HTML")
        pending.clear()
    elif data == "redo":
        pending["awaiting_feedback"] = True
        await tg("sendMessage", chat_id=TG_OWNER_ID,
                 text="✍️ Что переделать? Напиши комментарий:")
    elif data == "skip":
        log_post(pending["theme"]["id"], pending["caption"], pending["image_url"], None, "skipped")
        await tg("sendMessage", chat_id=TG_OWNER_ID, text="⏭ Пост пропущен")
        pending.clear()

async def handle_message(msg: dict):
    if not pending or not pending.get("awaiting_feedback"):
        return
    feedback = msg.get("text", "").strip()
    if not feedback or feedback.startswith("/"):
        return
    pending["awaiting_feedback"] = False
    await tg("sendMessage", chat_id=TG_OWNER_ID, text=f"♻️ Переделываю: «{feedback}»... (~40 сек)")
    await run_workflow(feedback=feedback)

async def polling_loop():
    global tg_offset
    log.info("Telegram polling started")
    while True:
        try:
            r = await tg("getUpdates", offset=tg_offset, timeout=25,
                         allowed_updates=["message", "callback_query"])
            for upd in r.get("result", []):
                tg_offset = upd["update_id"] + 1
                if "callback_query" in upd:
                    await handle_callback(upd["callback_query"])
                elif "message" in upd:
                    msg = upd["message"]
                    if msg.get("from", {}).get("id") == TG_OWNER_ID and "text" in msg:
                        await handle_message(msg)
        except Exception as e:
            log.error(f"Polling error: {e}")
            await asyncio.sleep(5)

def is_posting_time() -> bool:
    now = datetime.now(BKK)
    return now.weekday() in (1, 4) and now.hour == 10 and now.minute < 5

async def schedule_loop():
    global last_posted_date
    log.info("Schedule loop started (Tue/Fri 10:00 BKK)")
    while True:
        try:
            today = datetime.now(BKK).strftime("%Y-%m-%d")
            if is_posting_time() and last_posted_date != today and not pending:
                last_posted_date = today
                await run_workflow()
        except Exception as e:
            log.error(f"Schedule error: {e}")
        await asyncio.sleep(300)

@asynccontextmanager
async def lifespan(app: FastAPI):
    init_supabase()
    asyncio.create_task(polling_loop())
    asyncio.create_task(schedule_loop())
    log.info("✅ Instagram bot running")
    yield

app = FastAPI(lifespan=lifespan)

@app.get("/health")
def health():
    return {"status": "ok", "pending": bool(pending), "last_posted": last_posted_date}

@app.post("/post/now")
async def force_post():
    if pending:
        return {"error": "Already pending — check Telegram"}
    asyncio.create_task(run_workflow())
    return {"status": "generating — check Telegram in ~40 sec"}
