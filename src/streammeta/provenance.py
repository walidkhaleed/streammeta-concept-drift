"""Per-run provenance and gates bound to executable source and environment."""
from pathlib import Path
import datetime as dt
import gc
import importlib.metadata
import json
import os
import platform
import socket
import subprocess
import sys
import zipfile
import hashlib
import tempfile
import psutil
from threadpoolctl import threadpool_info
from .io import atomic_json, digest, file_sha
from .system import self_affinity,procfs_pid

THREAD_VARS=("OMP_NUM_THREADS","MKL_NUM_THREADS","OPENBLAS_NUM_THREADS","NUMEXPR_NUM_THREADS","VECLIB_MAXIMUM_THREADS")

def utc_now():return dt.datetime.now(dt.timezone.utc).isoformat()

def project_root():
    if os.environ.get("STREAMMETA_PROJECT_ROOT"):
        p=Path(os.environ["STREAMMETA_PROJECT_ROOT"]).resolve()
        if not (p/"pyproject.toml").is_file():raise RuntimeError("Invalid STREAMMETA_PROJECT_ROOT")
        return p
    for base in [Path(__file__).resolve(),Path.cwd().resolve()]:
        for p in [base,*base.parents]:
            if (p/"pyproject.toml").is_file() and (p/"src/streammeta").is_dir():return p
    raise RuntimeError("Use a V5 checkout or set STREAMMETA_PROJECT_ROOT")

def command(args,cwd=None):
    try:
        r=subprocess.run(args,cwd=cwd,text=True,capture_output=True,timeout=20)
        return {"returncode":r.returncode,"stdout":r.stdout.strip(),"stderr":r.stderr.strip()}
    except (OSError,subprocess.TimeoutExpired) as e:return {"unavailable":str(e)}

def source_files(root=None):
    root=Path(root or project_root()); result=[]
    for name in ("src","tests","config","experiments","hpc"):
        result.extend(p for p in (root/name).rglob('*') if p.is_file() and not any(x in ("__pycache__",".pytest_cache") or x.endswith('.egg-info') for x in p.parts) and p.suffix not in (".pyc",))
    for name in ("pyproject.toml","requirements-lock.txt","audit/design_and_statistical_plan.md","audit/issue_reconciliation.md","audit/phase0_complete.json"):
        p=root/name
        if p.is_file():result.append(p)
    return sorted(result)

def source_state(root=None):
    root=Path(root or project_root()); files={p.relative_to(root).as_posix():file_sha(p) for p in source_files(root)}
    status=command(["git","status","--porcelain"],root)
    commit=command(["git","rev-parse","HEAD"],root)
    return {"source_sha256":digest(files),"files":files,
            "git_commit":commit.get("stdout") if commit.get("returncode")==0 else None,
            "git_dirty":bool(status.get("stdout")) if status.get("returncode")==0 else None,
            "git_status":status,"git_commit_command":commit}

def package_versions():
    return dict(sorted((d.metadata["Name"].lower(),d.version) for d in importlib.metadata.distributions() if d.metadata.get("Name")))

def environment_signature():
    return digest({"python":platform.python_version(),"packages":package_versions()})

def capture_environment():
    vm=psutil.virtual_memory()
    cgroup={}
    for name in ("cpu.max","memory.max","cpuset.cpus.effective","cpuset.mems.effective"):
        p=Path('/sys/fs/cgroup')/name;cgroup[name]=p.read_text().strip() if p.exists() else None
    return {"captured_utc":utc_now(),"hostname":socket.gethostname(),"pid":os.getpid(),"procfs_pid":procfs_pid(),
            "python":sys.version,"executable":sys.executable,"platform":platform.platform(),"kernel":platform.release(),
            "machine":platform.machine(),"logical_cpu_count":psutil.cpu_count(),"physical_cpu_count":psutil.cpu_count(logical=False),
            "affinity":self_affinity(),"rss_source":"/proc/self/statm resident pages times SC_PAGE_SIZE on Linux; psutil self process elsewhere",
            "ram_bytes":vm.total,"available_ram_bytes":vm.available,"cgroup":cgroup,
            "lscpu":command(["lscpu","--json"]),"numa":command(["numactl","--hardware"]),
            "load_average":os.getloadavg() if hasattr(os,"getloadavg") else None,
            "packages":package_versions(),"environment_signature":environment_signature(),
            "thread_environment":{k:os.environ.get(k) for k in THREAD_VARS},"threadpools":threadpool_info(),
            "gc":{"enabled":gc.isenabled(),"threshold":gc.get_threshold(),"counts":gc.get_count()},
            "timer":"time.perf_counter_ns, monotonic high-resolution nanoseconds"}

def verify_source_archive(path, source):
    """Verify member identity separately from the ZIP container's byte hash."""
    with zipfile.ZipFile(path) as z:
        names=z.namelist()
        if len(names)!=len(set(names)) or set(names)!=set(source['files']):
            raise RuntimeError('Source archive member set mismatch')
        members={name:hashlib.sha256(z.read(name)).hexdigest() for name in names}
    if members!=source['files'] or digest(members)!=source['source_sha256']:
        raise RuntimeError('Source archive member content mismatch')
    return {'source_sha256':digest(members),'container_sha256':file_sha(path)}

def require_source_archive(output, source):
    path=Path(output)/'source_snapshots'/(source['source_sha256']+'.zip')
    if not path.is_file():
        raise RuntimeError('Source snapshot must be prepared before worker fan-out')
    verify_source_archive(path,source)
    return path

def archive_source(output,source):
    from .publication import file_lock, sync_directory
    root=project_root();dest=Path(output)/'source_snapshots'/(source['source_sha256']+'.zip')
    dest.parent.mkdir(parents=True,exist_ok=True)
    with file_lock(Path(output)/'.locks'/('source-'+source['source_sha256']+'.lock')):
        if dest.exists():
            verify_source_archive(dest,source)
            return dest
        fd,name=tempfile.mkstemp(prefix=dest.name+'.',suffix='.partial',dir=dest.parent)
        os.close(fd);tmp=Path(name)
        try:
            with zipfile.ZipFile(tmp,'w',zipfile.ZIP_DEFLATED) as z:
                for rel in sorted(source['files']):
                    data=(root/rel).read_bytes()
                    if hashlib.sha256(data).hexdigest()!=source['files'][rel]:
                        raise RuntimeError('Source changed during snapshot preparation')
                    info=zipfile.ZipInfo(rel,date_time=(1980,1,1,0,0,0))
                    info.create_system=3;info.external_attr=0o100644<<16
                    info.compress_type=zipfile.ZIP_DEFLATED
                    z.writestr(info,data)
            verify_source_archive(tmp,source)
            with tmp.open('rb') as handle:os.fsync(handle.fileno())
            os.replace(tmp,dest);sync_directory(dest.parent)
        finally:
            tmp.unlink(missing_ok=True)
    return dest

def check_gates(c,output):
    if c.stage=="smoke":return
    root=project_root()
    if not (root/"audit/phase0_complete.json").is_file():raise RuntimeError("Phase 0 is incomplete")
    source=source_state();env=environment_signature()
    for name in (["tests"] if c.stage=="controlled" else ["tests","controls"]):
        p=Path(output)/"gates"/(name+".json")
        if not p.exists():raise RuntimeError(f"Missing {name} gate; no scientific run allowed")
        gate=json.loads(p.read_text())
        if not gate.get("passed") or gate["source_sha256"]!=source["source_sha256"] or gate["environment_signature"]!=env:
            raise RuntimeError(f"Stale/failed {name} gate; rerun validation after source/environment changes")

def write_gate(output,name,details):
    source=source_state()
    atomic_json(Path(output)/"gates"/(name+".json"),{"passed":True,"created_utc":utc_now(),
                "source_sha256":source["source_sha256"],"environment_signature":environment_signature(),**details})
