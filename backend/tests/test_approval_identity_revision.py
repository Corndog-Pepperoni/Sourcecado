"""Approval identity freshness is monotonic and independent of unrelated CRM edits."""
import json

from coworker.crm_tasks import TaskService
from coworker.people import PersonStore


def keep(people, **fields):
    return people.keep_from_apollo(apollo_id='person-id',first_name=fields.get('first_name','Ada'),
        last_name_obfuscated='Lovelace',title=fields.get('title','Lead'),company=fields.get('company','Analytic'))


def test_identity_revision_changes_for_facts_and_aba_but_not_provider_replays(tmp_path):
    people=PersonStore(tmp_path);person=keep(people);pid=person['person_id']
    assert person['identity_revision']==0
    assert people.apply_enrichment(pid,email='a@example.test')['identity_revision']==1
    assert people.apply_enrichment(pid,email='a@example.test')['identity_revision']==1
    assert people.apply_enrichment(pid,email='b@example.test')['identity_revision']==2
    assert people.apply_enrichment(pid,email='a@example.test')['identity_revision']==3
    assert people.apply_enrichment(pid,phone='555-0100',title='Director')['identity_revision']==3
    assert people.apply_enrichment(pid,company='Other company')['identity_revision']==4
    assert PersonStore(tmp_path).get(pid)['identity_revision']==4


def test_keep_patch_and_revert_share_monotonic_identity_revision(tmp_path):
    people=PersonStore(tmp_path);person=keep(people);pid=person['person_id']
    assert keep(people)['identity_revision']==0
    assert keep(people,title='New role')['identity_revision']==0
    assert keep(people,company='Another company')['identity_revision']==1
    current=people.get(pid)
    patched=people.patch(pid,fields={'first_name':'Augusta'},expected_version=current['version'],
        actor='director',rationale_summary='Correct name')
    assert patched['identity_revision']==2
    restored=people.revert(pid,to_version=1,expected_version=patched['version'],
        actor='director',rationale_summary='Restore original identity')
    assert restored['identity_revision']==3
    assert restored['first_name']=='Ada'
    history=json.loads(people._conn.execute('SELECT person_json FROM person_versions WHERE user_id = %s AND person_id=%s AND version=%s',
        (people._user_id,pid,restored['version'])).fetchone()[0])
    assert history['identity_revision']==3


def test_unrelated_task_description_and_status_edits_do_not_stale_approval_identity(tmp_path):
    people=PersonStore(tmp_path);pid=keep(people)['person_id']
    TaskService(people).create(pid,title='Call Ada',timezone='UTC',actor='director',operation_id='identity-unrelated-task')
    people.patch_description(pid,'general',text='Operator notes',expected_version=0,
        actor='director',rationale_summary='Save notes')
    people.set_sequence(pid,'open',actor='director')
    assert people.get(pid)['identity_revision']==0
