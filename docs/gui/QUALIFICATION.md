# GUI launcher qualification receipts

## 2026-11-03 — macOS 26.7.2 (25G332), Apple Silicon, MANAGED Mac with Xcode installed

Not a clean default Mac: corporate-managed, developer tools present. Clean-Mac behaviour is UNTESTED.

Generation: `install/gui-launcher.py generate` from a source checkout (channel `checkout`), 0.23 s.
- `xattr`: `com.apple.provenance` only, no `com.apple.quarantine`
- `codesign -dv`: `Identifier=io.github.haiggoh.free-agents.launcher`, `Signature=adhoc`, `TeamIdentifier=not set`; `--verify --strict --deep` OK
- `spctl --assess`: `rejected` (expected for ad-hoc; Gatekeeper only blocks quarantined downloads)
- resolver skipped the installed plugin cache 0.50.4 (no GUI) and used the checkout via PLUGIN_HINT

Finder double-click (user, 10:38): the app opened the native window and rendered the core's Home
menu with emoji correct. Interim page is labels-only (no click/hover/loading yet, by design until
plan Task 9). Log: `locate.sh: starting …/bin/free-agents-gui --native`, no error. After the window
was closed: 0 `fa_gui/main.py` processes left.
- assumed (not explicitly reported): no "Apple could not verify" sheet and no Terminal window —
  the user reported only that the app opens. Re-confirm (a)/(b) at the next run.

Native selftest (Task 2): WKWebView loaded the UI (`connected`), button round trip wrote v0.50.4,
window close exited 0 with no leftover processes.

Known gaps: Dock label reads the Python process (cosmetic); a second click starts a second
instance; failure drills (renamed venv / missing plugin → dialog) NOT yet run on the real app
(covered only by locate.sh unit tests).

UNTESTED: clean default Mac, other macOS versions, Intel, Homebrew bottle lifecycle, MDM-restricted
Macs, Linux (no Linux runtime on this machine).
