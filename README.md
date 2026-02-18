# Claude Telegram Bot

Telegram бот для интеграции с Claude Code CLI.

## Возможности

- Обработка текстовых сообщений через Claude Code
- Поддержка изображений и документов
- Сохранение контекста диалога
- Автоматическая отправка созданных файлов
- Разбивка длинных ответов
- Фильтрация по username

## Структура проекта

```
claude-telegram-bot/
├── .env                  # Конфигурация
├── requirements.txt      # Python зависимости
├── config/
│   └── settings.py      # Настройки приложения
├── src/
│   ├── main.py          # Точка входа
│   ├── bot/             # Обработчики Telegram
│   ├── claude/          # Интеграция с Claude CLI
│   ├── media/           # Обработка медиафайлов
│   └── utils/           # Утилиты
├── data/
│   ├── sessions/        # История диалогов
│   ├── media/           # Временные медиафайлы
│   └── logs/            # Логи
└── workspace/           # Рабочая директория для Claude
```

## Команды бота

- `/start` - приветствие и инструкции
- `/help` - справка по использованию
- `/reset` - очистка истории диалога

## Управление сервисом

```bash
# Проверить статус
sudo systemctl status claude-telegram-bot.service

# Остановить
sudo systemctl stop claude-telegram-bot.service

# Запустить
sudo systemctl start claude-telegram-bot.service

# Перезапустить
sudo systemctl restart claude-telegram-bot.service

# Отключить автозапуск
sudo systemctl disable claude-telegram-bot.service

# Включить автозапуск
sudo systemctl enable claude-telegram-bot.service
```

## Просмотр логов

```bash
# Логи systemd
journalctl -u claude-telegram-bot.service -f

# Файловые логи
tail -f ~/claude-telegram-bot/data/logs/bot.log
```

## Конфигурация

Файл `.env` содержит:
- `TELEGRAM_BOT_TOKEN` - токен Telegram бота
- `ALLOWED_USERNAME` - разрешенный username
- `CLAUDE_CLI_PATH` - путь к Claude CLI
- `WORKSPACE_DIR` - рабочая директория
- `DATA_DIR` - директория данных
- `SESSION_TIMEOUT_HOURS` - таймаут сессии

## Технологии

- Python 3.13
- python-telegram-bot 21.0
- pydantic 2.12+
- aiofiles
- Claude Code CLI

## Безопасность

- Фильтрация по username на уровне MessageFilter
- Токен бота хранится в .env с правами 600
- Изолированные workspace для каждого пользователя
- Логирование всех действий
