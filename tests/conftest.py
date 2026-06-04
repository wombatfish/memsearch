import pytest


@pytest.fixture(autouse=True)
def _isolate_memsearch_env(monkeypatch):
    """Prevent a real MEMSEARCH_DIR from leaking into tests.

    Several tests resolve the memory root via ``os.environ["MEMSEARCH_DIR"]``
    (e.g. ``maintenance.run_due_tasks``). On any machine with the plugin
    installed that variable is set, so without this fixture those tests read the
    live state file instead of their ``tmp_path`` sandbox and fail spuriously.
    """
    monkeypatch.delenv("MEMSEARCH_DIR", raising=False)
