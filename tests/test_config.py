import pytest
from test_cli import run

from lime.config import Settings, load_settings


def test_defaults_include_padding_on_all_sides_and_code_numbers():
    assert Settings().padding == 12
    assert Settings().vertical_padding == 6
    assert Settings().line_numbers


def test_yaml_values_and_relative_font(tmp_path):
    config = tmp_path / "config.yaml"
    config.write_text("padding: 8\nvertical_padding: 4\nline_numbers: false\nfont: test.ttf\n")
    settings = load_settings(config)
    assert settings.padding == 8 and settings.vertical_padding == 4
    assert settings.line_numbers is False
    assert settings.font == str(tmp_path / "test.ttf")


@pytest.mark.parametrize(
    "yaml",
    [
        "[]",
        "padding: -1",
        "padding: true",
        "line_numbers: yes",
        "width: '80'",
        "unknown: 1",
        "images: 'false'",
        "headings: foo",
        "padding: 1\npadding: 2",
        "!!python/object/apply:os.system ['id']",
    ],
)
def test_invalid_config_is_rejected(tmp_path, yaml):
    config = tmp_path / "config.yaml"
    config.write_text(yaml)
    with pytest.raises(ValueError):
        load_settings(config)


def test_cli_can_override_config(tmp_path):
    config = tmp_path / "config.yaml"
    config.write_text("line_numbers: false\n")
    source = "```python\nprint('hi')\n```"
    configured = run("--config", str(config), "-", source=source)
    overridden = run("--config", str(config), "--line-numbers", "-", source=source)
    assert "1 " not in configured.stdout
    assert "1 " in overridden.stdout


def test_explicit_missing_config_reports_error(tmp_path):
    result = run("--config", str(tmp_path / "missing.yaml"), "-", source="# Hi")
    assert result.returncode == 1 and "cannot read config" in result.stderr
