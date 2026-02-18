"""Custom Telegram filters."""

from telegram import Update
from telegram.ext import filters
from config.settings import settings


class AllowedUserFilter(filters.MessageFilter):
    """Filter that only allows messages from the configured username."""

    def filter(self, message):
        """Check if message is from allowed user."""
        if not message.from_user:
            return False
        return message.from_user.username == settings.allowed_username


# Create filter instance
allowed_user_filter = AllowedUserFilter()
