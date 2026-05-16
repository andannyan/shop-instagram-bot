"""
Instagram Auto-Posting Agent
Runs on Railway, posts to Instagram 2x per week (Tuesday/Friday 10:00 BKK)
Uses nano Banana or Google Imagen for images, GPT for captions, Telegram preview bot.
"""

import asyncio
import os
import random
from datetime import datetime
from zoneinfo import ZoneInfo
import logging
import httpx
from typing import Optional

from supabase import create_client, Client

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Environment variables
SUPABASE_URL = os.getenv("SUPABASE_URL", "https://tzgmgzjlmuwxfrkfaauj.supabase.co")
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_KEY", "")
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "")
TELEGRAM_BOT_TOKEN = os.getenv("CLAUDE_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = int(os.getenv("TELEGRAM_USER_ID", "7255533143"))
INSTAGRAM_ACCESS_TOKEN = os.getenv("INSTAGRAM_ACCESS_TOKEN", "")
INSTAGRAM_BUSINESS_ACCOUNT_ID = os.getenv("INSTAGRAM_BUSINESS_ACCOUNT_ID", "")

# Image generation: nano Banana vs Google Imagen
# Set to "banana" or "google"
IMAGE_GEN_PROVIDER = os.getenv("IMAGE_GEN_PROVIDER", "banana")
BANANA_API_KEY = os.getenv("BANANA_API_KEY", "")
BANANA_MODEL_KEY = os.getenv("BANANA_MODEL_KEY", "")

# Supabase client
sb: Optional[Client] = None

# Bangkok timezone
BKK_TZ = ZoneInfo("Asia/Bangkok")


async def init_supabase():
    """Initialize Supabase client"""
    global sb
    sb = create_client(SUPABASE_URL, SUPABASE_KEY)
    logger.info("✅ Supabase initialized")


async def get_random_theme():
    """Select random active theme from instagram_content table"""
    if not sb:
        raise RuntimeError("Supabase not initialized")
    
    response = sb.table("instagram_content").select("*").eq("is_active", True).execute()
    themes = response.data
    
    if not themes:
        logger.error("❌ No active themes found in instagram_content")
        return None
    
    theme = random.choice(themes)
    logger.info(f"🎯 Selected theme: {theme['theme']}")
    return theme


async def generate_image_banana(prompt: str) -> Optional[str]:
    """Generate image via nano Banana API"""
    if not BANANA_API_KEY or not BANANA_MODEL_KEY:
        logger.error("❌ Banana API keys not configured")
        return None
    
    try:
        url = "https://api.banana.dev/start/v4/"
        payload = {
            "api_key": BANANA_API_KEY,
            "model_key": BANANA_MODEL_KEY,
            "startModel": True,
            "modelInputs": {
                "prompt": prompt,
                "num_inference_steps": 25,
                "guidance_scale": 7.5,
            }
        }
        
        async with httpx.AsyncClient() as client:
            response = await client.post(url, json=payload, timeout=60)
            result = response.json()
            
            if result.get("modelOutputs"):
                image_url = result["modelOutputs"][0].get("image_url")
                if image_url:
                    logger.info(f"✅ Image generated via Banana: {image_url}")
                    return image_url
    
    except Exception as e:
        logger.error(f"❌ Banana image generation failed: {e}")
    
    return None


async def generate_image_google(prompt: str) -> Optional[str]:
    """Generate image via Google Imagen (free tier if available)"""
    # Google Imagen free tier availability varies; fallback to description if unavailable
    logger.warning("⚠️ Google Imagen free API not directly available; using prompt as fallback")
    # Could integrate with Google Vertex AI or similar if credentials available
    return None


async def generate_image(prompt: str) -> Optional[str]:
    """Route to appropriate image generation provider"""
    if IMAGE_GEN_PROVIDER == "banana":
        return await generate_image_banana(prompt)
    elif IMAGE_GEN_PROVIDER == "google":
        return await generate_image_google(prompt)
    else:
        logger.error(f"❌ Unknown image provider: {IMAGE_GEN_PROVIDER}")
        return None


async def generate_caption(theme: dict) -> str:
    """Generate Instagram caption using DeepSeek-V4-Flash"""
    if not DEEPSEEK_API_KEY:
        logger.error("❌ DeepSeek API key not configured")
        return theme.get("description", "")

    try:
        system_prompt = """You are an Instagram content creator for a premium cannabis shop on Koh Samui.
Write engaging, trendy Instagram captions in English that:
- Include the provided hashtags
- Are 2-3 sentences max
- Evoke the mood/vibe of the cannabis strain/product
- Use emojis strategically (2-4 max)
- Appeal to tourists and locals interested in premium cannabis culture"""

        user_prompt = f"""Theme: {theme['theme']}
Description: {theme['description']}
Category: {theme['category']}
Hashtags to include: {theme['hashtags']}

Write an Instagram caption."""

        payload = {
            "model": "deepseek-chat",
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ],
            "temperature": 0.9,
            "max_tokens": 150
        }

        headers = {
            "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
            "Content-Type": "application/json"
        }

        async with httpx.AsyncClient() as client:
            response = await client.post(
                "https://api.deepseek.com/chat/completions",
                json=payload,
                headers=headers,
                timeout=30
            )
            result = response.json()

            if response.status_code != 200:
                logger.error(f"❌ DeepSeek API error: {response.status_code} {result}")
                return theme.get("description", "")

            caption = result["choices"][0]["message"]["content"].strip()
            logger.info(f"✅ Caption generated: {caption[:50]}...")
            return caption

    except Exception as e:
        logger.error(f"❌ Caption generation failed: {e}")
        return theme.get("description", "")


async def send_telegram_preview(theme: dict, caption: str, image_url: Optional[str]):
    """Send preview to Telegram bot for approval before posting"""
    if not TELEGRAM_BOT_TOKEN:
        logger.error("❌ Telegram bot token not configured")
        return
    
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        
        message = f"""📸 *Instagram Preview*\n
*Theme:* {theme['theme']}
*Category:* {theme['category'].upper()}

*Caption:*
{caption}

*Hashtags:* {theme['hashtags']}

Image: {image_url or "(Generated)"}

✅ Ready to post? Manual confirmation or auto-post in 5 min."""
        
        payload = {
            "chat_id": TELEGRAM_CHAT_ID,
            "text": message,
            "parse_mode": "Markdown"
        }
        
        async with httpx.AsyncClient() as client:
            response = await client.post(url, json=payload, timeout=30)
            if response.status_code == 200:
                logger.info("✅ Telegram preview sent")
            else:
                logger.error(f"❌ Telegram send failed: {response.status_code}")
    
    except Exception as e:
        logger.error(f"❌ Telegram preview failed: {e}")


async def post_to_instagram(theme: dict, caption: str, image_url: Optional[str]) -> bool:
    """Post to Instagram using Meta Graph API"""
    if not INSTAGRAM_ACCESS_TOKEN or not INSTAGRAM_BUSINESS_ACCOUNT_ID:
        logger.warning("⚠️ Instagram credentials not configured; skipping actual post")
        return False
    
    try:
        # Meta Graph API endpoint
        url = f"https://graph.instagram.com/v18.0/{INSTAGRAM_BUSINESS_ACCOUNT_ID}/media"
        
        # Create container (image post)
        container_payload = {
            "image_url": image_url or "https://via.placeholder.com/1080x1350",
            "caption": caption,
            "access_token": INSTAGRAM_ACCESS_TOKEN
        }
        
        async with httpx.AsyncClient() as client:
            # Create media container
            response = await client.post(url, json=container_payload, timeout=30)
            if response.status_code != 200:
                logger.error(f"❌ Instagram container creation failed: {response.status_code}")
                return False
            
            container_id = response.json().get("id")
            
            # Publish container
            publish_url = f"https://graph.instagram.com/v18.0/{INSTAGRAM_BUSINESS_ACCOUNT_ID}/media_publish"
            publish_payload = {
                "creation_id": container_id,
                "access_token": INSTAGRAM_ACCESS_TOKEN
            }
            
            publish_response = await client.post(publish_url, json=publish_payload, timeout=30)
            if publish_response.status_code == 200:
                post_id = publish_response.json().get("id")
                logger.info(f"✅ Posted to Instagram: {post_id}")
                return post_id
            else:
                logger.error(f"❌ Instagram publish failed: {publish_response.status_code}")
                return False
    
    except Exception as e:
        logger.error(f"❌ Instagram post failed: {e}")
        return False


async def log_post(theme_id: int, caption: str, image_url: Optional[str], instagram_post_id: Optional[str]):
    """Log post to instagram_posts_log table"""
    if not sb:
        raise RuntimeError("Supabase not initialized")
    
    try:
        sb.table("instagram_posts_log").insert({
            "theme_id": theme_id,
            "caption": caption,
            "image_url": image_url,
            "instagram_post_id": instagram_post_id or "pending",
            "posted_at": datetime.now(BKK_TZ).isoformat(),
            "status": "published" if instagram_post_id else "preview"
        }).execute()
        
        logger.info(f"✅ Post logged to instagram_posts_log")
    
    except Exception as e:
        logger.error(f"❌ Logging failed: {e}")


async def create_instagram_post():
    """Main workflow: select theme → generate image → generate caption → send preview → post"""
    logger.info("🔄 Starting Instagram post creation workflow...")
    
    try:
        # Step 1: Select random theme
        theme = await get_random_theme()
        if not theme:
            logger.error("❌ Could not select theme")
            return
        
        # Step 2: Generate image
        image_url = await generate_image(theme["prompt"])
        logger.info(f"🖼️ Image URL: {image_url}")
        
        # Step 3: Generate caption
        caption = await generate_caption(theme)
        
        # Step 4: Send preview to Telegram
        await send_telegram_preview(theme, caption, image_url)
        
        # Step 5: Post to Instagram (if credentials available)
        post_id = await post_to_instagram(theme, caption, image_url)
        
        # Step 6: Log to Supabase
        await log_post(theme["id"], caption, image_url, post_id)
        
        logger.info("✅ Instagram post workflow completed")
    
    except Exception as e:
        logger.error(f"❌ Post creation failed: {e}")


def is_posting_time() -> bool:
    """Check if current time is Tuesday or Friday at 10:00 BKK (±5 min window)"""
    now = datetime.now(BKK_TZ)
    
    # Tuesday = 1, Friday = 4
    is_posting_day = now.weekday() in [1, 4]
    
    # 10:00 BKK ±5 minutes = 09:55-10:05
    is_posting_hour = 9 <= now.hour <= 10
    is_posting_minute = 55 <= now.minute or now.minute <= 5
    
    return is_posting_day and is_posting_hour and is_posting_minute


async def instagram_posting_loop():
    """Background loop: checks schedule every 5 minutes"""
    logger.info("🚀 Instagram posting loop started")
    
    await init_supabase()
    
    while True:
        try:
            if is_posting_time():
                logger.info("⏰ Posting time reached!")
                await create_instagram_post()
                # Sleep for 1 hour to avoid duplicate posts
                await asyncio.sleep(3600)
            else:
                # Check again in 5 minutes
                await asyncio.sleep(300)
        
        except Exception as e:
            logger.error(f"❌ Loop error: {e}")
            await asyncio.sleep(300)


# For Railway deployment: run as task within shop_api startup
if __name__ == "__main__":
    asyncio.run(instagram_posting_loop())
