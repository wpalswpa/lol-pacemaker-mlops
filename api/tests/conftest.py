"""API 테스트는 규칙 기준선과 고정 자료로 돈다 (학습 산출물·Docker 불필요, AGENTS.md 7절)."""
import importlib
import os

import pytest
from fastapi.testclient import TestClient

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")


@pytest.fixture(scope="session")
def app_module():
    os.environ["MODEL_IMPL"] = "rule"
    os.environ["MODEL_DIR"] = FIXTURES
    os.environ.pop("APP_VERSION", None)
    import api.app

    return importlib.reload(api.app)


@pytest.fixture(scope="session")
def client(app_module):
    return TestClient(app_module.app)
