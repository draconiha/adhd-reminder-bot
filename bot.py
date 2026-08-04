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

# ========== ЛОГИРОВАНИЕ АКТИВНОСТИ ==========
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

def create_calendar_keyboard(user_id, year=None, month=None):
    now = get_current_time()
    if year is None: year = now.year
    if month is None: month = now.month

    markup = types.InlineKeyboardMarkup(row_width=7)
    month_name = calendar.month_name[month]
    header = f"{month_name} {year}"

    prev_month = month - 1 if month > 1 else 12
    prev_year = year if month > 1 else year - 1
    next_month = month + 1 if month < 12 else 1
    next_year = year if month < 12 else year + 1

    markup.row(
        types.InlineKeyboardButton("◀️", callback_data=f"calendar_{prev_year}_{prev_month}"),
        types.InlineKeyboardButton(header, callback_data="calendar_current"),
        types.InlineKeyboardButton("▶️", callback_data=f"calendar_{next_year}_{next_month}")
    )

    week_days = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]
    markup.row(*[types.InlineKeyboardButton(day, callback_data="ignore") for day in week_days])

    cal = calendar.monthcalendar(year, month)
    for week in cal:
        row = []
        for day in week:
            if day == 0:
                row.append(types.InlineKeyboardButton(" ", callback_data="ignore"))
            else:
                date_str = f"{year}-{month:02d}-{day:02d}"
                has_tasks = len(get_tasks_by_date(user_id, date_str)) > 0
                if year == now.year and month == now.month and day == now.day:
                    text = f"[{day}] ●" if has_tasks else f"[{day}]"
                else:
                    text = f"{day} ●" if has_tasks else str(day)
                row.append(types.InlineKeyboardButton(text, callback_data=f"day_{date_str}"))
        markup.row(*row)

    today = get_current_time()
    markup.row(
        types.InlineKeyboardButton("📅 Сегодня", callback_data=f"day_{today.strftime('%Y-%m-%d')}"),
        types.InlineKeyboardButton("📋 Все делишки", callback_data="all_tasks")
    )
    return markup

def create_settings_keyboard():
    markup = types.InlineKeyboardMarkup(row_width=2)
    markup.add(
        types.InlineKeyboardButton("⏰ Время по умолчанию", callback_data="setting_default_time"),
        types.InlineKeyboardButton("⏱️ Напоминать заранее", callback_data="setting_default_before"),
        types.InlineKeyboardButton("🔁 Управление повторяющимися", callback_data="setting_recurring"),
        types.InlineKeyboardButton("📊 Статистика", callback_data="setting_stats"),
        types.InlineKeyboardButton("🏠 В меню", callback_data="main_menu")
    )
    return markup

def create_default_time_keyboard(user_id):
    settings = get_user_settings(user_id)
    default = settings['default_reminder_time']
    times = ["09:00", "10:00", "11:00", "12:00", "13:00", "14:00", "15:00", "16:00", "17:00", "18:00", "19:00", "20:00"]
    markup = types.InlineKeyboardMarkup(row_width=3)
    for i in range(0, len(times), 3):
        row = []
        for t in times[i:i+3]:
            emoji = "✅" if t == default else "🕘"
            row.append(types.InlineKeyboardButton(f"{emoji} {t}", callback_data=f"dtime_{t}"))
        markup.row(*row)
    markup.row(types.InlineKeyboardButton("◀️ Назад", callback_data="back_settings"))
    return markup

def create_default_before_keyboard(user_id):
    settings = get_user_settings(user_id)
    default = settings['default_remind_before']
    options = [("0", "Не напоминать"), ("5", "5 мин"), ("15", "15 мин"), ("30", "30 мин"), ("60", "1 час"), ("120", "2 часа"), ("1440", "За день")]
    markup = types.InlineKeyboardMarkup(row_width=2)
    for value, text in options:
        emoji = "✅" if int(value) == default else "⏱️"
        markup.add(types.InlineKeyboardButton(f"{emoji}{text}", callback_data=f"dbefore_{value}"))
    markup.row(types.InlineKeyboardButton("◀️ Назад", callback_data="back_settings"))
    return markup

def create_recurring_management_keyboard():
    markup = types.InlineKeyboardMarkup(row_width=2)
    markup.add(
        types.InlineKeyboardButton("📋 Список повторяющихся дел", callback_data="recurring_list"),
        types.InlineKeyboardButton("🗑️ Удалить все", callback_data="recurring_delete_all_ask"),
        types.InlineKeyboardButton("◀️ Назад", callback_data="back_settings")
    )
    return markup

def create_recurring_keyboard():
    markup = types.InlineKeyboardMarkup(row_width=2)
    markup.add(
        types.InlineKeyboardButton("📅 Каждый день", callback_data="type_daily"),
        types.InlineKeyboardButton("🏢 По будням", callback_data="type_weekdays"),
        types.InlineKeyboardButton("🎉 По выходным", callback_data="type_weekends"),
        types.InlineKeyboardButton("📆 Каждую неделю", callback_data="type_weekly"),
        types.InlineKeyboardButton("🗓️ Каждый месяц", callback_data="type_monthly"),
        types.InlineKeyboardButton("❌ Без повтора", callback_data="type_none"),
        types.InlineKeyboardButton("◀️ Отмена", callback_data="type_cancel")
    )
    return markup

def create_days_of_week_keyboard(selected_days=None):
    if selected_days is None:
        selected_days = []
    markup = types.InlineKeyboardMarkup(row_width=3)
    days = [
        ("Пн", "mon"), ("Вт", "tue"), ("Ср", "wed"),
        ("Чт", "thu"), ("Пт", "fri"), ("Сб", "sat"), ("Вс", "sun")
    ]
    for day_name, day_code in days:
        emoji = "✅" if day_code in selected_days else ""
        markup.add(types.InlineKeyboardButton(f"{emoji}{day_name}", callback_data=f"weekday_{day_code}"))
    markup.row(
        types.InlineKeyboardButton("✅ Готово", callback_data="weekdays_done"),
        types.InlineKeyboardButton("❌ Отмена", callback_data="type_cancel")
    )
    return markup

def create_reminder_time_keyboard(user_id=None):
    if user_id:
        settings = get_user_settings(user_id)
        default = settings['default_reminder_time']
    else:
        default = "09:00"
    times = ["09:00","10:00","11:00","12:00","13:00","14:00","15:00","16:00","17:00","18:00","19:00","20:00","21:00","22:00","23:00"]
    markup = types.InlineKeyboardMarkup(row_width=3)
    for i in range(0, len(times), 3):
        row = []
        for t in times[i:i+3]:
            emoji = "⏰" if t == default else "🕘"
            row.append(types.InlineKeyboardButton(f"{emoji} {t}", callback_data=f"time_{t}"))
        markup.row(*row)
    markup.row(
        types.InlineKeyboardButton("❌ Без напоминания", callback_data="time_none"),
        types.InlineKeyboardButton("◀️ Отмена", callback_data="time_cancel")
    )
    return markup

def create_remind_before_keyboard(user_id=None):
    if user_id:
        settings = get_user_settings(user_id)
        default = settings['default_remind_before']
    else:
        default = 0
    options = [("5","5 мин"),("15","15 мин"),("30","30 мин"),("60","1 час"),("120","2 часа"),("1440","За день")]
    markup = types.InlineKeyboardMarkup(row_width=2)
    for value, text in options:
        emoji = "⏱️" if int(value) == default else ""
        markup.add(types.InlineKeyboardButton(f"{emoji}{text}", callback_data=f"before_{value}"))
    markup.add(
        types.InlineKeyboardButton("❌ Не напоминать заранее", callback_data="before_none"),
        types.InlineKeyboardButton("◀️ Отмена", callback_data="before_cancel")
    )
    return markup

def create_stats_keyboard():
    markup = types.InlineKeyboardMarkup(row_width=2)
    markup.add(
        types.InlineKeyboardButton("📈 За сегодня", callback_data="stats_today"),
        types.InlineKeyboardButton("📊 За неделю", callback_data="stats_week"),
        types.InlineKeyboardButton("📉 За месяц", callback_data="stats_month"),
        types.InlineKeyboardButton("📋 Все время", callback_data="stats_all"),
        types.InlineKeyboardButton("◀️ Назад", callback_data="back_settings")
    )
    return markup

def create_stats_choice_keyboard():
    markup = types.InlineKeyboardMarkup(row_width=2)
    markup.add(
        types.InlineKeyboardButton("📊 Краткая статистика", callback_data="admin_stats_short"),
        types.InlineKeyboardButton("📋 Подробная статистика (с именами)", callback_data="admin_stats_detailed")
    )
    return markup

def create_recurring_list_keyboard(tasks, page=0, tasks_per_page=5):
    markup = types.InlineKeyboardMarkup(row_width=1)
    start_idx = page * tasks_per_page
    end_idx = start_idx + tasks_per_page
    current_tasks = tasks[start_idx:end_idx]
    for task in current_tasks:
        (task_id, task_text, recurrence_type, recurrence_days_json,
         reminder_time, remind_before, start_date, end_date, is_active) = task
        short_text = task_text[:30] + "..." if len(task_text) > 30 else task_text
        markup.add(types.InlineKeyboardButton(f"🗑️ {short_text}", callback_data=f"delete_recurring_{task_id}"))
    navigation_buttons = []
    if page > 0:
        navigation_buttons.append(types.InlineKeyboardButton("◀️ Назад", callback_data=f"recurring_page_{page-1}"))
    if end_idx < len(tasks):
        navigation_buttons.append(types.InlineKeyboardButton("Вперед ▶️", callback_data=f"recurring_page_{page+1}"))
    if navigation_buttons:
        markup.row(*navigation_buttons)
    markup.row(
        types.InlineKeyboardButton("🗑️ Удалить все", callback_data="recurring_delete_all_ask"),
        types.InlineKeyboardButton("◀️ Назад к управлению", callback_data="recurring_manage")
    )
    return markup

def create_confirm_delete_all_recurring_keyboard():
    markup = types.InlineKeyboardMarkup(row_width=2)
    markup.add(
        types.InlineKeyboardButton("✅ Да, удалить всё", callback_data="recurring_delete_all_confirm"),
        types.InlineKeyboardButton("❌ Нет, оставить", callback_data="recurring_manage")
    )
    return markup

# ========== РАБОТА С БАЗОЙ ==========
def get_tasks_by_date(user_id, date_str):
    conn = sqlite3.connect('tasks.db')
    cursor = conn.cursor()
    cursor.execute("SELECT id, task, is_done, reminder_time, remind_before FROM tasks WHERE user_id=? AND date=? ORDER BY reminder_time", (user_id, date_str))
    tasks = cursor.fetchall()
    conn.close()
    return tasks

def add_task_to_db(user_id, task_text, date_str, reminder_time="09:00", remind_before=0):
    conn = sqlite3.connect('tasks.db')
    cursor = conn.cursor()
    if reminder_time == "none" or not reminder_time:
        settings = get_user_settings(user_id)
        reminder_time = settings['default_reminder_time']
    cursor.execute("INSERT INTO tasks (user_id, task, date, reminder_time, remind_before) VALUES (?,?,?,?,?)",
                   (user_id, task_text, date_str, reminder_time, remind_before))
    task_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return task_id

def get_task_by_id(task_id):
    conn = sqlite3.connect('tasks.db')
    cursor = conn.cursor()
    cursor.execute("SELECT user_id, task, date, is_done, reminder_time, remind_before FROM tasks WHERE id=?", (task_id,))
    task = cursor.fetchone()
    conn.close()
    return task

def mark_task_done(task_id, user_id):
    conn = sqlite3.connect('tasks.db')
    cursor = conn.cursor()
    cursor.execute("UPDATE tasks SET is_done=1 WHERE id=? AND user_id=?", (task_id, user_id))
    conn.commit()
    conn.close()

def delete_task(task_id, user_id):
    conn = sqlite3.connect('tasks.db')
    cursor = conn.cursor()
    cursor.execute("DELETE FROM tasks WHERE id=? AND user_id=?", (task_id, user_id))
    conn.commit()
    conn.close()

def clear_day(user_id, date_str):
    conn = sqlite3.connect('tasks.db')
    cursor = conn.cursor()
    cursor.execute("DELETE FROM tasks WHERE user_id=? AND date=?", (user_id, date_str))
    conn.commit()
    conn.close()

def format_date(date_str):
    try:
        d = datetime.datetime.strptime(date_str, '%Y-%m-%d').date()
        today = get_current_time().date()
        if d == today: return "сегодня"
        if d == today + datetime.timedelta(days=1): return "завтра"
        if d == today - datetime.timedelta(days=1): return "вчера"
        months = {1:'января',2:'февраля',3:'марта',4:'апреля',5:'мая',6:'июня',
                  7:'июля',8:'августа',9:'сентября',10:'октября',11:'ноября',12:'декабря'}
        return f"{d.day} {months[d.month]} {d.year if d.year!=today.year else ''}".strip()
   except ValueError:
    return date_str

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
            break
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

# ========== НАПОМИНАНИЯ ==========
def check_reminders():
    while True:
        try:
            now = get_current_time()
            current_date = now.strftime('%Y-%m-%d')
            tomorrow_date = (now + datetime.timedelta(days=1)).strftime('%Y-%m-%d')
            current_time = now.strftime('%H:%M')

            conn = sqlite3.connect('tasks.db')
            cursor = conn.cursor()

            # Задачи на сегодня
            cursor.execute("""
                SELECT id, user_id, task, reminder_time, remind_before
                FROM tasks
                WHERE date = ? AND is_done = 0 AND reminder_sent = 0
                AND reminder_time IS NOT NULL AND reminder_time != 'None'
            """, (current_date,))
            tasks_today = cursor.fetchall()

            # Задачи на завтра с remind_before = 1440 (за день)
            cursor.execute("""
                SELECT id, user_id, task, reminder_time, remind_before
                FROM tasks
                WHERE date = ? AND is_done = 0 AND reminder_sent = 0
                AND remind_before = 1440
                AND reminder_time IS NOT NULL AND reminder_time != 'None'
            """, (tomorrow_date,))
            tasks_tomorrow = cursor.fetchall()

            tasks = tasks_today + tasks_tomorrow

            for task_id, user_id, task_text, reminder_time, remind_before in tasks:
                try:
                    # Для задач на завтра время напоминания считается от сегодняшнего дня
                    if remind_before == 1440:
                        reminder_datetime = datetime.datetime.strptime(f"{current_date} {reminder_time}", "%Y-%m-%d %H:%M")
                    else:
                        reminder_datetime = datetime.datetime.strptime(f"{current_date} {reminder_time}", "%Y-%m-%d %H:%M")
                        if remind_before > 0:
                            reminder_datetime = reminder_datetime - datetime.timedelta(minutes=remind_before)

                    if now >= reminder_datetime:
                        if remind_before > 0:
                            if remind_before < 60:
                                text = f"⏰ <b>Скоро дело!</b>\n\n{task_text}\n\nЧерез {remind_before} минут ({reminder_time})"
                            elif remind_before == 60:
                                text = f"⏰ <b>Скоро дело!</b>\n\n{task_text}\n\nЧерез 1 час ({reminder_time})"
                            elif remind_before == 120:
                                text = f"⏰ <b>Скоро дело!</b>\n\n{task_text}\n\nЧерез 2 часа ({reminder_time})"
                            elif remind_before == 1440:
                                text = f"⏰ <b>Скоро дело!</b>\n\n{task_text}\n\nЗавтра в {reminder_time}"
                        else:
                            text = f"⏰ <b>Пора делать!</b>\n\n{task_text}\n\nСейчас время: {reminder_time}"

                        bot.send_message(user_id, text, parse_mode='HTML')
                        logger.info(f"Отправлено уведомление для задачи {task_id} пользователю {user_id}")

                        cursor.execute("UPDATE tasks SET reminder_sent = 1 WHERE id = ?", (task_id,))
                        conn.commit()
                except Exception as e:
                    logger.error(f"Ошибка при обработке уведомления для задачи {task_id}: {e}")

            conn.close()
            time.sleep(30)
        except Exception as e:
            logger.error(f"Ошибка в системе уведомлений: {e}")
            time.sleep(60)

def reset_daily_reminders():
    while True:
        try:
            now = get_current_time()
            current_time = now.strftime('%H:%M')
            today_str = now.strftime('%Y-%m-%d')

            if current_time == '00:00':
                conn = sqlite3.connect('tasks.db')
                cursor = conn.cursor()

                # Сбрасываем reminder_sent только для задач на сегодня
                cursor.execute("UPDATE tasks SET reminder_sent = 0 WHERE date = ?", (today_str,))
                conn.commit()

                # Генерируем повторяющиеся задачи на новый день
                cursor.execute("SELECT DISTINCT user_id FROM recurring_tasks WHERE is_active = 1")
                users = cursor.fetchall()
                for (uid,) in users:
                    generate_recurring_tasks_for_user(uid)
                conn.close()
                logger.info("Сброшены статусы уведомлений и сгенерированы повторяющиеся задачи")
                time.sleep(60)
            else:
                time.sleep(30)
        except Exception as e:
            logger.error(f"Ошибка в функции сброса уведомлений: {e}")
            time.sleep(60)

# ========== СТАТИСТИКА ПОЛЬЗОВАТЕЛЯ ==========
def show_user_stats(user_id, message_id=None):
    conn = sqlite3.connect('tasks.db')
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM tasks WHERE user_id=?", (user_id,))
    total_tasks = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(*) FROM tasks WHERE user_id=? AND is_done=1", (user_id,))
    done_tasks = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(*) FROM recurring_tasks WHERE user_id=?", (user_id,))
    recurring_tasks = cursor.fetchone()[0]
    today = get_current_time().strftime('%Y-%m-%d')
    cursor.execute("SELECT COUNT(*) FROM tasks WHERE user_id=? AND date=?", (user_id, today))
    today_tasks = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(*) FROM tasks WHERE user_id=? AND date=? AND is_done=1", (user_id, today))
    today_done = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(DISTINCT date) FROM tasks WHERE user_id=?", (user_id,))
    days_count = cursor.fetchone()[0]
    avg_per_day = total_tasks / max(days_count, 1)
    conn.close()

    if total_tasks > 0:
        completion_rate = (done_tasks / total_tasks) * 100
        today_rate = (today_done / max(today_tasks, 1)) * 100 if today_tasks > 0 else 0
    else:
        completion_rate = 0
        today_rate = 0

    text = f"""
📊 <b>Твоя статистика:</b>

📈 <b>Общая статистика:</b>
• Всего задач: {total_tasks}
• Выполнено: {done_tasks} ({completion_rate:.1f}%)
• Повторяющихся: {recurring_tasks}
• В среднем в день: {avg_per_day:.1f} задач

📅 <b>Сегодня:</b>
• Задач на сегодня: {today_tasks}
• Выполнено: {today_done} ({today_rate:.1f}%)

🎯 <b>Рекомендации:</b>
"""
    if completion_rate < 50:
        text += "• Старайся выполнять хотя бы половину запланированного!\n"
    if avg_per_day > 10:
        text += "• Может, стоит сократить количество задач в день?\n"
    if today_rate == 100 and today_tasks > 0:
        text += "• Отлично! Ты выполнил все задачи на сегодня! 🎉\n"

    markup = create_stats_keyboard()
    if message_id:
        try:
            bot.edit_message_text(text, user_id, message_id, parse_mode='HTML', reply_markup=markup)
        except Exception as e:
            if "message is not modified" not in str(e):
                bot.send_message(user_id, text, parse_mode='HTML', reply_markup=markup)
    else:
        bot.send_message(user_id, text, parse_mode='HTML', reply_markup=markup)

def show_today_stats(user_id, message_id):
    today = get_current_time().strftime('%Y-%m-%d')
    conn = sqlite3.connect('tasks.db')
    cursor = conn.cursor()
    cursor.execute("SELECT task, reminder_time, remind_before, is_done FROM tasks WHERE user_id=? AND date=?", (user_id, today))
    tasks = cursor.fetchall()
    conn.close()
    if not tasks:
        text = "📅 <b>Сегодня нет задач!</b>\n\nМожно отдохнуть или добавить новые дела 😊"
    else:
        total = len(tasks)
        done = sum(1 for t in tasks if t[3] == 1)
        rate = (done / total * 100) if total else 0
        text = f"""
📅 <b>Статистика на сегодня:</b>

• Всего задач: {total}
• Выполнено: {done} ({rate:.1f}%)
• Осталось: {total - done}

📋 <b>Список задач:</b>
"""
        for task, rtime, rbefore, done_flag in tasks:
            status = "✅" if done_flag else "⏳"
            time_info = f" ({rtime})" if rtime and rtime != 'None' else ""
            if rbefore and not done_flag:
                if rbefore < 60:
                    time_info += f" ⏰ за {rbefore} мин"
                elif rbefore == 60:
                    time_info += " ⏰ за 1 час"
                elif rbefore == 120:
                    time_info += " ⏰ за 2 часа"
                elif rbefore == 1440:
                    time_info += " ⏰ за день"
            text += f"{status} {task}{time_info}\n"
    markup = create_stats_keyboard()
    try:
        bot.edit_message_text(text, user_id, message_id, parse_mode='HTML', reply_markup=markup)
    except Exception as e:
        if "message is not modified" not in str(e):
            bot.send_message(user_id, text, parse_mode='HTML', reply_markup=markup)

def show_week_stats(user_id, message_id):
    now = get_current_time()
    week_ago = now - datetime.timedelta(days=7)
    start = week_ago.strftime('%Y-%m-%d')
    end = now.strftime('%Y-%m-%d')
    conn = sqlite3.connect('tasks.db')
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM tasks WHERE user_id=? AND date BETWEEN ? AND ?", (user_id, start, end))
    total = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(*) FROM tasks WHERE user_id=? AND date BETWEEN ? AND ? AND is_done=1", (user_id, start, end))
    done = cursor.fetchone()[0]
    cursor.execute("SELECT date, COUNT(*) FROM tasks WHERE user_id=? AND date BETWEEN ? AND ? AND is_done=1 GROUP BY date ORDER BY COUNT(*) DESC LIMIT 1", (user_id, start, end))
    best = cursor.fetchone()
    conn.close()
    rate = (done / total * 100) if total else 0
    text = f"""
📊 <b>Статистика за неделю:</b>

• Всего задач: {total}
• Выполнено: {done} ({rate:.1f}%)
• Среднее в день: {total/7:.1f} задач
"""
    if best:
        best_date = format_date(best[0])
        text += f"• Самый продуктивный день: {best_date} ({best[1]} задач)\n"
    if rate > 70:
        text += "\n🎉 <b>Отличная неделя! Так держать!</b>"
    elif rate > 50:
        text += "\n👍 <b>Хорошая неделя! Можно ещё лучше!</b>"
    else:
        text += "\n💪 <b>Не сдавайся! На следующей неделе будет лучше!</b>"
    markup = create_stats_keyboard()
    try:
        bot.edit_message_text(text, user_id, message_id, parse_mode='HTML', reply_markup=markup)
    except Exception as e:
        if "message is not modified" not in str(e):
            bot.send_message(user_id, text, parse_mode='HTML', reply_markup=markup)

def show_month_stats(user_id, message_id):
    now = get_current_time()
    month_ago = now - datetime.timedelta(days=30)
    start = month_ago.strftime('%Y-%m-%d')
    end = now.strftime('%Y-%m-%d')
    conn = sqlite3.connect('tasks.db')
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM tasks WHERE user_id=? AND date BETWEEN ? AND ?", (user_id, start, end))
    total = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(*) FROM tasks WHERE user_id=? AND date BETWEEN ? AND ? AND is_done=1", (user_id, start, end))
    done = cursor.fetchone()[0]
    conn.close()
    rate = (done / total * 100) if total else 0
    text = f"""
📉 <b>Статистика за месяц:</b>

• Всего задач: {total}
• Выполнено: {done} ({rate:.1f}%)
• Среднее в день: {total/30:.1f} задач
• Средняя производительность: {rate:.1f}%

"""
    if rate > 80:
        text += "🏆 <b>Ты просто машина продуктивности! Так держать!</b>"
    elif rate > 60:
        text += "🌟 <b>Отличный месяц! Ты на правильном пути!</b>"
    elif rate > 40:
        text += "👍 <b>Хороший месяц! Есть куда расти!</b>"
    else:
        text += "💪 <b>Не сдавайся! Каждый маленький шаг важен!</b>"
    markup = create_stats_keyboard()
    try:
        bot.edit_message_text(text, user_id, message_id, parse_mode='HTML', reply_markup=markup)
    except Exception as e:
        if "message is not modified" not in str(e):
            bot.send_message(user_id, text, parse_mode='HTML', reply_markup=markup)

# ========== ОТЧЁТЫ ОБ АКТИВНОСТИ (АДМИНСКИЕ) ==========
def send_activity_report(target_user_id=None):
    conn = sqlite3.connect('tasks.db')
    cursor = conn.cursor()
    cursor.execute("""
        SELECT COUNT(DISTINCT user_id) 
        FROM user_activity 
        WHERE timestamp >= datetime('now', '-1 day')
    """)
    daily_users = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(DISTINCT user_id) FROM user_activity")
    total_users = cursor.fetchone()[0]
    cursor.execute("""
        SELECT COUNT(*) 
        FROM user_activity 
        WHERE action='start' AND timestamp >= datetime('now', '-1 day')
    """)
    daily_starts = cursor.fetchone()[0]
    cursor.execute("""
        SELECT user_id, username, first_name, action, timestamp 
        FROM user_activity 
        ORDER BY timestamp DESC LIMIT 10
    """)
    recent = cursor.fetchall()
    conn.close()
    text = f"📊 <b>Краткий отчёт по активности</b>\n\n"
    text += f"👥 Уникальных пользователей за сутки: {daily_users}\n"
    text += f"👤 Всего уникальных: {total_users}\n"
    text += f"🚀 Запусков /start за сутки: {daily_starts}\n\n"
    text += "<b>Последние 10 действий:</b>\n"
    for uid, uname, fname, act, ts in recent:
        name_part = f"{fname or ''} (@{uname})" if uname else (fname or str(uid))
        text += f"• {ts} — {name_part}: {act}\n"
    if target_user_id:
        bot.send_message(target_user_id, text, parse_mode='HTML')
    else:
        for admin in ADMIN_IDS:
            try:
                bot.send_message(admin, text, parse_mode='HTML')
           except Exception as e:
    logger.error(f"Не удалось отправить отчёт админу {admin}: {e}")

def send_detailed_activity_report(target_user_id):
    conn = sqlite3.connect('tasks.db')
    cursor = conn.cursor()
    cursor.execute("""
        SELECT user_id, username, first_name, action, timestamp 
        FROM user_activity a1
        WHERE timestamp = (
            SELECT MAX(timestamp) 
            FROM user_activity a2 
            WHERE a2.user_id = a1.user_id
        )
        ORDER BY timestamp DESC
    """)
    users = cursor.fetchall()
    conn.close()
    if not users:
        text = "📭 Нет данных об активности."
    else:
        text = f"📋 <b>Подробный отчёт (всего пользователей: {len(users)})</b>\n\n"
        for uid, uname, fname, act, ts in users:
            name_part = f"{fname or ''} (@{uname})" if uname else (fname or "без имени")
            text += f"• <b>{uid}</b> — {name_part}\n  Последнее действие: {act} ({ts})\n\n"
    # Разбиваем, если очень длинный
    split_and_send(target_user_id, text, parse_mode='HTML')

def send_users_with_tasks(admin_id):
    conn = sqlite3.connect('tasks.db')
    cursor = conn.cursor()
    cursor.execute("SELECT DISTINCT user_id FROM tasks ORDER BY user_id")
    users = cursor.fetchall()
    conn.close()
    if not users:
        bot.send_message(admin_id, "📭 Нет пользователей с задачами.")
        return
    text = "👥 <b>Пользователи с делами:</b>\n\n"
    user_list = [str(u[0]) for u in users]
    chunk_size = 50
    for i in range(0, len(user_list), chunk_size):
        chunk = "\n".join(user_list[i:i+chunk_size])
        bot.send_message(admin_id, f"<code>{chunk}</code>", parse_mode='HTML')
    bot.send_message(admin_id, f"Всего: {len(users)} пользователей.")

def daily_report_worker():
    while True:
        now = get_current_time()
        # Проверяем 22:00 UTC с точностью до 10 секунд
        if now.hour == 22 and now.minute == 0 and now.second < 10:
            send_activity_report()
            time.sleep(60)
        time.sleep(30)

# ========== ОСНОВНЫЕ ФУНКЦИИ ОТОБРАЖЕНИЯ ==========
def show_calendar(user_id, edit_message_id=None):
    text = "📅 Выберите день:"
    markup = create_calendar_keyboard(user_id)
    if edit_message_id:
        bot.edit_message_text(text, user_id, edit_message_id, reply_markup=markup)
    else:
        msg = bot.send_message(user_id, text, reply_markup=markup)
        user_calendar_messages[user_id] = msg.message_id

def show_day_tasks(user_id, date_str, edit_message_id=None):
    tasks = get_tasks_by_date(user_id, date_str)
    formatted = format_date(date_str)
    now = get_current_time().strftime('%H:%M')
    if tasks:
        text = f"📌 <b>Делишки на {formatted}:</b>\n"
        text += f"⏰ <i>Текущее время: {now}</i>\n\n"
        for tid, task, done, rtime, rbefore in tasks:
            if done:
                text += f"<s>{task}</s> ✅\n"
            else:
                time_info = f" ({rtime})" if rtime and rtime != 'None' else ""
                if rbefore:
                    if rbefore < 60:
                        time_info += f" ⏰ за {rbefore} мин"
                    elif rbefore == 60:
                        time_info += " ⏰ за 1 час"
                    elif rbefore == 120:
                        time_info += " ⏰ за 2 часа"
                    elif rbefore == 1440:
                        time_info += " ⏰ за день"
                text += f"• {task}{time_info}\n"
    else:
        text = f"📭 На {formatted} чисто!\n⏰ <i>Текущее время: {now}</i>"

    markup = types.InlineKeyboardMarkup(row_width=1)
    for tid, task, done, rtime, rbefore in tasks:
        if not done:
            short = task[:20] + "..." if len(task)>20 else task
            markup.add(types.InlineKeyboardButton(f"🔧 {short}", callback_data=f"task_{tid}"))
    markup.row(
        types.InlineKeyboardButton("➕ Плюс дело", callback_data=f"add_{date_str}"),
        types.InlineKeyboardButton("🔄 Повтор", callback_data=f"recur_{date_str}"),
        types.InlineKeyboardButton("🗑️ Минус вайб", callback_data=f"clear_ask_{date_str}")
    )
    markup.row(
        types.InlineKeyboardButton("◀️ Назад к календарю", callback_data="back_calendar"),
        types.InlineKeyboardButton("🏠 В меню", callback_data="main_menu")
    )

    if edit_message_id:
        try:
            bot.edit_message_text(text, user_id, edit_message_id, parse_mode='HTML', reply_markup=markup)
        except Exception as e:
            logger.error(f"Ошибка при изменении сообщения: {e}")
            bot.send_message(user_id, text, parse_mode='HTML', reply_markup=markup)
    else:
        bot.send_message(user_id, text, parse_mode='HTML', reply_markup=markup)

def show_today_tasks(user_id):
    today = get_current_time().strftime('%Y-%m-%d')
    show_day_tasks(user_id, today)

def show_task_details(user_id, task_id, message_id=None):
    task = get_task_by_id(task_id)
    if not task:
        if message_id:
            bot.edit_message_text("❌ Задача не найдена", user_id, message_id)
        else:
            bot.send_message(user_id, "❌ Задача не найдена")
        return
    uid, task_text, date_str, is_done, reminder_time, remind_before = task
    if uid != user_id:
        if message_id:
            bot.edit_message_text("❌ Ошибка доступа", user_id, message_id)
        else:
            bot.send_message(user_id, "❌ Ошибка доступа")
        return
    status = "✅ Выполнена" if is_done else "🕐 В процессе"
    reminder = f"⏰ {reminder_time}" if reminder_time and reminder_time != 'None' else ""
    if remind_before:
        if remind_before < 60:
            reminder += f" (за {remind_before} мин)"
        elif remind_before == 60:
            reminder += " (за 1 час)"
        elif remind_before == 120:
            reminder += " (за 2 часа)"
        elif remind_before == 1440:
            reminder += " (за день)"
    markup = types.InlineKeyboardMarkup(row_width=2)
    if not is_done:
        markup.add(types.InlineKeyboardButton("✅ Выполнить", callback_data=f"done_{task_id}"))
    markup.add(types.InlineKeyboardButton("🗑️ Удалить", callback_data=f"delete_one_{task_id}"))
    markup.add(types.InlineKeyboardButton("📅 Перенести", callback_data=f"move_{task_id}"))
    markup.add(types.InlineKeyboardButton("◀️ Назад", callback_data=f"day_{date_str}"))
    full_text = f"📝 <b>{task_text}</b>\n\n📅 {format_date(date_str)}\n{reminder}\n📊 {status}"
    if message_id:
        try:
            bot.edit_message_text(full_text, user_id, message_id, parse_mode='HTML', reply_markup=markup)
     except Exception as e:
logger.error(f"Ошибка при изменении сообщения: {e}")
            bot.send_message(user_id, full_text, parse_mode='HTML', reply_markup=markup)
    else:
        bot.send_message(user_id, full_text, parse_mode='HTML', reply_markup=markup)

# ========== ОБРАБОТЧИКИ КОМАНД ==========
@bot.message_handler(commands=['start'])
def start_command(message):
    user_id = message.chat.id
    username = message.from_user.username
    first_name = message.from_user.first_name
    log_user_activity(user_id, "start", username, first_name)
    bot.send_message(message.chat.id,
                     "👋 Привет! Я твоя СДВГ-напоминалка!\n\n"
                     "📅 <b>Календарь</b> — просмотр дел по дням\n"
                     "➕ <b>Плюс дело</b> — быстро добавить дело на сегодня\n"
                     "📋 <b>Что сегодня?</b> — список дел на сегодня\n"
                     "⚙️ <b>Настройки</b> — изменить время напоминаний\n\n"
                     "<b>✨ Новые функции:</b>\n"
                     "• <b>Повторяющиеся дела</b> — создай дело один раз, и оно будет появляться каждый день, по будням или в выбранные дни\n"
                     "• <b>Настройки</b> — выбери время напоминаний по умолчанию и интервал предупреждения\n"
                     "• <b>Статистика</b> — смотри, сколько дел выполнено за день, неделю или месяц\n\n"
                     "Бот стал ещё удобнее, пользуйтесь с удовольствием! 😊",
                     parse_mode='HTML',
                     reply_markup=create_main_keyboard())

@bot.message_handler(commands=['команды'])
def commands_help(message):
    user_id = message.chat.id
    if user_id not in ADMIN_IDS:
        return
    text = (
        "🔹 <b>Команды администратора:</b>\n\n"
        "/рассылка текст — отправить сообщение всем пользователям\n"
        "/статистика — показать статистику использования (с кнопками)\n"
        "/пользователи — список ID всех пользователей, у которых есть дела\n"
        "/broadcast текст — то же, что и /рассылка\n"
        "/stats — то же, что и /статистика\n"
    )
    bot.send_message(user_id, text, parse_mode='HTML')

@bot.message_handler(commands=['broadcast', 'рассылка'])
def broadcast_command(message):
    user_id = message.chat.id
    if user_id not in ADMIN_IDS:
        return
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        bot.send_message(user_id, "❌ Напиши сообщение после команды.\nПример: /рассылка Всем привет!")
        return
    text = parts[1]
    bot.delete_message(user_id, message.message_id)
    bot.send_message(user_id, f"📢 Начинаю рассылку:\n\n{text}")

    conn = sqlite3.connect('tasks.db')
    cursor = conn.cursor()
    users = set()
    cursor.execute("SELECT DISTINCT user_id FROM tasks")
    for (uid,) in cursor.fetchall(): users.add(uid)
    cursor.execute("SELECT DISTINCT user_id FROM recurring_tasks")
    for (uid,) in cursor.fetchall(): users.add(uid)
    cursor.execute("SELECT DISTINCT user_id FROM user_settings")
    for (uid,) in cursor.fetchall(): users.add(uid)
    conn.close()

    sent_ids = []
    failed_ids = []
    for uid in users:
        if uid == user_id: continue
        try:
            bot.send_message(uid, f"📢 Сообщение от админа:\n\n{text}")
            sent_ids.append(uid)
            time.sleep(0.05)
        except Exception as e:
            failed_ids.append(uid)
            logger.error(f"Не удалось отправить {uid}: {e}")

    report = f"✅ Рассылка завершена!\n📊 Отправлено: {len(sent_ids)}\n❌ Не удалось: {len(failed_ids)}"
    if sent_ids:
        ids_str = ", ".join(str(x) for x in sent_ids[:30])
        if len(sent_ids) > 30:
            ids_str += f" и ещё {len(sent_ids)-30}"
        report += f"\n\n<b>ID получателей:</b> {ids_str}"
    bot.send_message(user_id, report, parse_mode='HTML')

@bot.message_handler(commands=['stats', 'статистика'])
def stats_command(message):
    user_id = message.chat.id
    if user_id not in ADMIN_IDS:
        return
    bot.send_message(user_id, "📊 Выберите тип отчёта:", reply_markup=create_stats_choice_keyboard())

@bot.message_handler(commands=['пользователи'])
def users_command(message):
    user_id = message.chat.id
    if user_id not in ADMIN_IDS:
        return
    send_users_with_tasks(user_id)

@bot.message_handler(func=lambda m: True)
def handle_message(message):
    user_id = message.chat.id
    text = message.text.strip()

    # Проверяем, не находится ли пользователь в состоянии ввода чисел для месячного повтора
    if user_id in user_temp_data and user_temp_data[user_id].get('action') == 'enter_monthly_days':
        # Обрабатываем ввод чисел
        numbers = []
        for part in re.findall(r'\d+', text):
            num = int(part)
            if 1 <= num <= 31:
                numbers.append(num)
        if not numbers:
            bot.send_message(user_id, "❌ Нужно ввести числа от 1 до 31 через запятую! Попробуйте ещё раз.")
            return
        numbers = sorted(list(set(numbers)))
        temp = user_temp_data[user_id]
        temp['recurrence_days'] = numbers
        temp['action'] = 'set_recurring_time'
        days_text = ', '.join(map(str, numbers))
        bot.send_message(user_id, f"🔄 <b>{temp['task_text']}</b>\n\n📅 Числа месяца: {days_text}\n\nНа какое время запланировать повтор?",
                         parse_mode='HTML', reply_markup=create_reminder_time_keyboard(user_id))
        return

    # Остальные обработчики
    if text == '📅 Календарь':
        show_calendar(user_id)
    elif text == '➕ Плюс дело':
        user_states[user_id] = {'action': 'add_today'}
        bot.send_message(user_id, "Напиши, что нужно сделать сегодня:")
    elif text == '📋 Что сегодня?':
        show_today_tasks(user_id)
    elif text == '⚙️ Настройки':
        settings = get_user_settings(user_id)
        bot.send_message(user_id,
            f"⚙️ Твои настройки:\n⏰ Время по умолчанию: {settings['default_reminder_time']}\n"
            f"⏱️ Напоминать заранее: {settings['default_remind_before']} мин",
            reply_markup=create_settings_keyboard())
    elif user_id in user_states and user_states[user_id].get('action') == 'add_today':
        today = get_current_time().strftime('%Y-%m-%d')
        user_temp_data[user_id] = {'task_text': text, 'date': today, 'action': 'set_task_time'}
        del user_states[user_id]
        bot.send_message(user_id, f"📝 <b>{text}</b>\nНа какое время?", parse_mode='HTML',
                         reply_markup=create_reminder_time_keyboard(user_id))
    elif user_id in user_states and user_states[user_id].get('action') == 'add_with_date':
        date = user_states[user_id]['date']
        user_temp_data[user_id] = {'task_text': text, 'date': date, 'action': 'set_task_time'}
        del user_states[user_id]
        bot.send_message(user_id, f"📝 <b>{text}</b>\nНа какое время?", parse_mode='HTML',
                         reply_markup=create_reminder_time_keyboard(user_id))
    elif user_id in user_temp_data and user_temp_data[user_id].get('action') == 'awaiting_recurring_text':
        user_temp_data[user_id]['task_text'] = text
        user_temp_data[user_id]['action'] = 'select_recurrence_type'
        bot.send_message(user_id, "Как часто повторять?", reply_markup=create_recurring_keyboard())
    else:
        today = get_current_time().strftime('%Y-%m-%d')
        user_temp_data[user_id] = {'task_text': text, 'date': today, 'action': 'set_task_time'}
        bot.send_message(user_id, f"✅ Добавил на сегодня!\n📝 <b>{text}</b>\nНа какое время?", parse_mode='HTML',
                         reply_markup=create_reminder_time_keyboard(user_id))

# ========== ОБРАБОТЧИКИ CALLBACK ==========
@bot.callback_query_handler(func=lambda call: True)
def callback_handler(call):
    user_id = call.message.chat.id
    msg_id = call.message.message_id
    data = call.data

    # Админские кнопки статистики
    if data == 'admin_stats_short':
        if user_id in ADMIN_IDS:
            send_activity_report(target_user_id=user_id)
        bot.answer_callback_query(call.id)
        return
    if data == 'admin_stats_detailed':
        if user_id in ADMIN_IDS:
            send_detailed_activity_report(user_id)
        bot.answer_callback_query(call.id)
        return

    # Календарь
    if data.startswith('calendar_'):
        if data == 'calendar_current':
            bot.edit_message_reply_markup(user_id, msg_id, reply_markup=create_calendar_keyboard(user_id))
        else:
            parts = data.split('_')
            if len(parts) == 3:
                y, m = int(parts[1]), int(parts[2])
                bot.edit_message_reply_markup(user_id, msg_id, reply_markup=create_calendar_keyboard(user_id, y, m))
        bot.answer_callback_query(call.id)

    elif data.startswith('day_'):
        date = data.replace('day_', '')
        show_day_tasks(user_id, date, msg_id)
        bot.answer_callback_query(call.id)

    elif data.startswith('task_'):
        tid = int(data.replace('task_', ''))
        show_task_details(user_id, tid, msg_id)
        bot.answer_callback_query(call.id)

    elif data.startswith('add_'):
        date = data.replace('add_', '')
        user_states[user_id] = {'action': 'add_with_date', 'date': date}
        bot.send_message(user_id, f"Что нужно сделать {format_date(date)}?")
        bot.answer_callback_query(call.id)

    elif data.startswith('done_'):
        tid = int(data.replace('done_', ''))
        mark_task_done(tid, user_id)
        task = get_task_by_id(tid)
        if task:
            _, _, date, _, _, _ = task
            show_day_tasks(user_id, date, msg_id)
        bot.answer_callback_query(call.id, "✅ Готово!")

    elif data.startswith('delete_one_'):
        tid = int(data.replace('delete_one_', ''))
        task = get_task_by_id(tid)
        if task:
            _, _, date, _, _, _ = task
            delete_task(tid, user_id)
            show_day_tasks(user_id, date, msg_id)
        bot.answer_callback_query(call.id, "🗑️ Удалено!")

    elif data.startswith('move_'):
        tid = int(data.replace('move_', ''))
        user_temp_data[user_id] = {'move_task_id': tid}
        bot.send_message(user_id, "📅 Выбери новую дату:", reply_markup=create_calendar_keyboard(user_id))
        bot.answer_callback_query(call.id)

    elif data.startswith('clear_ask_'):
        date = data.replace('clear_ask_', '')
        markup = types.InlineKeyboardMarkup(row_width=2)
        markup.add(
            types.InlineKeyboardButton("✅ Да, удалить всё", callback_data=f"clear_confirm_{date}"),
            types.InlineKeyboardButton("❌ Нет", callback_data=f"day_{date}")
        )
        bot.edit_message_text(f"⚠️ Удалить все дела на {format_date(date)}?", user_id, msg_id, reply_markup=markup)
        bot.answer_callback_query(call.id)

    elif data.startswith('clear_confirm_'):
        date = data.replace('clear_confirm_', '')
        clear_day(user_id, date)
        bot.send_message(user_id, f"✅ Все дела на {format_date(date)} удалены!", reply_markup=create_main_keyboard())
        bot.answer_callback_query(call.id)

    elif data == 'back_calendar':
        show_calendar(user_id, msg_id)
        bot.answer_callback_query(call.id)

    elif data == 'main_menu':
        bot.send_message(user_id, "Главное меню:", reply_markup=create_main_keyboard())
        bot.answer_callback_query(call.id)

    elif data == 'all_tasks':
        conn = sqlite3.connect('tasks.db')
        cursor = conn.cursor()
        cursor.execute("SELECT date, task, is_done, reminder_time, remind_before FROM tasks WHERE user_id=? ORDER BY date, reminder_time", (user_id,))
        tasks = cursor.fetchall()
        conn.close()
        if not tasks:
            bot.send_message(user_id, "📭 Пока пусто!", reply_markup=create_main_keyboard())
        else:
            by_date = {}
            for date_str, task, done, rtime, rbefore in tasks:
                by_date.setdefault(date_str, []).append((task, done, rtime, rbefore))
            msg = "📋 <b>Все твои делишки:</b>\n\n"
            for date_str in sorted(by_date.keys()):
                formatted = format_date(date_str)
                msg += f"<b>{formatted}:</b>\n"
                for task, done, rtime, rbefore in by_date[date_str]:
                    if done:
                        msg += f"  <s>{task}</s> ✅\n"
                    else:
                        time_info = f" ({rtime})" if rtime and rtime != 'None' else ""
                        if rbefore:
                            if rbefore < 60:
                                time_info += f" ⏰ за {rbefore} мин"
                            elif rbefore == 60:
                                time_info += " ⏰ за 1 час"
                            elif rbefore == 120:
                                time_info += " ⏰ за 2 часа"
                            elif rbefore == 1440:
                                time_info += " ⏰ за день"
                        msg += f"  • {task}{time_info}\n"
                msg += "\n"
            # Используем разбивку для длинных сообщений
            split_and_send(user_id, msg, parse_mode='HTML', reply_markup=create_main_keyboard())
        bot.answer_callback_query(call.id)

    # --- НАСТРОЙКИ ---
    elif data == 'setting_default_time':
        bot.edit_message_text("⏰ Выбери время по умолчанию:", user_id, msg_id,
                              reply_markup=create_default_time_keyboard(user_id))
        bot.answer_callback_query(call.id)

    elif data == 'setting_default_before':
        bot.edit_message_text("⏱️ Напомнить заранее:", user_id, msg_id,
                              reply_markup=create_default_before_keyboard(user_id))
        bot.answer_callback_query(call.id)

    elif data == 'setting_recurring':
        bot.edit_message_text("🔁 Управление повторяющимися делами:", user_id, msg_id,
                              reply_markup=create_recurring_management_keyboard())
        bot.answer_callback_query(call.id)

    elif data == 'setting_stats':
        show_user_stats(user_id, msg_id)
        bot.answer_callback_query(call.id)

    elif data.startswith('dtime_'):
        if data == 'dtime_back_settings':
            settings = get_user_settings(user_id)
            bot.edit_message_text(
                f"⚙️ Твои настройки:\n⏰ Время по умолчанию: {settings['default_reminder_time']}\n⏱️ Напоминать заранее: {settings['default_remind_before']} мин",
                user_id, msg_id, reply_markup=create_settings_keyboard())
            bot.answer_callback_query(call.id)
            return
        time_val = data.replace('dtime_', '')
        update_user_setting(user_id, 'default_reminder_time', time_val)
        settings = get_user_settings(user_id)
        bot.edit_message_text(
            f"⚙️ Твои настройки:\n⏰ Время по умолчанию: {settings['default_reminder_time']}\n⏱️ Напоминать заранее: {settings['default_remind_before']} мин",
            user_id, msg_id, reply_markup=create_settings_keyboard())
        bot.answer_callback_query(call.id, f"✅ Время по умолчанию: {time_val}")

    elif data.startswith('dbefore_'):
        if data == 'dbefore_back_settings':
            settings = get_user_settings(user_id)
            bot.edit_message_text(
                f"⚙️ Твои настройки:\n⏰ Время по умолчанию: {settings['default_reminder_time']}\n⏱️ Напоминать заранее: {settings['default_remind_before']} мин",
                user_id, msg_id, reply_markup=create_settings_keyboard())
            bot.answer_callback_query(call.id)
            return
        before_val = int(data.replace('dbefore_', ''))
        update_user_setting(user_id, 'default_remind_before', before_val)
        settings = get_user_settings(user_id)
        bot.edit_message_text(
            f"⚙️ Твои настройки:\n⏰ Время по умолчанию: {settings['default_reminder_time']}\n⏱️ Напоминать заранее: {settings['default_remind_before']} мин",
            user_id, msg_id, reply_markup=create_settings_keyboard())
        bot.answer_callback_query(call.id, f"✅ Напоминать заранее изменено")

    elif data == 'back_settings':
        settings = get_user_settings(user_id)
        bot.edit_message_text(
            f"⚙️ Твои настройки:\n⏰ Время по умолчанию: {settings['default_reminder_time']}\n⏱️ Напоминать заранее: {settings['default_remind_before']} мин",
            user_id, msg_id, reply_markup=create_settings_keyboard())
        bot.answer_callback_query(call.id)

    elif data.startswith('stats_'):
        if data == 'stats_today':
            show_today_stats(user_id, msg_id)
        elif data == 'stats_week':
            show_week_stats(user_id, msg_id)
        elif data == 'stats_month':
            show_month_stats(user_id, msg_id)
        elif data == 'stats_all':
            show_user_stats(user_id, msg_id)
        bot.answer_callback_query(call.id)

    # --- ДОБАВЛЕНИЕ ВРЕМЕНИ ---
    elif data.startswith('time_'):
        time_val = data.replace('time_', '')
        if time_val == 'cancel':
            if user_id in user_temp_data:
                del user_temp_data[user_id]
            bot.send_message(user_id, "❌ Отменено", reply_markup=create_main_keyboard())
            bot.answer_callback_query(call.id)
            return
        if user_id not in user_temp_data:
            bot.answer_callback_query(call.id)
            return
        temp = user_temp_data[user_id]
        if time_val == 'none':
            time_val = None
        temp['reminder_time'] = time_val
        if temp.get('action') == 'set_task_time':
            temp['action'] = 'set_remind_before'
            bot.send_message(user_id, f"⏰ Время: {time_val if time_val else 'без напоминания'}\nНапомнить заранее?",
                             reply_markup=create_remind_before_keyboard(user_id))
        elif temp.get('action') == 'set_recurring_time':
            temp['action'] = 'set_recurring_remind_before'
            bot.send_message(user_id, f"⏰ Время: {time_val if time_val else 'без напоминания'}\nНапомнить заранее?",
                             reply_markup=create_remind_before_keyboard(user_id))
        else:
            add_task_to_db(user_id, temp['task_text'], temp['date'], time_val, 0)
            bot.send_message(user_id, f"✅ Задача добавлена на {format_date(temp['date'])}!",
                             reply_markup=create_main_keyboard())
            del user_temp_data[user_id]
        bot.answer_callback_query(call.id)

    elif data.startswith('before_'):
        choice = data.replace('before_', '')
        if choice == 'cancel':
            if user_id in user_temp_data:
                del user_temp_data[user_id]
            bot.send_message(user_id, "❌ Отменено", reply_markup=create_main_keyboard())
            bot.answer_callback_query(call.id)
            return
        if user_id not in user_temp_data:
            bot.answer_callback_query(call.id)
            return
        temp = user_temp_data[user_id]
        before = 0
        if choice == 'none':
            before = 0
        elif choice == '5': before = 5
        elif choice == '15': before = 15
        elif choice == '30': before = 30
        elif choice == '60': before = 60
        elif choice == '120': before = 120
        elif choice == '1440': before = 1440
        else: before = 0

        if temp.get('action') == 'set_remind_before':
            add_task_to_db(user_id, temp['task_text'], temp['date'], temp['reminder_time'], before)
            bot.send_message(user_id, f"✅ Задача добавлена на {format_date(temp['date'])}!",
                             reply_markup=create_main_keyboard())
            del user_temp_data[user_id]
        elif temp.get('action') == 'set_recurring_remind_before':
            add_recurring_task(user_id, temp['task_text'], temp['recurrence_type'],
                               temp.get('recurrence_days', []), temp['reminder_time'],
                               before, temp['date'])
            bot.send_message(user_id, f"✅ Повторяющаяся задача создана!",
                             reply_markup=create_main_keyboard())
            del user_temp_data[user_id]
        bot.answer_callback_query(call.id)

    # --- ПОВТОРЯЮЩИЕСЯ ---
    elif data.startswith('recur_'):
        date = data.replace('recur_', '')
        user_temp_data[user_id] = {'date': date, 'action': 'awaiting_recurring_text'}
        bot.send_message(user_id, f"Что нужно повторять {format_date(date)}?")
        bot.answer_callback_query(call.id)

    elif data.startswith('type_'):
        recur_type = data.replace('type_', '')
        if recur_type == 'cancel':
            if user_id in user_temp_data:
                del user_temp_data[user_id]
            bot.send_message(user_id, "❌ Отменено", reply_markup=create_main_keyboard())
            bot.answer_callback_query(call.id)
            return
        if user_id not in user_temp_data:
            bot.answer_callback_query(call.id, "❌ Ошибка: данные не найдены")
            return
        temp = user_temp_data[user_id]
        if recur_type == 'none':
            temp['action'] = 'set_task_time'
            bot.send_message(user_id, f"✅ Окей!\n\n📝 <b>{temp['task_text']}</b>\n\nНа какое время запланировать?",
                           parse_mode='HTML', reply_markup=create_reminder_time_keyboard(user_id))
        elif recur_type in ['daily', 'weekdays', 'weekends']:
            temp['recurrence_type'] = recur_type
            temp['recurrence_days'] = []
            temp['action'] = 'set_recurring_time'
            bot.send_message(user_id, f"🔄 <b>{temp['task_text']}</b>\n\nНа какое время запланировать повтор?",
                           parse_mode='HTML', reply_markup=create_reminder_time_keyboard(user_id))
        elif recur_type == 'weekly':
            temp['recurrence_type'] = 'weekly'
            temp['action'] = 'select_weekly_days'
            temp['selected_days'] = []
            bot.send_message(user_id, "Выбери дни недели для повтора:", reply_markup=create_days_of_week_keyboard())
        elif recur_type == 'monthly':
            temp['recurrence_type'] = 'monthly'
            temp['action'] = 'enter_monthly_days'
            bot.send_message(user_id, "Введи числа месяца через запятую (например: 5, 10, 15):")
        bot.answer_callback_query(call.id)

    elif data.startswith('weekday_'):
        day_code = data.replace('weekday_', '')
        if user_id not in user_temp_data:
            bot.answer_callback_query(call.id)
            return
        temp = user_temp_data[user_id]
        if temp.get('action') == 'select_weekly_days':
            selected_days = temp.get('selected_days', [])
            if day_code in selected_days:
                selected_days.remove(day_code)
            else:
                selected_days.append(day_code)
            temp['selected_days'] = selected_days
            days_names = {'mon': 'Пн', 'tue': 'Вт', 'wed': 'Ср', 'thu': 'Чт', 'fri': 'Пт', 'sat': 'Сб', 'sun': 'Вс'}
            selected_text = ', '.join([days_names[d] for d in sorted(selected_days)]) if selected_days else "Не выбрано"
            try:
                bot.edit_message_text(
                    f"Выбери дни недели для повтора:\n\nВыбрано: {selected_text}",
                    user_id, msg_id,
                    reply_markup=create_days_of_week_keyboard(selected_days)
                )
            except Exception as e:
    logger.error(f"Не удалось обновить выбор дней недели: {e}")
        bot.answer_callback_query(call.id)

    elif data == 'weekdays_done':
        if user_id not in user_temp_data:
            bot.answer_callback_query(call.id)
            return
        temp = user_temp_data[user_id]
        if temp.get('action') == 'select_weekly_days':
            selected_days = temp.get('selected_days', [])
            if not selected_days:
                bot.send_message(user_id, "❌ Нужно выбрать хотя бы один день!")
                bot.answer_callback_query(call.id)
                return
            day_map = {'mon': 0, 'tue': 1, 'wed': 2, 'thu': 3, 'fri': 4, 'sat': 5, 'sun': 6}
            recurrence_days = [day_map[code] for code in selected_days]
            temp['recurrence_days'] = recurrence_days
            temp['action'] = 'set_recurring_time'
            bot.send_message(user_id, f"🔄 <b>{temp['task_text']}</b>\n\nНа какое время запланировать повтор?",
                           parse_mode='HTML', reply_markup=create_reminder_time_keyboard(user_id))
        bot.answer_callback_query(call.id)

    elif data.startswith('recurring_page_'):
        page = int(data.replace('recurring_page_', ''))
        tasks = get_recurring_tasks(user_id)
        bot.edit_message_reply_markup(user_id, msg_id, reply_markup=create_recurring_list_keyboard(tasks, page))

    elif data.startswith('delete_recurring_'):
        task_id = int(data.replace('delete_recurring_', ''))
        delete_recurring_task(task_id, user_id)
        tasks = get_recurring_tasks(user_id)
        if tasks:
            bot.edit_message_text("🔁 Управление повторяющимися делами:", user_id, msg_id,
                                reply_markup=create_recurring_management_keyboard())
        else:
            bot.send_message(user_id, "✅ Повторяющаяся задача удалена!")
        bot.answer_callback_query(call.id)

    elif data == 'recurring_delete_all_ask':
        tasks = get_recurring_tasks(user_id)
        if not tasks:
            bot.answer_callback_query(call.id, "❌ Нет повторяющихся дел")
            return
        markup = create_confirm_delete_all_recurring_keyboard()
        bot.edit_message_text("⚠️ Удалить ВСЕ повторяющиеся дела?", user_id, msg_id, reply_markup=markup)
        bot.answer_callback_query(call.id)

    elif data == 'recurring_delete_all_confirm':
        deleted = delete_all_recurring_tasks(user_id)
        bot.send_message(user_id, f"✅ Удалено {deleted} повторяющихся дел!")
        bot.answer_callback_query(call.id)

    elif data == 'recurring_manage':
        bot.edit_message_text("🔁 Управление повторяющимися делами:", user_id, msg_id,
                            reply_markup=create_recurring_management_keyboard())
        bot.answer_callback_query(call.id)

    elif data == 'recurring_list':
        tasks = get_recurring_tasks(user_id)
        if not tasks:
            bot.edit_message_text("📭 Нет повторяющихся дел.", user_id, msg_id)
        else:
            bot.edit_message_text("📋 Список повторяющихся дел:", user_id, msg_id,
                                reply_markup=create_recurring_list_keyboard(tasks))
        bot.answer_callback_query(call.id)

    # Если ничего не подошло
    else:
        bot.answer_callback_query(call.id)

# ========== ЗАПУСК ==========
if __name__ == "__main__":
    logger.info("🤖 СДВГ-напоминалка запущена!")

    # Потоки для напоминаний
    reminder_thread = threading.Thread(target=check_reminders)
    reminder_thread.daemon = True
    reminder_thread.start()

    reset_thread = threading.Thread(target=reset_daily_reminders)
    reset_thread.daemon = True
    reset_thread.start()

    # Поток для ежедневного отчёта
    report_thread = threading.Thread(target=daily_report_worker)
    report_thread.daemon = True
    report_thread.start()

    retry_delay = 5
    while True:
        try:
            bot.infinity_polling(timeout=60, long_polling_timeout=30, logger_level=logging.ERROR)
        except Exception as e:
            logger.error(f"Ошибка: {e}. Перезапуск через {retry_delay} сек...")
            time.sleep(retry_delay)
            retry_delay = min(retry_delay * 2, 60)
