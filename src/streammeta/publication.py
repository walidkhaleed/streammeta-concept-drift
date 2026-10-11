"""Single-host POSIX publication locks; no distributed-filesystem guarantee."""
from contextlib import contextmanager
from pathlib import Path
import fcntl
import os


@contextmanager
def file_lock(path, *, shared=False):
    """Fail immediately on contention. Never unlink a live lock inode."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a+b') as handle:
        try:
            fcntl.flock(handle, (fcntl.LOCK_SH if shared else fcntl.LOCK_EX) | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RuntimeError(f'Publication lock active: {path}') from exc
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


@contextmanager
def worker_session(output):
    from .manifest_contract import frozen_release_exists
    if frozen_release_exists(Path(output).parent):
        raise RuntimeError('Immutable release: no workers allowed')
    with file_lock(Path(output)/'.locks/workers.lock', shared=True):
        yield


@contextmanager
def publication_session(output):
    """A collector, validator or freezer excludes every supported writer."""
    output = Path(output)
    with file_lock(output/'.locks/collector.lock'):
        with file_lock(output/'.locks/workers.lock'):
            yield


def sync_directory(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def publish_tables(staging, destination):
    """Atomic per-file replaces under the caller's exclusive publication lock.

    The caller maintains a transaction marker until ALL replacements complete.
    A crash leaves the marker; release validation refuses that generation.
    """
    staging, destination = Path(staging), Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    for path in sorted(staging.iterdir()):
        if not path.is_file():
            raise RuntimeError('Only flat aggregate tables may be published')
        with path.open('rb') as handle:
            os.fsync(handle.fileno())
        os.replace(path, destination/path.name)
    sync_directory(destination)
