"""D01: corrupt root mirrors fail before stale outer inventory can mask the defect."""
import csv
import io
import json
import shutil
from pathlib import Path
import pytest
from streammeta import release as r, reporting
from streammeta.io import atomic_json, digest, file_sha
from streammeta.manifest_contract import (
    CANONICAL_RESULT_MANIFEST as CANONICAL,
    DECLARED_RESULT_MANIFEST_MIRRORS as MIRRORS,
    verify_manifest_contract, publish_manifest_mirrors,
)
from test_release_integrity import fixture_release


def mutate(f, mutation, target='v5_0_1_result_manifest.csv'):
    path=f.root/target;table=list(csv.reader(io.StringIO(path.read_text())))
    if mutation=='missing_mirror':path.unlink();return
    if mutation=='extra_mirror':shutil.copyfile(path,f.root/'v5_unapproved_result_manifest.csv');return
    if mutation=='column_order':table=[list(reversed(row)) for row in table]
    elif mutation=='row_order':table=[table[0],*reversed(table[1:])]
    elif mutation=='run_id':table[1][table[0].index('run_id')]='rejected-1'
    elif mutation=='provenance_cell':table[1][table[0].index('config_id')]='wrong-config'
    elif mutation=='scientific_cell':table[1][table[0].index('roc_auc_exact')]='1.0'
    elif mutation=='stale_a01':table[1][table[0].index('artifacts_manifest_sha256')]='7335e86313680202e42f84624a83fc251c4a6617a828c4be1f20336089b9518f'
    elif mutation=='duplicate_row':table.append(table[1])
    elif mutation=='missing_row':table.pop()
    elif mutation=='added_row':
        extra=table[1].copy();extra[table[0].index('run_id')]='added-run';table.append(extra)
    elif mutation=='wrong_schema':table[0][table[0].index('run_id')]='wrong_id'
    elif mutation=='format_only':path.write_bytes(path.read_bytes().replace(b'\n',b'\r\n'));return
    else:raise AssertionError(mutation)
    with path.open('w',newline='') as handle:csv.writer(handle,lineterminator='\n').writerows(table)


def assert_all_guards_reject(f, baseline_freeze, match='Result-manifest inconsistency'):
    # A normal, accurate, NEW inventory must not legitimize an inconsistent CSV.
    r.write_inventory(f.root)
    assert r.verify_inventory(f.root)==file_sha(f.root/r.INVENTORY_NAME)
    with pytest.raises(RuntimeError,match=match):r.validate_release(f.out)
    with pytest.raises(RuntimeError,match=match):r.seal_release(f.out)
    # Isolate the verifier too: its synthetic frozen envelope binds the refreshed
    # inventory, eliminating an intentionally stale outer-inventory explanation.
    baseline_freeze['package_inventory_sha256']=file_sha(f.root/r.INVENTORY_NAME)
    atomic_json(f.root/r.FREEZE_NAME,baseline_freeze)
    with pytest.raises(RuntimeError,match=match):r.verify_frozen_release(f.root)


@pytest.mark.parametrize('mirror',MIRRORS)
@pytest.mark.parametrize('mutation',[
    'missing_mirror','extra_mirror','column_order','row_order','run_id',
    'provenance_cell','scientific_cell','stale_a01','duplicate_row',
    'missing_row','added_row','wrong_schema','format_only',
])
def test_mirror_corruption_rejected_after_fresh_inventory(fixture_release,mirror,mutation):
    f=fixture_release;baseline=r.seal_release(f.out)
    assert r.verify_frozen_release(f.root)['passed']
    (f.root/r.FREEZE_NAME).unlink()
    mutate(f,mutation,mirror)
    with pytest.raises(RuntimeError,match='Result-manifest inconsistency'):reporting.collect(f.out)
    assert_all_guards_reject(f,baseline)


@pytest.mark.parametrize('mirror,mutation',[
    ('v5_result_manifest.csv','stale_a01'),
    ('v5_0_1_result_manifest.csv','scientific_cell'),
])
def test_exact_auditor_counterexamples(fixture_release,mirror,mutation):
    f=fixture_release;baseline=r.seal_release(f.out);(f.root/r.FREEZE_NAME).unlink()
    canonical=(f.root/CANONICAL).read_bytes();mutate(f,mutation,mirror)
    assert (f.root/CANONICAL).read_bytes()==canonical
    assert_all_guards_reject(f,baseline)


@pytest.mark.parametrize('when',['after_validation','after_inventory_before_freeze'])
def test_late_mirror_change_cannot_freeze(fixture_release,when):
    f=fixture_release
    # The fixture already has the accepted validation gate. Inventory is accurate
    # in the first case and deliberately old in the second: same semantic guard.
    if when=='after_inventory_before_freeze':r.write_inventory(f.root)
    mutate(f,'scientific_cell','v5_0_2_result_manifest.csv')
    if when=='after_validation':r.write_inventory(f.root)
    with pytest.raises(RuntimeError,match='Result-manifest inconsistency'):r.seal_release(f.out)
    assert not (f.root/r.FREEZE_NAME).exists()


def test_validation_and_freeze_bind_complete_mirror_contract(fixture_release,monkeypatch):
    f=fixture_release;seen=[]
    def scientific_fixture_check(path,summary):
        seen.append(summary['run_id']);return {'run_id':summary['run_id'],'passed':True,'n':2}
    monkeypatch.setattr('streammeta.validation.validate_run',scientific_fixture_check)
    r.validate_release(f.out)
    contract=verify_manifest_contract(f.root,f.policy['accepted_artifacts'])
    assert contract['canonical']['path']==CANONICAL
    assert contract['declared_mirror_paths']==list(MIRRORS)
    assert contract['mirror_sha256']=={p:file_sha(f.root/p) for p in MIRRORS}
    assert contract['schema_sha256']==digest({'columns':contract['columns'],'row_width':len(contract['columns']),'encoding':'utf-8'})
    assert contract['row_count']==2 and contract['byte_equal'] is True
    assert contract['accepted_run_sequence_sha256']==digest(['accepted-0','accepted-1'])
    assert set(seen)==set(f.policy['accepted_artifacts'])
    payload=r.read_json(f.out/'release/evidence_validation.json');gate=r.read_json(f.out/'release/evidence_gate.json')
    assert payload['bindings']['result_manifest_contract']==contract
    assert gate['manifest_contract_sha256']==digest(contract)
    r.write_inventory(f.root);freeze=r.seal_release(f.out)
    assert freeze['result_manifest_contract']==contract and freeze['manifest_contract_sha256']==digest(contract)
    assert r.verify_frozen_release(f.root)['passed']


@pytest.mark.parametrize('field',['manifest_contract_sha256','result_manifest_contract'])
def test_frozen_contract_field_cannot_be_forged(fixture_release,field):
    f=fixture_release;freeze=r.seal_release(f.out);freeze[field]='wrong';atomic_json(f.root/r.FREEZE_NAME,freeze)
    with pytest.raises(RuntimeError,match='Frozen manifest contract mismatch'):r.verify_frozen_release(f.root)


def setup_collector(f,monkeypatch):
    monkeypatch.setattr(reporting,'source_state',r.source_state)
    monkeypatch.setattr(reporting,'environment_signature',r.environment_signature)
    def existing_table(output,destination,*args):
        shutil.copyfile(f.root/CANONICAL,destination/'v5_result_manifest.csv');return {'fixture':True}
    monkeypatch.setattr(reporting,'_collect_tables',existing_table)


def test_collector_publishes_and_binds_all_declared_mirrors(fixture_release,monkeypatch):
    f=fixture_release;setup_collector(f,monkeypatch);reporting.collect(f.out)
    contract=verify_manifest_contract(f.root)
    assert r.read_json(f.out/'release/collection.json')['result_manifest_contract']==contract
    assert not (f.out/'collection_in_progress.json').exists()


def test_interrupted_mirror_publication_blocks_freeze_and_recovers(fixture_release,monkeypatch):
    from streammeta import manifest_contract as mc
    f=fixture_release;setup_collector(f,monkeypatch);replace=mc.os.replace
    def interrupted(source,destination):
        if Path(destination)==f.root/'v5_0_1_result_manifest.csv':raise OSError('injected mirror replacement failure')
        return replace(source,destination)
    with monkeypatch.context() as m:
        m.setattr(mc.os,'replace',interrupted)
        with pytest.raises(OSError,match='injected mirror'):reporting.collect(f.out)
    assert (f.out/'collection_in_progress.json').exists()
    with pytest.raises(RuntimeError,match='Incomplete aggregate'):r.inspect_links(f.root,f.policy)
    assert not list(f.root.glob('*result_manifest.csv.*.partial'))
    reporting.collect(f.out)
    assert not (f.out/'collection_in_progress.json').exists()
    assert verify_manifest_contract(f.root)['byte_equal']


def test_verifiers_do_not_repair_a_corrupt_mirror(fixture_release):
    f=fixture_release;mutate(f,'scientific_cell');path=f.root/'v5_0_1_result_manifest.csv';data=path.read_bytes()
    with pytest.raises(RuntimeError,match='Result-manifest inconsistency'):verify_manifest_contract(f.root)
    assert path.read_bytes()==data
