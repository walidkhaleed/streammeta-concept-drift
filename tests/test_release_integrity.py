"""Temporary provenance fixtures only. Never read/write accepted V5 runs."""
from pathlib import Path
import csv
import json
import shutil
from types import SimpleNamespace
import pytest
from streammeta.io import atomic_json, digest, file_sha
from streammeta import release as r
from streammeta.reporting import manifest_frame
from streammeta.provenance import archive_source
from streammeta.immutability import compare_bytes
from streammeta.manifest_contract import publish_manifest_mirrors, verify_manifest_contract


def rewrite_csv(path, rows):
    with path.open('w', newline='') as handle:
        w=csv.DictWriter(handle,fieldnames=list(rows[0]));w.writeheader();w.writerows(rows)


@pytest.fixture
def fixture_release(tmp_path, monkeypatch):
    root=tmp_path/'release';out=root/'outputs';src=tmp_path/'scientific_source';src.mkdir()
    (src/'a.py').write_text('# synthetic fixture source\n')
    files={'a.py':file_sha(src/'a.py')};source={'files':files,'source_sha256':digest(files),'git_commit':'fixture-commit','git_dirty':False}
    monkeypatch.setattr('streammeta.provenance.project_root',lambda:src)
    archive=archive_source(out,source)
    env={'python':'3.12.14 fixture','packages':{'fixture':'1'}}
    env['environment_signature']=digest({'python':'3.12.14','packages':env['packages']})
    util=root/'src/streammeta/fixture.py';util.parent.mkdir(parents=True);util.write_text('# fixture utility source\n')
    util_files={'src/streammeta/fixture.py':file_sha(util)}
    utility={'source_sha256':digest(util_files),'git_dirty':False,'git_commit':'fixture-utility-commit','files':util_files}
    monkeypatch.setattr(r,'source_state',lambda:utility)
    monkeypatch.setattr(r,'environment_signature',lambda:'fixture-utility-env')
    jobs=[];run_artifacts={}
    for i in range(2):
        rid=f'accepted-{i}';p=out/'runs'/rid;p.mkdir(parents=True)
        config={'stage':'primary','arm':'fixture','replicate':i,'dataset':'Fixture','method':'Meta'};jobs.append(config)
        summary={**config,'run_id':rid,'config_id':digest(config),'status':'complete','source_sha256':source['source_sha256'],
                 'environment_signature':env['environment_signature'],'roc_auc_exact':.7+i/10,'n':2}
        provenance={'run_id':rid,'config_id':digest(config),'status':'complete','source':source,
                    'source_snapshot':archive.relative_to(out).as_posix(),'source_snapshot_sha256':file_sha(archive)}
        for name,obj in [('config.json',config),('summary.json',summary),('provenance.json',provenance),('environment.json',env)]:atomic_json(p/name,obj)
        (p/'predictions.fixture').write_bytes(b'synthetic immutable trace '+bytes([i]))
        artifacts={f.name:{'bytes':f.stat().st_size,'sha256':file_sha(f)} for f in p.iterdir()}
        atomic_json(p/'artifacts.json',artifacts);run_artifacts[rid]=file_sha(p/'artifacts.json')
    atomic_json(out/'plans/primary_plan.json',{'jobs':jobs,'plan_sha256':digest(jobs)})
    rejected=out/'development_history'
    atomic_json(rejected/'artifact_integrity_incident/rejected_runs.json',{'runs':[{'run_id':'rejected-1'}]})
    p=rejected/'null_calibration_incident/tables/v5_result_manifest.csv';p.parent.mkdir(parents=True)
    p.write_text('run_id\nrejected-2\n')
    tables=out/'tables';tables.mkdir()
    manifest_frame(out,source['source_sha256'],env['environment_signature']).to_csv(tables/'v5_result_manifest.csv',index=False)
    publish_manifest_mirrors(root)
    anchors=root/'provenance/original_v5';anchors.mkdir(parents=True)
    shutil.copyfile(tables/'v5_result_manifest.csv',anchors/'result_manifest.csv')
    atomic_json(anchors/'freeze.json',{'source':source,'environment_signature':env['environment_signature'],'run_artifacts':run_artifacts,'run_count':2})
    inventory={'files':{'v5/'+p.relative_to(root).as_posix():{'bytes':p.stat().st_size,'sha256':file_sha(p)}
                        for p in out.rglob('*') if p.is_file() and '.locks' not in p.parts}}
    atomic_json(anchors/'inventory.json',inventory)
    monkeypatch.setattr(r,'ANCHORS',{name:file_sha(anchors/name) for name in ('freeze.json','inventory.json')})
    policy=r.create_policy(root)
    bindings=r.inspect_links(root,policy)
    payload={'release':r.VERSION,'passed':True,'bindings':bindings,'utility_source_sha256':utility['source_sha256'],
             'utility_environment_signature':'fixture-utility-env','scientific_checks':[{'run_id':rid,'passed':True,'n':2} for rid in run_artifacts]}
    atomic_json(out/'release/evidence_validation.json',payload)
    gate={'passed':True,'payload_sha256':file_sha(out/'release/evidence_validation.json'),
          'utility_source_sha256':utility['source_sha256'],'utility_environment_signature':'fixture-utility-env',
          **{k:bindings[k] for k in ('run_set_sha256','artifact_versions_sha256','result_manifest_sha256','manifest_contract_sha256','policy_sha256')}}
    atomic_json(out/'release/evidence_gate.json',gate)
    # Synthetic gate metadata exercises sealing; it is never a production gate.
    junit=out/'release/fixture-tests.xml';junit.write_text('<testsuites><testsuite tests="34" failures="0" errors="0" skipped="0"/></testsuites>')
    shutil.copyfile(junit,out/'release/provenance_tests.xml')
    report=out/'release/fixture-tests.txt';report.write_text('Synthetic fixture gate for unit testing only.\n')
    atomic_json(out/'release/test_gate.json',{'passed':True,'utility_source_sha256':utility['source_sha256'],'utility_environment_signature':'fixture-utility-env',
        'junit':junit.relative_to(root).as_posix(),'junit_sha256':file_sha(junit),'report':report.relative_to(root).as_posix(),
        'dedicated_provenance_junit_sha256':file_sha(junit),'dedicated_provenance_counts':{'tests':34,'failures':0,'errors':0,'skipped':0},
        'report_sha256':file_sha(report),'counts':{'tests':34,'failures':0,'errors':0,'skipped':0}})
    atomic_json(out/'release/clean_checkout.json',{'passed':True,'utility_source_sha256':utility['source_sha256'],'utility_git_commit':utility['git_commit'],'test_counts':{'tests':34,'failures':0,'errors':0,'skipped':0}})
    atomic_json(out/'release/collection.json',{'utility_source_sha256':utility['source_sha256'],'policy_sha256':digest(policy),
                'tables':{p.name:file_sha(p) for p in tables.iterdir()},'result_manifest_contract':verify_manifest_contract(root)})
    atomic_json(root/'scientific_result_diff.json',{'scientific_values_changed':0,'missing_files':0,'new_files':0})
    _,counts=compare_bytes(root);atomic_json(root/'provenance/scientific_immutability_summary.json',counts)
    r.write_inventory(root)
    return SimpleNamespace(root=root,out=out,policy=policy)


def reseal_payload(f):
    path=f.out/'release/evidence_gate.json';gate=r.read_json(path)
    gate['payload_sha256']=file_sha(f.out/'release/evidence_validation.json');atomic_json(path,gate)


@pytest.mark.parametrize('mutation',[
    'stale_artifact_pointer','modified_artifact','wrong_run_row','changed_config','changed_plan_content','changed_plan_hash',
    'omitted_manifest_run','omitted_run_directory','duplicate_run','inserted_rejected_run','stale_validation_payload',
    'validation_omits_run','stale_validated_artifact_version','mismatched_manifest_hash','changed_source','changed_environment',
    'wrong_validated_run_set_hash','changed_policy','changed_inventory','changed_scientific_value','incomplete_collection',
])
def test_freeze_rejects_integrity_mutations(fixture_release,mutation):
    f=fixture_release;manifest=f.out/'tables/v5_result_manifest.csv';rows=r.csv_rows(manifest)
    run=f.out/'runs/accepted-0'
    if mutation=='stale_artifact_pointer':rows[0]['artifacts_manifest_sha256']='stale';rewrite_csv(manifest,rows)
    elif mutation=='modified_artifact':(run/'predictions.fixture').write_bytes(b'changed after validation')
    elif mutation=='wrong_run_row':rows[0]['run_path']=rows[1]['run_path'];rewrite_csv(manifest,rows)
    elif mutation=='changed_config':
        p=run/'config.json';d=r.read_json(p);d['replicate']=999;atomic_json(p,d)
    elif mutation in ('changed_plan_content','changed_plan_hash'):
        p=f.out/'plans/primary_plan.json';d=r.read_json(p)
        if mutation=='changed_plan_content':d['jobs'][0]['replicate']=99
        else:d['plan_sha256']='wrong'
        atomic_json(p,d)
    elif mutation=='omitted_manifest_run':rewrite_csv(manifest,rows[1:])
    elif mutation=='omitted_run_directory':shutil.rmtree(run)
    elif mutation=='duplicate_run':rewrite_csv(manifest,rows+[rows[0]])
    elif mutation=='inserted_rejected_run':shutil.copytree(run,f.out/'runs/rejected-1')
    elif mutation=='stale_validation_payload':
        p=f.out/'release/evidence_validation.json';d=r.read_json(p);d['scientific_checks'][0]['n']=100;atomic_json(p,d)
    elif mutation=='validation_omits_run':
        p=f.out/'release/evidence_validation.json';d=r.read_json(p);d['scientific_checks'].pop();atomic_json(p,d);reseal_payload(f)
    elif mutation=='stale_validated_artifact_version':
        p=f.out/'release/evidence_validation.json';d=r.read_json(p);d['bindings']['runs']['accepted-0']['artifacts_manifest_sha256']='old';atomic_json(p,d);reseal_payload(f)
    elif mutation in ('mismatched_manifest_hash','wrong_validated_run_set_hash'):
        p=f.out/'release/evidence_gate.json';d=r.read_json(p);d['result_manifest_sha256' if mutation=='mismatched_manifest_hash' else 'run_set_sha256']='wrong';atomic_json(p,d)
    elif mutation in ('changed_source','changed_environment'):
        p=run/'summary.json';d=r.read_json(p);d['source_sha256' if mutation=='changed_source' else 'environment_signature']='wrong';atomic_json(p,d)
    elif mutation=='changed_policy':
        p=f.root/'provenance/release_policy.json';d=r.read_json(p);d['accepted_artifacts'].pop('accepted-1');atomic_json(p,d)
    elif mutation=='changed_inventory':
        p=f.root/r.INVENTORY_NAME;d=r.read_json(p);d['files'].pop(next(iter(d['files'])));atomic_json(p,d)
    elif mutation=='changed_scientific_value':rows[0]['roc_auc_exact']='1.0';rewrite_csv(manifest,rows)
    elif mutation=='incomplete_collection':atomic_json(f.out/'collection_in_progress.json',{'partial':True})
    with pytest.raises(RuntimeError):r.seal_release(f.out)
    assert not (f.root/r.FREEZE_NAME).exists()


def test_valid_fixture_freezes_and_verifies_exact_set(fixture_release):
    f=fixture_release;freeze=r.seal_release(f.out)
    assert freeze['run_artifacts']==f.policy['accepted_artifacts']
    assert r.verify_frozen_release(f.root)['verified_runs']==2
    with pytest.raises(RuntimeError,match='already frozen'):r.seal_release(f.out)


@pytest.mark.parametrize('mutation',['freeze_map','freeze_manifest_hash','inventory_hash','new_payload_file','missing_payload_file'])
def test_post_freeze_verifier_rejects_mutations(fixture_release,mutation):
    f=fixture_release;r.seal_release(f.out);p=f.root/r.FREEZE_NAME;d=r.read_json(p)
    if mutation=='freeze_map':d['run_artifacts']['accepted-0']='wrong'
    elif mutation=='freeze_manifest_hash':d['result_manifest_sha256']='wrong'
    elif mutation=='inventory_hash':d['package_inventory_sha256']='wrong'
    elif mutation=='new_payload_file':(f.root/'unexpected.txt').write_text('extra')
    else:(f.out/'release/fixture-tests.txt').unlink()
    atomic_json(p,d)
    with pytest.raises(RuntimeError):r.verify_frozen_release(f.root)


def test_original_trust_anchor_cannot_be_rewritten(fixture_release):
    f=fixture_release;p=f.root/'provenance/original_v5/freeze.json';d=r.read_json(p);d['run_count']=0;atomic_json(p,d)
    with pytest.raises(RuntimeError,match='anchor changed'):r.load_policy(f.root)


def test_rejected_history_is_not_discovered_by_collector(fixture_release):
    f=fixture_release
    frame=manifest_frame(f.out,f.policy['scientific_source']['source_sha256'],f.policy['scientific_environment'],set(f.policy['accepted_artifacts']))
    assert set(frame.run_id)=={'accepted-0','accepted-1'}
    assert not set(frame.run_id).intersection(f.policy['rejected_run_ids'])


@pytest.mark.parametrize('mutation',['modified_packaged_source','extra_packaged_source','stale_clean_checkout'])
def test_packaged_source_must_match_tested_checkout(fixture_release,mutation):
    f=fixture_release
    if mutation=='modified_packaged_source':(f.root/'src/streammeta/fixture.py').write_text('# untested change\n')
    elif mutation=='extra_packaged_source':(f.root/'src/streammeta/extra.py').write_text('# untested extra module\n')
    else:
        p=f.out/'release/clean_checkout.json';d=r.read_json(p);d['utility_source_sha256']='untested';atomic_json(p,d)
    with pytest.raises(RuntimeError):r.seal_release(f.out)
    assert not (f.root/r.FREEZE_NAME).exists()
