# Тестирование ADHD Reminder Bot без остановки боевого бота

## 1. Боевой бот
Боевой бот остаётся на VDSina в `/root/adhd-reminder-bot`.
Токен хранится только в серверном `config.py`.

## 2. Тестовый бот
Создай второго Telegram-бота через @BotFather и получи отдельный токен.

На компьютере:
```powershell
git clone https://github.com/draconiha/adhd-reminder-bot.git adhd-reminder-bot-test
cd adhd-reminder-bot-test
git checkout dev
```

Создай рядом с `bot.py` файл `config.py`:
```python
BOT_TOKEN = "ТОКЕН_ТЕСТОВОГО_БОТА"
```

Дальше:
```powershell
py -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
python bot.py
```

Тестовый бот использует свой `tasks.db`.

## 3. Ветки
- `main` — боевой код.
- `dev` — текущая тестовая версия.

Не запускай два процесса с одним Telegram-токеном через polling одновременно.

## 4. Обновление VDSina после проверки
```bash
cd /root/adhd-reminder-bot
git checkout main
git pull
systemctl restart adhd-bot
systemctl status adhd-bot --no-pager
```

Логи:
```bash
journalctl -u adhd-bot -n 50 --no-pager
```

## 5. 24/7
`deploy/adhd-bot.service` устанавливается так:
```bash
cp /root/adhd-reminder-bot/deploy/adhd-bot.service /etc/systemd/system/adhd-bot.service
systemctl daemon-reload
systemctl enable --now adhd-bot
systemctl status adhd-bot --no-pager
```

Если бот уже запущен вручную, сначала останови старый процесс.

## 6. Что добавлено в dev
- 00:00–23:00 для времени по умолчанию и времени задачи;
- ежедневная сводка дел в пользовательское время по умолчанию;
- дружелюбное сообщение при отсутствии планов;
- «✅ Сделано» прямо в уведомлении;
- нормальное сообщение для просроченного дела;
- «📥 Куча дел»;
- разбор дела из кучи через дату → время → предварительное напоминание;
- первичная настройка времени подъёма и сна;
- защита callback от повторного answerCallbackQuery;
- исправление уведомлений «за день»;
- systemd-конфигурация и проверка синтаксиса.
