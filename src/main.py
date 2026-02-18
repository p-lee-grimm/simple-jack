"""Main entry point for the Telegram bot."""

from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    filters
)
from telegram.request import HTTPXRequest
from config.settings import settings
from src.bot.handlers import (
    start_command,
    help_command,
    reset_command,
    switch_command,
    text_message_handler,
    photo_handler,
    document_handler,
    stop_button_callback,
    permission_button_callback,
    question_button_callback,
    download_button_callback,
)
from src.bot.filters import allowed_user_filter
from src.utils.logger import setup_logger


logger = setup_logger(__name__)


def main():
    """Initialize and run the bot."""
    logger.info("Starting Telegram bot...")
    logger.info(f"Allowed username: {settings.allowed_username}")
    logger.info(f"Claude CLI path: {settings.claude_cli_path}")
    logger.info(f"Workspace directory: {settings.workspace_dir}")

    # Create application with retry logic for network errors
    request = HTTPXRequest(
        connection_pool_size=8,
        read_timeout=20.0,
        write_timeout=20.0,
        connect_timeout=10.0,
        pool_timeout=5.0,
    )
    application = (
        Application.builder()
        .token(settings.telegram_bot_token)
        .request(request)
        .concurrent_updates(True)
        .build()
    )

    # Register command handlers (with user filter)
    application.add_handler(
        CommandHandler("start", start_command, filters=allowed_user_filter)
    )
    application.add_handler(
        CommandHandler("help", help_command, filters=allowed_user_filter)
    )
    application.add_handler(
        CommandHandler("reset", reset_command, filters=allowed_user_filter)
    )
    application.add_handler(
        CommandHandler("switch", switch_command, filters=allowed_user_filter)
    )

    # Register callback query handlers (for inline buttons)
    application.add_handler(
        CallbackQueryHandler(stop_button_callback, pattern="^stop_")
    )
    application.add_handler(
        CallbackQueryHandler(permission_button_callback, pattern="^perm_")
    )
    application.add_handler(
        CallbackQueryHandler(question_button_callback, pattern="^q_")
    )
    application.add_handler(
        CallbackQueryHandler(download_button_callback, pattern="^dl_")
    )

    # Register message handlers (with user filter)
    application.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND & allowed_user_filter,
            text_message_handler
        )
    )
    application.add_handler(
        MessageHandler(
            filters.PHOTO & allowed_user_filter,
            photo_handler
        )
    )
    application.add_handler(
        MessageHandler(
            filters.Document.ALL & allowed_user_filter,
            document_handler
        )
    )

    logger.info("Bot initialized, starting polling...")

    # Run the bot
    application.run_polling(allowed_updates=["message", "callback_query"])


if __name__ == "__main__":
    main()
