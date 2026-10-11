"""Local POSIX concurrency tests using temporary files and subprocesses."""
from pathlib import Path
import os
import subprocess
import sys
from types import SimpleNamespace
import pytest
from streammeta.io import atomic_json, digest, file_sha
from streammeta.publication import file_lock, worker_session, publication_session
from streammeta.provenance import archive_source, require_source_archive


@pytest.fixture
def snapshot(tmp_path,monkeypatch):
    root=tmp_path/'source';root.mkdir();(root/'utility.py').write_text('x=1\n')
    files={'utility.py':file_sha(root/'utility.py')};source={'files':files,'source_sha256':digest(files)}
    monkeypatch.setattr('streammeta.provenance.project_root',lambda:root)
    return root,source,tmp_path/'outputs'


def test_source_zip_is_deterministic_across_file_mtimes(snapshot):
    root,source,out=snapshot
    first=archive_source(out,source);first_hash=file_sha(first)
    os.utime(root/'utility.py',(1800000000,1800000000))
    second=archive_source(out.parent/'other',source)
    assert file_sha(second)==first_hash
    assert require_source_archive(out,source)==first


def test_existing_corrupt_source_archive_is_never_reused(snapshot):
    root,source,out=snapshot;path=archive_source(out,source);path.write_bytes(b'not a zip')
    import zipfile
    with pytest.raises((RuntimeError,zipfile.BadZipFile)):archive_source(out,source)
    assert path.read_bytes()==b'not a zip'


def test_source_changed_during_preparation_is_rejected(snapshot):
    root,source,out=snapshot;(root/'utility.py').write_text('x=2\n')
    with pytest.raises(RuntimeError,match='Source changed'):archive_source(out,source)
    assert not list((out/'source_snapshots').glob('*.zip'))


def test_workers_require_precreated_snapshot(snapshot):
    root,source,out=snapshot
    with pytest.raises(RuntimeError,match='before worker fan-out'):require_source_archive(out,source)


def test_another_process_holding_archive_lock_blocks_publication(snapshot):
    root,source,out=snapshot;lock=out/'.locks'/('source-'+source['source_sha256']+'.lock')
    code="from streammeta.publication import file_lock; import sys\nwith file_lock(sys.argv[1]):\n print('locked',flush=True)\n sys.stdin.readline()\n"
    proc=subprocess.Popen([sys.executable,'-c',code,str(lock)],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
    try:
        assert proc.stdout.readline().strip()=='locked'
        with pytest.raises(RuntimeError,match='lock active'):archive_source(out,source)
    finally:
        proc.communicate('\n',timeout=10)
    assert proc.returncode==0
    assert archive_source(out,source).is_file()


def test_collector_refuses_active_collector_without_overwriting(tmp_path):
    from streammeta.reporting import collect
    out=tmp_path/'outputs';dest=out/'tables/table.csv';dest.parent.mkdir(parents=True);dest.write_text('original\n')
    with file_lock(out/'.locks/collector.lock'):
        with pytest.raises(RuntimeError,match='lock active'):collect(out)
    assert dest.read_text()=='original\n'


def test_workers_and_publishers_exclude_each_other(tmp_path):
    with worker_session(tmp_path):
        with worker_session(tmp_path):pass
        with pytest.raises(RuntimeError,match='lock active'):
            with publication_session(tmp_path):pass
    with publication_session(tmp_path):
        with pytest.raises(RuntimeError,match='lock active'):
            with worker_session(tmp_path):pass


def test_second_process_collector_refused(tmp_path):
    with publication_session(tmp_path):
        code="from streammeta.reporting import collect; import sys; collect(sys.argv[1])"
        result=subprocess.run([sys.executable,'-c',code,str(tmp_path)],capture_output=True,text=True,timeout=20)
    assert result.returncode!=0 and 'lock active' in result.stderr


def test_partial_table_publication_retains_failure_marker(snapshot,monkeypatch):
    from streammeta import reporting
    from streammeta import publication
    from streammeta.release import inspect_links
    root,source,out=snapshot;archive_source(out,source)
    monkeypatch.setattr(reporting,'source_state',lambda:source)
    def tables(output,dest,*args):
        (dest/'a.csv').write_text('new,a\n');(dest/'b.csv').write_text('new,b\n');return {}
    monkeypatch.setattr(reporting,'_collect_tables',tables)
    original=publication.os.replace;calls=[]
    def interrupted(a,b):
        if str(b).endswith('b.csv'):raise OSError('simulated interruption between atomic replaces')
        calls.append((a,b));return original(a,b)
    monkeypatch.setattr(publication.os,'replace',interrupted)
    with pytest.raises(OSError,match='simulated interruption'):reporting.collect(out)
    assert (out/'collection_in_progress.json').is_file()
    assert (out/'tables/a.csv').read_text()=='new,a\n'
    with pytest.raises(RuntimeError,match='Incomplete aggregate'):inspect_links(out.parent,{})


def test_source_temp_names_are_unique_without_pid_assumptions(snapshot,monkeypatch):
    from streammeta import provenance
    root,source,out=snapshot;paths=[]
    def fail_replace(a,b):paths.append(str(a));raise OSError('publication interrupted')
    monkeypatch.setattr(provenance.os,'replace',fail_replace)
    for _ in range(2):
        with pytest.raises(OSError,match='publication interrupted'):archive_source(out,source)
    assert len(paths)==len(set(paths))==2
    assert not any(Path(p).exists() for p in paths)


def test_campaign_prepares_snapshot_and_never_reuses_logs(snapshot,monkeypatch):
    from streammeta import campaign
    root,source,out=snapshot
    monkeypatch.setattr(campaign,'source_state',lambda:source)
    monkeypatch.setattr(campaign,'environment_signature',lambda:'fixture')
    observed=[]
    def fake_worker(*args,**kwargs):
        observed.append(require_source_archive(out,source).is_file());return SimpleNamespace(returncode=0)
    monkeypatch.setattr(campaign.subprocess,'run',fake_worker)
    jobs=[{'dataset':'Fixture','method':'Meta','replicate':i} for i in range(2)]
    plan=out.parent/'plan.json';atomic_json(plan,{'phase':'primary','jobs':jobs,'plan_sha256':digest(jobs)})
    first=campaign.run_campaign(plan,out,workers=2);second=campaign.run_campaign(plan,out,workers=2)
    assert all(observed) and len(observed)==4
    assert first['invocation']!=second['invocation']
    assert len(list((out/'campaign_logs').glob('*/*/job_*.log')))==4
