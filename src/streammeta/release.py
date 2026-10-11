"""V5.0.2: strict, anchored correction of previously audited evidence.

Scientific runs retain V5 source/environment identities. The release utilities
and their tests have a separate identity. This profile cannot certify new runs.
"""
from pathlib import Path, PurePosixPath
import csv
import hashlib
import io
import json
import xml.etree.ElementTree as ET

from .io import atomic_json, digest, file_sha
from .provenance import source_state, environment_signature, utc_now, verify_source_archive
from .manifest_contract import verify_manifest_contract, frozen_release_exists

VERSION = 'V5.0.2'
ANCHORS = {
    'freeze.json': '4c5bda443fa1c17429108e3b1cfcc3d1f5da756081f124fef48fa32c32604b95',
    'inventory.json': 'cb21ac077e43327a4f506d0b1fb733326f62ebc903e31c5a86e2d72c1e19ecb9',
}
INVENTORY_NAME = 'PACKAGE_CONTENTS_SHA256.json'
FREEZE_NAME = 'v5_0_2_freeze_manifest.json'
# The acyclic envelope is checked separately. No scientific path is excluded.
ENVELOPES = {INVENTORY_NAME, FREEZE_NAME}


def read_json(path):
    return json.loads(Path(path).read_text())


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def inside(root, rel):
    rel = PurePosixPath(rel)
    require(not rel.is_absolute() and '..' not in rel.parts, f'Unsafe relative path: {rel}')
    path = Path(root).joinpath(*rel.parts)
    require(not path.is_symlink() and path.resolve().is_relative_to(Path(root).resolve()), f'Unsafe symlink: {path}')
    return path


def csv_rows(path):
    with Path(path).open(newline='') as handle:
        reader = csv.DictReader(handle)
        require(reader.fieldnames is not None and len(reader.fieldnames) == len(set(reader.fieldnames)), 'Duplicate/missing CSV columns')
        rows = list(reader)
    require(all(None not in row and all(v is not None for v in row.values()) for row in rows), 'Malformed CSV row')
    return rows


def keyed(rows):
    ids = [row['run_id'] for row in rows]
    require(len(ids) == len(set(ids)), 'Duplicate accepted run ID')
    return dict(zip(ids, rows))


def original_anchors(root):
    base = Path(root)/'provenance/original_v5'
    for name, expected in ANCHORS.items():
        require(file_sha(base/name) == expected, f'Original V5 anchor changed: {name}')
    freeze, inventory = read_json(base/'freeze.json'), read_json(base/'inventory.json')
    require(digest(freeze['source']['files']) == freeze['source']['source_sha256'], 'Original source-member identity mismatch')
    expected = inventory['files']['v5/outputs/tables/v5_result_manifest.csv']['sha256']
    require(file_sha(base/'result_manifest.csv') == expected, 'Original result-manifest anchor changed')
    return freeze, inventory


def derive_policy(root):
    root = Path(root)
    freeze, inventory = original_anchors(root)
    rows = keyed(csv_rows(root/'provenance/original_v5/result_manifest.csv'))
    require(set(rows) == set(freeze['run_artifacts']) and len(rows) == freeze['run_count'], 'Original accepted-run set mismatch')
    files = inventory['files']
    rejected = set()
    for rel in ['outputs/development_history/artifact_integrity_incident/rejected_runs.json',
                'outputs/development_history/null_calibration_incident/tables/v5_result_manifest.csv']:
        path = inside(root, rel)
        require(file_sha(path) == files['v5/'+rel]['sha256'], 'Rejected-run declaration changed')
        records = read_json(path)['runs'] if path.suffix == '.json' else csv_rows(path)
        rejected.update(r['run_id'] for r in records)
    require(not rejected.intersection(rows), 'Rejected and accepted run sets overlap')
    return {
        'release': VERSION, 'scientific_source': freeze['source'],
        'scientific_environment': freeze['environment_signature'],
        'original_anchors': ANCHORS,
        'accepted_artifacts': freeze['run_artifacts'],
        'config_ids': {rid: row['config_id'] for rid, row in rows.items()},
        'approved_unplanned_run_ids': sorted(rid for rid, row in rows.items()
                                             if row['stage'] == 'controlled' and row['arm'] == 'clean_checkout'),
        'plans': {key.removeprefix('v5/'): val['sha256'] for key, val in files.items()
                  if key.startswith('v5/outputs/plans/') and key.endswith('.json')},
        'source_archives': {key.removeprefix('v5/'): val['sha256'] for key, val in files.items()
                            if key.startswith('v5/outputs/source_snapshots/') and key.endswith('.zip')},
        'rejected_run_ids': sorted(rejected),
    }


def load_policy(root, path=None):
    path = Path(path) if path else Path(root)/'provenance/release_policy.json'
    policy = read_json(path)
    require(policy == derive_policy(root), 'Release policy differs from immutable V5 anchors')
    return policy


def create_policy(root):
    policy = derive_policy(root)
    atomic_json(Path(root)/'provenance/release_policy.json', policy)
    return policy


def inspect_evidence(root, policy):
    """No fitting: authenticate every accepted byte and every identity edge."""
    root = Path(root); output = root/'outputs'
    expected = set(policy['accepted_artifacts'])
    actual = {p.name for p in (output/'runs').iterdir()}
    require(actual == expected, 'Accepted run directory set mismatch (missing, extra or rejected run)')
    require(not expected.intersection(policy['rejected_run_ids']), 'Rejected run inserted')
    plans = {}; configurations = {}
    require({str(p.relative_to(root)) for p in (output/'plans').glob('*.json')} == set(policy['plans']), 'Plan set mismatch')
    for rel, expected_sha in policy['plans'].items():
        path = inside(root, rel); plan = read_json(path)
        require(digest(plan['jobs']) == plan['plan_sha256'], f'Plan content hash mismatch: {rel}')
        require(file_sha(path) == expected_sha, f'Plan differs from audited content: {rel}')
        plans[rel] = {'file_sha256': file_sha(path), 'content_sha256': plan['plan_sha256']}
        for config in plan['jobs']:
            configurations.setdefault(digest(config), []).append(rel)
    require(set(configurations).issubset(set(policy['config_ids'].values())), 'Planned accepted configuration missing')
    for rel, expected_sha in policy['source_archives'].items():
        path = inside(root, rel)
        require(file_sha(path) == expected_sha, 'Source ZIP container identity mismatch')
        verify_source_archive(path, policy['scientific_source'])
    records = {}
    for rid in sorted(expected):
        path = inside(output/'runs', rid)
        require(path.is_dir(), f'Missing run: {rid}')
        artifacts_path = path/'artifacts.json'
        artifact_sha = file_sha(artifacts_path)
        require(artifact_sha == policy['accepted_artifacts'][rid], f'Artifact manifest version changed: {rid}')
        artifacts = read_json(artifacts_path)
        actual_files = {p.relative_to(path).as_posix() for p in path.rglob('*') if p.is_file() and p != artifacts_path}
        require(actual_files == set(artifacts), f'Artifact file set mismatch: {rid}')
        for rel, rec in artifacts.items():
            artifact = inside(path, rel)
            require(artifact.stat().st_size == rec['bytes'] and file_sha(artifact) == rec['sha256'], f'Artifact corruption: {rid}/{rel}')
        config, summary, provenance, env = (read_json(path/name) for name in ('config.json', 'summary.json', 'provenance.json', 'environment.json'))
        cid = digest(config)
        require(cid == policy['config_ids'][rid] == summary['config_id'] == provenance['config_id'], f'Config hash mismatch: {rid}')
        require(summary['run_id'] == provenance['run_id'] == rid, f'Run ID mismatch: {rid}')
        require(summary['status'] == provenance['status'] == 'complete', f'Incomplete accepted run: {rid}')
        require(cid in configurations or rid in policy['approved_unplanned_run_ids'], f'Unplanned config: {rid}')
        src = provenance['source']
        require(digest(src['files']) == src['source_sha256'] == summary['source_sha256'] == policy['scientific_source']['source_sha256'], f'Source hash mismatch: {rid}')
        require(src['files'] == policy['scientific_source']['files'] and src['git_commit'] == policy['scientific_source']['git_commit'] and src['git_dirty'] is False, f'Source identity mismatch: {rid}')
        env_hash = digest({'python': env['python'].split()[0], 'packages': env['packages']})
        require(env_hash == env['environment_signature'] == summary['environment_signature'] == policy['scientific_environment'], f'Environment hash mismatch: {rid}')
        snapshot = 'outputs/'+provenance['source_snapshot']
        require(snapshot in policy['source_archives'] and provenance['source_snapshot_sha256'] == policy['source_archives'][snapshot], f'Run source-archive pointer mismatch: {rid}')
        records[rid] = {'config_id': cid, 'artifacts_manifest_sha256': artifact_sha,
                        'source_sha256': src['source_sha256'], 'environment_signature': env_hash,
                        'plans': {name: plans[name] for name in configurations.get(cid, [])},
                        'supplemental_audited_clean_checkout': rid in policy['approved_unplanned_run_ids']}
    return {'runs': records, 'plans': plans, 'run_set_sha256': digest(sorted(records)),
            'artifact_versions_sha256': digest({rid: rec['artifacts_manifest_sha256'] for rid, rec in records.items()})}


def inspect_links(root, policy):
    from .reporting import manifest_frame
    root = Path(root); output = root/'outputs'
    require(not (output/'collection_in_progress.json').exists(), 'Incomplete aggregate publication; rerun collector')
    contract = verify_manifest_contract(root, policy['accepted_artifacts'])
    bindings = inspect_evidence(root, policy)
    bindings['result_manifest_contract'] = contract
    bindings['manifest_contract_sha256'] = digest(contract)
    path = output/'tables/v5_result_manifest.csv'
    rows = keyed(csv_rows(path))
    expected_ids = set(policy['accepted_artifacts'])
    require(set(rows) == expected_ids, 'Manifest accepted run set mismatch')
    frame = manifest_frame(output, policy['scientific_source']['source_sha256'], policy['scientific_environment'], expected_ids)
    generated = keyed(list(csv.DictReader(io.StringIO(frame.to_csv(index=False)))))
    for rid, row in rows.items():
        require(row['artifacts_manifest_sha256'] == bindings['runs'][rid]['artifacts_manifest_sha256'], f'Stale artifacts_manifest_sha256: {rid}')
        require(row == generated[rid], f'Result-manifest row differs from authoritative run: {rid}')
        bindings['runs'][rid]['manifest_row_sha256'] = digest(row)
    bindings['result_manifest_sha256'] = file_sha(path)
    bindings['policy_sha256'] = digest(policy)
    return bindings


def validate_release(output, policy_path=None):
    from .publication import publication_session
    from .validation import validate_run
    output = Path(output); root = output.parent
    with publication_session(output):
        require(not frozen_release_exists(root), 'Immutable release: cannot revalidate in place')
        policy = load_policy(root, policy_path)
        bindings = inspect_links(root, policy)
        source = source_state(); env = environment_signature()
        results = []
        for i, rid in enumerate(sorted(bindings['runs']), 1):
            path = output/'runs'/rid
            results.append(validate_run(path, read_json(path/'summary.json')))
            if i % 20 == 0:
                print(json.dumps({'validated_existing_runs': i, 'total': len(bindings['runs'])}), flush=True)
        require(bindings == inspect_links(root, policy), 'Evidence changed during validation')
        require(source['source_sha256'] == source_state()['source_sha256'] and env == environment_signature(), 'Utility source/environment changed during validation')
        payload = {'release': VERSION, 'validated_utc': utc_now(), 'bindings': bindings,
                   'utility_source_sha256': source['source_sha256'], 'utility_environment_signature': env,
                   'scientific_checks': results, 'passed': True}
        path = output/'release/evidence_validation.json'
        atomic_json(path, payload)
        gate = {'release': VERSION, 'passed': True, 'payload_sha256': file_sha(path),
                'run_set_sha256': bindings['run_set_sha256'], 'artifact_versions_sha256': bindings['artifact_versions_sha256'],
                'result_manifest_sha256': bindings['result_manifest_sha256'], 'manifest_contract_sha256': bindings['manifest_contract_sha256'], 'policy_sha256': digest(policy),
                'utility_source_sha256': source['source_sha256'], 'utility_environment_signature': env}
        atomic_json(output/'release/evidence_gate.json', gate)
        return {'passed': True, 'existing_runs_validated': len(results), 'scientific_experiments_run': 0, **gate}


def verify_evidence_gate(root, policy):
    root = Path(root); base = root/'outputs/release'
    bindings = inspect_links(root, policy)
    gate, payload = read_json(base/'evidence_gate.json'), read_json(base/'evidence_validation.json')
    require(gate['passed'] is True and payload['passed'] is True, 'Failed evidence gate')
    require(file_sha(base/'evidence_validation.json') == gate['payload_sha256'], 'Stale validation payload hash')
    require(payload['bindings'] == bindings, 'Validation is stale for current artifact versions / manifest / run set')
    checks = payload['scientific_checks']
    require(set(keyed(checks)) == set(bindings['runs']) and all(r['passed'] is True for r in checks), 'Exact validated scientific run set mismatch')
    for key in ('run_set_sha256', 'artifact_versions_sha256', 'result_manifest_sha256', 'manifest_contract_sha256', 'policy_sha256'):
        require(gate[key] == bindings[key], f'Evidence gate {key} mismatch')
    for key, actual in [('utility_source_sha256', source_state()['source_sha256']), ('utility_environment_signature', environment_signature())]:
        require(gate[key] == payload[key] == actual, f'Stale release {key}')
    return bindings, gate


def payload_files(root):
    root = Path(root); files = {}
    for path in sorted(root.rglob('*')):
        rel = path.relative_to(root).as_posix()
        if not path.is_file() or rel in ENVELOPES:
            continue
        parts = Path(rel).parts
        if any(x in ('.git', '__pycache__', '.pytest_cache', '.locks') or x.endswith('.egg-info') for x in parts) or path.suffix == '.pyc':
            continue
        require(not path.is_symlink(), f'Package contains a symlink: {rel}')
        files[rel] = {'bytes': path.stat().st_size, 'sha256': file_sha(path)}
    return files


def write_inventory(root):
    root = Path(root)
    require(not frozen_release_exists(root), 'Immutable release: cannot replace inventory')
    inventory = {'release': VERSION, 'excluded_envelopes': sorted(ENVELOPES), 'files': payload_files(root),
                 'chain': 'payload -> inventory -> freeze -> external identity/archive checksum; no circular self-hash'}
    atomic_json(root/INVENTORY_NAME, inventory)
    return inventory


def verify_inventory(root, expected_sha=None):
    root = Path(root); path = root/INVENTORY_NAME
    if expected_sha:
        require(file_sha(path) == expected_sha, 'Package inventory checksum mismatch')
    inventory = read_json(path)
    require(inventory['release'] == VERSION and inventory['excluded_envelopes'] == sorted(ENVELOPES), 'Inventory envelope scope mismatch')
    require(inventory['files'] == payload_files(root), 'Package inventory content/set mismatch')
    return file_sha(path)


def verify_test_gate(root):
    root = Path(root); base = root/'outputs/release'
    gate = read_json(base/'test_gate.json')
    require(gate['passed'] is True and gate['utility_source_sha256'] == source_state()['source_sha256'] and gate['utility_environment_signature'] == environment_signature(), 'Stale test gate')
    require((root/gate['junit']).is_file() and (root/gate['report']).is_file(), 'Test report payload missing')
    require(file_sha(root/gate['junit']) == gate['junit_sha256'] and file_sha(root/gate['report']) == gate['report_sha256'], 'Test report payload changed')
    tree = ET.parse(root/gate['junit']).getroot()
    suites = [tree] if tree.tag == 'testsuite' else list(tree)
    counts = {k: sum(int(s.get(k, 0)) for s in suites) for k in ('tests', 'failures', 'errors', 'skipped')}
    require(counts == gate['counts'] and counts['tests'] > 33 and all(counts[k] == 0 for k in ('failures', 'errors', 'skipped')), 'Full old/new test suite has not passed')
    dedicated = root/'outputs/release/provenance_tests.xml'
    require(dedicated.is_file(), 'Dedicated provenance test payload missing')
    require(file_sha(dedicated) == gate['dedicated_provenance_junit_sha256'], 'Dedicated provenance test payload changed')
    tree = ET.parse(dedicated).getroot()
    suites = [tree] if tree.tag == 'testsuite' else list(tree)
    dedicated_counts = {k: sum(int(s.get(k, 0)) for s in suites) for k in ('tests', 'failures', 'errors', 'skipped')}
    require(dedicated_counts == gate['dedicated_provenance_counts'] and dedicated_counts['tests'] > 0 and all(dedicated_counts[k] == 0 for k in ('failures', 'errors', 'skipped')), 'Dedicated provenance tests have not passed')
    return gate


def verify_packaged_source(root, source):
    from .provenance import source_files
    root=Path(root)
    files={path.relative_to(root).as_posix():file_sha(path) for path in source_files(root)}
    require(files == source['files'] and digest(files) == source['source_sha256'],
            'Packaged utility source differs from tested clean checkout')


def verify_clean_checkout(root, source, test_gate):
    clean=read_json(Path(root)/'outputs/release/clean_checkout.json')
    require(clean['passed'] is True and clean['utility_source_sha256'] == source['source_sha256']
            and clean['utility_git_commit'] == source['git_commit'] and clean['test_counts'] == test_gate['counts'],
            'Clean-checkout identity/test evidence missing or stale')
    return clean


def seal_release(output, policy_path=None):
    from .publication import publication_session
    output = Path(output); root = output.parent
    with publication_session(output):
        require(not frozen_release_exists(root), 'Release already frozen; do not overwrite a freeze')
        source = source_state()
        require(source['git_dirty'] is False, 'Commit utility source before freezing')
        verify_packaged_source(root, source)
        policy = load_policy(root, policy_path)
        bindings, gate = verify_evidence_gate(root, policy)
        test_gate = verify_test_gate(root)
        verify_clean_checkout(root, source, test_gate)
        collection = read_json(output/'release/collection.json')
        require(collection['utility_source_sha256'] == source['source_sha256'] and collection['policy_sha256'] == digest(policy), 'Stale collection identity')
        table_hashes = {p.name: file_sha(p) for p in sorted((output/'tables').iterdir()) if p.is_file()}
        require(collection['tables'] == table_hashes, 'Aggregate tables changed after collection')
        require(collection['result_manifest_contract'] == bindings['result_manifest_contract'], 'Collection manifest contract is stale')
        immutable = read_json(root/'scientific_result_diff.json')
        require(immutable['scientific_values_changed'] == 0 and immutable['missing_files'] == 0 and immutable['new_files'] == 0, 'Scientific values changed')
        byte_report = read_json(root/'provenance/scientific_immutability_summary.json')
        require(byte_report['different'] == byte_report['missing'] == byte_report['new'] == 0, 'Scientific bytes changed')
        from .immutability import compare_bytes
        _, actual_immutability = compare_bytes(root)
        require(actual_immutability == byte_report, 'Immutability report stale')
        inventory_sha = verify_inventory(root)
        freeze = {'release': VERSION, 'status': 'ready_for_independent_delta_audit', 'frozen_utc': utc_now(),
                  'statement': 'V5.0.2 is a provenance/package integrity patch derived from V5.0.1. No accepted scientific artifact, numerical result, run ID, experiment configuration, or manuscript content changed.',
                  'utility_source': source, 'utility_environment_signature': environment_signature(),
                  'scientific_source_sha256': policy['scientific_source']['source_sha256'],
                  'scientific_environment_signature': policy['scientific_environment'],
                  'original_v5_anchors': ANCHORS, 'run_count': len(bindings['runs']), 'bindings': bindings,
                  'run_artifacts': {rid: rec['artifacts_manifest_sha256'] for rid, rec in bindings['runs'].items()},
                  'result_manifest_sha256': bindings['result_manifest_sha256'],
                  'result_manifest_contract': bindings['result_manifest_contract'],
                  'manifest_contract_sha256': bindings['manifest_contract_sha256'], 'package_inventory_sha256': inventory_sha,
                  'evidence_gate_sha256': file_sha(output/'release/evidence_gate.json'),
                  'evidence_payload_sha256': gate['payload_sha256'], 'test_gate': test_gate,
                  'collection_sha256': file_sha(output/'release/collection.json')}
        require(freeze['run_artifacts'] == policy['accepted_artifacts'], 'Freeze-map entries mismatch')
        atomic_json(root/FREEZE_NAME, freeze)
        return freeze


def verify_frozen_release(root):
    root = Path(root); freeze = read_json(root/FREEZE_NAME)
    policy = load_policy(root)
    bindings, gate = verify_evidence_gate(root, policy)
    current_source=source_state()
    verify_packaged_source(root, current_source)
    verify_clean_checkout(root, current_source, verify_test_gate(root))
    require(freeze['release'] == VERSION and all(freeze['utility_source'][k] == current_source[k] for k in ('source_sha256','files','git_commit')), 'Frozen utility source identity mismatch')
    require(freeze['scientific_source_sha256'] == policy['scientific_source']['source_sha256'] and freeze['scientific_environment_signature'] == policy['scientific_environment'], 'Frozen scientific identity mismatch')
    require(freeze['utility_environment_signature'] == environment_signature(), 'Frozen utility environment mismatch')
    require(freeze['original_v5_anchors'] == ANCHORS, 'Frozen original anchors mismatch')
    require(freeze['test_gate'] == verify_test_gate(root), 'Frozen test gate mismatch')
    require(freeze['collection_sha256'] == file_sha(root/'outputs/release/collection.json'), 'Frozen collection identity mismatch')
    require(freeze['bindings'] == bindings, 'Frozen evidence bindings mismatch')
    require(freeze['run_artifacts'] == policy['accepted_artifacts'] and freeze['run_count'] == len(bindings['runs']), 'Freeze-map entry/set mismatch')
    require(freeze['result_manifest_sha256'] == bindings['result_manifest_sha256'], 'Frozen result-manifest hash mismatch')
    require(freeze['result_manifest_contract'] == bindings['result_manifest_contract'] and freeze['manifest_contract_sha256'] == bindings['manifest_contract_sha256'], 'Frozen manifest contract mismatch')
    require(freeze['evidence_payload_sha256'] == gate['payload_sha256'] and freeze['evidence_gate_sha256'] == file_sha(root/'outputs/release/evidence_gate.json'), 'Frozen evidence gate mismatch')
    verify_inventory(root, freeze['package_inventory_sha256'])
    return {'release': VERSION, 'verified_runs': len(bindings['runs']), 'inventory_sha256': freeze['package_inventory_sha256'], 'freeze_sha256': file_sha(root/FREEZE_NAME), 'passed': True}
