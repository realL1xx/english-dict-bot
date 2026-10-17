import asyncio
import os
import sqlite3
from datetime import datetime, timezone, timedelta

from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    ReplyKeyboardMarkup,
    KeyboardButton,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
)
from aiogram.exceptions import TelegramForbiddenError, TelegramBadRequest

# ====================== НАСТРОЙКИ ======================
TOKEN = os.getenv("BOT_TOKEN") or "ВСТАВЬ_СЮДА_ТОКЕН"

TEACHERS = {"elenaedvlasova"}
ADMINS = {"l1xxoff"}

KGD_TZ = timezone(timedelta(hours=2))

DB_PATH = "/app/data/english_dict.db"

RECENT_DAYS = 8


# ====================== БАЗА ДАННЫХ ======================
def init_db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)

    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS words (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            word TEXT NOT NULL UNIQUE,
            translation TEXT NOT NULL,
            added_at TEXT NOT NULL
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS homework (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            text TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            first_name TEXT,
            added_at TEXT NOT NULL
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            from_user_id INTEGER NOT NULL,
            from_username TEXT,
            from_name TEXT,
            text TEXT NOT NULL,
            created_at TEXT NOT NULL,
            is_read INTEGER NOT NULL DEFAULT 0,
            reply_text TEXT,
            replied_at TEXT
        )
    """)
    conn.commit()
    conn.close()


def get_connection():
    return sqlite3.connect(DB_PATH)


def save_user(user_id: int, username: str | None, first_name: str | None):
    conn = get_connection()
    c = conn.cursor()
    now = datetime.now(KGD_TZ).isoformat()
    c.execute(
        "INSERT OR IGNORE INTO users (user_id, username, first_name, added_at) "
        "VALUES (?, ?, ?, ?)",
        (user_id, username, first_name, now),
    )
    conn.commit()
    conn.close()


def get_all_users():
    conn = get_connection()
    c = conn.cursor()
    c.execute("SELECT user_id FROM users")
    rows = c.fetchall()
    conn.close()
    return [row[0] for row in rows]


def get_users_detailed():
    conn = get_connection()
    c = conn.cursor()
    c.execute(
        "SELECT user_id, username, first_name, added_at FROM users ORDER BY added_at ASC"
    )
    rows = c.fetchall()
    conn.close()
    return rows


# NEW: обновить username и first_name у всех пользователей через Telegram API
async def refresh_usernames() -> tuple[int, int]:
    """Возвращает (обновлено, ошибок)."""
    conn = get_connection()
    c = conn.cursor()
    c.execute("SELECT user_id FROM users")
    user_ids = [row[0] for row in c.fetchall()]
    conn.close()

    updated = 0
    errors = 0

    for uid in user_ids:
        try:
            chat = await bot.get_chat(uid)
            new_username = chat.username
            new_first_name = chat.first_name

            conn = get_connection()
            c = conn.cursor()
            c.execute(
                "UPDATE users SET username = ?, first_name = ? WHERE user_id = ?",
                (new_username, new_first_name, uid),
            )
            conn.commit()
            conn.close()
            updated += 1
        except (TelegramForbiddenError, TelegramBadRequest):
            errors += 1
        except Exception:
            errors += 1

        await asyncio.sleep(0.05)

    return updated, errors


def save_message(user_id: int, username: str | None, name: str | None, text: str) -> int:
    conn = get_connection()
    c = conn.cursor()
    now = datetime.now(KGD_TZ).isoformat()
    c.execute(
        "INSERT INTO messages (from_user_id, from_username, from_name, text, created_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (user_id, username, name, text, now),
    )
    msg_id = c.lastrowid
    conn.commit()
    conn.close()
    return msg_id


def get_unread_messages():
    conn = get_connection()
    c = conn.cursor()
    c.execute(
        "SELECT id, from_user_id, from_username, from_name, text, created_at "
        "FROM messages WHERE is_read = 0 ORDER BY id ASC"
    )
    rows = c.fetchall()
    conn.close()
    return rows


def get_all_messages(limit: int = 30):
    conn = get_connection()
    c = conn.cursor()
    c.execute(
        "SELECT id, from_user_id, from_username, from_name, text, created_at, "
        "is_read, reply_text, replied_at "
        "FROM messages ORDER BY id DESC LIMIT ?",
        (limit,),
    )
    rows = c.fetchall()
    conn.close()
    return rows


def get_message_by_id(msg_id: int):
    conn = get_connection()
    c = conn.cursor()
    c.execute(
        "SELECT id, from_user_id, from_username, from_name, text, created_at, "
        "is_read, reply_text, replied_at "
        "FROM messages WHERE id = ?",
        (msg_id,),
    )
    row = c.fetchone()
    conn.close()
    return row


def mark_message_read(msg_id: int):
    conn = get_connection()
    c = conn.cursor()
    c.execute("UPDATE messages SET is_read = 1 WHERE id = ?", (msg_id,))
    conn.commit()
    conn.close()


def save_reply(msg_id: int, reply_text: str):
    conn = get_connection()
    c = conn.cursor()
    now = datetime.now(KGD_TZ).isoformat()
    c.execute(
        "UPDATE messages SET reply_text = ?, replied_at = ?, is_read = 1 WHERE id = ?",
        (reply_text, now, msg_id),
    )
    conn.commit()
    conn.close()


# ====================== ХЕЛПЕРЫ ======================
def get_role(username: str | None) -> str:
    if not username:
        return "guest"
    u = username.lower()
    if u in ADMINS:
        return "admin"
    if u in TEACHERS:
        return "teacher"
    return "student"


def is_allowed(username: str | None) -> bool:
    return get_role(username) in ("admin", "teacher", "student")


def get_word_count():
    conn = get_connection()
    c = conn.cursor()
    c.execute("SELECT COUNT(*) FROM words")
    count = c.fetchone()[0]
    conn.close()
    return count


def get_last_update():
    conn = get_connection()
    c = conn.cursor()
    c.execute("SELECT added_at FROM words ORDER BY id DESC LIMIT 1")
    row = c.fetchone()
    word_time = row[0] if row else None

    c.execute("SELECT updated_at FROM homework ORDER BY id DESC LIMIT 1")
    row = c.fetchone()
    hw_time = row[0] if row else None
    conn.close()

    times = [t for t in (word_time, hw_time) if t]
    if not times:
        return None
    return max(times)


def get_homework():
    conn = get_connection()
    c = conn.cursor()
    c.execute("SELECT text FROM homework ORDER BY id DESC LIMIT 1")
    row = c.fetchone()
    conn.close()
    return row[0] if row else "ДЗ пока не задано"


def add_word(word: str, translation: str) -> bool:
    conn = get_connection()
    c = conn.cursor()
    now = datetime.now(KGD_TZ).isoformat()
    try:
        c.execute(
            "INSERT INTO words (word, translation, added_at) VALUES (?, ?, ?)",
            (word, translation, now),
        )
        conn.commit()
        return True
    except sqlite3.IntegrityError:
        return False
    finally:
        conn.close()


def delete_word_by_id(word_id: int) -> tuple[bool, str, str]:
    conn = get_connection()
    c = conn.cursor()
    c.execute("SELECT word, translation FROM words WHERE id = ?", (word_id,))
    row = c.fetchone()
    if not row:
        conn.close()
        return False, "", ""
    word, translation = row
    c.execute("DELETE FROM words WHERE id = ?", (word_id,))
    conn.commit()
    conn.close()
    return True, word, translation


def add_homework(text: str):
    conn = get_connection()
    c = conn.cursor()
    now = datetime.now(KGD_TZ).isoformat()
    c.execute(
        "INSERT INTO homework (text, updated_at) VALUES (?, ?)",
        (text, now),
    )
    conn.commit()
    conn.close()


def get_letters():
    conn = get_connection()
    c = conn.cursor()
    c.execute("SELECT DISTINCT word FROM words")
    rows = c.fetchall()
    conn.close()
    return sorted({row[0][0].upper() for row in rows if row[0]})


def get_words_by_letter(letter: str):
    conn = get_connection()
    c = conn.cursor()
    c.execute(
        "SELECT id, word, translation FROM words "
        "WHERE UPPER(word) LIKE ? ORDER BY word COLLATE NOCASE",
        (f"{letter}%",),
    )
    rows = c.fetchall()
    conn.close()
    return rows


def get_all_words():
    conn = get_connection()
    c = conn.cursor()
    c.execute(
        "SELECT id, word, translation FROM words ORDER BY word COLLATE NOCASE"
    )
    rows = c.fetchall()
    conn.close()
    return rows


def get_recent_words():
    conn = get_connection()
    c = conn.cursor()
    threshold = (datetime.now(KGD_TZ) - timedelta(days=RECENT_DAYS)).isoformat()
    c.execute(
        "SELECT id, word, translation FROM words "
        "WHERE added_at >= ? ORDER BY word COLLATE NOCASE",
        (threshold,),
    )
    rows = c.fetchall()
    conn.close()
    return rows


def format_word(word: str, translation: str) -> str:
    return f"<b>{word}</b> — {translation}"


def format_datetime(iso_str: str) -> str:
    try:
        dt = datetime.fromisoformat(iso_str)
        return dt.strftime("%d.%m.%Y %H:%M")
    except Exception:
        return iso_str


# ====================== КЛАВИАТУРЫ ======================
def main_menu(role: str) -> ReplyKeyboardMarkup:
    buttons = [
        [KeyboardButton(text="📖 Словарь")],
        [KeyboardButton(text="ℹ️ Информация")],
    ]

    if role in ("student", "teacher"):
        buttons.append([KeyboardButton(text="✉️ Сообщение администратору")])

    if role in ("admin", "teacher"):
        buttons.append([KeyboardButton(text="➕ Добавить")])

    if role == "admin":
        buttons.append([KeyboardButton(text="📨 Сообщения")])
        buttons.append([KeyboardButton(text="🛠 Диагностика")])

    return ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True)


def dictionary_keyboard(letters: list[str], role: str) -> InlineKeyboardMarkup:
    rows = []
    row = []
    for letter in letters:
        row.append(InlineKeyboardButton(text=letter, callback_data=f"letter:{letter}"))
        if len(row) == 6:
            rows.append(row)
            row = []
    if row:
        rows.append(row)

    rows.append([InlineKeyboardButton(text="📚 Все слова", callback_data="all_words")])

    if role in ("admin", "teacher"):
        rows.append(
            [InlineKeyboardButton(text="🗑 Удалить слово", callback_data="delete_start")]
        )

    return InlineKeyboardMarkup(inline_keyboard=rows)


def add_menu() -> ReplyKeyboardMarkup:
    buttons = [
        [KeyboardButton(text="📝 Добавить слово")],
        [KeyboardButton(text="📚 Добавить ДЗ")],
        [KeyboardButton(text="🔙 Назад")],
    ]
    return ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True)


def diagnostic_menu() -> ReplyKeyboardMarkup:
    buttons = [
        [KeyboardButton(text="▶️ Старт")],
        [KeyboardButton(text="🔄 Обновление")],
        [KeyboardButton(text="📢 Рассылка")],
        [KeyboardButton(text="👥 Пользователи")],
        [KeyboardButton(text="⏹ Конец")],
        [KeyboardButton(text="🔙 Назад")],
    ]
    return ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True)


def cancel_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="❌ Отмена")]],
        resize_keyboard=True,
    )


def delete_words_keyboard(words: list) -> InlineKeyboardMarkup:
    rows = []
    for word_id, word, translation in words:
        rows.append(
            [InlineKeyboardButton(text=word, callback_data=f"del_word:{word_id}")]
        )
    rows.append([InlineKeyboardButton(text="🔙 Назад", callback_data="delete_back")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def confirm_delete_keyboard(word_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ Да, удалить", callback_data=f"del_confirm:{word_id}"
                ),
                InlineKeyboardButton(text="❌ Отмена", callback_data="delete_back"),
            ]
        ]
    )


def messages_list_keyboard(messages: list) -> InlineKeyboardMarkup:
    rows = []
    for msg_id, from_user_id, from_username, from_name, text, created_at in messages:
        name = from_name or from_username or f"id{from_user_id}"
        preview = text[:30].replace("\n", " ")
        if len(text) > 30:
            preview += "…"
        label = f"🆕 {name}: {preview}"
        rows.append(
            [InlineKeyboardButton(text=label, callback_data=f"msg_open:{msg_id}")]
        )
    rows.append([InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_main_cb")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def message_open_keyboard(msg_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✍️ Ответить", callback_data=f"msg_reply:{msg_id}"
                )
            ],
            [
                InlineKeyboardButton(
                    text="🔙 К сообщениям", callback_data="messages_list"
                )
            ],
        ]
    )


# NEW: клавиатура под списком пользователей
def users_list_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🔄 Обновить ники", callback_data="users_refresh"
                )
            ],
            [
                InlineKeyboardButton(
                    text="🔙 Назад", callback_data="diag_back"
                )
            ],
        ]
    )


# ====================== FSM ======================
class AddWord(StatesGroup):
    word = State()
    translation = State()


class AddHW(StatesGroup):
    text = State()


class Broadcast(StatesGroup):
    update_text = State()
    custom_text = State()


class UserMessage(StatesGroup):
    text = State()


class AdminReply(StatesGroup):
    text = State()


# ====================== БОТ ======================
bot = Bot(token=TOKEN)
dp = Dispatcher()


async def broadcast_text(text: str):
    for uid in get_all_users():
        try:
            await bot.send_message(uid, text, parse_mode="HTML")
        except (TelegramForbiddenError, TelegramBadRequest):
            pass
        except Exception:
            pass
        await asyncio.sleep(0.05)


async def broadcast_new_word(word: str, translation: str):
    text = (
        f"🆕 <b>Добавлено новое слово!</b>\n\n"
        f"{format_word(word, translation)}"
    )
    await broadcast_text(text)


async def broadcast_deleted_word(word: str, translation: str):
    text = (
        f"🗑 <b>Слово удалено из словаря</b>\n\n"
        f"<b>{word}</b> — {translation}"
    )
    await broadcast_text(text)


async def send_long_message(message: types.Message, text: str, reply_markup=None):
    LIMIT = 4000
    parts = []
    while len(text) > LIMIT:
        cut = text.rfind("\n", 0, LIMIT)
        if cut == -1:
            cut = LIMIT
        parts.append(text[:cut])
        text = text[cut:].lstrip("\n")
    parts.append(text)

    for i, part in enumerate(parts):
        if i == len(parts) - 1 and reply_markup is not None:
            await message.answer(part, parse_mode="HTML", reply_markup=reply_markup)
        else:
            await message.answer(part, parse_mode="HTML")


def get_admin_user_ids():
    conn = get_connection()
    c = conn.cursor()
    c.execute("SELECT user_id, username FROM users")
    rows = c.fetchall()
    conn.close()
    result = []
    for uid, uname in rows:
        if uname and uname.lower() in ADMINS:
            result.append(uid)
    return result


# ---------- /start ----------
@dp.message(Command("start"))
async def cmd_start(message: types.Message, state: FSMContext):
    await state.clear()
    save_user(
        message.from_user.id,
        message.from_user.username,
        message.from_user.first_name,
    )

    username = message.from_user.username
    if not is_allowed(username):
        await message.answer(
            "⛔ Доступ запрещён.\n"
            "Этот бот только для учеников, учителей и админов."
        )
        return

    role = get_role(username)
    await message.answer(
        f"Привет, {message.from_user.first_name}! 👋\n"
        f"Твоя роль: {role}",
        reply_markup=main_menu(role),
    )


# ---------- Словарь ----------
@dp.message(F.text == "📖 Словарь")
async def show_dictionary(message: types.Message, state: FSMContext):
    if not is_allowed(message.from_user.username):
        return
    await state.clear()

    role = get_role(message.from_user.username)

    recent = get_recent_words()
    if recent:
        lines = [f"🕒 <b>Недавние слова (за {RECENT_DAYS} дней):</b>\n"]
        for _, word, translation in recent:
            lines.append(format_word(word, translation))
        recent_text = "\n".join(lines)
        await send_long_message(message, recent_text)

    letters = get_letters()
    if not letters:
        await message.answer("Словарь пуст. Учитель ещё не добавил слова.")
        return

    await message.answer(
        "Выбери первую букву слова или нажми «Все слова»:",
        reply_markup=dictionary_keyboard(letters, role),
    )


@dp.callback_query(F.data.startswith("letter:"))
async def show_words(callback: types.CallbackQuery):
    if not is_allowed(callback.from_user.username):
        await callback.answer("Нет доступа", show_alert=True)
        return

    letter = callback.data.split(":")[1]
    words = get_words_by_letter(letter)

    if not words:
        await callback.message.edit_text(f"На букву {letter} слов нет.")
        await callback.answer()
        return

    lines = [f"<b>Слова на «{letter}»</b>\n"]
    for _, word, translation in words:
        lines.append(format_word(word, translation))

    text = "\n".join(lines)
    back_kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🔙 К буквам", callback_data="back_to_letters")]
        ]
    )
    await callback.message.edit_text(text, parse_mode="HTML", reply_markup=back_kb)
    await callback.answer()


@dp.callback_query(F.data == "all_words")
async def show_all_words(callback: types.CallbackQuery):
    if not is_allowed(callback.from_user.username):
        await callback.answer("Нет доступа", show_alert=True)
        return

    words = get_all_words()
    if not words:
        await callback.message.edit_text("Словарь пуст.")
        await callback.answer()
        return

    lines = [f"📚 <b>Все слова ({len(words)})</b>\n"]

    current_letter = None
    for _, word, translation in words:
        first = word[0].upper()
        if first != current_letter:
            if current_letter is not None:
                lines.append("")
            lines.append(f"— <b>{first}</b> —")
            current_letter = first
        lines.append(format_word(word, translation))

    text = "\n".join(lines)

    back_kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🔙 К буквам", callback_data="back_to_letters")]
        ]
    )

    await callback.message.edit_text(text, parse_mode="HTML", reply_markup=back_kb)
    await callback.answer()


@dp.callback_query(F.data == "back_to_letters")
async def back_to_letters(callback: types.CallbackQuery):
    if not is_allowed(callback.from_user.username):
        return
    role = get_role(callback.from_user.username)
    letters = get_letters()

    if not letters:
        await callback.message.edit_text("Словарь пуст.")
        await callback.answer()
        return

    await callback.message.edit_text(
        "Выбери первую букву слова или нажми «Все слова»:",
        reply_markup=dictionary_keyboard(letters, role),
    )
    await callback.answer()


# ---------- Информация ----------
@dp.message(F.text == "ℹ️ Информация")
async def show_info(message: types.Message):
    if not is_allowed(message.from_user.username):
        return

    count = get_word_count()
    last_update = get_last_update()
    hw = get_homework()

    if last_update:
        last_update_str = format_datetime(last_update)
    else:
        last_update_str = "ещё не было"

    text = (
        f"📊 <b>Информация о словаре</b>\n\n"
        f"📖 Слов в словаре: <b>{count}</b>\n"
        f"🕒 Последнее обновление: <b>{last_update_str}</b> (Калининград)\n\n"
        f"📚 <b>ДЗ на следующий урок:</b>\n{hw}"
    )
    await message.answer(text, parse_mode="HTML")


# ---------- Сообщение администратору (ученик/учитель) ----------
@dp.message(F.text == "✉️ Сообщение администратору")
async def user_msg_start(message: types.Message, state: FSMContext):
    role = get_role(message.from_user.username)
    if role not in ("student", "teacher"):
        return

    await state.set_state(UserMessage.text)
    await message.answer(
        "Напиши сообщение для администратора.\n"
        "Он получит его и сможет ответить.\n\n"
        "Или нажми «❌ Отмена».",
        reply_markup=cancel_keyboard(),
    )


@dp.message(UserMessage.text)
async def user_msg_send(message: types.Message, state: FSMContext):
    if message.text == "❌ Отмена":
        await state.clear()
        role = get_role(message.from_user.username)
        await message.answer("Отменено.", reply_markup=main_menu(role))
        return

    text = message.text.strip()
    save_message(
        message.from_user.id,
        message.from_user.username,
        message.from_user.first_name,
        text,
    )

    admin_ids = get_admin_user_ids()
    if not admin_ids:
        await message.answer(
            "⚠️ В системе пока нет активных администраторов. "
            "Сообщение сохранено, но доставка не гарантирована.",
            reply_markup=main_menu(get_role(message.from_user.username)),
        )
        await state.clear()
        return

    sender_name = message.from_user.first_name or message.from_user.username or "без имени"
    sender_username = f" (@{message.from_user.username})" if message.from_user.username else ""

    notif = (
        f"📨 <b>Новое сообщение от ученика/учителя</b>\n\n"
        f"От: <b>{sender_name}</b>{sender_username}\n\n"
        f"{text}\n\n"
        f"<i>Ответить можно через «📨 Сообщения».</i>"
    )

    for admin_id in admin_ids:
        try:
            await bot.send_message(admin_id, notif, parse_mode="HTML")
        except (TelegramForbiddenError, TelegramBadRequest):
            pass
        except Exception:
            pass
        await asyncio.sleep(0.05)

    role = get_role(message.from_user.username)
    await message.answer(
        "✅ Сообщение отправлено администратору.",
        reply_markup=main_menu(role),
    )
    await state.clear()


# ---------- Сообщения (админ) ----------
@dp.message(F.text == "📨 Сообщения")
async def admin_messages_list(message: types.Message, state: FSMContext):
    role = get_role(message.from_user.username)
    if role != "admin":
        return
    await state.clear()

    unread = get_unread_messages()
    if unread:
        text = f"📨 <b>Непрочитанные сообщения ({len(unread)}):</b>\n\n"
        text += "Выбери сообщение, чтобы прочитать и ответить."
        await message.answer(
            text,
            parse_mode="HTML",
            reply_markup=messages_list_keyboard(unread),
        )
        return

    all_msgs = get_all_messages(limit=10)
    if not all_msgs:
        await message.answer("Сообщений пока нет.")
        return

    text = "📨 <b>Непрочитанных нет. Последние сообщения:</b>\n\n"
    await message.answer(
        text,
        parse_mode="HTML",
        reply_markup=messages_list_keyboard([
            (m[0], m[1], m[2], m[3], m[4], m[5]) for m in all_msgs
        ]),
    )


@dp.callback_query(F.data == "messages_list")
async def admin_messages_list_cb(callback: types.CallbackQuery):
    role = get_role(callback.from_user.username)
    if role != "admin":
        await callback.answer("Нет доступа", show_alert=True)
        return

    unread = get_unread_messages()
    if unread:
        text = f"📨 <b>Непрочитанные сообщения ({len(unread)}):</b>\n\n"
        text += "Выбери сообщение, чтобы прочитать и ответить."
        await callback.message.edit_text(
            text,
            parse_mode="HTML",
            reply_markup=messages_list_keyboard(unread),
        )
        await callback.answer()
        return

    all_msgs = get_all_messages(limit=10)
    if not all_msgs:
        await callback.message.edit_text("Сообщений пока нет.")
        await callback.answer()
        return

    text = "📨 <b>Непрочитанных нет. Последние сообщения:</b>\n\n"
    await callback.message.edit_text(
        text,
        parse_mode="HTML",
        reply_markup=messages_list_keyboard([
            (m[0], m[1], m[2], m[3], m[4], m[5]) for m in all_msgs
        ]),
    )
    await callback.answer()


@dp.callback_query(F.data.startswith("msg_open:"))
async def admin_message_open(callback: types.CallbackQuery):
    role = get_role(callback.from_user.username)
    if role != "admin":
        await callback.answer("Нет доступа", show_alert=True)
        return

    msg_id = int(callback.data.split(":")[1])
    msg = get_message_by_id(msg_id)
    if not msg:
        await callback.answer("Сообщение не найдено", show_alert=True)
        return

    (
        _id,
        from_user_id,
        from_username,
        from_name,
        text,
        created_at,
        is_read,
        reply_text,
        replied_at,
    ) = msg

    mark_message_read(msg_id)

    sender = from_name or from_username or f"id{from_user_id}"
    sender_username = f" (@{from_username})" if from_username else ""

    header = (
        f"📨 <b>Сообщение #{msg_id}</b>\n"
        f"От: <b>{sender}</b>{sender_username}\n"
        f"Дата: {format_datetime(created_at)}\n\n"
        f"<b>Текст:</b>\n{text}\n"
    )

    if reply_text:
        header += (
            f"\n✅ <b>Твой ответ:</b>\n{reply_text}\n"
            f"<i>Отправлен: {format_datetime(replied_at)}</i>"
        )

    await callback.message.edit_text(
        header,
        parse_mode="HTML",
        reply_markup=message_open_keyboard(msg_id),
    )
    await callback.answer()


@dp.callback_query(F.data.startswith("msg_reply:"))
async def admin_reply_start(callback: types.CallbackQuery, state: FSMContext):
    role = get_role(callback.from_user.username)
    if role != "admin":
        await callback.answer("Нет доступа", show_alert=True)
        return

    msg_id = int(callback.data.split(":")[1])
    msg = get_message_by_id(msg_id)
    if not msg:
        await callback.answer("Сообщение не найдено", show_alert=True)
        return

    await state.update_data(reply_to_msg_id=msg_id)
    await state.set_state(AdminReply.text)

    await callback.message.answer(
        f"Напиши ответ на сообщение #{msg_id}.\n\n"
        f"Или нажми «❌ Отмена».",
        reply_markup=cancel_keyboard(),
    )
    await callback.answer()


@dp.message(AdminReply.text)
async def admin_reply_send(message: types.Message, state: FSMContext):
    role = get_role(message.from_user.username)
    if role != "admin":
        await state.clear()
        return

    if message.text == "❌ Отмена":
        await state.clear()
        await message.answer("Отменено.", reply_markup=main_menu("admin"))
        return

    data = await state.get_data()
    msg_id = data.get("reply_to_msg_id")
    if not msg_id:
        await state.clear()
        await message.answer("Ошибка: не найдено сообщение для ответа.")
        return

    msg = get_message_by_id(msg_id)
    if not msg:
        await state.clear()
        await message.answer("Сообщение не найдено.")
        return

    reply_text = message.text.strip()
    from_user_id = msg[1]

    save_reply(msg_id, reply_text)

    try:
        await bot.send_message(
            from_user_id,
            f"📬 <b>Ответ от администратора:</b>\n\n{reply_text}",
            parse_mode="HTML",
        )
        delivered = True
    except (TelegramForbiddenError, TelegramBadRequest):
        delivered = False
    except Exception:
        delivered = False

    if delivered:
        await message.answer(
            f"✅ Ответ отправлен автору сообщения #{msg_id}.",
            reply_markup=main_menu("admin"),
        )
    else:
        await message.answer(
            f"⚠️ Ответ сохранён, но не доставлен (автор заблокировал бота).",
            reply_markup=main_menu("admin"),
        )
    await state.clear()


@dp.callback_query(F.data == "back_to_main_cb")
async def back_to_main_cb(callback: types.CallbackQuery):
    if not is_allowed(callback.from_user.username):
        return
    role = get_role(callback.from_user.username)
    try:
        await callback.message.delete()
    except Exception:
        pass
    await callback.message.answer(
        "Главное меню",
        reply_markup=main_menu(role),
    )
    await callback.answer()


# ---------- Меню «Добавить» ----------
@dp.message(F.text == "➕ Добавить")
async def add_menu_handler(message: types.Message, state: FSMContext):
    role = get_role(message.from_user.username)
    if role not in ("admin", "teacher"):
        return
    await state.clear()
    await message.answer("Что добавить?", reply_markup=add_menu())


# ---------- Добавить слово (2 шага) ----------
@dp.message(F.text == "📝 Добавить слово")
async def add_word_start(message: types.Message, state: FSMContext):
    role = get_role(message.from_user.username)
    if role not in ("admin", "teacher"):
        return

    await state.set_state(AddWord.word)
    await message.answer(
        "Напиши слово на английском:",
        reply_markup=cancel_keyboard(),
    )


@dp.message(AddWord.word)
async def add_word_word(message: types.Message, state: FSMContext):
    if message.text == "❌ Отмена":
        await state.clear()
        role = get_role(message.from_user.username)
        await message.answer("Отменено.", reply_markup=main_menu(role))
        return

    await state.update_data(word=message.text.strip())
    await state.set_state(AddWord.translation)
    await message.answer(
        "Теперь напиши перевод:", reply_markup=cancel_keyboard()
    )


@dp.message(AddWord.translation)
async def add_word_translation(message: types.Message, state: FSMContext):
    if message.text == "❌ Отмена":
        await state.clear()
        role = get_role(message.from_user.username)
        await message.answer("Отменено.", reply_markup=main_menu(role))
        return

    data = await state.get_data()
    translation = message.text.strip()
    word = data["word"]

    success = add_word(word, translation)
    role = get_role(message.from_user.username)

    if success:
        display = format_word(word, translation)
        await message.answer(
            f"✅ Добавлено: {display}",
            parse_mode="HTML",
            reply_markup=main_menu(role),
        )
        await broadcast_new_word(word, translation)
    else:
        await message.answer(
            f"❌ Слово «{word}» уже есть в словаре.",
            reply_markup=main_menu(role),
        )
    await state.clear()


# ---------- Удаление слов ----------
@dp.callback_query(F.data == "delete_start")
async def delete_start(callback: types.CallbackQuery):
    role = get_role(callback.from_user.username)
    if role not in ("admin", "teacher"):
        await callback.answer("Нет доступа", show_alert=True)
        return

    letters = get_letters()
    if not letters:
        await callback.answer("Словарь пуст", show_alert=True)
        return

    rows = []
    row = []
    for letter in letters:
        row.append(InlineKeyboardButton(text=letter, callback_data=f"del_letter:{letter}"))
        if len(row) == 6:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_letters")])

    await callback.message.edit_text(
        "Выбери букву слова, которое нужно удалить:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )
    await callback.answer()


@dp.callback_query(F.data.startswith("del_letter:"))
async def delete_letter(callback: types.CallbackQuery):
    role = get_role(callback.from_user.username)
    if role not in ("admin", "teacher"):
        await callback.answer("Нет доступа", show_alert=True)
        return

    letter = callback.data.split(":")[1]
    words = get_words_by_letter(letter)

    if not words:
        await callback.answer("На эту букву слов нет", show_alert=True)
        return

    await callback.message.edit_text(
        f"Выбери слово на «{letter}» для удаления:",
        reply_markup=delete_words_keyboard(words),
    )
    await callback.answer()


@dp.callback_query(F.data.startswith("del_word:"))
async def delete_word_confirm(callback: types.CallbackQuery):
    role = get_role(callback.from_user.username)
    if role not in ("admin", "teacher"):
        await callback.answer("Нет доступа", show_alert=True)
        return

    word_id = int(callback.data.split(":")[1])

    conn = get_connection()
    c = conn.cursor()
    c.execute("SELECT word, translation FROM words WHERE id = ?", (word_id,))
    row = c.fetchone()
    conn.close()

    if not row:
        await callback.answer("Слово не найдено", show_alert=True)
        return

    word, translation = row
    display = format_word(word, translation)

    await callback.message.edit_text(
        f"Удалить это слово?\n\n{display}",
        parse_mode="HTML",
        reply_markup=confirm_delete_keyboard(word_id),
    )
    await callback.answer()


@dp.callback_query(F.data.startswith("del_confirm:"))
async def delete_word_do(callback: types.CallbackQuery):
    role = get_role(callback.from_user.username)
    if role not in ("admin", "teacher"):
        await callback.answer("Нет доступа", show_alert=True)
        return

    word_id = int(callback.data.split(":")[1])
    success, word, translation = delete_word_by_id(word_id)

    if not success:
        await callback.answer("Слово уже удалено", show_alert=True)
        return

    await callback.message.edit_text(
        f"🗑 Удалено: {format_word(word, translation)}",
        parse_mode="HTML",
    )
    await callback.answer("Удалено")
    await broadcast_deleted_word(word, translation)


@dp.callback_query(F.data == "delete_back")
async def delete_back(callback: types.CallbackQuery):
    if not is_allowed(callback.from_user.username):
        return
    role = get_role(callback.from_user.username)
    letters = get_letters()

    if not letters:
        await callback.message.edit_text("Словарь пуст.")
        await callback.answer()
        return

    await callback.message.edit_text(
        "Выбери первую букву слова или нажми «Все слова»:",
        reply_markup=dictionary_keyboard(letters, role),
    )
    await callback.answer()


# ---------- Добавить ДЗ ----------
@dp.message(F.text == "📚 Добавить ДЗ")
async def add_hw_start(message: types.Message, state: FSMContext):
    role = get_role(message.from_user.username)
    if role not in ("admin", "teacher"):
        return

    await state.set_state(AddHW.text)
    await message.answer(
        "Напиши ДЗ на следующий урок и дату одним сообщением.\n"
        "Пример: <i>Упражнение 5 стр. 23, повторить слова. К 15.11</i>\n\n"
        "Или нажми «❌ Отмена».",
        parse_mode="HTML",
        reply_markup=cancel_keyboard(),
    )


@dp.message(AddHW.text)
async def add_hw_text(message: types.Message, state: FSMContext):
    if message.text == "❌ Отмена":
        await state.clear()
        role = get_role(message.from_user.username)
        await message.answer("Отменено.", reply_markup=main_menu(role))
        return

    add_homework(message.text.strip())
    role = get_role(message.from_user.username)
    await message.answer(
        "✅ ДЗ сохранено.",
        reply_markup=main_menu(role),
    )
    await state.clear()


# ---------- Диагностика (только админ) ----------
@dp.message(F.text == "🛠 Диагностика")
async def diagnostic_handler(message: types.Message, state: FSMContext):
    role = get_role(message.from_user.username)
    if role != "admin":
        return
    await state.clear()
    await message.answer(
        "Меню диагностики:",
        reply_markup=diagnostic_menu(),
    )


@dp.message(F.text == "▶️ Старт")
async def diag_start(message: types.Message, state: FSMContext):
    role = get_role(message.from_user.username)
    if role != "admin":
        return
    await state.clear()

    text = (
        "⚠️ <b>Началась тех. работа над ботом.</b>\n"
        "Не используйте бота, пока видите это сообщение последним."
    )
    await broadcast_text(text)
    await message.answer("✅ Сообщение разослано.", reply_markup=diagnostic_menu())


@dp.message(F.text == "🔄 Обновление")
async def diag_update_start(message: types.Message, state: FSMContext):
    role = get_role(message.from_user.username)
    if role != "admin":
        return

    await state.set_state(Broadcast.update_text)
    await message.answer(
        "Напиши, что нужно добавить в сообщение об обновлении:",
        reply_markup=cancel_keyboard(),
    )


@dp.message(Broadcast.update_text)
async def diag_update_send(message: types.Message, state: FSMContext):
    role = get_role(message.from_user.username)
    if role != "admin":
        await state.clear()
        return

    if message.text == "❌ Отмена":
        await state.clear()
        await message.answer("Отменено.", reply_markup=diagnostic_menu())
        return

    admin_text = message.text.strip()

    text = (
        f"🔄 <b>БОТ ОБНОВЛЕН</b>\n\n"
        f"{admin_text}\n\n"
        f"Напишите /start чтобы обновить бота"
    )
    await broadcast_text(text)
    await message.answer(
        "✅ Сообщение об обновлении разослано.",
        reply_markup=diagnostic_menu(),
    )
    await state.clear()


@dp.message(F.text == "📢 Рассылка")
async def diag_custom_start(message: types.Message, state: FSMContext):
    role = get_role(message.from_user.username)
    if role != "admin":
        return

    await state.set_state(Broadcast.custom_text)
    await message.answer(
        "Напиши текст, который нужно отправить всем пользователям.\n"
        "Можно использовать HTML: <b>жирный</b>, <i>курсив</i>, "
        "<a href=\"https://example.com\">ссылка</a>.",
        parse_mode="HTML",
        reply_markup=cancel_keyboard(),
    )


@dp.message(Broadcast.custom_text)
async def diag_custom_send(message: types.Message, state: FSMContext):
    role = get_role(message.from_user.username)
    if role != "admin":
        await state.clear()
        return

    if message.text == "❌ Отмена":
        await state.clear()
        await message.answer("Отменено.", reply_markup=diagnostic_menu())
        return

    custom_text = message.text.strip()

    text = f"📢 <b>Сообщение от администратора:</b>\n\n{custom_text}"

    await broadcast_text(text)
    await message.answer(
        "✅ Сообщение разослано всем пользователям.",
        reply_markup=diagnostic_menu(),
    )
    await state.clear()


# ---------- Пользователи (админ) ----------
@dp.message(F.text == "👥 Пользователи")
async def diag_users(message: types.Message, state: FSMContext):
    role = get_role(message.from_user.username)
    if role != "admin":
        return
    await state.clear()

    users = get_users_detailed()
    if not users:
        await message.answer(
            "Пока никто не заходил в бота.",
            reply_markup=diagnostic_menu(),
        )
        return

    lines = [f"👥 <b>Пользователи бота ({len(users)})</b>\n"]

    for user_id, username, first_name, added_at in users:
        if username:
            uname = f"@{username}"
        else:
            uname = f"id{user_id} (без username)"

        name = first_name or "—"

        user_role = get_role(username)
        role_icon = {
            "admin": "🛠",
            "teacher": "👨‍🏫",
            "student": "🎓",
        }.get(user_role, "❔")

        date_str = format_datetime(added_at)

        lines.append(f"{role_icon} <b>{uname}</b> — {name}")
        lines.append(f"   <i>{date_str}</i>")

    text = "\n".join(lines)

    if len(text) <= 4000:
        await message.answer(
            text,
            parse_mode="HTML",
            reply_markup=users_list_keyboard(),
        )
    else:
        await send_long_message(message, text, reply_markup=users_list_keyboard())


@dp.callback_query(F.data == "users_refresh")
async def users_refresh_handler(callback: types.CallbackQuery):
    role = get_role(callback.from_user.username)
    if role != "admin":
        await callback.answer("Нет доступа", show_alert=True)
        return

    await callback.answer("Обновляю ники…")
    await callback.message.edit_text(
        "🔄 Обновляю ники через Telegram…\nЭто может занять несколько секунд."
    )

    updated, errors = await refresh_usernames()

    users = get_users_detailed()
    lines = [f"👥 <b>Пользователи бота ({len(users)})</b>\n"]
    lines.append(
        f"<i>Обновлено: {updated}, ошибок: {errors}</i>\n"
    )

    for user_id, username, first_name, added_at in users:
        if username:
            uname = f"@{username}"
        else:
            uname = f"id{user_id} (без username)"

        name = first_name or "—"

        user_role = get_role(username)
        role_icon = {
            "admin": "🛠",
            "teacher": "👨‍🏫",
            "student": "🎓",
        }.get(user_role, "❔")

        date_str = format_datetime(added_at)

        lines.append(f"{role_icon} <b>{uname}</b> — {name}")
        lines.append(f"   <i>{date_str}</i>")

    text = "\n".join(lines)

    if len(text) <= 4000:
        await callback.message.edit_text(
            text,
            parse_mode="HTML",
            reply_markup=users_list_keyboard(),
        )
    else:
        try:
            await callback.message.delete()
        except Exception:
            pass
        await send_long_message(
            callback.message,
            text,
            reply_markup=users_list_keyboard(),
        )


@dp.callback_query(F.data == "diag_back")
async def diag_back(callback: types.CallbackQuery):
    role = get_role(callback.from_user.username)
    if role != "admin":
        await callback.answer("Нет доступа", show_alert=True)
        return

    try:
        await callback.message.delete()
    except Exception:
        pass

    await callback.message.answer(
        "Меню диагностики:",
        reply_markup=diagnostic_menu(),
    )
    await callback.answer()


@dp.message(F.text == "⏹ Конец")
async def diag_end(message: types.Message, state: FSMContext):
    role = get_role(message.from_user.username)
    if role != "admin":
        return
    await state.clear()

    text = (
        "✅ <b>Тех. работы завершились.</b>\n"
        "Напишите /start чтобы начать пользоваться ботом."
    )
    await broadcast_text(text)
    await message.answer("✅ Сообщение разослано.", reply_markup=diagnostic_menu())


# ---------- Назад ----------
@dp.message(F.text == "🔙 Назад")
async def back_to_main(message: types.Message, state: FSMContext):
    await state.clear()
    role = get_role(message.from_user.username)
    await message.answer("Главное меню", reply_markup=main_menu(role))


# ---------- Фоллбэк ----------
@dp.message()
async def fallback(message: types.Message, state: FSMContext):
    current_state = await state.get_state()
    if current_state is not None:
        return

    save_user(
        message.from_user.id,
        message.from_user.username,
        message.from_user.first_name,
    )

    if not is_allowed(message.from_user.username):
        return

    role = get_role(message.from_user.username)
    await message.answer(
        "Не понял команду. Используй кнопки ниже.",
        reply_markup=main_menu(role),
    )


# ====================== ЗАПУСК ======================
async def main():
    init_db()
    print("Bot started")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
