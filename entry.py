import sys
import os
import logging
import threading
from pathlib import Path

logging.basicConfig(level=logging.WARNING)


def _ui_url() -> str:
    if getattr(sys, 'frozen', False):
        base = Path(sys._MEIPASS)  # type: ignore[attr-defined]
    else:
        base = Path(__file__).parent
    return (base / 'ai_bridge' / 'ui' / 'index.html').as_uri()


def _make_tray_image():
    """Load the same branded AI Bridge icon used by the Windows executable."""
    from PIL import Image, ImageDraw
    base = Path(sys._MEIPASS) if getattr(sys, 'frozen', False) else Path(__file__).parent
    icon_path = base / 'ai_bridge' / 'ui' / 'assets' / 'icons' / 'ai-bridge.ico'
    try:
        with Image.open(icon_path) as source:
            return source.convert('RGBA').resize((64, 64), Image.Resampling.LANCZOS)
    except (OSError, ValueError):
        # Keep the tray usable if an installation is missing the optional icon asset.
        size = 64
        img = Image.new('RGBA', (size, size), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        d.rounded_rectangle([1, 1, 62, 62], radius=18, fill=(18, 24, 42, 255), outline=(52, 61, 96, 255), width=2)
        d.line([(15, 38), (25, 28), (31, 26), (39, 30)], fill=(139, 140, 255, 255), width=5)
        d.line([(49, 26), (39, 36), (33, 38), (25, 34)], fill=(67, 215, 232, 255), width=5)
        d.ellipse([11, 34, 19, 42], fill=(139, 140, 255, 255))
        d.ellipse([45, 22, 53, 30], fill=(67, 215, 232, 255))
        return img


def main():
    from ai_bridge import proxy
    from ai_bridge.bridge import Bridge

    # Start proxy before creating the window so the port is ready
    proxy.start()

    import webview  # type: ignore[import]

    bridge = Bridge()

    window = webview.create_window(
        title='AI Bridge',
        url=_ui_url(),
        js_api=bridge,
        width=680,
        height=700,
        min_size=(520, 500),
        resizable=True,
        frameless=True,
        easy_drag=False,
        background_color='#0c0c10',
    )

    bridge.set_window(window)

    # ── System tray ───────────────────────────────────────────────────────────
    tray = None
    try:
        import pystray  # type: ignore[import]

        def _show(_icon=None, _item=None):
            window.show()

        def _quit(_icon=None, _item=None):
            nonlocal tray
            if tray:
                tray.stop()
            window.destroy()

        tray = pystray.Icon(
            'ai-bridge',
            _make_tray_image(),
            'AI Bridge',
            pystray.Menu(
                pystray.MenuItem('Mostrar', _show, default=True),
                pystray.MenuItem('Sair', _quit),
            ),
        )
        bridge.set_tray(tray)

        # Cancel native close (e.g. ALT+F4) → hide to tray instead
        def _on_closing():
            window.hide()
            return False

        window.events.closing += _on_closing

        threading.Thread(target=tray.run, daemon=True, name='ai-bridge-tray').start()
    except ImportError:
        pass  # pystray/PIL unavailable — close button destroys the window normally

    webview.start(debug=('--debug' in sys.argv))

    if tray:
        tray.stop()


if __name__ == '__main__':
    main()
