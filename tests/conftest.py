import pytest

import etf_momentum.data as data


@pytest.fixture(autouse=True)
def no_naver_fill(monkeypatch):
    """Tests run offline: the Naver gap fill is off unless a test turns it on with a fake fetch."""
    monkeypatch.setattr(data, "NAVER_FILL", False)
