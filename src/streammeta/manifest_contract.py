"""V5.0.2: one canonical result manifest and an exact set of byte-equal mirrors.

Only root *result_manifest*.csv entries are release mirrors. Historical copies
belong in provenance subdirectories. Publication callers hold the existing
exclusive publication lock and retain the incomplete-generation marker.
"""
from pathlib import Path
import csv
import hashlib
import io
import os
import tempfile
from .io import digest

CANONICAL_RESULT_MANIFEST = 'outputs/tables/v5_result_manifest.csv'
DECLARED_RESULT_MANIFEST_MIRRORS = (
    'v5_result_manifest.csv',
    'v5_0_1_result_manifest.csv',
    'v5_0_2_result_manifest.csv',
)
ERROR = 'Result-manifest inconsistency: '


def require(condition, message):
    if not condition:
        raise RuntimeError(ERROR + message)


def frozen_release_exists(root):
    return any(Path(root).glob('v5*_freeze_manifest.json'))


def mirror_paths(root):
    return {p.name for p in Path(root).glob('*result_manifest*.csv')}


def manifest_bytes(root, rel):
    root = Path(root); path = root / rel
    require(path.is_file() and not path.is_symlink()
            and path.resolve().is_relative_to(root.resolve()), f'missing or unsafe file: {rel}')
    return path.read_bytes()


def verify_manifest_contract(root, accepted_ids=None):
    """Read-only; no silent repair. Byte equality also binds all CSV structure."""
    root = Path(root)
    declared = list(DECLARED_RESULT_MANIFEST_MIRRORS)
    actual = mirror_paths(root)
    require(actual == set(declared),
            f'declared mirror set mismatch; missing={sorted(set(declared)-actual)}, extra={sorted(actual-set(declared))}')
    canonical = manifest_bytes(root, CANONICAL_RESULT_MANIFEST)
    try:
        table = list(csv.reader(io.StringIO(canonical.decode('utf-8')), strict=True))
    except (UnicodeDecodeError, csv.Error) as exc:
        raise RuntimeError(ERROR + 'malformed canonical CSV') from exc
    require(len(table) > 1, 'empty canonical result manifest')
    columns, rows = table[0], table[1:]
    require(len(columns) == len(set(columns)) and 'run_id' in columns and all(columns), 'invalid canonical schema')
    require(all(len(row) == len(columns) for row in rows), 'invalid canonical row width')
    ids = [row[columns.index('run_id')] for row in rows]
    require(all(ids) and len(ids) == len(set(ids)), 'duplicate or missing canonical run ID')
    if accepted_ids is not None:
        require(set(ids) == set(accepted_ids), 'canonical accepted run set mismatch')
    canonical_sha = hashlib.sha256(canonical).hexdigest()
    hashes = {}
    for rel in declared:
        data = manifest_bytes(root, rel)
        require(data == canonical, f'mirror differs from canonical: {rel}')
        hashes[rel] = hashlib.sha256(data).hexdigest()
    return {
        'contract_version': 'V5.0.2',
        'canonical': {'path': CANONICAL_RESULT_MANIFEST, 'sha256': canonical_sha},
        'declared_mirror_paths': declared, 'mirror_sha256': hashes,
        'byte_equal': True, 'columns': columns,
        'schema_sha256': digest({'columns': columns, 'row_width': len(columns), 'encoding': 'utf-8'}),
        'row_count': len(rows), 'accepted_run_sequence_sha256': digest(ids),
        'accepted_run_set_sha256': digest(sorted(ids)),
    }


def check_before_collection(root, accepted_ids=None):
    """An existing generation must be internally consistent before recollection."""
    root = Path(root)
    if (root/'outputs/collection_in_progress.json').exists():
        # Explicit recovery of an interrupted generation; the collector rebuilds
        # from authenticated runs and retains the marker until all mirrors agree.
        require(mirror_paths(root).issubset(set(DECLARED_RESULT_MANIFEST_MIRRORS)), 'undeclared root mirror')
        return None
    if (root/CANONICAL_RESULT_MANIFEST).exists() or mirror_paths(root):
        return verify_manifest_contract(root, accepted_ids)
    return None


def publish_manifest_mirrors(root, accepted_ids=None):
    """Publish the canonical bytes to declared roots under the caller's lock.

    An explicit publisher creates a new generation; verifiers never repair one.
    Unknown mirror paths are always rejected. Failed replacement leaves the
    caller's collection marker in place until a complete publication succeeds.
    """
    from .publication import sync_directory
    root = Path(root)
    require(not frozen_release_exists(root), 'immutable release: cannot publish mirrors')
    require(mirror_paths(root).issubset(set(DECLARED_RESULT_MANIFEST_MIRRORS)), 'undeclared root mirror')
    data = manifest_bytes(root, CANONICAL_RESULT_MANIFEST)
    for rel in DECLARED_RESULT_MANIFEST_MIRRORS:
        path = root/rel
        require(not path.is_symlink() and (not path.exists() or path.is_file()), f'unsafe mirror: {rel}')
        fd, name = tempfile.mkstemp(prefix=rel+'.', suffix='.partial', dir=root)
        temp = Path(name)
        try:
            with os.fdopen(fd, 'wb') as handle:
                handle.write(data); handle.flush(); os.fsync(handle.fileno())
            os.replace(temp, path)
        finally:
            temp.unlink(missing_ok=True)
    sync_directory(root)
    return verify_manifest_contract(root, accepted_ids)
