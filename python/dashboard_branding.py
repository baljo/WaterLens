# Applies the embedded WaterLens logo and “WaterLens with UNO Q” name to the existing local dashboard without external assets. 2026-09-08 21:38 Europe/Helsinki, Thomas Vikström.

import sys
import threading
import time

from waterlens_brand import WATERLENS_LOGO_DATA_URI


def _brand_page(page):
    page = page.replace(
        "<title>UNO Q Water Analyzer</title>",
        "<title>WaterLens with UNO Q</title>",
    )
    page = page.replace(
        '<div class="eyebrow">UNO Q WATER ANALYZER</div>',
        '<div class="eyebrow">WATERLENS WITH UNO Q</div>',
    )
    page = page.replace(
        "header h1 {",
        ".brand-logo {\n"
        "            display: block;\n"
        "            width: min(100%, 359px);\n"
        "            height: auto;\n"
        "            margin: 0 0 12px;\n"
        "        }\n\n"
        "        header h1 {",
        1,
    )
    page = page.replace(
        "<header>\n            <h1>UNO Q Water Analyzer</h1>",
        f'<header>\n            <img class="brand-logo" src="{WATERLENS_LOGO_DATA_URI}" alt="WaterLens">\n'
        "            <h1>WaterLens with UNO Q</h1>",
        1,
    )
    return page


def _install_when_ready():
    main_module = sys.modules.get("__main__")

    for _ in range(500):
        original = getattr(main_module, "dashboard_html", None)

        if callable(original) and not getattr(original, "_waterlens_branded", False):
            def branded_dashboard_html():
                return _brand_page(original())

            branded_dashboard_html._waterlens_branded = True
            main_module.dashboard_html = branded_dashboard_html
            print("WaterLens dashboard branding enabled")
            return

        time.sleep(0.02)

    print("WARNING: WaterLens dashboard branding hook was not installed")


def install_dashboard_branding():
    thread = threading.Thread(target=_install_when_ready, daemon=True)
    thread.start()
