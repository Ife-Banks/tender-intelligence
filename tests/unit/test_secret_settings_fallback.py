import base64

from tender_intelligence.config.settings import get_env_settings
from tender_intelligence.crypto.secrets import SecretError, get_master_key


def test_master_key_reads_settings_dotenv_when_not_exported(monkeypatch, tmp_path):
    encoded = base64.b64encode(b"x" * 32).decode("ascii")
    (tmp_path / ".env").write_text(f"TI_MASTER_KEY={encoded}\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("TI_MASTER_KEY", raising=False)
    get_env_settings.cache_clear()
    try:
        assert get_master_key() == b"x" * 32
    finally:
        get_env_settings.cache_clear()


def test_master_key_rejects_wrong_decoded_length(monkeypatch):
    monkeypatch.setenv("TI_MASTER_KEY", base64.b64encode(b"short").decode("ascii"))
    get_env_settings.cache_clear()
    try:
        try:
            get_master_key()
        except SecretError as exc:
            assert "32 bytes" in str(exc)
        else:
            raise AssertionError("invalid master key length was accepted")
    finally:
        get_env_settings.cache_clear()
