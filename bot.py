import asyncio
import base64
import ipaddress
import os
import re
import struct
import logging
from aiohttp import web
from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import Message
from telethon import TelegramClient, events
from telethon.sessions import StringSession

# Logging setup
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

API_ID = 2040
API_HASH = "b18441a1ff607e10a989891a5462e627"
BOT_TOKEN = os.getenv("BOT_TOKEN")

if not BOT_TOKEN:
    logger.error("BOT_TOKEN environment variable is missing!")

dp = Dispatcher(storage=MemoryStorage())
bot = Bot(token=BOT_TOKEN) if BOT_TOKEN else None


class LoginStates(StatesGroup):
    waiting_for_auth_key = State()
    waiting_for_phone = State()
    waiting_for_dc_id = State()
    waiting_for_user_id = State()
    waiting_for_verification_choice = State()
    waiting_for_confirmation = State()
    waiting_for_final_verify = State()


def create_string_session(dc_id: int, auth_key_hex: str) -> StringSession:
    dc_ips = {
        1: "149.154.175.50",
        2: "149.154.167.51",
        3: "149.154.175.100",
        4: "149.154.167.91",
        5: "91.108.56.130",
    }
    server_address = dc_ips.get(dc_id, "91.108.56.130")
    port = 443
    auth_key_bytes = bytes.fromhex(auth_key_hex)
    ip_packed = ipaddress.ip_address(server_address).packed
    session_data = struct.pack(
        f">B{len(ip_packed)}sH256s", dc_id, ip_packed, port, auth_key_bytes
    )
    encoded_session = "1" + base64.urlsafe_b64encode(session_data).decode("ascii")
    return StringSession(encoded_session)


@dp.message(Command("start"))
async def cmd_start(message: Message, state: FSMContext):
    await state.clear()
    await message.answer(
        "🚀 **Telegram Account Manager Bot**\n\n"
        "Send `/login` to start extracting account info and handling verifications."
    )


@dp.message(Command("login"))
async def cmd_login(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("Please send your **Auth Key (HEX)**:")
    await state.set_state(LoginStates.waiting_for_auth_key)


@dp.message(LoginStates.waiting_for_auth_key)
async def process_auth_key(message: Message, state: FSMContext):
    await state.update_data(auth_key=message.text.strip())
    await message.answer("Got it. Now send your **Phone Number** (e.g., 855979678585):")
    await state.set_state(LoginStates.waiting_for_phone)


@dp.message(LoginStates.waiting_for_phone)
async def process_phone(message: Message, state: FSMContext):
    await state.update_data(phone=message.text.strip())
    await message.answer("Send your **DC ID** (e.g., 1, 2, 3, 4, or 5):")
    await state.set_state(LoginStates.waiting_for_dc_id)


@dp.message(LoginStates.waiting_for_dc_id)
async def process_dc_id(message: Message, state: FSMContext):
    try:
        dc_id = int(message.text.strip())
        await state.update_data(dc_id=dc_id)
    except ValueError:
        await message.answer("Invalid DC ID. Please send a number (e.g., 5):")
        return

    await message.answer("Send your expected **Telegram Account ID**:")
    await state.set_state(LoginStates.waiting_for_user_id)


@dp.message(LoginStates.waiting_for_user_id)
async def process_user_id(message: Message, state: FSMContext):
    expected_user_id = message.text.strip()
    data = await state.update_data(expected_user_id=expected_user_id)

    auth_key = data["auth_key"]
    phone = data["phone"]
    dc_id = data["dc_id"]

    await message.answer("🔄 Connecting to Telegram data center...")

    try:
        session = create_string_session(dc_id, auth_key)
        client = TelegramClient(
            session=session,
            api_id=API_ID,
            api_hash=API_HASH,
            device_model="YIO780TWPB-EXTREME",
            system_version="Windows 10",
            app_version="7.2.8 x64",
        )
        await client.connect()
        me = await client.get_me()

        if not me:
            await message.answer("❌ Error: Connected, but account profile is empty/invalid.")
            await client.disconnect()
            return

        if str(me.id) != expected_user_id:
            await message.answer(f"⚠️ Warning: Connected ID ({me.id}) does not match expected ID ({expected_user_id}).")

        dialogs = await client.get_dialogs()
        chats_count = sum(1 for d in dialogs if d.is_group)
        channels_count = sum(1 for d in dialogs if d.is_channel)
        admin_count = sum(1 for d in dialogs if (d.is_group or d.is_channel) and getattr(d.entity, 'admin_rights', None))

        info_text = (
            f"✅ **Successfully Logged In!**\n\n"
            f"• **Account Name:** {me.first_name or ''} {me.last_name or ''} (@{me.username or 'No Username'})\n"
            f"• **Telegram User ID:** {me.id}\n"
            f"• **Phone Number:** {me.phone}\n"
            f"• **Country Region:** +{me.phone[:3] if me.phone else 'N/A'}\n"
            f"• **Chats:** {chats_count}\n"
            f"• **Channels:** {channels_count}\n"
            f"• **Admins:** {admin_count}\n\n"
            f"Type **1** to run: `Get code + Verified` or type `/start` to abort."
        )

        await state.update_data(client=client)
        await message.answer(info_text)
        await state.set_state(LoginStates.waiting_for_verification_choice)

    except Exception as e:
        await message.answer(f"❌ Connection Failed:\n`{str(e)}`", parse_mode="Markdown")
        await state.clear()


@dp.message(LoginStates.waiting_for_verification_choice, F.text == "1")
async def start_verification(message: Message, state: FSMContext):
    data = await state.get_data()
    client = data["client"]
    phone = data["phone"]

    try:
        sent = await client.send_code_request(phone)
        await state.update_data(phone_code_hash=sent.phone_code_hash)
        await message.answer(
            f"📱 Phone number `{phone}` dropped and code requested.\n\n"
            "Please type **Yes** when you have sent/triggered the code to Telegram 777000:"
        )
        await state.set_state(LoginStates.waiting_for_confirmation)
    except Exception as e:
        await message.answer(f"❌ Error requesting code: {e}")


@dp.message(LoginStates.waiting_for_confirmation, F.text.casefold() == "yes")
async def listen_for_code(message: Message, state: FSMContext):
    data = await state.get_data()
    client = data["client"]

    await message.answer("🔍 Listening for incoming verification message from 777000...")

    code_future = asyncio.get_running_loop().create_future()

    @client.on(events.NewMessage(chats=777000))
    async def handler(event):
        text = event.raw_text
        match = re.search(r'\b(\d{5})\b', text)
        if match:
            code = match.group(1)
            await event.delete()
            if not code_future.done():
                code_future.set_result(code)

    try:
        code = await asyncio.wait_for(code_future, timeout=60.0)
        client.remove_event_handler(handler)
        await state.update_data(extracted_code=code)
        await message.answer(
            f"🔑 **Extracted Code:** `{code}`\n"
            "Verification message erased from history.\n\n"
            "Type **Yes** to sign in and verify/secure the device now:",
            parse_mode="Markdown",
        )
        await state.set_state(LoginStates.waiting_for_final_verify)
    except asyncio.TimeoutError:
        client.remove_event_handler(handler)
        await message.answer("⏱️ Timed out waiting for the verification message from 777000.")


@dp.message(LoginStates.waiting_for_final_verify, F.text.casefold() == "yes")
async def final_sign_in(message: Message, state: FSMContext):
    data = await state.get_data()
    client = data["client"]
    phone = data["phone"]
    code = data["extracted_code"]
    phone_code_hash = data["phone_code_hash"]

    try:
        await client.sign_in(phone=phone, code=code, phone_code_hash=phone_code_hash)
        await message.answer("🎉 **Successfully logged in and marked device as safe!**")
    except Exception as e:
        await message.answer(f"❌ Sign-in error: {e}")
    finally:
        await client.disconnect()
        await state.clear()


async def handle_ping(request):
    return web.Response(text="Bot is running!")


async def web_server():
    app = web.Application()
    app.router.add_get("/", handle_ping)
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.getenv("PORT", 8000))
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    logger.info(f"Web server started on port {port}")


async def main():
    if not bot:
        logger.error("Bot token is not configured. Exiting.")
        return
    
    await web_server()
    await dp.start_polling(bot)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Bot stopped.")
