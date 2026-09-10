import pytest
from test_cli import configured, run

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


def test_the_config_file_drives_rendering(tmp_path):
    source = "```python\nprint('hi')\n```"
    numbered = run("-", source=source, env={"XDG_CONFIG_HOME": configured(tmp_path, "")})
    plain = run(
        "-", source=source, env={"XDG_CONFIG_HOME": configured(tmp_path, "line_numbers: false\n")}
    )
    assert "1 " in numbered.stdout
    assert "1 " not in plain.stdout


def test_a_broken_config_file_reports_an_error(tmp_path):
    result = run(
        "-", source="# Hi", env={"XDG_CONFIG_HOME": configured(tmp_path, "headings: nonsense\n")}
    )
    assert result.returncode == 1 and "headings" in result.stderr


def test_interactive_defaults_to_auto():
    settings = load_settings()
    assert settings.interactive == "auto"


@pytest.mark.parametrize("key, value", [("interactive", "sometimes")])
def test_invalid_choice_is_rejected(tmp_path, key, value):
    path = tmp_path / "config.yaml"
    path.write_text(f"{key}: {value}\n")
    with pytest.raises(ValueError, match=key):
        load_settings(path)


def test_valid_choices_are_accepted(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text("interactive: 'off'\n")
    settings = load_settings(path)
    assert settings.interactive == "off"


def test_zen_defaults_to_false():
    assert Settings().zen is False


def test_zen_can_be_configured(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text("zen: true\n")
    settings = load_settings(path)
    assert settings.zen is True


def test_zen_must_be_boolean(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text("zen: 'true'\n")
    with pytest.raises(ValueError, match="zen"):
        load_settings(path)
