"""Portable, data-derived scientific figures with archival source data."""
from pathlib import Path
import json
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from .config import DATASETS,METHODS
from .io import atomic_json,file_sha
from .provenance import source_state,utc_now

def figures(output):
    output=Path(output);tables=output/'tables';dest=output/'figures';dest.mkdir(parents=True,exist_ok=True);made=[]
    plt.rcParams.update({'font.size':10,'axes.spines.top':False,'axes.spines.right':False,'pdf.fonttype':42})
    colors=dict(zip(METHODS,['#575d68','#258783','#a87520','#9358a5','#215bad']))
    def save(name,fig,data,description):
        fig.tight_layout();data.to_csv(dest/f'{name}_source.csv',index=False)
        fig.savefig(dest/f'{name}.pdf',bbox_inches='tight');fig.savefig(dest/f'{name}.png',dpi=180,bbox_inches='tight')
        atomic_json(dest/f'{name}_metadata.json',{'created_utc':utc_now(),'description':description,'source_sha256':source_state()['source_sha256'],
                    'source_data_sha256':file_sha(dest/f'{name}_source.csv'),'axes':[{'xlabel':a.get_xlabel(),'ylabel':a.get_ylabel(),'xlim':list(a.get_xlim()),'ylim':list(a.get_ylim()),'xscale':a.get_xscale(),'yscale':a.get_yscale()} for a in fig.axes]})
        plt.close(fig);made.append(name)
    def read(name):
        p=tables/name
        try:return pd.read_csv(p) if p.exists() else pd.DataFrame()
        except pd.errors.EmptyDataError:return pd.DataFrame()
    p=read('v5_primary_results.csv')
    if not p.empty:
        fig,ax=plt.subplots(figsize=(9,4));base=np.arange(len(DATASETS))
        for i,m in enumerate(METHODS):
            q=p[p.method==m].set_index('dataset').reindex(DATASETS)
            ax.scatter(base+(i-2)*.13,q.roc_auc_exact,label=m,c=colors[m],s=35)
        ax.set(xticks=base,xticklabels=DATASETS,ylabel='Exact empirical ROC AUC',ylim=(0,1.025),xlabel='Stream');ax.legend(ncols=5,loc='lower center');ax.grid(axis='y',alpha=.2)
        save('primary_exact_auc',fig,p,'Principal seed, full horizon; exact AUC; no uncertainty inferred from one run.')
    for name,col,label in [('window','window','Verified-history window (observations)'),('delay','delay','Label delay (observations)')]:
        d=read(f'v5_{name}_results.csv')
        if not d.empty:
            fig,ax=plt.subplots(figsize=(6.5,4))
            for i,g in d.groupby('replicate'):
                g=g.sort_values(col);ax.plot(g[col],g.roc_auc_exact,c=colors['Meta'],alpha=.18,lw=1)
            mean=d.groupby(col).roc_auc_exact.mean();ax.plot(mean.index,mean.values,'o-',color=colors['Meta'],lw=2,label='Mean of paired repetitions')
            ax.set(xlabel=label,ylabel='Exact empirical ROC AUC',xticks=sorted(d[col].unique()),ylim=(0,1));ax.legend();ax.grid(alpha=.2)
            save(f'agrawal_{name}',fig,d,'Actual numeric horizontal positions. Thin lines are fixed-seed paired repetitions; thick line is their mean.')
    d=read('v5_paired_router_effects.csv')
    if not d.empty:
        fig,ax=plt.subplots(figsize=(7,4));y=np.arange(len(d));err=np.array([d['mean']-d.ci95_low,d.ci95_high-d['mean']])
        ax.errorbar(d['mean'],y,xerr=err,fmt='o',color=colors['Meta'],capsize=3);ax.axvline(0,c='gray',lw=1)
        ax.set(yticks=y,yticklabels=d.dataset,xlabel='Meta minus RecentError exact AUC (paired mean and 95% t interval)',ylabel='Stream')
        save('paired_router_effects',fig,d,'Ten repetitions under separately labeled synthetic-data or real-learner designs; intervals across repetition differences.')
    r=read('v5_resource_results.csv')
    if not r.empty:
        for hardware,group in r.groupby('hardware_fingerprint'):
            q=group[(group.phase=='after_warmup')&(group.timer=='core_step_time_ms')&(group.arm=='full')]
            if q.empty:continue
            fig,ax=plt.subplots(figsize=(9,4))
            for j,m in enumerate(METHODS):
                g=q[q.method==m].groupby('dataset')['mean'].agg(['mean','min','max']).reindex(DATASETS)
                ax.errorbar(np.arange(len(DATASETS))+(j-2)*.13,g['mean'],yerr=[g['mean']-g['min'],g['max']-g['mean']],fmt='o',label=m,color=colors[m],ms=4,capsize=2)
            ax.set(xticks=np.arange(len(DATASETS)),xticklabels=DATASETS,ylabel='Mean core step time (ms)',xlabel='Stream',yscale='log');ax.legend(ncols=5,fontsize=9);ax.grid(axis='y',alpha=.2)
            save(f'resource_core_{hardware[:8]}',fig,q,'Full horizon after first 1000 observations. Points mean run means; bars min/max of three fresh-process repetitions. One hardware group only; log y scale.')
    x=read('v5_xai_events.csv')
    if not x.empty:
        q=x[(x.design=='scheduled_xai_control')&(x.success==True)]
        if not q.empty:
            names=['NullControl','LabelRuleControl','CovariateControl','MixedControl'];fig,ax=plt.subplots(figsize=(8,4))
            for i,name in enumerate(names):
                g=q[q.dataset==name];ax.scatter(np.full(len(g),i)+np.linspace(-.1,.1,len(g)),g.roc_auc_exact_oof,c='#215bad',s=25)
            ax.axhline(.5,color='gray',ls='--');ax.set(xticks=range(4),xticklabels=['Stationary null','Label-rule shift','Covariate shift','Mixed shift'],ylabel='Out-of-fold temporal-window ROC AUC',ylim=(0,1),xlabel='Predeclared synthetic control')
            save('xai_temporal_controls',fig,q,'Scheduled retrospective windows straddle known synthetic boundary. Values assess discrimination, not causal attribution or automatic localization.')
    return {'figures':made}
