"""Prediction receives x and time only; verified feedback is a separate API."""
from collections import deque
from dataclasses import dataclass
import time
import numpy as np
import xgboost as xgb
from river import ensemble, forest, preprocessing, tree
from .config import RunConfig

def expert_class(p):
    return int(p >= .5)

def manager_class(q):
    return int(q > .5)

def router_target(y, p_hat, p_arf):
    return int(abs(y-p_arf) < abs(y-p_hat))

def probability(model,x):
    p = float(model.predict_proba_one(x).get(1,0.0))
    if not np.isfinite(p) or not 0 <= p <= 1:
        raise ValueError(f"Invalid class-1 probability {p}")
    return p

class RollingState:
    def __init__(self,w):
        self.w = w; self.rows = deque(maxlen=w); self.verified_count = 0

    def features(self):
        if len(self.rows) < self.w: return None
        return tuple(np.asarray(self.rows,dtype=float).mean(axis=0).tolist())

    def update(self,y,p_hat,p_arf):
        h,a = expert_class(p_hat),expert_class(p_arf)
        self.rows.append((int(y),int(h!=y),int(a!=y),int(h!=a)))
        self.verified_count += 1

def xgb_model(seed,n_estimators=10):
    return xgb.XGBClassifier(n_estimators=n_estimators,max_depth=3,learning_rate=.1,
                            objective="binary:logistic",eval_metric="logloss",random_state=seed,
                            n_jobs=1,tree_method="hist",device="cpu",subsample=1.0,colsample_bytree=1.0)

class BoundedManager:
    def __init__(self,capacity,refit_every,seed):
        self.X=deque(maxlen=capacity); self.y=deque(maxlen=capacity)
        self.refit_every=refit_every; self.seed=seed; self.since_fit=0
        self.model=None; self.fit_count=0

    @property
    def trained(self): return self.model is not None

    def choose(self,mf):
        if mf is None or not self.trained: return 1,None
        # Positive-class column is resolved explicitly rather than guessed.
        classes=list(self.model.classes_); q=float(self.model.predict_proba(np.asarray([mf]))[0,classes.index(1)])
        return manager_class(q),q

    def observe(self,mf,z):
        if mf is None: return False
        self.X.append(tuple(mf)); self.y.append(int(z)); self.since_fit += 1
        if self.since_fit < self.refit_every or len(set(self.y)) < 2: return False
        model=xgb_model(self.seed); model.fit(np.asarray(self.X),np.asarray(self.y))
        self.model=model; self.fit_count += 1; self.since_fit=0
        return True

@dataclass(frozen=True)
class PredictionContext:
    t: int
    x: dict
    mf: tuple | None
    probability: float
    route: str
    p_hat: float | None
    p_arf: float | None
    q_arf: float | None
    manager_trained: bool
    fit_count: int
    verified_count: int

class OnlineLearner:
    def __init__(self,c:RunConfig,clock=time.perf_counter_ns):
        self.config=c; self.clock=clock; self.verified_count=0
        self.paired=c.method in ("Meta","RecentError")
        def hat():return preprocessing.StandardScaler() | tree.HoeffdingAdaptiveTreeClassifier(seed=c.seeds.model)
        def arf():return preprocessing.StandardScaler() | forest.ARFClassifier(n_models=c.n_models,seed=c.seeds.model)
        if self.paired:
            self.hat,self.arf=hat(),arf(); self.state=RollingState(c.window)
        else:
            self.model={"HAT":hat,"ARF":arf,"SRP":lambda:preprocessing.StandardScaler() | ensemble.SRPClassifier(n_models=c.n_models,seed=c.seeds.model)}[c.method]()
            self.state=None
        self.manager=BoundedManager(c.buffer_size,c.refit_every,c.seeds.router) if c.method=="Meta" else None

    @property
    def fit_count(self): return self.manager.fit_count if self.manager else 0

    @property
    def trained(self): return bool(self.manager and self.manager.trained)

    def predict(self,x,t):
        response0=self.clock(); meta0=self.clock()
        mf=self.state.features() if self.paired else None
        meta_ns=self.clock()-meta0
        start=self.clock(); q=None; ph=pa=None
        if self.paired:
            if self.manager:
                choice,q=self.manager.choose(mf)
            else:
                choice=1 if mf is None else int(mf[2] < mf[1])
            route="ARF" if choice else "HAT"
            if choice: pa=probability(self.arf,x); p=pa
            else: ph=probability(self.hat,x); p=ph
        else:
            route=self.config.method; p=probability(self.model,x)
            if route=="HAT":ph=p
            if route=="ARF":pa=p
        selected_ns=self.clock()-start; response_ns=self.clock()-response0
        start=self.clock()
        if self.paired:
            if route=="ARF":ph=probability(self.hat,x)
            else:pa=probability(self.arf,x)
        shadow_ns=self.clock()-start if self.paired else 0
        ctx=PredictionContext(t,dict(x),mf,p,route,ph,pa,q,self.trained,self.fit_count,self.verified_count)
        return ctx,{"meta_state_time_ns":meta_ns,"selected_prediction_time_ns":selected_ns,
                    "response_path_time_ns":response_ns,"shadow_prediction_time_ns":shadow_ns}

    def verify(self,ctx:PredictionContext,y:int):
        z=None
        if self.paired:
            z=router_target(y,ctx.p_hat,ctx.p_arf)
            if self.manager:self.manager.observe(ctx.mf,z)
            self.state.update(y,ctx.p_hat,ctx.p_arf)
            self.hat.learn_one(ctx.x,y);self.arf.learn_one(ctx.x,y)
        else:self.model.learn_one(ctx.x,y)
        self.verified_count += 1
        return z

