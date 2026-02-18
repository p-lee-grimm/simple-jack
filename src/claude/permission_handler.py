"""Permission request handler for Claude tool approvals via Telegram."""

import asyncio
from dataclasses import dataclass, field
from typing import Optional, Dict, Any


@dataclass
class PermissionRequest:
    """Represents a pending permission request."""
    tool_name: str
    tool_input: Dict[str, Any]
    request_id: str
    response_future: asyncio.Future = field(repr=False)


class PermissionManager:
    """Manages pending permission requests, keyed by request_id."""

    def __init__(self):
        self._pending: Dict[str, PermissionRequest] = {}

    def create_request(
        self, tool_name: str, tool_input: dict, request_id: str
    ) -> PermissionRequest:
        loop = asyncio.get_running_loop()
        future = loop.create_future()
        request = PermissionRequest(
            tool_name=tool_name,
            tool_input=tool_input,
            request_id=request_id,
            response_future=future,
        )
        self._pending[request_id] = request
        return request

    def resolve(self, request_id: str, approved: bool) -> bool:
        request = self._pending.pop(request_id, None)
        if request is None:
            return False
        if not request.response_future.done():
            request.response_future.set_result(approved)
        return True

    def cancel(self, request_id: str):
        """Cancel a specific pending request."""
        req = self._pending.pop(request_id, None)
        if req and not req.response_future.done():
            req.response_future.set_result(False)

    def cancel_all(self):
        for req in self._pending.values():
            if not req.response_future.done():
                req.response_future.set_result(False)
        self._pending.clear()


# Global singleton
permission_manager = PermissionManager()
