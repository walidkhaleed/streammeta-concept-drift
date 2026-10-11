"""Outcome-independent paired experiment plans."""
from dataclasses import replace
from pathlib import Path
from .config import RunConfig,Seeds,XAIConfig,DATASETS,METHODS
from .io import atomic_json,digest
from .provenance import utc_now

def data_seeds(i):return Seeds(data=42+i,wrapper=142+i)
def real_seeds(i):return Seeds(model=242+i,router=342+i)

def make_plan(phase,n_seeds=10,resource_repeats=3,horizon='full'):
    if n_seeds<1 or resource_repeats<1:raise ValueError('Positive replication counts required')
    jobs=[]
    if phase=='smoke':
        jobs=[RunConfig(dataset='SEA',method=m,n=256,stage='smoke') for m in METHODS]
    elif phase=='controlled':
        jobs=[RunConfig(stage='controlled',arm=a) for a in ('primary_w100_tau0','window_w100_tau0','delay_tau0_w100')]
    elif phase=='primary':
        jobs=[RunConfig(dataset=d,method=m,stage=phase) for d in DATASETS for m in METHODS]
    elif phase in ('windows','delays'):
        for i in range(n_seeds):
            for v in ((50,100,500) if phase=='windows' else (0,100,500)):
                jobs.append(RunConfig(stage=phase,arm=f'{phase}_{v}',design='synthetic_data',replicate=i,seeds=data_seeds(i),
                                      window=v if phase=='windows' else 100,delay=v if phase=='delays' else 0))
    elif phase=='robustness':
        for i in range(n_seeds):
            for d in DATASETS:
                real=d in ('Elec2','Phishing')
                for m in METHODS:
                    jobs.append(RunConfig(dataset=d,method=m,stage=phase,replicate=i,
                                          design='real_learner' if real else 'synthetic_data',seeds=real_seeds(i) if real else data_seeds(i)))
    elif phase=='xai':
        # Three predeclared event-triggered Agrawal runs; ten repetitions of each controlled scenario.
        for i in range(3):
            jobs.append(RunConfig(stage='xai',n=6000,replicate=i,design='adwin_case_study',seeds=data_seeds(i),xai=XAIConfig(enabled=True)))
        for i in range(n_seeds):
            for d in ('NullControl','LabelRuleControl','CovariateControl','MixedControl'):
                jobs.append(RunConfig(dataset=d,stage='xai',design='scheduled_xai_control',replicate=i,seeds=data_seeds(i),
                                      xai=XAIConfig(enabled=True,adwin_enabled=False,scheduled_event=3199)))
    elif phase=='resource':
        import random
        for i in range(resource_repeats):
            batch=[]
            for d in DATASETS:
                for m in METHODS:
                    n=min(2000,RunConfig(dataset=d).length) if horizon=='short' else None
                    batch.append(RunConfig(dataset=d,method=m,stage=phase,n=n,arm=horizon,design='runtime_repetition',resource_repeat=i,
                                          seeds=replace(Seeds(),resource_run=542+i)))
            # Each repetition's recorded resource seed randomizes its complete block.
            # It never changes stream/model/router configuration.
            random.Random(542+i).shuffle(batch);jobs.extend(batch)
    else:raise ValueError(f'Unsupported phase {phase}')
    return jobs

def save_plan(phase,output,n_seeds=10,resource_repeats=3,horizon='full'):
    jobs=make_plan(phase,n_seeds,resource_repeats,horizon)
    data={"phase":phase,"created_utc":utc_now(),"n_seeds":n_seeds,"resource_repeats":resource_repeats,
          "horizon":horizon,"primary_metric":"roc_auc_exact","jobs":[c.to_dict() for c in jobs]}
    data['plan_sha256']=digest(data['jobs'])
    path=Path(output)/'plans'/f'{phase}_{horizon if phase=="resource" else "plan"}.json'
    if path.exists():
        import json
        old=json.loads(path.read_text())
        if old['plan_sha256']!=data['plan_sha256']:raise RuntimeError('Plan exists with different jobs; use a new campaign output directory')
        return path
    atomic_json(path,data);return path
