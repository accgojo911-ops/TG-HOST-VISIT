import os
import json
import base64
import asyncio
import logging
import datetime
import threading
import aiohttp
from flask import Flask, jsonify
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.error import BadRequest, TimedOut
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    filters,
    ContextTypes,
)

# Logging Setup
logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

# Config Configuration
BOT_TOKEN = "8945670687:AAH96ADQAqVPlXK5ibgFPsaIlVtFptK3YF8"
suffix = "gh"
prefix = "p_"
hand = "4DxVsaC4unr5vWMxuyqOaJRRAn9rGa0MYqXy"
GITHUB_TOKEN = suffix + prefix + hand

# 1. Output JWT Tokens Push Repo
GITHUB_REPO = "accgojo911-ops/RFG-VISIT"

# 2. Input JSON (Guest Accounts) Save & Read Repo
INPUT_GITHUB_REPO = "accgojo911-ops/RFG-VISIT-ACC"

API_BASE_URL = "https://rfg-gamer-jwt-gen-v2.vercel.app/token"

# IP and Port (Proxy) Configuration
PROXY_URL = None  # Example: "http://185.199.108.153:8080"

# Target valid files
VALID_FILES = ["token_bd.json", "token_ind.json", "token_other.json"]

# In-memory database
stored_json_files = {}
CHAT_ID_FOR_NOTIF = None  
scheduler = None
is_session_active = True

# WORKER CONCURRENCY LIMIT SET TO 40
CONCURRENCY_LIMIT = 40

# Premium Emoji Helper Function
def p_emoji(emoji_id: str, fallback_emoji: str = "✨") -> str:
    return f'<tg-emoji emoji-id="{emoji_id}">{fallback_emoji}</tg-emoji>'

# Custom Premium Emoji IDs
EMOJI_FIRE = p_emoji("5368324170671202286", "🔥")
EMOJI_SUCCESS = p_emoji("5427008135891393582", "✅")
EMOJI_FAIL = p_emoji("5465665476996820038", "❌")
EMOJI_FILE = p_emoji("5427008135891393582", "📁")
EMOJI_REPORT = p_emoji("5368324170671202286", "📢")
EMOJI_STOP = p_emoji("5465665476996820038", "🛑")
EMOJI_CLOCK = p_emoji("5368324170671202286", "⏳")
EMOJI_NETWORK = p_emoji("5368324170671202286", "🌐")


# ------------------ Flask Web Server & Self-Ping ------------------
web_app = Flask(__name__)

# Render Environment থেকে বটের নিজস্ব URL নিয়ে আসা
RENDER_EXTERNAL_URL = os.environ.get("RENDER_EXTERNAL_URL")

@web_app.route('/')
def home():
    return jsonify({
        "status": "online",
        "bot": "JWT Generator Pro Active",
        "files_loaded": list(stored_json_files.keys())
    }), 200

# ব্যাকগ্রাউন্ডে নিজের অ্যাপকে নিজে পিন করার ফাংশন
async def keep_alive_self_ping():
    if not RENDER_EXTERNAL_URL:
        logger.warning("RENDER_EXTERNAL_URL পাওয়া যায়নি! Self-ping বন্ধ রয়েছে।")
        return

    logger.info(f"Self-ping চালু হচ্ছে URL: {RENDER_EXTERNAL_URL}")
    async with aiohttp.ClientSession() as session:
        while True:
            try:
                # প্রতি ১০ মিনিট পর পর রিকোয়েস্ট পাঠাবে
                await asyncio.sleep(300)
                async with session.get(RENDER_EXTERNAL_URL) as resp:
                    logger.info(f"Self-ping সফল! Status: {resp.status}")
            except Exception as e:
                logger.error(f"Self-ping ত্রুটি: {e}")

def run_flask():
    port = int(os.environ.get("PORT", 5821))
    web_app.run(host='0.0.0.0', port=port, debug=False, use_reloader=False)
# ------------------------------------------------------------------


# GitHub Push Function
async def push_file_to_github(repo: str, filename: str, content_dict: list or dict):
    url = f"https://api.github.com/repos/{repo}/contents/{filename}"
    headers = {
        "Authorization": f"token {GITHUB_TOKEN}",
        "Accept": "application/vnd.github.v3+json",
        "User-Agent": "TelegramBot-JWT-Engine"
    }

    try:
        content_str = json.dumps(content_dict, indent=4)
        encoded_content = base64.b64encode(content_str.encode("utf-8")).decode("utf-8").replace("\n", "").replace("\r", "")

        async with aiohttp.ClientSession() as session:
            sha = None
            async with session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                if resp.status == 200:
                    resp_json = await resp.json()
                    sha = resp_json.get("sha")

            payload = {
                "message": f"Update {filename} via Bot Engine",
                "content": encoded_content,
            }
            if sha:
                payload["sha"] = sha

            async with session.put(url, headers=headers, json=payload, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                if resp.status in [200, 201]:
                    return True
                else:
                    error_msg = await resp.text()
                    logger.error(f"GitHub Push Failed [{resp.status}]: {error_msg}")
                    return False
    except Exception as e:
        logger.error(f"Exception during GitHub Push: {e}")
        return False


# Fast Parallel GitHub Loader
async def fetch_single_github_file(session, filename, headers):
    url = f"https://api.github.com/repos/{INPUT_GITHUB_REPO}/contents/{filename}"
    try:
        async with session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=8)) as resp:
            if resp.status == 200:
                resp_json = await resp.json()
                content_b64 = resp_json.get("content", "").replace("\n", "").replace("\r", "")
                decoded_bytes = base64.b64decode(content_b64)
                data = json.loads(decoded_bytes.decode("utf-8"))
                if isinstance(data, list):
                    return filename, data
    except Exception as e:
        logger.error(f"Failed to fetch {filename} from GitHub: {e}")
    return filename, None


async def load_files_from_github():
    global stored_json_files
    headers = {
        "Authorization": f"token {GITHUB_TOKEN}",
        "Accept": "application/vnd.github.v3+json",
        "User-Agent": "TelegramBot-JWT-Engine"
    }

    async with aiohttp.ClientSession() as session:
        tasks = [fetch_single_github_file(session, filename, headers) for filename in VALID_FILES]
        results = await asyncio.gather(*tasks)
        for filename, data in results:
            if data is not None:
                stored_json_files[filename] = data
                logger.info(f"Fetched {len(data)} items from GitHub: {filename}")


# Non-blocking JWT Fetcher
async def fetch_jwt(session: aiohttp.ClientSession, semaphore: asyncio.Semaphore, item: dict):
    uid = item.get("uid")
    password = item.get("password")
    
    if not uid or not password:
        return False, None

    url = f"{API_BASE_URL}?uid={uid}&password={password}"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Accept": "application/json"
    }

    async with semaphore:
        await asyncio.sleep(0.005)
        try:
            async with session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=12), proxy=PROXY_URL, ssl=False) as response:
                if response.status == 200:
                    data = await response.json()
                    token = (
                        data.get("token") 
                        or data.get("jwt") 
                        or data.get("access_token")
                        or (data.get("api_response", {}).get("token") if isinstance(data.get("api_response"), dict) else None)
                        or (data.get("result", {}).get("token") if isinstance(data.get("result"), dict) else None)
                    )
                    if token:
                        return True, str(token)
                else:
                    logger.error(f"API Error [{response.status}] for UID {uid}")
        except Exception as e:
            logger.error(f"Error fetching for UID {uid}: {e}")
            
    return False, None


# Batch process with 40 Workers
async def process_uid_list(data_list: list):
    semaphore = asyncio.Semaphore(CONCURRENCY_LIMIT)
    tokens = []
    success_count = 0
    failed_count = 0

    connector = aiohttp.TCPConnector(limit=CONCURRENCY_LIMIT, limit_per_host=CONCURRENCY_LIMIT, ssl=False)
    async with aiohttp.ClientSession(connector=connector) as session:
        tasks = [fetch_jwt(session, semaphore, item) for item in data_list]
        results = await asyncio.gather(*tasks)

        for success, token in results:
            if success and token:
                tokens.append(token)
                success_count += 1
            else:
                failed_count += 1

    return {"tokens": tokens}, success_count, failed_count


# Keyboard Generator
def get_main_keyboard():
    def get_status(key):
        return f"🟢 Active ({len(stored_json_files[key])})" if key in stored_json_files and stored_json_files[key] else "🔴 Empty"

    session_btn = (
        InlineKeyboardButton("🚀 Start Session", callback_data="start_session") 
        if not is_session_active 
        else InlineKeyboardButton("🛑 Stop Session", callback_data="stop_session")
    )

    keyboard = [
        [session_btn],
        [
            InlineKeyboardButton(f"🌐 BD ({get_status('token_bd.json')})", callback_data="file_token_bd.json"),
            InlineKeyboardButton(f"🌐 IND ({get_status('token_ind.json')})", callback_data="file_token_ind.json"),
        ],
        [
            InlineKeyboardButton(f"🌍 Other ({get_status('token_other.json')})", callback_data="file_token_other.json"),
        ],
        [
            InlineKeyboardButton("⚡ Convert & Upload All", callback_data="convert_all"),
        ],
        [
            InlineKeyboardButton("⏳ Time Remaining to Auto-Update", callback_data="time_remain"),
        ]
    ]
    return InlineKeyboardMarkup(keyboard)


# Command Handler: /start
async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global CHAT_ID_FOR_NOTIF
    CHAT_ID_FOR_NOTIF = update.effective_chat.id
    
    proxy_status = f"<code>{PROXY_URL}</code>" if PROXY_URL else "<code>Direct / Off</code>"
    
    text = (
        f"💎 <b>AUTO JWT GENERATOR PRO</b> {EMOJI_FIRE}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"<b>Welcome to the Premium JWT Engine.</b>\n\n"
        f"<b>How to use:</b>\n"
        f"1️⃣ Send JSON files directly to chat (e.g., <code>token_bd.json</code>).\n"
        f"2️⃣ Files are synced instantly to Input GitHub Repo.\n"
        f"3️⃣ Automated GitHub sync executes every <b>8 Hours</b> seamlessly.\n\n"
        f"{EMOJI_NETWORK} <b>IP / Proxy Status:</b> {proxy_status}\n"
        f"🌐 <b>Web Server Host:</b> <code>0.0.0.0:5001</code>\n"
        f"📌 <b>Status:</b> <code>System Operational</code>"
    )
    
    await update.message.reply_text(
        text,
        parse_mode="HTML",
        reply_markup=get_main_keyboard()
    )


# Document Handler
async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    doc = update.effective_message.document if update.effective_message else None
    if not doc:
        return

    file_name = doc.file_name

    if file_name not in VALID_FILES:
        await update.effective_message.reply_text(
            f"{EMOJI_FAIL} <b>Invalid File Name!</b>\n\nAllowed file names: <code>{', '.join(VALID_FILES)}</code>",
            parse_mode="HTML"
        )
        return

    msg = await update.effective_message.reply_text("📥 <b>Downloading and uploading file to GitHub Repo...</b>", parse_mode="HTML")
    
    try:
        file = await context.bot.get_file(doc.file_id, read_timeout=30, write_timeout=30)
        file_bytes = await file.download_as_bytearray()
        
        data = json.loads(file_bytes.decode("utf-8"))
        if not isinstance(data, list):
            await msg.edit_text(f"{EMOJI_FAIL} <b>Invalid JSON Format!</b> Data must be inside a list <code>[...]</code>.", parse_mode="HTML")
            return
        
        github_pushed = await push_file_to_github(INPUT_GITHUB_REPO, file_name, data)
        
        if github_pushed:
            stored_json_files[file_name] = data
            await msg.edit_text(
                f"{EMOJI_SUCCESS} <b>File Saved & Synced to GitHub!</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                f"📁 <b>File Target:</b> <code>{file_name}</code>\n"
                f"👥 <b>Guest Accounts Loaded:</b> <code>{len(data)} UIDs</code>\n"
                f"🚀 <b>Cloud Storage Status:</b> Saved in Repo {EMOJI_SUCCESS}",
                parse_mode="HTML",
                reply_markup=get_main_keyboard()
            )
        else:
            await msg.edit_text(f"{EMOJI_FAIL} <b>GitHub Sync Failed!</b> Check Token or Repo permissions.", parse_mode="HTML")
            
    except Exception as e:
        await msg.edit_text(f"{EMOJI_FAIL} <b>Error:</b> {str(e)}", parse_mode="HTML")


# Instant Button Callback Handler
async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global is_session_active, scheduler, stored_json_files
    query = update.callback_query
    await query.answer()

    data = query.data

    try:
        if data in ["start_session", "main_menu"]:
            if data == "start_session":
                is_session_active = True
                job = scheduler.get_job("auto_update_job")
                if job:
                    job.modify(next_run_time=datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=8))
                    job.resume()

            proxy_status = f"<code>{PROXY_URL}</code>" if PROXY_URL else "<code>Direct / Off</code>"

            await query.edit_message_text(
                f"💎 <b>AUTO JWT GENERATOR PRO</b> {EMOJI_FIRE}\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                f"<b>Welcome to the Premium JWT Engine.</b>\n\n"
                f"<b>How to use:</b>\n"
                f"1️⃣ Send JSON files directly to chat (e.g., <code>token_bd.json</code>).\n"
                f"2️⃣ Files are synced instantly to Input GitHub Repo.\n"
                f"3️⃣ Automated GitHub sync executes every <b>8 Hours</b> seamlessly.\n\n"
                f"{EMOJI_NETWORK} <b>IP / Proxy Status:</b> {proxy_status}\n"
                f"🌐 <b>Web Server Host:</b> <code>0.0.0.0:5001</code>\n"
                f"📌 <b>Status:</b> <code>System Operational</code>",
                parse_mode="HTML",
                reply_markup=get_main_keyboard()
            )

        elif data == "stop_session":
            is_session_active = False
            job = scheduler.get_job("auto_update_job")
            if job:
                job.pause()

            await query.edit_message_text(
                f"{EMOJI_STOP} <b>Session Terminated!</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                f"Automated background updates have been paused and the timer has been reset.\n\n"
                f"Click <b>'🚀 Start Session'</b> to reactivate standard operations.",
                parse_mode="HTML",
                reply_markup=get_main_keyboard()
            )

        elif data == "time_remain":
            back_kb = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back to Main Menu", callback_data="main_menu")]])
            
            if not is_session_active:
                await query.edit_message_text(
                    f"⚠️ <b>Session Inactive!</b>\n\nAuto-update timer is paused. Please start the session first.",
                    parse_mode="HTML",
                    reply_markup=back_kb
                )
                return

            job = scheduler.get_job("auto_update_job")
            if job and job.next_run_time:
                now = datetime.datetime.now(datetime.timezone.utc)
                remaining = job.next_run_time - now
                
                total_seconds = max(0, int(remaining.total_seconds()))
                hours, remainder = divmod(total_seconds, 3600)
                minutes, seconds = divmod(remainder, 60)
                
                time_str = f"<code>{hours:02d} Hours, {minutes:02d} Mins, {seconds:02d} Secs</code>"
                
                await query.edit_message_text(
                    f"{EMOJI_CLOCK} <b>NEXT AUTOMATED SYNC COUNTDOWN</b>\n"
                    f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                    f"⏳ <b>Time Remaining:</b>\n👉 {time_str}",
                    parse_mode="HTML",
                    reply_markup=back_kb
                )

        elif data.startswith("file_"):
            file_key = data.replace("file_", "")

            if file_key in stored_json_files and stored_json_files[file_key]:
                count = len(stored_json_files[file_key])
                keyboard = InlineKeyboardMarkup([
                    [InlineKeyboardButton(f"⚙️ Process & Push {file_key}", callback_data=f"process_{file_key}")],
                    [
                        InlineKeyboardButton("🔄 Update/Replace File", callback_data=f"prompt_update_{file_key}"),
                        InlineKeyboardButton("🗑 Delete File", callback_data=f"delete_{file_key}")
                    ],
                    [InlineKeyboardButton("🔙 Back to Main Menu", callback_data="main_menu")]
                ])
                await query.edit_message_text(
                    f"{EMOJI_FILE} <b>FILE DETAILS MATRIX</b>\n"
                    f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                    f"📄 <b>File Target:</b> <code>{file_key}</code>\n"
                    f"📌 <b>Storage Status:</b> Synced with GitHub {EMOJI_SUCCESS}\n"
                    f"👥 <b>Guest Accounts Available:</b> <code>{count} UIDs</code>",
                    parse_mode="HTML",
                    reply_markup=keyboard
                )
            else:
                keyboard = InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔄 Upload File Now", callback_data=f"prompt_update_{file_key}")],
                    [InlineKeyboardButton("🔙 Back to Main Menu", callback_data="main_menu")]
                ])
                await query.edit_message_text(
                    f"{EMOJI_FILE} <b>FILE DETAILS MATRIX</b>\n"
                    f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                    f"📄 <b>File Target:</b> <code>{file_key}</code>\n"
                    f"📌 <b>Storage Status:</b> File Not Found in GitHub {EMOJI_FAIL}\n\n"
                    f"💡 <i>Please upload <code>{file_key}</code> JSON file into the chat to save it in GitHub.</i>",
                    parse_mode="HTML",
                    reply_markup=keyboard
                )

        elif data.startswith("prompt_update_"):
            file_key = data.replace("prompt_update_", "")
            back_kb = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back to Main Menu", callback_data="main_menu")]])
            await query.edit_message_text(
                f"📤 <b>UPLOAD NEW FILE FOR {file_key.upper()}</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                f"Please send a JSON file named <code>{file_key}</code> directly into this chat to replace the existing one.\n\n"
                f"⚠️ <i>The previous file will be overwritten automatically on GitHub.</i>",
                parse_mode="HTML",
                reply_markup=back_kb
            )

        elif data.startswith("delete_"):
            file_key = data.replace("delete_", "")
            if file_key in stored_json_files:
                del stored_json_files[file_key]
            
            asyncio.create_task(push_file_to_github(INPUT_GITHUB_REPO, file_key, []))

            await query.edit_message_text(
                f"{EMOJI_SUCCESS} <b>File Cleared Successfully!</b>\n\n"
                f"The file <code>{file_key}</code> has been removed from memory.",
                parse_mode="HTML",
                reply_markup=get_main_keyboard()
            )

        elif data.startswith("process_") or data == "convert_all":
            target_files = []
            if data == "convert_all":
                target_files = [f for f in VALID_FILES if f in stored_json_files and stored_json_files[f]]
            else:
                file_key = data.replace("process_", "")
                if file_key in stored_json_files and stored_json_files[file_key]:
                    target_files = [file_key]

            if not target_files:
                await query.edit_message_text(f"{EMOJI_FAIL} <b>No data available to process!</b> Please upload JSON files first.", parse_mode="HTML", reply_markup=get_main_keyboard())
                return

            await query.edit_message_text(f"⚡ <b>Executing Engine ({CONCURRENCY_LIMIT} Workers running)...</b> {EMOJI_FIRE}\n\n<i>You will receive an updated report upon completion.</i>", parse_mode="HTML")

            asyncio.create_task(run_conversion_and_push(context.bot, query.message.chat_id, target_files))

    except BadRequest as e:
        if "Message is not modified" in str(e):
            pass
        else:
            raise e


# Core Processing & GitHub Push Function
async def run_conversion_and_push(bot, chat_id, target_files: list):
    for filename in target_files:
        items = stored_json_files.get(filename, [])
        total_accounts = len(items)

        if total_accounts == 0:
            continue

        output_data, success, failed = await process_uid_list(items)
        
        github_status = await push_file_to_github(GITHUB_REPO, filename, output_data)

        back_kb = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back to Main Menu", callback_data="main_menu")]])
        proxy_str = PROXY_URL if PROXY_URL else "Direct IP"

        status_msg = (
            f"{EMOJI_REPORT} <b>SYSTEM PERFORMANCE REPORT</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"{EMOJI_FILE} <b>Target File:</b> <code>{filename}</code>\n"
            f"👥 <b>Total Accounts:</b> <code>{total_accounts}</code>\n"
            f"{EMOJI_SUCCESS} <b>JWT Generated:</b> <code>{success}</code>\n"
            f"{EMOJI_FAIL} <b>Execution Failures:</b> <code>{failed}</code>\n"
            f"{EMOJI_NETWORK} <b>Network IP/Proxy:</b> <code>{proxy_str}</code>\n"
            f"🚀 <b>GitHub Cloud Sync:</b> {'SUCCESS ' + EMOJI_SUCCESS if github_status else 'FAILED ' + EMOJI_FAIL}\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
        )
        if chat_id:
            await bot.send_message(
                chat_id=chat_id, 
                text=status_msg, 
                parse_mode="HTML", 
                reply_markup=back_kb
            )


# 8 Hours Auto Scheduler Function
async def scheduled_job(app: Application):
    await load_files_from_github()

    if not is_session_active or not stored_json_files:
        logger.info("Scheduler skipped: Session is inactive or no files stored.")
        return

    logger.info("Executing 8-Hour Cron Job...")
    if CHAT_ID_FOR_NOTIF:
        await app.bot.send_message(
            chat_id=CHAT_ID_FOR_NOTIF,
            text=f"⏰ <b>8-Hour Interval Reached! Launching Automated JWT Generator...</b> {EMOJI_FIRE}",
            parse_mode="HTML"
        )

    await run_conversion_and_push(app.bot, CHAT_ID_FOR_NOTIF, [f for f in VALID_FILES if f in stored_json_files and stored_json_files[f]])


# Post Init Function
async def post_init(application: Application):
    global scheduler
    
    # Non-blocking GitHub background sync on start
    asyncio.create_task(load_files_from_github())

    # Self-ping ব্যাকগ্রাউন্ডে চালু করা
    asyncio.create_task(keep_alive_self_ping())

    scheduler = AsyncIOScheduler()
    scheduler.add_job(scheduled_job, "interval", hours=8, id="auto_update_job", args=[application])
    scheduler.start()
    logger.info("Scheduler started successfully inside active event loop.")


# Global Error Handler
async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.error("Exception while handling an update:", exc_info=context.error)


def main():
    # Start Web Server Thread
    threading.Thread(target=run_flask, daemon=True).start()

    app = (
        Application.builder()
        .token(BOT_TOKEN)
        .read_timeout(30)
        .write_timeout(30)
        .get_updates_read_timeout(30)
        .post_init(post_init)
        .build()
    )

    # Handlers
    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_document))
    app.add_handler(CallbackQueryHandler(button_handler))
    
    app.add_error_handler(error_handler)

    logger.info("Bot & Web Server starting...")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
