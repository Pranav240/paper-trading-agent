"""
The interface every repository implementation must satisfy.

`Protocol` (structural typing) instead of an ABC (nominal typing) is a
deliberate choice: PostgresRepository and InMemoryStore don't need to
inherit from a common base class, they just both need to provide these
three async methods with these signatures. FastAPI's route handlers type
-hint against this Protocol, not against either concrete class — that's
what makes "swap the implementation without touching the routes" literally
true rather than just a nice sentiment.

Both implementations are `async def` now, even InMemoryStore, which does
no real I/O. That's on purpose too: the interface has to be uniform for
the swap to actually be a swap. An interface where one implementation is
sync and the other is async isn't interchangeable, it's two different
interfaces that happen to have similar names.
"""

from __future__ import annotations

from typing import Protocol

from app.models import Decision, Position, RunResult


class Repository(Protocol):
    async def list_positions(self, symbol: str | None = None) -> list[Position]: ...

    async def list_decisions(
        self, symbol: str | None = None, limit: int = 50
    ) -> list[Decision]: ...

    async def trigger_run(self) -> RunResult: ...
