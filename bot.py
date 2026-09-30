#!/usr/bin/env python3
"""
saveRestrictedMessagesBot - single-file Telegram bot

Accepts a Telegram message link, reads the source message with a dedicated
Telegram user account (MTProto), and saves the media/text into that account's
Saved Messages ("me").

Transfer paths:
1) Native media reuse when Telegram permits it.
2) Fallback remote relay: temporary file on the hosting server, then upload to
   Saved Messages. The temporary file is deleted.

IMPORTANT:
The fallback consumes the HOST SERVER's bandwidth. It does not pass the large
media through your phone. If you run this script on your own PC, the PC uses
its own Internet connection.

Required environment variables:
  API_ID
  API_HASH
  BOT_TOKEN
  USER_SESSION
  OWNER_ID

First-time session creation:
  API_ID=... API_HASH=... python bot.py --login

Dependency:
  pip install -U telethon
Optional:
  pip install -U cryptg

Never commit USER_SESSION to GitHub.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import re
import sys
import tempfile
import time
from pathlib import Path
from urllib.parse import urlparse

try:
    from telethon import TelegramClient, events
    from telethon.errors import (
        AuthKeyUnregisteredError,
        FloodWaitError,
        RPCError,
        SessionPasswordNeededError,
    )
    from telethon.sessions import StringSession
except ImportError:
    print("Missing dependency. Install it with: pip install -U telethon")
    raise


def env_required(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def load_config() -> tuple[int, str, str, str, int]:
    try:
        api_id = int(env_required("API_ID"))
    except ValueError as exc:
        raise RuntimeError("API_ID must be an integer") from exc

    api_hash = env_required("API_HASH")
    bot_token = env_required("BOT_TOKEN")
    user_session = env_required("USER_SESSION")

    try:
        owner_id = int(env_required("OWNER_ID"))
    except ValueError as exc:
        raise RuntimeError("OWNER_ID must be an integer") from exc

    return api_id, api_hash, bot_token, user_session, owner_id


LINK_RE = re.compile(
    r"https?://(?:t|telegram)\.me/[^\s<>()]+",
    re.IGNORECASE,
)


def extract_links(text: str) -> list[str]:
    return LINK_RE.findall(text or "")


def parse_message_link(link: str) -> tuple[object, int]:
    normalized = link.strip().rstrip(".,;")
    parsed = urlparse(normalized)
    host = parsed.netloc.lower().split(":")[0]

    if host not in {"t.me", "telegram.me"}:
        raise ValueError("الرابط ليس رابط Telegram صالحًا.")

    parts = [p for p in parsed.path.split("/") if p]
    if len(parts) < 2:
        raise ValueError("تعذر استخراج رقم الرسالة من الرابط.")

    if parts[0].lower() == "c":
        if len(parts) < 3:
            raise ValueError("رابط /c/ ناقص.")
        raw_peer = parts[1]
        raw_message = parts[2]
        if not raw_peer.isdigit() or not raw_message.isdigit():
            raise ValueError("معرّف المجموعة أو رقم الرسالة غير صالح.")
        return int("-100" + raw_peer), int(raw_message)

    username = parts[0].lstrip("@")
    raw_message = parts[1]
    if not re.fullmatch(r"[A-Za-z0-9_]{4,64}", username):
        raise ValueError("اسم المستخدم في الرابط غير صالح.")
    if not raw_message.isdigit():
        raise ValueError("رقم الرسالة غير صالح.")
    return username, int(raw_message)


def human_size(value: int | None) -> str:
    if not value:
        return "غير معروف"
    size = float(value)
    units = ("B", "KB", "MB", "GB", "TB")
    for unit in units:
        if size < 1024 or unit == units[-1]:
            return f"{size:.2f} {unit}"
        size /= 1024
    return f"{size:.2f} TB"


def safe_filename(name: str | None, fallback: str) -> str:
    if not name:
        return fallback
    cleaned = re.sub(r'[\\/:*?"<>|\x00-\x1F]', "_", name).strip()
    return cleaned[:240] or fallback


def media_name(message) -> str:
    name = getattr(getattr(message, "file", None), "name", None)
    if name:
        return safe_filename(name, f"message_{message.id}")
    return f"telegram_{message.id}"


def media_size(message) -> int | None:
    return getattr(getattr(message, "file", None), "size", None)


def caption_for(message) -> str | None:
    text = (getattr(message, "message", None) or "").strip()
    return text or None


def is_media(message) -> bool:
    return bool(getattr(message, "media", None))


async def create_user_session() -> None:
    try:
        api_id = int(env_required("API_ID"))
    except ValueError as exc:
        raise RuntimeError("API_ID must be an integer") from exc
    api_hash = env_required("API_HASH")

    print("\n=== Telegram user session setup ===")
    print("Run this locally in your own terminal.")
    print("The phone number and login code are not sent to this bot.\n")

    client = TelegramClient(StringSession(), api_id, api_hash)
    await client.connect()

    try:
        phone = input("Telegram phone number (e.g. +967...): ").strip()
        if not phone:
            raise RuntimeError("Phone number is required.")

        await client.send_code_request(phone)
        code = input("Telegram login code: ").strip()

        try:
            await client.sign_in(phone=phone, code=code)
        except SessionPasswordNeededError:
            password = input("Two-step verification password: ")
            await client.sign_in(password=password)

        session = client.session.save()
        me = await client.get_me()

        print("\nLogin succeeded.")
        print(f"Account: @{getattr(me, 'username', None) or me.id}")
        print("\nUSER_SESSION=\n")
        print(session)
        print("\nStore it as a secret/environment variable.")
        print("Do not commit it to GitHub or send it to anyone.")
    finally:
        await client.disconnect()


class TransferEngine:
    def __init__(self, user_client: TelegramClient, owner_id: int):
        self.user = user_client
        self.owner_id = owner_id
        self.lock = asyncio.Lock()

    async def resolve_message(self, link: str):
        peer, message_id = parse_message_link(link)
        entity = await self.user.get_entity(peer)
        message = await self.user.get_messages(entity, ids=message_id)

        if not message:
            raise ValueError(
                "لم أجد الرسالة. تأكد أن الحساب الثاني عضو في المجموعة أو القناة "
                "وأن رابط الرسالة صحيح."
            )
        return entity, message

    async def save_text(self, message) -> None:
        text = (message.message or "").strip()
        if not text:
            raise ValueError("الرسالة لا تحتوي نصًا أو وسائط قابلة للحفظ.")
        await self.user.send_message("me", text, link_preview=False)

    async def try_native_reuse(self, message) -> bool:
        if not is_media(message):
            return False

        try:
            await self.user.send_file(
                "me",
                message.media,
                caption=caption_for(message),
                force_document=False,
            )
            return True
        except (RPCError, ValueError, TypeError):
            return False

    async def remote_relay(self, message, status_message) -> None:
        size = media_size(message)
        name = media_name(message)

        with tempfile.TemporaryDirectory(prefix="save_restricted_") as td:
            destination = Path(td) / name
            last_update = 0.0
            last_percent = -1

            async def update_download_progress(current: int, total: int) -> None:
                nonlocal last_update, last_percent
                if not total:
                    return

                now = time.monotonic()
                percent = int((current / total) * 100)

                if percent == last_percent and (now - last_update) < 3:
                    return
                if (now - last_update) < 2 and percent < 100:
                    return

                last_percent = percent
                last_update = now
                try:
                    await status_message.edit(
                        f"⏬ النقل على خادم البوت: {percent}% "
                        f"({human_size(current)} / {human_size(total)})\n"
                        f"الملف: {name}"
                    )
                except Exception:
                    pass

            def progress_callback(current: int, total: int) -> None:
                try:
                    asyncio.get_running_loop().create_task(
                        update_download_progress(current, total)
                    )
                except RuntimeError:
                    pass

            await self.user.download_media(
                message,
                file=str(destination),
                progress_callback=progress_callback,
            )

            if not destination.exists():
                raise RuntimeError("فشل نقل الوسائط إلى خادم البوت.")

            await status_message.edit(
                "⬆️ تم استلام الملف على خادم البوت. "
                "جاري رفعه إلى Saved Messages...\n"
                f"الملف: {name}\n"
                f"الحجم: {human_size(size)}"
            )

            await self.user.send_file(
                "me",
                str(destination),
                caption=caption_for(message),
                force_document=False,
                supports_streaming=True,
            )

    async def process(self, link: str, status_message) -> str:
        async with self.lock:
            entity, message = await self.resolve_message(link)

            size = media_size(message)
            chat_title = (
                getattr(entity, "title", None)
                or getattr(entity, "username", None)
                or "المصدر"
            )

            if not is_media(message):
                await self.save_text(message)
                return f"✅ تم حفظ النص في Saved Messages.\nالمصدر: {chat_title}"

            await status_message.edit(
                "🔎 وجدت الوسائط.\n"
                f"المصدر: {chat_title}\n"
                f"الحجم: {human_size(size)}\n"
                "أحاول أولًا النقل المباشر داخل Telegram..."
            )

            if await self.try_native_reuse(message):
                return (
                    "✅ تم الحفظ في Saved Messages باستخدام إعادة استخدام الوسائط "
                    "داخل Telegram، من دون تنزيل الملف على جهازك."
                )

            await status_message.edit(
                "🛡️ النقل المباشر رفضه Telegram (غالبًا لأن المحتوى محمي).\n"
                "سأستخدم النقل البعيد عبر خادم البوت؛ الملف لن يمر عبر هاتفك."
            )

            await self.remote_relay(message, status_message)

            return (
                "✅ اكتمل النقل إلى Saved Messages.\n"
                "مسار النقل البعيد يستهلك بيانات خادم البوت وليس بيانات هاتفك."
            )


async def run_bot() -> None:
    api_id, api_hash, bot_token, user_session, owner_id = load_config()

    bot_client = TelegramClient(
        StringSession(),
        api_id,
        api_hash,
        device_model="saveRestrictedMessagesBot",
        system_version="1.0",
        app_version="1.0",
    )

    user_client = TelegramClient(
        StringSession(user_session),
        api_id,
        api_hash,
        device_model="saveRestrictedMessagesBot-user",
        system_version="1.0",
        app_version="1.0",
    )

    await user_client.start()
    await bot_client.start(bot_token=bot_token)

    bot_me = await bot_client.get_me()
    user_me = await user_client.get_me()
    engine = TransferEngine(user_client, owner_id)

    print("\n=== saveRestrictedMessagesBot ===")
    print(f"Bot: @{getattr(bot_me, 'username', None) or bot_me.id}")
    print(
        f"Reader account: @{getattr(user_me, 'username', None) or user_me.id}"
    )
    print(f"Owner ID: {owner_id}")
    print("Status: RUNNING\n")

    @bot_client.on(events.NewMessage(incoming=True))
    async def handler(event) -> None:
        sender = await event.get_sender()
        sender_id = getattr(sender, "id", None)
        text = (event.raw_text or "").strip()

        if sender_id != owner_id:
            return

        if not text:
            return

        if text in {"/start", "/help"}:
            await event.respond(
                "أرسل رابط رسالة Telegram، مثل:\n"
                "https://t.me/c/123456789/42\n\n"
                "الحساب المستخدم للوصول إلى المجموعات هو حساب Telegram "
                "الثاني المخصص للـ USER_SESSION.\n\n"
                "/status — حالة الحسابات\n"
                "/id — رقم حسابك"
            )
            return

        if text == "/id":
            await event.respond(f"Telegram user ID: {sender_id}")
            return

        if text == "/status":
            try:
                reader = await user_client.get_me()
                await event.respond(
                    "✅ البوت يعمل.\n"
                    f"Reader account: @{getattr(reader, 'username', None) or reader.id}\n"
                    f"Owner ID: {owner_id}"
                )
            except Exception as exc:
                await event.respond(
                    f"⚠️ فحص الحساب فشل: {type(exc).__name__}"
                )
            return

        if text.startswith("/"):
            await event.respond("الأمر غير معروف. استخدم /help")
            return

        links = extract_links(text)
        if not links:
            await event.respond(
                "أرسل رابط رسالة Telegram صالحًا.\n"
                "مثال: https://t.me/c/123456789/42"
            )
            return

        if len(links) > 1:
            await event.respond("أرسل رابطًا واحدًا في كل مرة.")
            return

        status = await event.respond("⏳ جاري قراءة الرابط...")

        try:
            result = await engine.process(links[0], status)
            await status.edit(result)

        except FloodWaitError as exc:
            await status.edit(
                f"⏳ Telegram فرض انتظارًا لمدة {exc.seconds} ثانية بسبب FloodWait."
            )

        except AuthKeyUnregisteredError:
            await status.edit(
                "❌ جلسة الحساب الثاني غير صالحة أو انتهت. "
                "أنشئ USER_SESSION جديدة باستخدام python bot.py --login."
            )

        except Exception as exc:
            print(
                f"[ERROR] {type(exc).__name__}: {exc}",
                file=sys.stderr,
            )
            await status.edit(
                "❌ تعذر إتمام العملية.\n"
                f"الخطأ: {type(exc).__name__}\n\n"
                "تحقق من أن الحساب الثاني عضو في المصدر وأن الرابط صحيح."
            )

    try:
        await bot_client.run_until_disconnected()
    finally:
        await user_client.disconnect()
        await bot_client.disconnect()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Single-file Telegram restricted-message saver"
    )
    parser.add_argument(
        "--login",
        action="store_true",
        help="Create a Telethon StringSession for the dedicated account",
    )
    args = parser.parse_args()

    try:
        if args.login:
            asyncio.run(create_user_session())
        else:
            asyncio.run(run_bot())
    except KeyboardInterrupt:
        print("\nStopped.")
    except Exception as exc:
        print(
            f"\n[FATAL] {type(exc).__name__}: {exc}",
            file=sys.stderr,
        )
        sys.exit(1)


if __name__ == "__main__":
    main()
