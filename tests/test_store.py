import pytest

from kb import store


@pytest.fixture(autouse=True)
def _reset_embedded_client():
    store._EMBEDDED["client"] = None
    yield
    client = store._EMBEDDED["client"]
    store._EMBEDDED["client"] = None
    if client is not None:
        close = getattr(client, "close", None)
        if callable(close):
            close()


def _config(monkeypatch, values):
    monkeypatch.setattr(store, "cfg", lambda key, default=None: values.get(key, default))


@pytest.mark.parametrize(
    ("configured", "expected"),
    [
        ("server", False),
        ("embedded", True),
        ("EMBEDDED", True),      # 大小寫唔應該影響
        ("  embedded  ", True),  # 手寫 config 常見多餘空白
        (None, False),           # 預設要係 server，唔可以靜靜轉模式
        ("", False),
    ],
)
def test_mode_detection(monkeypatch, configured, expected):
    _config(monkeypatch, {"qdrant.mode": configured})
    assert store.is_embedded() is expected


def test_storage_path_defaults_inside_the_vault(monkeypatch, tmp_path):
    _config(monkeypatch, {"qdrant.mode": "embedded", "kb_root": str(tmp_path)})
    assert store.storage_path() == tmp_path / "state" / "qdrant"


def test_storage_path_can_be_overridden(monkeypatch, tmp_path):
    _config(monkeypatch, {
        "qdrant.mode": "embedded",
        "kb_root": str(tmp_path),
        "qdrant.path": str(tmp_path / "elsewhere"),
    })
    assert store.storage_path() == tmp_path / "elsewhere"


def test_embedded_callers_share_one_client(monkeypatch, tmp_path):
    """The embedded store admits one holder, and call sites open freely.

    A run that opens a second client — ingest followed by the catalog pass, for
    instance — would otherwise fail on the directory lock.
    """
    _config(monkeypatch, {"qdrant.mode": "embedded", "kb_root": str(tmp_path)})
    first = store.connect()
    second = store.connect()
    assert first is second


def test_server_mode_does_not_share(monkeypatch):
    """Independent clients are correct against a server and keep timeouts honest."""
    created = []

    class FakeClient:
        def __init__(self, **kwargs):
            created.append(kwargs)

    monkeypatch.setattr(store, "cfg", lambda key, default=None: {
        "qdrant.mode": "server",
        "qdrant.host": "example",
        "qdrant.port": 6333,
        "qdrant.timeout_batch": 60,
    }.get(key, default))
    monkeypatch.setitem(
        __import__("sys").modules,
        "qdrant_client",
        type("M", (), {"QdrantClient": FakeClient}),
    )
    store.connect()
    store.connect(timeout=5)
    assert len(created) == 2
    assert created[1]["timeout"] == 5


def test_embedded_creates_its_directory(monkeypatch, tmp_path):
    root = tmp_path / "vault"
    _config(monkeypatch, {"qdrant.mode": "embedded", "kb_root": str(root)})
    assert not (root / "state" / "qdrant").exists()
    store.connect()
    assert (root / "state" / "qdrant").is_dir()


def test_describe_names_the_mode(monkeypatch, tmp_path):
    _config(monkeypatch, {"qdrant.mode": "embedded", "kb_root": str(tmp_path)})
    assert "embedded" in store.describe()

    _config(monkeypatch, {"qdrant.mode": "server", "qdrant.host": "h", "qdrant.port": 1})
    assert store.describe() == "h:1"
