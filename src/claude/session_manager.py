"""Session management for Claude conversations."""

import json
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional, Dict, Any, List, Tuple
from config.settings import settings
from src.utils.logger import setup_logger


logger = setup_logger(__name__)


class Session:
    """Represents a conversation session."""

    def __init__(self, user_id: int, session_id: Optional[str] = None):
        self.user_id = user_id
        self.session_id = session_id or str(uuid.uuid4())
        self.messages = []
        self.working_directory = str(Path(settings.workspace_dir) / f"user_{user_id}")
        self.last_activity = datetime.now()
        self.created_at = datetime.now()
        self.approved_tools: set = set()  # Tools approved for the lifetime of this session

    def add_message(self, role: str, content: str, metadata: Optional[Dict[str, Any]] = None):
        """Add a message to the session history."""
        message = {
            "role": role,
            "content": content,
            "timestamp": datetime.now().isoformat(),
            "metadata": metadata or {}
        }
        self.messages.append(message)
        self.last_activity = datetime.now()

    def to_dict(self) -> Dict[str, Any]:
        """Convert session to dictionary."""
        return {
            "user_id": self.user_id,
            "session_id": self.session_id,
            "messages": self.messages,
            "working_directory": self.working_directory,
            "last_activity": self.last_activity.isoformat(),
            "created_at": self.created_at.isoformat(),
            "approved_tools": list(self.approved_tools),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Session":
        """Create session from dictionary."""
        session = cls(data["user_id"], data["session_id"])
        session.messages = data.get("messages", [])
        session.working_directory = data.get("working_directory", session.working_directory)
        session.last_activity = datetime.fromisoformat(data["last_activity"])
        session.created_at = datetime.fromisoformat(data.get("created_at", data["last_activity"]))
        session.approved_tools = set(data.get("approved_tools", []))
        return session

    def is_expired(self) -> bool:
        """Check if session has expired."""
        timeout = timedelta(hours=settings.session_timeout_hours)
        return datetime.now() - self.last_activity > timeout

    def preview(self) -> str:
        """Get a short preview of the session (first user message)."""
        for msg in self.messages:
            if msg.get("role") == "user":
                content = msg.get("content", "")
                return content[:60] + "..." if len(content) > 60 else content
        return "(пустая сессия)"


class SessionManager:
    """Manages user sessions with support for multiple sessions per user."""

    def __init__(self):
        self.sessions_dir = settings.sessions_dir
        self.sessions_dir.mkdir(parents=True, exist_ok=True)

    def _user_dir(self, user_id: int) -> Path:
        """Get per-user sessions directory."""
        d = self.sessions_dir / f"user_{user_id}"
        d.mkdir(parents=True, exist_ok=True)
        return d

    def _index_path(self, user_id: int) -> Path:
        return self._user_dir(user_id) / "index.json"

    def _always_approved_path(self, user_id: int) -> Path:
        return self._user_dir(user_id) / "always_approved.json"

    def load_always_approved(self, user_id: int) -> set:
        """Load the set of permanently approved tools for this user."""
        path = self._always_approved_path(user_id)
        if path.exists():
            try:
                with open(path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                return set(data.get("tools", []))
            except Exception as e:
                logger.error(f"Failed to load always_approved for user {user_id}: {e}")
        return set()

    def save_always_approved(self, user_id: int, tools: set):
        """Persist the set of permanently approved tools for this user."""
        path = self._always_approved_path(user_id)
        try:
            with open(path, 'w', encoding='utf-8') as f:
                json.dump({"tools": sorted(tools)}, f, indent=2)
        except Exception as e:
            logger.error(f"Failed to save always_approved for user {user_id}: {e}")

    def _session_path(self, user_id: int, session_id: str) -> Path:
        return self._user_dir(user_id) / f"{session_id}.json"

    def _load_index(self, user_id: int) -> Dict[str, Any]:
        path = self._index_path(user_id)
        if path.exists():
            try:
                with open(path, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except Exception as e:
                logger.error(f"Failed to load index for user {user_id}: {e}")
        return {"active_session_id": None, "session_ids": []}

    def _save_index(self, user_id: int, index: Dict[str, Any]):
        path = self._index_path(user_id)
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(index, f, indent=2, ensure_ascii=False)

    def _migrate_legacy(self, user_id: int):
        """Migrate from old single-file format to new multi-session format."""
        legacy_file = self.sessions_dir / f"user_{user_id}.json"
        if not legacy_file.exists():
            return

        index_path = self._index_path(user_id)
        if index_path.exists():
            # Already migrated, just remove legacy
            legacy_file.unlink(missing_ok=True)
            return

        try:
            with open(legacy_file, 'r', encoding='utf-8') as f:
                data = json.load(f)

            # Check it's actual session data (has "messages" key)
            if "session_id" in data and "messages" in data:
                session = Session.from_dict(data)
                session_path = self._session_path(user_id, session.session_id)
                with open(session_path, 'w', encoding='utf-8') as f:
                    json.dump(session.to_dict(), f, indent=2, ensure_ascii=False)

                index = {
                    "active_session_id": session.session_id,
                    "session_ids": [session.session_id],
                }
                self._save_index(user_id, index)
                logger.info(f"Migrated legacy session for user {user_id}: {session.session_id}")

            legacy_file.unlink(missing_ok=True)
        except Exception as e:
            logger.error(f"Failed to migrate legacy session for user {user_id}: {e}", exc_info=True)

    def get_session(self, user_id: int) -> Session:
        """Get or create active session for user."""
        self._migrate_legacy(user_id)

        index = self._load_index(user_id)
        active_id = index.get("active_session_id")

        if active_id:
            session_path = self._session_path(user_id, active_id)
            if session_path.exists():
                try:
                    with open(session_path, 'r', encoding='utf-8') as f:
                        data = json.load(f)
                    session = Session.from_dict(data)

                    if session.is_expired():
                        logger.info(f"Session expired for user {user_id}, creating new session")
                        return self._create_new_session(user_id, index)

                    logger.info(f"Loaded existing session for user {user_id}: {session.session_id}")
                    return session
                except Exception as e:
                    logger.error(f"Failed to load session {active_id}: {e}", exc_info=True)

        return self._create_new_session(user_id, index)

    def _create_new_session(self, user_id: int, index: Dict[str, Any]) -> Session:
        """Create a new session and update the index."""
        session = Session(user_id)
        self.save_session(session)

        index["active_session_id"] = session.session_id
        if session.session_id not in index.get("session_ids", []):
            index.setdefault("session_ids", []).append(session.session_id)
        self._save_index(user_id, index)

        logger.info(f"Created new session for user {user_id}: {session.session_id}")
        return session

    def save_session(self, session: Session):
        """Save session to disk."""
        try:
            session_path = self._session_path(session.user_id, session.session_id)
            with open(session_path, 'w', encoding='utf-8') as f:
                json.dump(session.to_dict(), f, indent=2, ensure_ascii=False)
            logger.debug(f"Saved session for user {session.user_id}")
        except Exception as e:
            logger.error(f"Failed to save session: {e}", exc_info=True)

    def reset_session(self, user_id: int) -> Session:
        """Create new session, keep old sessions available for /switch."""
        self._migrate_legacy(user_id)
        index = self._load_index(user_id)

        session = Session(user_id)
        self.save_session(session)

        index["active_session_id"] = session.session_id
        if session.session_id not in index.get("session_ids", []):
            index.setdefault("session_ids", []).append(session.session_id)
        self._save_index(user_id, index)

        logger.info(f"Reset session for user {user_id}, new session: {session.session_id}")
        return session

    def switch_session(self, user_id: int, session_id: str) -> Optional[Session]:
        """
        Switch to an existing session.

        Returns the session if found, None otherwise.
        """
        self._migrate_legacy(user_id)
        index = self._load_index(user_id)

        # Support partial session ID matching
        session_ids = index.get("session_ids", [])
        matches = [sid for sid in session_ids if sid.startswith(session_id)]

        if len(matches) == 0:
            return None
        if len(matches) > 1:
            # Ambiguous — try exact match first
            if session_id in matches:
                matches = [session_id]
            else:
                return None  # Ambiguous

        target_id = matches[0]
        session_path = self._session_path(user_id, target_id)
        if not session_path.exists():
            # Remove stale entry
            index["session_ids"] = [s for s in session_ids if s != target_id]
            self._save_index(user_id, index)
            return None

        try:
            with open(session_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            session = Session.from_dict(data)

            index["active_session_id"] = target_id
            self._save_index(user_id, index)

            logger.info(f"Switched session for user {user_id} to {target_id}")
            return session
        except Exception as e:
            logger.error(f"Failed to load session {target_id}: {e}", exc_info=True)
            return None

    def list_sessions(self, user_id: int) -> List[Tuple[Session, bool]]:
        """
        List all sessions for a user.

        Returns list of (session, is_active) tuples, sorted by last_activity descending.
        """
        self._migrate_legacy(user_id)
        index = self._load_index(user_id)

        active_id = index.get("active_session_id")
        session_ids = index.get("session_ids", [])
        sessions = []

        for sid in session_ids:
            session_path = self._session_path(user_id, sid)
            if not session_path.exists():
                continue
            try:
                with open(session_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                session = Session.from_dict(data)
                sessions.append((session, sid == active_id))
            except Exception as e:
                logger.warning(f"Failed to load session {sid}: {e}")

        # Sort by last activity, newest first
        sessions.sort(key=lambda x: x[0].last_activity, reverse=True)
        return sessions


# Global session manager instance
session_manager = SessionManager()
