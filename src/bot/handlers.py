"""Telegram bot message handlers."""

import asyncio
import json
import uuid
from typing import List, Dict, Any, Optional
from telegram import Update, Message, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from telegram.error import BadRequest, NetworkError
from src.claude.executor import execute_claude
from src.claude.session_manager import session_manager
from src.claude.permission_handler import permission_manager
from src.claude.question_handler import question_manager
from src.media.downloader import download_photo, download_document, copy_to_workspace
from src.utils.message_splitter import split_message
from src.utils.markdown_to_html import safe_markdown_to_html
from config.settings import settings
from src.utils.logger import setup_logger


logger = setup_logger(__name__)


# Telegram message size limit
TELEGRAM_MAX_MESSAGE_LENGTH = 4096

# Store active stop events by message ID
active_stop_events = {}

# Store file paths pending download (download_id -> list of Path objects)
pending_downloads: Dict[str, list] = {}

# Per-user locks to serialize message processing
_user_locks: Dict[int, asyncio.Lock] = {}


def _get_user_lock(user_id: int) -> asyncio.Lock:
    """Get or create an asyncio lock for a specific user."""
    if user_id not in _user_locks:
        _user_locks[user_id] = asyncio.Lock()
    return _user_locks[user_id]


async def _reply_with_retry(message: Message, text: str, **kwargs) -> None:
    """Send a reply with automatic retry on network errors."""
    for attempt in range(3):
        try:
            await message.reply_text(text, **kwargs)
            return
        except NetworkError as e:
            if attempt < 2:
                logger.warning(f"Network error sending reply (attempt {attempt + 1}/3): {e}, retrying...")
                await asyncio.sleep(2 ** attempt)
            else:
                logger.error(f"Failed to send reply after 3 attempts: {e}")
                raise


def create_streaming_callback(status_message: Message, stop_event: asyncio.Event):
    """Create a callback function for streaming Claude output updates."""
    last_text = ""

    async def update_callback(text: str):
        nonlocal last_text

        if text == last_text:
            return

        last_text = text

        display_text = text
        if len(text) > TELEGRAM_MAX_MESSAGE_LENGTH - 100:
            display_text = text[:TELEGRAM_MAX_MESSAGE_LENGTH - 100] + "\n\n... (продолжение следует)"

        try:
            keyboard = InlineKeyboardMarkup([
                [InlineKeyboardButton("🛑 Прекратить", callback_data=f"stop_{status_message.message_id}")]
            ])

            await status_message.edit_text(
                f"⏳ Обрабатываю запрос...\n\n{display_text}",
                reply_markup=keyboard,
                disable_web_page_preview=True
            )
        except BadRequest as e:
            if "message is not modified" not in str(e).lower():
                logger.warning(f"Failed to update streaming message: {e}")
        except Exception as e:
            logger.warning(f"Unexpected error updating streaming message: {e}")

    return update_callback


async def stop_button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle stop button press."""
    query = update.callback_query
    if query.from_user.username != settings.allowed_username:
        await query.answer("⛔ Доступ запрещён.")
        return
    await query.answer()

    callback_data = query.data
    if not callback_data.startswith("stop_"):
        return

    raw_id = callback_data.split("_", 1)[1]
    if not raw_id.isdigit():
        # Clicked before real message ID was set (stop_temp)
        return

    message_id = int(raw_id)

    if message_id in active_stop_events:
        stop_event = active_stop_events[message_id]
        stop_event.set()
        logger.info(f"Stop event set for message {message_id}")

        try:
            await query.edit_message_text(
                "🛑 Остановка выполнения...",
                reply_markup=None
            )
        except BadRequest:
            pass


async def permission_button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle permission approve/deny button press."""
    query = update.callback_query
    if query.from_user.username != settings.allowed_username:
        await query.answer("⛔ Доступ запрещён.")
        return
    await query.answer()

    data = query.data
    if data.startswith("perm_approve_"):
        request_id = data[len("perm_approve_"):]
        approved = True
    elif data.startswith("perm_deny_"):
        request_id = data[len("perm_deny_"):]
        approved = False
    else:
        return

    resolved = permission_manager.resolve(request_id, approved)

    if resolved:
        status_icon = "✅" if approved else "❌"
        status_text = "Одобрено" if approved else "Отклонено"
        try:
            original_text = query.message.text or ""
            await query.edit_message_text(
                f"{original_text}\n\n{status_icon} {status_text}",
                reply_markup=None
            )
        except BadRequest:
            pass
    else:
        try:
            await query.edit_message_text(
                "⚠️ Запрос разрешения уже истёк или был обработан.",
                reply_markup=None
            )
        except BadRequest:
            pass


async def question_button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle question answer button press (single-select, multi-select toggle, done, other)."""
    query = update.callback_query
    if query.from_user.username != settings.allowed_username:
        await query.answer("⛔ Доступ запрещён.")
        return
    data = query.data  # format: q_{request_id}_{question_idx}_{action}

    parts = data.split("_")
    if len(parts) < 4:
        await query.answer()
        return

    request_id = parts[1]
    question_idx = int(parts[2])
    action = "_".join(parts[3:])  # handles "other", "done", or option index

    request = question_manager.get_request(request_id)
    if request is None:
        await query.answer("Вопрос уже обработан или истёк.")
        try:
            await query.edit_message_reply_markup(reply_markup=None)
        except BadRequest:
            pass
        return

    question = request.questions[question_idx] if question_idx < len(request.questions) else None
    if question is None:
        await query.answer()
        return

    is_multi = question.get("multiSelect", False)
    options = question.get("options", [])

    if action == "other":
        # User wants to type a custom answer
        await query.answer("Отправьте текстовый ответ следующим сообщением.")
        chat_id = query.message.chat_id
        question_manager.set_awaiting_text(chat_id, request_id, question_idx)
        try:
            await query.edit_message_text(
                query.message.text + "\n\n⌨️ Ожидаю текстовый ответ...",
                reply_markup=None,
            )
        except BadRequest:
            pass
        return

    if action == "done":
        # Finalize multi-select
        await query.answer()
        all_done = question_manager.finalize_multi_select(request_id, question_idx)
        answer = request.answers.get(question_idx, "(ничего не выбрано)")
        try:
            await query.edit_message_text(
                query.message.text + f"\n\n✅ Ответ: {answer}",
                reply_markup=None,
            )
        except BadRequest:
            pass
        return

    # Numeric option index
    try:
        option_idx = int(action)
    except ValueError:
        await query.answer()
        return

    if option_idx >= len(options):
        await query.answer()
        return

    if is_multi:
        # Toggle selection
        selections = question_manager.toggle_multi_select(request_id, question_idx, option_idx)
        await query.answer()

        # Rebuild keyboard with checkmarks
        keyboard_buttons = []
        for i, opt in enumerate(options):
            label = opt.get("label", f"Option {i+1}")
            prefix = "✅ " if i in selections else ""
            keyboard_buttons.append(
                [InlineKeyboardButton(f"{prefix}{label}", callback_data=f"q_{request_id}_{question_idx}_{i}")]
            )
        keyboard_buttons.append(
            [InlineKeyboardButton("✏️ Другой ответ", callback_data=f"q_{request_id}_{question_idx}_other"),
             InlineKeyboardButton("✔️ Готово", callback_data=f"q_{request_id}_{question_idx}_done")]
        )
        try:
            await query.edit_message_reply_markup(
                reply_markup=InlineKeyboardMarkup(keyboard_buttons)
            )
        except BadRequest:
            pass
    else:
        # Single select — set answer immediately
        await query.answer()
        label = options[option_idx].get("label", f"Option {option_idx+1}")
        question_manager.set_answer(request_id, question_idx, label)
        try:
            await query.edit_message_text(
                query.message.text + f"\n\n✅ Ответ: {label}",
                reply_markup=None,
            )
        except BadRequest:
            pass


async def question_text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle free-text answer for 'Other' question option. Returns True if handled."""
    chat_id = update.message.chat_id
    pending = question_manager.get_awaiting_text(chat_id)
    if pending is None:
        return False

    request_id, question_idx = pending
    question_manager.clear_awaiting_text(chat_id)

    answer_text = update.message.text
    question_manager.set_answer(request_id, question_idx, answer_text)

    await update.message.reply_text(f"✅ Ответ принят: {answer_text}")
    return True


def create_question_callback(chat, reply_to_message_id: int, tracked_ids: set):
    """
    Create a question callback for the executor.

    Sends each question as a Telegram message with inline keyboard buttons.
    Returns answers dict or None on timeout/cancel.
    """
    async def on_question(tool_input: Dict[str, Any]) -> Dict[int, str]:
        questions = tool_input.get("questions", [])
        if not questions:
            return {}

        request_id = str(uuid.uuid4())[:8]
        tracked_ids.add(request_id)
        request = question_manager.create_request(request_id, questions)

        # Send each question as a separate message with buttons
        for idx, question in enumerate(questions):
            q_text = question.get("question", "Вопрос без текста")
            header = question.get("header", "")
            options = question.get("options", [])
            is_multi = question.get("multiSelect", False)

            # Build message text
            msg_lines = []
            if header:
                msg_lines.append(f"❓ [{header}]")
            msg_lines.append(q_text)
            if is_multi:
                msg_lines.append("\n(можно выбрать несколько)")

            # Add option descriptions
            for i, opt in enumerate(options):
                desc = opt.get("description", "")
                if desc:
                    msg_lines.append(f"  {opt.get('label', '')}: {desc}")

            msg_text = "\n".join(msg_lines)

            # Build keyboard
            keyboard_buttons = []
            for i, opt in enumerate(options):
                label = opt.get("label", f"Option {i+1}")
                keyboard_buttons.append(
                    [InlineKeyboardButton(label, callback_data=f"q_{request_id}_{idx}_{i}")]
                )

            if is_multi:
                keyboard_buttons.append(
                    [InlineKeyboardButton("✏️ Другой ответ", callback_data=f"q_{request_id}_{idx}_other"),
                     InlineKeyboardButton("✔️ Готово", callback_data=f"q_{request_id}_{idx}_done")]
                )
            else:
                keyboard_buttons.append(
                    [InlineKeyboardButton("✏️ Другой ответ", callback_data=f"q_{request_id}_{idx}_other")]
                )

            await chat.send_message(
                msg_text,
                reply_markup=InlineKeyboardMarkup(keyboard_buttons),
                reply_to_message_id=reply_to_message_id,
            )

        # Wait for all answers
        try:
            answers = await asyncio.wait_for(request.response_future, timeout=300)
            return answers
        except asyncio.TimeoutError:
            question_manager.cancel(request_id)
            logger.info("Question request timed out")
            return None

    return on_question


def format_tool_description(tool_name: str, tool_input: dict) -> str:
    """Format tool info for permission request display."""
    if tool_name == "Bash":
        cmd = tool_input.get("command", "")
        desc = tool_input.get("description", "")
        if len(cmd) > 500:
            cmd = cmd[:500] + "..."
        text = f"Команда: {cmd}"
        if desc:
            text += f"\nОписание: {desc}"
        return text
    elif tool_name in ("Write", "Edit"):
        path = tool_input.get("file_path", "")
        return f"Файл: {path}"
    elif tool_name == "Read":
        path = tool_input.get("file_path", "")
        return f"Файл: {path}"
    else:
        try:
            input_str = json.dumps(tool_input, indent=2, ensure_ascii=False)
            if len(input_str) > 500:
                input_str = input_str[:500] + "..."
            return f"Параметры:\n{input_str}"
        except Exception:
            return str(tool_input)[:500]


def create_permission_callback(chat, reply_to_message_id: int, tracked_ids: set):
    """
    Create a permission request callback for the executor.

    Sends a single consolidated permission message with all unique tools.
    User approves/denies all at once.
    """
    async def on_permission_request(denials: List[Dict[str, Any]]) -> List[str]:
        # Deduplicate by tool_name, keep first example of each
        seen = {}
        for denial in denials:
            tool_name = denial.get("tool_name", "Unknown")
            if tool_name not in seen:
                seen[tool_name] = denial

        # Build consolidated description
        lines = []
        for tool_name, denial in seen.items():
            tool_input = denial.get("tool_input", {})
            desc = format_tool_description(tool_name, tool_input)
            lines.append(f"• {tool_name}: {desc}")

        request_id = str(uuid.uuid4())[:8]
        tracked_ids.add(request_id)
        unique_tools = list(seen.keys())

        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("✅ Одобрить все", callback_data=f"perm_approve_{request_id}"),
                InlineKeyboardButton("❌ Отклонить", callback_data=f"perm_deny_{request_id}"),
            ]
        ])

        # Split description into messages if too long
        header = "🔐 Запрос разрешений\n\n"
        max_len = TELEGRAM_MAX_MESSAGE_LENGTH - 100  # reserve space

        # Build chunks, accounting for header in first chunk
        chunks = []
        current_chunk = ""
        first_chunk_max = max_len - len(header)
        for line in lines:
            # Truncate individual lines that are too long
            if len(line) > max_len:
                line = line[:max_len - 3] + "..."
            limit = first_chunk_max if not chunks and not current_chunk else max_len
            if current_chunk and len(current_chunk) + len(line) + 1 > limit:
                chunks.append(current_chunk)
                current_chunk = line
            else:
                current_chunk = (current_chunk + "\n" + line) if current_chunk else line
        if current_chunk:
            chunks.append(current_chunk)

        try:
            if len(chunks) <= 1:
                perm_text = f"{header}{chunks[0] if chunks else ''}"
                await chat.send_message(
                    perm_text,
                    reply_markup=keyboard,
                    reply_to_message_id=reply_to_message_id,
                )
            else:
                for i, chunk in enumerate(chunks):
                    if i == 0:
                        await chat.send_message(
                            f"{header}{chunk}",
                            reply_to_message_id=reply_to_message_id,
                        )
                    elif i == len(chunks) - 1:
                        await chat.send_message(
                            chunk,
                            reply_markup=keyboard,
                            reply_to_message_id=reply_to_message_id,
                        )
                    else:
                        await chat.send_message(
                            chunk,
                            reply_to_message_id=reply_to_message_id,
                        )
        except BadRequest as e:
            logger.error(f"Failed to send permission request message: {e}")
            # Fallback: send truncated version
            fallback = f"{header}Инструменты: {', '.join(unique_tools)}"
            await chat.send_message(
                fallback,
                reply_markup=keyboard,
                reply_to_message_id=reply_to_message_id,
            )

        request = permission_manager.create_request(
            ",".join(unique_tools), {}, request_id
        )

        try:
            approved = await asyncio.wait_for(request.response_future, timeout=300)
            if approved:
                return unique_tools
        except asyncio.TimeoutError:
            permission_manager.resolve(request_id, False)
            logger.info(f"Permission request timed out for {unique_tools}")

        return []

    return on_permission_request


async def send_response_html(
    status_message: Optional[Message],
    update: Update,
    response_text: str,
):
    """Send final response as a NEW message (preserves chat history).

    The streaming status message is deleted (if provided) and the final
    formatted response is sent as a new message so it persists in chat history.
    """
    html_text, parse_mode = safe_markdown_to_html(response_text)

    logger.debug(f"send_response_html: input_len={len(response_text)}, html_len={len(html_text)}, has_backtick={'`' in response_text}, has_code_tag={'<code>' in html_text}")

    # Delete the streaming status message if provided
    if status_message:
        try:
            await status_message.delete()
        except Exception:
            pass

    # Send final response as new message(s) — they persist in chat history
    chunks = split_message(html_text, is_html=True)
    for chunk in chunks:
        if not chunk.strip():
            continue
        try:
            await _reply_with_retry(
                update.message,
                chunk,
                parse_mode="HTML",
                disable_web_page_preview=True,
            )
        except BadRequest as e:
            logger.warning(f"HTML send failed (BadRequest): {e}")
            # Fallback: send as plain text
            plain_chunks = split_message(response_text)
            for pc in plain_chunks:
                if pc.strip():
                    await _reply_with_retry(update.message, pc)
            return


def _human_size(size_bytes: int) -> str:
    """Return human-readable file size."""
    for unit in ("B", "KB", "MB"):
        if size_bytes < 1024:
            return f"{size_bytes:.0f} {unit}" if unit == "B" else f"{size_bytes:.1f} {unit}"
        size_bytes /= 1024
    return f"{size_bytes:.1f} GB"


async def send_created_files(update: Update, created_files):
    """Show a summary of created files with a Download button."""
    if not created_files:
        return

    valid_files = []
    warnings = []
    for file_path in created_files:
        try:
            size = file_path.stat().st_size
            if size > 50 * 1024 * 1024:
                warnings.append(f"⚠️ {file_path.name} — слишком большой (> 50 MB)")
            else:
                valid_files.append((file_path, size))
        except Exception as e:
            logger.error(f"Failed to stat file {file_path}: {e}")
            warnings.append(f"⚠️ {file_path.name} — ошибка доступа")

    lines = ["📎 <b>Созданные файлы:</b>\n"]
    for fp, size in valid_files:
        lines.append(f"  • <code>{fp.name}</code>  ({_human_size(size)})")
    for w in warnings:
        lines.append(w)

    if not valid_files:
        # Only warnings, no downloadable files
        await update.message.reply_text("\n".join(lines), parse_mode="HTML")
        return

    download_id = str(uuid.uuid4())[:8]
    pending_downloads[download_id] = [fp for fp, _ in valid_files]

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton(
            f"📥 Скачать ({len(valid_files)})",
            callback_data=f"dl_{download_id}",
        )]
    ])

    await update.message.reply_text(
        "\n".join(lines),
        parse_mode="HTML",
        reply_markup=keyboard,
    )


async def send_tool_activity_log(update: Update, tool_actions):
    """Send a compact summary of tools Claude used during execution."""
    if not tool_actions:
        return

    # Group by tool name, deduplicate
    tool_icons = {
        "Bash": "⚙️",
        "Read": "📄",
        "Write": "✏️",
        "Edit": "✏️",
        "Glob": "🔍",
        "Grep": "🔍",
        "Task": "📦",
    }

    lines = []
    for action in tool_actions:
        icon = tool_icons.get(action.tool_name, "🔧")
        summary = action.summary
        if len(summary) > 60:
            summary = summary[:57] + "..."
        lines.append(f"  {icon} <code>{action.tool_name}</code>: {summary}")

    if len(lines) > 15:
        lines = lines[:15]
        lines.append(f"  ... и ещё {len(tool_actions) - 15} действий")

    msg = "📝 <b>Действия:</b>\n" + "\n".join(lines)

    if len(msg) > TELEGRAM_MAX_MESSAGE_LENGTH:
        msg = msg[:TELEGRAM_MAX_MESSAGE_LENGTH - 3] + "..."

    # Remove surrogate characters that break UTF-8 encoding
    msg = msg.encode('utf-8', errors='replace').decode('utf-8')

    try:
        await _reply_with_retry(update.message, msg, parse_mode="HTML")
    except (BadRequest, Exception) as e:
        logger.warning(f"Failed to send tool activity log: {e}")


async def download_button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle download button press — send stored files."""
    query = update.callback_query
    if query.from_user.username != settings.allowed_username:
        await query.answer("⛔ Доступ запрещён.")
        return
    await query.answer()

    download_id = query.data[3:]  # strip "dl_"
    files = pending_downloads.pop(download_id, None)

    if files is None:
        try:
            original_text = query.message.text_html or query.message.text or ""
            await query.edit_message_text(
                original_text + "\n\n⚠️ Файлы уже были скачаны или ссылка устарела.",
                parse_mode="HTML",
                reply_markup=None,
            )
        except BadRequest:
            pass
        return

    for file_path in files:
        try:
            with open(file_path, 'rb') as f:
                await query.message.reply_document(
                    document=f,
                    filename=file_path.name,
                    caption=f"📎 {file_path.name}",
                )
            logger.info(f"Sent created file: {file_path}")
        except Exception as e:
            logger.error(f"Failed to send file {file_path}: {e}", exc_info=True)
            await query.message.reply_text(
                f"⚠️ Не удалось отправить файл {file_path.name}"
            )

    try:
        original_text = query.message.text_html or query.message.text or ""
        await query.edit_message_text(
            original_text + "\n\n✅ Отправлено",
            parse_mode="HTML",
            reply_markup=None,
        )
    except BadRequest:
        pass


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /start command."""
    welcome_message = (
        "👋 Привет! Я бот для работы с Claude Code.\n\n"
        "Отправьте мне любое сообщение, и я передам его Claude для обработки.\n\n"
        "Я поддерживаю:\n"
        "• Текстовые сообщения\n"
        "• Изображения\n"
        "• Документы\n"
        "• Сохранение контекста диалога\n\n"
        "Команды:\n"
        "/start - показать это сообщение\n"
        "/help - справка\n"
        "/reset - начать новый диалог\n"
        "/switch - список сессий / переключение"
    )
    await update.message.reply_text(welcome_message)


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /help command."""
    help_message = (
        "📖 <b>Справка по использованию бота</b>\n\n"
        "<b>Возможности:</b>\n"
        "🔹 Текстовые сообщения, изображения, документы\n"
        "🔹 Контекст диалога сохраняется в сессии\n"
        "🔹 Созданные Claude файлы отправляются вам\n"
        "🔹 Запросы разрешений и вопросы от Claude — через кнопки\n"
        "🔹 Кнопка «Прекратить» для остановки выполнения\n\n"
        "<b>Команды:</b>\n"
        "/reset — новый диалог (старая сессия сохраняется)\n"
        "/switch — список сессий\n"
        "/switch <code>&lt;id&gt;</code> — переключиться на сессию\n"
        "/help — эта справка"
    )
    await update.message.reply_text(help_message, parse_mode="HTML")


async def reset_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /reset command."""
    user_id = update.effective_user.id

    session = session_manager.reset_session(user_id)

    logger.info(f"Session reset for user {user_id}, new session: {session.session_id}")

    await update.message.reply_text(
        "✅ Начинаем новую сессию!\n"
        f"ID: <code>{session.session_id}</code>\n\n"
        "Предыдущие сессии сохранены — /switch для списка.",
        parse_mode="HTML",
    )


async def switch_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /switch command — list sessions or switch to one."""
    user_id = update.effective_user.id
    args = context.args  # text after /switch

    if args:
        # Switch to specific session
        target = args[0]
        session = session_manager.switch_session(user_id, target)
        if session:
            preview = session.preview()
            msg_count = len(session.messages)
            await update.message.reply_text(
                f"✅ Переключено на сессию <code>{session.session_id}</code>\n"
                f"Сообщений: {msg_count}\n"
                f"Превью: {preview}",
                parse_mode="HTML",
            )
        else:
            await update.message.reply_text(
                f"❌ Сессия с ID <code>{target}</code> не найдена.\n"
                "Используйте /switch без аргументов для списка.",
                parse_mode="HTML",
            )
        return

    # List all sessions
    sessions = session_manager.list_sessions(user_id)

    if not sessions:
        await update.message.reply_text("Нет сохранённых сессий.")
        return

    lines = ["📋 <b>Ваши сессии:</b>\n"]
    for i, (sess, is_active) in enumerate(sessions):
        marker = "▶️ " if is_active else "  "
        created = sess.created_at.strftime("%d.%m %H:%M")
        msg_count = len(sess.messages)
        preview = sess.preview()
        short_id = sess.session_id[:8]

        lines.append(
            f"{marker}<code>{short_id}</code>  {created}  ({msg_count} сообщ.)\n"
            f"    {preview}"
        )

    lines.append(
        "\nПереключить: /switch <code>&lt;id&gt;</code> (достаточно первых символов)"
    )

    await update.message.reply_text(
        "\n".join(lines),
        parse_mode="HTML",
    )


async def _handle_claude_request(
    update: Update,
    status_message: Message,
    stop_event: asyncio.Event,
    user_message: str,
    continue_session: bool,
    user_id: int,
    session,
    empty_response_text: str = "✅ Claude выполнил команду без текстового ответа.",
):
    """Common logic for handling a Claude request with permissions and HTML formatting."""
    perm_request_ids = set()
    question_request_ids = set()

    streaming_callback = create_streaming_callback(status_message, stop_event)
    permission_callback = create_permission_callback(
        update.message.chat,
        update.message.message_id,
        perm_request_ids,
    )
    question_callback = create_question_callback(
        update.message.chat,
        update.message.message_id,
        question_request_ids,
    )

    try:
        response = await execute_claude(
            message=user_message,
            session_id=session.session_id,
            user_id=user_id,
            continue_session=continue_session,
            on_output_update=streaming_callback,
            stop_event=stop_event,
            on_permission_request=permission_callback,
            on_question=question_callback,
        )

        logger.info(f"execute_claude returned: error={response.error!r}, text_len={len(response.text or '')}, files={len(response.created_files)}")

        # Always delete the streaming status message and send final as new message
        try:
            await status_message.delete()
            logger.debug("Status message deleted")
        except Exception as e:
            logger.debug(f"Could not delete status message: {e}")

        if response.error and response.error != "Остановлено пользователем":
            error_msg = f"❌ Ошибка при выполнении Claude:\n\n{response.error}"
            await _reply_with_retry(update.message, error_msg)
            return

        if response.error == "Остановлено пользователем":
            if response.text and response.text.strip():
                await send_response_html(None, update, response.text)
            else:
                await _reply_with_retry(update.message, "🛑 Выполнение остановлено.")
            return

        session.add_message("user", user_message)
        session.add_message("assistant", response.text)
        session_manager.save_session(session)

        if response.text and response.text.strip():
            logger.info(f"Sending response to user ({len(response.text)} chars)")
            await send_response_html(None, update, response.text)
            logger.info("Response sent successfully")
        else:
            logger.info("Empty response, sending placeholder")
            await _reply_with_retry(update.message, empty_response_text)

        await send_created_files(update, response.created_files)
        await send_tool_activity_log(update, response.tool_actions)
        logger.info("_handle_claude_request complete")
    finally:
        for rid in perm_request_ids:
            permission_manager.cancel(rid)
        for rid in question_request_ids:
            question_manager.cancel(rid)


async def text_message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle text messages."""
    # Check if this message is a free-text answer to a question "Other" option
    handled = await question_text_handler(update, context)
    if handled:
        return

    user_id = update.effective_user.id
    user_message = update.message.text

    logger.info(f"Received message from user {user_id}: {user_message[:100]}...")

    user_lock = _get_user_lock(user_id)
    if user_lock.locked():
        await update.message.reply_text("⏳ Предыдущий запрос ещё выполняется, подождите...")
        return

    async with user_lock:
        await update.message.chat.send_action("typing")

        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("🛑 Прекратить", callback_data="stop_temp")]
        ])
        status_message = await update.message.reply_text(
            "⏳ Обрабатываю запрос...",
            reply_markup=keyboard
        )

        stop_event = asyncio.Event()
        active_stop_events[status_message.message_id] = stop_event

        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("🛑 Прекратить", callback_data=f"stop_{status_message.message_id}")]
        ])
        await status_message.edit_reply_markup(reply_markup=keyboard)

        try:
            session = session_manager.get_session(user_id)
            continue_session = len(session.messages) > 0

            await _handle_claude_request(
                update, status_message, stop_event,
                user_message, continue_session, user_id, session,
            )

        except Exception as e:
            logger.error(f"Error handling text message: {e}", exc_info=True)
            try:
                await _reply_with_retry(update.message, "❌ Произошла ошибка при обработке сообщения.")
            except Exception:
                pass  # If we can't send the error, log it and move on
        finally:
            if status_message.message_id in active_stop_events:
                del active_stop_events[status_message.message_id]


async def photo_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle photo messages."""
    user_id = update.effective_user.id
    caption = update.message.caption or "Посмотри на это изображение"

    logger.info(f"Received photo from user {user_id}")

    user_lock = _get_user_lock(user_id)
    if user_lock.locked():
        await update.message.reply_text("⏳ Предыдущий запрос ещё выполняется, подождите...")
        return

    async with user_lock:
        await update.message.chat.send_action("typing")

        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("🛑 Прекратить", callback_data="stop_temp")]
        ])
        status_message = await update.message.reply_text(
            "⏳ Обрабатываю изображение...",
            reply_markup=keyboard
        )

        stop_event = asyncio.Event()
        active_stop_events[status_message.message_id] = stop_event

        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("🛑 Прекратить", callback_data=f"stop_{status_message.message_id}")]
        ])
        await status_message.edit_reply_markup(reply_markup=keyboard)

        try:
            photo = update.message.photo[-1]
            file_path = await download_photo(photo, user_id)
            if not file_path:
                await update.message.reply_text("❌ Не удалось скачать изображение")
                return

            workspace_path = copy_to_workspace(file_path, user_id)
            if not workspace_path:
                await update.message.reply_text("❌ Не удалось скопировать изображение в рабочую директорию")
                return

            session = session_manager.get_session(user_id)
            claude_message = f"{caption}\n\nФайл изображения: {workspace_path.name}"
            continue_session = len(session.messages) > 0

            await _handle_claude_request(
                update, status_message, stop_event,
                claude_message, continue_session, user_id, session,
                empty_response_text="✅ Claude обработал изображение.",
            )

        except Exception as e:
            logger.error(f"Error handling photo: {e}", exc_info=True)
            try:
                await _reply_with_retry(update.message, "❌ Произошла ошибка при обработке изображения.")
            except Exception:
                pass
        finally:
            if status_message.message_id in active_stop_events:
                del active_stop_events[status_message.message_id]


async def document_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle document messages."""
    user_id = update.effective_user.id
    caption = update.message.caption or "Посмотри на этот документ"

    logger.info(f"Received document from user {user_id}: {update.message.document.file_name}")

    user_lock = _get_user_lock(user_id)
    if user_lock.locked():
        await update.message.reply_text("⏳ Предыдущий запрос ещё выполняется, подождите...")
        return

    async with user_lock:
        await update.message.chat.send_action("typing")

        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("🛑 Прекратить", callback_data="stop_temp")]
        ])
        status_message = await update.message.reply_text(
            "⏳ Обрабатываю документ...",
            reply_markup=keyboard
        )

        stop_event = asyncio.Event()
        active_stop_events[status_message.message_id] = stop_event

        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("🛑 Прекратить", callback_data=f"stop_{status_message.message_id}")]
        ])
        await status_message.edit_reply_markup(reply_markup=keyboard)

        try:
            file_path = await download_document(update.message.document, user_id)
            if not file_path:
                await update.message.reply_text("❌ Не удалось скачать документ")
                return

            workspace_path = copy_to_workspace(file_path, user_id)
            if not workspace_path:
                await update.message.reply_text("❌ Не удалось скопировать документ в рабочую директорию")
                return

            session = session_manager.get_session(user_id)
            claude_message = f"{caption}\n\nФайл документа: {workspace_path.name}"
            continue_session = len(session.messages) > 0

            await _handle_claude_request(
                update, status_message, stop_event,
                claude_message, continue_session, user_id, session,
                empty_response_text="✅ Claude обработал документ.",
            )

        except Exception as e:
            logger.error(f"Error handling document: {e}", exc_info=True)
            try:
                await _reply_with_retry(update.message, "❌ Произошла ошибка при обработке документа.")
            except Exception:
                pass
        finally:
            if status_message.message_id in active_stop_events:
                del active_stop_events[status_message.message_id]
