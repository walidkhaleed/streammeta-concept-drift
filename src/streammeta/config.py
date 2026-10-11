"""Validated, immutable experiment configuration; no hidden global seed."""
from dataclasses import asdict, dataclass, field, replace
import hashlib

LENGTHS = {"Elec2": 45312, "Phishing": 1250, "Agrawal": 10000,
           "SEA": 10000, "STAGGER": 10000, "Hyperplane": 10000,
           "NullControl": 6000, "LabelRuleControl": 6000,
           "CovariateControl": 6000, "MixedControl": 6000}
DATASETS = tuple(list(LENGTHS)[:6])
METHODS = ("HAT", "ARF", "SRP", "RecentError", "Meta")
STAGES = ("smoke", "controlled", "primary", "windows", "delays", "robustness", "xai", "resource", "optional")
META_NAMES = ("Class_1_Ratio", "HAT_Error", "ARF_Error", "Model_Disagreement")

def derived_seed(seed: int, domain: str) -> int:
    return int.from_bytes(hashlib.sha256(f"v5:{domain}:{seed}".encode()).digest()[:4], "big")

@dataclass(frozen=True)
class Seeds:
    data: int = 42
    wrapper: int = 142
    model: int = 242
    router: int = 342
    xai: int = 442
    resource_run: int = 542

    def __post_init__(self):
        for k, v in asdict(self).items():
            if type(v) is not int or not 0 <= v < 2**32:
                raise ValueError(f"Invalid {k} seed: {v}")

@dataclass(frozen=True)
class XAIConfig:
    enabled: bool = False
    adwin_enabled: bool = True
    delta: float = .002
    raw_window: int = 400
    meta_window: int = 500
    min_meta: int = 50
    folds: int = 4
    permutations: int = 99
    block_size: int = 20
    scheduled_event: int | None = None

    def __post_init__(self):
        if not 0 < self.delta < 1:
            raise ValueError("ADWIN delta must be in (0,1)")
        if self.raw_window < 80 or self.raw_window % (2*self.block_size):
            raise ValueError("raw_window must have two equal halves of whole blocks")
        if self.folds < 2 or self.raw_window//(2*self.block_size) < self.folds:
            raise ValueError("Each temporal half needs at least one block per fold")
        if self.permutations < 1 or self.min_meta < 1 or self.meta_window < self.min_meta:
            raise ValueError("Invalid XAI sample/permutation settings")

@dataclass(frozen=True)
class RunConfig:
    dataset: str = "Agrawal"
    method: str = "Meta"
    stage: str = "smoke"
    arm: str = "default"
    design: str = "principal"
    replicate: int = 0
    resource_repeat: int = 0
    n: int | None = None
    window: int = 100
    delay: int = 0
    buffer_size: int = 1000
    refit_every: int = 250
    n_models: int = 5
    auc_thresholds: int = 101
    warmup: int = 1000
    memory_every: int = 25
    seeds: Seeds = field(default_factory=Seeds)
    xai: XAIConfig = field(default_factory=XAIConfig)

    def __post_init__(self):
        if self.dataset not in LENGTHS or self.method not in METHODS or self.stage not in STAGES:
            raise ValueError("Unknown dataset/method/stage")
        if self.n is not None and not 1 <= self.n <= LENGTHS[self.dataset]:
            raise ValueError("n must be positive and no longer than the declared stream")
        if min(self.window, self.buffer_size, self.refit_every, self.n_models, self.memory_every) <= 0:
            raise ValueError("Positive window/buffer/refit/model/sample counts required")
        if self.delay < 0 or self.warmup < 0 or self.auc_thresholds < 2:
            raise ValueError("Invalid delay/warmup/AUC threshold count")
        if self.xai.enabled and self.method != "Meta":
            raise ValueError("Dual-tier XAI currently explains the Meta router only")
        if self.xai.scheduled_event is not None and not 0 <= self.xai.scheduled_event < self.length:
            raise ValueError("Scheduled control event must be inside the stream")

    @property
    def length(self):
        return self.n if self.n is not None else LENGTHS[self.dataset]

    def to_dict(self):
        return asdict(self)

    @classmethod
    def from_dict(cls, data):
        data = dict(data)
        data["seeds"] = Seeds(**data.get("seeds", {}))
        data["xai"] = XAIConfig(**data.get("xai", {}))
        return cls(**data)

    def control_signature(self):
        d = self.to_dict()
        for k in ("stage", "arm", "design", "replicate", "resource_repeat"):
            d.pop(k)
        return d

