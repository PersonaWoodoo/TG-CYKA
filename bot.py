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
    pocx INTEGER DEFAULT 5000,
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
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER,
    amount INTEGER,
    timestamp INTEGER
)
""")

# Таблица рефералов
cursor.execute("""
CREATE TABLE IF NOT EXISTS referrals (
    referrer_id INTEGER,
    referred_id INTEGER,
    bonus_given INTEGER DEFAULT 0,
    PRIMARY KEY (referrer_id, referred_id)
)
""")

# Таблица краш-игры
cursor.execute("""
CREATE TABLE IF NOT EXISTS crash_games (
    game_id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER,
    bet INTEGER,
    multiplier REAL,
    status TEXT,
    timestamp INTEGER
)
""")

conn.commit()

# ========== ИГРЫ ==========

# 1. Игра Башня (9 тапов, 4 ячейки, 1-3 бомбы)
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

# 2. Игра Мины (поле 5x5, 5 мин, безвозмездный бонус 1% к ставке)
class MinesGame:
    GRID_SIZE = 25  # 5x5
    MINES_COUNT = 5
    
    def __init__(self):
        self.cells = [False] * self.GRID_SIZE
        mine_positions = random.sample(range(self.GRID_SIZE), self.MINES_COUNT)
        for pos in mine_positions:
            self.cells[pos] = True
        self.taps = []
        self.alive = True
        self.bonus = 0.01  # 1% безвозмездный бонус
    
    def tap(self, index: int) -> tuple:
        if not self.alive or index in self.taps:
            return "invalid", 0
        if self.cells[index]:
            self.alive = False
            return "mine", 0
        self.taps.append(index)
        multiplier = 0.2 * len(self.taps)  # +0.2x за каждый тап
        return "safe", multiplier

# 3. Игра Кубик (чёт/нечет 1.85x или число 1-6 4x)
class DiceGame:
    @staticmethod
    def roll():
        return random.randint(1, 6)
    
    @staticmethod
    def check_even_odd(num: int, bet_type: str) -> bool:
        if bet_type == "even":
            return num % 2 == 0
        return num % 2 == 1
    
    @staticmethod
    def check_number(num: int, guess: int) -> bool:
        return num == guess

# 4. Игра Краш (ставка и множитель до 50x)
class CrashGame:
    def __init__(self):
        self.multiplier = 1.0
        self.crashed = False
    
    def update(self) -> float:
        # Рандомный рост множителя
        growth = random.uniform(0.05, 0.3)
        self.multiplier += growth
        # Шанс краша ~5% на каждом шагу
        if random.random() < 0.05:
            self.crashed = True
        return self.multiplier
    
    def cashout(self, bet: float) -> float:
        return bet * self.multiplier

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

class TreasuryState(StatesGroup):
    waiting_for_amount = State()
    waiting_for_withdraw_amount = State()

class MinesGameState(StatesGroup):
    waiting_for_confirm = State()

class CrashGameState(StatesGroup):
    waiting_for_action = State()

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

def update_username(user_id: int, username: str, first_name: str):
    cursor.execute("UPDATE users SET username = ?, first_name = ? WHERE user_id = ?", (username, first_name, user_id))
    conn.commit()

# ========== КЛАВИАТУРЫ ==========
def main_menu():
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🎮 Игры", callback_data="menu_games")],
        [InlineKeyboardButton(text="💰 Финансы", callback_data="menu_finance")],
        [InlineKeyboardButton(text="🏦 Казна", callback_data="treasury_menu")],
        [InlineKeyboardButton(text="👥 Рефералы", callback_data="referrals")],
        [InlineKeyboardButton(text="ℹ️ Профиль", callback_data="profile")]
    ])
    if ADMIN_IDS:
        kb.inline_keyboard.append([InlineKeyboardButton(text="👑 Админ-панель", callback_data="admin_panel")])
    return kb

def games_menu():
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🗼 Башня", callback_data="tower_start")],
        [InlineKeyboardButton(text="💣 Мины (1000 POCX)", callback_data="mines_start")],
        [InlineKeyboardButton(text="🎲 Кубик", callback_data="dice_menu")],
        [InlineKeyboardButton(text="💥 Краш", callback_data="crash_menu")],
        [InlineKeyboardButton(text="🔙 Назад", callback_data="main_menu")]
    ])
    return kb

def dice_menu():
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🎲 Чёт/Нечет (x1.85)", callback_data="dice_even_odd")],
        [InlineKeyboardButton(text="🔢 Угадать число 1-6 (x4)", callback_data="dice_number")],
        [InlineKeyboardButton(text="🔙 Назад", callback_data="menu_games")]
    ])
    return kb

def dice_bet_menu():
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="100 POCX", callback_data="dice_bet_100")],
        [InlineKeyboardButton(text="500 POCX", callback_data="dice_bet_500")],
        [InlineKeyboardButton(text="1000 POCX", callback_data="dice_bet_1000")],
        [InlineKeyboardButton(text="5000 POCX", callback_data="dice_bet_5000")],
        [InlineKeyboardButton(text="🔙 Назад", callback_data="dice_menu")]
    ])
    return kb

def dice_even_odd_menu(bet: int):
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Чёт (x1.85)", callback_data=f"dice_play_even_{bet}")],
        [InlineKeyboardButton(text="Нечет (x1.85)", callback_data=f"dice_play_odd_{bet}")],
        [InlineKeyboardButton(text="🔙 Отмена", callback_data="dice_menu")]
    ])
    return kb

def dice_number_menu(bet: int):
    btns = []
    for i in range(1, 7):
        btns.append(InlineKeyboardButton(text=str(i), callback_data=f"dice_play_num_{i}_{bet}"))
    kb = InlineKeyboardMarkup(inline_row_width=3)
    kb.add(*btns)
    kb.add(InlineKeyboardButton(text="🔙 Отмена", callback_data="dice_menu"))
    return kb

def crash_menu():
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="100 POCX", callback_data="crash_bet_100")],
        [InlineKeyboardButton(text="500 POCX", callback_data="crash_bet_500")],
        [InlineKeyboardButton(text="1000 POCX", callback_data="crash_bet_1000")],
        [InlineKeyboardButton(text="5000 POCX", callback_data="crash_bet_5000")],
        [InlineKeyboardButton(text="🔙 Назад", callback_data="menu_games")]
    ])
    return kb

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

def admin_menu():
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📊 Статистика", callback_data="admin_stats")],
        [InlineKeyboardButton(text="➕ Выдать POCX", callback_data="admin_add_pocx")],
        [InlineKeyboardButton(text="➖ Забрать POCX", callback_data="admin_remove_pocx")],
        [InlineKeyboardButton(text="📢 Рассылка", callback_data="admin_broadcast")],
        [InlineKeyboardButton(text="📜 Список чеков", callback_data="admin_checks")],
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
        [InlineKeyboardButton(text="🔙 Назад", callback_data="tower_start")]
    ])
    return kb

def tower_game_keyboard(step: int, taps_left: int):
    kb = InlineKeyboardMarkup(inline_row_width=2)
    btns = []
    for i in range(4):
        btns.append(InlineKeyboardButton(text=f"🗼 {i+1}", callback_data=f"tower_tap_{step}_{i}"))
    kb.add(*btns)
    kb.add(InlineKeyboardButton(text=f"💸 Забрать ({taps_left} тапов)", callback_data="tower_cashout"))
    return kb

def mines_game_keyboard():
    kb = InlineKeyboardMarkup(inline_row_width=5)
    btns = []
    for i in range(25):
        btns.append(InlineKeyboardButton(text="❓", callback_data=f"mines_tap_{i}"))
    kb.add(*btns)
    kb.add(InlineKeyboardButton(text="💸 Забрать выигрыш", callback_data="mines_cashout"))
    return kb

# ========== ОСНОВНЫЕ КОМАНДЫ ДЛЯ ЧАТА ==========
@dp.message(Command("start"))
async def cmd_start(message: Message):
    user_id = message.from_user.id
    update_username(user_id, message.from_user.username, message.from_user.first_name)
    
    cursor.execute("SELECT user_id FROM users WHERE user_id = ?", (user_id,))
    if not cursor.fetchone():
        cursor.execute("INSERT INTO users (user_id, pocx, username, first_name) VALUES (?, ?, ?, ?)", 
                       (user_id, 5000, message.from_user.username, message.from_user.first_name))
        conn.commit()
    
    # Реферальная система
    args = message.text.split()
    if len(args) > 1 and args[1].isdigit():
        referrer_id = int(args[1])
        if referrer_id != user_id and is_admin(referrer_id):
            cursor.execute("INSERT OR IGNORE INTO referrals (referrer_id, referred_id, bonus_given) VALUES (?, ?, 0)", (referrer_id, user_id))
            conn.commit()
            
            cursor.execute("SELECT bonus_given FROM referrals WHERE referrer_id = ? AND referred_id = ?", (referrer_id, user_id))
            res = cursor.fetchone()
            if res and res[0] == 0:
                bonus = random.randint(1000, 5000)
                for admin_id in ADMIN_IDS:
                    add_pocx(admin_id, bonus)
                cursor.execute("UPDATE referrals SET bonus_given = 1 WHERE referrer_id = ? AND referred_id = ?", (referrer_id, user_id))
                conn.commit()
                for admin_id in ADMIN_IDS:
                    await bot.send_message(admin_id, f"👥 Новый реферал! +{bonus} POCX за друга {user_id}")
    
    await message.answer(
        "🏦 **Добро пожаловать в POCX Bot!**\n\n"
        "💰 Твой бонус: 5000 POCX\n"
        "⭐ Курс: 1 Star = 2500 POCX\n\n"
        "📱 **Команды для чата:**\n"
        "• `б` - показать профиль\n"
        "• `мины 1000` - сыграть в Мины на 1000 POCX\n"
        "• `башня 5000` - сыграть в Башню на 5000 POCX\n"
        "• `кубик 1000 чёт` - поставить на чёт\n"
        "• `д 5000` - перевести (в ответ на сообщение)\n\n"
        "Выбери действие в меню ниже:",
        reply_markup=main_menu(),
        parse_mode="Markdown"
    )

# Команда "б" - профиль
@dp.message(Command("б"))
async def chat_profile(message: Message):
    user_id = message.from_user.id
    update_username(user_id, message.from_user.username, message.from_user.first_name)
    
    cursor.execute("SELECT pocx, total_activations_used FROM users WHERE user_id = ?", (user_id,))
    res = cursor.fetchone()
    balance = res[0] if res else 5000
    activations = res[1] if res else 0
    
    cursor.execute("SELECT COUNT(*) FROM referrals WHERE referred_id = ?", (user_id,))
    referred_by = cursor.fetchone()[0]
    
    await message.reply(
        f"👤 **Профиль**\n\n"
        f"🆔 ID: `{user_id}`\n"
        f"📛 Имя: {message.from_user.first_name}\n"
        f"💰 Баланс: {balance} POCX\n"
        f"🎫 Активаций: {activations}/50\n"
        f"👥 Пригласил: {referred_by}",
        parse_mode="Markdown"
    )

# Команда "мины 1000" - игра в Мины
@dp.message(lambda msg: msg.text and msg.text.lower().startswith("мины"))
async def chat_mines_game(message: Message):
    user_id = message.from_user.id
    
    if not check_cooldown(user_id):
        await message.reply("⏳ Подожди 5 секунд между играми!")
        return
    
    args = message.text.split()
    if len(args) != 2 or not args[1].isdigit():
        await message.reply("❌ Используй: `мины [сумма]`\nПример: `мины 1000`", parse_mode="Markdown")
        return
    
    bet = int(args[1])
    if bet != 1000:
        await message.reply("❌ В игре Мины фиксированная ставка - 1000 POCX!")
        return
    
    if not remove_pocx(user_id, bet):
        await message.reply("❌ Недостаточно POCX!")
        return
    
    game = MinesGame()
    
    # Безвозмездный бонус 1%
    bonus_amount = int(bet * game.bonus)
    add_pocx(user_id, bonus_amount)
    
    await message.reply(
        f"💣 **Игра Мины**\n\n"
        f"💰 Ставка: {bet} POCX\n"
        f"🎁 Безвозмездный бонус: +{bonus_amount} POCX\n"
        f"💣 Мин: {MinesGame.MINES_COUNT}\n"
        f"🎯 За каждый безопасный тап +0.2x\n"
        f"🗺 Поле 5x5\n\n"
        f"Выбери клетки, чтобы открыть их:",
        reply_markup=mines_game_keyboard()
    )

# Команда "башня 5000" - игра в Башню
@dp.message(lambda msg: msg.text and msg.text.lower().startswith("башня"))
async def chat_tower_game(message: Message, state: FSMContext):
    user_id = message.from_user.id
    
    if not check_cooldown(user_id):
        await message.reply("⏳ Подожди 5 секунд между играми!")
        return
    
    args = message.text.split()
    if len(args) != 3 or not args[1].isdigit() or args[2] not in ['1', '2', '3']:
        await message.reply("❌ Используй: `башня [сумма] [бомбы]`\nПример: `башня 5000 1`\nБомбы: 1, 2 или 3", parse_mode="Markdown")
        return
    
    bet = int(args[1])
    bombs = int(args[2])
    
    if not remove_pocx(user_id, bet):
        await message.reply("❌ Недостаточно POCX!")
        return
    
    game = TowerGame(bombs)
    await state.update_data(tower_game=game, tower_bet=bet, tower_wins=0, tower_step=0, tower_in_chat=True, tower_chat_id=message.chat.id, tower_msg_id=message.message_id)
    
    await message.reply(
        f"🗼 **Игра Башня**\n\n"
        f"💣 Бомб: {bombs}\n"
        f"💰 Ставка: {bet} POCX\n"
        f"🎯 Всего тапов: {TowerGame.MAX_TAPS}\n"
        f"📊 Множитель за тап: +{game.multipliers[bombs]}x\n\n"
        f"Чтобы продолжить, используй кнопки ниже 👇",
        reply_markup=tower_game_keyboard(0, TowerGame.MAX_TAPS)
    )

# Команда "кубик 1000 чёт" - игра в Кубик
@dp.message(lambda msg: msg.text and msg.text.lower().startswith("кубик"))
async def chat_dice_game(message: Message):
    user_id = message.from_user.id
    
    if not check_cooldown(user_id):
        await message.reply("⏳ Подожди 5 секунд между играми!")
        return
    
    args = message.text.lower().split()
    if len(args) < 3:
        await message.reply("❌ Используй: `кубик [сумма] [чёт/нечет/число]`\nПримеры:\n• `кубик 1000 чёт`\n• `кубик 500 3`", parse_mode="Markdown")
        return
    
    try:
        bet = int(args[1])
    except ValueError:
        await message.reply("❌ Сумма должна быть числом!")
        return
    
    if not remove_pocx(user_id, bet):
        await message.reply("❌ Недостаточно POCX!")
        return
    
    dice_result = DiceGame.roll()
    
    if args[2] == "чёт" or args[2] == "нечет":
        bet_type = args[2]
        win = DiceGame.check_even_odd(dice_result, bet_type)
        if win:
            win_amount = int(bet * 1.85)
            add_pocx(user_id, win_amount)
            await message.reply(f"🎲 **Результат:** {dice_result} ({'чёт' if dice_result % 2 == 0 else 'нечет'})\n✅ Ты выиграл! +{win_amount} POCX")
        else:
            await message.reply(f"🎲 **Результат:** {dice_result} ({'чёт' if dice_result % 2 == 0 else 'нечет'})\n❌ Ты проиграл {bet} POCX")
    else:
        try:
            guess = int(args[2])
            if guess < 1 or guess > 6:
                await message.reply("❌ Число должно быть от 1 до 6!")
                return
            win = DiceGame.check_number(dice_result, guess)
            if win:
                win_amount = int(bet * 4)
                add_pocx(user_id, win_amount)
                await message.reply(f"🎲 **Результат:** {dice_result}\n✅ Ты угадал! +{win_amount} POCX")
            else:
                await message.reply(f"🎲 **Результат:** {dice_result}\n❌ Ты не угадал! Проиграно {bet} POCX")
        except ValueError:
            await message.reply("❌ Неверный формат! Используй 'чёт', 'нечет' или число 1-6")
            return

# Команда "д 5000" - перевод
@dp.message(lambda msg: msg.text and msg.text.lower().startswith("д "))
async def chat_transfer(message: Message):
    if not message.reply_to_message:
        await message.reply("❌ Ответь на сообщение пользователя!")
        return
    
    args = message.text.split()
    if len(args) != 2 or not args[1].isdigit():
        await message.reply("❌ Используй: `д [сумма]` (ответом на сообщение)", parse_mode="Markdown")
        return
    
    amount = int(args[1])
    if amount < 100 or amount > 5000:
        await message.reply("❌ Сумма перевода должна быть от 100 до 5000 POCX!")
        return
    
    if not remove_pocx(message.from_user.id, amount):
        await message.reply("❌ Недостаточно POCX!")
        return
    
    target = message.reply_to_message.from_user.id
    add_pocx(target, amount)
    await message.reply(f"✅ {amount} POCX отправлено пользователю!")

# ========== МЕНЮ БОТА (КОЛБЭКИ) ==========
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

@dp.callback_query(F.data == "balance")
async def show_balance(callback: CallbackQuery):
    cursor.execute("SELECT pocx FROM users WHERE user_id = ?", (callback.from_user.id,))
    res = cursor.fetchone()
    balance = res[0] if res else 5000
    await callback.answer(f"💰 Баланс: {balance} POCX", show_alert=True)

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

# ========== ИГРА МИНЫ ==========
@dp.callback_query(F.data.startswith("mines_tap_"))
async def mines_tap(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    game = data.get("mines_game")
    bet = data.get("mines_bet", 1000)
    taps = data.get("mines_taps", [])
    
    if not game:
        game = MinesGame()
        await state.update_data(mines_game=game, mines_bet=1000, mines_taps=[])
    
    cell = int(callback.data.split("_")[2])
    
    if cell in taps:
        await callback.answer("Ты уже открыл эту клетку!", show_alert=True)
        return
    
    result, multiplier = game.tap(cell)
    
    if result == "mine":
        await callback.message.edit_text(
            f"💥 **МИНА!**\n\n"
            f"Ты проиграл {bet} POCX\n"
            f"📊 Открыто клеток: {len(taps)}\n"
            f"🏆 Потенциальный выигрыш: {int(bet + bet * multiplier)} POCX",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🎮 Играть снова", callback_data="mines_start")],
                [InlineKeyboardButton(text="🔙 В меню", callback_data="menu_games")]
            ])
        )
        await state.update_data(mines_game=None)
        return
    
    taps.append(cell)
    await state.update_data(mines_taps=taps)
    
    # Обновляем клавиатуру, показывая открытые клетки
    kb = InlineKeyboardMarkup(inline_row_width=5)
    for i in range(25):
        if i in taps:
            kb.insert(InlineKeyboardButton(text="✅", callback_data=f"mines_tap_{i}"))
        else:
            kb.insert(InlineKeyboardButton(text="❓", callback_data=f"mines_tap_{i}"))
    kb.add(InlineKeyboardButton(text=f"💸 Забрать ({int(bet + bet * multiplier)} POCX)", callback_data="mines_cashout"))
    
    await callback.message.edit_text(
        f"💣 **Игра Мины**\n\n"
        f"💰 Ставка: {bet} POCX\n"
        f"✅ Безопасных клеток: {len(taps)}\n"
        f"📊 Текущий множитель: {multiplier:.2f}x\n"
        f"💎 Потенциальный выигрыш: {int(bet + bet * multiplier)} POCX\n\n"
        f"Выбери следующую клетку:",
        reply_markup=kb
    )

@dp.callback_query(F.data == "mines_cashout")
async def mines_cashout(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    game = data.get("mines_game")
    bet = data.get("mines_bet", 1000)
    taps = data.get("mines_taps", [])
    
    if not game:
        await callback.answer("Нет активной игры!", show_alert=True)
        return
    
    multiplier = 0.2 * len(taps)
    win_amount = int(bet + bet * multiplier)
    add_pocx(callback.from_user.id, win_amount)
    
    await callback.message.edit_text(
        f"💰 **Ты забрал выигрыш!**\n\n"
        f"📊 Открыто клеток: {len(taps)}\n"
        f"📈 Множитель: {multiplier:.2f}x\n"
        f"💎 Выигрыш: {win_amount} POCX",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🎮 Играть снова", callback_data="mines_start")],
            [InlineKeyboardButton(text="🔙 В меню", callback_data="menu_games")]
        ])
    )
    await state.update_data(mines_game=None)

@dp.callback_query(F.data == "mines_start")
async def mines_start(callback: CallbackQuery, state: FSMContext):
    if not check_cooldown(callback.from_user.id):
        await callback.answer("⏳ Подожди 5 секунд!", show_alert=True)
        return
    
    bet = 1000
    if not remove_pocx(callback.from_user.id, bet):
        await callback.answer("❌ Недостаточно POCX!", show_alert=True)
        return
    
    game = MinesGame()
    bonus_amount = int(bet * game.bonus)
    add_pocx(callback.from_user.id, bonus_amount)
    
    await state.update_data(mines_game=game, mines_bet=bet, mines_taps=[])
    
    await callback.message.edit_text(
        f"💣 **Игра Мины**\n\n"
        f"💰 Ставка: {bet} POCX\n"
        f"🎁 Безвозмездный бонус: +{bonus_amount} POCX\n"
        f"💣 Мин: {MinesGame.MINES_COUNT}\n"
        f"🎯 За каждый безопасный тап +0.2x\n"
        f"🗺 Поле 5x5\n\n"
        f"Выбери клетки, чтобы открыть их:",
        reply_markup=mines_game_keyboard()
    )

# ========== ИГРА КРАШ ==========
crash_games = {}

@dp.callback_query(F.data == "crash_menu")
async def crash_menu_cmd(callback: CallbackQuery):
    await callback.message.edit_text(
        "💥 **Игра Краш**\n\n"
        "💰 Ставка умножается на текущий множитель\n"
        "📈 Множитель растёт до краша\n"
        "🎯 Максимальный множитель: 50x\n\n"
        "Выбери ставку:",
        reply_markup=crash_menu(),
        parse_mode="Markdown"
    )

@dp.callback_query(F.data.startswith("crash_bet_"))
async def crash_start(callback: CallbackQuery, state: FSMContext):
    user_id = callback.from_user.id
    bet = int(callback.data.split("_")[2])
    
    if not check_cooldown(user_id):
        await callback.answer("⏳ Подожди 5 секунд!", show_alert=True)
        return
    
    if not remove_pocx(user_id, bet):
        await callback.answer("❌ Недостаточно POCX!", show_alert=True)
        return
    
    game = CrashGame()
    game.bet = bet
    crash_games[user_id] = game
    
    await state.update_data(crash_bet=bet, crash_multiplier=1.0)
    
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💸 Забрать", callback_data="crash_cashout")],
        [InlineKeyboardButton(text="❌ Выйти", callback_data="menu_games")]
    ])
    
    msg = await callback.message.edit_text(
        f"💥 **Игра Краш**\n\n"
        f"💰 Ставка: {bet} POCX\n"
        f"📈 Текущий множитель: 1.00x\n"
        f"💎 Потенциальный выигрыш: {bet} POCX\n\n"
        f"Жди краша или забирай!",
        reply_markup=kb
    )
    
    # Запускаем обновление множителя
    asyncio.create_task(crash_update_loop(user_id, msg.chat.id, msg.message_id, state))

async def crash_update_loop(user_id: int, chat_id: int, msg_id: int, state: FSMContext):
    game = crash_games.get(user_id)
    if not game:
        return
    
    while not game.crashed:
        await asyncio.sleep(0.5)
        multiplier = game.update()
        
        if multiplier >= 50:
            multiplier = 50
            game.crashed = True
        
        try:
            kb = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="💸 Забрать", callback_data="crash_cashout")],
                [InlineKeyboardButton(text="❌ Выйти", callback_data="menu_games")]
            ])
            await bot.edit_message_text(
                f"💥 **Игра Краш**\n\n"
                f"💰 Ставка: {game.bet} POCX\n"
                f"📈 Текущий множитель: {multiplier:.2f}x\n"
                f"💎 Потенциальный выигрыш: {int(game.bet * multiplier)} POCX\n\n"
                f"Жди краша или забирай!",
                chat_id=chat_id,
                message_id=msg_id,
                reply_markup=kb
            )
            await state.update_data(crash_multiplier=multiplier)
        except:
            pass
    
    # Краш
    try:
        await bot.edit_message_text(
            f"💥 **КРАШ!**\n\n"
            f"💰 Ставка: {game.bet} POCX\n"
            f"📈 Множитель в момент краша: {game.multiplier:.2f}x\n"
            f"❌ Ты проиграл {game.bet} POCX",
            chat_id=chat_id,
            message_id=msg_id,
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🎮 Играть снова", callback_data="crash_menu")],
                [InlineKeyboardButton(text="🔙 В меню", callback_data="menu_games")]
            ])
        )
    except:
        pass
    
    del crash_games[user_id]

@dp.callback_query(F.data == "crash_cashout")
async def crash_cashout(callback: CallbackQuery, state: FSMContext):
    user_id = callback.from_user.id
    game = crash_games.get(user_id)
    
    if not game:
        await callback.answer("Нет активной игры!", show_alert=True)
        return
    
    data = await state.get_data()
    multiplier = data.get("crash_multiplier", 1.0)
    win_amount = int(game.bet * multiplier)
    add_pocx(user_id, win_amount)
    
    await callback.message.edit_text(
        f"💰 **Ты забрал выигрыш!**\n\n"
        f"💰 Ставка: {game.bet} POCX\n"
        f"📈 Множитель: {multiplier:.2f}x\n"
        f"💎 Выигрыш: {win_amount} POCX",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🎮 Играть снова", callback_data="crash_menu")],
            [InlineKeyboardButton(text="🔙 В меню", callback_data="menu_games")]
        ])
    )
    
    del crash_games[user_id]
    await state.update_data(crash_bet=None, crash_multiplier=None)

# ========== ИГРА КУБИК ==========
@dp.callback_query(F.data == "dice_menu")
async def dice_menu_cmd(callback: CallbackQuery):
    await callback.message.edit_text(
        "🎲 **Игра Кубик**\n\n"
        "Выбери тип ставки:",
        reply_markup=dice_menu(),
        parse_mode="Markdown"
    )

@dp.callback_query(F.data == "dice_even_odd")
async def dice_even_odd_menu_cmd(callback: CallbackQuery):
    await callback.message.edit_text(
        "🎲 **Чёт / Нечет**\n\n"
        "Множитель: x1.85\n\n"
        "Выбери ставку:",
        reply_markup=dice_bet_menu(),
        parse_mode="Markdown"
    )
    await callback.answer()

@dp.callback_query(F.data == "dice_number")
async def dice_number_menu_cmd(callback: CallbackQuery):
    await callback.message.edit_text(
        "🎲 **Угадать число**\n\n"
        "Множитель: x4\n\n"
        "Выбери ставку:",
        reply_markup=dice_bet_menu(),
        parse_mode="Markdown"
    )
    await callback.answer()

@dp.callback_query(F.data.startswith("dice_bet_"))
async def dice_bet_selected(callback: CallbackQuery, state: FSMContext):
    bet = int(callback.data.split("_")[2])
    await state.update_data(dice_bet=bet)
    
    # Определяем, откуда пришли (чёт/нечет или число)
    await callback.message.edit_text(
        f"🎲 Ставка: {bet} POCX\n\n"
        f"Выбери:",
        reply_markup=dice_even_odd_menu(bet)
    )

@dp.callback_query(F.data.startswith("dice_play_even_"))
async def dice_play_even(callback: CallbackQuery, state: FSMContext):
    user_id = callback.from_user.id
    bet = int(callback.data.split("_")[3])
    
    if not check_cooldown(user_id):
        await callback.answer("⏳ Подожди 5 секунд!", show_alert=True)
        return
    
    if not remove_pocx(user_id, bet):
        await callback.answer("❌ Недостаточно POCX!", show_alert=True)
        return
    
    dice_result = DiceGame.roll()
    win = DiceGame.check_even_odd(dice_result, "even")
    
    if win:
        win_amount = int(bet * 1.85)
        add_pocx(user_id, win_amount)
        await callback.message.edit_text(
            f"🎲 **Результат:** {dice_result} (чёт)\n"
            f"✅ Ты выиграл! +{win_amount} POCX",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🎲 Играть снова", callback_data="dice_menu")],
                [InlineKeyboardButton(text="🔙 В меню", callback_data="menu_games")]
            ])
        )
    else:
        await callback.message.edit_text(
            f"🎲 **Результат:** {dice_result} (чёт)\n"
            f"❌ Ты проиграл {bet} POCX",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🎲 Играть снова", callback_data="dice_menu")],
                [InlineKeyboardButton(text="🔙 В меню", callback_data="menu_games")]
            ])
        )
    await state.clear()

@dp.callback_query(F.data.startswith("dice_play_odd_"))
async def dice_play_odd(callback: CallbackQuery, state: FSMContext):
    user_id = callback.from_user.id
    bet = int(callback.data.split("_")[3])
    
    if not check_cooldown(user_id):
        await callback.answer("⏳ Подожди 5 секунд!", show_alert=True)
        return
    
    if not remove_pocx(user_id, bet):
        await callback.answer("❌ Недостаточно POCX!", show_alert=True)
        return
    
    dice_result = DiceGame.roll()
    win = DiceGame.check_even_odd(dice_result, "odd")
    
    if win:
        win_amount = int(bet * 1.85)
        add_pocx(user_id, win_amount)
        await callback.message.edit_text(
            f"🎲 **Результат:** {dice_result} (нечет)\n"
            f"✅ Ты выиграл! +{win_amount} POCX",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🎲 Играть снова", callback_data="dice_menu")],
                [InlineKeyboardButton(text="🔙 В меню", callback_data="menu_games")]
            ])
        )
    else:
        await callback.message.edit_text(
            f"🎲 **Результат:** {dice_result} (нечет)\n"
            f"❌ Ты проиграл {bet} POCX",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🎲 Играть снова", callback_data="dice_menu")],
                [InlineKeyboardButton(text="🔙 В меню", callback_data="menu_games")]
            ])
        )
    await state.clear()

@dp.callback_query(F.data.startswith("dice_play_num_"))
async def dice_play_number(callback: CallbackQuery, state: FSMContext):
    user_id = callback.from_user.id
    parts = callback.data.split("_")
    guess = int(parts[3])
    bet = int(parts[4])
    
    if not check_cooldown(user_id):
        await callback.answer("⏳ Подожди 5 секунд!", show_alert=True)
        return
    
    if not remove_pocx(user_id, bet):
        await callback.answer("❌ Недостаточно POCX!", show_alert=True)
        return
    
    dice_result = DiceGame.roll()
    win = DiceGame.check_number(dice_result, guess)
    
    if win:
        win_amount = int(bet * 4)
        add_pocx(user_id, win_amount)
        await callback.message.edit_text(
            f"🎲 **Результат:** {dice_result}\n"
            f"✅ Ты угадал! +{win_amount} POCX",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🎲 Играть снова", callback_data="dice_menu")],
                [InlineKeyboardButton(text="🔙 В меню", callback_data="menu_games")]
            ])
        )
    else:
        await callback.message.edit_text(
            f"🎲 **Результат:** {dice_result}\n"
            f"❌ Ты не угадал! Проиграно {bet} POCX",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🎲 Играть снова", callback_data="dice_menu")],
                [InlineKeyboardButton(text="🔙 В меню", callback_data="menu_games")]
            ])
        )
    await state.clear()

# ========== ИГРА БАШНЯ (КОЛБЭКИ) ==========
@dp.callback_query(F.data == "tower_start")
async def tower_start_cmd(callback: CallbackQuery, state: FSMContext):
    if not check_cooldown(callback.from_user.id):
        await callback.answer("⏳ Подожди 5 секунд!", show_alert=True)
        return
    await state.update_data(tower_game=None, tower_bet=None, tower_bombs=None, tower_wins=0, tower_step=0, tower_in_chat=False)
    await callback.message.edit_text(
        "🗼 **Игра Башня**\n\n"
        "Выбери количество бомб:",
        reply_markup=tower_bombs_menu(),
        parse_mode="Markdown"
    )

@dp.callback_query(F.data.startswith("tower_bombs_"))
async def tower_bombs_cmd(callback: CallbackQuery, state: FSMContext):
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
async def tower_bet_cmd(callback: CallbackQuery, state: FSMContext):
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
        f"🎯 Осталось тапов: {TowerGame.MAX_TAPS}\n\n"
        f"**Выбери ячейку (1-4):**",
        reply_markup=tower_game_keyboard(0, TowerGame.MAX_TAPS),
        parse_mode="Markdown"
    )

@dp.callback_query(F.data.startswith("tower_tap_"))
async def tower_tap_cmd(callback: CallbackQuery, state: FSMContext):
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
    taps_left = TowerGame.MAX_TAPS - game.total_taps
    
    if result == "bomb":
        await callback.message.edit_text(
            f"💥 **БОМБА!**\n\n"
            f"Ты проиграл {bet} POCX\n"
            f"
