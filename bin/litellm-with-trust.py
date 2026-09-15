#!/usr/bin/env python3
"""litellm-with-trust.py - start the LiteLLM proxy with the OS trust store injected.

THE PROBLEM THIS SOLVES. On a TLS-inspecting corporate network, every Python HTTPS request
fails with "[SSL: CERTIFICATE_VERIFY_FAILED] self-signed certificate in certificate chain",
because certifi's bundle has no corporate CA while the macOS Keychain does. This repo
already ships the fix as a PYTHONPATH shim (install/hf-ipv4/sitecustomize.py, which calls
truststore.inject_into_ssl()), and remote-session.sh exports PYTHONPATH for exactly that.

It never took effect. The pipx console script `~/.local/bin/litellm` carries `-E` in its
shebang, and `-E` makes Python IGNORE PYTHONPATH, so the proxy loaded the system
sitecustomize and kept using certifi. The visible symptom was misleading: LiteLLM wraps the
local TLS failure as an HTTP 500, which reads as a provider-side outage.

WHY A WRAPPER, rather than any of the easier options. We must not edit anything under
~/.local/pipx (pipx owns it and reinstalls silently discard changes), must not disable
certificate verification, and must not set a global CA bypass - the whole point is to keep
verification ON and merely verify against the trust store that actually contains the
corporate CA. So we re-enter the SAME pipx interpreter WITHOUT -E and import the proxy
entry point ourselves. Nothing outside this repo is modified.

Usage:
  litellm-with-trust.py [litellm args...]   start the proxy (args forwarded verbatim)
  litellm-with-trust.py --print-trust-state  report shim/truststore state, start nothing
  litellm-with-trust.py --help

Environment:
  LA_TRUST_SHIM   directory containing the sitecustomize.py shim. Defaults to this
                  repo's install/hf-ipv4. Also honoured via PYTHONPATH by the shim itself.

Exit codes: 0 ok; 2 usage error; 3 the LiteLLM entry point could not be imported.
"""
import os
import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parent.parent
DEFAULT_SHIM = REPO / "install" / "hf-ipv4"


def _shim_dir():
    return pathlib.Path(os.environ.get("LA_TRUST_SHIM") or DEFAULT_SHIM)


def _ensure_shim_on_path():
    """Load OUR shim by absolute path, and do not rely on `import sitecustomize`.

    THE TRAP, hit while building this: Python imports a `sitecustomize` module during
    interpreter startup, long before this function runs. On this machine that resolves to
    Homebrew's system copy, so by the time we get here `sitecustomize` is ALREADY in
    sys.modules -- inserting our directory into sys.path and importing the name is a no-op
    that returns the system file's path and injects nothing. The wrapper looked like it
    worked while doing exactly what the -E bug did.

    So the shim is loaded from its own file path under a private module name, which cannot
    collide with the startup import. Returns the path actually executed.
    """
    d = _shim_dir()
    shim = d / "sitecustomize.py"
    if not shim.is_file():
        print(f"litellm-with-trust: WARNING no trust shim at {shim}", file=sys.stderr)
        return ""
    if d.is_dir() and str(d) not in sys.path:
        sys.path.insert(0, str(d))
    try:
        import importlib.util
        spec = importlib.util.spec_from_file_location("_la_trust_shim", shim)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)            # runs inject_into_ssl()
        return str(shim)
    except Exception as exc:                       # a broken shim must not be silent
        print(f"litellm-with-trust: WARNING could not load the trust shim: {exc}",
              file=sys.stderr)
        return ""


def _truststore_state():
    """Whether ssl is actually using the OS trust store.

    truststore.inject_into_ssl() replaces ssl.SSLContext, so identity of that attribute is
    the observable evidence -- not merely that the import succeeded.
    """
    try:
        import ssl
        import truststore
    except Exception:
        return "unavailable"
    if ssl.SSLContext is getattr(truststore, "SSLContext", None):
        return "injected"
    return "not-injected"


def main(argv):
    args = list(argv)

    # Arguments are handled BEFORE the proxy is touched: probing this script with --help
    # must never start a server that then looks like help output.
    if "--help" in args or "-h" in args:
        print(__doc__)
        return 0

    loaded = _ensure_shim_on_path()
    state = _truststore_state()

    if "--print-trust-state" in args:
        print(f"sitecustomize={loaded}")
        print(f"truststore={state}")
        print(f"shim_dir={_shim_dir()}")
        print(f"interpreter={sys.executable}")
        return 0

    if state != "injected":
        # Loud, not fatal: on a normal network certifi is fine, and refusing to start would
        # break the common case to protect the corporate one. But a silent fallback is how
        # this bug hid for weeks, so it is stated every time.
        print(f"litellm-with-trust: NOTE OS trust store is {state} "
              f"(shim: {loaded or 'not loaded'}). On a TLS-inspecting network, "
              f"upstream HTTPS may fail with a certificate error.", file=sys.stderr)
    else:
        print(f"litellm-with-trust: OS trust store injected via {loaded}", file=sys.stderr)

    try:
        from litellm.proxy.proxy_cli import run_server
    except Exception as exc:
        print(f"litellm-with-trust: cannot import LiteLLM's proxy entry point: {exc}\n"
              f"  interpreter: {sys.executable}\n"
              f"  install with: pipx install 'litellm[proxy]'", file=sys.stderr)
        return 3

    # Hand the forwarded arguments to click's own parser as if we were the console script.
    sys.argv = ["litellm"] + [a for a in args if a != "--print-trust-state"]
    run_server()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
