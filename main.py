
import os
import sys
import subprocess
import threading
import time
import shutil
import zipfile
import tarfile
import sqlite3
import signal
import ast
import importlib
import importlib.util
import html as html_lib
import logging
import secrets
import pysqlite3 as sqlite3
from datetime import datetime

STOP_EVENT = threading.Event()

def install_requirements():
    requirements = ["pyTelegramBotAPI", "requests", "psutil"]
    for package in requirements:
        try:
            if package == "pyTelegramBotAPI":
                import telebot
            elif package == "psutil":
                import psutil
            elif package == "requests":
                import requests
            print(f"✅ {package} already installed")
        except ImportError:
            print(f"📦 Installing {package}...")
            try:
                subprocess.check_call([sys.executable, "-m", "pip", "install", package])
                print(f"✅ {package} installed successfully")
            except Exception as e:
                print(f"❌ Failed to install {package}: {e}")

install_requirements()

import psutil
import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup, KeyboardButton, InputFile

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)
logging.getLogger("telebot").setLevel(logging.CRITICAL)
logging.getLogger("urllib3").setLevel(logging.WARNING)

# ============ CONFIGURATION ============
BOT_TOKEN = "8741135835:AAEZSWb-mP15IFs1Hpd30GiDqlsYN0HGlh8"
OWNER_USERNAME = "bouchor"
CHANNEL_LINK = "https://t.me/+5VqU4mPSF9k5NGVl"
CHANNEL_ID =-1003476442442
ADMIN_IDS = [6653458698]

COST_PER_HOSTING = 2
INITIAL_COINS = 2
REFERRAL_REWARD_REFERRER = 2
REFERRAL_REWARD_NEW = 1

COIN_PACKAGES = {100: 20, 200: 40, 400: 60, 500: 80}
CPU_THRESHOLD = 1000.0
MEMORY_THRESHOLD = 1000.0
MAX_RUNNING_PROCESSES = 9999
MAX_FILES_PER_USER = int(os.environ.get("MAX_FILES_PER_USER", "15"))
MAX_RESTARTS = 3
RESTART_DELAY = 5

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
DB_PATH = os.path.join(DATA_DIR, "metadata.db")
UPLOADS_DIR = os.path.join(DATA_DIR, "uploads")
LOGS_DIR = os.path.join(DATA_DIR, "logs")
TEMP_DIR = os.path.join(DATA_DIR, "temp")

for directory in [DATA_DIR, UPLOADS_DIR, LOGS_DIR, TEMP_DIR]:
    os.makedirs(directory, exist_ok=True)

START_TIME = datetime.utcnow()
conn = sqlite3.connect(DB_PATH, check_same_thread=False)
conn.row_factory = sqlite3.Row
db_lock = threading.Lock()

def init_db():
    with db_lock:
        cur = conn.cursor()
        cur.execute('''CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY, username TEXT, joined_at TEXT, last_seen TEXT,
            coins INTEGER DEFAULT 0, referred_by INTEGER DEFAULT NULL, referral_code TEXT UNIQUE)''')
        cur.execute('''CREATE TABLE IF NOT EXISTS files (
            id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, username TEXT,
            filename TEXT, orig_name TEXT, path TEXT, uploaded_at TEXT, file_type TEXT,
            pid INTEGER, status TEXT DEFAULT 'Stopped', auto_restart INTEGER DEFAULT 1,
            restart_count INTEGER DEFAULT 0)''')
        cur.execute('''CREATE TABLE IF NOT EXISTS runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT, file_id INTEGER, started_at TEXT,
            finished_at TEXT, pid INTEGER, log_path TEXT, exit_code INTEGER)''')
        cur.execute('''CREATE TABLE IF NOT EXISTS referrals (
            id INTEGER PRIMARY KEY AUTOINCREMENT, referrer_id INTEGER, new_user_id INTEGER, referred_at TEXT)''')
        cur.execute('''CREATE TABLE IF NOT EXISTS purchase_requests (
            id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, coins INTEGER,
            amount_taka INTEGER, status TEXT DEFAULT 'pending', created_at TEXT)''')
        conn.commit()
        cur.execute("SELECT user_id FROM users WHERE referral_code IS NULL")
        rows = cur.fetchall()
        for row in rows:
            uid = row["user_id"]
            code = f"REF{uid}{secrets.token_hex(4)}"
            cur.execute("UPDATE users SET referral_code = ? WHERE user_id = ?", (code, uid))
        conn.commit()

init_db()

def get_or_create_user(user_id, username, referred_by_id=None):
    with db_lock:
        cur = conn.cursor()
        cur.execute("SELECT * FROM users WHERE user_id = ?", (user_id,))
        user = cur.fetchone()
        if user:
            cur.execute("UPDATE users SET last_seen = ?, username = ? WHERE user_id = ?",
                        (datetime.utcnow().isoformat(), username, user_id))
            conn.commit()
            return user
        else:
            ref_code = f"REF{user_id}{secrets.token_hex(4)}"
            cur.execute(
                "INSERT INTO users (user_id, username, joined_at, last_seen, coins, referred_by, referral_code) VALUES (?,?,?,?,?,?,?)",
                (user_id, username, datetime.utcnow().isoformat(), datetime.utcnow().isoformat(), INITIAL_COINS, referred_by_id, ref_code)
            )
            conn.commit()
            if referred_by_id:
                cur.execute("SELECT coins FROM users WHERE user_id = ?", (referred_by_id,))
                referrer = cur.fetchone()
                if referrer:
                    new_coins = referrer["coins"] + REFERRAL_REWARD_REFERRER
                    cur.execute("UPDATE users SET coins = ? WHERE user_id = ?", (new_coins, referred_by_id))
                    cur.execute("INSERT INTO referrals (referrer_id, new_user_id, referred_at) VALUES (?,?,?)",
                                (referred_by_id, user_id, datetime.utcnow().isoformat()))
                    conn.commit()
                    threading.Thread(target=lambda: bot.send_message(referred_by_id,
                        f"🎉 <b>Referral Reward!</b>\n\n@{username or user_id} joined using your link!\nYou earned <b>+{REFERRAL_REWARD_REFERRER} coins</b>."), daemon=True).start()
                cur.execute("UPDATE users SET coins = coins + ? WHERE user_id = ?", (REFERRAL_REWARD_NEW, user_id))
                conn.commit()
            cur.execute("SELECT * FROM users WHERE user_id = ?", (user_id,))
            return cur.fetchone()

def get_user_coins(user_id):
    cur = conn.cursor()
    cur.execute("SELECT coins FROM users WHERE user_id = ?", (user_id,))
    row = cur.fetchone()
    return row["coins"] if row else 0

def deduct_coins(user_id, amount):
    with db_lock:
        cur = conn.cursor()
        cur.execute("SELECT coins FROM users WHERE user_id = ?", (user_id,))
        row = cur.fetchone()
        if row and row["coins"] >= amount:
            cur.execute("UPDATE users SET coins = coins - ? WHERE user_id = ?", (amount, user_id))
            conn.commit()
            return True
        return False

def add_coins(user_id, amount):
    with db_lock:
        cur = conn.cursor()
        cur.execute("UPDATE users SET coins = coins + ? WHERE user_id = ?", (amount, user_id))
        conn.commit()

def get_referral_link(user_id):
    cur = conn.cursor()
    cur.execute("SELECT referral_code FROM users WHERE user_id = ?", (user_id,))
    row = cur.fetchone()
    if row:
        return f"https://t.me/{bot.get_me().username}?start=ref_{row['referral_code']}"
    return None

def find_user_by_referral_code(code):
    cur = conn.cursor()
    cur.execute("SELECT user_id FROM users WHERE referral_code = ?", (code,))
    row = cur.fetchone()
    return row["user_id"] if row else None

def add_file_record(user_id, username, filename, orig_name, path, file_type):
    with db_lock:
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO files (user_id, username, filename, orig_name, path, uploaded_at, file_type, auto_restart) VALUES (?,?,?,?,?,?,?,?)",
            (user_id, username, filename, orig_name, path, datetime.utcnow().isoformat(), file_type, 1)
        )
        conn.commit()
        return cur.lastrowid

def list_user_files(user_id):
    cur = conn.cursor()
    cur.execute("SELECT id, filename, orig_name, uploaded_at, file_type, status, pid, auto_restart FROM files WHERE user_id=? ORDER BY id DESC", (user_id,))
    return cur.fetchall()

def get_file_record(file_id):
    cur = conn.cursor()
    cur.execute("SELECT * FROM files WHERE id=?", (file_id,))
    return cur.fetchone()

def remove_file_record(file_id):
    with db_lock:
        cur = conn.cursor()
        cur.execute("DELETE FROM files WHERE id=?", (file_id,))
        conn.commit()

def record_run_start(file_id, pid, log_path):
    with db_lock:
        cur = conn.cursor()
        cur.execute("INSERT INTO runs (file_id, started_at, pid, log_path) VALUES (?,?,?,?)",
                    (file_id, datetime.utcnow().isoformat(), pid, log_path))
        conn.commit()
        return cur.lastrowid

def record_run_finish(run_id, exit_code):
    with db_lock:
        cur = conn.cursor()
        cur.execute("UPDATE runs SET finished_at=?, exit_code=? WHERE id=?", (datetime.utcnow().isoformat(), exit_code, run_id))
        conn.commit()

def update_file_status(file_id, pid, status):
    with db_lock:
        cur = conn.cursor()
        cur.execute("UPDATE files SET pid=?, status=? WHERE id=?", (pid, status, file_id))
        conn.commit()

def update_auto_restart(file_id, auto_restart):
    with db_lock:
        cur = conn.cursor()
        cur.execute("UPDATE files SET auto_restart=? WHERE id=?", (auto_restart, file_id))
        conn.commit()

def reset_restart_count(file_id):
    with db_lock:
        cur = conn.cursor()
        cur.execute("UPDATE files SET restart_count=0 WHERE id=?", (file_id,))
        conn.commit()

def increment_restart_count(file_id):
    with db_lock:
        cur = conn.cursor()
        cur.execute("UPDATE files SET restart_count = restart_count + 1 WHERE id=?", (file_id,))
        conn.commit()
        cur.execute("SELECT restart_count FROM files WHERE id=?", (file_id,))
        return cur.fetchone()["restart_count"]

processes = {}
proc_lock = threading.Lock()

def get_system_load():
    try:
        cpu = psutil.cpu_percent(interval=0.1)
        mem = psutil.virtual_memory().percent
        proc_count = len(processes)
        return float(cpu), float(mem), int(proc_count)
    except:
        return 0.0, 0.0, 0

def should_stop_due_to_load():
    return False, None

def get_file_type(filename):
    name = filename.lower()
    if name.endswith(".py"): return "python"
    if name.endswith(".js"): return "javascript"
    if name.endswith(".zip"): return "zip"
    if any(name.endswith(ext) for ext in [".tar", ".tar.gz", ".tgz"]): return "archive"
    return "unknown"

def extract_archive(file_path, extract_dir):
    try:
        if file_path.lower().endswith(".zip"):
            with zipfile.ZipFile(file_path, 'r') as zf:
                zf.extractall(extract_dir)
        elif file_path.lower().endswith(".tar.gz") or file_path.lower().endswith(".tgz"):
            with tarfile.open(file_path, 'r:gz') as tf:
                tf.extractall(extract_dir)
        elif file_path.lower().endswith(".tar"):
            with tarfile.open(file_path, 'r') as tf:
                tf.extractall(extract_dir)
        else:
            return False, "Unsupported archive format"
        return True, None
    except Exception as e:
        return False, str(e)

def find_main_file(directory):
    priority = ["main.py", "bot.py", "app.py", "server.py", "index.py", "script.py",
                "main.js", "bot.js", "app.js", "server.js", "index.js", "script.js"]
    for f in priority:
        p = os.path.join(directory, f)
        if os.path.isfile(p): return p
    for root, _, files in os.walk(directory):
        for f in priority:
            if f in files:
                return os.path.join(root, f)
    for root, _, files in os.walk(directory):
        for f in files:
            if f.endswith((".py", ".js")):
                return os.path.join(root, f)
    return None

def install_requirements_from_file(path, chat_id):
    if not os.path.exists(path):
        return True, "No requirements.txt"
    with open(path) as f:
        reqs = [l.strip() for l in f if l.strip() and not l.startswith('#')]
    if not reqs:
        return True, "Empty requirements.txt"
    ok, fail = 0, 0
    failed = []
    for pkg in reqs:
        try:
            subprocess.run([sys.executable, "-m", "pip", "install", pkg], capture_output=True, timeout=120, check=True)
            ok += 1
        except:
            fail += 1
            failed.append(pkg)
    if fail == 0:
        return True, f"✅ {ok} packages installed"
    else:
        return False, f"⚠️ {ok} ok, {fail} failed: {', '.join(failed[:3])}"

def extract_imports(file_path):
    imports = set()
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            tree = ast.parse(f.read())
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    imports.add(alias.name.split('.')[0])
            elif isinstance(node, ast.ImportFrom):
                if node.module and node.level == 0:
                    imports.add(node.module.split('.')[0])
    except:
        pass
    return imports

def install_missing_imports(imports, chat_id):
    missing = []
    for m in imports:
        try:
            importlib.import_module(m)
        except ImportError:
            missing.append(m)
    if not missing:
        return True, "All imports already installed"
    mapping = {'telebot': 'pyTelegramBotAPI', 'PIL': 'Pillow', 'cv2': 'opencv-python',
               'Crypto': 'pycryptodome', 'bs4': 'beautifulsoup4', 'yaml': 'pyyaml', 'dotenv': 'python-dotenv',
               'pyrogram': 'pyrogram', 'tgCrypto': 'tgCrypto'}
    ok, failed = 0, []
    for m in missing:
        pkg = mapping.get(m, m)
        try:
            subprocess.run([sys.executable, "-m", "pip", "install", pkg], capture_output=True, timeout=120, check=True)
            ok += 1
        except:
            failed.append(m)
    if not failed:
        return True, f"✅ {ok} missing modules installed"
    else:
        return False, f"⚠️ {ok} ok, {len(failed)} failed: {', '.join(failed[:3])}"

bot = telebot.TeleBot(BOT_TOKEN, parse_mode="HTML")

def _graceful_shutdown(signum=None, frame=None):
    STOP_EVENT.set()
    try:
        bot.stop_polling()
    except:
        pass
    conn.close()
    raise SystemExit(0)

signal.signal(signal.SIGINT, _graceful_shutdown)
signal.signal(signal.SIGTERM, _graceful_shutdown)

def main_menu_kb():
    kb = ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    kb.add(KeyboardButton("✨ Updates Channel"), KeyboardButton("📤 Upload File"))
    kb.add(KeyboardButton("📁 My Files"), KeyboardButton("💎 My Coins"))
    kb.add(KeyboardButton("🔗 Referral Link"), KeyboardButton("💳 Buy Points"))
    kb.add(KeyboardButton("⚡ Bot Speed"), KeyboardButton("📊 Statistics"))
    kb.add(KeyboardButton("👑 Contact Owner"), KeyboardButton("🔄 Auto‑Restart"))
    return kb

def file_actions_kb(file_id, is_running=False, auto_restart=1):
    kb = InlineKeyboardMarkup(row_width=2)
    if is_running:
        kb.add(InlineKeyboardButton("⏹ Stop", callback_data=f"stop:{file_id}"),
               InlineKeyboardButton("🔄 Restart", callback_data=f"restart:{file_id}"))
    else:
        kb.add(InlineKeyboardButton("▶️ Start", callback_data=f"start:{file_id}"),
               InlineKeyboardButton("🔄 Restart", callback_data=f"restart:{file_id}"))
    kb.add(InlineKeyboardButton("🗑 Delete", callback_data=f"delete:{file_id}"),
           InlineKeyboardButton("📄 Logs", callback_data=f"logs:{file_id}"))
    status = "✅ ON" if auto_restart else "❌ OFF"
    kb.add(InlineKeyboardButton(f"🔄 Auto‑Restart {status}", callback_data=f"autorestart:{file_id}"))
    kb.add(InlineKeyboardButton("⬅️ Back", callback_data="back_to_files"))
    return kb
def log_upload_to_channel(user, file_name, file_type, file_id, file_path):
    if CHANNEL_ID is None:
        logger.warning("CHANNEL_ID is not set. Skipping file upload log.")
        return
    time.sleep(1)
    try:
        with open(file_path, 'rb') as f:
            caption = (f"📁 <b>New File Uploaded</b>\n\n"
                       f"👤 <b>User:</b> {html_lib.escape(user.first_name)} (<code>{user.id}</code>)\n"
                       f"🆔 <b>Username:</b> @{user.username if user.username else 'N/A'}\n"
                       f"📄 <b>Filename:</b> {html_lib.escape(file_name)}\n"
                       f"🔖 <b>Type:</b> {file_type.upper()}\n"
                       f"🕒 <b>Time:</b> {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S UTC')}\n"
                       f"🆔 <b>File ID:</b> <code>{file_id}</code>")
            bot.send_document(CHANNEL_ID, InputFile(f, filename=file_name), caption=caption, parse_mode="HTML")
        logger.info(f"✅ Successfully logged file '{file_name}' to the channel.")
    except Exception as e:
        logger.error(f"❌ Failed to log file to channel: {e}")
        try:
            fallback_message = (f"🚨 <b>File Upload Log Failed (Document Send)</b>\n\n"
                                f"👤 <b>User:</b> {html_lib.escape(user.first_name)} (<code>{user.id}</code>)\n"
                                f"📄 <b>Filename:</b> {html_lib.escape(file_name)}\n"
                                f"❌ <b>Error:</b> {str(e)}")
            bot.send_message(CHANNEL_ID, fallback_message, parse_mode="HTML")
            logger.warning("Sent a fallback text message to the channel instead.")
        except Exception as final_e:
            logger.error(f"❌ CRITICAL: Could not send even a text message to the channel: {final_e}")

@bot.message_handler(commands=['start'])
def start_with_ref(message):
    text = message.text
    user = message.from_user
    referred_by_id = None
    if text.startswith('/start ref_'):
        code = text.split('ref_')[1].strip()
        rid = find_user_by_referral_code(code)
        if rid and rid != user.id:
            referred_by_id = rid
    user_record = get_or_create_user(user.id, user.username or "", referred_by_id)
    coins = user_record["coins"]
    files = list_user_files(user.id)
    try:
        bot.send_sticker(message.chat.id, "CAACAgIAAxkBAAEHBadjYqGtLrCxpX9vfqKzBQzFZBqyJAACawADpDufDvDlC5j9YZmmKQQ")
    except:
        pass
    welcome = (f"🔥 <b>HASIB HOSSEN TECH BOT HOSTING</b>\n\n"
               f"👋 Welcome <b>{html_lib.escape(user.first_name)}</b>\n"
               f"🆔 ID: <code>{user.id}</code>\n"
               f"💰 Coins: <code>{coins}</code>\n"
               f"📂 Files: {len(files)}/{MAX_FILES_PER_USER}\n\n"
               f"👇 Use the buttons below!")
    bot.send_message(message.chat.id, welcome, reply_markup=main_menu_kb())

@bot.message_handler(commands=['mycoins'])
def mycoins_command(message):
    coins = get_user_coins(message.from_user.id)
    bot.send_message(message.chat.id, f"💎 <b>Your Coin Balance</b>\n\n💰 <code>{coins}</code> coins")

@bot.message_handler(commands=['addcoins'])
def addcoins_command(message):
    user_id = message.from_user.id
    if user_id not in ADMIN_IDS:
        bot.reply_to(message, "❌ You are not authorized to use this command.")
        return
    args = message.text.split()
    if len(args) != 3:
        bot.reply_to(message, "⚠️ Usage: /addcoins <user_id> <amount>")
        return
    try:
        target_id = int(args[1])
        amount = int(args[2])
        if amount <= 0:
            bot.reply_to(message, "Amount must be positive.")
            return
        add_coins(target_id, amount)
        bot.reply_to(message, f"✅ Added <b>{amount}</b> coins to user <code>{target_id}</code>.")
        try:
            bot.send_message(target_id, f"🎉 <b>Admin Action</b>\n\nYou received <b>+{amount}</b> coins!\nNew balance: <b>{get_user_coins(target_id)}</b> coins.")
        except:
            pass
    except Exception as e:
        bot.reply_to(message, f"Error: {e}")

@bot.message_handler(func=lambda m: m.text == "✨ Updates Channel")
def updates_handler(message):
    kb = InlineKeyboardMarkup().add(InlineKeyboardButton("📢 Join Channel", url=CHANNEL_LINK))
    bot.send_message(message.chat.id, "✨ Stay updated! Join our channel:", reply_markup=kb)

@bot.message_handler(func=lambda m: m.text == "👑 Contact Owner")
def contact_handler(message):
    kb = InlineKeyboardMarkup().add(InlineKeyboardButton("👤 Message Owner", url=f"https://t.me/{OWNER_USERNAME}"))
    bot.send_message(message.chat.id, "💬 Need help? Contact owner:", reply_markup=kb)

@bot.message_handler(func=lambda m: m.text == "⚡ Bot Speed")
def speed_handler(message):
    cpu, mem, pc = get_system_load()
    uptime = datetime.utcnow() - START_TIME
    days = uptime.days
    hours, rem = divmod(uptime.seconds, 3600)
    mins = rem // 60
    bot.send_message(message.chat.id,
                     f"⚡ <b>System Status</b>\n\n• CPU: <code>{cpu:.1f}%</code>\n• Memory: <code>{mem:.1f}%</code>\n• Running: <code>{pc}</code> / {MAX_RUNNING_PROCESSES}\n• Uptime: {days}d {hours}h {mins}m")

@bot.message_handler(func=lambda m: m.text == "📊 Statistics")
def stats_handler(message):
    cur = conn.cursor()
    cur.execute("SELECT COUNT(DISTINCT user_id) FROM files")
    uc = cur.fetchone()[0] or 0
    cur.execute("SELECT COUNT(*) FROM files")
    fc = cur.fetchone()[0] or 0
    cur.execute("SELECT COUNT(*) FROM files WHERE status='Running'")
    rc = cur.fetchone()[0] or 0
    cur.execute("SELECT COUNT(*) FROM referrals")
    refc = cur.fetchone()[0] or 0
    cpu, mem, _ = get_system_load()
    bot.send_message(message.chat.id,
                     f"📊 <b>Statistics</b>\n\n👥 Users: <code>{uc}</code>\n📁 Files: <code>{fc}</code>\n🚀 Running: <code>{rc}</code>\n🤝 Referrals: <code>{refc}</code>\n⚡ CPU: {cpu:.1f}%\n💾 MEM: {mem:.1f}%")

@bot.message_handler(func=lambda m: m.text == "📁 My Files")
def my_files_handler(message):
    send_files_list(message.chat.id, message.from_user.id)

def send_files_list(chat_id, user_id):
    files = list_user_files(user_id)
    if not files:
        bot.send_message(chat_id, "📁 <b>Your Files</b>\n\nNo files uploaded yet.\nUse Upload button.")
        return
    text = "📁 <b>Your Files</b>\n\nClick a file to manage it:"
    kb = InlineKeyboardMarkup(row_width=1)
    for f in files:
        fid, fn, orig, _, ft, status, _, _ = f
        emoji = "🟢" if status == "Running" else "🔴"
        icon = "🐍" if ft == "python" else "📜" if ft == "javascript" else "🗃"
        kb.add(InlineKeyboardButton(f"{emoji} {icon} {html_lib.escape(orig)}", callback_data=f"manage:{fid}"))
    bot.send_message(chat_id, text, reply_markup=kb)

@bot.message_handler(func=lambda m: m.text == "💎 My Coins")
def my_coins_button(message):
    uid = message.from_user.id
    coins = get_user_coins(uid)
    link = get_referral_link(uid)
    text = (f"💎 <b>Your Coin Wallet</b>\n\n💰 Balance: <code>{coins}</code> coins\n\n🔗 <b>Referral Link:</b>\n<code>{link}</code>\n\n"
            f"✨ You earn +{REFERRAL_REWARD_REFERRER} per referral, they get +{REFERRAL_REWARD_NEW}.")
    bot.send_message(message.chat.id, text)

@bot.message_handler(func=lambda m: m.text == "🔗 Referral Link")
def referral_link_button(message):
    link = get_referral_link(message.from_user.id)
    bot.send_message(message.chat.id, f"🔗 <b>Your Link</b>\n\n<code>{link}</code>\n\nShare to earn coins!")

@bot.message_handler(func=lambda m: m.text == "💳 Buy Points")
def buy_points_button(message):
    text = ("💳 <b>COIN PRICE LIST</b>\n━━━━━━━━━━━━━━━━━━━━\n"
            "🔹 100 points : 20 TK\n🔹 200 points : 40 TK\n"
            "🔹 400 points : 60 TK\n🔹 500 points : 80 TK\n\n"
            "⚠️ Click the button below to buy points.")
    kb = InlineKeyboardMarkup(row_width=2)
    for coins, price in COIN_PACKAGES.items():
        kb.add(InlineKeyboardButton(f"💰 {coins} pts - {price} TK", callback_data=f"buy:{coins}:{price}"))
    bot.send_message(message.chat.id, text, reply_markup=kb)

@bot.message_handler(func=lambda m: m.text == "📤 Upload File")
def upload_handler(message):
    uid = message.from_user.id
    coins = get_user_coins(uid)
    if coins < COST_PER_HOSTING:
        bot.send_message(message.chat.id,
                         f"❌ <b>Low coins!</b>\n\nNeed {COST_PER_HOSTING} coins, you have {coins}.\n"
                         f"👉 refer and Earn or buy points.")
        return
    bot.send_message(message.chat.id,
                     f"📤 <b>Upload a File</b>\n\nCost: {COST_PER_HOSTING} coins (your balance: {coins})\n\n"
                     "Send .py / .js / .zip / .tar")

@bot.message_handler(content_types=['document'])
def document_handler(message):
    user = message.from_user
    uid = user.id

    if not deduct_coins(uid, COST_PER_HOSTING):
        bot.reply_to(message, f"❌ Not enough coins! Need {COST_PER_HOSTING}.")
        return

    if len(list_user_files(uid)) >= MAX_FILES_PER_USER:
        bot.reply_to(message, f"❌ File limit reached ({MAX_FILES_PER_USER}).")
        add_coins(uid, COST_PER_HOSTING)
        return

    try:
        file_info = bot.get_file(message.document.file_id)
        file_bytes = bot.download_file(file_info.file_path)
    except Exception as e:
        bot.reply_to(message, f"Download error: {e}")
        add_coins(uid, COST_PER_HOSTING)
        return

    orig_name = message.document.file_name or "unknown"
    ftype = get_file_type(orig_name)
    user_dir = os.path.join(UPLOADS_DIR, str(uid))
    os.makedirs(user_dir, exist_ok=True)
    safe_name = f"{int(time.time())}_{orig_name}"
    file_path = os.path.join(user_dir, safe_name)

    try:
        with open(file_path, 'wb') as f:
            f.write(file_bytes)
    except Exception as e:
        bot.reply_to(message, f"Save error: {e}")
        add_coins(uid, COST_PER_HOSTING)
        return

    final_path = file_path
    if ftype in ["zip", "archive"]:
        status_msg = bot.reply_to(message, "📦 Extracting archive...")
        extr_dir = os.path.join(TEMP_DIR, f"extracted_{uid}_{int(time.time())}")
        os.makedirs(extr_dir, exist_ok=True)
        ok, err = extract_archive(file_path, extr_dir)
        if not ok:
            bot.edit_message_text(f"Extract failed: {err}", status_msg.chat.id, status_msg.message_id)
            add_coins(uid, COST_PER_HOSTING)
            try: os.remove(file_path)
            except: pass
            return
        main_file = find_main_file(extr_dir)
        if not main_file:
            bot.edit_message_text("No main .py/.js found.", status_msg.chat.id, status_msg.message_id)
            add_coins(uid, COST_PER_HOSTING)
            try: shutil.rmtree(extr_dir); os.remove(file_path)
            except: pass
            return
        final_path = extr_dir
        ftype = get_file_type(main_file)
        bot.edit_message_text(f"✅ Main file: <code>{os.path.basename(main_file)}</code>", status_msg.chat.id, status_msg.message_id, parse_mode="HTML")

    file_id = add_file_record(uid, user.username, safe_name, orig_name, final_path, ftype)
    # --- CORRECTED: Passing 'file_path' (the path to the uploaded file) instead of 'final_path' ---
    log_upload_to_channel(user, orig_name, ftype, file_id, file_path)

    if ftype in ["python", "javascript"]:
        bot.reply_to(message, f"✅ Uploaded! Starting...\n💰 -{COST_PER_HOSTING} coins.")
        start_file_process(file_id, message.chat.id)
    else:
        bot.reply_to(message, f"✅ Uploaded! Use '📁 My Files' to manage.\n💰 -{COST_PER_HOSTING} coins.")

@bot.message_handler(func=lambda m: m.text == "🔄 Auto‑Restart Info")
def autorestart_info(message):
    bot.send_message(message.chat.id,
                     f"🔄 <b>Auto‑Restart System</b>\n\n• Max attempts: {MAX_RESTARTS}\n• Delay: {RESTART_DELAY}s\n• Toggle ON/OFF per file in manage menu.")

def start_file_process(file_id, chat_id, is_restart=False):
    should, reason = should_stop_due_to_load()
    if should:
        bot.send_message(chat_id, f"⚠️ Cannot start: {reason}")
        return False
    rec = get_file_record(file_id)
    if not rec:
        bot.send_message(chat_id, "File not found")
        return False
    if not is_restart:
        reset_restart_count(file_id)

    path = rec["path"]
    orig = rec["orig_name"]
    ftype = rec["file_type"]

    if os.path.isdir(path):
        main = find_main_file(path)
        if not main:
            bot.send_message(chat_id, "No main script found.")
            return False
        target = main
        workdir = os.path.dirname(main)
    else:
        if not os.path.exists(path):
            bot.send_message(chat_id, f"File missing: {path}")
            return False
        target = path
        workdir = os.path.dirname(path)

    ext = os.path.splitext(target)[1].lower()
    if ext == ".py":
        req_path = os.path.join(workdir, "requirements.txt")
        if os.path.exists(req_path):
            bot.send_message(chat_id, "📦 Installing requirements...")
            ok, msg = install_requirements_from_file(req_path, chat_id)
            bot.send_message(chat_id, msg)
        bot.send_message(chat_id, "🔍 Checking imports...")
        imports = extract_imports(target)
        if imports:
            ok, msg = install_missing_imports(imports, chat_id)
            bot.send_message(chat_id, msg)

    if ext == ".py":
        cmd = [sys.executable, target]
    elif ext == ".js":
        cmd = ["node", target]
    else:
        bot.send_message(chat_id, f"Unsupported file type: {ext}")
        return False

    log_name = f"file_{file_id}_{int(time.time())}.log"
    log_path = os.path.join(LOGS_DIR, log_name)

    try:
        with open(log_path, 'w') as lf:
            proc = subprocess.Popen(cmd, stdout=lf, stderr=subprocess.STDOUT, cwd=workdir, text=True)
        run_id = record_run_start(file_id, proc.pid, log_path)
        update_file_status(file_id, proc.pid, "Running")
        with proc_lock:
            processes[file_id] = {'process': proc, 'run_id': run_id, 'log_path': log_path, 'chat_id': chat_id, 'original_name': orig}
        bot.send_message(chat_id, f"✅ <b>{html_lib.escape(orig)}</b> started! PID: <code>{proc.pid}</code>")

        def monitor():
            try:
                code = proc.wait()
            except:
                code = -1
            finally:
                update_file_status(file_id, None, "Stopped")
                record_run_finish(run_id, code)
                with proc_lock:
                    processes.pop(file_id, None)
                if code != 0:
                    rec2 = get_file_record(file_id)
                    if rec2 and rec2["auto_restart"] and rec2["restart_count"] < MAX_RESTARTS:
                        new_cnt = increment_restart_count(file_id)
                        bot.send_message(chat_id, f"⚠️ {html_lib.escape(orig)} crashed (code {code}). Restarting in {RESTART_DELAY}s... (attempt {new_cnt}/{MAX_RESTARTS})")
                        time.sleep(RESTART_DELAY)
                        start_file_process(file_id, chat_id, is_restart=True)
                    else:
                        if rec2 and rec2["auto_restart"]:
                            bot.send_message(chat_id, f"❌ {html_lib.escape(orig)} crashed {MAX_RESTARTS} times. Auto-restart disabled.")
                else:
                    reset_restart_count(file_id)
        threading.Thread(target=monitor, daemon=True).start()
        return True
    except Exception as e:
        bot.send_message(chat_id, f"Start error: {e}")
        return False

def stop_file_process(file_id):
    with proc_lock:
        if file_id in processes:
            proc = processes[file_id]['process']
            try:
                if proc.poll() is None:
                    proc.terminate()
                    try:
                        proc.wait(5)
                    except:
                        proc.kill()
            except:
                pass
            processes.pop(file_id, None)
    update_file_status(file_id, None, "Stopped")
    reset_restart_count(file_id)

def get_file_logs(file_id, lines=100):
    try:
        with proc_lock:
            if file_id in processes:
                lp = processes[file_id]['log_path']
                if os.path.exists(lp):
                    with open(lp) as f:
                        return ''.join(f.readlines()[-lines:])
        cur = conn.cursor()
        cur.execute("SELECT log_path FROM runs WHERE file_id=? ORDER BY started_at DESC LIMIT 1", (file_id,))
        row = cur.fetchone()
        if row and row[0] and os.path.exists(row[0]):
            with open(row[0]) as f:
                return ''.join(f.readlines()[-lines:])
        return "No logs found"
    except Exception as e:
        return f"Error: {e}"

@bot.callback_query_handler(func=lambda call: True)
def callback_handler(call):
    data = call.data
    chat_id = call.message.chat.id
    user_id = call.from_user.id

    if data == "back_to_files":
        try: bot.delete_message(chat_id, call.message.message_id)
        except: pass
        send_files_list(chat_id, user_id)
        return

    try:
        if data.startswith("manage:"):
            fid = int(data.split(":")[1])
            show_file_management(chat_id, fid, user_id, call.message.message_id)

        elif data.startswith("start:"):
            fid = int(data.split(":")[1])
            bot.answer_callback_query(call.id, "Starting...")
            start_file_process(fid, chat_id)
            time.sleep(1)
            show_file_management(chat_id, fid, user_id, call.message.message_id)

        elif data.startswith("stop:"):
            fid = int(data.split(":")[1])
            bot.answer_callback_query(call.id, "Stopping...")
            stop_file_process(fid)
            bot.send_message(chat_id, "⏹ Process stopped")
            time.sleep(1)
            show_file_management(chat_id, fid, user_id, call.message.message_id)

        elif data.startswith("restart:"):
            fid = int(data.split(":")[1])
            bot.answer_callback_query(call.id, "Restarting...")
            stop_file_process(fid)
            time.sleep(2)
            start_file_process(fid, chat_id)
            time.sleep(1)
            show_file_management(chat_id, fid, user_id, call.message.message_id)

        elif data.startswith("autorestart:"):
            fid = int(data.split(":")[1])
            rec = get_file_record(fid)
            if rec:
                new_val = 0 if rec["auto_restart"] else 1
                update_auto_restart(fid, new_val)
                bot.answer_callback_query(call.id, f"Auto‑restart {'ON' if new_val else 'OFF'}")
                show_file_management(chat_id, fid, user_id, call.message.message_id)

        elif data.startswith("delete:"):
            fid = int(data.split(":")[1])
            bot.answer_callback_query(call.id, "Deleting...")
            rec = get_file_record(fid)
            if rec:
                stop_file_process(fid)
                fpath = rec["path"]
                try:
                    if os.path.isdir(fpath):
                        shutil.rmtree(fpath, ignore_errors=True)
                    elif os.path.exists(fpath):
                        os.remove(fpath)
                except Exception as e:
                    logger.error(f"Delete error: {e}")
                remove_file_record(fid)
            bot.send_message(chat_id, "🗑 File deleted")
            send_files_list(chat_id, user_id)

        elif data.startswith("logs:"):
            fid = int(data.split(":")[1])
            bot.answer_callback_query(call.id, "Fetching logs...")
            logs = get_file_logs(fid)
            rec = get_file_record(fid)
            fname = rec["orig_name"] if rec else "Unknown"
            if len(logs) > 4000:
                logs = logs[-4000:] + "\n... (truncated)"
            bot.send_message(chat_id, f"📄 <b>Logs for {html_lib.escape(fname)}</b>\n\n<pre>{html_lib.escape(logs)}</pre>")

        elif data.startswith("buy:"):
            parts = data.split(":")
            if len(parts) == 3:
                coins = int(parts[1])
                price = int(parts[2])
                user = call.from_user
                msg = (f"💳 <b>Purchase Request</b>\n\n👤 {html_lib.escape(user.first_name)} (@{user.username or 'N/A'})\n"
                       f"🆔 <code>{user.id}</code>\n💰 Coins: {coins}\n💵 Price: {price} TK\n\nUse /addcoins {user.id} {coins}")
                for admin_id in ADMIN_IDS:
                    try:
                        bot.send_message(admin_id, msg)
                    except:
                        pass
                bot.answer_callback_query(call.id, "Request sent to admin.")
                bot.send_message(chat_id, "✅ Purchase request sent. Admin will add coins after payment confirmation.")

    except Exception as e:
        bot.answer_callback_query(call.id, "Error processing request")
        logger.error(f"Callback error: {e}")

def show_file_management(chat_id, file_id, user_id, message_id=None):
    rec = get_file_record(file_id)
    if not rec or rec["user_id"] != user_id:
        bot.send_message(chat_id, "Access denied or file not found.")
        return
    with proc_lock:
        running = file_id in processes
    status = "🟢 Running" if running else "🔴 Stopped"
    pid_text = f"\n🆔 PID: <code>{rec['pid']}</code>" if rec['pid'] else ""
    autotxt = "✅ ON" if rec['auto_restart'] else "❌ OFF"
    text = (f"⚙️ <b>Manage File</b>\n\n📁 {html_lib.escape(rec['orig_name'])}\n"
            f"📊 Type: {rec['file_type']}\n📈 Status: {status}{pid_text}\n🔄 Auto‑Restart: {autotxt}\n"
            f"⏰ Uploaded: {rec['uploaded_at'][:16]}")
    kb = file_actions_kb(file_id, running, rec['auto_restart'])
    if message_id:
        try: bot.edit_message_text(text, chat_id, message_id, reply_markup=kb)
        except: bot.send_message(chat_id, text, reply_markup=kb)
    else:
        bot.send_message(chat_id, text, reply_markup=kb)

def start_bot():
    logger.info("🚀 Starting bot...")
    if CHANNEL_ID:
        try:
            bot.send_message(CHANNEL_ID, "✅ Bot online. File logging enabled.")
        except Exception as e:
            logger.error(f"Channel test error: {e}")
    while True:
        try:
            bot.infinity_polling(timeout=60, long_polling_timeout=50)
        except KeyboardInterrupt:
            break
        except SystemExit:
            break
        except Exception as e:
            logger.error(f"Polling error: {e}")
            time.sleep(5)
        if STOP_EVENT.is_set():
            break

if __name__ == "__main__":
    start_bot()
