"""
generate_session.py — Run this ONCE on your local machine.

It logs into Telegram and prints a SESSION_STRING you can paste
into Render's environment variables. This way your login survives
every redeploy without needing the .session file.

Usage:
    python generate_session.py
"""

import asyncio
from dotenv import load_dotenv
import os

load_dotenv()


async def main():
    from telethon import TelegramClient
    from telethon.sessions import StringSession

    api_id   = int(os.getenv("API_ID", 0))
    api_hash = os.getenv("API_HASH", "")
    phone    = os.getenv("PHONE_NUMBER", "")

    if not api_id or not api_hash:
        print("❌  Make sure API_ID and API_HASH are in your .env file.")
        return

    print("📱  Logging into Telegram to generate a session string...")
    print("    You will be asked for your phone number and verification code.\n")

    async with TelegramClient(StringSession(), api_id, api_hash) as client:
        await client.start(phone=phone)
        session_string = client.session.save()

    print("\n" + "=" * 60)
    print("✅  YOUR SESSION STRING (keep this secret!):")
    print("=" * 60)
    print(session_string)
    print("=" * 60)
    print("\n👉  Copy the string above and add it to Render as:")
    print("    Key:   SESSION_STRING")
    print("    Value: (the string above)\n")


if __name__ == "__main__":
    asyncio.run(main())
