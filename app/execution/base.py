from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class ExecutionProvider(ABC):
    """Common interface every execution provider (host, docker, ...) implements.

    See docs/EXECUTION_PROVIDER.md section 2.
    """

    @abstractmethod
    def available(self) -> bool: ...

    @abstractmethod
    def create_context(self, configuration: dict[str, Any]) -> Any: ...

    @abstractmethod
    def execute(self, command: list[str], options: dict[str, Any] | None = None) -> Any: ...

    @abstractmethod
    def start_process(
        self, command: list[str], options: dict[str, Any] | None = None
    ) -> Any: ...

    @abstractmethod
    def stop_process(self, process_id: int) -> None: ...

    @abstractmethod
    def signal_process(self, process_id: int, sig: int) -> None: ...

    @abstractmethod
    def stream_output(self, process_id: int, after_id: int | None = None) -> Any: ...

    @abstractmethod
    def process_status(self, process_id: int) -> Any: ...

    @abstractmethod
    def destroy_context(self, context_id: int) -> None: ...
