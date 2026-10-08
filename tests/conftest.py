"""Shared helpers. Everything here is SYNTHETIC test data (see tests/fixtures/scenarios.yaml)."""

from pathlib import Path

import pytest
import yaml

from intent_job_agent.config import Settings
from intent_job_agent.domain import IntentModel, Job, Label, Vocabulary
from intent_job_agent.engine import Engine, LabelInput
from intent_job_agent.llm import FakeClient
from intent_job_agent.store import Store

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = yaml.safe_load((ROOT / "tests/fixtures/scenarios.yaml").read_text(encoding="utf-8"))
assert FIXTURES["synthetic"] is True

BASE_INTENT = FIXTURES["base_intent"]

# Keys the synthetic user's vocabulary already knows. Keys outside this list are "new keys".
BASE_VOCAB = {
    "role": {"ml_engineering": [], "backend_engineering": ["后端", "server_side"], "data_science": []},
    "domain": {"fintech": ["金融科技", "支付"], "healthcare": [], "ecommerce": []},
    "specialty": {"ai_infra": [], "recsys": ["推荐算法"], "cv": [], "risk_control": ["风控"]},
    "seniority": {"senior": [], "mid": [], "new_grad": []},
    "company_type": {"big_tech": ["大厂"], "startup_growth": [], "startup_early": []},
    "location": {
        "china": [],
        "china.shanghai": [],
        "china.beijing": [],
        "china.shenzhen": [],
        "china.hangzhou": [],
        "us": [],
        "us.seattle": [],
    },
    "work_mode": {"remote": [], "hybrid": [], "onsite": []},
    "employment_type": {"full_time": [], "contract": ["外包"]},
    "tech_stack": {"python": [], "go": [], "pytorch": [], "cuda": []},
}


def settings() -> Settings:
    return Settings.load(ROOT / "configs/default.yaml")


class World:
    """A fresh store + engine for one test."""

    def __init__(self, tmp_path: Path, intent: dict | None = None, compensation=None, evidence=None, llm=None):
        self.settings = settings()
        self.store = Store(tmp_path / "agent.sqlite")
        self.llm = llm or FakeClient()
        self.engine = Engine(self.store, self.settings, self.llm)
        self._intent = intent if intent is not None else BASE_INTENT
        self._compensation = compensation
        self._evidence = evidence or {}
        self._n = 0
        self._initialized = False

    def _id(self, prefix: str) -> str:
        self._n += 1
        return f"{prefix}-{self._n}"

    def job(self, tags, **kw) -> Job:
        return Job(id=kw.pop("id", self._id("job")), title=kw.pop("title", "Synthetic job"), tags=tags, **kw)

    def history(self, tags, value, slot="recommended", day="2026-09-20", label_id=None, **job_kw) -> Label:
        """Seed a past label without running analysis (as if it happened before this test)."""
        job = self.job(tags, **job_kw)
        self.store.add_job(job)
        label = Label(id=label_id or self._id("label"), job_id=job.id, day=day, slot=slot, value=value)
        self.store.add_label(label)
        return label

    def init(self) -> IntentModel:
        intent = IntentModel.build(self._intent, compensation=self._compensation, evidence=self._evidence)
        self.store.initialize(intent, Vocabulary.build(BASE_VOCAB))
        self._initialized = True
        return intent

    def day(self, day: str, *inputs: LabelInput):
        if not self._initialized:
            self.init()
        return self.engine.process_day(day, list(inputs))

    @property
    def intent(self) -> IntentModel:
        return self.store.current_intent()


@pytest.fixture
def world(tmp_path):
    return World(tmp_path)


@pytest.fixture
def make_world(tmp_path):
    counter = {"n": 0}

    def factory(**kw):
        counter["n"] += 1
        path = tmp_path / f"w{counter['n']}"
        path.mkdir()
        return World(path, **kw)

    return factory
