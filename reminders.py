import sqlite3
import datetime
import time
import logging
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
                        reminder_datetime = datetime.datetime.strptime(
                            f"{current_date} {reminder_time}",
                            "%Y-%m-%d %H:%M"
                        )
                    else:
                        reminder_datetime = datetime.datetime.strptime(
                            f"{current_date} {reminder_time}",
                            "%Y-%m-%d %H:%M"
                        )
                        if remind_before > 0:
                            reminder_datetime = reminder_datetime - datetime.timedelta(
                                minutes=remind_before
                            )

                    if now >= reminder_datetime:
                        if remind_before > 0:
                            if remind_before < 60:
                                text = (
                                    f"⏰ <b>Скоро дело!</b>\n\n"
                                    f"{task_text}\n\n"
                                    f"Через {remind_before} минут ({reminder_time})"
                                )
                            elif remind_before == 60:
                                text = (
                                    f"⏰ <b>Скоро дело!</b>\n\n"
                                    f"{task_text}\n\n"
                                    f"Через 1 час ({reminder_time})"
                                )
                            elif remind_before == 120:
                                text = (
                                    f"⏰ <b>Скоро дело!</b>\n\n"
                                    f"{task_text}\n\n"
                                    f"Через 2 часа ({reminder_time})"
                                )
                            elif remind_before == 1440:
                                text = (
                                    f"⏰ <b>Скоро дело!</b>\n\n"
                                    f"{task_text}\n\n"
                                    f"Завтра в {reminder_time}"
                                )
                        else:
                            text = (
                                f"⏰ <b>Пора делать!</b>\n\n"
                                f"{task_text}\n\n"
                                f"Сейчас время: {reminder_time}"
                            )

                        bot.send_message(user_id, text, parse_mode='HTML')
                        logger.info(
                            f"Отправлено уведомление для задачи {task_id} "
                            f"пользователю {user_id}"
                        )

                        cursor.execute(
                            "UPDATE tasks SET reminder_sent = 1 WHERE id = ?",
                            (task_id,)
                        )
                        conn.commit()

                except Exception as e:
                    logger.error(
                        f"Ошибка при обработке уведомления для задачи "
                        f"{task_id}: {e}"
                    )

            conn.close()
            time.sleep(30)

        except Exception as e:
            logger.error(f"Ошибка в системе уведомлений: {e}")
            time.sleep(60)


def reset_daily_reminders(bot, logger, generate_recurring_tasks_for_user):
    while True:
        try:
            now = get_current_time()
            current_time = now.strftime('%H:%M')
            today_str = now.strftime('%Y-%m-%d')

            if current_time == '00:00':
                conn = sqlite3.connect('tasks.db')
                cursor = conn.cursor()

                # Сбрасываем reminder_sent только для задач на сегодня
                cursor.execute(
                    "UPDATE tasks SET reminder_sent = 0 WHERE date = ?",
                    (today_str,)
                )
                conn.commit()

                # Генерируем повторяющиеся задачи на новый день
                cursor.execute(
                    "SELECT DISTINCT user_id "
                    "FROM recurring_tasks WHERE is_active = 1"
                )
                users = cursor.fetchall()

                for (uid,) in users:
                    generate_recurring_tasks_for_user(uid)

                conn.close()

                logger.info(
                    "Сброшены статусы уведомлений и сгенерированы "
                    "повторяющиеся задачи"
                )
                time.sleep(60)

            else:
                time.sleep(30)

        except Exception as e:
            logger.error(
                f"Ошибка в функции сброса уведомлений: {e}"
            )
            time.sleep(60)
