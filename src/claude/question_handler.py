"""Question handler for Claude AskUserQuestion tool via Telegram."""

import asyncio
from dataclasses import dataclass, field
from typing import Optional, Dict, Any, List


@dataclass
class QuestionRequest:
    """Represents a pending question from Claude to the user."""
    request_id: str
    questions: List[Dict[str, Any]]
    # Maps question_idx -> collected answer string
    answers: Dict[int, str] = field(default_factory=dict)
    # For multiSelect: maps question_idx -> set of selected option indices
    multi_selections: Dict[int, set] = field(default_factory=dict)
    # Future resolved when all questions are answered
    response_future: asyncio.Future = field(repr=False, default=None)
    # Track which questions are waiting for free-text "Other" input
    awaiting_text: Dict[int, bool] = field(default_factory=dict)

    def all_answered(self) -> bool:
        return len(self.answers) == len(self.questions)


class QuestionManager:
    """Manages pending question requests from Claude, keyed by request_id."""

    def __init__(self):
        self._pending: Dict[str, QuestionRequest] = {}
        # Maps chat_id -> request_id for "Other" free-text routing
        self._awaiting_text: Dict[int, tuple] = {}  # chat_id -> (request_id, question_idx)

    def create_request(
        self, request_id: str, questions: List[Dict[str, Any]]
    ) -> QuestionRequest:
        loop = asyncio.get_running_loop()
        future = loop.create_future()
        request = QuestionRequest(
            request_id=request_id,
            questions=questions,
            response_future=future,
        )
        self._pending[request_id] = request
        return request

    def get_request(self, request_id: str) -> Optional[QuestionRequest]:
        return self._pending.get(request_id)

    def set_answer(self, request_id: str, question_idx: int, answer: str) -> bool:
        """Set answer for a single question. Returns True if all questions answered."""
        request = self._pending.get(request_id)
        if request is None:
            return False
        request.answers[question_idx] = answer
        if request.all_answered() and not request.response_future.done():
            request.response_future.set_result(request.answers)
            self._pending.pop(request_id, None)
            return True
        return False

    def toggle_multi_select(self, request_id: str, question_idx: int, option_idx: int) -> set:
        """Toggle an option in multiSelect mode. Returns current selections."""
        request = self._pending.get(request_id)
        if request is None:
            return set()
        if question_idx not in request.multi_selections:
            request.multi_selections[question_idx] = set()
        selections = request.multi_selections[question_idx]
        if option_idx in selections:
            selections.discard(option_idx)
        else:
            selections.add(option_idx)
        return selections

    def finalize_multi_select(self, request_id: str, question_idx: int) -> bool:
        """Finalize multiSelect answer. Returns True if all questions answered."""
        request = self._pending.get(request_id)
        if request is None:
            return False
        selections = request.multi_selections.get(question_idx, set())
        question = request.questions[question_idx]
        options = question.get("options", [])
        selected_labels = [options[i]["label"] for i in sorted(selections) if i < len(options)]
        answer = ", ".join(selected_labels) if selected_labels else "(ничего не выбрано)"
        return self.set_answer(request_id, question_idx, answer)

    def set_awaiting_text(self, chat_id: int, request_id: str, question_idx: int):
        """Mark that we're waiting for free-text input from this chat."""
        self._awaiting_text[chat_id] = (request_id, question_idx)

    def get_awaiting_text(self, chat_id: int) -> Optional[tuple]:
        """Check if this chat has a pending free-text question. Returns (request_id, question_idx) or None."""
        return self._awaiting_text.get(chat_id)

    def clear_awaiting_text(self, chat_id: int):
        self._awaiting_text.pop(chat_id, None)

    def cancel(self, request_id: str):
        """Cancel a specific pending request."""
        req = self._pending.pop(request_id, None)
        if req and req.response_future and not req.response_future.done():
            req.response_future.set_result(None)

    def cancel_all(self):
        for req in self._pending.values():
            if req.response_future and not req.response_future.done():
                req.response_future.set_result(None)
        self._pending.clear()
        self._awaiting_text.clear()


# Global singleton
question_manager = QuestionManager()
