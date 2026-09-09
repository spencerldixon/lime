"""Install the generated formula in a local tap and create the user config."""

import os
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TAP = "lime/local"
FORMULA = f"{TAP}/lime"


def brew(*args, capture=False, check=True):
    return subprocess.run(
        ["brew", *args],
        check=check,
        text=True,
        capture_output=capture,
        env={**os.environ, "HOMEBREW_NO_AUTO_UPDATE": "1", "HOMEBREW_NO_INSTALL_UPGRADE": "1"},
    )


def main():
    formula = ROOT / "Formula/lime.rb"
    if not formula.exists():
        raise SystemExit("Run scripts/build_homebrew.py first.")
    tap_path = Path(brew("--repository", TAP, capture=True).stdout.strip())
    if not tap_path.exists():
        developer_was_off = "disabled" in brew("developer", "state", capture=True).stdout
        try:
            brew("tap-new", "--no-git", TAP)
        finally:
            if developer_was_off:
                brew("developer", "off")
    (tap_path / "Formula").mkdir(exist_ok=True)
    shutil.copyfile(formula, tap_path / "Formula/lime.rb")
    installed = brew("list", "--versions", FORMULA, capture=True, check=False).stdout.strip()
    brew("reinstall" if installed else "install", FORMULA)
    brew("test", FORMULA)
    config = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "lime/config.yaml"
    config.parent.mkdir(parents=True, exist_ok=True)
    try:
        with config.open("x", encoding="utf-8") as stream:
            stream.write((ROOT / "config.example.yaml").read_text())
    except FileExistsError:
        print(f"Kept existing configuration: {config}")
    else:
        print(f"Created configuration: {config}")
    print("Installed. Try: lime examples/demo.md")


if __name__ == "__main__":
    try:
        main()
    except subprocess.CalledProcessError as error:
        raise SystemExit(
            f"Homebrew stopped (exit {error.returncode}); address the error above and rerun."
        ) from None
