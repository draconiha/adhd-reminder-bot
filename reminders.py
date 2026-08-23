import sqlite3
import datetime
import time
import logging
import builtins
import sys
from zoneinfo import ZoneInfo


def get_current_time():
    """Возвращает текущее время по Москве."""
    return datetime.datetime.now(ZoneInfo("Europe/Moscow"))


def check_reminders(bot, logger):
    while True:
        try:
            now = get_current_time()
            current_date = now.strftime('%Y-%m-%d')
            tomorrow_date = (now + datetime.timedelta(days=1)).strftime('%Y-%m-%d')

            conn = sqlite3.connect('tasks.db')
            cursor = conn.cursor()

            cursor.execute("""
                SELECT id, user_id, task, reminder_time, remind_before
                FROM tasks
                WHERE date = ? AND is_done = 0 AND reminder_sent = 0
                AND reminder_time IS NOT NULL AND reminder_time != 'None'
            """, (current_date,))
            tasks_today = cursor.fetchall()

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
                    reminder_datetime = datetime.datetime.strptime(
                        f"{current_date} {reminder_time}",
                        "%Y-%m-%d %H:%M"
                    ).replace(tzinfo=ZoneInfo("Europe/Moscow"))

                    if remind_before > 0 and remind_before != 1440:
                        reminder_datetime -= datetime.timedelta(minutes=remind_before)

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
                                text = f"⏰ <b>Скоро дело!</b>\n\n{task_text}\n\nЧерез {remind_before} минут ({reminder_time})"
                        else:
                            text = f"⏰ <b>Пора делать!</b>\n\n{task_text}\n\nСейчас время: {reminder_time}"

                        bot.send_message(user_id, text, parse_mode='HTML')
                        logger.info(f"Отправлено уведомление для задачи {task_id} пользователю {user_id}")

                        cursor.execute(
                            "UPDATE tasks SET reminder_sent = 1 WHERE id = ?",
                            (task_id,)
                        )
                        conn.commit()

                except Exception as e:
                    logger.error(f"Ошибка при обработке уведомления для задачи {task_id}: {e}")

            conn.close()
            time.sleep(30)

        except Exception as e:
            logger.error(f"Ошибка в системе уведомлений: {e}")
            time.sleep(60)


def reset_daily_reminders(logger, generate_recurring_tasks_for_user):
    while True:
        try:
            now = get_current_time()
            current_time = now.strftime('%H:%M')
            today_str = now.strftime('%Y-%m-%d')

            if current_time == '00:00':
                conn = sqlite3.connect('tasks.db')
                cursor = conn.cursor()

                cursor.execute(
                    "UPDATE tasks SET reminder_sent = 0 WHERE date = ? AND is_done = 0",
                    (today_str,)
                )
                conn.commit()

                cursor.execute(
                    "SELECT DISTINCT user_id FROM recurring_tasks WHERE is_active = 1"
                )
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


def _create_recurring_details_keyboard(task_id):
    from telebot import types
    markup = types.InlineKeyboardMarkup(row_width=2)
    markup.add(
        types.InlineKeyboardButton(
            "🗑️ Удалить",
            callback_data=f"delete_recurring_{task_id}"
        ),
        types.InlineKeyboardButton(
            "◀️ К списку",
            callback_data="recurring_list"
        )
    )
    return markup


def _show_recurring_details(user_id, task_id, message_id=None):
    main = sys.modules.get("__main__")
    if main is None:
        return

    get_recurring_tasks = getattr(main, "get_recurring_tasks", None)
    format_date = getattr(main, "format_date", None)
    bot = getattr(main, "bot", None)

    if not get_recurring_tasks or not format_date or not bot:
        raise RuntimeError("Не удалось получить функции бота для показа повторяющегося дела")

    tasks = get_recurring_tasks(user_id)
    task = next((item for item in tasks if item[0] == task_id), None)

    if not task:
        text = "❌ Повторяющееся дело не найдено."
        if message_id:
            bot.edit_message_text(text, user_id, message_id)
        else:
            bot.send_message(user_id, text)
        return

    (
        _, task_text, recurrence_type, recurrence_days_json,
        reminder_time, remind_before, start_date, end_date, is_active
    ) = task

    recurrence_names = {
        "daily": "каждый день",
        "weekdays": "по будням",
        "weekends": "по выходным",
        "weekly": "еженедельно",
        "monthly": "ежемесячно",
        "none": "без повтора"
    }
    recurrence_text = recurrence_names.get(recurrence_type, recurrence_type)

    try:
        import json
        days = json.loads(recurrence_days_json) if recurrence_days_json else []
    except Exception:
        days = []

    if recurrence_type == "weekly" and days:
        names = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]
        selected_names = [
            names[d] for d in days
            if isinstance(d, int) and 0 <= d <= 6
        ]
        if selected_names:
            recurrence_text += ": " + ", ".join(selected_names)
    elif recurrence_type == "monthly" and days:
        recurrence_text += ": " + ", ".join(map(str, days))

    start_text = format_date(start_date) if start_date else "не указано"
    end_text = format_date(end_date) if end_date else "без ограничения"

    if reminder_time and reminder_time != "None":
        reminder_text = reminder_time
        if remind_before:
            if remind_before < 60:
                reminder_text += f" (за {remind_before} мин)"
            elif remind_before == 60:
                reminder_text += " (за 1 час)"
            elif remind_before == 120:
                reminder_text += " (за 2 часа)"
            elif remind_before == 1440:
                reminder_text += " (за день)"
    else:
        reminder_text = "без напоминания"

    status_text = "активно" if is_active else "выключено"

    text = (
        f"🔄 <b>{task_text}</b>\n\n"
        f"📅 Начало: {start_text}\n"
        f"📅 До: {end_text}\n"
        f"🔁 Повтор: {recurrence_text}\n"
        f"⏰ Время: {reminder_text}\n"
        f"📌 Статус: {status_text}"
    )

    markup = _create_recurring_details_keyboard(task_id)

    if message_id:
        bot.edit_message_text(
            text,
            user_id,
            message_id,
            parse_mode="HTML",
            reply_markup=markup
        )
    else:
        bot.send_message(
            user_id,
            text,
            parse_mode="HTML",
            reply_markup=markup
        )


# Делаем функции доступными в bot.py без повторной правки огромного файла.
builtins.show_recurring_details = _show_recurring_details
builtins.create_recurring_details_keyboard = _create_recurring_details_keyboard
