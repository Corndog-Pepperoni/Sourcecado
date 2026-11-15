"""One transaction for the people and CRM store, shared by its modules.

`PersonStore`, `TaskService`, `CrmService`, the analysis worker and the
correction service all write the same tables, and several of their writes must
commit together: a task, its `crm_changes` row and its `crm_operations`
receipt. With SQLite they shared one connection. On Postgres they share one
`Database.tx` block.

`PeopleTx.begin()` opens that block, or joins the block that this thread
already has open for the same store. A joined call never opens a second
transaction, so a helper that runs inside a caller's write is part of that
write. The open block lives in a thread-local slot only while the `with` block
runs; no connection is kept after it ends.

Writers of one user run one at a time, as they did on SQLite. A transaction
takes the user's advisory lock at its first write, as SQLite took its write
lock at the first write. The writers that SQLite started with BEGIN IMMEDIATE
take the lock before their first read (`PersonStore.lock_writes`). A
transaction holds no row lock before it holds this lock, so the two kinds of
lock cannot deadlock.

Statements outside any block (plain reads) run in a short transaction of their
own. Each statement passes its parameters as `%s`, so a literal percent sign
in SQL is written `%%`. A write outside a block is refused, so a
multi-statement write cannot commit one statement at a time by mistake.
"""

from __future__ import annotations

import re
import threading
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from typing import Any
from uuid import UUID

import psycopg

from coworker.db import Database

# The rows read like sqlite3.Row did: no user_id (the transaction implies it)
# and no seq (an ordering column that replaces the SQLite rowid).
HIDDEN_COLUMNS = frozenset({"user_id", "seq"})
_WRITE = re.compile(r"^\s*(insert|update|delete)\b", re.IGNORECASE)


class Row:
    """A row that reads by name or by position, like sqlite3.Row."""

    __slots__ = ("_names", "_values", "_index")

    def __init__(self, names: tuple[str, ...], values: tuple[Any, ...], index: dict[str, int]):
        self._names = names
        self._values = values
        self._index = index

    def keys(self) -> list[str]:
        return list(self._names)

    def __getitem__(self, key: int | str) -> Any:
        if isinstance(key, str):
            return self._values[self._index[key]]
        return self._values[key]

    def __iter__(self) -> Iterator[Any]:
        return iter(self._values)

    def __len__(self) -> int:
        return len(self._values)

    def __eq__(self, other: object) -> bool:
        if isinstance(other, Row):
            return self._names == other._names and self._values == other._values
        return NotImplemented

    __hash__ = None  # type: ignore[assignment]

    def __repr__(self) -> str:
        return f"Row({dict(zip(self._names, self._values))!r})"


class Result:
    """The fetched rows of one statement."""

    def __init__(self, rows: list[Row], rowcount: int) -> None:
        self._rows = rows
        self._next = 0
        self.rowcount = rowcount

    def fetchone(self) -> Row | None:
        if self._next >= len(self._rows):
            return None
        row = self._rows[self._next]
        self._next += 1
        return row

    def fetchall(self) -> list[Row]:
        rows = self._rows[self._next :]
        self._next = len(self._rows)
        return rows

    def __iter__(self) -> Iterator[Row]:
        return iter(self.fetchall())


class Tx:
    """The connection of one open transaction."""

    def __init__(self, raw: psycopg.Connection) -> None:
        self.raw = raw
        self._locked = False

    def execute(self, query: str, params: Sequence[Any] = ()) -> Result:
        # Every statement names user_id. Row-level security (slice S18) adds a
        # second barrier; it does not replace this filter.
        if "user_id" not in query:
            raise RuntimeError("people store query does not filter by user_id")
        if not self._locked and _WRITE.match(query):
            self.lock_user()
        with self.raw.cursor() as cursor:
            cursor.execute(query, tuple(params))
            if cursor.description is None:
                return Result([], cursor.rowcount)
            names = [column.name for column in cursor.description]
            keep = [i for i, name in enumerate(names) if name not in HIDDEN_COLUMNS]
            shown = tuple(names[i] for i in keep)
            index = {name: position for position, name in enumerate(shown)}
            rows = [
                Row(shown, tuple(values[i] for i in keep), index)
                for values in cursor.fetchall()
            ]
            return Result(rows, cursor.rowcount)

    def lock_user(self) -> None:
        """Take the transaction-scoped write lock of the acting user."""
        self.raw.execute(
            "select pg_advisory_xact_lock(hashtextextended("
            "'people:' || current_setting('app.user_id'), 0))"
        )
        self._locked = True

    @contextmanager
    def savepoint(self) -> Iterator[Tx]:
        """A nested block: an error inside it undoes only its own statements."""
        locked = self._locked
        try:
            with self.raw.transaction():
                yield self
        except BaseException:
            # A lock taken inside the rolled-back block may be gone with it.
            self._locked = locked
            raise


class PeopleTx:
    """The transaction scope of one PersonStore."""

    def __init__(self, database: Database, user_id: UUID) -> None:
        self._db = database
        self.user_id = user_id
        self.lock = threading.RLock()
        self._local = threading.local()

    @property
    def current(self) -> Tx | None:
        return getattr(self._local, "tx", None)

    @contextmanager
    def begin(self) -> Iterator[Tx]:
        """Open the store's transaction, or join the one this thread has open.

        Holds the store lock for the whole block, as the SQLite store did, so
        two threads of one process never interleave their writes.
        """
        with self.lock:
            active = self.current
            if active is not None:
                yield active
                return
            with self._db.tx(self.user_id) as raw:
                tx = Tx(raw)
                self._local.tx = tx
                try:
                    yield tx
                finally:
                    self._local.tx = None


class PeopleSql:
    """`PersonStore._conn`: runs a statement in the store's open transaction.

    `in_transaction` is True inside `PersonStore.transaction()`.
    """

    def __init__(self, scope: PeopleTx) -> None:
        self._scope = scope

    @property
    def in_transaction(self) -> bool:
        return self._scope.current is not None

    def execute(self, query: str, params: Sequence[Any] = ()) -> Result:
        active = self._scope.current
        if active is not None:
            return active.execute(query, params)
        if _WRITE.match(query):
            raise RuntimeError("people store write outside a transaction")
        with self._scope.begin() as tx:
            return tx.execute(query, params)
