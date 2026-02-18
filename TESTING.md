# Руководство по тестированию бота

## Проверка статуса

```bash
# Статус сервиса
sudo systemctl status claude-telegram-bot.service

# Логи в реальном времени
journalctl -u claude-telegram-bot.service -f
```

## Тестовые сценарии

### 1. Базовая проверка
- Отправьте `/start` в Telegram боту
- Ожидается: приветственное сообщение

### 2. Текстовое сообщение
- Отправьте: "Привет! Напиши простой скрипт Python для вычисления факториала"
- Ожидается: ответ от Claude с кодом

### 3. Изображение
- Отправьте изображение с подписью: "Что на этом изображении?"
- Ожидается: описание изображения от Claude

### 4. Документ
- Отправьте текстовый файл с подписью: "Проанализируй этот файл"
- Ожидается: анализ содержимого файла

### 5. Длинный ответ
- Отправьте: "Напиши подробное руководство по Python для начинающих с примерами кода"
- Ожидается: ответ разбит на несколько сообщений с номерами [1/N], [2/N], и т.д.

### 6. Контекст диалога
- Отправьте: "Напиши функцию для сложения двух чисел"
- Затем: "Теперь добавь обработку ошибок"
- Ожидается: Claude помнит контекст и дополняет предыдущий код

### 7. Сброс сессии
- Отправьте `/reset`
- Ожидается: подтверждение сброса с новым session ID

### 8. Созданные файлы
- Отправьте: "Создай файл hello.py с простым примером кода"
- Ожидается: код + автоматическая отправка созданного файла

## Проверка безопасности

### Фильтрация по username
1. Попросите другого пользователя (не тот, что указан в `ALLOWED_USERNAME`) отправить сообщение боту
2. Ожидается: бот игнорирует сообщение (нет ответа)

## Отладка

### Просмотр файловых логов
```bash
tail -f ~/claude-telegram-bot/data/logs/bot.log
```

### Проверка сессий
```bash
ls -la ~/claude-telegram-bot/data/sessions/
cat ~/claude-telegram-bot/data/sessions/user_*.json
```

### Проверка workspace
```bash
ls -la ~/claude-telegram-bot/workspace/user_*/
```

### Проверка медиафайлов
```bash
ls -la ~/claude-telegram-bot/data/media/user_*/
```

## Перезапуск бота

```bash
# Остановить
sudo systemctl stop claude-telegram-bot.service

# Запустить
sudo systemctl start claude-telegram-bot.service

# Перезапустить
sudo systemctl restart claude-telegram-bot.service
```

## Ручной запуск (для отладки)

```bash
cd ~/claude-telegram-bot
source ~/envs/claude-bot-env/bin/activate
python -m src.main
```

## Проверка интеграции с Claude

```bash
# Проверить, что Claude CLI доступен
claude --version

# Тестовый запуск
cd ~/claude-telegram-bot/workspace
claude -p --session-id "test-session" "Hello, Claude!"
```

## Мониторинг ресурсов

```bash
# Использование памяти и CPU
ps aux | grep python | grep claude

# Размер логов
du -sh ~/claude-telegram-bot/data/logs/

# Размер workspace
du -sh ~/claude-telegram-bot/workspace/
```
