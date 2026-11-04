#!/usr/bin/env python3
"""docs/gui/design/shoot.py — Task 8: start both direction apps, screenshot every page at
1280x800 and 900x700, light and dark, into docs/gui/screens/. Run with the GUI venv python.

Usage: shoot.py [--help]   (no arguments; unknown flags exit non-zero with usage)
"""
import argparse
import subprocess
import sys
import time
from pathlib import Path

parser = argparse.ArgumentParser(description="Screenshot the two Task 8 design directions.")
parser.parse_args()

from playwright.sync_api import sync_playwright

HERE = Path(__file__).resolve().parent
OUT = HERE.parent / "screens"
OUT.mkdir(exist_ok=True)
SIZES = [(1280, 800), (900, 700)]
APPS = [("a", 8901), ("b", 8802)]
PAGES = ["/", "/remote"]


def main():
    procs = []
    for name, port in APPS:
        procs.append(subprocess.Popen(
            [sys.executable, str(HERE / f"direction_{name}.py")],
            cwd=HERE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
    try:
        # NiceGUI's first import+bind takes minutes on this loaded shared box — poll, don't sleep.
        import urllib.request
        for name, port in APPS:
            for _ in range(24):
                try:
                    urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=3)
                    break
                except Exception:
                    time.sleep(10)
            print("app up:", name, port, flush=True)
        with sync_playwright() as p:
            browser = p.chromium.launch()
            for name, port in APPS:
                for page_path in PAGES:
                    for theme in ("light", "dark"):
                        for w, h in SIZES:
                            page = browser.new_page(viewport={"width": w, "height": h})
                            page.goto(f"http://127.0.0.1:{port}{page_path}?theme={theme}")
                            page.wait_for_timeout(700)
                            fname = f"{name}-{page_path.strip('/') or 'home'}-{theme}-{w}x{h}.png"
                            page.screenshot(path=str(OUT / fname), full_page=True)
                            page.close()
                            print("shot", fname)
            browser.close()
    finally:
        for pr in procs:
            pr.terminate()


if __name__ == "__main__":
    main()
