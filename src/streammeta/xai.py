"""Retrospective, pre-learning explanation snapshots with out-of-fold Tier 2.

Temporal-window attribution is descriptive. No causal/localization claim is made.
"""
from collections import deque
from pathlib import Path
from itertools import combinations
import hashlib
import json
import numpy as np
import pandas as pd
from scipy.stats import spearmanr
from sklearn.metrics import roc_auc_score
from river import drift
import shap
from .config import META_NAMES, derived_seed
from .io import atomic_json, file_sha
from .models import expert_class, xgb_model

def mean_abs_shap(model,X):
    explainer=shap.TreeExplainer(model,feature_perturbation="tree_path_dependent",model_output="raw")
    values=np.asarray(explainer.shap_values(X),dtype=float)
    if values.shape!=X.shape:raise RuntimeError(f"Unexpected SHAP shape {values.shape} for {X.shape}")
    if not np.isfinite(values).all():raise RuntimeError("Nonfinite SHAP values")
    return values,np.mean(np.abs(values),axis=0)

def permute_blocks_within_folds(y,blocks,fold,rng):
    """Conditional block permutation preserving observed fold class counts.

    Unrestricted block permutations alter fold class balance; training-prior
    shifts can then produce artificially low out-of-fold null AUC. Holding the
    fold design fixed includes its class counts, not just the row indices.
    """
    y=np.asarray(y);out=np.empty_like(y)
    for k in np.unique(fold):
        ids=np.unique(blocks[fold==k]);labels=[]
        for block in ids:
            mask=blocks==block
            if len(np.unique(fold[mask]))!=1 or len(np.unique(y[mask]))!=1:raise ValueError('Blocks must lie within one fold and one observed temporal class')
            labels.append(y[mask][0])
        for block,label in zip(ids,rng.permutation(labels)):out[blocks==block]=label
    return out

def temporal_discrimination(raw,cfg,seed,out_dir):
    """Blocked cross-fit; each test block is excluded from its fitted model.

    Permutation null shuffles whole block labels within each fixed fold,
    preserving its observed class counts and block-size dependence.
    The same fixed folds are used for observed and null fits. No labels after event.
    """
    out_dir=Path(out_dir);out_dir.mkdir(parents=True,exist_ok=True)
    tids=np.array([t for t,x in raw]);features=sorted(raw[0][1]);n=len(raw)
    if n!=cfg.raw_window or not np.array_equal(tids,np.arange(tids[0],tids[-1]+1)):
        raise ValueError("Expected complete contiguous retrospective raw window")
    X=np.asarray([[x[k] for k in features] for t,x in raw],dtype=float)
    if not np.isfinite(X).all():raise ValueError("Tier 2 requires finite numeric encoded features")
    y=np.r_[np.zeros(n//2,dtype=int),np.ones(n//2,dtype=int)]
    blocks=np.arange(n)//cfg.block_size
    # Restart fold assignment in each temporal half to balance observed labels.
    fold=(blocks % (n//(2*cfg.block_size))) % cfg.folds
    p=np.zeros(n);shap_oof=np.zeros_like(X);importance=[];fold_rows=[]
    for k in range(cfg.folds):
        train=fold!=k;test=~train
        model=xgb_model(derived_seed(seed,f"tier2-fold-{k}"),20)
        model.fit(X[train],y[train]);p[test]=model.predict_proba(X[test])[:,1]
        sv,imp=mean_abs_shap(model,X[test]);shap_oof[test]=sv;importance.append(imp)
        model.save_model(out_dir/f"tier2_fold_{k}.ubj")
        fold_rows.append({"fold":k,"train_n":int(train.sum()),"test_n":int(test.sum()),
                          "roc_auc_exact":float(roc_auc_score(y[test],p[test])),
                          "seed":derived_seed(seed,f"tier2-fold-{k}"),"model_params":model.get_params()})
    observed=float(roc_auc_score(y,p));rng=np.random.default_rng(seed);null=[]
    permutation_assignments=[]
    for perm_index in range(cfg.permutations):
        yp=permute_blocks_within_folds(y,blocks,fold,rng);pp=np.zeros(n)
        for block in np.unique(blocks):
            mask=blocks==block
            permutation_assignments.append({'permutation':perm_index,'block':int(block),'fold':int(fold[mask][0]),'temporal_label_permuted':int(yp[mask][0])})
        for k in range(cfg.folds):
            train=fold!=k;test=~train
            if len(np.unique(yp[train]))<2:pp[test]=float(yp[train][0])
            else:
                model=xgb_model(derived_seed(seed,f"tier2-fold-{k}"),20)
                model.fit(X[train],yp[train]);pp[test]=model.predict_proba(X[test])[:,1]
        null.append(float(roc_auc_score(yp,pp)))
    p_value=(1+sum(a>=observed for a in null))/(1+len(null))
    stability=[]
    for a,b in combinations(range(cfg.folds),2):
        va,vb=importance[a],importance[b]
        corr=float(spearmanr(va,vb).statistic) if np.ptp(va)>0 and np.ptp(vb)>0 else None
        stability.append({"fold_a":a,"fold_b":b,"spearman_importance":corr})
    pd.DataFrame({"transaction_id":tids,"temporal_label":y,"fold":fold,"block":blocks,"probability_newer_oof":p}).to_parquet(out_dir/"tier2_oof_predictions.parquet",index=False)
    pd.DataFrame(X,columns=features).assign(transaction_id=tids).to_parquet(out_dir/"raw_windows.parquet",index=False)
    pd.DataFrame(shap_oof,columns=features).assign(transaction_id=tids).to_parquet(out_dir/"tier2_oof_shap.parquet",index=False)
    pd.DataFrame({"feature":features,"mean_abs_shap_raw_margin":np.abs(shap_oof).mean(axis=0)}).to_csv(out_dir/"tier2_importance.csv",index=False)
    pd.DataFrame({"permutation":range(cfg.permutations),"roc_auc_exact":null}).to_csv(out_dir/"tier2_permutation_null.csv",index=False)
    pd.DataFrame(permutation_assignments).to_parquet(out_dir/'tier2_permutation_block_assignments.parquet',index=False)
    result={"interpretation":"descriptive_temporal_window_attribution","roc_auc_exact_oof":observed,
            "permutation_p_one_sided":p_value,"permutations":cfg.permutations,"permutation_unit":"contiguous equal-size blocks, shuffled within fixed folds",
            "permutation_conditioning":"each fold retains its observed temporal-class counts; same folds, class balance and learner fitting procedure for observed/null",
            "block_size":cfg.block_size,"folds":fold_rows,"fold_stability":stability,"features":features,
            "older_index_range":[int(tids[0]),int(tids[n//2-1])],"newer_index_range":[int(tids[n//2]),int(tids[-1])],
            "older_sample_range":[int(tids[0])+1,int(tids[n//2-1])+1],"newer_sample_range":[int(tids[n//2])+1,int(tids[-1])+1],
            "shap_model_output":"raw log-odds margin","shap_feature_perturbation":"tree_path_dependent",
            "reference":"training-path sample counts; no supplied background dataset",
            "encoding":"fixed numeric feature schema; no fitted global preprocessing",
            "localization_validated":False,"causal_claim":False}
    atomic_json(out_dir/"tier2_validation.json",result)
    return result

class XAIObserver:
    def __init__(self,c,run_dir):
        self.config=c;self.cfg=c.xai;self.run_dir=Path(run_dir)
        self.detector=drift.ADWIN(delta=self.cfg.delta)
        self.raw=deque(maxlen=self.cfg.raw_window);self.meta=deque(maxlen=self.cfg.meta_window)
        self.events=[];self.successful=[];self.n_adwin_events=0;self.n_capable=0
        self._scheduled_done=False

    def observe_input(self,ctx):
        self.raw.append((ctx.t,dict(ctx.x)))
        if ctx.mf is not None:self.meta.append((ctx.t,tuple(ctx.mf)))

    def on_release(self,ctx,y,now,learner):
        detected=False
        if self.cfg.adwin_enabled:
            self.detector.update(int(expert_class(ctx.probability)!=y))
            detected=bool(self.detector.drift_detected)
        scheduled=now==self.cfg.scheduled_event and not self._scheduled_done
        if scheduled:self._scheduled_done=True
        if not detected and not scheduled:return
        self.n_adwin_events += int(detected)
        event={"event_index_zero_based":now,"event_sample_number_one_based":now+1,
               "prediction_time":ctx.t,"label_verification_time":now,"adwin_update_time":now,
               "xai_event_time":now,"trigger":"adwin_and_scheduled" if detected and scheduled else ("adwin" if detected else "scheduled_control"),
               "trigger_error_transaction_id":ctx.t,"trigger_error_sample_number":ctx.t+1,
               "manager_fit_count_before_learning":learner.fit_count,"verified_count_before_learning":learner.verified_count,
               "order":"prediction -> enqueue -> verification -> ADWIN -> XAI snapshot -> supervised learning",
               "status":"ineligible","success":False,"capable":False}
        capable=learner.trained and len(self.meta)>=self.cfg.min_meta and len(self.raw)==self.cfg.raw_window
        if capable:
            self.n_capable += 1;event["capable"]=True
            event_dir=self.run_dir/"xai"/f"event_{now:08d}"
            event_dir.mkdir(parents=True,exist_ok=False)
            try:
                X=np.asarray([v for t,v in self.meta]);tids=[t for t,v in self.meta]
                learner.manager.model.save_model(event_dir/"tier1_router.ubj")
                sv,imp=mean_abs_shap(learner.manager.model,X)
                pd.DataFrame(X,columns=META_NAMES).assign(transaction_id=tids).to_parquet(event_dir/"tier1_meta_states.parquet",index=False)
                pd.DataFrame(sv,columns=META_NAMES).assign(transaction_id=tids).to_parquet(event_dir/"tier1_shap.parquet",index=False)
                pd.DataFrame({"feature":META_NAMES,"mean_abs_shap_raw_margin":imp}).to_csv(event_dir/"tier1_importance.csv",index=False)
                seed=derived_seed(self.config.seeds.xai,f"event-{now}")
                tier2=temporal_discrimination(list(self.raw),self.cfg,seed,event_dir)
                event.update({"status":"successful","success":True,"event_dir":str(event_dir.relative_to(self.run_dir)),
                              "router_snapshot_sha256":file_sha(event_dir/"tier1_router.ubj"),
                              "meta_index_range":[tids[0],tids[-1]],"event_seed":seed,
                              "tier1_interpretation":"current router attribution over recent historical meta-states",
                              "tier2":tier2})
                self.successful.append(event)
            except Exception as e:
                event.update(status="failed",error=f"{type(e).__name__}: {e}")
                atomic_json(event_dir/"event.json",event);self.events.append(event)
                self.save_events()
                raise
            atomic_json(event_dir/"event.json",event)
        else:
            event["ineligible_reason"]={"manager_trained":learner.trained,"meta_n":len(self.meta),"raw_n":len(self.raw)}
        self.events.append(event)

    def save_events(self):
        atomic_json(self.run_dir/"xai_events.json",self.events)

    def summary(self,n):
        self.save_events()
        return {"n_adwin_events":self.n_adwin_events,"n_xai_capable_events":self.n_capable,
                "n_xai_successful":len(self.successful),"n_scheduled_control_events":sum('scheduled' in e['trigger'] for e in self.events),
                "invocation_reduction_percent":100*(1-len(self.successful)/n),
                "invocation_reduction_scope":"successful calls relative to hypothetical N per-sample calls; includes scheduled calls when configured",
                "xai_claim":"descriptive attribution; no causal or automatic localization claim"}
