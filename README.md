# Claude Telegram Bot

Telegram bot for integration with Claude Code CLI.

## Features

- Process text messages via Claude Code
- Support for images and documents
- Conversation context persistence
- Automatic sending of created files
- Long response splitting
- Username filtering

## Project Structure

```
claude-telegram-bot/
├── .env                  # Configuration
├── requirements.txt      # Python dependencies
├── config/
│   └── settings.py      # Application settings
├── src/
│   ├── main.py          # Entry point
│   ├── bot/             # Telegram handlers
│   ├── claude/          # Claude CLI integration
│   ├── media/           # Media file handling
│   └── utils/           # Utilities
├── data/
│   ├── sessions/        # Conversation history
│   ├── media/           # Temporary media files
│   └── logs/            # Logs
└── workspace/           # Working directory for Claude
```

## Installation and Setup

### 1. Prerequisites

- Python 3.13+
- [Claude Code CLI](https://claude.ai/code) — installed and authenticated (`claude --version`)
- Telegram Bot Token — obtain from [@BotFather](https://t.me/BotFather)

### 2. Clone the Repository

```bash
git clone https://github.com/p-lee-grimm/simple-jack.git claude-telegram-bot
cd claude-telegram-bot
```

### 3. Virtual Environment and Dependencies

```bash
python3 -m venv ~/envs/claude-bot-env
source ~/envs/claude-bot-env/bin/activate
pip install -r requirements.txt
```

### 4. Configuration

Create a `.env` file in the project root:

```bash
cp .env.example .env
nano .env
```

Contents of `.env`:

```env
TELEGRAM_BOT_TOKEN=your_bot_token_here
ALLOWED_USERNAME=your_telegram_username
CLAUDE_CLI_PATH=/home/YOUR_USERNAME/.local/bin/claude
WORKSPACE_DIR=/home/YOUR_USERNAME/claude-telegram-bot/workspace
DATA_DIR=/home/YOUR_USERNAME/claude-telegram-bot/data
SESSION_TIMEOUT_HOURS=24
```

Set file permissions:

```bash
chmod 600 .env
```

### 5. Running

**Manually (for development and debugging):**

```bash
source ~/envs/claude-bot-env/bin/activate
python -m src.main
```

**As a systemd service (for production):**

Edit `claude-telegram-bot.service`, replacing `YOUR_USERNAME` with your username, then install the service:

```bash
sudo cp claude-telegram-bot.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable claude-telegram-bot
sudo systemctl start claude-telegram-bot
```

## Bot Commands

- `/start` - greeting and instructions
- `/help` - usage help
- `/reset` - clear conversation history

## Service Management

```bash
# Check status
sudo systemctl status claude-telegram-bot.service

# Stop
sudo systemctl stop claude-telegram-bot.service

# Start
sudo systemctl start claude-telegram-bot.service

# Restart
sudo systemctl restart claude-telegram-bot.service

# Disable autostart
sudo systemctl disable claude-telegram-bot.service

# Enable autostart
sudo systemctl enable claude-telegram-bot.service
```

## Viewing Logs

```bash
# systemd logs
journalctl -u claude-telegram-bot.service -f

# File logs
tail -f ~/claude-telegram-bot/data/logs/bot.log
```

## Configuration

The `.env` file contains:
- `TELEGRAM_BOT_TOKEN` - Telegram bot token
- `ALLOWED_USERNAME` - allowed username
- `CLAUDE_CLI_PATH` - path to Claude CLI
- `WORKSPACE_DIR` - working directory
- `DATA_DIR` - data directory
- `SESSION_TIMEOUT_HOURS` - session timeout

## Technologies

- Python 3.13
- python-telegram-bot 21.0
- pydantic 2.12+
- aiofiles
- Claude Code CLI

## Security

- Username filtering at the MessageFilter level
- Bot token stored in .env with 600 permissions
- Isolated workspaces per user
- Logging of all actions
