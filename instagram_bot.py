#!/usr/bin/env python3
"""
Instagram Auto-Posting Bot with Telegram Approval
Schedule: Tuesday & Friday 10:00 BKK (UTC+7)

Flow:
  generate → Telegram preview → [✅ Post] [♻️ Redo] [⏭ Skip]
  ♻️ Redo → user types feedback → regenerate → new preview
  ✅ Approve → post to Instagram

Env vars required:
  SUPABASE_URL, SUPABASE_SERVICE_KEY
  INSTAGRAM_BOT_TOKEN     ← Telegram bot (separate from nekiagent)
  TELEGRAM_USER_ID        ← owner ID (7255533143)
  DEEPSEEK_API_KEY        ← captions (deepseek-chat)
  GOOGLE_API_KEY          ← image generation (Google Imagen 3)

Env vars optional:
  INSTAGRAM_ACCESS_TOKEN
  INSTAGRAM_BUSINESS_ACCOUNT_ID
"""

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

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger(__name__)

# ─── Config ───────────────────────────────────────────────────────────────────

SUPABASE_URL     = os.getenv("SUPABASE_URL", "")
SUPABASE_KEY     = os.getenv("SUPABASE_SERVICE_KEY", "")
DEEPSEEK_KEY     = os.getenv("DEEPSEEK_API_KEY", "")
GOOGLE_KEY       = os.getenv("GOOGLE_API_KEY", "")
TG_TOKEN         = os.getenv("INSTAGRAM_BOT_TOKEN", "")
TG_OWNER_ID      = int(os.getenv("TELEGRAM_USER_ID", "7255533143"))
IG_ACCESS_TOKEN  = os.getenv("INSTAGRAM_ACCESS_TOKEN", "")
IG_ACCOUNT_ID    = os.getenv("INSTAGRAM_BUSINESS_ACCOUNT_ID", "")

BKK = ZoneInfo("Asia/Bangkok")

# ─── State ────────────────────────────────────────────────────────────────────

sb: Client = None
pending: dict = {}          # post waiting for Telegram approval
tg_offset: int = 0
last_posted_date: str = ""

# ─── Supabase ─────────────────────────────────────────────────────────────────

def init_supabase():
    global sb
    sb = create_client(SUPABASE_URL, SUPABASE_KEY)
    log.info("Supabase ready")

def upload_image_to_storage(image_bytes: bytes) -> str | None:
    """Upload image bytes to Supabase Storage, return public URL"""
    try:
        filename = f"post_{datetime.now(BKK).strftime('%Y%m%d_%H%M%S')}.png"
        sb.storage.from_("instagram").upload(
            path=filename,
            file=image_bytes,
            file_options={"content-type": "image/png"},
        )
        url = sb.storage.from_("instagram").get_public_url(filename)
        log.info(f"Image uploaded: {filename}")
        return url
    except Exception as e:
        log.error(f"Storage upload failed: {e}")
        return None

# ─── Image Generation (Google Imagen 3) ───────────────────────────────────────

async def generate_image(prompt: str) -> tuple[bytes | None, str | None]:
    """
    Generate image via Google Imagen 3 API.
    Returns (image_bytes, public_supabase_url) or (None, None) on failure.
    """
    if not GOOGLE_KEY:
        log.warning("No GOOGLE_API_KEY — skipping image generation")
        return None, None
    try:
        url = (
            "https://generativelanguage.googleapis.com/v1beta/models/"
            f"imagen-3.0-generate-002:predict?key={GOOGLE_KEY}"
        )
        payload = {
            "instances": [{"prompt": prompt + ", high quality, Instagram-ready, no text overlay"}],
            "parameters": {
                "sampleCount": 1,
                "aspectRatio": "1:1",
                "safetyFilterLevel": "BLOCK_ONLY_HIGH",
                "personGeneration": "ALLOW_ADULT",
            },
        }
        async with httpx.AsyncClient() as client:
            r = await client.post(url, json=payload, timeout=90)
            r.raise_for_status()
            b64 = r.json()["predictions"][0]["bytesBase64Encoded"]
            image_bytes = base64.b64decode(b64)
            log.info("Image generated via Google Imagen 3")

        public_url = upload_image_to_storage(image_bytes)
        return image_bytes, public_url

    except Exception as e:
        log.error(f"Google Imagen failed: {e}")
        return None, None

# ─── Caption Generation (DeepSeek) ───────────────────────────────────────────

SYSTEM_PROMPTS = {
    "meme": (
        "You are a meme creator for Lighthouse — a premium cannabis shop on Koh Samui, Thailand.\n"
        "Write SHORT, punchy Instagram captions:\n"
        "- Break the 'weed = lazy' stereotype — show athletes, creatives, productive people who also enjoy cannabis\n"
        "- Format: funny setup + punchline\n"
        "- 1–3 sentences MAX, 2–3 emojis\n"
        "- Hashtags at the end (use the ones provided)\n"
        "Style: witty, modern, not cringe. English only."
    ),
    "active": (
        "You are a content creator for Lighthouse — a premium cannabis shop on Koh Samui, Thailand.\n"
        "Write inspiring captions about cannabis + active lifestyle:\n"
        "- Audience: athletes, yogis, swimmers, runners, gym-goers who enjoy cannabis\n"
        "- Show cannabis enhances focus, recovery, enjoyment of physical activity\n"
        "- Energetic, motivating tone. 2–3 sentences, 3–4 emojis\n"
        "- Hashtags at the end (use the ones provided)\n"
        "English only."
    ),
    "educational": (
        "You are an educational content creator for Lighthouse — a premium cannabis shop on Koh Samui, Thailand.\n"
        "Write informative Instagram captions:\n"
        "- Teach one specific thing about cannabis (strains, effects, terpenes, tips)\n"
        "- Clear for beginners, interesting for enthusiasts\n"
        "- Builds brand trust. 3–4 sentences with real facts, 2–3 emojis\n"
        "- Hashtags at the end (use the ones provided)\n"
        "English only."
    ),
}

async def generate_caption(theme: dict, feedback: str = "") -> str:
    if not DEEPSEEK_KEY:
        log.error("No DEEPSEEK_API_KEY")
        return theme.get("description", "")

    category = theme.get("category", "educational")
    system = SYSTEM_PROMPTS.get(category, SYSTEM_PROMPTS["educational"])

    user_msg = (
        f"Theme: {theme['theme']}\n"
        f"Description: {theme['description']}\n"
        f"Hashtags to include: {theme['hashtags']}"
    )
    if feedback:
        user_msg += f"\n\nPREVIOUS VERSION REJECTED. Feedback: {feedback}\nRewrite accordingly."

    try:
        async with httpx.AsyncClient() as client:
            r = await client.post(
                "https://api.deepseek.com/chat/completions",
                headers={"Authorization": f"Bearer {DEEPSEEK_KEY}"},
                json={
                    "model": "deepseek-chat",
                    "messages": [
                        {"role": "system", "content": system},
                        {"role": "user",   "content": user_msg},
                    ],
                    "temperature": 0.9,
                    "max_tokens": 250,
                },
                timeout=30,
            )
            r.raise_for_status()
            caption = r.json()["choices"][0]["message"]["content"].strip()
            log.info("Caption generated via DeepSeek")
            return caption
    except Exception as e:
        log.error(f"DeepSeek failed: {e}")
        return theme.get("description", "")

# ─── Telegram ─────────────────────────────────────────────────────────────────

async def tg(method: str, **kwargs) -> dict:
    async with httpx.AsyncClient() as client:
        r = await client.post(
            f"https://api.telegram.org/bot{TG_TOKEN}/{method}",
            json=kwargs,
            timeout=30,
        )
        return r.json()

async def tg_send_photo_bytes(image_bytes: bytes, caption: str, keyboard: dict) -> int | None:
    """Send photo from bytes (not URL) to Telegram"""
    async with httpx.AsyncClient() as client:
        r = await client.post(
            f"https://api.telegram.org/bot{TG_TOKEN}/sendPhoto",
            data={
                "chat_id": str(TG_OWNER_ID),
                "caption": caption[:1024],
                "parse_mode": "HTML",
                "reply_markup": __import__("json").dumps(keyboard),
            },
            files={"photo": ("post.png", image_bytes, "image/png")},
            timeout=60,
        )
        return r.json().get("result", {}).get("message_id")

CAT_EMOJI = {"meme": "😂", "active": "🏃", "educational": "📚"}

KEYBOARD = {"inline_keyboard": [[
    {"text": "✅ Опубликовать", "callback_data": "approve"},
    {"text": "♻️ Переделать",  "callback_data": "redo"},
    {"text": "⏭ Пропустить",   "callback_data": "skip"},
]]}

async def send_preview(theme: dict, caption: str,
                       image_bytes: bytes | None, image_url: str | None) -> int | None:
    emoji = CAT_EMOJI.get(theme["category"], "📸")
    text = (
        f"{emoji} <b>Instagram Preview</b>\n\n"
        f"<b>Тема:</b> {theme['theme']}\n"
        f"<b>Тип:</b> {theme['category'].upper()}\n\n"
        f"<b>Caption:</b>\n{caption}"
    )

    if image_bytes:
        return await tg_send_photo_bytes(image_bytes, text, KEYBOARD)

    # No image — send text only
    text += "\n\n⚠️ <i>Картинка не сгенерирована</i>"
    r = await tg("sendMessage",
        chat_id=TG_OWNER_ID,
        text=text,
        parse_mode="HTML",
        reply_markup=KEYBOARD,
    )
    return r.get("result", {}).get("message_id")

# ─── Instagram Posting ────────────────────────────────────────────────────────

async def post_to_instagram(caption: str, image_url: str) -> str | None:
    if not IG_ACCESS_TOKEN or not IG_ACCOUNT_ID:
        log.warning("Instagram credentials not configured")
        return None
    try:
        base = f"https://graph.instagram.com/v18.0/{IG_ACCOUNT_ID}"
        async with httpx.AsyncClient() as client:
            r = await client.post(f"{base}/media", json={
                "image_url": image_url,
                "caption": caption,
                "access_token": IG_ACCESS_TOKEN,
            }, timeout=30)
            r.raise_for_status()

            r2 = await client.post(f"{base}/media_publish", json={
                "creation_id": r.json()["id"],
                "access_token": IG_ACCESS_TOKEN,
            }, timeout=30)
            r2.raise_for_status()
            post_id = r2.json()["id"]
            log.info(f"Posted to Instagram: {post_id}")
            return post_id
    except Exception as e:
        log.error(f"Instagram post failed: {e}")
        return None

def log_post(theme_id: int, caption: str, image_url: str | None,
             ig_post_id: str | None, status: str):
    try:
        sb.table("instagram_posts_log").insert({
            "theme_id": theme_id,
            "caption": caption,
            "image_url": image_url or "",
            "instagram_post_id": ig_post_id or "",
            "posted_at": datetime.now(BKK).isoformat(),
            "status": status,
        }).execute()
    except Exception as e:
        log.error(f"DB log failed: {e}")

# ─── Core Workflow ─────────────────────────────────────────────────────────────

async def run_post_workflow(feedback: str = ""):
    global pending

    # On redo keep same theme, on fresh pick random
    if feedback and pending.get("theme"):
        theme = pending["theme"]
    else:
        res = sb.table("instagram_content").select("*").eq("is_active", True).execute()
        themes = res.data
        if not themes:
            await tg("sendMessage", chat_id=TG_OWNER_ID,
                     text="❌ Нет активных тем в instagram_content!")
            return
        theme = random.choice(themes)

    log.info(f"Workflow: '{theme['theme']}' | feedback: '{feedback or 'none'}'")

    caption = await generate_caption(theme, feedback)
    image_bytes, image_url = await generate_image(theme["prompt"])

    msg_id = await send_preview(theme, caption, image_bytes, image_url)

    pending.update({
        "theme": theme,
        "caption": caption,
        "image_url": image_url,
        "message_id": msg_id,
        "awaiting_feedback": False,
    })

# ─── Callback & Message Handlers ─────────────────────────────────────────────

async def handle_callback(cb: dict):
    global pending

    cb_id  = cb["id"]
    data   = cb.get("data", "")
    msg_id = cb["message"]["message_id"]

    if not pending or pending.get("message_id") != msg_id:
        await tg("answerCallbackQuery", callback_query_id=cb_id, text="Нет активного поста")
        return

    await tg("answerCallbackQuery", callback_query_id=cb_id)

    if data == "approve":
        if pending.get("image_url"):
            ig_id = await post_to_instagram(pending["caption"], pending["image_url"])
        else:
            ig_id = None

        if ig_id:
            log_post(pending["theme"]["id"], pending["caption"],
                     pending["image_url"], ig_id, "published")
            await tg("sendMessage", chat_id=TG_OWNER_ID,
                     text=f"✅ <b>Опубликовано в Instagram!</b>\nPost ID: <code>{ig_id}</code>",
                     parse_mode="HTML")
        else:
            log_post(pending["theme"]["id"], pending["caption"],
                     pending["image_url"], None, "approved_manual")
            await tg("sendMessage", chat_id=TG_OWNER_ID,
                     text=(
                         "⚠️ Instagram не настроен — пост сохранён.\n\n"
                         f"<b>Caption для ручной публикации:</b>\n{pending['caption']}"
                     ),
                     parse_mode="HTML")
        pending.clear()

    elif data == "redo":
        pending["awaiting_feedback"] = True
        await tg("sendMessage", chat_id=TG_OWNER_ID,
                 text="✍️ Что переделать? Напиши комментарий:\n(например: «сделай смешнее», «другой тон», «убери хэштеги», «новая картинка»)")

    elif data == "skip":
        log_post(pending["theme"]["id"], pending["caption"],
                 pending["image_url"], None, "skipped")
        await tg("sendMessage", chat_id=TG_OWNER_ID, text="⏭ Пост пропущен")
        pending.clear()

async def handle_message(msg: dict):
    if not pending or not pending.get("awaiting_feedback"):
        return

    feedback = msg.get("text", "").strip()
    if not feedback or feedback.startswith("/"):
        return

    pending["awaiting_feedback"] = False
    await tg("sendMessage", chat_id=TG_OWNER_ID,
             text=f"♻️ Переделываю с учётом: «{feedback}»...\nПодожди ~40 сек")
    await run_post_workflow(feedback=feedback)

# ─── Polling Loop ─────────────────────────────────────────────────────────────

async def telegram_polling_loop():
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

# ─── Schedule Loop ────────────────────────────────────────────────────────────

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
                log.info("⏰ Posting time!")
                last_posted_date = today
                await run_post_workflow()
        except Exception as e:
            log.error(f"Schedule error: {e}")
        await asyncio.sleep(300)

# ─── FastAPI ──────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    init_supabase()
    asyncio.create_task(telegram_polling_loop())
    asyncio.create_task(schedule_loop())
    log.info("✅ Instagram bot running")
    yield

app = FastAPI(lifespan=lifespan, title="Instagram Bot")

@app.get("/health")
def health():
    return {
        "status": "ok",
        "pending_post": bool(pending),
        "last_posted": last_posted_date,
    }

@app.post("/post/now")
async def force_post():
    """Manual trigger for testing — generates post and sends to Telegram for approval"""
    if pending:
        return {"error": "Already have pending post — check Telegram"}
    asyncio.create_task(run_post_workflow())
    return {"status": "generating — check Telegram in ~40 sec"}
