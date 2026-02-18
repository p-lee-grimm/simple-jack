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

## Установка и запуск

### 1. Предварительные требования

- Python 3.13+
- [Claude Code CLI](https://claude.ai/code) — установлен и авторизован (`claude --version`)
- Telegram Bot Token — получить у [@BotFather](https://t.me/BotFather)

### 2. Клонирование репозитория

```bash
git clone https://github.com/p-lee-grimm/simple-jack.git claude-telegram-bot
cd claude-telegram-bot
```

### 3. Виртуальное окружение и зависимости

```bash
python3 -m venv ~/envs/claude-bot-env
source ~/envs/claude-bot-env/bin/activate
pip install -r requirements.txt
```

### 4. Конфигурация

Создайте файл `.env` в корне проекта:

```bash
cp .env.example .env
nano .env
```

Содержимое `.env`:

```env
TELEGRAM_BOT_TOKEN=your_bot_token_here
ALLOWED_USERNAME=your_telegram_username
CLAUDE_CLI_PATH=/home/YOUR_USERNAME/.local/bin/claude
WORKSPACE_DIR=/home/YOUR_USERNAME/claude-telegram-bot/workspace
DATA_DIR=/home/YOUR_USERNAME/claude-telegram-bot/data
SESSION_TIMEOUT_HOURS=24
```

Установите права доступа:

```bash
chmod 600 .env
```

### 5. Запуск

**Вручную (для разработки и отладки):**

```bash
source ~/envs/claude-bot-env/bin/activate
python -m src.main
```

**Как systemd-сервис (для продакшена):**

Отредактируйте `claude-telegram-bot.service`, заменив `YOUR_USERNAME` на ваше имя пользователя, затем установите сервис:

```bash
sudo cp claude-telegram-bot.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable claude-telegram-bot
sudo systemctl start claude-telegram-bot
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
