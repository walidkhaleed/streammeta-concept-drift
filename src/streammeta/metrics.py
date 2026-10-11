"""Primary exact empirical AUC and explicitly approximate online diagnostic."""
import numpy as np
from river import metrics
from sklearn.metrics import roc_auc_score, f1_score, cohen_kappa_score
from .models import expert_class

class Metrics:
    def __init__(self,n_thresholds):
        self.n_thresholds=n_thresholds
        self.approx=metrics.ROCAUC(n_thresholds=n_thresholds)
        self.f1=metrics.F1(); self.kappa=metrics.CohenKappa()

    def update(self,y,p):
        cls=expert_class(p)
        self.approx.update(y,p);self.f1.update(y,cls);self.kappa.update(y,cls)

    def result(self,y,p):
        y=np.asarray(y);p=np.asarray(p);pred=(p>=.5).astype(int)
        exact=float(roc_auc_score(y,p)) if len(np.unique(y))==2 else None
        k=float(cohen_kappa_score(y,pred,labels=[0,1])) if len(np.unique(np.r_[y,pred]))==2 else None
        return {"primary_metric":"roc_auc_exact","roc_auc_exact":exact,
                "roc_auc_streaming_approx":float(self.approx.get()),"auc_thresholds":self.n_thresholds,
                "f1":float(f1_score(y,pred,zero_division=0)),"cohen_kappa":k,
                "river_f1":float(self.f1.get()),"river_cohen_kappa":float(self.kappa.get()),
                "auc_exact_status":"defined" if exact is not None else "undefined_single_class"}

def recompute(y,p,n_thresholds):
    obj=Metrics(n_thresholds)
    for yy,pp in zip(y,p):obj.update(int(yy),float(pp))
    return obj.result(y,p)
