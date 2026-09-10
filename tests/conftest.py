import pytest


@pytest.fixture(autouse=True)
def _isolated_config(tmp_path_factory, monkeypatch):
    """Point XDG_CONFIG_HOME at an empty directory so tests never read a real
    ~/.config/lime/config.yaml on the machine running them. Tests that need a
    config write one and override this via env / monkeypatch.setenv."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path_factory.mktemp("xdg")))
