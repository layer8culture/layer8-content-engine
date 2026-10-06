import copy
import json
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from studio_lib import config  # noqa: E402


@pytest.fixture
def cfg():
    return config.load_config()


@pytest.fixture
def plan():
    return copy.deepcopy(json.loads((ROOT / "tests" / "fixtures" / "plan-sample.json").read_text(encoding="utf-8")))


@pytest.fixture
def post(plan):
    return plan["posts"][0]
