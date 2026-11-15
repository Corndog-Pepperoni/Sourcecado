"""Durable tracked mail, collection progress, and pending analysis in the people store.

Bodies are sensitive, untrusted evidence. They stay out of timeline payloads
and operational logs; future analysis reads this store through person ownership.
"""

import hashlib
import json
from typing import Any
from urllib.parse import quote

from coworker.people import PersonStore, _new_event_id

def source_ref(message_id: str, thread_id: str, account: str | None = None) -> dict[str, Any]:
    # authuser selects the account without assuming it is Gmail's first tab.
    query = f"?authuser={quote(account, safe='')}" if account else ""
    return {"provider": "Gmail", "message_id": message_id, "thread_id": thread_id,
        "url": f"https://mail.google.com/mail/u/0/{query}#all/{quote(thread_id, safe='')}"}


class MailRepository:
    def __init__(self, people: PersonStore):
        self.people = people

    def message(self, account: str, message_id: str) -> dict[str, Any] | None:
        with self.people.transaction():
            row = self.people._conn.execute(
                "SELECT * FROM gmail_messages WHERE user_id = %s AND account = %s AND message_id = %s",
                (self.people._user_id, account, message_id)).fetchone()
        return dict(row) if row else None

    def analysis_state(self, person_id: str) -> dict[str, Any]:
        with self.people.transaction():
            row = self.people._conn.execute(
                "SELECT * FROM crm_analysis_state WHERE user_id = %s AND person_id = %s",
                (self.people._user_id, person_id)).fetchone()
        return self.analysis_projection(dict(row) if row else None)

    @staticmethod
    def analysis_projection(row: dict[str, Any] | None) -> dict[str, Any]:
        result = dict(row) if row else {"latest_evidence_revision":0,"latest_revision":0,"analyzed_revision":0}
        latest=result.get('latest_revision',0)
        dirty=result.get('latest_evidence_revision',0)>result.get('observed_collection_revision',0)
        pending=dirty or latest!=result.get('analyzed_revision',0)
        result['status']='pending' if pending else 'current' if latest else 'not_analyzed'
        if result.get('failure_code') and pending:
            result['status']='failed' if result.get('attempts',0)>=3 else 'retrying'
        result['next_attempt_at']=result.get('next_retry_at')
        result['attempted_revision']=latest if result.get('attempts',0) else 0
        result['failure']=result.get('failure_code')
        for key in ('lease_token','usage_json'):result.pop(key,None)
        return result

    def incomplete_body_count(self, account: str) -> int:
        with self.people.transaction():
            return self.people._conn.execute("""SELECT count(*) FROM gmail_messages m
                JOIN people p ON p.user_id = m.user_id AND p.person_id = m.person_id
                WHERE m.user_id = %s AND m.account = %s AND m.body_state != 'present' AND p.deleted_at IS NULL""",
                (self.people._user_id, account)).fetchone()[0]

    def sync_state(self) -> dict[str, Any]:
        with self.people.transaction():
            row = self.people._conn.execute(
                "SELECT * FROM gmail_collection_state WHERE user_id = %s AND id = 1",
                (self.people._user_id,)).fetchone()
        return dict(row) if row else {"status": "never", "account": None, "last_attempt_at": None,
            "last_success_at": None, "warning": None, "outbound_after": None,
            "outbound_checked_at": None, "inbound_page_token": None, "inbound_target_history_id": None, "resync_history_id": None}

    def public_sync_state(self) -> dict[str, Any]:
        state = self.sync_state()
        return {key: state.get(key) for key in ("status", "last_attempt_at", "last_success_at", "warning", "outbound_checked_at")}

    def finish_check(self, *, status: str, account: str | None, warning: str | None = None,
        outbound_after: str | None = None, checked_at: str | None = None,
        inbound_page_token: str | None = None, inbound_target_history_id: str | None = None,
        resync_history_id: str | None = None, advance_inbound: bool = False,
        inbound_cursor: str | None = None) -> None:
        now = checked_at or self.people._now()
        prior = self.sync_state()
        # A failed attempt records a warning, never advances a replay boundary.
        if status == "failed":
            account = prior["account"]
            outbound_after = prior["outbound_after"]
            inbound_page_token = prior["inbound_page_token"]
            inbound_target_history_id = prior["inbound_target_history_id"]
            resync_history_id = prior.get("resync_history_id")
        with self.people.crm_transaction() as conn:
            conn.execute("""INSERT INTO gmail_collection_state (user_id, id, account, status,
                last_attempt_at, last_success_at, warning, outbound_after, outbound_checked_at,
                inbound_page_token, inbound_target_history_id, resync_history_id)
                VALUES (%s, 1, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT(user_id, id) DO UPDATE SET account=excluded.account, status=excluded.status,
                last_attempt_at=excluded.last_attempt_at, last_success_at=excluded.last_success_at,
                warning=excluded.warning, outbound_after=excluded.outbound_after,
                outbound_checked_at=excluded.outbound_checked_at,
                inbound_page_token=excluded.inbound_page_token,
                inbound_target_history_id=excluded.inbound_target_history_id,
                resync_history_id=excluded.resync_history_id""",
                (self.people._user_id, account, status, now, now if status == "ok" else prior["last_success_at"], warning,
                 outbound_after, now if status == "ok" else prior["outbound_checked_at"],
                 inbound_page_token, inbound_target_history_id, resync_history_id))
            if advance_inbound and status != "failed":
                conn.execute("""INSERT INTO reply_sync (user_id, id, history_id, updated_at) VALUES (%s, 1, %s, %s)
                    ON CONFLICT(user_id, id) DO UPDATE SET history_id=excluded.history_id, updated_at=excluded.updated_at""",
                    (self.people._user_id, inbound_cursor, now))

    def save_message(self, person_id: str, message: dict[str, Any], *, direction: str,
        account: str, content: dict[str, Any]) -> bool:
        mid, thread = str(message.get("id") or ""), str(message.get("thread_id") or "")
        if not mid or not thread:
            raise ValueError("Gmail message and thread identity are required")
        payload = {"direction": direction, "message_id": mid, "thread_id": thread,
            "from": message.get("from"), "to": message.get("to"), "subject": message.get("subject"),
            "rfc_message_id": message.get("rfc_message_id"), "in_reply_to": message.get("in_reply_to"),
            "snippet": str(message.get("snippet") or "")[:1000], "received_at": message.get("received_at"),
            "source_ref": source_ref(mid, thread, account), "untrusted": True, "read_only": True,
            "body_state": content["body_state"]}
        fingerprint = hashlib.sha256(json.dumps({"metadata": payload, "content": content},
            sort_keys=True).encode()).hexdigest()
        now = self.people._now()
        with self.people.crm_transaction() as conn:
            self.people._load_person_row(person_id)
            row = conn.execute("SELECT * FROM gmail_messages WHERE user_id = %s AND account = %s AND message_id = %s",
                (self.people._user_id, account, mid)).fetchone()
            if row and row["person_id"] != person_id:
                raise ValueError("Gmail message ownership conflict")
            if row and row["fingerprint"] == fingerprint:
                return False
            event = conn.execute("SELECT * FROM events WHERE user_id = %s AND person_id = %s AND external_key = %s",
                (self.people._user_id, person_id, f"gmail:message:{mid}")).fetchone()
            if event and event["kind"] == "send":
                # Enrich the original approval receipt; never replace its authority.
                payload = {**json.loads(event["payload"]), **payload}
            conn.execute("""INSERT INTO gmail_messages (user_id, account, message_id, person_id, thread_id,
                direction, body, body_state, fingerprint, updated_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT(user_id, account, message_id) DO UPDATE SET body=excluded.body,
                body_state=excluded.body_state, fingerprint=excluded.fingerprint, updated_at=excluded.updated_at""",
                (self.people._user_id, account, mid, person_id, thread, direction, content["body"], content["body_state"], fingerprint, now))
            if event:
                conn.execute("UPDATE events SET payload = %s WHERE user_id = %s AND event_id = %s", (json.dumps(payload), self.people._user_id, event["event_id"]))
            else:
                headline = str(message.get("subject") or "No subject")
                conn.execute("""INSERT INTO events(user_id, event_id, person_id, external_key, source, kind,
                    summary, payload, actor, tool) VALUES (%s, %s, %s, %s, 'gmail', 'mail', %s, %s, 'assistant', 'gmail_read')""",
                    (self.people._user_id, _new_event_id(), person_id, f"gmail:message:{mid}",
                     f"{'Received from' if direction == 'inbound' else 'Sent to'} {message.get('from' if direction == 'inbound' else 'to')}: {headline}", json.dumps(payload)))
            conn.execute("""INSERT INTO crm_analysis_state(user_id, person_id, latest_evidence_revision, updated_at)
                VALUES (%s, %s, 1, %s)
                ON CONFLICT(user_id, person_id) DO UPDATE
                SET latest_evidence_revision=crm_analysis_state.latest_evidence_revision+1,
                updated_at=excluded.updated_at""", (self.people._user_id, person_id, now))
        return True
