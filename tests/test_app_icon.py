from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
ICON = ROOT / "ai_bridge" / "ui" / "assets" / "icons" / "ai-bridge.ico"


def test_branded_icon_is_valid_multiresolution_ico():
    assert ICON.is_file()
    with Image.open(ICON) as image:
        assert image.format == "ICO"
        assert image.size[0] >= 64
        assert image.size[1] >= 64


def test_windows_executable_and_tray_use_branded_icon():
    spec = (ROOT / "build.spec").read_text(encoding="utf-8")
    entry = (ROOT / "entry.py").read_text(encoding="utf-8")
    assert "icon='ai_bridge/ui/assets/icons/ai-bridge.ico'" in spec
    assert "ai-bridge.ico" in entry


def test_mode_switch_ui_has_no_second_confirmation_dialog():
    source = (ROOT / "ai_bridge" / "ui" / "app.js").read_text(encoding="utf-8")
    assert "window.confirm(" not in source
    assert "api('activate_target', _currentTarget, mode)" in source
    assert "let _modeSwitchPending = false" in source
