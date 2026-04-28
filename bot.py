import asyncio
import html
import json
import random
import sqlite3
import string
import time
from datetime import datetime
from typing import Any, Dict, Optional

from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandStart, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    ChatMemberUpdated,
    ChatJoinRequest
)

# ==================== КОНФИГУРАЦИЯ ====================
TOKEN = "8776620773:AAGYaBqZ_Qn_SYEet_o3M-bc8rc6UFXvecA"
ADMIN_IDS = {8478884644, 8293927811}

# Каналы/чаты для обязательной подписки
REQUIRED_CHANNELS = [
    {"chat_id": "@POCXCHANEL", "link": "https://t.me/POCXCHANEL", "name": "POCX Канал"},
    {"chat_id": "@POCXCHAT", "link": "https://t.me/POCXCHAT", "name": "POCX Чат"},
]

CURRENCY_NAME = "POCX"
START_BALANCE = 5000.0
MIN_BET = 100.0

BONUS_COOLDOWN_SECONDS = 12 * 60 * 60  # 12 часов
BONUS_REWARD_MIN = 100
BONUS_REWARD_MAX = 2000

# Коэффициенты для игр
TOWER_MULTIPLIERS = [1.20, 1.48, 1.86, 2.35, 2.95, 3.75, 4.85, 6.15]
GOLD_MULTIPLIERS = [1.15, 1.35, 1.62, 2.0, 2.55, 3.25, 4.2]
DIAMOND_MULTIPLIERS = [1.12, 1.28, 1.48, 1.72, 2.02, 2.4, 2.92, 3.6]

RED_NUMBERS = {1, 3, 5, 7, 9, 12, 14, 16, 18, 19, 21, 23, 25, 27, 30, 32, 34, 36}

BANK_TERMS = {7: 0.03, 14: 0.07, 30: 0.18}

LEGACY_GOLD_MULTIPLIERS = [2, 4, 8, 16, 32, 64, 128, 256, 512, 1024, 2048, 4096]
FOOTBALL_MULTIPLIERS = {"gol": 1.6, "mimo": 2.2}

bot = Bot(token=TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher(storage=MemoryStorage())

# Глобальные хранилища игр
TOWER_GAMES: Dict[int, Dict[str, Any]] = {}
GOLD_GAMES: Dict[int, Dict[str, Any]] = {}
DIAMOND_GAMES: Dict[int, Dict[str, Any]] = {}
MINES_GAMES: Dict[int, Dict[str, Any]] = {}
OCHKO_GAMES: Dict[int, Dict[str, Any]] = {}
NGOLD_GAMES: Dict[str, Dict[str, Any]] = {}
NTOWER_GAMES: Dict[str, Dict[str, Any]] = {}
NMINES_GAMES: Dict[str, Dict[str, Any]] = {}
NDIAMOND_GAMES: Dict[str, Dict[str, Any]] = {}
NOCHKO_GAMES: Dict[str, Dict[str, Any]] = {}
NFOOTBALL_GAMES: Dict[int, Dict[str, Any]] = {}

user_game_locks: Dict[str, asyncio.Lock] = {}

# ==================== БАЗА ДАННЫХ ====================
DB_PATH = "pocx_bot.db"

def get_db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db()
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id TEXT PRIMARY KEY,
                coins REAL DEFAULT 0,
                total_wagered REAL DEFAULT 0,
                total_won REAL DEFAULT 0,
                total_lost REAL DEFAULT 0,
                referral_code TEXT UNIQUE,
                referrer_id TEXT,
                created_at INTEGER DEFAULT 0,
                last_daily_bonus INTEGER DEFAULT 0,
                daily_streak INTEGER DEFAULT 0,
                status INTEGER DEFAULT 0,
                banned INTEGER DEFAULT 0,
                checks TEXT DEFAULT '[]'
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS bets (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT,
                bet_amount REAL,
                choice TEXT,
                outcome TEXT,
                win INTEGER,
                payout REAL,
                ts INTEGER
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS referrals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                referrer_id TEXT,
                referred_id TEXT,
                earned_amount REAL DEFAULT 0,
                created_at INTEGER DEFAULT 0
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS checks (
                code TEXT PRIMARY KEY,
                creator_id TEXT,
                per_user REAL,
                remaining INTEGER,
                claimed TEXT,
                password TEXT
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS promos (
                name TEXT PRIMARY KEY,
                reward REAL,
                claimed TEXT,
                remaining_activations INTEGER
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS bank_deposits (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT,
                principal REAL,
                rate REAL,
                term_days INTEGER,
                opened_at INTEGER,
                status TEXT,
                closed_at INTEGER
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS json_data (
                key TEXT PRIMARY KEY,
                value TEXT
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS banned_users (
                user_id TEXT PRIMARY KEY,
                banned_at INTEGER,
                reason TEXT
            )
        """)
        conn.commit()
    finally:
        conn.close()

def now_ts() -> int:
    return int(time.time())

def fmt_money(value: float) -> str:
    value = round(float(value), 2)
    abs_value = abs(value)
    if abs_value >= 1000:
        compact = value / 1000
        text = f"{compact:.2f}".rstrip("0").rstrip(".")
        return f"{text}к {CURRENCY_NAME}"
    if abs(value - int(value)) < 1e-9:
        return f"{int(value)} {CURRENCY_NAME}"
    return f"{value:.2f} {CURRENCY_NAME}"

def fmt_dt(ts: int) -> str:
    return datetime.fromtimestamp(ts).strftime("%d.%m.%Y %H:%M")

def fmt_left(seconds: int) -> str:
    seconds = max(0, int(seconds))
    h = seconds // 3600
    m = (seconds % 3600) // 60
    s = seconds % 60
    if h > 0:
        return f"{h}ч {m}м"
    if m > 0:
        return f"{m}м {s}с"
    return f"{s}с"

def parse_amount(text: str) -> float:
    raw = str(text or "").strip().lower().replace(" ", "").replace(",", ".")
    multiplier = 1.0
    if raw.endswith(("к", "k")):
        raw = raw[:-1]
        multiplier = 1000.0
    value = float(raw) * multiplier
    if value <= 0:
        raise ValueError("amount must be positive")
    return round(value, 2)

def escape_html(text: Optional[str]) -> str:
    return html.escape(str(text or ""), quote=False)

def mention_user(user_id: int, name: Optional[str] = None) -> str:
    label = escape_html(name or f"Игрок {user_id}")
    return f'<a href="tg://user?id={int(user_id)}">{label}</a>'

def ensure_user_in_conn(conn: sqlite3.Connection, user_id: int) -> None:
    conn.execute(
        """
        INSERT OR IGNORE INTO users (id, coins, referral_code, created_at, status, checks)
        VALUES (?, ?, ?, ?, 0, '[]')
        """,
        (str(user_id), START_BALANCE, None, now_ts()),
    )

def ensure_user(user_id: int) -> None:
    conn = get_db()
    try:
        ensure_user_in_conn(conn, user_id)
        conn.commit()
    finally:
        conn.close()

def get_user(user_id: int) -> sqlite3.Row:
    conn = get_db()
    try:
        ensure_user_in_conn(conn, user_id)
        row = conn.execute("SELECT * FROM users WHERE id = ?", (str(user_id),)).fetchone()
        conn.commit()
        return row
    finally:
        conn.close()

def add_balance(user_id: int, delta: float) -> float:
    conn = get_db()
    try:
        conn.execute("BEGIN IMMEDIATE")
        ensure_user_in_conn(conn, user_id)
        conn.execute("UPDATE users SET coins = coins + ? WHERE id = ?", (round(delta, 2), str(user_id)))
        row = conn.execute("SELECT coins FROM users WHERE id = ?", (str(user_id),)).fetchone()
        conn.commit()
        return float(row["coins"] or 0)
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

def remove_balance(user_id: int, amount: float) -> bool:
    conn = get_db()
    try:
        conn.execute("BEGIN IMMEDIATE")
        ensure_user_in_conn(conn, user_id)
        row = conn.execute("SELECT coins FROM users WHERE id = ?", (str(user_id),)).fetchone()
        balance = float(row["coins"] or 0)
        if balance < amount:
            conn.rollback()
            return False
        conn.execute("UPDATE users SET coins = coins - ? WHERE id = ?", (round(amount, 2), str(user_id)))
        conn.commit()
        return True
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

def reserve_bet(user_id: int, bet: float) -> tuple[bool, float]:
    conn = get_db()
    try:
        conn.execute("BEGIN IMMEDIATE")
        ensure_user_in_conn(conn, user_id)
        row = conn.execute("SELECT coins FROM users WHERE id = ?", (str(user_id),)).fetchone()
        coins = float(row["coins"] or 0)
        if coins < bet:
            conn.rollback()
            return False, coins
        new_balance = round(coins - bet, 2)
        conn.execute("UPDATE users SET coins = ? WHERE id = ?", (new_balance, str(user_id)))
        conn.commit()
        return True, new_balance
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

def finalize_reserved_bet(user_id: int, bet: float, payout: float, choice: str, outcome: str) -> float:
    payout = round(max(0.0, payout), 2)
    conn = get_db()
    try:
        conn.execute("BEGIN IMMEDIATE")
        ensure_user_in_conn(conn, user_id)
        if payout > 0:
            conn.execute("UPDATE users SET coins = coins + ? WHERE id = ?", (payout, str(user_id)))
        conn.execute(
            "INSERT INTO bets (user_id, bet_amount, choice, outcome, win, payout, ts) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (str(user_id), round(bet, 2), choice, outcome, 1 if payout > 0 else 0, payout, now_ts()),
        )
        if payout > 0:
            conn.execute("UPDATE users SET total_won = total_won + ? WHERE id = ?", (payout, str(user_id)))
        else:
            conn.execute("UPDATE users SET total_lost = total_lost + ? WHERE id = ?", (bet, str(user_id)))
        conn.execute("UPDATE users SET total_wagered = total_wagered + ? WHERE id = ?", (bet, str(user_id)))
        row = conn.execute("SELECT coins FROM users WHERE id = ?", (str(user_id),)).fetchone()
        conn.commit()
        return float(row["coins"] or 0)
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

def settle_instant_bet(user_id: int, bet: float, payout: float, choice: str, outcome: str) -> tuple[bool, float]:
    payout = round(max(0.0, payout), 2)
    conn = get_db()
    try:
        conn.execute("BEGIN IMMEDIATE")
        ensure_user_in_conn(conn, user_id)
        row = conn.execute("SELECT coins FROM users WHERE id = ?", (str(user_id),)).fetchone()
        coins = float(row["coins"] or 0)
        if coins < bet:
            conn.rollback()
            return False, coins
        new_balance = round(coins - bet + payout, 2)
        conn.execute("UPDATE users SET coins = ? WHERE id = ?", (new_balance, str(user_id)))
        conn.execute(
            "INSERT INTO bets (user_id, bet_amount, choice, outcome, win, payout, ts) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (str(user_id), round(bet, 2), choice, outcome, 1 if payout > 0 else 0, payout, now_ts()),
        )
        if payout > 0:
            conn.execute("UPDATE users SET total_won = total_won + ? WHERE id = ?", (payout, str(user_id)))
        else:
            conn.execute("UPDATE users SET total_lost = total_lost + ? WHERE id = ?", (bet, str(user_id)))
        conn.execute("UPDATE users SET total_wagered = total_wagered + ? WHERE id = ?", (bet, str(user_id)))
        conn.commit()
        return True, new_balance
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

def is_user_banned(user_id: int) -> bool:
    conn = get_db()
    try:
        row = conn.execute("SELECT 1 FROM banned_users WHERE user_id = ?", (str(user_id),)).fetchone()
        return row is not None
    finally:
        conn.close()

def ban_user(user_id: int, reason: str = "") -> bool:
    conn = get_db()
    try:
        conn.execute("INSERT OR IGNORE INTO banned_users (user_id, banned_at, reason) VALUES (?, ?, ?)",
                     (str(user_id), now_ts(), reason))
        conn.execute("UPDATE users SET banned = 1 WHERE id = ?", (str(user_id),))
        conn.commit()
        return True
    except Exception:
        conn.rollback()
        return False
    finally:
        conn.close()

def unban_user(user_id: int) -> bool:
    conn = get_db()
    try:
        conn.execute("DELETE FROM banned_users WHERE user_id = ?", (str(user_id),))
        conn.execute("UPDATE users SET banned = 0 WHERE id = ?", (str(user_id),))
        conn.commit()
        return True
    except Exception:
        conn.rollback()
        return False
    finally:
        conn.close()

def set_referral_code(user_id: int) -> str:
    code = hashlib.md5(f"{user_id}{time.time()}".encode()).hexdigest()[:8]
    conn = get_db()
    try:
        conn.execute("UPDATE users SET referral_code = ? WHERE id = ?", (code, str(user_id)))
        conn.commit()
        return code
    finally:
        conn.close()

def get_referral_code(user_id: int) -> str:
    conn = get_db()
    try:
        row = conn.execute("SELECT referral_code FROM users WHERE id = ?", (str(user_id),)).fetchone()
        if row and row["referral_code"]:
            return row["referral_code"]
        code = set_referral_code(user_id)
        return code
    finally:
        conn.close()

def add_referral(referrer_id: int, referred_id: int) -> bool:
    conn = get_db()
    try:
        conn.execute("INSERT OR IGNORE INTO referrals (referrer_id, referred_id, created_at) VALUES (?, ?, ?)",
                     (str(referrer_id), str(referred_id), now_ts()))
        conn.commit()
        return True
    except Exception:
        return False
    finally:
        conn.close()

def get_referral_stats(user_id: int) -> dict:
    conn = get_db()
    try:
        count = conn.execute("SELECT COUNT(*) FROM referrals WHERE referrer_id = ?", (str(user_id),)).fetchone()[0]
        earned = conn.execute("SELECT COALESCE(SUM(earned_amount), 0) FROM referrals WHERE referrer_id = ?", (str(user_id),)).fetchone()[0]
        return {"count": count, "earned": earned}
    finally:
        conn.close()

# ==================== ПРОВЕРКА ПОДПИСКИ ====================
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

@dp.callback_query(F.data == "check_subscription")
async def check_subscription_callback(callback: CallbackQuery):
    ok, not_subscribed = await check_subscription(callback.from_user.id)
    if ok:
        await callback.message.edit_text("✅ Спасибо за подписку! Теперь ты можешь пользоваться ботом.")
        await callback.answer()
    else:
        await callback.message.edit_text(
            "❌ Для использования бота необходимо подписаться на следующие каналы:",
            reply_markup=subscription_keyboard(not_subscribed)
        )
        await callback.answer()

# ==================== MIDDLEWARE ДЛЯ ПРОВЕРКИ ПОДПИСКИ ====================
@dp.message()
async def subscription_middleware(message: Message):
    if is_user_banned(message.from_user.id):
        await message.answer("❌ Ты забанен и не можешь использовать бота.")
        return
    ok, not_subscribed = await check_subscription(message.from_user.id)
    if not ok:
        await message.answer(
            "❌ Для использования бота необходимо подписаться на следующие каналы:",
            reply_markup=subscription_keyboard(not_subscribed)
        )
        return

@dp.callback_query()
async def subscription_callback_middleware(callback: CallbackQuery):
    if callback.data == "check_subscription":
        return
    if is_user_banned(callback.from_user.id):
        await callback.answer("❌ Ты забанен!", show_alert=True)
        return
    ok, not_subscribed = await check_subscription(callback.from_user.id)
    if not ok:
        await callback.answer("❌ Подпишись на каналы!", show_alert=True)
        return

# ==================== ГЛАВНОЕ МЕНЮ ====================
def main_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🎮 Игры", callback_data="games_menu")],
        [InlineKeyboardButton(text="💰 Баланс", callback_data="balance")],
        [InlineKeyboardButton(text="🎁 Бонус", callback_data="bonus")],
        [InlineKeyboardButton(text="👥 Рефералы", callback_data="referrals")],
        [InlineKeyboardButton(text="🏆 Топ", callback_data="top")],
        [InlineKeyboardButton(text="🏦 Банк", callback_data="bank_menu")],
        [InlineKeyboardButton(text="🧾 Чеки", callback_data="checks_menu")],
        [InlineKeyboardButton(text="🔄 Отмена игры", callback_data="cancel_game")],
    ])

def games_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🗼 Башня", callback_data="game:tower"),
         InlineKeyboardButton(text="🥇 Золото", callback_data="game:gold")],
        [InlineKeyboardButton(text="💎 Алмазы", callback_data="game:diamonds"),
         InlineKeyboardButton(text="💣 Мины", callback_data="game:mines")],
        [InlineKeyboardButton(text="🎴 Очко", callback_data="game:ochko"),
         InlineKeyboardButton(text="🎡 Рулетка", callback_data="game:roulette")],
        [InlineKeyboardButton(text="📈 Краш", callback_data="game:crash"),
         InlineKeyboardButton(text="🎲 Кубик", callback_data="game:cube")],
        [InlineKeyboardButton(text="🎯 Кости", callback_data="game:dice"),
         InlineKeyboardButton(text="⚽ Футбол", callback_data="game:football")],
        [InlineKeyboardButton(text="🏀 Баскет", callback_data="game:basket")],
        [InlineKeyboardButton(text="🔙 Назад", callback_data="main_menu")],
    ])

def cancel_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_current")]
    ])

@dp.callback_query(F.data == "main_menu")
async def main_menu_callback(callback: CallbackQuery):
    await callback.message.edit_text("🎮 <b>Главное меню</b>", reply_markup=main_menu())
    await callback.answer()

@dp.callback_query(F.data == "games_menu")
async def games_menu_callback(callback: CallbackQuery):
    await callback.message.edit_text("🎮 <b>Выбери игру</b>", reply_markup=games_menu())
    await callback.answer()

@dp.callback_query(F.data == "cancel_game")
async def cancel_current_game(callback: CallbackQuery):
    user_id = callback.from_user.id
    cleared = False
    
    if user_id in TOWER_GAMES:
        game = TOWER_GAMES.pop(user_id)
        bet = game["bet"]
        add_balance(user_id, bet)
        await callback.answer(f"Игра Башня отменена. Возвращено {fmt_money(bet)}", show_alert=True)
        cleared = True
    elif user_id in GOLD_GAMES:
        game = GOLD_GAMES.pop(user_id)
        bet = game["bet"]
        add_balance(user_id, bet)
        await callback.answer(f"Игра Золото отменена. Возвращено {fmt_money(bet)}", show_alert=True)
        cleared = True
    elif user_id in DIAMOND_GAMES:
        game = DIAMOND_GAMES.pop(user_id)
        bet = game["bet"]
        add_balance(user_id, bet)
        await callback.answer(f"Игра Алмазы отменена. Возвращено {fmt_money(bet)}", show_alert=True)
        cleared = True
    elif user_id in MINES_GAMES:
        game = MINES_GAMES.pop(user_id)
        bet = game["bet"]
        add_balance(user_id, bet)
        await callback.answer(f"Игра Мины отменена. Возвращено {fmt_money(bet)}", show_alert=True)
        cleared = True
    elif user_id in OCHKO_GAMES:
        game = OCHKO_GAMES.pop(user_id)
        bet = game["bet"]
        add_balance(user_id, bet)
        await callback.answer(f"Игра Очко отменена. Возвращено {fmt_money(bet)}", show_alert=True)
        cleared = True
    else:
        await callback.answer("Нет активной игры для отмены", show_alert=True)
    
    if cleared:
        await callback.message.answer("✅ Игра отменена. Можешь начать новую.", reply_markup=main_menu())

@dp.callback_query(F.data == "cancel_current")
async def cancel_current(callback: CallbackQuery):
    await cancel_current_game(callback)

@dp.callback_query(F.data == "balance")
async def show_balance(callback: CallbackQuery):
    user = get_user(callback.from_user.id)
    await callback.answer(f"💰 Баланс: {fmt_money(user['coins'])}", show_alert=True)

# ==================== БОНУС ====================
@dp.callback_query(F.data == "bonus")
async def bonus_command(callback: CallbackQuery):
    user_id = callback.from_user.id
    user = get_user(user_id)
    last = user["last_daily_bonus"] or 0
    streak = user["daily_streak"] or 0
    now = now_ts()
    
    if now - last < BONUS_COOLDOWN_SECONDS:
        left = BONUS_COOLDOWN_SECONDS - (now - last)
        await callback.answer(f"Бонус будет доступен через {fmt_left(left)}", show_alert=True)
        return
    
    base_bonus = random.randint(BONUS_REWARD_MIN, BONUS_REWARD_MAX)
    bonus = int(base_bonus * (1 + min(streak * 0.05, 0.5)))
    add_balance(user_id, bonus)
    
    conn = get_db()
    conn.execute("UPDATE users SET last_daily_bonus = ?, daily_streak = daily_streak + 1 WHERE id = ?",
                 (now, str(user_id)))
    conn.commit()
    conn.close()
    
    await callback.message.edit_text(
        f"🎁 <b>Ежедневный бонус!</b>\n\n"
        f"💰 +{fmt_money(bonus)}\n"
        f"🔥 Серия дней: {streak + 1}\n"
        f"📈 Бонус увеличен на {min(streak * 5, 50)}%!",
        reply_markup=main_menu()
    )
    await callback.answer()

# ==================== РЕФЕРАЛЫ ====================
@dp.callback_query(F.data == "referrals")
async def referrals_menu(callback: CallbackQuery):
    user_id = callback.from_user.id
    code = get_referral_code(user_id)
    stats = get_referral_stats(user_id)
    link = f"https://t.me/{(await bot.get_me()).username}?start=ref{code}"
    
    await callback.message.edit_text(
        f"👥 <b>Реферальная система</b>\n\n"
        f"🔗 Твоя ссылка:\n<code>{link}</code>\n\n"
        f"👤 Приглашено: {stats['count']}\n"
        f"💰 Заработано: {fmt_money(stats['earned'])}\n\n"
        f"🎁 За каждого друга: +{fmt_money(500)}",
        reply_markup=main_menu()
    )

# ==================== ТОП ====================
@dp.callback_query(F.data == "top")
async def top_command(callback: CallbackQuery):
    conn = get_db()
    rows = conn.execute("SELECT id, coins FROM users ORDER BY coins DESC LIMIT 10").fetchall()
    conn.close()
    
    medals = ["🥇", "🥈", "🥉"]
    lines = ["🏆 <b>Топ игроков</b>\n"]
    for i, row in enumerate(rows):
        medal = medals[i] if i < 3 else f"{i+1}."
        name = mention_user(int(row["id"]))
        lines.append(f"{medal} {name} — <b>{fmt_money(row['coins'])}</b>")
    
    await callback.message.edit_text("\n".join(lines), reply_markup=main_menu())
    await callback.answer()

# ==================== ИГРЫ ====================
# Краш
@dp.callback_query(F.data == "game:crash")
async def crash_start(callback: CallbackQuery, state: FSMContext):
    await state.set_state("crash_waiting_bet")
    await callback.message.edit_text(
        "📈 <b>Краш</b>\n\nВведи ставку и множитель через пробел\nПример: <code>1000 2.5</code>",
        reply_markup=cancel_kb()
    )
    await callback.answer()

@dp.message(StateFilter("crash_waiting_bet"))
async def crash_bet(message: Message, state: FSMContext):
    parts = message.text.strip().split()
    if len(parts) != 2:
        await message.answer("❌ Формат: <code>ставка множитель</code>\nПример: <code>1000 2.5</code>")
        return
    
    try:
        bet = parse_amount(parts[0])
        target = float(parts[1].replace(",", "."))
    except Exception:
        await message.answer("❌ Неверный формат!")
        return
    
    if bet < MIN_BET:
        await message.answer(f"❌ Минимальная ставка: {fmt_money(MIN_BET)}")
        return
    if target < 1.01 or target > 10:
        await message.answer("❌ Множитель должен быть от 1.01 до 10")
        return
    
    user_id = message.from_user.id
    user = get_user(user_id)
    if user["coins"] < bet:
        await message.answer("❌ Недостаточно средств!")
        return
    
    # Генерация краша
    r = random.random()
    if r < 0.06:
        crash_multiplier = 1.00
    elif r < 0.55:
        crash_multiplier = round(random.uniform(1.01, 1.80), 2)
    elif r < 0.80:
        crash_multiplier = round(random.uniform(1.81, 2.80), 2)
    elif r < 0.93:
        crash_multiplier = round(random.uniform(2.81, 4.50), 2)
    elif r < 0.985:
        crash_multiplier = round(random.uniform(4.51, 9.50), 2)
    else:
        crash_multiplier = round(random.uniform(9.51, 10.0), 2)
    
    win = crash_multiplier >= target
    payout = bet * target if win else 0
    
    ok, balance = settle_instant_bet(user_id, bet, payout, f"crash:{target}", f"result={crash_multiplier}")
    if not ok:
        await message.answer("❌ Недостаточно средств!")
        return
    
    await message.answer(
        f"📈 <b>Краш</b>\n\n"
        f"💥 Множитель краша: <b>{crash_multiplier}x</b>\n"
        f"🎯 Твоя цель: <b>{target}x</b>\n"
        f"Результат: <b>{'✅ Победа!' if win else '❌ Поражение'}</b>\n"
        f"💰 Выплата: <b>{fmt_money(payout)}</b>\n"
        f"💰 Баланс: <b>{fmt_money(balance)}</b>",
        reply_markup=main_menu()
    )
    await state.clear()

# Кубик
@dp.callback_query(F.data == "game:cube")
async def cube_start(callback: CallbackQuery, state: FSMContext):
    await state.set_state("cube_waiting_bet")
    await callback.message.edit_text(
        "🎲 <b>Кубик</b>\n\nВведи ставку и число (1-6) или 'чет'/'нечет'\nПример: <code>1000 5</code> или <code>1000 чет</code>",
        reply_markup=cancel_kb()
    )
    await callback.answer()

@dp.message(StateFilter("cube_waiting_bet"))
async def cube_bet(message: Message, state: FSMContext):
    parts = message.text.strip().lower().split()
    if len(parts) != 2:
        await message.answer("❌ Формат: <code>ставка число|чет|нечет</code>\nПример: <code>1000 5</code>")
        return
    
    try:
        bet = parse_amount(parts[0])
    except Exception:
        await message.answer("❌ Неверная ставка!")
        return
    
    if bet < MIN_BET:
        await message.answer(f"❌ Минимальная ставка: {fmt_money(MIN_BET)}")
        return
    
    bet_type = parts[1]
    valid = {"1","2","3","4","5","6","чет","нечет","б","м"}
    if bet_type not in valid:
        await message.answer("❌ Неверный тип ставки! Используй: 1-6, чет, нечет")
        return
    
    user_id = message.from_user.id
    user = get_user(user_id)
    if user["coins"] < bet:
        await message.answer("❌ Недостаточно средств!")
        return
    
    dice_msg = await message.answer_dice(emoji="🎲")
    number = int(dice_msg.dice.value)
    
    win = False
    mult = 0.0
    if bet_type == str(number):
        win, mult = True, 3.5
    elif bet_type == "чет" and number % 2 == 0:
        win, mult = True, 1.9
    elif bet_type == "нечет" and number % 2 == 1:
        win, mult = True, 1.9
    elif bet_type == "б" and number >= 4:
        win, mult = True, 1.9
    elif bet_type == "м" and number <= 3:
        win, mult = True, 1.9
    
    payout = bet * mult if win else 0
    ok, balance = settle_instant_bet(user_id, bet, payout, f"cube:{bet_type}", f"num={number}")
    if not ok:
        await message.answer("❌ Недостаточно средств!")
        return
    
    parity = "чет" if number % 2 == 0 else "нечет"
    more_less = "больше" if number >= 4 else "меньше"
    
    await message.answer(
        f"🎲 <b>Кубик</b>\n\n"
        f"Выпало: <b>{number}</b> ({more_less}, {parity})\n"
        f"Твой выбор: <b>{bet_type}</b>\n"
        f"Результат: <b>{'✅ Победа!' if win else '❌ Поражение'}</b>\n"
        f"💰 Выплата: <b>{fmt_money(payout if win else 0)}</b>\n"
        f"💰 Баланс: <b>{fmt_money(balance)}</b>",
        reply_markup=main_menu()
    )
    await state.clear()

# Кости
@dp.callback_query(F.data == "game:dice")
async def dice_start(callback: CallbackQuery, state: FSMContext):
    await state.set_state("dice_waiting_bet")
    await callback.message.edit_text(
        "🎯 <b>Кости</b>\n\nВведи ставку и выбор (м - меньше 7, б - больше 7, равно)\nПример: <code>1000 м</code>",
        reply_markup=cancel_kb()
    )
    await callback.answer()

@dp.message(StateFilter("dice_waiting_bet"))
async def dice_bet(message: Message, state: FSMContext):
    parts = message.text.strip().lower().split()
    if len(parts) != 2:
        await message.answer("❌ Формат: <code>ставка м|б|равно</code>\nПример: <code>1000 м</code>")
        return
    
    try:
        bet = parse_amount(parts[0])
    except Exception:
        await message.answer("❌ Неверная ставка!")
        return
    
    if bet < MIN_BET:
        await message.answer(f"❌ Минимальная ставка: {fmt_money(MIN_BET)}")
        return
    
    choice = parts[1]
    if choice not in {"м", "б", "равно"}:
        await message.answer("❌ Выбери: м (меньше), б (больше), равно")
        return
    
    user_id = message.from_user.id
    user = get_user(user_id)
    if user["coins"] < bet:
        await message.answer("❌ Недостаточно средств!")
        return
    
    d1_msg = await message.answer_dice(emoji="🎲")
    d2_msg = await message.answer_dice(emoji="🎲")
    d1 = int(d1_msg.dice.value)
    d2 = int(d2_msg.dice.value)
    total = d1 + d2
    
    win = False
    mult = 0.0
    if choice == "м" and total < 7:
        win, mult = True, 2.25
    elif choice == "б" and total > 7:
        win, mult = True, 2.25
    elif choice == "равно" and total == 7:
        win, mult = True, 5.0
    
    payout = bet * mult if win else 0
    relation = "меньше 7" if total < 7 else ("больше 7" if total > 7 else "равно 7")
    
    ok, balance = settle_instant_bet(user_id, bet, payout, f"dice:{choice}", f"{d1}+{d2}={total}")
    if not ok:
        await message.answer("❌ Недостаточно средств!")
        return
    
    await message.answer(
        f"🎯 <b>Кости</b>\n\n"
        f"Выпало: <b>{d1}</b> + <b>{d2}</b> = <b>{total}</b> ({relation})\n"
        f"Твой выбор: <b>{choice}</b>\n"
        f"Результат: <b>{'✅ Победа!' if win else '❌ Поражение'}</b>\n"
        f"💰 Выплата: <b>{fmt_money(payout if win else 0)}</b>\n"
        f"💰 Баланс: <b>{fmt_money(balance)}</b>",
        reply_markup=main_menu()
    )
    await state.clear()

# Рулетка
@dp.callback_query(F.data == "game:roulette")
async def roulette_start(callback: CallbackQuery, state: FSMContext):
    await state.set_state("roulette_waiting_bet")
    await callback.message.edit_text(
        "🎡 <b>Рулетка</b>\n\nВведи ставку и выбор (красное/черное/чет/нечет/зеро)\nПример: <code>1000 красное</code>",
        reply_markup=cancel_kb()
    )
    await callback.answer()

@dp.message(StateFilter("roulette_waiting_bet"))
async def roulette_bet(message: Message, state: FSMContext):
    parts = message.text.strip().lower().split()
    if len(parts) != 2:
        await message.answer("❌ Формат: <code>ставка выбор</code>\nВыбор: красное, черное, чет, нечет, зеро")
        return
    
    try:
        bet = parse_amount(parts[0])
    except Exception:
        await message.answer("❌ Неверная ставка!")
        return
    
    if bet < MIN_BET:
        await message.answer(f"❌ Минимальная ставка: {fmt_money(MIN_BET)}")
        return
    
    choice_map = {
        "красное": "red", "красный": "red", "red": "red",
        "черное": "black", "черный": "black", "black": "black",
        "чет": "even", "четное": "even", "even": "even",
        "нечет": "odd", "нечетное": "odd", "odd": "odd",
        "зеро": "zero", "zero": "zero", "0": "zero"
    }
    
    choice = choice_map.get(parts[1])
    if not choice:
        await message.answer("❌ Неверный выбор! Используй: красное, черное, чет, нечет, зеро")
        return
    
    user_id = message.from_user.id
    user = get_user(user_id)
    if user["coins"] < bet:
        await message.answer("❌ Недостаточно средств!")
        return
    
    number = random.randint(0, 35)
    if number == 0:
        color = "green"
        parity = ""
    elif number % 2 == 0:
        color = "black"
        parity = "even"
    else:
        color = "red"
        parity = "odd"
    
    multiplier = 2.0
    win = False
    if choice == "zero":
        win = number == 0
        if win:
            multiplier = 35.0
    elif choice == "black":
        win = number != 0 and number % 2 == 0
    elif choice == "red":
        win = number != 0 and number % 2 == 1
    elif choice == "even":
        win = number != 0 and number % 2 == 0
    elif choice == "odd":
        win = number != 0 and number % 2 == 1
    
    payout = bet * multiplier if win else 0
    color_text = "зеленый" if number == 0 else ("черный" if color == "black" else "красный")
    
    ok, balance = settle_instant_bet(user_id, bet, payout, f"roulette:{choice}", f"num={number}")
    if not ok:
        await message.answer("❌ Недостаточно средств!")
        return
    
    await message.answer(
        f"🎡 <b>Рулетка</b>\n\n"
        f"Выпало: <b>{number}</b> ({color_text})\n"
        f"Твой выбор: <b>{parts[1]}</b>\n"
        f"Результат: <b>{'✅ Победа!' if win else '❌ Поражение'}</b>\n"
        f"💰 Выплата: <b>{fmt_money(payout)}</b>\n"
        f"💰 Баланс: <b>{fmt_money(balance)}</b>",
        reply_markup=main_menu()
    )
    await state.clear()

# Футбол
@dp.callback_query(F.data == "game:football")
async def football_start(callback: CallbackQuery, state: FSMContext):
    await state.set_state("football_waiting_bet")
    await callback.message.edit_text(
        "⚽ <b>Футбол</b>\n\nВведи ставку и выбор (гол/мимо)\nПример: <code>1000 гол</code>",
        reply_markup=cancel_kb()
    )
    await callback.answer()

@dp.message(StateFilter("football_waiting_bet"))
async def football_bet(message: Message, state: FSMContext):
    parts = message.text.strip().lower().split()
    if len(parts) != 2:
        await message.answer("❌ Формат: <code>ставка гол|мимо</code>\nПример: <code>1000 гол</code>")
        return
    
    try:
        bet = parse_amount(parts[0])
    except Exception:
        await message.answer("❌ Неверная ставка!")
        return
    
    if bet < MIN_BET:
        await message.answer(f"❌ Минимальная ставка: {fmt_money(MIN_BET)}")
        return
    
    choice = "gol" if parts[1] in {"гол", "goal"} else "mimo"
    if choice not in {"gol", "mimo"}:
        await message.answer("❌ Выбери: гол или мимо")
        return
    
    user_id = message.from_user.id
    user = get_user(user_id)
    if user["coins"] < bet:
        await message.answer("❌ Недостаточно средств!")
        return
    
    dice_msg = await message.answer_dice(emoji="⚽")
    value = int(dice_msg.dice.value)
    outcome = "gol" if value >= 3 else "mimo"
    
    win = outcome == choice
    payout = bet * FOOTBALL_MULTIPLIERS[choice] if win else 0
    
    ok, balance = settle_instant_bet(user_id, bet, payout, f"football:{choice}", f"value={value}")
    if not ok:
        await message.answer("❌ Недостаточно средств!")
        return
    
    await message.answer(
        f"⚽ <b>Футбол</b>\n\n"
        f"Итог: <b>{'Гол!' if outcome == 'gol' else 'Мимо!'}</b>\n"
        f"Твой выбор: <b>{'Гол' if choice == 'gol' else 'Мимо'}</b>\n"
        f"Результат: <b>{'✅ Победа!' if win else '❌ Поражение'}</b>\n"
        f"💰 Выплата: <b>{fmt_money(payout)}</b>\n"
        f"💰 Баланс: <b>{fmt_money(balance)}</b>",
        reply_markup=main_menu()
    )
    await state.clear()

# Баскет
@dp.callback_query(F.data == "game:basket")
async def basket_start(callback: CallbackQuery, state: FSMContext):
    await state.set_state("basket_waiting_bet")
    await callback.message.edit_text(
        "🏀 <b>Баскет</b>\n\nВведи ставку\nПример: <code>1000</code>",
        reply_markup=cancel_kb()
    )
    await callback.answer()

@dp.message(StateFilter("basket_waiting_bet"))
async def basket_bet(message: Message, state: FSMContext):
    try:
        bet = parse_amount(message.text.strip())
    except Exception:
        await message.answer("❌ Неверная ставка!")
        return
    
    if bet < MIN_BET:
        await message.answer(f"❌ Минимальная ставка: {fmt_money(MIN_BET)}")
        return
    
    user_id = message.from_user.id
    user = get_user(user_id)
    if user["coins"] < bet:
        await message.answer("❌ Недостаточно средств!")
        return
    
    dice_msg = await message.answer_dice(emoji="🏀")
    value = int(dice_msg.dice.value)
    win = value in {4, 5}
    payout = bet * 2.2 if win else 0
    
    result_text = "Точный бросок!" if win else "Промах!"
    
    ok, balance = settle_instant_bet(user_id, bet, payout, "basketball", f"value={value}")
    if not ok:
        await message.answer("❌ Недостаточно средств!")
        return
    
    await message.answer(
        f"🏀 <b>Баскет</b>\n\n"
        f"Результат: <b>{result_text}</b>\n"
        f"Результат: <b>{'✅ Победа!' if win else '❌ Поражение'}</b>\n"
        f"💰 Выплата: <b>{fmt_money(payout)}</b>\n"
        f"💰 Баланс: <b>{fmt_money(balance)}</b>",
        reply_markup=main_menu()
    )
    await state.clear()

# ==================== БАШНЯ (МИНИ-ИГРА С ИНЛАЙН КНОПКАМИ) ====================
def tower_kb(level: int, can_cashout: bool = False) -> InlineKeyboardMarkup:
    kb = InlineKeyboardMarkup(inline_row_width=3)
    for i in range(1, 4):
        kb.insert(InlineKeyboardButton(text=str(i), callback_data=f"tower_pick_{i}"))
    if can_cashout:
        kb.add(InlineKeyboardButton(text="💰 Забрать выигрыш", callback_data="tower_cashout"))
    kb.add(InlineKeyboardButton(text="❌ Сдаться", callback_data="tower_cancel"))
    return kb

@dp.callback_query(F.data == "game:tower")
async def tower_start(callback: CallbackQuery, state: FSMContext):
    await state.set_state("tower_waiting_bet")
    await callback.message.edit_text(
        "🗼 <b>Башня</b>\n\nВведи ставку (минимальная 100 POCX)\nПример: <code>1000</code>",
        reply_markup=cancel_kb()
    )
    await callback.answer()

@dp.message(StateFilter("tower_waiting_bet"))
async def tower_bet_amount(message: Message, state: FSMContext):
    try:
        bet = parse_amount(message.text.strip())
    except Exception:
        await message.answer("❌ Неверная ставка!")
        return
    
    if bet < MIN_BET:
        await message.answer(f"❌ Минимальная ставка: {fmt_money(MIN_BET)}")
        return
    
    user_id = message.from_user.id
    user = get_user(user_id)
    if user["coins"] < bet:
        await message.answer("❌ Недостаточно средств!")
        return
    
    ok, _ = reserve_bet(user_id, bet)
    if not ok:
        await message.answer("❌ Недостаточно средств!")
        return
    
    TOWER_GAMES[user_id] = {"bet": bet, "level": 0, "multiplier": 1.0}
    
    await message.answer(
        f"🗼 <b>Башня</b>\n\n"
        f"💰 Ставка: {fmt_money(bet)}\n"
        f"🎯 Уровень: 1/8\n"
        f"📈 Текущий множитель: 1.00x\n"
        f"💎 Потенциальный выигрыш: {fmt_money(bet)}\n\n"
        f"Выбери безопасную клетку (1-3):",
        reply_markup=tower_kb(1, False)
    )
    await state.clear()

@dp.callback_query(F.data.startswith("tower_pick_"))
async def tower_pick(callback: CallbackQuery):
    user_id = callback.from_user.id
    game = TOWER_GAMES.get(user_id)
    if not game:
        await callback.answer("Нет активной игры!", show_alert=True)
        return
    
    chosen = int(callback.data.split("_")[2])
    bomb = random.randint(1, 3)
    
    if chosen == bomb:
        # Проигрыш
        bet = game["bet"]
        finalize_reserved_bet(user_id, bet, 0, "tower", "lose")
        TOWER_GAMES.pop(user_id)
        await callback.message.edit_text(
            f"💥 <b>БАХ! Ты попал на мину!</b>\n\n"
            f"💰 Ставка: {fmt_money(bet)}\n"
            f"❌ Проигрыш!",
            reply_markup=main_menu()
        )
        await callback.answer()
        return
    
    # Победа в уровне
    level = game["level"] + 1
    multiplier = TOWER_MULTIPLIERS[level - 1] if level <= len(TOWER_MULTIPLIERS) else TOWER_MULTIPLIERS[-1]
    game["level"] = level
    game["multiplier"] = multiplier
    potential_win = game["bet"] * multiplier
    
    if level >= len(TOWER_MULTIPLIERS):
        # Полная победа
        win_amount = potential_win
        balance = finalize_reserved_bet(user_id, game["bet"], win_amount, "tower", "win_full")
        TOWER_GAMES.pop(user_id)
        await callback.message.edit_text(
            f"🎉 <b>ПОБЕДА! Ты прошёл всю башню!</b>\n\n"
            f"💰 Ставка: {fmt_money(game['bet'])}\n"
            f"📈 Множитель: {multiplier}x\n"
            f"💰 Выигрыш: {fmt_money(win_amount)}\n"
            f"💰 Баланс: {fmt_money(balance)}",
            reply_markup=main_menu()
        )
        await callback.answer()
        return
    
    await callback.message.edit_text(
        f"🗼 <b>Башня</b>\n\n"
        f"✅ Уровень {level} пройден!\n"
        f"💰 Ставка: {fmt_money(game['bet'])}\n"
        f"🎯 Уровень: {level + 1}/8\n"
        f"📈 Текущий множитель: {multiplier}x\n"
        f"💎 Потенциальный выигрыш: {fmt_money(potential_win)}\n\n"
        f"Выбери безопасную клетку (1-3):",
        reply_markup=tower_kb(level + 1, True)
    )
    await callback.answer()

@dp.callback_query(F.data == "tower_cashout")
async def tower_cashout(callback: CallbackQuery):
    user_id = callback.from_user.id
    game = TOWER_GAMES.get(user_id)
    if not game:
        await callback.answer("Нет активной игры!", show_alert=True)
        return
    
    win_amount = game["bet"] * game["multiplier"]
    balance = finalize_reserved_bet(user_id, game["bet"], win_amount, "tower", f"cashout_lvl={game['level']}")
    TOWER_GAMES.pop(user_id)
    
    await callback.message.edit_text(
        f"💰 <b>Выигрыш забран!</b>\n\n"
        f"💰 Ставка: {fmt_money(game['bet'])}\n"
        f"📈 Множитель: {game['multiplier']}x\n"
        f"💰 Выигрыш: {fmt_money(win_amount)}\n"
        f"💰 Баланс: {fmt_money(balance)}",
        reply_markup=main_menu()
    )
    await callback.answer()

@dp.callback_query(F.data == "tower_cancel")
async def tower_cancel(callback: CallbackQuery):
    user_id = callback.from_user.id
    game = TOWER_GAMES.get(user_id)
    if not game:
        await callback.answer("Нет активной игры!", show_alert=True)
        return
    
    refund = game["bet"] if game["level"] == 0 else 0
    balance = finalize_reserved_bet(user_id, game["bet"], refund, "tower", "cancel")
    TOWER_GAMES.pop(user_id)
    
    await callback.message.edit_text(
        f"❌ <b>Игра отменена</b>\n\n"
        f"💰 Возвращено: {fmt_money(refund)}\n"
        f"💰 Баланс: {fmt_money(balance)}",
        reply_markup=main_menu()
    )
    await callback.answer()

# ==================== АДМИН-ПАНЕЛЬ ====================
def admin_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📊 Статистика", callback_data="admin_stats")],
        [InlineKeyboardButton(text="➕ Выдать POCX", callback_data="admin_give")],
        [InlineKeyboardButton(text="➖ Забрать POCX", callback_data="admin_take")],
        [InlineKeyboardButton(text="🔨 Забанить", callback_data="admin_ban")],
        [InlineKeyboardButton(text="🔓 Разбанить", callback_data="admin_unban")],
        [InlineKeyboardButton(text="📢 Рассылка", callback_data="admin_broadcast")],
        [InlineKeyboardButton(text="🎟 Создать промокод", callback_data="admin_promo")],
        [InlineKeyboardButton(text="🔙 Назад", callback_data="main_menu")]
    ])

@dp.message(Command("admin"))
async def admin_panel(message: Message):
    if message.from_user.id not in ADMIN_IDS:
        await message.answer("❌ Нет доступа!")
        return
    await message.answer("👑 <b>Админ-панель</b>", reply_markup=admin_menu())

@dp.callback_query(F.data == "admin_stats")
async def admin_stats(callback: CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        await callback.answer("❌ Нет доступа!", show_alert=True)
        return
    
    conn = get_db()
    users = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    total_balance = conn.execute("SELECT COALESCE(SUM(coins), 0) FROM users").fetchone()[0]
    total_wagered = conn.execute("SELECT COALESCE(SUM(total_wagered), 0) FROM users").fetchone()[0]
    total_won = conn.execute("SELECT COALESCE(SUM(total_won), 0) FROM users").fetchone()[0]
    total_lost = conn.execute("SELECT COALESCE(SUM(total_lost), 0) FROM users").fetchone()[0]
    banned = conn.execute("SELECT COUNT(*) FROM banned_users").fetchone()[0]
    conn.close()
    
    await callback.message.edit_text(
        f"📊 <b>Статистика казино</b>\n\n"
        f"👥 Пользователей: {users}\n"
        f"🚫 Забанено: {banned}\n"
        f"💰 Общий баланс: {fmt_money(total_balance)}\n"
        f"📊 Общий вейджер: {fmt_money(total_wagered)}\n"
        f"🏆 Выиграно: {fmt_money(total_won)}\n"
        f"💸 Проиграно: {fmt_money(total_lost)}\n"
        f"📈 Прибыль казино: {fmt_money(total_lost - total_won)}",
        reply_markup=admin_menu()
    )
    await callback.answer()

@dp.callback_query(F.data == "admin_give")
async def admin_give_start(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id not in ADMIN_IDS:
        await callback.answer("❌ Нет доступа!", show_alert=True)
        return
    await state.set_state("admin_give_waiting")
    await callback.message.edit_text(
        "➕ <b>Выдача POCX</b>\n\nВведи ID пользователя и сумму через пробел\nПример: <code>123456789 5000</code>",
        reply_markup=admin_menu()
    )
    await callback.answer()

@dp.message(StateFilter("admin_give_waiting"))
async def admin_give_execute(message: Message, state: FSMContext):
    if message.from_user.id not in ADMIN_IDS:
        await message.answer("❌ Нет доступа!")
        await state.clear()
        return
    
    parts = message.text.split()
    if len(parts) != 2 or not parts[0].isdigit():
        await message.answer("❌ Формат: <code>ID сумма</code>\nПример: <code>123456789 5000</code>")
        return
    
    user_id = int(parts[0])
    try:
        amount = parse_amount(parts[1])
    except:
        await message.answer("❌ Неверная сумма!")
        return
    
    balance = add_balance(user_id, amount)
    await message.answer(
        f"✅ Выдано {fmt_money(amount)} пользователю {user_id}\n"
        f"💰 Новый баланс: {fmt_money(balance)}",
        reply_markup=admin_menu()
    )
    await state.clear()

@dp.callback_query(F.data == "admin_take")
async def admin_take_start(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id not in ADMIN_IDS:
        await callback.answer("❌ Нет доступа!", show_alert=True)
        return
    await state.set_state("admin_take_waiting")
    await callback.message.edit_text(
        "➖ <b>Списание POCX</b>\n\nВведи ID пользователя и сумму через пробел\nПример: <code>123456789 5000</code>",
        reply_markup=admin_menu()
    )
    await callback.answer()

@dp.message(StateFilter("admin_take_waiting"))
async def admin_take_execute(message: Message, state: FSMContext):
    if message.from_user.id not in ADMIN_IDS:
        await message.answer("❌ Нет доступа!")
        await state.clear()
        return
    
    parts = message.text.split()
    if len(parts) != 2 or not parts[0].isdigit():
        await message.answer("❌ Формат: <code>ID сумма</code>\nПример: <code>123456789 5000</code>")
        return
    
    user_id = int(parts[0])
    try:
        amount = parse_amount(parts[1])
    except:
        await message.answer("❌ Неверная сумма!")
        return
    
    if not remove_balance(user_id, amount):
        await message.answer(f"❌ У пользователя {user_id} недостаточно средств!")
        return
    
    user = get_user(user_id)
    await message.answer(
        f"✅ Списано {fmt_money(amount)} у пользователя {user_id}\n"
        f"💰 Новый баланс: {fmt_money(user['coins'])}",
        reply_markup=admin_menu()
    )
    await state.clear()

@dp.callback_query(F.data == "admin_ban")
async def admin_ban_start(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id not in ADMIN_IDS:
        await callback.answer("❌ Нет доступа!", show_alert=True)
        return
    await state.set_state("admin_ban_waiting")
    await callback.message.edit_text(
        "🔨 <b>Бан пользователя</b>\n\nВведи ID пользователя\nПример: <code>123456789</code>\nМожно добавить причину через пробел",
        reply_markup=admin_menu()
    )
    await callback.answer()

@dp.message(StateFilter("admin_ban_waiting"))
async def admin_ban_execute(message: Message, state: FSMContext):
    if message.from_user.id not in ADMIN_IDS:
        await message.answer("❌ Нет доступа!")
        await state.clear()
        return
    
    parts = message.text.split(maxsplit=1)
    if not parts[0].isdigit():
        await message.answer("❌ Введи корректный ID!")
        return
    
    user_id = int(parts[0])
    reason = parts[1] if len(parts) > 1 else "Не указана"
    
    if ban_user(user_id, reason):
        await message.answer(f"✅ Пользователь {user_id} забанен\n📝 Причина: {reason}", reply_markup=admin_menu())
    else:
        await message.answer(f"❌ Ошибка при бане пользователя {user_id}", reply_markup=admin_menu())
    await state.clear()

@dp.callback_query(F.data == "admin_unban")
async def admin_unban_start(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id not in ADMIN_IDS:
        await callback.answer("❌ Нет доступа!", show_alert=True)
        return
    await state.set_state("admin_unban_waiting")
    await callback.message.edit_text(
        "🔓 <b>Разбан пользователя</b>\n\nВведи ID пользователя\nПример: <code>123456789</code>",
        reply_markup=admin_menu()
    )
    await callback.answer()

@dp.message(StateFilter("admin_unban_waiting"))
async def admin_unban_execute(message: Message, state: FSMContext):
    if message.from_user.id not in ADMIN_IDS:
        await message.answer("❌ Нет доступа!")
        await state.clear()
        return
    
    if not message.text.isdigit():
        await message.answer("❌ Введи корректный ID!")
        return
    
    user_id = int(message.text)
    if unban_user(user_id):
        await message.answer(f"✅ Пользователь {user_id} разбанен", reply_markup=admin_menu())
    else:
        await message.answer(f"❌ Ошибка при разбане пользователя {user_id}", reply_markup=admin_menu())
    await state.clear()

@dp.callback_query(F.data == "admin_broadcast")
async def admin_broadcast_start(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id not in ADMIN_IDS:
        await callback.answer("❌ Нет доступа!", show_alert=True)
        return
    await state.set_state("admin_broadcast_waiting")
    await callback.message.edit_text(
        "📢 <b>Рассылка</b>\n\nВведи текст рассылки:",
        reply_markup=admin_menu()
    )
    await callback.answer()

@dp.message(StateFilter("admin_broadcast_waiting"))
async def admin_broadcast_execute(message: Message, state: FSMContext):
    if message.from_user.id not in ADMIN_IDS:
        await message.answer("❌ Нет доступа!")
        await state.clear()
        return
    
    text = message.text
    conn = get_db()
    users = conn.execute("SELECT id FROM users").fetchall()
    conn.close()
    
    sent = 0
    for user in users:
        try:
            await bot.send_message(int(user["id"]), f"📢 <b>РАССЫЛКА</b>\n\n{text}")
            sent += 1
            await asyncio.sleep(0.05)
        except:
            pass
    
    await message.answer(f"✅ Рассылка завершена! Отправлено {sent} пользователям.", reply_markup=admin_menu())
    await state.clear()

@dp.callback_query(F.data == "admin_promo")
async def admin_promo_start(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id not in ADMIN_IDS:
        await callback.answer("❌ Нет доступа!", show_alert=True)
        return
    await state.set_state("admin_promo_code")
    await callback.message.edit_text(
        "🎟 <b>Создание промокода</b>\n\nВведи код промокода (только буквы и цифры):",
        reply_markup=admin_menu()
    )
    await callback.answer()

@dp.message(StateFilter("admin_promo_code"))
async def admin_promo_code(message: Message, state: FSMContext):
    code = message.text.strip().upper()
    if not code or len(code) < 3 or len(code) > 24:
        await message.answer("❌ Код должен быть 3-24 символа!")
        return
    await state.update_data(promo_code=code)
    await state.set_state("admin_promo_reward")
    await message.answer("💰 Введи сумму награды:")

@dp.message(StateFilter("admin_promo_reward"))
async def admin_promo_reward(message: Message, state: FSMContext):
    try:
        reward = parse_amount(message.text)
    except:
        await message.answer("❌ Введи корректную сумму!")
        return
    await state.update_data(promo_reward=reward)
    await state.set_state("admin_promo_activations")
    await message.answer("🔢 Введи количество активаций:")

@dp.message(StateFilter("admin_promo_activations"))
async def admin_promo_activations(message: Message, state: FSMContext):
    try:
        activations = int(message.text)
        if activations <= 0:
            raise ValueError
    except:
        await message.answer("❌ Введи положительное целое число!")
        return
    
    data = await state.get_data()
    code = data["promo_code"]
    reward = data["promo_reward"]
    
    conn = get_db()
    conn.execute(
        "INSERT OR REPLACE INTO promos (name, reward, claimed, remaining_activations) VALUES (?, ?, '[]', ?)",
        (code, round(reward, 2), activations)
    )
    conn.commit()
    conn.close()
    
    await message.answer(
        f"✅ Промокод создан!\n"
        f"🎫 Код: <code>{code}</code>\n"
        f"💰 Награда: {fmt_money(reward)}\n"
        f"🎯 Активаций: {activations}",
        reply_markup=admin_menu()
    )
    await state.clear()

# ==================== СТАРТ ====================
@dp.message(CommandStart())
async def start_command(message: Message):
    user_id = message.from_user.id
    
    # Обработка реферальной ссылки
    args = message.text.split()
    if len(args) > 1 and args[1].startswith("ref"):
        ref_code = args[1][3:]
        conn = get_db()
        row = conn.execute("SELECT id FROM users WHERE referral_code = ?", (ref_code,)).fetchone()
        if row and int(row["id"]) != user_id:
            add_referral(int(row["id"]), user_id)
            add_balance(int(row["id"]), 500)
        conn.close()
    
    ensure_user(user_id)
    
    # Генерация реферального кода
    code = get_referral_code(user_id)
    link = f"https://t.me/{(await bot.get_me()).username}?start=ref{code}"
    
    user = get_user(user_id)
    
    await message.answer(
        f"🎰 <b>POCX Casino Bot</b>\n\n"
        f"💰 Баланс: <b>{fmt_money(user['coins'])}</b>\n"
        f"🔗 Твоя реферальная ссылка:\n<code>{link}</code>\n\n"
        f"⭐ 1 Star = 2500 POCX\n"
        f"🔥 КД между играми: 5 секунд\n\n"
        f"Используй кнопки ниже для навигации.",
        reply_markup=main_menu()
    )

# ==================== ЗАПУСК БОТА ====================
async def main():
    init_db()
    print("🤖 POCX Bot запущен!")
    print(f"👥 Админы: {ADMIN_IDS}")
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
