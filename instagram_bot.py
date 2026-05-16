#!/usr/bin/env python3
"""
INSTAGRAM BOT — Standalone Service
===================================

Autonomous Instagram auto-posting agent for cannabis shop content.
Completely separate from shop operations bot.

SCHEDULE: Tuesday & Friday at 10:00 BKK (UTC+7)
- Selects random theme from instagram_content table
- Generates image (Banana API)
- Generates caption (DeepSeek-V4-Flash)
- Sends Telegram preview
- Posts to Instagram (Meta Graph API)

DEPLOYMENT OPTIONS:
1. As background task in shop_api.py (current setup) ✅
2. As standalone service on Railway (separate dyno)
3. Scheduled job (n8n, cron, etc.)

Environment Variables Required:
- SUPABASE_URL
- SUPABASE_SERVICE_KEY
- DEEPSEEK_API_KEY
- CLAUDE_BOT_TOKEN (for Telegram previews)
- TELEGRAM_USER_ID
- INSTAGRAM_ACCESS_TOKEN (Meta)
- INSTAGRAM_BUSINESS_ACCOUNT_ID
- IMAGE_GEN_PROVIDER (banana|google)
- BANANA_API_KEY
- BANANA_MODEL_KEY
"""

import asyncio
import sys
import os

# Import the actual Instagram agent logic
from instagram_agent import instagram_posting_loop, init_supabase
import logging

logging.basicConfig(
    level=logging.INFO,
    format='[INSTAGRAM_BOT] %(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


async def main():
    """Start the Instagram bot as a standalone service."""
    logger.info("🤖 INSTAGRAM BOT starting...")
    logger.info(f"Mode: Standalone Instagram Auto-Posting Service")
    logger.info(f"Schedule: Tuesday & Friday 10:00 BKK (UTC+7)")
    logger.info("")

    # Check required environment variables
    required_vars = [
        "SUPABASE_URL",
        "SUPABASE_SERVICE_KEY",
        "DEEPSEEK_API_KEY",
    ]

    missing = [v for v in required_vars if not os.getenv(v)]
    if missing:
        logger.error(f"❌ Missing environment variables: {', '.join(missing)}")
        sys.exit(1)

    # Optional but recommended
    optional_vars = [
        "INSTAGRAM_ACCESS_TOKEN",
        "INSTAGRAM_BUSINESS_ACCOUNT_ID",
        "BANANA_API_KEY",
        "BANANA_MODEL_KEY",
        "CLAUDE_BOT_TOKEN",
        "TELEGRAM_USER_ID",
    ]

    unconfigured = [v for v in optional_vars if not os.getenv(v)]
    if unconfigured:
        logger.warning(f"⚠️  Optional vars not set (Instagram posting will be limited): {', '.join(unconfigured)}")

    logger.info("✅ Environment check passed")
    logger.info("")

    # Run the background loop
    try:
        await instagram_posting_loop()
    except KeyboardInterrupt:
        logger.info("⏸  Instagram bot stopped by user")
    except Exception as e:
        logger.error(f"❌ Fatal error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    logger.info("=" * 60)
    logger.info("🤖 INSTAGRAM BOT — Auto-Posting Service")
    logger.info("=" * 60)
    asyncio.run(main())
