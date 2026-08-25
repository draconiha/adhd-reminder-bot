from pathlib import Path
import re

path = Path('bot.py')
text = path.read_text(encoding='utf-8')

# Add link from generated task rows to recurring template.
if "if 'recurring_id' not in columns:" not in text:
    marker = '''        if 'reminder_sent' not in columns:
            cursor.execute("ALTER TABLE tasks ADD COLUMN reminder_sent INTEGER DEFAULT 0")
'''
    addition = '''        if 'recurring_id' not in columns:
            cursor.execute("ALTER TABLE tasks ADD COLUMN recurring_id INTEGER")
'''
    if marker not in text:
        raise SystemExit('tasks migration marker not found')
    text = text.replace(marker, marker + addition, 1)

old_insert = '''                    cursor.execute(
                        "INSERT INTO tasks (user_id, task, date, reminder_time, remind_before, reminder_sent) VALUES (?, ?, ?, ?, ?, 0)",
                        (user_id, task_text, date_str, reminder_time, remind_before)
                    )
'''
new_insert = '''                    cursor.execute(
                        "INSERT INTO tasks (user_id, task, date, reminder_time, remind_before, reminder_sent, recurring_id) VALUES (?, ?, ?, ?, ?, 0, ?)",
                        (user_id, task_text, date_str, reminder_time, remind_before, task_id)
                    )
'''
if old_insert in text:
    text = text.replace(old_insert, new_insert, 1)

# Backfill old future instances conservatively.
if 'def migrate_legacy_recurring_instances():' not in text:
    helper = r'''

def migrate_legacy_recurring_instances():
    conn = sqlite3.connect('tasks.db')
    cursor = conn.cursor()
    today = get_current_time().date()
    horizon = today + datetime.timedelta(days=60)
    try:
        cursor.execute("SELECT id, user_id, task, recurrence_type, recurrence_days, reminder_time, remind_before, start_date, end_date, created_at FROM recurring_tasks WHERE is_active=1")
        for recurring in cursor.fetchall():
            rid, uid, task_text, recurrence_type, days_json, reminder_time, remind_before, start_date_str, end_date_str, created_at = recurring
            try:
                days = json.loads(days_json) if days_json else []
            except Exception:
                days = []
            try:
                start_date = datetime.datetime.strptime(start_date_str, '%Y-%m-%d').date()
            except Exception:
                start_date = today
            try:
                end_date = datetime.datetime.strptime(end_date_str, '%Y-%m-%d').date() if end_date_str else None
            except Exception:
                end_date = None
            cursor.execute("SELECT id, date FROM tasks WHERE user_id=? AND recurring_id IS NULL AND task=? AND reminder_time=? AND remind_before=? AND is_done=0 AND date>=? AND date<=? AND created_at>=?", (uid, task_text, reminder_time, remind_before, today.strftime('%Y-%m-%d'), horizon.strftime('%Y-%m-%d'), created_at))
            for task_row_id, date_str in cursor.fetchall():
                try:
                    current = datetime.datetime.strptime(date_str, '%Y-%m-%d').date()
                except Exception:
                    continue
                if current < start_date or (end_date and current > end_date):
                    continue
                should_exist = (
                    recurrence_type == 'daily'
                    or (recurrence_type == 'weekdays' and current.weekday() < 5)
                    or (recurrence_type == 'weekends' and current.weekday() >= 5)
                    or (recurrence_type == 'weekly' and current.weekday() in days)
                    or (recurrence_type == 'monthly' and current.day in days)
                )
                if should_exist:
                    cursor.execute("UPDATE tasks SET recurring_id=? WHERE id=? AND recurring_id IS NULL", (rid, task_row_id))
        conn.commit()
    finally:
        conn.close()
'''
    marker = 'def delete_recurring_task(task_id, user_id):'
    if marker not in text:
        raise SystemExit('delete marker not found')
    text = text.replace(marker, helper + '\n' + marker, 1)

# Delete one recurring template plus all unfinished generated instances.
pattern = re.compile(r"def delete_recurring_task\(task_id, user_id\):\n.*?return True\n", re.S)
replacement = '''def delete_recurring_task(task_id, user_id):
    migrate_legacy_recurring_instances()
    conn = sqlite3.connect('tasks.db')
    cursor = conn.cursor()
    cursor.execute("SELECT id FROM recurring_tasks WHERE id=? AND user_id=?", (task_id, user_id))
    if not cursor.fetchone():
        conn.close()
        return False, 0
    cursor.execute("DELETE FROM tasks WHERE user_id=? AND recurring_id=? AND is_done=0", (user_id, task_id))
    deleted_instances = cursor.rowcount
    cursor.execute("DELETE FROM recurring_tasks WHERE id=? AND user_id=?", (task_id, user_id))
    conn.commit()
    conn.close()
    return True, deleted_instances
'''
text, count = pattern.subn(replacement, text, count=1)
if count != 1:
    raise SystemExit('delete_recurring_task replacement failed')

# Delete all recurring templates plus unfinished generated instances.
pattern = re.compile(r"def delete_all_recurring_tasks\(user_id\):\n.*?return deleted_count\n", re.S)
replacement = '''def delete_all_recurring_tasks(user_id):
    migrate_legacy_recurring_instances()
    conn = sqlite3.connect('tasks.db')
    cursor = conn.cursor()
    cursor.execute("DELETE FROM tasks WHERE user_id=? AND recurring_id IS NOT NULL AND is_done=0", (user_id,))
    deleted_instances = cursor.rowcount
    cursor.execute("DELETE FROM recurring_tasks WHERE user_id=?", (user_id,))
    deleted_count = cursor.rowcount
    conn.commit()
    conn.close()
    return deleted_count, deleted_instances
'''
text, count = pattern.subn(replacement, text, count=1)
if count != 1:
    raise SystemExit('delete_all_recurring_tasks replacement failed')

# Detail-card keyboards.
if 'def create_recurring_details_keyboard(task_id):' not in text:
    helper = r'''

def create_recurring_details_keyboard(task_id):
    markup = types.InlineKeyboardMarkup(row_width=2)
    markup.add(
        types.InlineKeyboardButton("🗑️ Удалить", callback_data=f"delete_recurring_ask_{task_id}"),
        types.InlineKeyboardButton("◀️ К списку", callback_data="recurring_list")
    )
    return markup
'''
    text = text.replace('def create_confirm_delete_all_recurring_keyboard():', helper + '\n\ndef create_confirm_delete_all_recurring_keyboard():', 1)

if 'def create_confirm_delete_recurring_keyboard(task_id):' not in text:
    helper = r'''

def create_confirm_delete_recurring_keyboard(task_id):
    markup = types.InlineKeyboardMarkup(row_width=2)
    markup.add(
        types.InlineKeyboardButton("✅ Да, удалить", callback_data=f"delete_recurring_confirm_{task_id}"),
        types.InlineKeyboardButton("❌ Нет", callback_data=f"recurring_view_{task_id}")
    )
    return markup
'''
    text = text.replace('def create_confirm_delete_all_recurring_keyboard():', helper + '\n\ndef create_confirm_delete_all_recurring_keyboard():', 1)

# Detail renderer intentionally has no embedded newline escapes in f-strings.
if 'def show_recurring_details(user_id, task_id' not in text:
    helper = r'''

def show_recurring_details(user_id, task_id, message_id=None):
    tasks = get_recurring_tasks(user_id)
    task = next((t for t in tasks if t[0] == task_id), None)
    if not task:
        text = "❌ Повторяющееся дело не найдено."
        if message_id:
            bot.edit_message_text(text, user_id, message_id)
        else:
            bot.send_message(user_id, text)
        return
    (_, task_text, recurrence_type, recurrence_days_json, reminder_time, remind_before, start_date, end_date, is_active) = task
    names = {'daily': 'каждый день', 'weekdays': 'по будням', 'weekends': 'по выходным', 'weekly': 'еженедельно', 'monthly': 'ежемесячно', 'none': 'без повтора'}
    recurrence_text = names.get(recurrence_type, recurrence_type)
    try:
        days = json.loads(recurrence_days_json) if recurrence_days_json else []
    except Exception:
        days = []
    if recurrence_type == 'weekly' and days:
        day_names = ['Пн', 'Вт', 'Ср', 'Чт', 'Пт', 'Сб', 'Вс']
        selected = [day_names[d] for d in days if isinstance(d, int) and 0 <= d <= 6]
        if selected:
            recurrence_text += ': ' + ', '.join(selected)
    elif recurrence_type == 'monthly' and days:
        recurrence_text += ': ' + ', '.join(map(str, days))
    start_text = format_date(start_date) if start_date else 'не указано'
    end_text = format_date(end_date) if end_date else 'без ограничения'
    if reminder_time and reminder_time != 'None':
        reminder_text = reminder_time
        if remind_before == 5: reminder_text += ' (за 5 мин)'
        elif remind_before == 15: reminder_text += ' (за 15 мин)'
        elif remind_before == 30: reminder_text += ' (за 30 мин)'
        elif remind_before == 60: reminder_text += ' (за 1 час)'
        elif remind_before == 120: reminder_text += ' (за 2 часа)'
        elif remind_before == 1440: reminder_text += ' (за день)'
    else:
        reminder_text = 'без напоминания'
    status_text = 'активно' if is_active else 'выключено'
    text = f"🔄 <b>{task_text}</b> | 📅 Начало: {start_text} | 📅 До: {end_text} | 🔁 Повтор: {recurrence_text} | ⏰ Время: {reminder_text} | 📌 Статус: {status_text}"
    markup = create_recurring_details_keyboard(task_id)
    if message_id:
        bot.edit_message_text(text, user_id, message_id, parse_mode='HTML', reply_markup=markup)
    else:
        bot.send_message(user_id, text, parse_mode='HTML', reply_markup=markup)
'''
    text = text.replace('def create_confirm_delete_all_recurring_keyboard():', helper + '\n\ndef create_confirm_delete_all_recurring_keyboard():', 1)

# Callbacks: view -> confirm -> delete.
pattern = re.compile(r"    elif data\.startswith\('recurring_view_'\):.*?    elif data == 'recurring_delete_all_ask':", re.S)
replacement = '''    elif data.startswith('recurring_view_'):
        task_id = int(data.replace('recurring_view_', ''))
        show_recurring_details(user_id, task_id, msg_id)
        bot.answer_callback_query(call.id)

    elif data.startswith('delete_recurring_ask_'):
        task_id = int(data.replace('delete_recurring_ask_', ''))
        bot.edit_message_text("⚠️ Удалить это повторяющееся дело и все его невыполненные экземпляры?", user_id, msg_id, reply_markup=create_confirm_delete_recurring_keyboard(task_id))
        bot.answer_callback_query(call.id)

    elif data.startswith('delete_recurring_confirm_'):
        task_id = int(data.replace('delete_recurring_confirm_', ''))
        deleted_ok, deleted_instances = delete_recurring_task(task_id, user_id)
        tasks = get_recurring_tasks(user_id)
        if deleted_ok:
            result_text = f"✅ Повтор удалён. Невыполненных экземпляров удалено: {deleted_instances}."
        else:
            result_text = "❌ Повторяющееся дело не найдено."
        if tasks:
            bot.edit_message_text(result_text, user_id, msg_id, reply_markup=create_recurring_list_keyboard(tasks))
        else:
            bot.edit_message_text(result_text, user_id, msg_id, reply_markup=create_recurring_management_keyboard())
        bot.answer_callback_query(call.id, "✅ Готово" if deleted_ok else "❌ Не найдено")

    elif data == 'recurring_delete_all_ask':'''
text, count = pattern.subn(replacement, text, count=1)
if count != 1:
    raise SystemExit('recurring callback replacement failed')

# Delete-all callback.
pattern = re.compile(r"    elif data == 'recurring_delete_all_confirm':\n.*?    elif data == 'recurring_manage':", re.S)
replacement = '''    elif data == 'recurring_delete_all_confirm':
        deleted_recurring, deleted_instances = delete_all_recurring_tasks(user_id)
        result_text = f"✅ Удалено повторов: {deleted_recurring}. Невыполненных экземпляров удалено: {deleted_instances}."
        bot.edit_message_text(result_text, user_id, msg_id, reply_markup=create_recurring_management_keyboard())
        bot.answer_callback_query(call.id, "✅ Всё удалено")

    elif data == 'recurring_manage':'''
text, count = pattern.subn(replacement, text, count=1)
if count != 1:
    raise SystemExit('delete-all callback replacement failed')

path.write_text(text, encoding='utf-8')
print('Recurring deletion patch syntax-safe')
