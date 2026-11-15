"""The people store on Postgres: user isolation and atomic task writes.

Each state directory resolves to its own user (see tests/pg_fixtures.py), so
two PersonStore objects at two directories are two users of one database.
"""

from __future__ import annotations

import pytest

from coworker.crm_service import CrmService
from coworker.crm_tasks import TaskNotFound, TaskService
from coworker.people import PersonStore
from coworker.people_db import Tx

EMAIL = "maya.chen@example.com"


def _keep(people: PersonStore) -> str:
    person = people.keep_from_apollo(
        apollo_id="apollo-maya",
        first_name="Maya",
        last_name_obfuscated="Chen",
        title="Head of Platform",
        company="Northwind",
    )
    people.apply_enrichment(person["person_id"], email=EMAIL)
    return person["person_id"]


def _count(people: PersonStore, table: str) -> int:
    with people.transaction():
        return people._conn.execute(
            f"SELECT count(*) FROM {table} WHERE user_id = %s", (people._user_id,)
        ).fetchone()[0]


def test_two_users_keep_the_same_person_and_see_only_their_own(tmp_path):
    alice, bob = PersonStore(tmp_path / "alice"), PersonStore(tmp_path / "bob")
    assert alice._user_id != bob._user_id
    alice_pid, bob_pid = _keep(alice), _keep(bob)
    assert alice_pid != bob_pid
    for people, pid in ((alice, alice_pid), (bob, bob_pid)):
        TaskService(people).create(
            pid, title="Send the deck", timezone="UTC",
            operation_id="same-operation-id-for-both", actor="director",
        )
        people.set_sequence(pid, "open", actor="director")

    for people, own, other in ((alice, alice_pid, bob_pid), (bob, bob_pid, alice_pid)):
        assert [p["person_id"] for p in people.list_people()] == [own]
        assert people.get(own)["email"] == EMAIL
        assert people.get(other) is None
        assert people.get_by_apollo_id("apollo-maya")["person_id"] == own
        assert people.search(EMAIL)[0]["person_id"] == own
        assert len(people.search(EMAIL)) == 1
        assert people.timeline(other) == []
        assert people.versions(other) == []
        tasks = TaskService(people).list(timezone="UTC")["tasks"]
        assert [task["person_id"] for task in tasks] == [own]
        with pytest.raises(TaskNotFound):
            TaskService(people).list(timezone="UTC", person_id=other)
        with pytest.raises(TaskNotFound):
            CrmService(people).recent_changes(other)
        assert len(CrmService(people).recent_changes(own)) == 1


def test_a_failing_task_write_rolls_back_its_change_and_receipt(tmp_path, monkeypatch):
    people = PersonStore(tmp_path)
    pid = _keep(people)
    version = people.get(pid)["version"]
    before = {table: _count(people, table) for table in ("person_tasks", "crm_changes", "crm_operations")}

    original = Tx.execute

    def fail_on_receipt(self, query, params=()):
        if query.lstrip().upper().startswith("INSERT INTO CRM_OPERATIONS"):
            raise RuntimeError("receipt write failed")
        return original(self, query, params)

    monkeypatch.setattr(Tx, "execute", fail_on_receipt)
    with pytest.raises(RuntimeError, match="receipt write failed"):
        TaskService(people).create(
            pid, title="Send the deck", timezone="UTC",
            operation_id="task-create-that-fails", actor="director",
        )
    monkeypatch.setattr(Tx, "execute", original)

    after = {table: _count(people, table) for table in before}
    assert after == before
    assert people.get(pid)["version"] == version
    # The same operation id is free again: nothing of the failed write stayed.
    created = TaskService(people).create(
        pid, title="Send the deck", timezone="UTC",
        operation_id="task-create-that-fails", actor="director",
    )
    assert created["replayed"] is False
    assert {table: _count(people, table) for table in before} == {
        table: count + 1 for table, count in before.items()
    }
