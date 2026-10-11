"""Offline provenance reconstruction. This script never invokes a scientific run.

Use with explicit PYTHONPATH and STREAMMETA_PROJECT_ROOT pointing to a clean
checkout of the V5.0.1 source package; see PROVENANCE_RELEASE.md.
"""
from pathlib import Path, PurePosixPath
import argparse
import json
import os
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET
import zipfile

from streammeta.io import atomic_json, file_sha
from streammeta.provenance import source_state, environment_signature, project_root, THREAD_VARS, utc_now
from streammeta.release import create_policy, require

INPUT_HASHES = {
    'accepted':'44af327689945447b2306cb310e016f887ed6c762444e4f847b40fbcfa27fab6',
    'code':'767649d166b8b10743b75cfab417a6e19662074f606e02e1893c1ccc9b6a71ce',
    'rejected':'b296f8b0383d1bb768ad5471807c37fbd095f49b9ab03fe4fd82f55adc96e9a3',
}


def stage(args):
    root=Path(args.destination).resolve()
    require(not root.exists(), 'Destination must be new; never update audited V5 in place')
    for name,expected in INPUT_HASHES.items():
        require(file_sha(getattr(args,name))==expected, f'Unexpected audited {name} archive')
    root.mkdir(parents=True)
    with zipfile.ZipFile(args.accepted) as z:
        require(len(z.namelist())==len(set(z.namelist())), 'Duplicate ZIP members')
        for name in z.namelist():
            path=PurePosixPath(name)
            require(not path.is_absolute() and '..' not in path.parts, 'Unsafe archive member')
            if not name.startswith('v5/') or name.endswith('/'):continue
            dest=root/name.removeprefix('v5/');dest.parent.mkdir(parents=True,exist_ok=True)
            with z.open(name) as source,dest.open('wb') as handle:shutil.copyfileobj(source,handle)
        original=root/'provenance/original_v5';original.mkdir(parents=True)
        (original/'inventory.json').write_bytes(z.read('PACKAGE_CONTENTS_SHA256.json'))
        (original/'package_readme.md').write_bytes(z.read('README_AUDIT_PACKAGE.md'))
    shutil.copyfile(root/'outputs/v5_freeze_manifest.json',original/'freeze.json')
    shutil.copyfile(root/'outputs/tables/v5_result_manifest.csv',original/'result_manifest.csv')
    shutil.copyfile(root/'v5_result_manifest.csv',original/'root_result_manifest.csv')
    shutil.copyfile(root/'source.git.bundle',original/'source.git.bundle')
    shutil.copyfile(root/'README.md',original/'README.md')
    shutil.copyfile(args.code,original/'v5_codebase.zip')
    shutil.copyfile(args.audit_report,original/'independent_v5_audit_report.md')
    rejected=root/'provenance/rejected';rejected.mkdir()
    shutil.copyfile(args.rejected,rejected/'v5_rejected_development_campaign.zip')
    for p in list((root/'outputs/tables').glob('*'))+list((root/'outputs/figures').glob('*_source.csv'))+[
        root/n for n in ('v5_primary_results.csv','v5_window_results.csv','v5_delay_results.csv','v5_routing_baselines.csv')]:
        if p.name in ('v5_result_manifest.csv','table_metadata.json'):continue
        dest=original/'scientific_tables'/p.relative_to(root);dest.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(p,dest)
    code=project_root()
    tracked=subprocess.check_output(['git','ls-files','-z'],cwd=code).decode().split('\0')
    for rel in filter(None,tracked):
        source=code/rel
        if not source.is_file():continue
        dest=root/rel;dest.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(source,dest)
    # The source bundle is exported by packaging from this committed checkout.
    subprocess.run(['git','bundle','create',str(root/'source.git.bundle'),'--all'],cwd=code,check=True)
    f=json.loads((original/'freeze.json').read_text())
    identity={'release':'V5','source_sha256':f['source']['source_sha256'],'git_commit':f['source']['git_commit'],
              'environment_signature':f['environment_signature'],'frozen_utc':f['frozen_utc'],
              'freeze_sha256':file_sha(original/'freeze.json'),'inventory_sha256':file_sha(original/'inventory.json'),
              'result_manifest_sha256':file_sha(original/'result_manifest.csv')}
    for name in (*INPUT_HASHES,'audit_report'):
        path=Path(getattr(args,name));identity[path.name]={'bytes':path.stat().st_size,'sha256':file_sha(path)}
    atomic_json(root/'original_v5_identity.json',identity)
    policy=create_policy(root)
    return {'staged_release':str(root),'accepted_runs':len(policy['accepted_artifacts']),
            'rejected_run_ids':len(policy['rejected_run_ids']),'scientific_runs_executed':0}


def counts(path):
    xml=ET.parse(path).getroot();suites=[xml] if xml.tag=='testsuite' else list(xml)
    return {key:sum(int(s.get(key,0)) for s in suites) for key in ('tests','failures','errors','skipped')}


def tests(root):
    root=Path(root);code=project_root();before=source_state();env=environment_signature()
    require(not (root/'v5_0_1_freeze_manifest.json').exists(), 'Immutable release: test in a new staging tree')
    require(before['git_dirty'] is False,'Tests for release must run from a clean committed checkout')
    import streammeta
    require(Path(streammeta.__file__).resolve().is_relative_to(code.resolve()),'Editable old source shadows clean checkout')
    dest=root/'outputs/release';dest.mkdir(parents=True,exist_ok=True)
    runtime=dict(os.environ,PYTHONPATH=str(code/'src'),STREAMMETA_PROJECT_ROOT=str(code),PYTHONHASHSEED='0',**{k:'1' for k in THREAD_VARS})
    commands=[];outputs=[];result_counts={}
    for name,targets in [('full',[str(code/'tests')]),('provenance',[str(code/'tests/test_release_integrity.py'),str(code/'tests/test_publication.py')])]:
        command=[sys.executable,'-m','pytest','-ra','--junitxml',str(dest/f'{name}_tests.xml'),*targets]
        result=subprocess.run(command,cwd=code,env=runtime,text=True,stdout=subprocess.PIPE,stderr=subprocess.STDOUT)
        commands.append(command);outputs.append(result.stdout);result_counts[name]=counts(dest/f'{name}_tests.xml')
        (dest/f'{name}_tests.log').write_text('COMMAND: '+json.dumps(command)+'\n'+result.stdout)
        c=result_counts[name]
        require(result.returncode==0 and all(c[k]==0 for k in ('failures','errors','skipped')),f'{name} tests failed; see {dest}')
    full=ET.parse(dest/'full_tests.xml').getroot()
    old=sum('test_acceptance' in c.get('classname','') for c in full.iter('testcase'))
    require(old==33,'Original scientific suite count changed')
    require(before['source_sha256']==source_state()['source_sha256'] and env==environment_signature(),'Source/environment changed during tests')
    report='V5.0.1 CLEAN-CHECKOUT TEST REPORT\n'+json.dumps({
        'created_utc':utc_now(),'interpreter':sys.executable,'module_path':streammeta.__file__,'checkout':str(code),
        'utility_source_sha256':before['source_sha256'],'utility_git_commit':before['git_commit'],
        'utility_environment_signature':env,'old_test_count':old,'new_regression_test_count':result_counts['full']['tests']-old,
        'new_total_test_count':result_counts['full']['tests'],'counts':result_counts,'commands':commands,
        'science_campaigns_run':0},indent=2)+'\n\n'+'\n\n'.join(outputs)
    report_path=root/'v5_0_1_test_report.txt';report_path.write_text(report)
    atomic_json(dest/'test_gate.json',{'passed':True,'utility_source_sha256':before['source_sha256'],'utility_environment_signature':env,
        'counts':result_counts['full'],'old_test_count':old,'new_regression_test_count':result_counts['full']['tests']-old,
        'junit':'outputs/release/full_tests.xml','junit_sha256':file_sha(dest/'full_tests.xml'),
        'report':'v5_0_1_test_report.txt','report_sha256':file_sha(report_path),
        'dedicated_provenance_junit_sha256':file_sha(dest/'provenance_tests.xml'),'dedicated_provenance_counts':result_counts['provenance']})
    return result_counts


def main():
    parser=argparse.ArgumentParser(description=__doc__);sub=parser.add_subparsers(dest='command',required=True)
    p=sub.add_parser('stage')
    for name in ('accepted','code','rejected','audit-report','destination'):p.add_argument('--'+name,required=True)
    for name in ('tests','compare'):
        p=sub.add_parser(name);p.add_argument('--release-root',required=True)
    args=parser.parse_args()
    if args.command=='stage':result=stage(args)
    elif args.command=='tests':result=tests(Path(args.release_root).resolve())
    else:
        from streammeta.immutability import write_comparisons
        root=Path(args.release_root).resolve()
        shutil.copyfile(root/'outputs/tables/v5_result_manifest.csv',root/'v5_result_manifest.csv')
        result=write_comparisons(root)
    print(json.dumps(result,sort_keys=True),flush=True)


if __name__=='__main__':main()
