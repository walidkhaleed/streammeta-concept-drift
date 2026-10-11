"""Offline D01 patch preparation. Never launch a scientific campaign."""
from pathlib import Path, PurePosixPath
import argparse
import csv
import json
import os
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET
import zipfile
from streammeta.io import atomic_json, digest, file_sha
from streammeta.provenance import source_state, environment_signature, project_root, utc_now, THREAD_VARS
from streammeta.release import create_policy, require, original_anchors, csv_rows, keyed, FREEZE_NAME
from streammeta.manifest_contract import verify_manifest_contract, publish_manifest_mirrors, frozen_release_exists
from streammeta.immutability import compare_bytes

BASE_SHA256='d1ef71cf6c558203b19231f08e6fed85a69aeae9c91d0bfffef599ad058282b0'


def stage(archive, destination):
    root=Path(destination).resolve();code=project_root()
    require(not root.exists(),'Destination must be new; preserve V5 and V5.0.1')
    require(file_sha(archive)==BASE_SHA256,'Wrong V5.0.1 base archive')
    require(source_state()['git_dirty'] is False,'Commit patch utilities before staging')
    history=root/'provenance/v5_0_1';history.mkdir(parents=True)
    with zipfile.ZipFile(archive) as z:
        require(len(z.namelist())==len(set(z.namelist())),'Duplicate ZIP member')
        for item in z.infolist():
            name=PurePosixPath(item.filename)
            require(not name.is_absolute() and '..' not in name.parts and item.filename.startswith('v5_0_1/'),'Unsafe ZIP path')
            require((item.external_attr>>16)&0o170000!=0o120000,'Symlink ZIP member')
            if item.is_dir():continue
            rel=item.filename.removeprefix('v5_0_1/')
            target=history/rel if rel=='v5_0_1_freeze_manifest.json' else root/rel
            target.parent.mkdir(parents=True,exist_ok=True)
            with z.open(item) as src,target.open('wb') as dst:shutil.copyfileobj(src,dst)
    for rel in ['PACKAGE_CONTENTS_SHA256.json','source.git.bundle','provenance/release_policy.json','scientific_result_diff.json','outputs/tables/table_metadata.json']:
        target=history/rel;target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(root/rel,target)
    shutil.copytree(root/'outputs/release',history/'outputs/release',copy_function=shutil.copyfile)
    for rel in filter(None,subprocess.check_output(['git','ls-files','-z'],cwd=code).decode().split('\0')):
        target=root/rel;target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(code/rel,target)
    subprocess.run(['git','bundle','create',str(root/'source.git.bundle'),'--all'],cwd=code,check=True)
    canonical=(root/'outputs/tables/v5_result_manifest.csv').read_bytes()
    require(all((root/n).read_bytes()==canonical for n in ['v5_result_manifest.csv','v5_0_1_result_manifest.csv']), 'Base mirrors disagree')
    policy=create_policy(root)
    from streammeta.publication import publication_session
    with publication_session(root/'outputs'):
        marker=root/'outputs/collection_in_progress.json';atomic_json(marker,{'release':'V5.0.2','phase':'initial mirror publication'})
        contract=publish_manifest_mirrors(root,policy['accepted_artifacts']);marker.unlink()
    atomic_json(root/'provenance/v5_0_2_stage.json',{'base_archive_sha256':BASE_SHA256,'created_utc':utc_now(),
        'manifest_contract':contract,'science_campaigns_run':0,'manuscript_updated':False})
    return {'release':'V5.0.2','root':str(root),'manifest_contract_sha256':digest(contract),'science_campaigns_run':0}


def counts(path):
    tree=ET.parse(path).getroot();suites=[tree] if tree.tag=='testsuite' else list(tree)
    return {k:sum(int(s.get(k,0)) for s in suites) for k in ['tests','failures','errors','skipped']}


def tests(root):
    root=Path(root);code=project_root();source=source_state();environment=environment_signature()
    require(not frozen_release_exists(root),'Cannot test into a frozen release')
    require(source['git_dirty'] is False,'Use a clean committed checkout')
    import streammeta
    require(Path(streammeta.__file__).resolve().is_relative_to(code),'Wrong imported source')
    env=dict(os.environ,PYTHONPATH=str(code/'src'),STREAMMETA_PROJECT_ROOT=str(code),PYTHONHASHSEED='0',PYTHONDONTWRITEBYTECODE='1',**{k:'1' for k in THREAD_VARS})
    dest=root/'outputs/release';commands=[];outputs=[];totals={}
    selections=[('acceptance',['tests/test_acceptance.py'],33),
                ('v5_0_1_provenance',['tests/test_release_integrity.py','tests/test_publication.py'],43),
                ('d01',['tests/test_manifest_contract.py'],49),
                ('full',['tests'],125),
                ('provenance',['tests/test_release_integrity.py','tests/test_publication.py','tests/test_manifest_contract.py'],92)]
    for name,targets,expected in selections:
        xml=dest/f'{name}_tests.xml';cmd=[sys.executable,'-m','pytest','-ra','-p','no:cacheprovider','--junitxml',str(xml),*targets]
        result=subprocess.run(cmd,cwd=code,env=env,text=True,stdout=subprocess.PIPE,stderr=subprocess.STDOUT)
        (dest/f'{name}_tests.log').write_text('COMMAND: '+json.dumps(cmd)+'\n'+result.stdout)
        commands.append(cmd);outputs.append(result.stdout);totals[name]=counts(xml)
        require(result.returncode==0 and totals[name]==dict(tests=expected,failures=0,errors=0,skipped=0),f'{name} suite failed')
    require(source==source_state() and environment==environment_signature(),'Source/environment changed during tests')
    metadata={'release':'V5.0.2','created_utc':utc_now(),'interpreter':sys.executable,'module_path':streammeta.__file__,
        'checkout':str(code),'utility_source_sha256':source['source_sha256'],'utility_git_commit':source['git_commit'],
        'utility_environment_signature':environment,'counts':totals,'commands':commands,'science_campaigns_run':0,'manuscript_updated':False}
    report=root/'v5_0_2_test_report.txt';report.write_text('V5.0.2 CLEAN-CHECKOUT TEST REPORT\n'+json.dumps(metadata,indent=2)+'\n\n'+'\n\n'.join(outputs))
    atomic_json(dest/'test_gate.json',{'passed':True,'utility_source_sha256':source['source_sha256'],'utility_environment_signature':environment,
        'counts':totals['full'],'old_test_count':33,'v5_0_1_provenance_test_count':43,'new_d01_test_count':49,
        'junit':'outputs/release/full_tests.xml','junit_sha256':file_sha(dest/'full_tests.xml'),
        'report':'v5_0_2_test_report.txt','report_sha256':file_sha(report),
        'dedicated_provenance_junit_sha256':file_sha(dest/'provenance_tests.xml'),'dedicated_provenance_counts':totals['provenance']})
    atomic_json(dest/'clean_checkout.json',{'passed':True,'utility_source_sha256':source['source_sha256'],
        'utility_git_commit':source['git_commit'],'test_counts':totals['full'],'module_path':streammeta.__file__,'interpreter':sys.executable})
    atomic_json(dest/'test_execution.json',metadata)
    return metadata


def compare(root):
    root=Path(root);require(not frozen_release_exists(root),'Cannot compare into a frozen release')
    contract=verify_manifest_contract(root)
    rows,summary=compare_bytes(root)
    require(summary==dict(identical=9577,different=0,missing=0,new=0,scientific_files_compared=9577,original_scientific_files=9577),'Scientific bytes changed')
    rows=[{'path':r['path'],'v5_sha256':r['v5_sha256'],
           'v5_0_2_sha256':r['v5_0_1_sha256'],'status':r['status']} for r in rows]
    with (root/'scientific_immutability_v5_to_v5_0_2.csv').open('w',newline='') as h:
        w=csv.DictWriter(h,fieldnames=list(rows[0]));w.writeheader();w.writerows(rows)
    atomic_json(root/'provenance/scientific_immutability_summary.json',summary)
    (root/'scientific_immutability_v5_to_v5_0_2.md').write_text('# V5 → V5.0.2 scientific byte identity\n\n9,577 identical; zero different, missing or new. All four inherited partial stream files remain unchanged. Root manifests are separately bound by the V5.0.2 manifest contract. No scientific experiment was run.\n')
    _,inventory=original_anchors(root);before=root/'provenance/original_v5/scientific_tables';compared=[]
    for p in sorted(before.rglob('*')):
        if not p.is_file():continue
        rel=p.relative_to(before).as_posix();after=root/rel
        require(file_sha(p)==inventory['files']['v5/'+rel]['sha256'],'Original table anchor changed')
        if p.suffix=='.csv':
            with p.open(newline='') as h:a=list(csv.reader(h))
            with after.open(newline='') as h:b=list(csv.reader(h))
        else:a=json.loads(p.read_text());b=json.loads(after.read_text())
        require(a==b and p.read_bytes()==after.read_bytes(),'Scientific table values/bytes changed: '+rel)
        compared.append({'path':rel,'changed_values':0,'comparison':'exact CSV cell text / JSON value equality','bytes_identical':True})
    require(len(compared)==28,'Unexpected scientific table scope')
    old=csv_rows(root/'provenance/original_v5/result_manifest.csv');new=csv_rows(root/'outputs/tables/v5_result_manifest.csv')
    require([r['run_id'] for r in old]==[r['run_id'] for r in new] and list(old[0])==list(new[0]),'Manifest schema/order/run IDs changed')
    changes=[{'run_id':a['run_id'],'column':k,'v5_value':v,'v5_0_2_value':b[k]} for a,b in zip(old,new) for k,v in a.items() if v!=b[k]]
    require(changes==[dict(run_id='controlled-Agrawal-Meta-8616c654f8-da6dd829',column='artifacts_manifest_sha256',
        v5_value='7335e86313680202e42f84624a83fc251c4a6617a828c4be1f20336089b9518f',
        v5_0_2_value='5bcda8c20456578588fbd7b692c1dc82ca1fcad95809ded617393daed5a8f002')],'Unexpected V5 manifest delta')
    result={'release':'V5.0.2','scientific_values_changed':0,'manifest_scientific_cells_changed':0,'accepted_run_ids_changed':0,
        'accepted_runs':len(new),'missing_files':0,'new_files':0,'new_scientific_experiments':0,'science_campaigns_run':0,
        'manuscript_updated':False,'numerical_tolerance':0,'scientific_summary_files_compared':len(compared),'comparisons':compared,
        'allowed_provenance_changes':changes,'manifest_contract_sha256':digest(contract)}
    atomic_json(root/'scientific_result_diff_v5_to_v5_0_2.json',result)
    atomic_json(root/'scientific_result_diff.json',result)
    atomic_json(root/'provenance/pre_inventory_manifest_check.json',{'manifest_contract':contract,'manifest_contract_sha256':digest(contract)})
    return {'scientific_bytes':summary,'scientific_values_changed':0,'manifest_contract_sha256':digest(contract)}


def main():
    p=argparse.ArgumentParser(description=__doc__);sub=p.add_subparsers(dest='command',required=True)
    s=sub.add_parser('stage');s.add_argument('--base-archive',required=True);s.add_argument('--destination',required=True)
    for name in ['tests','compare','check-manifests']:
        s=sub.add_parser(name);s.add_argument('--release-root',required=True)
    a=p.parse_args()
    if a.command=='stage':result=stage(a.base_archive,a.destination)
    elif a.command=='tests':result=tests(Path(a.release_root).resolve())
    elif a.command=='compare':result=compare(Path(a.release_root).resolve())
    else:result=verify_manifest_contract(Path(a.release_root).resolve())
    print(json.dumps(result,sort_keys=True),flush=True)


if __name__=='__main__':main()
