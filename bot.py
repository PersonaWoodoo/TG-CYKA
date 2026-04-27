import asyncio
import random
import sqlite3
import string
from datetime import datetime
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery, Message, LabeledPrice, PreCheckoutQuery
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

# ========== КОНФИГУРАЦИЯ ==========
TOKEN = "8776620773:AAGYaBqZ_Qn_SYEet_o3M-bc8rc6UFXvecA"

# СПИСОК АДМИНОВ
ADMIN_IDS = [8478884644, 8293927811]

# КУРС: 1 Star = 2500 POCX
STARS_TO_POCX = 2500

bot = Bot(token=TOKEN)
dp = Dispatcher()

# ========== БАЗА ДАННЫХ ==========
conn = sqlite3.connect("pocx_bot.db", check_same_thread=False)
cursor = conn.cursor()

# Таблица пользователей
cursor.execute("""
CREATE TABLE IF NOT EXISTS users (
    user_id INTEGER PRIMARY KEY,
    pocx INTEGER DEFAULT 1000,
    ref_count INTEGER DEFAULT 0,
    last_game_time INTEGER DEFAULT 0,
    total_activations_used INTEGER DEFAULT 0,
    username TEXT,
    first_name TEXT
)
""")

# Таблица чеков
cursor.execute("""
CREATE TABLE IF NOT EXISTS checks (
    code TEXT PRIMARY KEY,
    amount INTEGER,
    uses_left INTEGER,
    max_uses INTEGER,
    password TEXT,
    creator_id INTEGER
)
""")

# Таблица казны
cursor.execute("""
CREATE TABLE IF NOT EXISTS treasury (
    chat_id INTEGER PRIMARY KEY,
    amount INTEGER DEFAULT 0
)
""")

# Таблица рефералов
cursor.execute("""
CREATE TABLE IF NOT EXISTS referrals (
    referrer_id INTEGER,
    referred_id INTEGER,
    PRIMARY KEY (referrer_id, referred_id)
)
""")

# Таблица реферальных бонусов
cursor.execute("""
CREATE TABLE IF NOT EXISTS ref_bonus_claimed (
    friend_id INTEGER PRIMARY KEY
)
""")

# Таблица транзакций
cursor.execute("""
CREATE TABLE IF NOT EXISTS transactions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    from_id INTEGER,
    to_id INTEGER,
    amount INTEGER,
    commission INTEGER,
    timestamp INTEGER
)
""")

conn.commit()


# ========== ИГРА БАШНЯ ==========
class TowerGame:
    GRID_SIZE = 4
    MAX_TAPS = 9
    
    def __init__(self, bombs: int):
        self.bombs = bombs
        self.cells = [False] * self.GRID_SIZE
        bomb_positions = random.sample(range(self.GRID_SIZE), bombs)
        for pos in bomb_positions:
            self.cells[pos] = True
        self.multipliers = {1: 0.1, 2: 0.25, 3: 0.6}
        self.alive = True
        self.total_taps = 0
        self.won = 0
    
    def tap(self, index: int) -> tuple:
        if not self.alive:
            return "dead", 0
        if self.total_taps >= self.MAX_TAPS:
            return "complete", self.won
        if self.cells[index]:
            self.alive = False
            return "bomb", 0
        self.total_taps += 1
        win = self.multipliers[self.bombs]
        self.won += win
        if self.total_taps >= self.MAX_TAPS:
            return "complete", self.won
        return "safe", win


# ========== FSM СОСТОЯНИЯ ==========
class TransferState(StatesGroup):
    waiting_for_user = State()
    waiting_for_amount = State()

class CheckState(StatesGroup):
    waiting_for_amount = State()
    waiting_for_max_uses = State()
    waiting_for_password = State()

class DonateState(StatesGroup):
    waiting_for_custom = State()

class AdminState(StatesGroup):
    waiting_for_user_id = State()
    waiting_for_amount = State()
    waiting_for_broadcast = State()


# ========== ГЛАВНОЕ МЕНЮ (ИНЛАЙН) ==========
def main_menu():
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🎮 Игры", callback_data="menu_games")],
        [InlineKeyboardButton(text="💰 Финансы", callback_data="menu_finance")],
        [InlineKeyboardButton(text="🏦 Казна", callback_data="treasury_menu")],
        [InlineKeyboardButton(text="👥 Рефералы", callback_data="referrals")],
        [InlineKeyboardButton(text="ℹ️ Профиль", callback_data="profile")]
    ])
    # Добавляем админ-кнопку только для админов
    if ADMIN_IDS:
        kb.inline_keyboard.append([InlineKeyboardButton(text="👑 Админ-панель", callback_data="admin_panel")])
    return kb

# ========== МЕНЮ ИГР ==========
def games_menu():
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🗼 Башня", callback_data="tower_start")],
        [InlineKeyboardButton(text="🔙 Назад", callback_data="main_menu")]
    ])
    return kb

# ========== ФИНАНСОВОЕ МЕНЮ ==========
def finance_menu():
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💰 Баланс", callback_data="balance")],
        [InlineKeyboardButton(text="💸 Перевести POCX", callback_data="transfer")],
        [InlineKeyboardButton(text="🎁 Создать чек", callback_data="create_check")],
        [InlineKeyboardButton(text="⭐ Активировать чек", callback_data="activate_check_menu")],
        [InlineKeyboardButton(text="💎 Донат (Stars)", callback_data="donate")],
        [InlineKeyboardButton(text="🔙 Назад", callback_data="main_menu")]
    ])
    return kb

# ========== АДМИН-МЕНЮ ==========
def admin_menu():
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📊 Статистика", callback_data="admin_stats")],
        [InlineKeyboardButton(text="➕ Выдать POCX", callback_data="admin_add_pocx")],
        [InlineKeyboardButton(text="➖ Забрать POCX", callback_data="admin_remove_pocx")],
        [InlineKeyboardButton(text="📢 Рассылка", callback_data="admin_broadcast")],
        [InlineKeyboardButton(text="📜 Список чеков", callback_data="admin_checks")],
        [InlineKeyboardButton(text="🗑 Удалить чек", callback_data="admin_delete_check")],
        [InlineKeyboardButton(text="👥 Список пользователей", callback_data="admin_users")],
        [InlineKeyboardButton(text="📈 Топ пользователей", callback_data="admin_top")],
        [InlineKeyboardButton(text="🔙 Назад", callback_data="main_menu")]
    ])
    return kb

def tower_bombs_menu():
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💣 1 бомба (+0.1x за тап)", callback_data="tower_bombs_1")],
        [InlineKeyboardButton(text="💣💣 2 бомбы (+0.25x за тап)", callback_data="tower_bombs_2")],
        [InlineKeyboardButton(text="💣💣💣 3 бомбы (+0.6x за тап)", callback_data="tower_bombs_3")],
        [InlineKeyboardButton(text="🔙 Назад", callback_data="menu_games")]
    ])
    return kb

def tower_bet_menu():
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💰 1000 POCX", callback_data="tower_bet_1000")],
        [InlineKeyboardButton(text="💰 5000 POCX", callback_data="tower_bet_5000")],
        [InlineKeyboardButton(text="💰 10000 POCX", callback_data="tower_bet_10000")],
        [InlineKeyboardButton(text="💰 50000 POCX", callback_data="tower_bet_50000")],
        [InlineKeyboardButton(text="💰 100000 POCX", callback_data="tower_bet_100000")],
        [InlineKeyboardButton(text="🔙 Назад", callback_data="tower_start")]
    ])
    return kb

def tower_game_keyboard(step: int, taps_left: int):
    kb = InlineKeyboardMarkup(inline_row_width=2)
    btns = []
    for i in range(4):
        btns.append(InlineKeyboardButton(text=f"🗼 {i+1}", callback_data=f"tower_tap_{step}_{i}"))
    kb.add(*btns)
    kb.add(InlineKeyboardButton(text=f"💸 Забрать ({taps_left} тапов ост.)", callback_data="tower_cashout"))
    return kb


# ========== ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ==========
def generate_check_code():
    return ''.join(random.choices(string.ascii_uppercase + string.digits, k=8))

def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS

def add_pocx(user_id: int, amount: int):
    cursor.execute("UPDATE users SET pocx = pocx + ? WHERE user_id = ?", (amount, user_id))
    if cursor.rowcount == 0:
        cursor.execute("INSERT INTO users (user_id, pocx) VALUES (?, ?)", (user_id, amount))
    conn.commit()

def remove_pocx(user_id: int, amount: int) -> bool:
    cursor.execute("SELECT pocx FROM users WHERE user_id = ?", (user_id,))
    res = cursor.fetchone()
    if not res or res[0] < amount:
        return False
    cursor.execute("UPDATE users SET pocx = pocx - ? WHERE user_id = ?", (amount, user_id))
    conn.commit()
    return True

def check_cooldown(user_id: int) -> bool:
    cursor.execute("SELECT last_game_time FROM users WHERE user_id = ?", (user_id,))
    res = cursor.fetchone()
    now = int(datetime.now().timestamp())
    if res and res[0]:
        if now - res[0] < 5:
            return False
    cursor.execute("UPDATE users SET last_game_time = ? WHERE user_id = ?", (now, user_id))
    if cursor.rowcount == 0:
        cursor.execute("INSERT INTO users (user_id, last_game_time) VALUES (?, ?)", (user_id, now))
    conn.commit()
    return True


# ========== ОСНОВНЫЕ МЕНЮ ==========
@dp.message(Command("start"))
async def cmd_start(message: Message):
    user_id = message.from_user.id
    
    # Регистрация пользователя
    cursor.execute("SELECT user_id FROM users WHERE user_id = ?", (user_id,))
    if not cursor.fetchone():
        cursor.execute("INSERT INTO users (user_id, pocx, username, first_name) VALUES (?, ?, ?, ?)", 
                       (user_id, 1000, message.from_user.username, message.from_user.first_name))
        conn.commit()
    
    # Реферальная система
    args = message.text.split()
    if len(args) > 1 and args[1].isdigit():
        referrer_id = int(args[1])
        if referrer_id != user_id and is_admin(referrer_id):
            cursor.execute("INSERT OR IGNORE INTO referrals (referrer_id, referred_id) VALUES (?, ?)", (referrer_id, user_id))
            conn.commit()
            
            cursor.execute("SELECT friend_id FROM ref_bonus_claimed WHERE friend_id = ?", (user_id,))
            if not cursor.fetchone():
                bonus = random.randint(1000, 5000)
                for admin_id in ADMIN_IDS:
                    add_pocx(admin_id, bonus)
                cursor.execute("INSERT INTO ref_bonus_claimed (friend_id) VALUES (?)", (user_id,))
                conn.commit()
                for admin_id in ADMIN_IDS:
                    await bot.send_message(admin_id, f"👥 Новый реферал! +{bonus} POCX за друга {user_id}")
    
    await message.answer(
        "🏦 **Добро пожаловать в POCX Bot!**\n\n"
        "💰 Твой бонус: 1000 POCX\n"
        "⭐ Курс: 1 Star = 2500 POCX\n\n"
        "Выбери действие в меню ниже:",
        reply_markup=main_menu(),
        parse_mode="Markdown"
    )

@dp.callback_query(F.data == "main_menu")
async def back_to_main_menu(callback: CallbackQuery):
    await callback.message.edit_text(
        "🏦 **Главное меню POCX Bot**\n\n"
        "Выбери раздел:",
        reply_markup=main_menu(),
        parse_mode="Markdown"
    )

@dp.callback_query(F.data == "menu_games")
async def show_games_menu(callback: CallbackQuery):
    await callback.message.edit_text(
        "🎮 **Игры**\n\n"
        "Выбери игру:",
        reply_markup=games_menu(),
        parse_mode="Markdown"
    )

@dp.callback_query(F.data == "menu_finance")
async def show_finance_menu(callback: CallbackQuery):
    await callback.message.edit_text(
        "💰 **Финансы**\n\n"
        "Управление средствами:",
        reply_markup=finance_menu(),
        parse_mode="Markdown"
    )

@dp.callback_query(F.data == "profile")
async def show_profile(callback: CallbackQuery):
    cursor.execute("SELECT pocx, total_activations_used FROM users WHERE user_id = ?", (callback.from_user.id,))
    res = cursor.fetchone()
    balance = res[0] if res else 0
    activations = res[1] if res else 0
    
    cursor.execute("SELECT COUNT(*) FROM referrals WHERE referred_id = ?", (callback.from_user.id,))
    referred_by = cursor.fetchone()[0]
    
    await callback.message.edit_text(
        f"👤 **Профиль**\n\n"
        f"🆔 ID: `{callback.from_user.id}`\n"
        f"📛 Имя: {callback.from_user.first_name}\n"
        f"💰 Баланс: {balance} POCX\n"
        f"🎫 Использовано активаций: {activations}/50\n"
        f"👥 Пригласил: {referred_by}\n\n"
        f"⭐ Курс: 1 Star = 2500 POCX",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔙 Назад", callback_data="main_menu")]
        ]),
        parse_mode="Markdown"
    )


# ========== ИГРА БАШНЯ ==========
@dp.callback_query(F.data == "tower_start")
async def tower_start(callback: CallbackQuery, state: FSMContext):
    if not check_cooldown(callback.from_user.id):
        await callback.answer("⏳ Подожди 5 секунд!", show_alert=True)
        return
    await state.update_data(tower_game=None, tower_bet=None, tower_bombs=None, tower_wins=0, tower_step=0)
    await callback.message.edit_text(
        "🗼 **Игра Башня**\n\n"
        "Выбери количество бомб:",
        reply_markup=tower_bombs_menu(),
        parse_mode="Markdown"
    )

@dp.callback_query(F.data.startswith("tower_bombs_"))
async def tower_bombs(callback: CallbackQuery, state: FSMContext):
    bombs = int(callback.data.split("_")[2])
    await state.update_data(tower_bombs=bombs)
    await callback.message.edit_text(
        f"🗼 **Игра Башня**\n\n"
        f"💣 Бомб: {bombs}\n"
        f"📊 Множитель за тап: +{['', '0.1', '0.25', '0.6'][bombs]}x\n\n"
        f"Выбери ставку:",
        reply_markup=tower_bet_menu(),
        parse_mode="Markdown"
    )

@dp.callback_query(F.data.startswith("tower_bet_"))
async def tower_bet(callback: CallbackQuery, state: FSMContext):
    bet = int(callback.data.split("_")[2])
    user_id = callback.from_user.id
    
    if not remove_pocx(user_id, bet):
        await callback.answer("❌ Недостаточно POCX!", show_alert=True)
        return
    
    data = await state.get_data()
    bombs = data.get("tower_bombs")
    game = TowerGame(bombs)
    await state.update_data(tower_game=game, tower_bet=bet, tower_wins=0, tower_step=0)
    
    await callback.message.edit_text(
        f"🗼 **Игра Башня**\n\n"
        f"💣 Бомб: {bombs}\n"
        f"💰 Ставка: {bet} POCX\n"
        f"🎯 Осталось тапов: 9\n\n"
        f"**Выбери ячейку (1-4):**",
        reply_markup=tower_game_keyboard(0, 9),
        parse_mode="Markdown"
    )

@dp.callback_query(F.data.startswith("tower_tap_"))
async def tower_tap(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    game = data.get("tower_game")
    bet = data.get("tower_bet")
    wins = data.get("tower_wins", 0)
    step = data.get("tower_step", 0)
    
    if not game:
        await callback.answer("Игра не найдена", show_alert=True)
        return
    
    parts = callback.data.split("_")
    cell = int(parts[3])
    
    result, win = game.tap(cell)
    taps_left = 9 - game.total_taps
    
    if result == "bomb":
        await callback.message.edit_text(
            f"💥 **БОМБА!**\n\n"
            f"Ты проиграл {bet} POCX\n"
            f"🏆 Выигрыш мог составить: {int(bet + bet * wins)} POCX",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🎮 Играть снова", callback_data="tower_start")],
                [InlineKeyboardButton(text="🔙 В меню", callback_data="menu_games")]
            ]),
            parse_mode="Markdown"
        )
        await state.update_data(tower_game=None)
        return
    
    if result == "complete":
        win_amount = int(bet + bet * game.won)
        add_pocx(callback.from_user.id, win_amount)
        await callback.message.edit_text(
            f"🎉 **ПОБЕДА!**\n\n"
            f"✅ Ты прошёл все 9 этажей!\n"
            f"💰 Выигрыш: {win_amount} POCX\n"
            f"📈 Множитель: {game.won:.2f}x",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🎮 Играть снова", callback_data="tower_start")],
                [InlineKeyboardButton(text="🔙 В меню", callback_data="menu_games")]
            ]),
            parse_mode="Markdown"
        )
        await state.update_data(tower_game=None)
        return
    
    wins += win
    step += 1
    await state.update_data(tower_wins=wins, tower_step=step)
    
    await callback.message.edit_text(
        f"🗼 **Игра Башня**\n\n"
        f"✅ Безопасно! +{win}x\n"
        f"📊 Текущий множитель: {wins:.2f}x\n"
        f"💎 Потенциальный выигрыш: {int(bet + bet * wins)} POCX\n"
        f"🎯 Осталось тапов: {taps_left}\n\n"
        f"**Выбери следующую ячейку:**",
        reply_markup=tower_game_keyboard(step, taps_left),
        parse_mode="Markdown"
    )

@dp.callback_query(F.data == "tower_cashout")
async def tower_cashout(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    game = data.get("tower_game")
    bet = data.get("tower_bet")
    wins = data.get("tower_wins", 0)
    
    if not game:
        await callback.answer("Нет активной игры", show_alert=True)
        return
    
    win_amount = int(bet + bet * wins)
    add_pocx(callback.from_user.id, win_amount)
    
    await callback.message.edit_text(
        f"💰 **Ты забрал выигрыш!**\n\n"
        f"💰 Сумма: {win_amount} POCX\n"
        f"📈 Множитель: {wins:.2f}x",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🎮 Играть снова", callback_data="tower_start")],
            [InlineKeyboardButton(text="🔙 В меню", callback_data="menu_games")]
        ]),
        parse_mode="Markdown"
    )
    await state.update_data(tower_game=None)


# ========== ФИНАНСЫ ==========
@dp.callback_query(F.data == "balance")
async def show_balance(callback: CallbackQuery):
    cursor.execute("SELECT pocx FROM users WHERE user_id = ?", (callback.from_user.id,))
    res = cursor.fetchone()
    balance = res[0] if res else 0
    await callback.answer(f"💰 Баланс: {balance} POCX", show_alert=True)

@dp.callback_query(F.data == "activate_check_menu")
async def activate_check_menu(callback: CallbackQuery):
    await callback.message.edit_text(
        "🎫 **Активация чека**\n\n"
        "Используй команду:\n"
        "`/activate КОД [пароль]`\n\n"
        "Пример: `/activate ABC123 pass`",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔙 Назад", callback_data="menu_finance")]
        ]),
        parse_mode="Markdown"
    )


# ========== АДМИН-ПАНЕЛЬ ==========
@dp.callback_query(F.data == "admin_panel")
async def admin_panel(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("❌ Нет доступа!", show_alert=True)
        return
    
    await callback.message.edit_text(
        "👑 **Админ-панель**\n\n"
        "Управление ботом:",
        reply_markup=admin_menu(),
        parse_mode="Markdown"
    )

@dp.callback_query(F.data == "admin_stats")
async def admin_stats(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("❌ Нет доступа!", show_alert=True)
        return
    
    cursor.execute("SELECT COUNT(*) FROM users")
    total_users = cursor.fetchone()[0]
    
    cursor.execute("SELECT SUM(pocx) FROM users")
    total_balance = cursor.fetchone()[0] or 0
    
    cursor.execute("SELECT COUNT(*) FROM checks")
    total_checks = cursor.fetchone()[0]
    
    cursor.execute("SELECT COUNT(*) FROM referrals")
    total_refs = cursor.fetchone()[0]
    
    await callback.message.edit_text(
        f"📊 **Статистика бота**\n\n"
        f"👥 Всего пользователей: {total_users}\n"
        f"💰 Общий баланс: {total_balance} POCX\n"
        f"🎫 Активных чеков: {total_checks}\n"
        f"👥 Рефералов: {total_refs}\n"
        f"⭐ Курс: 1 Star = {STARS_TO_POCX} POCX",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔙 Назад", callback_data="admin_panel")]
        ]),
        parse_mode="Markdown"
    )

@dp.callback_query(F.data == "admin_add_pocx")
async def admin_add_pocx_start(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer("❌ Нет доступа!", show_alert=True)
        return
    
    await callback.message.edit_text(
        "➕ **Выдача POCX**\n\n"
        "Введи ID пользователя:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔙 Отмена", callback_data="admin_panel")]
        ]),
        parse_mode="Markdown"
    )
    await state.set_state(AdminState.waiting_for_user_id)
    await state.update_data(admin_action="add")

@dp.callback_query(F.data == "admin_remove_pocx")
async def admin_remove_pocx_start(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer("❌ Нет доступа!", show_alert=True)
        return
    
    await callback.message.edit_text(
        "➖ **Списание POCX**\n\n"
        "Введи ID пользователя:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔙 Отмена", callback_data="admin_panel")]
        ]),
        parse_mode="Markdown"
    )
    await state.set_state(AdminState.waiting_for_user_id)
    await state.update_data(admin_action="remove")

@dp.message(AdminState.waiting_for_user_id)
async def admin_get_user(message: Message, state: FSMContext):
    if not message.text.isdigit():
        await message.answer("❌ Введи числовой ID!")
        return
    
    await state.update_data(admin_user_id=int(message.text))
    await message.answer("💰 Введи сумму:")
    await state.set_state(AdminState.waiting_for_amount)

@dp.message(AdminState.waiting_for_amount)
async def admin_get_amount(message: Message, state: FSMContext):
    if not message.text.isdigit():
        await message.answer("❌ Введи число!")
        return
    
    data = await state.get_data()
    user_id = data.get("admin_user_id")
    amount = int(message.text)
    action = data.get("admin_action")
    
    if action == "add":
        add_pocx(user_id, amount)
        await message.answer(f"✅ Выдано {amount} POCX пользователю {user_id}")
    else:
        if remove_pocx(user_id, amount):
            await message.answer(f"✅ Списано {amount} POCX у пользователя {user_id}")
        else:
            await message.answer(f"❌ Недостаточно POCX у пользователя {user_id}")
    
    await state.clear()
    await message.answer("👑 Админ-панель:", reply_markup=admin_menu())

@dp.callback_query(F.data == "admin_broadcast")
async def admin_broadcast_start(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer("❌ Нет доступа!", show_alert=True)
        return
    
    await callback.message.edit_text(
        "📢 **Рассылка**\n\n"
        "Введи текст для рассылки:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔙 Отмена", callback_data="admin_panel")]
        ]),
        parse_mode="Markdown"
    )
    await state.set_state(AdminState.waiting_for_broadcast)

@dp.message(AdminState.waiting_for_broadcast)
async def admin_broadcast_send(message: Message, state: FSMContext):
    text = message.text
    
    cursor.execute("SELECT user_id FROM users")
    users = cursor.fetchall()
    
    sent = 0
    for user in users:
        try:
            await bot.send_message(user[0], f"📢 **РАССЫЛКА**\n\n{text}", parse_mode="Markdown")
            sent += 1
            await asyncio.sleep(0.05)
        except:
            pass
    
    await message.answer(f"✅ Рассылка завершена! Отправлено {sent} пользователям.")
    await state.clear()
    await message.answer("👑 Админ-панель:", reply_markup=admin_menu())

@dp.callback_query(F.data == "admin_checks")
async def admin_checks(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("❌ Нет доступа!", show_alert=True)
        return
    
    cursor.execute("SELECT code, amount, uses_left, max_uses FROM checks LIMIT 10")
    checks = cursor.fetchall()
    
    if not checks:
        await callback.message.edit_text(
            "📜 **Список чеков**\n\nНет активных чеков.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🔙 Назад", callback_data="admin_panel")]
            ]),
            parse_mode="Markdown"
        )
        return
    
    text = "📜 **Список чеков (последние 10):**\n\n"
    for code, amount, uses_left, max_uses in checks:
        text += f"🎫 `{code}` - {amount} POCX ({uses_left}/{max_uses})\n"
    
    await callback.message.edit_text(
        text,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔙 Назад", callback_data="admin_panel")]
        ]),
        parse_mode="Markdown"
    )

@dp.callback_query(F.data == "admin_top")
async def admin_top(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("❌ Нет доступа!", show_alert=True)
        return
    
    cursor.execute("SELECT user_id, pocx FROM users ORDER BY pocx DESC LIMIT 10")
    users = cursor.fetchall()
    
    text = "🏆 **Топ пользователей:**\n\n"
    for i, (user_id, balance) in enumerate(users, 1):
        text += f"{i}. 🆔 {user_id} - {balance} POCX\n"
    
    await callback.message.edit_text(
        text,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔙 Назад", callback_data="admin_panel")]
        ]),
        parse_mode="Markdown"
    )

@dp.callback_query(F.data == "admin_users")
async def admin_users(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("❌ Нет доступа!", show_alert=True)
        return
    
    cursor.execute("SELECT COUNT(*) FROM users")
    total = cursor.fetchone()[0]
    
    await callback.message.edit_text(
        f"👥 **Пользователи**\n\n"
        f"Всего: {total}\n\n"
        f"Для просмотра списка используй базу данных.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔙 Назад", callback_data="admin_panel")]
        ]),
        parse_mode="Markdown"
    )


# ========== ПЕРЕВОДЫ ==========
@dp.callback_query(F.data == "transfer")
async def transfer_start(callback: CallbackQuery, state: FSMContext):
    await callback.message.edit_text(
        "💸 **Перевод POCX**\n\n"
        "Комиссия: 3%\n\n"
        "Введи ID пользователя:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔙 Отмена", callback_data="menu_finance")]
        ]),
        parse_mode="Markdown"
    )
    await state.set_state(TransferState.waiting_for_user)

@dp.message(TransferState.waiting_for_user)
async def transfer_user(message: Message, state: FSMContext):
    if not message.text.isdigit():
        await message.answer("❌ Введи числовой ID!")
        return
    await state.update_data(transfer_to=int(message.text))
    await message.answer("💰 Введи сумму перевода (комиссия 3%):")
    await state.set_state(TransferState.waiting_for_amount)

@dp.message(TransferState.waiting_for_amount)
async def transfer_amount(message: Message, state: FSMContext):
    if not message.text.isdigit():
        await message.answer("❌ Введи число!")
        return
    
    amount = int(message.text)
    user_id = message.from_user.id
    data = await state.get_data()
    to_id = data.get("transfer_to")
    
    commission = int(amount * 0.03)
    total = amount + commission
    
    if not remove_pocx(user_id, total):
        await message.answer(f"❌ Недостаточно POCX! Нужно: {total} (включая комиссию 3%)")
        await state.clear()
        return
    
    add_pocx(to_id, amount)
    await message.answer(f"✅ Переведено {amount} POCX пользователю {to_id}\n💸 Комиссия: {commission} POCX")
    await state.clear()
    await message.answer("🏦 Главное меню:", reply_markup=main_menu())


# ========== ЧЕКИ ==========
@dp.callback_query(F.data == "create_check")
async def create_check_start(callback: CallbackQuery, state: FSMContext):
    await callback.message.edit_text(
        "🎁 **Создание чека**\n\n"
        "💰 Введи сумму чека (макс 5,000,000 POCX):",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔙 Отмена", callback_data="menu_finance")]
        ]),
        parse_mode="Markdown"
    )
    await state.set_state(CheckState.waiting_for_amount)

@dp.message(CheckState.waiting_for_amount)
async def check_amount(message: Message, state: FSMContext):
    if not message.text.isdigit():
        await message.answer("❌ Введи число!")
        return
    amount = int(message.text)
    if amount > 5000000:
        await message.answer("❌ Максимум 5,000,000 POCX на один чек!")
        return
    
    if not remove_pocx(message.from_user.id, amount):
        await message.answer("❌ Недостаточно POCX!")
        await state.clear()
        return
    
    await state.update_data(check_amount=amount)
    await message.answer("🔢 Введи количество активаций (макс 50):")
    await state.set_state(CheckState.waiting_for_max_uses)

@dp.message(CheckState.waiting_for_max_uses)
async def check_max_uses(message: Message, state: FSMContext):
    if not message.text.isdigit():
        await message.answer("❌ Введи число!")
        return
    max_uses = int(message.text)
    if max_uses > 50:
        await message.answer("❌ Максимум 50 активаций!")
        return
    
    await state.update_data(check_max_uses=max_uses)
    await message.answer("🔐 Введи пароль для чека (или 'нет' для без пароля):")
    await state.set_state(CheckState.waiting_for_password)

@dp.message(CheckState.waiting_for_password)
async def check_password(message: Message, state: FSMContext):
    data = await state.get_data()
    code = generate_check_code()
    password = None if message.text.lower() == "нет" else message.text
    
    cursor.execute(
        "INSERT INTO checks (code, amount, uses_left, max_uses, password, creator_id) VALUES (?, ?, ?, ?, ?, ?)",
        (code, data["check_amount"], data["check_max_uses"], data["check_max_uses"], password, message.from_user.id)
    )
    conn.commit()
    
    await message.answer(
        f"✅ **Чек создан!**\n\n"
        f"🎫 Код: `{code}`\n"
        f"💰 Сумма: {data['check_amount']} POCX\n"
        f"📊 Активаций: {data['check_max_uses']}\n"
        f"🔐 Пароль: {password or 'нет'}\n\n"
        f"Используй: `/activate {code} {password if password else ''}`",
        parse_mode="Markdown"
    )
    await state.clear()
    await message.answer("🏦 Главное меню:", reply_markup=main_menu())

@dp.message(Command("activate"))
async def activate_check(message: Message):
    args = message.text.split()
    if len(args) < 2:
        await message.answer("❌ Используй: /activate КОД [пароль]")
        return
    
    code = args[1]
    password = args[2] if len(args) > 2 else None
    
    cursor.execute("SELECT amount, uses_left, password, creator_id FROM checks WHERE code = ?", (code,))
    res = cursor.fetchone()
    if not res:
        await message.answer("❌ Чек не найден!")
        return
    
    amount, uses_left, check_password, creator_id = res
    
    if uses_left <= 0:
        await message.answer("❌ Чек уже использован!")
        return
    
    if check_password and check_password != password:
        await message.answer("❌ Неверный пароль!")
        return
    
    cursor.execute("SELECT total_activations_used FROM users WHERE user_id = ?", (message.from_user.id,))
    user_res = cursor.fetchone()
    used = user_res[0] if user_res else 0
    if used >= 50:
        await message.answer("❌ Ты использовал лимит 50 активаций!")
        return
    
    add_pocx(message.from_user.id, amount)
    cursor.execute("UPDATE checks SET uses_left = uses_left - 1 WHERE code = ?", (code,))
    cursor.execute("UPDATE users SET total_activations_used = total_activations_used + 1 WHERE user_id = ?", (message.from_user.id,))
    if cursor.rowcount == 0:
        cursor.execute("INSERT INTO users (user_id, total_activations_used) VALUES (?, 1)", (message.from_user.id,))
    conn.commit()
    
    await message.answer(f"✅ Активировано! +{amount} POCX")


# ========== КАЗНА ==========
@dp.message(Command("add_treasury"))
async def add_treasury(message: Message):
    if message.chat.type == "private":
        await message.answer("❌ Эта команда работает только в чатах!")
        return
    
    args = message.text.split()
    if len(args) != 2 or not args[1].isdigit():
        await message.answer("❌ Используй: /add_treasury 15000")
        return
    
    amount = int(args[1])
    chat_id = message.chat.id
    
    if not remove_pocx(message.from_user.id, amount):
        await message.answer("❌ Недостаточно POCX!")
        return
    
    cursor.execute("INSERT INTO treasury (chat_id, amount) VALUES (?, ?) ON CONFLICT(chat_id) DO UPDATE SET amount = amount + ?", 
                   (chat_id, amount, amount))
    conn.commit()
    
    await message.answer(f"✅ Добавлено {amount} POCX в казну чата!")

@dp.callback_query(F.data == "treasury_menu")
async def treasury_menu(callback: CallbackQuery):
    cursor.execute("SELECT amount FROM treasury WHERE chat_id = ?", (callback.message.chat.id,))
    res = cursor.fetchone()
    amount = res[0] if res else 0
    
    await callback.message.edit_text(
        f"🏦 **Казна чата**\n\n"
        f"💰 Сумма: {amount} POCX\n\n"
        f"🤝 Добавить в казну:\n"
        f"`/add_treasury 15000`\n\n"
        f"💸 Забрать казну могут только администраторы чата",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔙 Назад", callback_data="main_menu")]
        ]),
        parse_mode="Markdown"
    )


# ========== РЕФЕРАЛЫ ==========
@dp.callback_query(F.data == "referrals")
async def show_referrals(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("❌ Только для админов!", show_alert=True)
        return
    
    cursor.execute("SELECT COUNT(*) FROM referrals WHERE referrer_id = ?", (callback.from_user.id,))
    count = cursor.fetchone()[0]
    link = f"https://t.me/{bot.username}?start={callback.from_user.id}"
    
    await callback.message.edit_text(
        f"👥 **Реферальная система**\n\n"
        f"📊 Твои рефералы: {count}\n"
        f"🔗 Твоя ссылка: `{link}`\n\n"
        f"⭐ За каждого нового друга ты получаешь от 1000 до 5000 POCX!",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔙 Назад", callback_data="main_menu")]
        ]),
        parse_mode="Markdown"
    )


# ========== ДОНАТ ==========
@dp.callback_query(F.data == "donate")
async def donate_menu(callback: CallbackQuery):
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⭐ 1 Star - 2500 POCX", callback_data="donate_stars_1")],
        [InlineKeyboardButton(text="⭐⭐ 5 Stars - 12500 POCX", callback_data="donate_stars_5")],
        [InlineKeyboardButton(text="⭐⭐⭐ 10 Stars - 25000 POCX", callback_data="donate_stars_10")],
        [InlineKeyboardButton(text="💰 25 Stars - 62500 POCX", callback_data="donate_stars_25")],
        [InlineKeyboardButton(text="💎 50 Stars - 125000 POCX", callback_data="donate_stars_50")],
        [InlineKeyboardButton(text="✏️ Своя сумма", callback_data="donate_custom_start")],
        [InlineKeyboardButton(text="🔙 Назад", callback_data="menu_finance")]
    ])
    await callback.message.edit_text(
        "💎 **Пополнение через Telegram Stars**\n\n"
        "⭐ 1 Star = 2500 POCX\n\n"
        "Выбери сумму:",
        reply_markup=kb,
        parse_mode="Markdown"
    )

@dp.callback_query(F.data.startswith("donate_stars_"))
async def donate_stars(callback: CallbackQuery):
    stars = int(callback.data.split("_")[2])
    pocx = stars * STARS_TO_POCX
    
    await bot.send_invoice(
        chat_id=callback.from_user.id,
        title="Пополнение POCX",
        description=f"Получи {pocx} POCX за {stars} ⭐\nКурс: 1⭐ = 2500 POCX",
        payload=f"stars_{stars}_{pocx}",
        provider_token="",
        currency="XTR",
        prices=[LabeledPrice(label=f"{stars} Telegram Stars", amount=stars)],
        start_parameter="donate",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔙 Назад", callback_data="donate")]])
    )

@dp.callback_query(F.data == "donate_custom_start")
async def donate_custom_start(callback: CallbackQuery, state: FSMContext):
    await callback.message.edit_text(
        "💰 **Своя сумма**\n\n"
        "Введи количество Stars (1-1000):\n"
        "⭐ 1 Star = 2500 POCX",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔙 Отмена", callback_data="donate")]]),
        parse_mode="Markdown"
    )
    await state.set_state(DonateState.waiting_for_custom)

@dp.message(DonateState.waiting_for_custom)
async def donate_custom(message: Message, state: FSMContext):
    if not message.text.isdigit():
        await message.answer("❌ Введи число!")
        return
    stars = int(message.text)
    if stars < 1 or stars > 1000:
        await message.answer("❌ От 1 до 1000 Stars!")
        return
    
    pocx = stars * STARS_TO_POCX
    await bot.send_invoice(
        chat_id=message.from_user.id,
        title="Пополнение POCX",
        description=f"Получи {pocx} POCX за {stars} ⭐\nКурс: 1⭐ = 2500 POCX",
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
    stars = int(payload.split("_")[1])
    pocx = int(payload.split("_")[2])
    
    add_pocx(message.from_user.id, pocx)
    await message.answer(f"✅ **Оплачено {stars} ⭐!**\n\n💰 Получено {pocx} POCX\n⭐ Курс: 1 Star = 2500 POCX", parse_mode="Markdown")
    await message.answer("🏦 Главное меню:", reply_markup=main_menu())


# ========== КОМАНДА "д 5000" ==========
@dp.message(F.text.lower().startswith("д "))
async def quick_check(message: Message):
    if not message.reply_to_message:
        await message.answer("❌ Ответь на сообщение пользователя!")
        return
    
    args = message.text.split()
    if len(args) != 2 or not args[1].isdigit():
        await message.answer("❌ Используй: д [сумма] (ответом на сообщение)")
        return
    
    amount = int(args[1])
    if amount > 5000000:
        await message.answer("❌ Максимум 5,000,000 POCX!")
        return
    
    if not remove_pocx(message.from_user.id, amount):
        await message.answer("❌ Недостаточно POCX!")
        return
    
    target = message.reply_to_message.from_user.id
    add_pocx(target, amount)
    await message.answer(f"✅ {amount} POCX отправлено пользователю!")


# ========== ЗАПУСК ==========
async def main():
    print("🤖 POCX Bot запущен!")
    print(f"👥 Админы: {ADMIN_IDS}")
    print(f"⭐ Курс: 1 Telegram Star = {STARS_TO_POCX} POCX")
    print(f"🎮 Игра Башня: 9 тапов, 4 ячейки")
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
