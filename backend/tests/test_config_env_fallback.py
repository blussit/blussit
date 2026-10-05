from pathlib import Path

from app.core import config


def test_resolve_env_file_prefers_dev_file_when_present(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "BACKEND_DIR", tmp_path)
    monkeypatch.delenv("ENV_FILE", raising=False)
    (tmp_path / ".env.development").write_text("MONGO_URI=mongodb://localhost:27017\n")

    assert config._resolve_env_file() == tmp_path / ".env.development"


def test_resolve_env_file_falls_back_to_dotenv_when_dev_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "BACKEND_DIR", tmp_path)
    monkeypatch.delenv("ENV_FILE", raising=False)
    (tmp_path / ".env").write_text("MONGO_URI=mongodb://localhost:27017\n")

    assert config._resolve_env_file() == tmp_path / ".env"
