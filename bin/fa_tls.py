#!/usr/bin/env python3
"""fa_tls.py — ONE TLS client context for every remote HTTPS caller in bin/ (0.54.0 Task 0).

WHY: on TLS-inspecting networks (corporate wifi) Python 3.13+ turns ssl.VERIFY_X509_STRICT on
by default, and the inspection CA's certificate carries no Authority Key Identifier — so every
plain urlopen()/HTTPSConnection fails with CERTIFICATE_VERIFY_FAILED even though the chain is
valid (measured 2026-11-04: `Missing Authority Key Identifier` against integrate.api.nvidia.com).

The context returned here clears ONLY VERIFY_X509_STRICT: chain verification (CERT_REQUIRED) and
hostname checking stay ON, so an untrusted CA or a wrong hostname is still rejected. When the
`truststore` package is importable it is preferred (the OS trust store, which contains the
inspection CA). Verification is NEVER switched off (no CERT_NONE, no check_hostname=False).

Usage (library):
    import fa_tls
    ctx = fa_tls.client_context()
    urllib.request.urlopen(req, timeout=t, context=ctx)          # https URLs
    http.client.HTTPSConnection(host, port, timeout=t, context=ctx)

Usage (CLI):
    fa_tls.py --help     this text
    fa_tls.py --print    print which branch is active ("truststore" | "stdlib-clear-strict")

Env: none. The truststore branch needs no configuration.
"""
import argparse
import ssl
import sys


def client_context(cafile: "str | None" = None) -> ssl.SSLContext:
    """A TLS client context that works on TLS-inspecting networks without disabling
    verification: truststore (OS keychain) when importable, else the stdlib default context
    with ONLY ssl.VERIFY_X509_STRICT cleared. `cafile` (tests only) trusts an extra CA via the
    stdlib context, since a truststore context reads the OS store and cannot take one."""
    if cafile:
        # Tests only: trust an extra CA. (Under a truststore-injected venv the test restores
        # ssl.SSLContext to truststore's saved original first, so this stays the pristine class.)
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        ctx.load_verify_locations(cafile=cafile)
        ctx.verify_mode = ssl.CERT_REQUIRED
        ctx.check_hostname = True
    else:
        try:
            import truststore  # type: ignore
            ctx = truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        except ImportError:
            ctx = ssl.create_default_context()
    # 0.54.0: the inspection CA has no AKI and python >=3.13 rejects such certs under
    # VERIFY_X509_STRICT by default. Clear ONLY that flag; CERT_REQUIRED + check_hostname
    # keep the chain and the hostname honest (pinned by tests/test_fa_tls_0540.py).
    ctx.verify_flags &= ~ssl.VERIFY_X509_STRICT
    return ctx


def _branch_name() -> str:
    try:
        import truststore  # noqa: F401
        return "truststore"
    except ImportError:
        return "stdlib-clear-strict"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        prog="fa_tls.py",
        description="One TLS client context for every remote caller: truststore when available, "
                    "else stdlib default with ONLY VERIFY_X509_STRICT cleared (chain + hostname "
                    "verification stay ON).",
        epilog="Env: none. Library use: import fa_tls; fa_tls.client_context().")
    ap.add_argument("--print", action="store_true",
                    help="print which branch client_context() takes (truststore | "
                         "stdlib-clear-strict) and exit")
    args = ap.parse_args(argv)
    if args.print:
        ctx = client_context()
        print(f"{_branch_name()} strict_cleared={not bool(ctx.verify_flags & ssl.VERIFY_X509_STRICT)} "
              f"check_hostname={ctx.check_hostname} verify_mode={ctx.verify_mode.name}")
        return 0
    ap.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
