"""Legacy attention decisions count only for unchanged mail.

Old v7 decisions reach Postgres as the rows an upgraded store held. The
runtime check in `crm_legacy_compat.legacy_decision` must honor them only
against unchanged, permitted, fully saved mail.
"""
import asyncio
import hashlib
import json
import re

import pytest

from coworker.crm_mail_repository import MailRepository, source_ref
from coworker.crm_tasks import TaskService, _fingerprint
from coworker.people import PersonStore
from test_crm_analysis import worker, tracked_person
from test_crm_email_tasks import BODY, TaskMail, TaskModel, candidate


def _legacy_source_ids(people, pid, refs):
    """The source ids the v7-to-v8 upgrade gave legacy dictionary references.

    Same rule as the removed SQLite upgrade (`crm_unified_schema._refs`): reuse
    the most restrictive existing alias of the message, else file a new one.
    A data import of an old store must produce the same rows.
    """
    result = []
    rows = people._conn.execute(
        "SELECT id,fields_json,restricted FROM person_attachments WHERE user_id=%s AND person_id=%s AND record_type='source_ref'",
        (people._user_id, pid)).fetchall()
    for ref in refs:
        mid = ref.get('message_id')
        matches = [row for row in rows if mid and (
            not ref.get('account') or not json.loads(row[1]).get('account') or json.loads(row[1]).get('account') == ref.get('account'))
            and (json.loads(row[1]).get('message_id') == mid or json.loads(row[1]).get('id') in {f'gmail:{mid}', f'gmail:message:{mid}'})]
        if matches:
            result.append(sorted(matches, key=lambda r: (-r[2], r[0]))[0][0])
            continue
        sid = 'source_' + hashlib.sha256((pid + json.dumps(ref, sort_keys=True)).encode()).hexdigest()[:32]
        fields = {**ref, 'provider': ref.get('provider', 'Gmail'), 'id': ref.get('id', f'gmail:{mid}')}
        people._conn.execute(
            """INSERT INTO person_attachments(user_id,id,person_id,record_type,idempotency_key,fields_json,restricted,created_at,updated_at)
            VALUES(%s,%s,%s,'source_ref',%s,%s,%s,people_timestamp(),people_timestamp()) ON CONFLICT DO NOTHING""",
            (people._user_id, sid, pid, 'crm-legacy:' + sid, json.dumps(fields),
             int(str(ref.get('sensitivity', '')).lower() == 'restricted')))
        result.append(sid)
    return sorted(set(result))


def legacy_store(tmp_path, *, kind, state):
    """A person whose CRM rows look like an upgraded v7 attention store.

    The rows are the ones the v7-to-v8 SQLite upgrade wrote: a `legacy:` proposal
    for a pending or dismissed review, an interpretation fingerprint, and, for a
    suppressed automatic creation, the materialized `legacy-auto:` proposal.
    """
    people = PersonStore(tmp_path)
    pid = tracked_person(people)
    mail = TaskMail()
    repo = MailRepository(people)
    evidence = worker(people, TaskModel(), mail)._collect(pid)
    legacy_sources, fingerprints = [], {}
    for support in evidence.manifest:
        mid = support['message_id']
        meta, full = mail.inbound_message(message_id=mid), mail.read(message_id=mid)
        repo.save_message(pid, meta, direction=support['direction'], account='director@club.test',
            content={'body': full['body'], 'body_state': 'present'})
        row = repo.message('director@club.test', mid)
        fingerprints[mid] = row['fingerprint']
        legacy_sources.append({**source_ref(mid, 'tracked-1', 'director@club.test'),
            'account': 'director@club.test', 'evidence_fingerprint': row['fingerprint']})
    value = {'title': 'Send project proposal', 'details': candidate()['request'], 'due_date': '2026-10-23'}
    intent = {'intent': sorted(set(re.findall(r'[a-z0-9]+',value['title'].lower())) -
        {'send','share','email','provide','the','a','an','to','for','please','me','us'})}
    if kind == 'description':
        value = {'slot': 'general', 'text': 'Legacy general suggestion'}
        intent = {'slot': 'general'}
    fp = _fingerprint({'kind': kind, 'intent': intent, 'message': None if kind=='description' else 'reply-1',
        'evidence': sorted(fingerprints.values()) if kind=='description' else fingerprints['reply-1']})
    change_id = None
    if state == 'undone' and kind == 'task_create':
        created = TaskService(people).create(pid, title=value['title'], timezone='UTC', actor='director',
            operation_id='legacy-created-task')
        change_id = created['change']['change_id']
        TaskService(people).update(pid, created['task']['task_id'], changes={'state':'cancelled'},
            expected_version=1, timezone='UTC', actor='director', operation_id='legacy-cancel-task')
    with people.transaction():
        if state != 'undone':
            sources = legacy_sources if kind=='description' else [r for r in legacy_sources if r['message_id']=='reply-1']
            people._conn.execute("""INSERT INTO crm_proposals(user_id,proposal_id,person_id,target_kind,target_id,
                target_slot,proposed_value,expected_target_version,reason,source_refs,intent_key,
                support_source_fingerprint,input_revision,state,created_at,updated_at)
                VALUES(%s,'legacy-proposal',%s,%s,NULL,%s,%s,0,'Preserve review',%s,%s,%s,0,%s,'2026-10-22','2026-10-22')""",
                (people._user_id, pid, kind, 'general' if kind=='description' else None,
                 value['text'] if kind=='description' else json.dumps(value),
                 json.dumps(_legacy_source_ids(people, pid, sources)), 'legacy:'+kind+':legacy-proposal', fp, state))
        people._conn.execute(
            "INSERT INTO crm_interpretations(user_id,person_id,fingerprint,change_id,suppressed) VALUES(%s,%s,%s,%s,%s)",
            (people._user_id, pid, fp, change_id, int(state=='undone')))
        if change_id is not None:
            change = people._conn.execute(
                "SELECT * FROM crm_changes WHERE user_id=%s AND change_id=%s", (people._user_id, change_id)).fetchone()
            task = json.loads(change['after_json'])
            people._conn.execute("""INSERT INTO crm_proposals(user_id,proposal_id,person_id,target_kind,
                proposed_value,expected_target_version,reason,source_refs,intent_key,support_source_fingerprint,
                input_revision,state,resolution,change_id,created_at,updated_at)
                VALUES(%s,%s,%s,'task_create',%s,0,%s,%s,%s,%s,0,'dismissed','automatic',%s,%s,%s)""",
                (people._user_id, 'proposal_legacy_' + hashlib.sha256(change_id.encode()).hexdigest()[:24], pid,
                 json.dumps({k: task.get(k) for k in ('title', 'details', 'due_date')}), change['reason'],
                 change['source_refs'], 'legacy-auto:' + change_id, fp, change_id,
                 change['created_at'], change['created_at']))
    return PersonStore(tmp_path), pid, mail


@pytest.mark.parametrize('kind', ['task_create','description'])
@pytest.mark.parametrize('state', ['pending','dismissed','undone'])
def test_upgrade_keeps_unchanged_legacy_decision_without_duplicate(tmp_path, kind, state):
    people,pid,mail = legacy_store(tmp_path,kind=kind,state=state)
    model = TaskModel(tasks=[candidate()] if kind=='task_create' else [])
    # Detailed description is unrelated work; only general has an old decision.
    service = worker(people,model,mail)
    previous = [r[0] for r in people._conn.execute("SELECT proposal_id FROM crm_proposals WHERE user_id=%s AND person_id=%s AND " +
        ("target_kind='description' AND target_slot='general'" if kind=='description' else "target_kind IN ('task_create','task_update')"),(people._user_id,pid))]
    result = asyncio.run(service.analyze(pid))
    assert result['status']=='analyzed',result
    rows = people._conn.execute("SELECT * FROM crm_proposals WHERE user_id=%s AND person_id=%s AND " +
        ("target_kind='description' AND target_slot='general'" if kind=='description' else "target_kind IN ('task_create','task_update')"),(people._user_id,pid)).fetchall()
    if state=='pending':
        assert [r['proposal_id'] for r in rows] == ['legacy-proposal']
        assert 'legacy-proposal' in result['proposal_ids']
    elif state=='dismissed':
        assert [r['proposal_id'] for r in rows] == ['legacy-proposal']
    else:
        assert [r['proposal_id'] for r in rows]==previous
    if kind=='task_create':
        assert TaskService(people).list(person_id=pid,timezone='UTC')['tasks']==[]
    else:
        assert people.descriptions(pid)['general']['text'] is None


@pytest.mark.parametrize('kind', ['task_create','description'])
def test_changed_body_is_new_evidence_after_legacy_dismissal(tmp_path, kind):
    people,pid,mail=legacy_store(tmp_path,kind=kind,state='dismissed')
    mail.body=BODY+' This is a new explicit message revision and a renewed request.'
    result=asyncio.run(worker(people,TaskModel(tasks=[candidate()] if kind=='task_create' else []),mail).analyze(pid))
    assert result['status']=='analyzed',result
    if kind=='task_create':
        assert TaskService(people).list(person_id=pid,timezone='UTC')['tasks'] or people._conn.execute("SELECT count(*) FROM crm_proposals WHERE user_id=%s AND person_id=%s AND target_kind IN ('task_create','task_update')",(people._user_id,pid)).fetchone()[0]>1
    else:
        assert people.descriptions(pid)['general']['text']


@pytest.mark.parametrize('kind', ['task_create','description'])
@pytest.mark.parametrize('invalid_saved', ['account','partial','missing','changed-fingerprint'])
def test_legacy_decision_needs_exact_full_saved_mail_identity(tmp_path, kind, invalid_saved):
    people,pid,mail=legacy_store(tmp_path,kind=kind,state='dismissed')
    with people.crm_transaction():
        if invalid_saved=='account':
            people._conn.execute("UPDATE gmail_messages SET account='other@club.test' WHERE user_id=%s AND message_id='reply-1'", (people._user_id,))
        elif invalid_saved=='partial':
            people._conn.execute("UPDATE gmail_messages SET body_state='partial' WHERE user_id=%s AND message_id='reply-1'", (people._user_id,))
        elif invalid_saved=='missing':
            people._conn.execute("DELETE FROM gmail_messages WHERE user_id=%s AND message_id='reply-1'", (people._user_id,))
        else:
            people._conn.execute("UPDATE gmail_messages SET fingerprint='new-fingerprint' WHERE user_id=%s AND message_id='reply-1'", (people._user_id,))
    service=worker(people,TaskModel(tasks=[candidate()] if kind=='task_create' else []),mail)
    result=asyncio.run(service.analyze(pid))
    assert result['status']=='analyzed',result
    if kind=='description':
        assert people.descriptions(pid)['general']['text']
    else:
        assert TaskService(people).list(person_id=pid,timezone='UTC')['tasks'] or people._conn.execute("SELECT count(*) FROM crm_proposals WHERE user_id=%s AND person_id=%s AND target_kind IN ('task_create','task_update')",(people._user_id,pid)).fetchone()[0]>1


def test_public_alias_cannot_reactivate_legacy_private_evidence(tmp_path):
    people,pid,mail=legacy_store(tmp_path,kind='task_create',state='dismissed')
    people.upsert_attachment(pid,record_type='source_ref',fields={
        'id':'gmail:reply-1','message_id':'reply-1','account':'director@club.test','sensitivity':'restricted'},
        idempotency_key='private-legacy-alias',actor='director',rationale_summary='Private source')
    model=TaskModel()
    result=asyncio.run(worker(people,model,mail).analyze(pid))
    assert result['status']=='partial' and result['failure_code']=='private_source',result
    assert model.calls==[]
    assert TaskService(people).list(person_id=pid,timezone='UTC')['tasks']==[]
