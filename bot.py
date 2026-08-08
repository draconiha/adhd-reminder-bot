import sqlite3
import datetime
import calendar
from telebot import TeleBot, types
import threading
import time
import json
import re
import logging
import sys
import os

# ========== ЗАГРУЗКА ТОКЕНА ИЗ КОНФИГА ==========
try:
    from config import BOT_TOKEN
except ImportError:
    # Если файла config.py нет, используем переменную окружения
    BOT_TOKEN = os.getenv('BOT_TOKEN')
    if not BOT_TOKEN:
        raise Exception("Токен не найден. Создайте config.py или задайте переменную окружения BOT_TOKEN")

# ========== ЛОГГИРОВАНИЕ ==========
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

bot = TeleBot(BOT_TOKEN)
ADMIN_IDS = [388016821]

user_states = {}
user_calendar_messages = {}
user_temp_data = {}

# ========== ВРЕМЯ (В UTC) ==========
def get_current_time():
    """Возвращает текущее время по Москве (UTC+3) без предупреждений."""
    # Московское время = UTC+3
    return datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None) + datetime.timedelta(hours=3)

# ========== ИНИЦИАЛИЗАЦИЯ БАЗЫ ==========
def init_db():
    conn = sqlite3.connect('tasks.db', check_same_thread=False)
    cursor = conn.cursor()
    cursor.execute('''CREATE TABLE IF NOT EXISTS tasks (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        task TEXT,
        date TEXT,
        is_done INTEGER DEFAULT 0,
        reminder_time TEXT DEFAULT '09:00',
        remind_before INTEGER DEFAULT 0,
        reminder_sent INTEGER DEFAULT 0,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )''')
    cursor.execute('''CREATE TABLE IF NOT EXISTS user_settings (
        user_id INTEGER PRIMARY KEY,
        default_reminder_time TEXT DEFAULT '09:00',
        default_remind_before INTEGER DEFAULT 0,
        theme TEXT DEFAULT 'light',
        auto_delete_done INTEGER DEFAULT 0,
        notification_type TEXT DEFAULT 'normal',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )''')
    cursor.execute('''CREATE TABLE IF NOT EXISTS recurring_tasks (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        task TEXT,
        recurrence_type TEXT,
        recurrence_days TEXT,
        reminder_time TEXT DEFAULT '09:00',
        remind_before INTEGER DEFAULT 0,
        start_date TEXT,
        end_date TEXT,
        is_active INTEGER DEFAULT 1,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )''')
    cursor.execute('''CREATE TABLE IF NOT EXISTS user_activity (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        username TEXT,
        first_name TEXT,
        action TEXT,
        timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
    )''')
    conn.commit()
    conn.close()
    logger.info("База данных инициализирована")

def add_missing_columns():
    conn = sqlite3.connect('tasks.db')
    cursor = conn.cursor()
    try:
        cursor.execute("PRAGMA table_info(user_settings)")
        columns = [column[1] for column in cursor.fetchall()]
        if 'theme' not in columns:
            cursor.execute("ALTER TABLE user_settings ADD COLUMN theme TEXT DEFAULT 'light'")
        if 'auto_delete_done' not in columns:
            cursor.execute("ALTER TABLE user_settings ADD COLUMN auto_delete_done INTEGER DEFAULT 0")
        if 'notification_type' not in columns:
            cursor.execute("ALTER TABLE user_settings ADD COLUMN notification_type TEXT DEFAULT 'normal'")
        cursor.execute("PRAGMA table_info(tasks)")
        columns = [column[1] for column in cursor.fetchall()]
        if 'reminder_sent' not in columns:
            cursor.execute("ALTER TABLE tasks ADD COLUMN reminder_sent INTEGER DEFAULT 0")
        cursor.execute("PRAGMA table_info(user_activity)")
        columns = [column[1] for column in cursor.fetchall()]
        if 'username' not in columns:
            cursor.execute("ALTER TABLE user_activity ADD COLUMN username TEXT")
        if 'first_name' not in columns:
            cursor.execute("ALTER TABLE user_activity ADD COLUMN first_name TEXT")
        conn.commit()
        logger.info("Проверка и добавление недостающих колонок завершена")
    except Exception as e:
        logger.error(f"Ошибка при добавлении колонок: {e}")
    finally:
        conn.close()

init_db()
add_missing_columns()

# ========== ЛОГГИРОВАНИЕ АКТИВНОСТИ ==========
def log_user_activity(user_id, action, username=None, first_name=None):
    conn = sqlite3.connect('tasks.db')
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO user_activity (user_id, username, first_name, action) VALUES (?, ?, ?, ?)",
        (user_id, username, first_name, action)
    )
    conn.commit()
    conn.close()
    logger.info(f"Активность: user={user_id}, username={username}, name={first_name}, action={action}")

# ========== НАСТРОЙКИ ПОЛЬЗОВАТЕЛЯ ==========
def get_user_settings(user_id):
    conn = sqlite3.connect('tasks.db')
    cursor = conn.cursor()
    cursor.execute("SELECT default_reminder_time, default_remind_before FROM user_settings WHERE user_id=?", (user_id,))
    s = cursor.fetchone()
    if not s:
        cursor.execute("INSERT INTO user_settings (user_id) VALUES (?)", (user_id,))
        conn.commit()
        s = ('09:00', 0)
    conn.close()
    return {'default_reminder_time': s[0], 'default_remind_before': s[1]}

def update_user_setting(user_id, setting_name, setting_value):
    allowed_settings = ['default_reminder_time', 'default_remind_before', 'theme', 'auto_delete_done', 'notification_type']
    if setting_name not in allowed_settings:
        return
    conn = sqlite3.connect('tasks.db')
    cursor = conn.cursor()
    cursor.execute(f"UPDATE user_settings SET {setting_name}=? WHERE user_id=?", (setting_value, user_id))
    conn.commit()
    conn.close()

# ========== ВСПОМОГАТЕЛЬНАЯ ФУНКЦИЯ ДЛЯ РАЗБИВКИ ДЛИННЫХ СООБЩЕНИЙ ==========
def split_and_send(chat_id, text, parse_mode=None, reply_markup=None, max_len=3500):
    """Разбивает длинное сообщение на части и отправляет их по очереди."""
    if len(text) <= max_len:
        bot.send_message(chat_id, text, parse_mode=parse_mode, reply_markup=reply_markup)
        return
    parts = []
    while len(text) > max_len:
        # Ищем последний перенос строки в пределах max_len
        split_at = text.rfind('\n', 0, max_len)
        if split_at == -1:
            split_at = max_len
        parts.append(text[:split_at])
        text = text[split_at:].lstrip()
    parts.append(text)
    for i, part in enumerate(parts):
        # reply_markup отправляем только с последней частью
        markup = reply_markup if i == len(parts)-1 else None
        bot.send_message(chat_id, part, parse_mode=parse_mode, reply_markup=markup)

# ========== КЛАВИАТУРЫ ==========
def create_main_keyboard():
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    markup.add('📅 Календарь', '➕ Плюс дело', '📋 Что сегодня?', '⚙️ Настройки')
    return markup

# ========== ПОВТОРЯЮЩИЕСЯ ЗАДАЧИ ==========
def add_recurring_task(user_id, task_text, recurrence_type, recurrence_days, reminder_time, remind_before, start_date, end_date=None):
    conn = sqlite3.connect('tasks.db')
    cursor = conn.cursor()
    if reminder_time == "none" or not reminder_time:
        settings = get_user_settings(user_id)
        reminder_time = settings['default_reminder_time']
    cursor.execute(
        """INSERT INTO recurring_tasks
        (user_id, task, recurrence_type, recurrence_days, reminder_time, remind_before, start_date, end_date)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (user_id, task_text, recurrence_type, json.dumps(recurrence_days), reminder_time, remind_before, start_date, end_date)
    )
    task_id = cursor.lastrowid
    conn.commit()
    conn.close()
    generate_recurring_tasks_for_user(user_id, task_id)
    return task_id

def generate_recurring_tasks_for_user(user_id, recurring_id=None):
    conn = sqlite3.connect('tasks.db')
    cursor = conn.cursor()
    if recurring_id:
        cursor.execute("SELECT * FROM recurring_tasks WHERE id = ? AND is_active = 1", (recurring_id,))
    else:
        cursor.execute("SELECT * FROM recurring_tasks WHERE user_id = ? AND is_active = 1", (user_id,))
    recurring_tasks = cursor.fetchall()
    today = get_current_time().date()
    for task in recurring_tasks:
        (task_id, user_id_db, task_text, recurrence_type, recurrence_days_json,
         reminder_time, remind_before, start_date_str, end_date_str, is_active, created_at) = task
        if user_id != user_id_db:
            continue
        try:
            recurrence_days = json.loads(recurrence_days_json)
        except (json.JSONDecodeError, TypeError):
            recurrence_days = []
        start_date = datetime.datetime.strptime(start_date_str, "%Y-%m-%d").date()
        end_date = None
        if end_date_str:
            end_date = datetime.datetime.strptime(end_date_str, "%Y-%m-%d").date()
        for i in range(60):
            current_date = today + datetime.timedelta(days=i)
            if end_date and current_date > end_date:
                continue
            should_create = False
            if recurrence_type == "daily":
                should_create = True
            elif recurrence_type == "weekdays":
                should_create = current_date.weekday() < 5
            elif recurrence_type == "weekends":
                should_create = current_date.weekday() >= 5
            elif recurrence_type == "weekly":
                should_create = current_date.weekday() in recurrence_days
            elif recurrence_type == "monthly":
                should_create = current_date.day in recurrence_days
            if should_create and current_date >= start_date:
                date_str = current_date.strftime("%Y-%m-%d")
                cursor.execute(
                    "SELECT id FROM tasks WHERE user_id = ? AND task = ? AND date = ?",
                    (user_id, task_text, date_str)
                )
                existing = cursor.fetchone()
                if not existing:
                    cursor.execute(
                        "INSERT INTO tasks (user_id, task, date, reminder_time, remind_before, reminder_sent) VALUES (?, ?, ?, ?, ?, 0)",
                        (user_id, task_text, date_str, reminder_time, remind_before)
                    )
    conn.commit()
    conn.close()

def get_recurring_tasks(user_id):
    conn = sqlite3.connect('tasks.db')
    cursor = conn.cursor()
    cursor.execute(
        "SELECT id, task, recurrence_type, recurrence_days, reminder_time, remind_before, start_date, end_date, is_active FROM recurring_tasks WHERE user_id = ? ORDER BY created_at DESC",
        (user_id,)
    )
    tasks = cursor.fetchall()
    conn.close()
    return tasks

def delete_recurring_task(task_id, user_id):
    conn = sqlite3.connect('tasks.db')
    cursor = conn.cursor()
    cursor.execute("DELETE FROM recurring_tasks WHERE id = ? AND user_id = ?", (task_id, user_id))
    conn.commit()
    conn.close()
    return True

def delete_all_recurring_tasks(user_id):
    conn = sqlite3.connect('tasks.db')
    cursor = conn.cursor()
    cursor.execute("DELETE FROM recurring_tasks WHERE user_id = ?", (user_id,))
    deleted_count = cursor.rowcount
    conn.commit()
    conn.close()
    return deleted_count
