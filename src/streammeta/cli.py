"""Portable command line; set native threads before importing scientific code."""
import argparse
import json
import os
from pathlib import Path
import sys

def main():
    names=('OMP_NUM_THREADS','MKL_NUM_THREADS','OPENBLAS_NUM_THREADS','NUMEXPR_NUM_THREADS','VECLIB_MAXIMUM_THREADS')
    wanted={'PYTHONHASHSEED':'0',**{k:'1' for k in names}}
    if any(os.environ.get(k)!=v for k,v in wanted.items()):
        os.execve(sys.executable,[sys.executable,'-m','streammeta.cli',*sys.argv[1:]],dict(os.environ,**wanted))
    parser=argparse.ArgumentParser(description='V5.0.2 provenance release and streaming utilities')
    sub=parser.add_subparsers(dest='command',required=True)
    for name in ('run','prepare','plan','campaign','task','test','validate-controls','collect','validate-evidence','figures','freeze','snapshot','inventory','verify-package'):
        p=sub.add_parser(name);p.add_argument('--output',default='outputs')
        if name in ('collect','validate-evidence','freeze'):p.add_argument('--release-policy')
        if name=='run':p.add_argument('--config',required=True)
        if name in ('plan','prepare'):p.add_argument('--phase',default='primary');p.add_argument('--n-seeds',type=int,default=10);p.add_argument('--resource-repeats',type=int,default=3);p.add_argument('--horizon',choices=['short','full'],default='full')
        if name in ('campaign','task'):p.add_argument('--plan',required=True)
        if name=='campaign':p.add_argument('--workers',type=int,default=1);p.add_argument('--no-resume',action='store_true')
        if name=='task':p.add_argument('--index',required=True,type=int)
    args=parser.parse_args();out=Path(args.output).resolve()
    from .io import jsonable
    from .manifest_contract import frozen_release_exists
    if frozen_release_exists(out.parent) and args.command!='verify-package':
        raise RuntimeError('Immutable release: use a new staging tree for any write operation')
    if args.command=='snapshot':
        from .provenance import archive_source,source_state
        result={'snapshot':str(archive_source(out,source_state()))}
    elif args.command=='inventory':
        from .release import write_inventory
        result={'files':len(write_inventory(out.parent)['files'])}
    elif args.command=='verify-package':
        from .release import verify_frozen_release
        result=verify_frozen_release(out.parent)
    elif args.command=='run':
        from .config import RunConfig
        from .runner import run_job
        result=run_job(RunConfig.from_dict(json.loads(Path(args.config).read_text())),out)
    elif args.command in ('plan','prepare'):
        from .plans import save_plan
        path=save_plan(args.phase,out,args.n_seeds,args.resource_repeats,args.horizon)
        if args.command=='prepare':
            from .provenance import archive_source,source_state
            archive_source(out,source_state())
            from .config import RunConfig
            from .streams import materialize
            seen=set()
            for c in json.loads(path.read_text())['jobs']:
                _,m=materialize(RunConfig.from_dict(c),out/'streams');seen.add(m['cache_id'])
            result={'plan':str(path),'materialized_streams':len(seen)}
        else:result={'plan':str(path)}
    elif args.command=='campaign':
        from .campaign import run_campaign
        result=run_campaign(args.plan,out,args.workers,not args.no_resume)
    elif args.command=='task':
        from .config import RunConfig
        from .io import digest
        from .runner import run_job
        plan=json.loads(Path(args.plan).read_text())
        if digest(plan['jobs'])!=plan['plan_sha256']:raise RuntimeError('Invalid plan hash')
        if args.index<0 or args.index>=len(plan['jobs']):raise ValueError('Array index outside plan')
        result=run_job(RunConfig.from_dict(plan['jobs'][args.index]),out)
    elif args.command=='test':
        from .validation import run_tests
        result=run_tests(out)
    elif args.command=='validate-controls':
        from .validation import validate_controls
        result=validate_controls(out)
    elif args.command=='validate-evidence':
        from .validation import validate_evidence
        result=validate_evidence(out,args.release_policy)
    elif args.command=='collect':
        from .reporting import collect
        result=collect(out,args.release_policy)
    elif args.command=='figures':
        from .plotting import figures
        result=figures(out)
    else:
        from .freeze import freeze
        result=freeze(out,args.release_policy)
    print(json.dumps(jsonable(result),sort_keys=True),flush=True)

if __name__=='__main__':main()
