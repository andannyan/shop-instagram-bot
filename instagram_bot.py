#!/usr/bin/env python3
"""
Instagram Auto-Posting Bot with Telegram Approval
==================================================
Schedule: Tuesday & Friday 10:00 BKK (UTC+7)

Flow:
  generate → Telegram preview → [✅ Post] [♻️ Redo] [⏭ Skip]
  ♻️ → user types feedback → regenerate → new preview

Env vars required:
  SUPABASE_URL, SUPABASE_SERVICE_KEY
  INSTAGRAM_BOT_TOKEN     ← separate bot from nekiagent!
  TELEGRAM_USER_ID        ← owner's Telegram ID (7255533143)
  DEEPSEEK_API_KEY        ← for captions (falls back to OpenAI)
  OPENAI_API_KEY          ← for captions fallback + DALL-E 3 images

Env vars optional (Instagram posting):
  INSTAGRAM_ACCESS_TOKEN
  INSTAGRAM_BUSINESS_ACCOUNT_ID
"""

import asyncio
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

SUPABASE_URL      = os.getenv("SUPABASE_URL", "")
SUPABASE_KEY      = os.getenv("SUPABASE_SERVICE_KEY", "")
OPENAI_API_KEY    = os.getenv("OPENAI_API_KEY", "")
DEEPSEEK_API_KEY  = os.getenv("DEEPSEEK_API_KEY", "")
TG_TOKEN          = os.getenv("INSTAGRAM_BOT_TOKEN", "")
TG_OWNER_ID       = int(os.getenv("TELEGRAM_USER_ID", "7255533143"))
IG_ACCESS_TOKEN   = os.getenv("INSTAGRAM_ACCESS_TOKEN", "")
IG_ACCOUNT_ID     = os.getenv("INSTAGRAM_BUSINESS_ACCOUNT_ID", "")

BKK = ZoneInfo("Asia/Bangkok")

# ─── State ────────────────────────────────────────────────────────────────────

sb: Client = None

# Pending post waiting for approval
pending: dict = {}
tg_offset: int = 0
last_posted_date: str = ""

# ─── Supabase ─────────────────────────────────────────────────────────────────

def init_supabase():
    global sb
    sb = create_client(SUPABASE_URL, SUPABASE_KEY)
    log.info("Supabase initialized")

# ─── Image Generation (DALL-E 3) ──────────────────────────────────────────────

async def generate_image(prompt: str) -> str | None:
    if not OPENAI_API_KEY:
        log.warning("No OPENAI_API_KEY — skipping image generation")
        return None
    try:
        async with httpx.AsyncClient() as client:
            r = await client.post(
                "https://api.openai.com/v1/images/generations",
                headers={"Authorization": f"Bearer {OPENAI_API_KEY}"},
                json={
                    "model": "dall-e-3",
                    "prompt": prompt + ", high quality, Instagram-ready, no text overlay",
                    "n": 1,
                    "size": "1024x1024",
                },
                timeout=90,
            )
            r.raise_for_status()
            url = r.json()["data"][0]["url"]
            log.info(f"Image generated")
            return url
    except Exception as e:
        log.error(f"Image generation failed: {e}")
        return None

# ─── Caption Generation ───────────────────────────────────────────────────────

SYSTEM_PROMPTS = {
    "meme": (
        "You are a meme creator for Lighthouse — a premium cannabis shop on Koh Samui, Thailand.\n"
        "Write SHORT, punchy Instagram captions:\n"
        "- Break the 'weed = lazy' stereotype. Show athletes, creatives, productive people.\n"
        "- Format: funny setup + punchline\n"
        "- 1–3 sentences MAX, 2–3 emojis\n"
        "- Hashtags at the end (use the ones provided)\n"
        "Style: witty, modern, not cringe. Write in English."
    ),
    "active": (
        "You are a content creator for Lighthouse — a premium cannabis shop on Koh Samui, Thailand.\n"
        "Write inspiring captions about cannabis + active lifestyle:\n"
        "- Audience: athletes, yogis, swimmers, runners, gym-goers\n"
        "- Cannabis enhances focus, recovery, enjoyment of physical activity\n"
        "- Energetic, motivating tone\n"
        "- 2–3 sentences, 3–4 emojis, hashtags at the end\n"
        "Write in English."
    ),
    "educational": (
        "You are an educational content creator for Lighthouse — a premium cannabis shop on Koh Samui, Thailand.\n"
        "Write informative Instagram captions:\n"
        "- Teach one specific thing about cannabis (strains, effects, science, tips)\n"
        "- Clear for beginners, interesting for enthusiasts\n"
        "- Builds brand trust and authority\n"
        "- 3–4 sentences with real facts, 2–3 emojis, hashtags at the end\n"
        "Write in English."
    ),
}

async def generate_caption(theme: dict, feedback: str = "") -> str:
    category = theme.get("category", "educational")
    system = SYSTEM_PROMPTS.get(category, SYSTEM_PROMPTS["educational"])

    user_msg = (
        f"Theme: {theme['theme']}\n"
        f"Description: {theme['description']}\n"
        f"Hashtags to include: {theme['hashtags']}"
    )
    if feedback:
        user_msg += f"\n\nPREVIOUS VERSION WAS REJECTED. Feedback: {feedback}\nRewrite accordingly."

    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": user_msg},
    ]
    payload = {"messages": messages, "temperature": 0.9, "max_tokens": 250}

    # Try DeepSeek first
    if DEEPSEEK_API_KEY:
        try:
            async with httpx.AsyncClient() as client:
                r = await client.post(
                    "https://api.deepseek.com/chat/completions",
                    headers={"Authorization": f"Bearer {DEEPSEEK_API_KEY}"},
                    json={**payload, "model": "deepseek-chat"},
                    timeout=30,
                )
                r.raise_for_status()
                caption = r.json()["choices"][0]["message"]["content"].strip()
                log.info("Caption via DeepSeek")
                return caption
        except Exception as e:
            log.error(f"DeepSeek failed: {e}")

    # Fallback: OpenAI
    if OPENAI_API_KEY:
        try:
            async with httpx.AsyncClient() as client:
                r = await client.post(
                    "https://api.openai.com/v1/chat/completions",
                    headers={"Authorization": f"Bearer {OPENAI_API_KEY}"},
                    json={**payload, "model": "gpt-4o-mini"},
                    timeout=30,
                )
                r.raise_for_status()
                caption = r.json()["choices"][0]["message"]["content"].strip()
                log.info("Caption via OpenAI (fallback)")
                return caption
        except Exception as e:
            log.error(f"OpenAI also failed: {e}")

    return theme.get("description", "No caption generated")

# ─── Telegram Helpers ─────────────────────────────────────────────────────────

async def tg(method: str, **kwargs) -> dict:
    async with httpx.AsyncClient() as client:
        r = await client.post(
            f"https://api.telegram.org/bot{TG_TOKEN}/{method}",
            json=kwargs,
            timeout=30,
        )
        return r.json()

CAT_EMOJI = {"meme": "😂", "active": "🏃", "educational": "📚"}

KEYBOARD = {"inline_keyboard": [[
    {"text": "✅ Опубликовать", "callback_data": "approve"},
    {"text": "♻️ Переделать",  "callback_data": "redo"},
    {"text": "⏭ Пропустить",   "callback_data": "skip"},
]]}

async def send_preview(theme: dict, caption: str, image_url: str | None) -> int | None:
    emoji = CAT_EMOJI.get(theme["category"], "📸")
    text = (
        f"{emoji} <b>Instagram Preview</b>\n\n"
        f"<b>Тема:</b> {theme['theme']}\n"
        f"<b>Тип:</b> {theme['category'].upper()}\n\n"
        f"<b>Caption:</b>\n{caption}"
    )

    if image_url:
        r = await tg("sendPhoto",
            chat_id=TG_OWNER_ID,
            photo=image_url,
            caption=text[:1024],
            parse_mode="HTML",
            reply_markup=KEYBOARD,
        )
    else:
        text += "\n\n⚠️ <i>Картинка не сгенерирована (нет OPENAI_API_KEY)</i>"
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
        log.warning("Instagram credentials not set")
        return None
    if not image_url:
        log.warning("No image URL — cannot post to Instagram")
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
            container_id = r.json()["id"]

            r2 = await client.post(f"{base}/media_publish", json={
                "creation_id": container_id,
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

    # Keep same theme on redo, pick new on fresh run
    if feedback and pending.get("theme"):
        theme = pending["theme"]
    else:
        res = sb.table("instagram_content").select("*").eq("is_active", True).execute()
        themes = res.data
        if not themes:
            log.error("No active themes in instagram_content")
            await tg("sendMessage", chat_id=TG_OWNER_ID,
                     text="❌ Нет активных тем в instagram_content!")
            return
        theme = random.choice(themes)

    log.info(f"Workflow: theme='{theme['theme']}' feedback='{feedback or 'none'}'")

    caption = await generate_caption(theme, feedback)
    image_url = await generate_image(theme["prompt"])

    msg_id = await send_preview(theme, caption, image_url)

    pending = {
        "theme": theme,
        "caption": caption,
        "image_url": image_url,
        "message_id": msg_id,
        "awaiting_feedback": False,
    }

# ─── Callback Handlers ────────────────────────────────────────────────────────

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
        ig_id = await post_to_instagram(pending["caption"], pending["image_url"])
        if ig_id:
            log_post(pending["theme"]["id"], pending["caption"],
                     pending["image_url"], ig_id, "published")
            await tg("sendMessage", chat_id=TG_OWNER_ID,
                     text=f"✅ <b>Опубликовано!</b>\nInstagram post ID: <code>{ig_id}</code>",
                     parse_mode="HTML")
        else:
            log_post(pending["theme"]["id"], pending["caption"],
                     pending["image_url"], None, "approved_no_ig")
            await tg("sendMessage", chat_id=TG_OWNER_ID,
                     text="⚠️ Instagram не настроен. Caption сохранён в логе.\n\n"
                          f"<b>Caption для ручного поста:</b>\n{pending['caption']}",
                     parse_mode="HTML")
        pending = {}

    elif data == "redo":
        pending["awaiting_feedback"] = True
        await tg("sendMessage", chat_id=TG_OWNER_ID,
                 text="✍️ Что переделать? Напиши комментарий (например: «сделай смешнее» / «другой тон» / «измени хэштеги»):")

    elif data == "skip":
        log_post(pending["theme"]["id"], pending["caption"],
                 pending["image_url"], None, "skipped")
        await tg("sendMessage", chat_id=TG_OWNER_ID, text="⏭ Пост пропущен")
        pending = {}

async def handle_message(msg: dict):
    global pending

    if not pending or not pending.get("awaiting_feedback"):
        return

    feedback = msg.get("text", "").strip()
    if not feedback or feedback.startswith("/"):
        return

    pending["awaiting_feedback"] = False
    await tg("sendMessage", chat_id=TG_OWNER_ID,
             text=f"♻️ Переделываю с учётом: «{feedback}»...\nПодожди ~30 сек")
    await run_post_workflow(feedback=feedback)

# ─── Telegram Polling Loop ────────────────────────────────────────────────────

async def telegram_polling_loop():
    global tg_offset
    log.info("Telegram polling started")

    while True:
        try:
            r = await tg("getUpdates",
                offset=tg_offset,
                timeout=25,
                allowed_updates=["message", "callback_query"],
            )
            for upd in r.get("result", []):
                tg_offset = upd["update_id"] + 1
                if "callback_query" in upd:
                    await handle_callback(upd["callback_query"])
                elif "message" in upd:
                    msg = upd["message"]
                    from_id = msg.get("from", {}).get("id")
                    if from_id == TG_OWNER_ID and "text" in msg:
                        await handle_message(msg)
        except Exception as e:
            log.error(f"Polling error: {e}")
            await asyncio.sleep(5)

# ─── Schedule Loop ────────────────────────────────────────────────────────────

def is_posting_time() -> bool:
    now = datetime.now(BKK)
    # Tuesday=1, Friday=4, 10:00–10:04 BKK
    return now.weekday() in (1, 4) and now.hour == 10 and now.minute < 5

async def schedule_loop():
    global last_posted_date
    log.info("Schedule loop started (Tue/Fri 10:00 BKK)")

    while True:
        try:
            today = datetime.now(BKK).strftime("%Y-%m-%d")
            if is_posting_time() and last_posted_date != today and not pending:
                log.info("⏰ Posting time — generating content")
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
    """Manual trigger — for testing without waiting for schedule"""
    if pending:
        return {"error": "Already have pending post waiting for approval"}
    asyncio.create_task(run_post_workflow())
    return {"status": "generating — check Telegram in ~30 sec"}
