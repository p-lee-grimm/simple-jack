"""Claude CLI executor with stream-json parsing and permission handling."""

import asyncio
import json
import os
import time
from pathlib import Path
from typing import List, Optional, Set, Callable, Awaitable, Dict, Any
from dataclasses import dataclass
from config.settings import settings
from src.utils.logger import setup_logger


async def retry_async(func, max_retries=3, delay=2.0, backoff=2.0):
    """Retry an async function with exponential backoff."""
    for attempt in range(max_retries):
        try:
            return await func()
        except Exception as e:
            if attempt == max_retries - 1:
                raise
            logger.warning(f"Retry {attempt + 1}/{max_retries} failed: {e}, retrying in {delay}s...")
            await asyncio.sleep(delay)
            delay *= backoff


logger = setup_logger(__name__)

MAX_PERMISSION_RETRIES = 5
MAX_PROCESS_WALL_CLOCK = 30 * 60  # 30 minutes overall timeout per process

# Semaphore to limit concurrent Claude CLI processes (prevent OOM)
_claude_semaphore = asyncio.Semaphore(2)


@dataclass
class ToolAction:
    """A tool action performed by Claude."""
    tool_name: str
    summary: str


@dataclass
class ClaudeResponse:
    """Response from Claude CLI."""
    text: str
    created_files: List[Path]
    exit_code: int
    error: Optional[str] = None
    tool_actions: List[ToolAction] = None


_SKIP_DIRS = {'.git', 'node_modules', '__pycache__', '.venv', '.claude'}


def get_workspace_files(workspace_dir: Path) -> Set[Path]:
    """Get set of all files in workspace directory, skipping large/internal dirs."""
    if not workspace_dir.exists():
        return set()

    files = set()
    for dirpath, dirnames, filenames in os.walk(workspace_dir):
        dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS]
        for fname in filenames:
            files.add(Path(dirpath) / fname)
    return files


def _extract_text_from_events(events: list) -> str:
    """Extract accumulated text from stream-json events."""
    text_parts = []
    for event in events:
        if event.get("type") == "assistant":
            message = event.get("message", {})
            for block in message.get("content", []):
                if block.get("type") == "text":
                    text_parts.append(block["text"])
        elif event.get("type") == "result":
            # Use result text if available and we have no assistant text
            result_text = event.get("result", "")
            if result_text and not text_parts:
                text_parts.append(result_text)
    return "".join(text_parts)


def _extract_tool_actions(events: list) -> List[ToolAction]:
    """Extract tool use actions from assistant events for activity log."""
    actions = []
    for event in events:
        if event.get("type") != "assistant":
            continue
        message = event.get("message", {})
        for block in message.get("content", []):
            if block.get("type") != "tool_use":
                continue
            tool_name = block.get("name", "")
            tool_input = block.get("input", {})

            if tool_name == "Bash":
                cmd = tool_input.get("command", "")
                desc = tool_input.get("description", "")
                summary = desc if desc else (cmd[:80] + "..." if len(cmd) > 80 else cmd)
            elif tool_name in ("Read", "Glob", "Grep"):
                path = tool_input.get("file_path", "") or tool_input.get("path", "")
                pattern = tool_input.get("pattern", "")
                summary = path or pattern
            elif tool_name in ("Write", "Edit"):
                path = tool_input.get("file_path", "")
                summary = path
            elif tool_name == "Task":
                summary = tool_input.get("description", "subtask")
            else:
                continue  # Skip less interesting tools

            if summary:
                actions.append(ToolAction(tool_name=tool_name, summary=summary))
    return actions


def _get_permission_denials(events: list) -> List[Dict[str, Any]]:
    """Extract permission denials from result event."""
    for event in events:
        if event.get("type") == "result":
            return event.get("permission_denials", [])
    return []


def _get_result_text(events: list) -> str:
    """Extract result text from the result event."""
    for event in events:
        if event.get("type") == "result":
            return event.get("result", "") or ""
    return ""


async def _run_claude_process(
    cmd: list,
    workspace_dir: Path,
    on_output_update: Optional[Callable[[str], Awaitable[None]]],
    stop_event: Optional[asyncio.Event],
) -> tuple:
    """
    Run Claude CLI subprocess and parse stream-json output.

    The prompt message must already be included in cmd as a positional argument.

    Returns:
        (events, accumulated_text, exit_code, error, was_stopped)
    """
    await _claude_semaphore.acquire()
    logger.debug(f"Acquired Claude semaphore (available: {_claude_semaphore._value})")
    try:
        return await _run_claude_process_inner(cmd, workspace_dir, on_output_update, stop_event)
    finally:
        _claude_semaphore.release()
        logger.debug(f"Released Claude semaphore (available: {_claude_semaphore._value})")


async def _run_claude_process_inner(
    cmd: list,
    workspace_dir: Path,
    on_output_update: Optional[Callable[[str], Awaitable[None]]],
    stop_event: Optional[asyncio.Event],
) -> tuple:
    """Inner implementation of _run_claude_process (without semaphore)."""
    process = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=str(workspace_dir),
        limit=10 * 1024 * 1024,  # 10MB buffer for long JSON lines
    )

    events = []
    accumulated_text = ""
    stderr_lines = []
    last_update_time = 0
    last_output_time = time.time()  # Track when we last got output
    update_interval = 2.0
    read_timeout = 600.0
    heartbeat_interval = 120.0  # Send "thinking" message every 2 minutes
    was_stopped = False

    async def heartbeat_task():
        """Send periodic 'thinking' updates when no output for a while."""
        nonlocal last_output_time
        try:
            while True:
                await asyncio.sleep(heartbeat_interval)
                if stop_event and stop_event.is_set():
                    break

                time_since_output = time.time() - last_output_time
                if time_since_output >= heartbeat_interval and on_output_update:
                    try:
                        minutes = int(time_since_output / 60)
                        thinking_msg = accumulated_text + f"\n\n[⏳ Думаю уже {minutes} мин...]"
                        await on_output_update(thinking_msg)
                    except Exception as e:
                        logger.warning(f"Error in heartbeat callback: {e}")
        except asyncio.CancelledError:
            pass

    async def read_stdout():
        nonlocal accumulated_text, last_update_time, last_output_time, was_stopped
        while True:
            if stop_event and stop_event.is_set():
                logger.info("Stop requested by user")
                was_stopped = True
                break

            try:
                line = await asyncio.wait_for(
                    process.stdout.readline(),
                    timeout=read_timeout
                )
                if not line:
                    break

                decoded_line = line.decode('utf-8', errors='replace').strip()
                if not decoded_line:
                    continue

                last_output_time = time.time()  # Update output timestamp

                try:
                    event = json.loads(decoded_line)
                    events.append(event)

                    # Extract text from assistant messages for streaming
                    if event.get("type") == "assistant":
                        message_obj = event.get("message", {})
                        for block in message_obj.get("content", []):
                            if block.get("type") == "text":
                                accumulated_text += block["text"]

                                if on_output_update:
                                    current_time = time.time()
                                    if current_time - last_update_time >= update_interval:
                                        try:
                                            await on_output_update(accumulated_text)
                                            last_update_time = current_time
                                        except Exception as e:
                                            logger.warning(f"Error in output update callback: {e}")
                except json.JSONDecodeError:
                    logger.debug(f"Non-JSON line from Claude: {decoded_line[:200]}")

            except asyncio.TimeoutError:
                logger.error("No output from Claude CLI for 10 minutes")
                raise

    async def read_stderr():
        while True:
            try:
                line = await asyncio.wait_for(
                    process.stderr.readline(),
                    timeout=read_timeout
                )
                if not line:
                    break
                stderr_lines.append(line.decode('utf-8', errors='replace'))
            except asyncio.TimeoutError:
                break

    # Start heartbeat task
    heartbeat = asyncio.create_task(heartbeat_task())

    try:
        results = await asyncio.wait_for(
            asyncio.gather(read_stdout(), read_stderr(), return_exceptions=True),
            timeout=MAX_PROCESS_WALL_CLOCK,
        )
        # Cancel heartbeat when done
        heartbeat.cancel()
        try:
            await heartbeat
        except asyncio.CancelledError:
            pass

        # Re-raise all exceptions from read_stdout (including timeout)
        for r in results:
            if isinstance(r, Exception):
                # TimeoutError means no output for 10 minutes - kill process
                process.kill()
                await process.wait()
                if isinstance(r, asyncio.TimeoutError):
                    return events, accumulated_text, -1, "Claude CLI не отвечает (10 минут без вывода)", False
                raise r
    except asyncio.TimeoutError:
        # Overall 30-minute wall clock timeout
        heartbeat.cancel()
        try:
            await heartbeat
        except asyncio.CancelledError:
            pass
        process.kill()
        await process.wait()
        return events, accumulated_text, -1, "Claude CLI превысил лимит времени (30 минут)", False

    if was_stopped:
        process.kill()
        await process.wait()
        if on_output_update and accumulated_text:
            try:
                await on_output_update(accumulated_text)
            except Exception as e:
                logger.warning(f"Error in final output update callback: {e}")
        stderr_text = ''.join(stderr_lines)
        return events, accumulated_text, -1, "Остановлено пользователем", True

    await process.wait()

    if on_output_update and accumulated_text:
        try:
            await on_output_update(accumulated_text)
        except Exception as e:
            logger.warning(f"Error in final output update callback: {e}")

    stderr_text = ''.join(stderr_lines)
    if stderr_text:
        logger.warning(f"Claude CLI stderr: {stderr_text}")

    logger.info(f"Claude CLI exit code: {process.returncode}")

    error = None
    if process.returncode != 0:
        error = stderr_text
        # If stderr is empty, check for errors in JSON result event
        if not error:
            for event in events:
                if event.get("type") == "result" and event.get("is_error"):
                    errors = event.get("errors", [])
                    error = "; ".join(errors) if errors else "Unknown error"
                    break
        if not error:
            error = f"Claude CLI exited with code {process.returncode}"
    return events, accumulated_text, process.returncode, error, False


async def execute_claude(
    message: str,
    session_id: str,
    user_id: int,
    continue_session: bool = False,
    on_output_update: Optional[Callable[[str], Awaitable[None]]] = None,
    stop_event: Optional[asyncio.Event] = None,
    on_permission_request: Optional[Callable[[List[Dict[str, Any]]], Awaitable[List[str]]]] = None,
    on_question: Optional[Callable[[Dict[str, Any]], Awaitable[Optional[Dict[int, str]]]]] = None,
    pre_approved_tools: Optional[Set[str]] = None,
) -> ClaudeResponse:
    """
    Execute Claude CLI with the given message.

    Args:
        message: User message to send to Claude
        session_id: Session ID for conversation continuity
        user_id: User ID for workspace organization
        continue_session: Whether to continue existing session
        on_output_update: Optional callback for streaming output updates
        stop_event: Optional event to signal execution should stop
        on_permission_request: Optional callback for permission requests.
            Receives list of denied tools, returns list of approved tool names.
        on_question: Optional callback for AskUserQuestion tool.
            Receives tool_input dict, returns dict of question_idx->answer or None.

    Returns:
        ClaudeResponse object
    """
    try:
        workspace_dir = Path(settings.workspace_dir) / f"user_{user_id}"
        workspace_dir.mkdir(parents=True, exist_ok=True)

        files_before = await asyncio.to_thread(get_workspace_files, workspace_dir)

        # Safe tools that don't need user approval
        SAFE_TOOLS = "Read,Glob,Grep,Explore,Task,TaskOutput"

        # Combine safe tools with any pre-approved tools (session or always)
        initial_tools = list(dict.fromkeys(
            SAFE_TOOLS.split(",") + (list(pre_approved_tools) if pre_approved_tools else [])
        ))

        # Build base command
        cmd = [
            settings.claude_cli_path,
            "-p",
            "--output-format", "stream-json",
            "--verbose",
            "--allowedTools", ",".join(initial_tools),
        ]

        if continue_session:
            cmd.extend(["--resume", session_id])
        else:
            cmd.extend(["--session-id", session_id])

        cmd.extend(["--", message])

        logger.info(f"Executing Claude CLI (stream-json): user={user_id}, continue={continue_session}")

        # First run
        events, accumulated_text, exit_code, error, was_stopped = await _run_claude_process(
            cmd, workspace_dir, on_output_update, stop_event
        )

        if was_stopped:
            return ClaudeResponse(
                text=accumulated_text,
                created_files=[],
                exit_code=-1,
                error="Остановлено пользователем"
            )

        if error and exit_code != 0:
            # Use result text if we have it, otherwise accumulated text
            result_text = _get_result_text(events) or accumulated_text
            return ClaudeResponse(
                text=result_text,
                created_files=[],
                exit_code=exit_code,
                error=error
            )

        # Tools to filter out from permission requests (not dangerous)
        SKIP_PERMISSION_TOOLS = {"EnterPlanMode", "ExitPlanMode",
                                 "TodoWrite", "TaskCreate", "TaskUpdate", "TaskList",
                                 "TaskGet", "NotebookEdit"}

        # Check for permission denials and retry loop
        for retry in range(MAX_PERMISSION_RETRIES):
            denials = _get_permission_denials(events)
            if not denials:
                break

            # Separate AskUserQuestion denials
            question_denials = [d for d in denials if d.get("tool_name") == "AskUserQuestion"]
            other_denials = [d for d in denials if d.get("tool_name") != "AskUserQuestion"]

            # Handle AskUserQuestion: show to user and get answers
            question_answer_text = None
            if question_denials and on_question:
                tool_input = question_denials[0].get("tool_input", {})
                logger.info(f"AskUserQuestion detected, forwarding to user: {list(tool_input.keys())}")
                try:
                    answers = await retry_async(
                        lambda: on_question(tool_input),
                        max_retries=3,
                        delay=1.0
                    )
                except Exception as e:
                    logger.error(f"Failed to ask question after retries: {e}")
                    answers = None
                if answers:
                    # Format answers as text for Claude
                    parts = []
                    questions = tool_input.get("questions", [])
                    for idx, answer in sorted(answers.items()):
                        q_text = questions[idx].get("question", f"Вопрос {idx+1}") if idx < len(questions) else f"Вопрос {idx+1}"
                        parts.append(f"{q_text}\nОтвет: {answer}")
                    question_answer_text = "Ответ пользователя:\n\n" + "\n\n".join(parts)
                else:
                    logger.info("User did not answer questions (cancelled/timeout)")
                    question_answer_text = "Пользователь не ответил на вопросы, продолжай без ответов."

            # Separate real permission requests from auto-approvable tools
            real_denials = [d for d in other_denials if d.get("tool_name") not in SKIP_PERMISSION_TOOLS]
            auto_tools = list({d.get("tool_name") for d in other_denials if d.get("tool_name") in SKIP_PERMISSION_TOOLS})

            logger.info(f"Permission denials (retry {retry+1}): real={[d['tool_name'] for d in real_denials]}, auto={auto_tools}, questions={len(question_denials)}")

            if real_denials and on_permission_request:
                # Ask user for approval of real tools only (with retry for network errors)
                try:
                    approved_tools = await retry_async(
                        lambda: on_permission_request(real_denials),
                        max_retries=3,
                        delay=1.0
                    )
                except Exception as e:
                    logger.error(f"Failed to request permissions after retries: {e}")
                    approved_tools = []

                if not approved_tools:
                    logger.info("User denied all permissions or permission request failed")
                    if not question_answer_text:
                        break
            else:
                approved_tools = []

            # If there's nothing to retry (no permissions, no questions), break
            if not approved_tools and not auto_tools and not question_answer_text:
                break

            # Combine user-approved + auto-approved + safe tools + pre-approved + AskUserQuestion
            all_tools = list(dict.fromkeys(
                approved_tools + auto_tools + SAFE_TOOLS.split(",")
                + (list(pre_approved_tools) if pre_approved_tools else [])
                + ["AskUserQuestion"]
            ))
            logger.info(f"Retrying with tools: {all_tools}")

            # Determine the continuation message
            if question_answer_text and approved_tools:
                continue_message = f"Разрешения выданы. {question_answer_text}"
            elif question_answer_text:
                continue_message = question_answer_text
            else:
                continue_message = "Разрешения выданы, попробуй снова"

            # Retry with --resume and --allowedTools
            retry_cmd = [
                settings.claude_cli_path,
                "-p",
                "--output-format", "stream-json",
                "--verbose",
                "--resume", session_id,
                "--allowedTools", ",".join(all_tools),
                "--",
                continue_message,
            ]

            # Preserve first-run text in case retry only returns a brief confirmation
            prev_accumulated = accumulated_text
            accumulated_text = ""

            events_new, text_new, exit_code, error, was_stopped = await _run_claude_process(
                retry_cmd, workspace_dir, on_output_update, stop_event
            )

            # If retry produced meaningful text, combine with first-run text;
            # otherwise keep the first-run text (retry was just a file write confirmation)
            if text_new and text_new.strip():
                if prev_accumulated and prev_accumulated.strip():
                    accumulated_text = prev_accumulated.strip() + "\n\n" + text_new.strip()
                else:
                    accumulated_text = text_new
            else:
                accumulated_text = prev_accumulated
            events = events_new

            if was_stopped:
                return ClaudeResponse(
                    text=accumulated_text,
                    created_files=[],
                    exit_code=-1,
                    error="Остановлено пользователем"
                )

        # Prefer accumulated_text (full step-by-step output) over result summary
        result_text = accumulated_text or _get_result_text(events)

        if not result_text or len(result_text.strip()) == 0:
            logger.warning(f"Claude CLI returned empty output! Exit code: {exit_code}")

        # Snapshot files after execution
        files_after = await asyncio.to_thread(get_workspace_files, workspace_dir)
        created_files = list(files_after - files_before)

        # Filter out session files and logs
        created_files = [
            f for f in created_files
            if not any(part.startswith('.claude') for part in f.parts)
            and f.suffix not in ['.log', '.tmp']
        ]

        if created_files:
            logger.info(f"Created files: {[str(f) for f in created_files]}")

        # Extract tool activity log
        tool_actions = _extract_tool_actions(events)

        return ClaudeResponse(
            text=result_text,
            created_files=created_files,
            exit_code=exit_code or 0,
            error=error,
            tool_actions=tool_actions,
        )

    except Exception as e:
        logger.error(f"Failed to execute Claude CLI: {e}", exc_info=True)
        return ClaudeResponse(
            text="",
            created_files=[],
            exit_code=-1,
            error=f"Failed to execute Claude CLI: {str(e)}"
        )
