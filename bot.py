import asyncio
import random
import sqlite3
import hashlib
import time
import json
from datetime import datetime, timedelta
from typing import Dict, Optional

from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery, Message, LabeledPrice, PreCheckoutQuery
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.enums import ParseMode

# ==================== КОНФИГУРАЦИЯ ====================
TOKEN = "НОВЫЙ_ТОКЕН_ПОСЛЕ_РЕВОКА"  # ЗАМЕНИ НА НОВЫЙ ТОКЕН!
ADMIN_IDS = [8478884644, 8293927811]
STARS_TO_POCX = 2500

MIN_BET = 100
MAX_BET = 500000
HOUSE_EDGE = 0.01
CRASH_HOUSE_EDGE = 0.03

DAILY_BONUS_MIN = 100
DAILY_BONUS_MAX = 2000
WELCOME_BONUS = 1000
REF_REWARD = 500

# ТВОИ КАНАЛЫ ДЛЯ ПОДПИСКИ (только они!)
REQUIRED_CHANNELS = [
    {"chat_id": "@POCXCHANEL", "link": "https://t.me/POCXCHANEL", "name": "📢 POCX Канал"},
    {"chat_id": "@POCXCHAT", "link": "https://t.me/POCXCHAT", "name": "💬 POCX Чат"},
]

VIP_LEVELS = [
    ("🥉 Bronze", 0, 0),
    ("🥈 Silver", 100000, 1),
    ("🥇 Gold", 500000, 2),
    ("💎 Platinum", 2000000, 3),
    ("👑 Diamond", 10000000, 5),
]

bot = Bot(token=TOKEN)
dp = Dispatcher()

# ==================== БАЗА ДАННЫХ ====================
conn = sqlite3.connect("pocx_bot.db", check_same_thread=False)
cursor = conn.cursor()

cursor.execute("""
CREATE TABLE IF NOT EXISTS users (
    user_id INTEGER PRIMARY KEY,
    username TEXT,
    balance INTEGER DEFAULT 5000,
    total_wagered INTEGER DEFAULT 0,
    total_won INTEGER DEFAULT 0,
    total_lost INTEGER DEFAULT 0,
    referral_code TEXT UNIQUE,
    referrer_id INTEGER,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    last_daily_bonus TIMESTAMP,
    welcome_bonus_claimed BOOLEAN DEFAULT 0,
    daily_streak INTEGER DEFAULT 0,
    last_game_time INTEGER DEFAULT 0,
    total_activations_used INTEGER DEFAULT 0,
    weekly_loss INTEGER DEFAULT 0,
    last_cashback_at TIMESTAMP
)
""")

cursor.execute("""
CREATE TABLE IF NOT EXISTS game_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER,
    game_type TEXT,
    bet_amount INTEGER,
    multiplier REAL,
    win_amount INTEGER,
    result TEXT,
    details TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)
""")

cursor.execute("""
CREATE TABLE IF NOT EXISTS referrals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    referrer_id INTEGER,
    referred_id INTEGER,
    earned_amount INTEGER DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)
""")

cursor.execute("""
CREATE TABLE IF NOT EXISTS banned_users (
    user_id INTEGER PRIMARY KEY,
    banned_at INTEGER,
    reason TEXT
)
""")

conn.commit()

# ==================== ВСПОМОГАТЕЛЬНЫЕ ====================
def add_pocx(user_id: int, amount: int):
    cursor.execute("UPDATE users SET balance = balance + ? WHERE user_id = ?", (amount, user_id))
    if cursor.rowcount == 0:
        cursor.execute("INSERT INTO users (user_id, balance) VALUES (?, ?)", (user_id, amount))
    conn.commit()

def remove_pocx(user_id: int, amount: int) -> bool:
    cursor.execute("SELECT balance FROM users WHERE user_id = ?", (user_id,))
    res = cursor.fetchone()
    if not res or res[0] < amount:
        return False
    cursor.execute("UPDATE users SET balance = balance - ? WHERE user_id = ?", (amount, user_id))
    conn.commit()
    return True

def get_user(user_id: int) -> dict:
    cursor.execute("SELECT * FROM users WHERE user_id = ?", (user_id,))
    row = cursor.fetchone()
    if not row:
        return None
    return {
        "user_id": row[0],
        "username": row[1],
        "balance": row[2],
        "total_wagered": row[3],
        "total_won": row[4],
        "total_lost": row[5],
        "referral_code": row[6],
        "referrer_id": row[7],
        "daily_streak": row[11] or 0,
        "welcome_bonus_claimed": row[10],
    }

def get_vip(wagered: int) -> tuple:
    current = VIP_LEVELS[0]
    for lvl in VIP_LEVELS:
        if wagered >= lvl[1]:
            current = lvl
    return current[0], current[2]

def check_cooldown(user_id: int) -> bool:
    cursor.execute("SELECT last_game_time FROM users WHERE user_id = ?", (user_id,))
    res = cursor.fetchone()
    now = int(time.time())
    if res and res[0]:
        if now - res[0] < 5:
            return False
    cursor.execute("UPDATE users SET last_game_time = ? WHERE user_id = ?", (now, user_id))
    if cursor.rowcount == 0:
        cursor.execute("INSERT INTO users (user_id, last_game_time) VALUES (?, ?)", (user_id, now))
    conn.commit()
    return True

def is_banned(user_id: int) -> bool:
    cursor.execute("SELECT 1 FROM banned_users WHERE user_id = ?", (user_id,))
    return cursor.fetchone() is not None

def ban_user(user_id: int, reason: str = ""):
    cursor.execute("INSERT OR IGNORE INTO banned_users (user_id, banned_at, reason) VALUES (?, ?, ?)",
                   (user_id, int(time.time()), reason))

def unban_user(user_id: int):
    cursor.execute("DELETE FROM banned_users WHERE user_id = ?", (user_id,))

def record_game(user_id: int, game_type: str, bet: int, multiplier: float, win: int, result: str, details: dict):
    cursor.execute("""
        UPDATE users SET 
            total_wagered = total_wagered + ?,
            total_won = total_won + ?,
            total_lost = total_lost + ?,
            weekly_loss = weekly_loss + ?
        WHERE user_id = ?
    """, (bet, win if result == "win" else 0, bet if result == "loss" else 0, bet if result == "loss" else 0, user_id))
    cursor.execute("""
        INSERT INTO game_history (user_id, game_type, bet_amount, multiplier, win_amount, result, details)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (user_id, game_type, bet, multiplier, win, result, json.dumps(details)))
    conn.commit()

# ==================== ПРОВЕРКА ПОДПИСКИ (ТОЛЬКО ТВОИ КАНАЛЫ) ====================
async def check_subscription(user_id: int) -> tuple[bool, list]:
    not_subscribed = []
    for channel in REQUIRED_CHANNELS:
        try:
            member = await bot.get_chat_member(chat_id=channel["chat_id"], user_id=user_id)
            if member.status in ["left", "kicked"]:
                not_subscribed.append(channel)
        except Exception:
            not_subscribed.append(channel)
    return len(not_subscribed) == 0, not_subscribed

def subscription_keyboard(not_subscribed: list) -> InlineKeyboardMarkup:
    kb = []
    for channel in not_subscribed:
        kb.append([InlineKeyboardButton(text=f"📢 Подписаться на {channel['name']}", url=channel["link"])])
    kb.append([InlineKeyboardButton(text="🔄 Проверить подписку", callback_data="check_subscription")])
    return InlineKeyboardMarkup(inline_keyboard=kb)

# ==================== MIDDLEWARE ====================
@dp.message()
async def subscription_middleware(message: Message):
    if is_banned(message.from_user.id):
        await message.answer("❌ Вы забанены!")
        return
    ok, not_subscribed = await check_subscription(message.from_user.id)
    if not ok and message.text != "/start":
        await message.answer(
            "❓ Для использования бота необходимо подписаться на каналы:",
            reply_markup=subscription_keyboard(not_subscribed)
        )
        return

@dp.callback_query()
async def subscription_callback_middleware(callback: CallbackQuery):
    if callback.data == "check_subscription":
        return
    if is_banned(callback.from_user.id):
        await callback.answer("❌ Вы забанены!", show_alert=True)
        return
    ok, not_subscribed = await check_subscription(callback.from_user.id)
    if not ok:
        await callback.answer("❓ Подпишитесь на каналы!", show_alert=True)
        return

@dp.callback_query(F.data == "check_subscription")
async def check_sub(callback: CallbackQuery):
    ok, not_subscribed = await check_subscription(callback.from_user.id)
    if ok:
        await callback.message.edit_text("✅ Спасибо за подписку! Теперь вы можете пользоваться ботом.", reply_markup=main_menu())
    else:
        await callback.message.edit_text(
            "❓ Для использования бота необходимо подписаться на каналы:",
            reply_markup=subscription_keyboard(not_subscribed)
        )
    await callback.answer()

# ==================== КЛАВИАТУРЫ ====================
def main_menu():
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🎮 Игры", callback_data="games")],
        [InlineKeyboardButton(text="💰 Финансы", callback_data="finance")],
        [InlineKeyboardButton(text="🏆 Топы", callback_data="top_menu")],
        [InlineKeyboardButton(text="🎁 Бонусы", callback_data="bonuses")],
        [InlineKeyboardButton(text="👥 Рефералы", callback_data="referrals")],
        [InlineKeyboardButton(text="📊 Статистика", callback_data="stats")],
        [InlineKeyboardButton(text="ℹ️ Профиль", callback_data="profile")]
    ])
    if 8478884644 in ADMIN_IDS or 8293927811 in ADMIN_IDS:
        kb.inline_keyboard.append([InlineKeyboardButton(text="👑 Админ-панель", callback_data="admin_panel")])
    return kb

def games_menu():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🎲 Dice", callback_data="game_dice")],
        [InlineKeyboardButton(text="🚀 Crash", callback_data="game_crash")],
        [InlineKeyboardButton(text="🗼 Башня", callback_data="game_tower")],
        [InlineKeyboardButton(text="💣 Mines", callback_data="game_mines")],
        [InlineKeyboardButton(text="🎡 Roulette", callback_data="game_roulette")],
        [InlineKeyboardButton(text="🎰 Слоты", callback_data="game_slots")],
        [InlineKeyboardButton(text="🪙 Coin Flip", callback_data="game_coin")],
        [InlineKeyboardButton(text="🃏 Hi-Lo", callback_data="game_hilo")],
        [InlineKeyboardButton(text="🔙 Назад", callback_data="main")]
    ])

def admin_menu():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📊 Статистика", callback_data="admin_stats")],
        [InlineKeyboardButton(text="➕ Выдать POCX", callback_data="admin_give")],
        [InlineKeyboardButton(text="➖ Забрать POCX", callback_data="admin_take")],
        [InlineKeyboardButton(text="🔨 Забанить", callback_data="admin_ban")],
        [InlineKeyboardButton(text="🔓 Разбанить", callback_data="admin_unban")],
        [InlineKeyboardButton(text="📢 Рассылка", callback_data="admin_broadcast")],
        [InlineKeyboardButton(text="🔙 Назад", callback_data="main")]
    ])

def top_menu():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🏆 За всё время", callback_data="top_all")],
        [InlineKeyboardButton(text="📅 За неделю", callback_data="top_week")],
        [InlineKeyboardButton(text="📆 За день", callback_data="top_day")],
        [InlineKeyboardButton(text="👥 Топ рефералов", callback_data="top_refs")],
        [InlineKeyboardButton(text="🔙 Назад", callback_data="main")]
    ])

def bonuses_menu():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🎁 Ежедневный бонус", callback_data="daily")],
        [InlineKeyboardButton(text="🆕 Приветственный бонус", callback_data="welcome")],
        [InlineKeyboardButton(text="🔙 Назад", callback_data="main")]
    ])

def finance_menu():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💰 Пополнить (Stars)", callback_data="donate")],
        [InlineKeyboardButton(text="🔙 Назад", callback_data="main")]
    ])

# ==================== КОМАНДЫ ====================
@dp.message(Command("start"))
async def cmd_start(message: Message):
    uid = message.from_user.id
    uname = message.from_user.username
    
    user = get_user(uid)
    if not user:
        code = hashlib.md5(f"{uid}{time.time()}".encode()).hexdigest()[:8]
        cursor.execute("INSERT INTO users (user_id, username, referral_code, balance) VALUES (?, ?, ?, ?)", (uid, uname, code, 5000))
        conn.commit()
        user = get_user(uid)
    
    # Реферальная система
    args = message.text.split()
    if len(args) > 1 and args[1].startswith("ref"):
        ref_code = args[1][3:]
        cursor.execute("SELECT user_id FROM users WHERE referral_code = ?", (ref_code,))
        ref = cursor.fetchone()
        if ref and ref[0] != uid:
            cursor.execute("INSERT OR IGNORE INTO referrals (referrer_id, referred_id) VALUES (?, ?)", (ref[0], uid))
            add_pocx(ref[0], 500)
            conn.commit()
    
    # Проверка подписки при старте
    ok, not_subscribed = await check_subscription(uid)
    if not ok:
        await message.answer(
            "❓ Для использования бота необходимо подписаться на каналы:",
            reply_markup=subscription_keyboard(not_subscribed)
        )
        return
    
    vip, _ = get_vip(user["total_wagered"])
    await message.answer(
        f"🎰 <b>POCX Casino Bot</b>\n\n"
        f"💰 Баланс: <b>{user['balance']:,} POCX</b>\n"
        f"💎 VIP: {vip}\n\n"
        f"🎮 Игры: Dice • Crash • Башня • Mines • Roulette • Слоты • Coin Flip • Hi-Lo\n\n"
        f"⭐ 1 Star = 2500 POCX\n"
        f"🔥 КД между играми: 5 секунд",
        reply_markup=main_menu(),
        parse_mode=ParseMode.HTML
    )

@dp.callback_query(F.data == "main")
async def cb_main(callback: CallbackQuery):
    user = get_user(callback.from_user.id)
    vip, _ = get_vip(user["total_wagered"])
    await callback.message.edit_text(
        f"🎰 <b>Главное меню</b>\n\n💰 Баланс: <b>{user['balance']:,} POCX</b> | {vip}",
        reply_markup=main_menu(),
        parse_mode=ParseMode.HTML
    )

@dp.callback_query(F.data == "games")
async def cb_games(callback: CallbackQuery):
    await callback.message.edit_text("🎮 <b>Выбери игру</b>", reply_markup=games_menu(), parse_mode=ParseMode.HTML)

@dp.callback_query(F.data == "profile")
async def cb_profile(callback: CallbackQuery):
    user = get_user(callback.from_user.id)
    vip, cb_pct = get_vip(user["total_wagered"])
    await callback.message.edit_text(
        f"👤 <b>Профиль</b>\n\n"
        f"🆔 ID: {user['user_id']}\n"
        f"📛 Имя: {callback.from_user.first_name}\n"
        f"💰 Баланс: {user['balance']:,} POCX\n"
        f"💎 VIP: {vip} (кэшбэк {cb_pct}%)\n"
        f"📊 Всего поставлено: {user['total_wagered']:,} POCX\n"
        f"🏆 Выиграно: {user['total_won']:,} POCX\n"
        f"💸 Проиграно: {user['total_lost']:,} POCX\n"
        f"🔥 Серия дней: {user['daily_streak']}",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔙 Назад", callback_data="main")]]),
        parse_mode=ParseMode.HTML
    )

@dp.callback_query(F.data == "stats")
async def cb_stats(callback: CallbackQuery):
    user = get_user(callback.from_user.id)
    cursor.execute("SELECT COUNT(*) FROM game_history WHERE user_id = ?", (callback.from_user.id,))
    played = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(*) FROM game_history WHERE user_id = ? AND result = 'win'", (callback.from_user.id,))
    wins = cursor.fetchone()[0]
    cursor.execute("SELECT MAX(multiplier) FROM game_history WHERE user_id = ? AND result = 'win'", (callback.from_user.id,))
    best = cursor.fetchone()[0] or 0
    winrate = wins / max(played, 1) * 100
    roi = (user["total_won"] - user["total_lost"]) / max(user["total_wagered"], 1) * 100
    
    await callback.message.edit_text(
        f"📊 <b>Статистика</b>\n\n"
        f"🎮 Игр сыграно: {played}\n"
        f"✅ Побед: {wins} ({winrate:.1f}%)\n"
        f"📈 Лучший множитель: {best:.2f}x\n"
        f"📊 ROI: {roi:.1f}%\n"
        f"💰 Общий вейджер: {user['total_wagered']:,} POCX\n"
        f"🏆 Выиграно всего: {user['total_won']:,} POCX",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔙 Назад", callback_data="main")]]),
        parse_mode=ParseMode.HTML
    )

@dp.callback_query(F.data == "referrals")
async def cb_referrals(callback: CallbackQuery):
    user = get_user(callback.from_user.id)
    cursor.execute("SELECT COUNT(*) FROM referrals WHERE referrer_id = ?", (callback.from_user.id,))
    count = cursor.fetchone()[0]
    cursor.execute("SELECT COALESCE(SUM(earned_amount), 0) FROM referrals WHERE referrer_id = ?", (callback.from_user.id,))
    earned = cursor.fetchone()[0]
    bot_info = await bot.get_me()
    link = f"https://t.me/{bot_info.username}?start=ref{user['referral_code']}"
    
    await callback.message.edit_text(
        f"👥 <b>Рефералы</b>\n\n"
        f"🔗 Ваша ссылка:\n<code>{link}</code>\n\n"
        f"👤 Рефералов: {count}\n"
        f"💰 Заработано: {earned:,} POCX\n\n"
        f"🎁 За каждого друга: +500 POCX",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔙 Назад", callback_data="main")]]),
        parse_mode=ParseMode.HTML
    )

# ==================== БОНУСЫ ====================
@dp.callback_query(F.data == "bonuses")
async def cb_bonuses(callback: CallbackQuery):
    await callback.message.edit_text(
        "🎁 <b>Бонусы</b>\n\nЕжедневный бонус: 100-2000 POCX\nПриветственный: 1000 POCX",
        reply_markup=bonuses_menu(),
        parse_mode=ParseMode.HTML
    )

@dp.callback_query(F.data == "daily")
async def cb_daily(callback: CallbackQuery):
    uid = callback.from_user.id
    cursor.execute("SELECT last_daily_bonus, daily_streak FROM users WHERE user_id = ?", (uid,))
    row = cursor.fetchone()
    last = row[0]
    streak = row[1] or 0
    
    if last:
        last_dt = datetime.fromisoformat(last)
        if datetime.now() - last_dt < timedelta(days=1):
            await callback.answer("❌ Бонус уже получен сегодня!", show_alert=True)
            return
    
    bonus = random.randint(100, 2000)
    bonus = int(bonus * (1 + min(streak * 0.05, 0.5)))
    add_pocx(uid, bonus)
    cursor.execute("UPDATE users SET last_daily_bonus = CURRENT_TIMESTAMP, daily_streak = daily_streak + 1 WHERE user_id = ?", (uid,))
    conn.commit()
    
    await callback.message.edit_text(
        f"🎁 <b>Ежедневный бонус!</b>\n\n"
        f"💰 +{bonus:,} POCX\n"
        f"🔥 Серия дней: {streak + 1}\n"
        f"📈 Бонус увеличен на {min(streak * 5, 50)}%!",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔙 Назад", callback_data="bonuses")]]),
        parse_mode=ParseMode.HTML
    )

@dp.callback_query(F.data == "welcome")
async def cb_welcome(callback: CallbackQuery):
    uid = callback.from_user.id
    cursor.execute("SELECT welcome_bonus_claimed FROM users WHERE user_id = ?", (uid,))
    claimed = cursor.fetchone()[0]
    if claimed:
        await callback.answer("❌ Приветственный бонус уже получен!", show_alert=True)
        return
    
    add_pocx(uid, 1000)
    cursor.execute("UPDATE users SET welcome_bonus_claimed = 1 WHERE user_id = ?", (uid,))
    conn.commit()
    
    await callback.message.edit_text(
        f"🎁 <b>Приветственный бонус!</b>\n\n💰 +1000 POCX",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔙 Назад", callback_data="bonuses")]]),
        parse_mode=ParseMode.HTML
    )

# ==================== ТОПЫ ====================
@dp.callback_query(F.data == "top_menu")
async def cb_top_menu(callback: CallbackQuery):
    await callback.message.edit_text("🏆 <b>Топы</b>\n\nВыбери категорию:", reply_markup=top_menu(), parse_mode=ParseMode.HTML)

@dp.callback_query(F.data == "top_all")
async def cb_top_all(callback: CallbackQuery):
    cursor.execute("SELECT user_id, username, total_won FROM users ORDER BY total_won DESC LIMIT 10")
    rows = cursor.fetchall()
    medals = ["🥇", "🥈", "🥉"]
    lines = ["🏆 <b>Топ за всё время</b>\n"]
    for i, row in enumerate(rows):
        medal = medals[i] if i < 3 else f"{i+1}."
        name = row[1] or f"ID{row[0]}"
        lines.append(f"{medal} {name} — <b>{row[2]:,} POCX</b>")
    await callback.message.edit_text("\n".join(lines), reply_markup=top_menu(), parse_mode=ParseMode.HTML)

@dp.callback_query(F.data == "top_week")
async def cb_top_week(callback: CallbackQuery):
    cursor.execute("""
        SELECT u.user_id, u.username, SUM(gh.win_amount) as total
        FROM game_history gh JOIN users u ON u.user_id = gh.user_id
        WHERE gh.created_at >= date('now', '-7 days') AND gh.result = 'win'
        GROUP BY gh.user_id ORDER BY total DESC LIMIT 10
    """)
    rows = cursor.fetchall()
    medals = ["🥇", "🥈", "🥉"]
    lines = ["🏆 <b>Топ за неделю</b>\n"]
    for i, row in enumerate(rows):
        medal = medals[i] if i < 3 else f"{i+1}."
        name = row[1] or f"ID{row[0]}"
        lines.append(f"{medal} {name} — <b>{row[2]:,} POCX</b>")
    await callback.message.edit_text("\n".join(lines), reply_markup=top_menu(), parse_mode=ParseMode.HTML)

@dp.callback_query(F.data == "top_day")
async def cb_top_day(callback: CallbackQuery):
    cursor.execute("""
        SELECT u.user_id, u.username, SUM(gh.win_amount) as total
        FROM game_history gh JOIN users u ON u.user_id = gh.user_id
        WHERE gh.created_at >= date('now', '-1 day') AND gh.result = 'win'
        GROUP BY gh.user_id ORDER BY total DESC LIMIT 10
    """)
    rows = cursor.fetchall()
    medals = ["🥇", "🥈", "🥉"]
    lines = ["🏆 <b>Топ за день</b>\n"]
    for i, row in enumerate(rows):
        medal = medals[i] if i < 3 else f"{i+1}."
        name = row[1] or f"ID{row[0]}"
        lines.append(f"{medal} {name} — <b>{row[2]:,} POCX</b>")
    await callback.message.edit_text("\n".join(lines), reply_markup=top_menu(), parse_mode=ParseMode.HTML)

@dp.callback_query(F.data == "top_refs")
async def cb_top_refs(callback: CallbackQuery):
    cursor.execute("""
        SELECT u.username, COUNT(r.referred_id), COALESCE(SUM(r.earned_amount), 0)
        FROM referrals r JOIN users u ON u.user_id = r.referrer_id
        GROUP BY r.referrer_id ORDER BY COUNT(r.referred_id) DESC LIMIT 10
    """)
    rows = cursor.fetchall()
    medals = ["🥇", "🥈", "🥉"]
    lines = ["👥 <b>Топ рефераловодов</b>\n"]
    for i, row in enumerate(rows):
        medal = medals[i] if i < 3 else f"{i+1}."
        name = row[0] or "Аноним"
        lines.append(f"{medal} {name} — <b>{row[1]}</b> реф. | {row[2]:,} POCX")
    await callback.message.edit_text("\n".join(lines), reply_markup=top_menu(), parse_mode=ParseMode.HTML)

# ==================== ИГРЫ (заглушки — добавь остальные игры по аналогии) ====================
@dp.callback_query(F.data == "game_dice")
async def cb_dice(callback: CallbackQuery):
    await callback.answer("🎲 Игра Dice в разработке!", show_alert=True)

@dp.callback_query(F.data == "game_crash")
async def cb_crash(callback: CallbackQuery):
    await callback.answer("🚀 Игра Crash в разработке!", show_alert=True)

@dp.callback_query(F.data == "game_tower")
async def cb_tower(callback: CallbackQuery):
    await callback.answer("🗼 Игра Башня в разработке!", show_alert=True)

@dp.callback_query(F.data == "game_mines")
async def cb_mines(callback: CallbackQuery):
    await callback.answer("💣 Игра Mines в разработке!", show_alert=True)

@dp.callback_query(F.data == "game_roulette")
async def cb_roulette(callback: CallbackQuery):
    await callback.answer("🎡 Игра Рулетка в разработке!", show_alert=True)

@dp.callback_query(F.data == "game_slots")
async def cb_slots(callback: CallbackQuery):
    await callback.answer("🎰 Игра Слоты в разработке!", show_alert=True)

@dp.callback_query(F.data == "game_coin")
async def cb_coin(callback: CallbackQuery):
    await callback.answer("🪙 Игра Coin Flip в разработке!", show_alert=True)

@dp.callback_query(F.data == "game_hilo")
async def cb_hilo(callback: CallbackQuery):
    await callback.answer("🃏 Игра Hi-Lo в разработке!", show_alert=True)

# ==================== ФИНАНСЫ ====================
@dp.callback_query(F.data == "finance")
async def cb_finance(callback: CallbackQuery):
    await callback.message.edit_text("💰 <b>Финансы</b>", reply_markup=finance_menu(), parse_mode=ParseMode.HTML)

@dp.callback_query(F.data == "donate")
async def cb_donate(callback: CallbackQuery):
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⭐ 1 Star - 2,500 POCX", callback_data="donate_1")],
        [InlineKeyboardButton(text="⭐⭐ 5 Stars - 12,500 POCX", callback_data="donate_5")],
        [InlineKeyboardButton(text="⭐⭐⭐ 10 Stars - 25,000 POCX", callback_data="donate_10")],
        [InlineKeyboardButton(text="💰 25 Stars - 62,500 POCX", callback_data="donate_25")],
        [InlineKeyboardButton(text="💎 50 Stars - 125,000 POCX", callback_data="donate_50")],
        [InlineKeyboardButton(text="✏️ Своя сумма", callback_data="donate_custom")],
        [InlineKeyboardButton(text="🔙 Назад", callback_data="finance")]
    ])
    await callback.message.edit_text(
        "💎 <b>Пополнение через Telegram Stars</b>\n\n⭐ 1 Star = 2500 POCX",
        reply_markup=kb,
        parse_mode=ParseMode.HTML
    )

@dp.callback_query(F.data.startswith("donate_"))
async def process_donate(callback: CallbackQuery, state: FSMContext):
    if callback.data == "donate_custom":
        await state.set_state("donate_custom")
        await callback.message.edit_text("💰 Введи количество Stars (1-1000):")
        return
    stars = int(callback.data.split("_")[1])
    pocx = stars * 2500
    await bot.send_invoice(
        chat_id=callback.from_user.id,
        title="Пополнение POCX",
        description=f"Получи {pocx:,} POCX за {stars} ⭐",
        payload=f"stars_{stars}_{pocx}",
        provider_token="",
        currency="XTR",
        prices=[LabeledPrice(label=f"{stars} Telegram Stars", amount=stars)],
        start_parameter="donate"
    )

@dp.message(StateFilter("donate_custom"))
async def donate_custom_amount(message: Message, state: FSMContext):
    if not message.text.isdigit():
        await message.answer("❌ Введи число!")
        return
    stars = int(message.text)
    if stars < 1 or stars > 1000:
        await message.answer("❌ От 1 до 1000 Stars!")
        return
    pocx = stars * 2500
    await bot.send_invoice(
        chat_id=message.from_user.id,
        title="Пополнение POCX",
        description=f"Получи {pocx:,} POCX за {stars} ⭐",
        payload=f"stars_{stars}_{pocx}",
        provider_token="",
        currency="XTR",
        prices=[LabeledPrice(label=f"{stars} Telegram Stars", amount=stars)],
        start_parameter="donate"
    )
    await state.clear()

@dp.pre_checkout_query()
async def pre_checkout(query: PreCheckoutQuery):
    await bot.answer_pre_checkout_query(query.id, ok=True)

@dp.message(F.successful_payment)
async def successful_payment(message: Message):
    payload = message.successful_payment.invoice_payload
    _, stars, pocx = payload.split("_")
    stars = int(stars)
    pocx = int(pocx)
    add_pocx(message.from_user.id, pocx)
    await message.answer(f"✅ Оплачено {stars} ⭐!\n💰 Получено {pocx:,} POCX", reply_markup=main_menu())

# ==================== АДМИН-ПАНЕЛЬ ====================
@dp.callback_query(F.data == "admin_panel")
async def cb_admin_panel(callback: CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        await callback.answer("❌ Нет доступа!", show_alert=True)
        return
    await callback.message.edit_text("👑 <b>Админ-панель</b>", reply_markup=admin_menu(), parse_mode=ParseMode.HTML)

@dp.callback_query(F.data == "admin_stats")
async def cb_admin_stats(callback: CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        await callback.answer("❌ Нет доступа!", show_alert=True)
        return
    cursor.execute("SELECT COUNT(*) FROM users")
    users = cursor.fetchone()[0]
    cursor.execute("SELECT SUM(balance) FROM users")
    total_balance = cursor.fetchone()[0] or 0
    cursor.execute("SELECT SUM(total_wagered) FROM users")
    total_wagered = cursor.fetchone()[0] or 0
    cursor.execute("SELECT SUM(total_won) FROM users")
    total_won = cursor.fetchone()[0] or 0
    cursor.execute("SELECT SUM(total_lost) FROM users")
    total_lost = cursor.fetchone()[0] or 0
    cursor.execute("SELECT COUNT(*) FROM banned_users")
    banned = cursor.fetchone()[0] or 0
    
    await callback.message.edit_text(
        f"📊 <b>Статистика казино</b>\n\n"
        f"👥 Пользователей: {users}\n"
        f"🚫 Забанено: {banned}\n"
        f"💰 Общий баланс: {total_balance:,} POCX\n"
        f"📊 Общий вейджер: {total_wagered:,} POCX\n"
        f"🏆 Выиграно всего: {total_won:,} POCX\n"
        f"💸 Проиграно всего: {total_lost:,} POCX\n"
        f"📈 Прибыль казино: {total_lost - total_won:,} POCX",
        reply_markup=admin_menu(),
        parse_mode=ParseMode.HTML
    )

@dp.callback_query(F.data == "admin_give")
async def cb_admin_give(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id not in ADMIN_IDS:
        await callback.answer("❌ Нет доступа!", show_alert=True)
        return
    await state.set_state("admin_give")
    await callback.message.edit_text("➕ <b>Выдача POCX</b>\n\nВведи ID и сумму через пробел\nПример: `123456789 50000`")

@dp.message(StateFilter("admin_give"))
async def admin_give_execute(message: Message, state: FSMContext):
    if message.from_user.id not in ADMIN_IDS:
        await message.answer("❌ Нет доступа!")
        await state.clear()
        return
    parts = message.text.split()
    if len(parts) != 2 or not parts[0].isdigit():
        await message.answer("❌ Пример: `123456789 50000`")
        return
    uid = int(parts[0])
    amount = int(parts[1])
    add_pocx(uid, amount)
    await message.answer(f"✅ Выдано {amount:,} POCX пользователю {uid}")
    await state.clear()

@dp.callback_query(F.data == "admin_take")
async def cb_admin_take(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id not in ADMIN_IDS:
        await callback.answer("❌ Нет доступа!", show_alert=True)
        return
    await state.set_state("admin_take")
    await callback.message.edit_text("➖ <b>Списание POCX</b>\n\nВведи ID и сумму через пробел\nПример: `123456789 50000`")

@dp.message(StateFilter("admin_take"))
async def admin_take_execute(message: Message, state: FSMContext):
    if message.from_user.id not in ADMIN_IDS:
        await message.answer("❌ Нет доступа!")
        await state.clear()
        return
    parts = message.text.split()
    if len(parts) != 2 or not parts[0].isdigit():
        await message.answer("❌ Пример: `123456789 50000`")
        return
    uid = int(parts[0])
    amount = int(parts[1])
    if not remove_pocx(uid, amount):
        await message.answer(f"❌ У пользователя {uid} недостаточно средств!")
        return
    await message.answer(f"✅ Списано {amount:,} POCX у пользователя {uid}")
    await state.clear()

@dp.callback_query(F.data == "admin_ban")
async def cb_admin_ban(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id not in ADMIN_IDS:
        await callback.answer("❌ Нет доступа!", show_alert=True)
        return
    await state.set_state("admin_ban")
    await callback.message.edit_text("🔨 <b>Бан пользователя</b>\n\nВведи ID пользователя\nПример: `123456789`")

@dp.message(StateFilter("admin_ban"))
async def admin_ban_execute(message: Message, state: FSMContext):
    if message.from_user.id not in ADMIN_IDS:
        await message.answer("❌ Нет доступа!")
        await state.clear()
        return
    if not message.text.isdigit():
        await message.answer("❌ Введи корректный ID!")
        return
    uid = int(message.text)
    ban_user(uid)
    await message.answer(f"✅ Пользователь {uid} забанен")
    await state.clear()

@dp.callback_query(F.data == "admin_unban")
async def cb_admin_unban(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id not in ADMIN_IDS:
        await callback.answer("❌ Нет доступа!", show_alert=True)
        return
    await state.set_state("admin_unban")
    await callback.message.edit_text("🔓 <b>Разбан пользователя</b>\n\nВведи ID пользователя\nПример: `123456789`")

@dp.message(StateFilter("admin_unban"))
async def admin_unban_execute(message: Message, state: FSMContext):
    if message.from_user.id not in ADMIN_IDS:
        await message.answer("❌ Нет доступа!")
        await state.clear()
        return
    if not message.text.isdigit():
        await message.answer("❌ Введи корректный ID!")
        return
    uid = int(message.text)
    unban_user(uid)
    await message.answer(f"✅ Пользователь {uid} разбанен")
    await state.clear()

@dp.callback_query(F.data == "admin_broadcast")
async def cb_admin_broadcast(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id not in ADMIN_IDS:
        await callback.answer("❌ Нет доступа!", show_alert=True)
        return
    await state.set_state("admin_broadcast")
    await callback.message.edit_text("📢 <b>Рассылка</b>\n\nВведи текст рассылки:")

@dp.message(StateFilter("admin_broadcast"))
async def admin_broadcast_execute(message: Message, state: FSMContext):
    if message.from_user.id not in ADMIN_IDS:
        await message.answer("❌ Нет доступа!")
        await state.clear()
        return
    text = message.text
    cursor.execute("SELECT user_id FROM users")
    users = cursor.fetchall()
    sent = 0
    for user in users:
        try:
            await bot.send_message(user[0], f"📢 <b>РАССЫЛКА</b>\n\n{text}")
            sent += 1
            await asyncio.sleep(0.05)
        except:
            pass
    await message.answer(f"✅ Рассылка завершена! Отправлено {sent} пользователям.")
    await state.clear()

# ==================== ЗАПУСК ====================
async def main():
    print("🤖 POCX Bot запущен!")
    print(f"👥 Админы: {ADMIN_IDS}")
    print(f"📢 Каналы для подписки: {[c['chat_id'] for c in REQUIRED_CHANNELS]}")
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
