"""Suite-wide pytest guards.

open_url() (bin/session_picker_model.py) falls back to the real macOS `open` when LA_URL_OPENER is
unset, so any test that activates an "open:<provider>" item outside the picker harness launches a
real browser tab. Point the opener at `true` for the whole run; tests that assert on the opener
(picker_harness, mock.patch) still override this.
"""
import os

os.environ.setdefault("LA_URL_OPENER", "true")
