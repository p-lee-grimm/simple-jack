"""Media file download from Telegram."""

import os
import shutil
from pathlib import Path
from typing import Optional
from telegram import PhotoSize, Document
from PIL import Image
from config.settings import settings
from src.utils.logger import setup_logger


logger = setup_logger(__name__)

MAX_IMAGE_DIMENSION = 1600  # Claude API limit is 2000 for multi-image, keep margin


def resize_image_if_needed(file_path: Path) -> None:
    """Resize image if any dimension exceeds MAX_IMAGE_DIMENSION."""
    try:
        with Image.open(file_path) as img:
            w, h = img.size
            if w <= MAX_IMAGE_DIMENSION and h <= MAX_IMAGE_DIMENSION:
                return

            ratio = min(MAX_IMAGE_DIMENSION / w, MAX_IMAGE_DIMENSION / h)
            new_size = (int(w * ratio), int(h * ratio))
            resized = img.resize(new_size, Image.LANCZOS)
            resized.save(file_path)
            logger.info(f"Resized image {file_path.name}: {w}x{h} -> {new_size[0]}x{new_size[1]}")
    except Exception as e:
        logger.warning(f"Failed to resize image {file_path}: {e}")


async def download_photo(photo: PhotoSize, user_id: int) -> Optional[Path]:
    """
    Download photo from Telegram.

    Args:
        photo: Photo object from Telegram
        user_id: User ID for organizing files

    Returns:
        Path to downloaded file or None if failed
    """
    try:
        # Create user media directory
        user_media_dir = settings.media_dir / f"user_{user_id}"
        user_media_dir.mkdir(parents=True, exist_ok=True)

        # Get file
        file = await photo.get_file()

        # Generate filename
        file_extension = Path(file.file_path).suffix or '.jpg'
        filename = f"photo_{file.file_unique_id}{file_extension}"
        file_path = user_media_dir / filename

        # Download
        await file.download_to_drive(str(file_path))
        logger.info(f"Downloaded photo to {file_path}")

        # Resize if too large for Claude API
        resize_image_if_needed(file_path)

        return file_path

    except Exception as e:
        logger.error(f"Failed to download photo: {e}", exc_info=True)
        return None


async def download_document(document: Document, user_id: int) -> Optional[Path]:
    """
    Download document from Telegram.

    Args:
        document: Document object from Telegram
        user_id: User ID for organizing files

    Returns:
        Path to downloaded file or None if failed
    """
    try:
        # Create user media directory
        user_media_dir = settings.media_dir / f"user_{user_id}"
        user_media_dir.mkdir(parents=True, exist_ok=True)

        # Get file
        file = await document.get_file()

        # Use original filename if available, strip directory components
        raw_name = document.file_name or f"document_{file.file_unique_id}"
        filename = Path(raw_name).name
        file_path = user_media_dir / filename

        # Download
        await file.download_to_drive(str(file_path))
        logger.info(f"Downloaded document to {file_path}")

        return file_path

    except Exception as e:
        logger.error(f"Failed to download document: {e}", exc_info=True)
        return None


def copy_to_workspace(file_path: Path, user_id: int) -> Optional[Path]:
    """
    Copy file to user's workspace directory.

    Args:
        file_path: Path to file to copy
        user_id: User ID

    Returns:
        Path to file in workspace or None if failed
    """
    try:
        # Create user workspace directory
        user_workspace = Path(settings.workspace_dir) / f"user_{user_id}"
        user_workspace.mkdir(parents=True, exist_ok=True)

        # Copy file
        dest_path = user_workspace / file_path.name
        shutil.copy2(file_path, dest_path)

        logger.info(f"Copied {file_path} to workspace: {dest_path}")
        return dest_path

    except Exception as e:
        logger.error(f"Failed to copy to workspace: {e}", exc_info=True)
        return None
