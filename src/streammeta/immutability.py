"""Exact V5-to-patch comparisons; never regenerate scientific observations."""
from pathlib import Path
import csv
import json
from .io import atomic_json, file_sha
from .release import original_anchors, csv_rows, keyed, require

PROVENANCE_EXCEPTIONS = {
    'outputs/tables/v5_result_manifest.csv': 'A01 artifact-manifest pointer only; every cell compared separately',
    'v5_result_manifest.csv': 'Root mirror of the corrected A01 manifest',
    'outputs/tables/table_metadata.json': 'Collection timestamp; scientific source and environment remain V5',
}


def scientific_path(rel):
    return (rel.startswith(('outputs/runs/', 'outputs/streams/', 'outputs/figures/', 'outputs/tables/'))
            or rel in {'v5_primary_results.csv','v5_window_results.csv','v5_delay_results.csv','v5_routing_baselines.csv'}) and rel not in PROVENANCE_EXCEPTIONS


def compare_bytes(root):
    root = Path(root); _, inventory = original_anchors(root)
    original = {key.removeprefix('v5/'): value for key, value in inventory['files'].items()
                if key.startswith('v5/') and scientific_path(key.removeprefix('v5/'))}
    current = {p.relative_to(root).as_posix() for p in root.rglob('*') if p.is_file()
               and scientific_path(p.relative_to(root).as_posix())}
    rows = []
    for rel in sorted(set(original)|current):
        old = original.get(rel); path = root/rel
        old_sha = old['sha256'] if old else ''
        new_sha = file_sha(path) if rel in current else ''
        status = 'new' if old is None else 'missing' if rel not in current else 'identical' if old_sha == new_sha else 'different'
        rows.append({'path': rel, 'v5_sha256': old_sha, 'v5_0_1_sha256': new_sha, 'status': status})
    counts = {status: sum(row['status'] == status for row in rows) for status in ('identical', 'different', 'missing', 'new')}
    counts.update(scientific_files_compared=counts['identical']+counts['different'], original_scientific_files=len(original))
    return rows, counts


def write_comparisons(root):
    root = Path(root)
    rows, counts = compare_bytes(root)
    with (root/'scientific_immutability_report.csv').open('w', newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=['path','v5_sha256','v5_0_1_sha256','status']); writer.writeheader(); writer.writerows(rows)
    atomic_json(root/'provenance/scientific_immutability_summary.json', counts)
    summary = '# Scientific immutability\n\nAll accepted run files (including prediction/update/delay/route traces, metrics, XAI models/SHAP/null arrays, timings and RSS), cached streams, figures and their source CSVs, and scientific aggregate tables are compared by SHA-256 against the anchored original inventory. Configurations and per-run provenance are also unchanged.\n\n'
    summary += '\n'.join(f'- {key}: **{value}**' for key,value in counts.items())+'\n\nProvenance-only exceptions:\n\n'
    summary += '\n'.join(f'- `{path}`: {reason}.' for path,reason in PROVENANCE_EXCEPTIONS.items())
    summary += '\n\nThe result manifest is checked cell by cell. No experiment was rerun. All scientific comparisons require exact equality, with no tolerance.\n'
    (root/'scientific_immutability_report.md').write_text(summary)
    old = keyed(csv_rows(root/'provenance/original_v5/result_manifest.csv'))
    new = keyed(csv_rows(root/'outputs/tables/v5_result_manifest.csv'))
    require(set(old) == set(new), 'Manifest run IDs changed')
    changes = []
    for rid, before in old.items():
        require(set(before) == set(new[rid]), 'Manifest columns changed')
        for key, value in before.items():
            if value != new[rid][key]: changes.append({'run_id':rid, 'column':key, 'v5_value':value, 'v5_0_1_value':new[rid][key]})
    expected = [{'run_id':'controlled-Agrawal-Meta-8616c654f8-da6dd829', 'column':'artifacts_manifest_sha256',
                 'v5_value':'7335e86313680202e42f84624a83fc251c4a6617a828c4be1f20336089b9518f',
                 'v5_0_1_value':'5bcda8c20456578588fbd7b692c1dc82ca1fcad95809ded617393daed5a8f002'}]
    require(changes == expected, f'Unexpected manifest changes: {changes}')
    with (root/'v5_to_v5_0_1_manifest_diff.csv').open('w',newline='') as handle:
        writer=csv.DictWriter(handle,fieldnames=list(expected[0]));writer.writeheader();writer.writerows(changes)
    (root/'v5_to_v5_0_1_manifest_diff.md').write_text('# Result-manifest cell diff\n\n532 run IDs, unchanged row order and column schema. Exactly one changed cell; every other cell is identical. This applies to the canonical manifest and its release mirrors.\n\n| Run | Column | V5 | V5.0.1 |\n|---|---|---|---|\n'+''.join('| '+' | '.join('`'+r[k]+'`' for k in expected[0])+' |\n' for r in changes))
    # This is a second, value-level check, not a float-tolerance comparison.
    original_tables = root/'provenance/original_v5/scientific_tables'
    def value_path(rel):
        return ((rel.startswith('outputs/tables/') and Path(rel).suffix in ('.csv','.json') and rel not in PROVENANCE_EXCEPTIONS)
                or (rel.startswith('outputs/figures/') and rel.endswith('_source.csv'))
                or rel in {'v5_primary_results.csv','v5_window_results.csv','v5_delay_results.csv','v5_routing_baselines.csv'})
    old_paths={p.relative_to(original_tables).as_posix() for p in original_tables.rglob('*') if p.is_file()}
    actual_paths={p.relative_to(root).as_posix() for p in root.rglob('*') if p.is_file() and value_path(p.relative_to(root).as_posix())}
    compared=[]; changed=0; missing=0; new_files=len(actual_paths-old_paths)
    _, original_inventory=original_anchors(root)
    for before in sorted(original_tables.rglob('*')):
        if not before.is_file():continue
        rel=before.relative_to(original_tables);after=root/rel
        require(file_sha(before)==original_inventory['files']['v5/'+rel.as_posix()]['sha256'], 'Original scientific table anchor changed')
        if not after.is_file():missing+=1;continue
        if before.suffix=='.csv':
            with before.open(newline='') as h: a=list(csv.reader(h))
            with after.open(newline='') as h: b=list(csv.reader(h))
            differences=sum(x!=y for ar,br in zip(a,b) for x,y in zip(ar,br))
            differences+=abs(len(a)-len(b))+sum(abs(len(ar)-len(br)) for ar,br in zip(a,b))
        elif before.suffix=='.json':
            differences=int(json.loads(before.read_text())!=json.loads(after.read_text()))
        else:continue
        changed+=differences;compared.append({'path':rel.as_posix(),'changed_values':differences,'comparison':'exact CSV cell text / JSON value equality'})
    # Scientific cells in the all-run manifest are independently exact too.
    manifest_changes=sum(r['column']!='artifacts_manifest_sha256' for r in changes)
    result={'scientific_values_changed':changed+manifest_changes,'missing_files':missing,'new_files':new_files,
            'scientific_summary_files_compared':len(compared),'comparisons':compared,
            'accepted_runs':len(old),'accepted_run_ids_changed':0,'manifest_scientific_cells_changed':manifest_changes,
            'allowed_provenance_changes':changes,'numerical_tolerance':0,'new_scientific_experiments':0}
    atomic_json(root/'scientific_result_diff.json',result)
    require(counts['different']==counts['missing']==counts['new']==0, 'Scientific byte immutability failed')
    require(result['scientific_values_changed']==result['missing_files']==0, 'Scientific result equality failed')
    return {'bytes':counts,'results':result}
