import pytest
from test_cli import configured, run

from lime.config import Settings, load_settings


def test_defaults():
    assert Settings().width == 88
    assert Settings().vertical_padding == 3


def test_missing_config_file_uses_defaults():
    assert load_settings() == Settings()


def test_yaml_values_are_read(tmp_path):
    config = tmp_path / "config.yaml"
    config.write_text("width: 100\nvertical_padding: 4\n")
    settings = load_settings(config)
    assert settings.width == 100 and settings.vertical_padding == 4


def test_a_partial_config_keeps_the_other_default(tmp_path):
    config = tmp_path / "config.yaml"
    config.write_text("width: 72\n")
    settings = load_settings(config)
    assert settings.width == 72 and settings.vertical_padding == 3


@pytest.mark.parametrize(
    "yaml",
    [
        "[]",
        "width: -1",
        "width: 5",
        "width: 2000",
        "width: true",
        "width: '80'",
        "vertical_padding: -1",
        "vertical_padding: 3.5",
        "unknown: 1",
        "zen: true",
        "padding: 12",
        "width: 1\nwidth: 2",
        "!!python/object/apply:os.system ['id']",
    ],
)
def test_invalid_config_is_rejected(tmp_path, yaml):
    config = tmp_path / "config.yaml"
    config.write_text(yaml)
    with pytest.raises(ValueError):
        load_settings(config)


def test_an_unknown_key_names_itself(tmp_path):
    config = tmp_path / "config.yaml"
    config.write_text("padding: 8\n")
    with pytest.raises(ValueError, match="padding"):
        load_settings(config)


def test_the_config_file_drives_rendering(tmp_path):
    source = "This is a fairly long paragraph written so that a narrow measure has to wrap it."
    narrow = run("-", source=source, env={"XDG_CONFIG_HOME": configured(tmp_path, "width: 20\n")})
    wide = run("-", source=source, env={"XDG_CONFIG_HOME": configured(tmp_path, "width: 88\n")})
    assert narrow.returncode == 0 and wide.returncode == 0
    # A narrower measure wraps the sentence across more lines.
    assert len(narrow.stdout.splitlines()) > len(wide.stdout.splitlines())


def test_a_broken_config_file_reports_an_error(tmp_path):
    result = run(
        "-", source="# Hi", env={"XDG_CONFIG_HOME": configured(tmp_path, "width: nonsense\n")}
    )
    assert result.returncode == 1 and "width" in result.stderr
