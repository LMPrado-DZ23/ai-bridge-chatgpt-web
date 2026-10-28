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
    """Build a simple tray icon with PIL (blue circle with a link symbol)."""
    from PIL import Image, ImageDraw
    size = 64
    img = Image.new('RGBA', (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    # Outer blue circle
    d.ellipse([0, 0, size - 1, size - 1], fill=(100, 180, 255, 255))
    # Dark inner background
    d.ellipse([14, 14, 50, 50], fill=(12, 12, 16, 255))
    # Two blue dots connected by a line (bridge/link metaphor)
    d.ellipse([18, 26, 28, 36], fill=(100, 180, 255, 255))
    d.ellipse([36, 26, 46, 36], fill=(100, 180, 255, 255))
    d.rectangle([27, 30, 37, 32], fill=(100, 180, 255, 255))
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
        width=520,
        height=640,
        min_size=(440, 540),
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
