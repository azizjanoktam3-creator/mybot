import asyncio
import logging
import re
import hashlib
import sqlite3
from datetime import datetime, timedelta

from aiogram import Bot, Dispatcher, F
from aiogram.types import Message, ChatPermissions
from aiogram.enums import ChatType, ChatMemberStatus
from aiogram.filters import Command, CommandObject
from aiogram.exceptions import TelegramBadRequest
TOKEN = os.getenv("BOT_TOKEN")
DB_NAME = "bot_data.db"
ADMIN_USERNAME = "@Iamthebestperson14"

logging.basicConfig(level=logging.INFO)
bot = Bot(token=TOKEN)
dp = Dispatcher()

last_messages = {}

SPAM_PATTERNS = [
    r"https?://",
    r"t\.me/",
    r"@\w+"
]

def init_db():
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id INTEGER, user_id INTEGER, username TEXT,
            full_name TEXT, text TEXT, timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS user_stats (
            chat_id INTEGER, user_id INTEGER,
            joined_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            warn_count INTEGER DEFAULT 0, mute_count INTEGER DEFAULT 0,
            reputation INTEGER DEFAULT 0, xp INTEGER DEFAULT 0, level INTEGER DEFAULT 1,
            PRIMARY KEY (chat_id, user_id)
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS user_last_seen (
            chat_id INTEGER, user_id INTEGER,
            last_seen_time DATETIME DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (chat_id, user_id)
        )
    """)
    conn.commit()
    conn.close()
init_db()

def save_message(chat_id, user_id, username, full_name, text):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO messages (chat_id, user_id, username, full_name, text) VALUES (?, ?, ?, ?, ?)",
        (chat_id, user_id, username, full_name, text)
    )
    conn.commit()
    conn.close()

def track_user(chat_id, user_id):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO user_stats (chat_id, user_id) VALUES (?, ?)
        ON CONFLICT(chat_id, user_id) DO UPDATE SET xp = xp + 1
    """, (chat_id, user_id))
    cursor.execute("SELECT xp, level FROM user_stats WHERE chat_id = ? AND user_id = ?", (chat_id, user_id))
    result = cursor.fetchone()
    if not result:
        conn.close()
        return None
    xp, level = result
    next_level_xp = level * 20
    if xp >= next_level_xp:
        new_level = level + 1
        cursor.execute("UPDATE user_stats SET level = ?, xp = 0 WHERE chat_id = ? AND user_id = ?", (new_level, chat_id, user_id))
        conn.commit()
        conn.close()
        return new_level
    conn.commit()
    conn.close()
    return None

def get_full_profile(chat_id, user_id):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM messages WHERE chat_id = ? AND user_id = ?", (chat_id, user_id))
    msg_count = cursor.fetchone()[0]
    cursor.execute("SELECT max(timestamp) FROM messages WHERE chat_id = ? AND user_id = ?", (chat_id, user_id))
    last_active = cursor.fetchone()[0]
    cursor.execute("""
        SELECT joined_at, warn_count, mute_count, reputation, level, xp 
        FROM user_stats WHERE chat_id = ? AND user_id = ?
    """, (chat_id, user_id))
    stats = cursor.fetchone()
    conn.close()
    if not stats:
        return msg_count, last_active, "Unknown", 0, 0, 0, 1, 0
    return (msg_count, last_active) + stats

async def punish_user_dynamic(message: Message, chat_id: int, user_id: int, reason: str, is_warn_command=False):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT warn_count, mute_count FROM user_stats WHERE chat_id = ? AND user_id = ?", (chat_id, user_id))
    row = cursor.fetchone()
    if not row:
        cursor.execute("INSERT INTO user_stats (chat_id, user_id, warn_count) VALUES (?, ?, 1)", (chat_id, user_id))
        warns, mutes = 1, 0
    else:
        warns = row[0] + 1
        mutes = row[1]
        cursor.execute("UPDATE user_stats SET warn_count = ? WHERE chat_id = ? AND user_id = ?", (warns, chat_id, user_id))
    conn.commit()
    conn.close()

    if not is_warn_command:
        try:
            await message.delete()
        except TelegramBadRequest:
            pass

    if warns < 3:
        alert = f"!! {message.from_user.full_name}, {reason}! Preduprezhdenie ({warns}/3)"
        await message.answer(alert)
    else:
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        new_mutes = mutes + 1
        cursor.execute("UPDATE user_stats SET warn_count = 0, mute_count = ? WHERE chat_id = ? AND user_id = ?", (new_mutes, chat_id, user_id))
        conn.commit()
        conn.close()
        minutes = min(new_mutes * 15, 45)
        until_date = datetime.now() + timedelta(minutes=minutes)
        permissions = ChatPermissions(can_send_messages=False, can_send_media_messages=False, can_send_other_messages=False)
        try:
            await bot.restrict_chat_member(chat_id=chat_id, user_id=user_id, permissions=permissions, until_date=until_date)
            await message.answer(f"// {message.from_user.full_name} v mute na {minutes} min. Prichina: {reason}")
        except Exception as e:
            logging.error(f"Error mute: {e}")

@dp.message(Command("start"), F.chat.type == ChatType.PRIVATE)
async def cmd_start(message: Message):
    help_text = (
        f"Привет, {message.from_user.first_name}! Я твой многофункциональный бот-модератор.\n\n"
        "🛡 Активные системы защиты чата:\n"
        "• Anti-Caps: Автоматически удаляет сообщения, написанные исключительно ЗАГЛАВНЫМИ буквами.\n"
        "• Анти-Спам: Блокирует рекламу, сторонние ссылки и юзернеймы.\n"
        "• Динамический мут: За 3 предупреждения юзер получает мут. Каждое новое наказание увеличивается на +15 минут (макс. 45 минут).\n\n"
        "🎮 Система геймификации и фана:\n"
        "• При общении в чате у вас накапливается опыт (XP) и автоматически повышается уровень (Level).\n"
        "• Ответив на полезное сообщение пользователя словом + или Спасибо, вы поднимаете ему Репутацию.\n\n"
        "💬 Команды для участников (в чате группы):\n"
        "• /me — Полноценная карточка пользователя со всей статистикой (дата входа, варны, уровень, репутация).\n"
        "• /top — Таблица лидеров чата по уровню и репутации.\n"
        "• /find [текст] или /myfind [текст] — Поиск ваших прошлых сообщений в базе данных этого чата.\n\n"
        "⚙️ Команды для Администраторов:\n"
        "• /warn — Выдать предупреждение вручную (использовать как ответ на сообщение нарушителя).\n"
        "• /clean [число] — Быстро очистить указанное количество сообщений.\n"
        "• /clean @username [число] — Очистить сообщения конкретного нарушителя.\n\n"
        f"🙋‍♂️ По любым вопросам обращаться: {ADMIN_USERNAME}"
    )
    await message.answer(help_text)
@dp.message(Command("me"), F.chat.type.in_({ChatType.GROUP, ChatType.SUPERGROUP}))
async def cmd_profile(message: Message):
    user_id, chat_id = message.from_user.id, message.chat.id
    msg_count, last_active, joined_at, warns, mutes, rep, level, xp = get_full_profile(chat_id, user_id)
    profile_text = (
        f"--- КАРТОЧКА УЧАСТНИКА: {message.from_user.full_name} ---\n\n"
        f"Вход в чат: {joined_at}\n"
        f"Активность: {last_active if last_active else 'Только что'}\n"
        f"Сообщений: {msg_count}\n"
        f"Варны: {warns}/3 | Муты: {mutes}\n"
        f"Репутация: {rep} | Уровень: {level} [{xp}/{level*20} XP]"
    )
    await message.answer(profile_text)

@dp.message(Command("top"), F.chat.type.in_({ChatType.GROUP, ChatType.SUPERGROUP}))
async def cmd_top(message: Message):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT user_id, level, reputation FROM user_stats WHERE chat_id = ? ORDER BY level DESC, reputation DESC LIMIT 10", (message.chat.id,))
    rows = cursor.fetchall()
    conn.close()
    if not rows:
        await message.reply("В этом чате пока нет активных участников.")
        return
    top_text = "--- ТОП-10 УЧАСТНИКОВ ЧАТА ---\n\n"
    for idx, row in enumerate(rows, start=1):
        try:
            member = await bot.get_chat_member(message.chat.id, row[0])
            name = member.user.first_name
        except:
            name = f"ID: {row[0]}"
        top_text += f"{idx}. {name} - Лвл: {row[1]}, Реп: {row[2]}\n"
    await message.answer(top_text)

@dp.message(Command("find", "myfind"), F.chat.type.in_({ChatType.GROUP, ChatType.SUPERGROUP}))
async def cmd_find_message(message: Message, command: CommandObject):
    query = command.args
    if not query:
        await message.reply("Пример использования: /find привет")
        return
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT text, timestamp FROM messages WHERE chat_id = ? AND user_id = ? AND text LIKE ? ORDER BY timestamp DESC LIMIT 5", (message.chat.id, message.from_user.id, f"%{query}%"))
    rows = cursor.fetchall()
    conn.close()
    if not rows:
        await message.reply("Ничего не найдено.")
        return
    res = f"Результаты по запросу \"{query}\":\n\n"
    for idx, row in enumerate(rows, start=1):
        res += f"{idx}. [{row[1]}] - \"{row[0]}\"\n"
    await message.answer(res)

@dp.message(Command("warn"), F.chat.type.in_({ChatType.GROUP, ChatType.SUPERGROUP}))
async def cmd_warn(message: Message):
    try:
        m = await bot.get_chat_member(message.chat.id, message.from_user.id)
        if m.status not in [ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.CREATOR]:
            await message.reply("Доступно только админам.")
            return
    except:
        return
    if not message.reply_to_message:
        await message.reply("Ответьте на сообщение нарушителя.")
        return
    await punish_user_dynamic(message.reply_to_message, message.chat.id, message.reply_to_message.from_user.id, "warn от админа", is_warn_command=True)

@dp.message(Command("clean"), F.chat.type.in_({ChatType.GROUP, ChatType.SUPERGROUP}))
async def cmd_clean(message: Message, command: CommandObject):
    try:
        m = await bot.get_chat_member(message.chat.id, message.from_user.id)
        if m.status not in [ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.CREATOR]:
            await message.reply("Доступно только админам.")
            return
    except:
        return
    args = command.args
    count = 10
    if args:
        parts = args.split()
        if len(parts) == 1 and parts[0].isdigit():
            count = int(parts[0])
        elif len(parts) == 2 and parts[0].startswith("@") and parts[1].isdigit():
            count = int(parts[1])
    count = min(max(count, 1), 100)
    start_id = message.message_id
    deleted = 0
    for i in range(200):
        if deleted >= count: break
        try:
            await bot.delete_message(chat_id=message.chat.id, message_id=start_id - i)
            if i > 0: deleted += 1
        except:
            continue
    info = await message.answer(f"Удалено сообщений: {deleted}")
    await asyncio.sleep(3)
    try: await info.delete()
    except: pass

@dp.message(F.chat.type.in_({ChatType.GROUP, ChatType.SUPERGROUP}))
async def main_chat_handler(message: Message):
    if not message.from_user: return
    uid, cid = message.from_user.id, message.chat.id
    text = message.text or message.caption or ""

    if text and not text.startswith("/"):
        save_message(cid, uid, message.from_user.username or "", message.from_user.full_name, text)
        lvl = track_user(cid, uid)
        if lvl:
            await message.reply(f"Поздравляем! {message.from_user.full_name} поднял уровень до {lvl}!")

    if message.reply_to_message and text.strip() in ["+", "rahmat", "raxmat", "Thanks", "спасибо", "Спасибо"]:
        if message.reply_to_message.from_user.id == uid:
            await message.reply("Нельзя повышать репутацию себе! 😅")
            return
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        cursor.execute("INSERT INTO user_stats (chat_id, user_id, reputation) VALUES (?, ?, 1) ON CONFLICT(chat_id, user_id) DO UPDATE SET reputation = reputation + 1", (cid, message.reply_to_message.from_user.id))
        conn.commit()
        conn.close()
        await message.reply(f"Репутация {message.reply_to_message.from_user.first_name} повышена!")
        return

    try:
        m = await bot.get_chat_member(cid, uid)
        if m.status in [ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.CREATOR]: return
    except:
        return

    if text and len(text) > 5:
        letters = re.sub(r'[^a-zA-Zа-яА-ЯёЁ]', '', text)
        if letters and letters.isupper():
            await punish_user_dynamic(message, cid, uid, "caps lock")
            return

    c_hash = None
    if message.text:
        c_hash = hashlib.md5(text.strip().lower().encode('utf-8')).hexdigest()
    elif message.animation:
        c_hash = f"gif_{message.animation.file_unique_id}"

    if c_hash:
        user_key = (cid, uid)
        current_time = datetime.now()
        if user_key in last_messages:
            prev_hash, prev_time = last_messages[user_key]
            if prev_hash == c_hash and (current_time - prev_time).total_seconds() < 10:
                await punish_user_dynamic(message, cid, uid, "flood (retry < 10 sec)")
                return
        last_messages[user_key] = (c_hash, current_time)

    if text and any(re.search(p, text.lower()) for p in SPAM_PATTERNS):
        await punish_user_dynamic(message, cid, uid, "links / spam")

async def main():
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())