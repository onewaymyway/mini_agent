import pytest
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


@pytest.fixture(autouse=True)
def _no_retry_backoff(monkeypatch):
    """自动重跑的退避等待在测试中设为 0，避免用例真的睡眠。"""
    import novel_writer.worker as worker_mod

    monkeypatch.setattr(worker_mod, "RETRY_BACKOFF_SEC", (0,))
    import novel_writer.autopilot as ap_mod

    monkeypatch.setattr(ap_mod, "RESET_BACKOFF_SEC", (0,))

