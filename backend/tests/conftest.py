import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.main import create_app  # noqa: E402
from app.services import Services  # noqa: E402


@pytest.fixture
def services(tmp_path):
    s = Services(tmp_path / "test.db")
    yield s
    s.db.close()


@pytest.fixture
def client(services):
    app = create_app(services)
    with TestClient(app, base_url="http://127.0.0.1:8765", headers={"X-Teacher-Agent": "1"}) as c:
        yield c
