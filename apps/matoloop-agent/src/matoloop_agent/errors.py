from __future__ import annotations


class AgentError(Exception):
    def __init__(
        self,
        code: str,
        message: str,
        params: dict[str, str | bool | int | float] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.params = params
