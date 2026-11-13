"""0.54.0 Task 0 — ONE TLS client context for every remote caller (bin/fa_tls.py), and honest
hotswap failure messages (bin/free-agent-tool.py).

Why: on TLS-inspecting networks (corporate wifi) python 3.13+ defaults ssl.VERIFY_X509_STRICT
ON, and the inspection CA's certificate lacks an Authority Key Identifier — so EVERY plain
urllib/HTTPSConnection call fails with CERTIFICATE_VERIFY_FAILED even though the chain itself
is valid. The fix clears ONLY that flag (chain + hostname verification stay ON), or uses the
OS trust store via truststore when importable. Verification is never disabled.
"""
import importlib.util
import os
import re
import socket
import ssl
import stat
import subprocess
import sys
import threading
from pathlib import Path

import pytest

BIN = Path(__file__).resolve().parent.parent / "bin"
sys.path.insert(0, str(BIN))


@pytest.fixture(autouse=True)
def _pristine_ssl_for_cafile_tests(monkeypatch):
    """The picker venv injects truststore into ssl.SSLContext at interpreter start, which would
    verify against the OS keychain and ignore the test's temp CA. Restore the saved original for
    the duration of each test (truststore._api keeps it as _original_SSLContext)."""
    import ssl
    try:
        import truststore._api as tapi
        orig = tapi._original_SSLContext
    except Exception:
        orig = None
    if orig is not None and ssl.SSLContext is not orig:
        monkeypatch.setattr(ssl, "SSLContext", orig)


def _load_bin_module(name: str, filename: str):
    """Load a bin/ module whose filename may contain dashes (free-agent-tool.py)."""
    spec = importlib.util.spec_from_file_location(name, BIN / filename)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    sys.modules[name] = mod
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


fat = _load_bin_module("free_agent_tool", "free-agent-tool.py")


# --- fixture: a CA + leaf with NO Authority Key Identifier (reproduces the TLS-inspection CA) ---

def _openssl_gen(d: Path):
    """CA + localhost leaf, both WITHOUT an Authority Key Identifier (LibreSSL x509 -req adds
    none unless asked). Returns (ca_crt, leaf_crt, leaf_key)."""
    def run(*args):
        r = subprocess.run(["openssl", *args], capture_output=True, text=True, cwd=d)
        assert r.returncode == 0, f"openssl {args[0]} failed: {r.stderr}"
    run("req", "-x509", "-newkey", "rsa:2048", "-keyout", "ca.key", "-out", "ca.crt",
        "-days", "2", "-nodes", "-subj", "/CN=FA Test CA")
    run("req", "-newkey", "rsa:2048", "-keyout", "leaf.key", "-out", "leaf.csr",
        "-nodes", "-subj", "/CN=localhost")
    (d / "ext.cnf").write_text(
        "basicConstraints=critical,CA:FALSE\nsubjectAltName=DNS:localhost,IP:127.0.0.1\n")
    run("x509", "-req", "-in", "leaf.csr", "-CA", "ca.crt", "-CAkey", "ca.key",
        "-CAcreateserial", "-out", "leaf.crt", "-days", "2", "-extfile", "ext.cnf")
    # the fixture must actually lack the AKI — otherwise it cannot reproduce the real failure
    txt = subprocess.run(["openssl", "x509", "-in", str(d / "leaf.crt"), "-noout", "-text"],
                         capture_output=True, text=True).stdout
    assert "Authority Key Identifier" not in txt, "fixture leaf unexpectedly HAS an AKI"
    return d / "ca.crt", d / "leaf.crt", d / "leaf.key"


class _TLSServer:
    """A looping TLS server on a scratch loopback port (never 4141-4151 / 8000-8010)."""

    def __init__(self, cert: Path, key: Path):
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(str(cert), str(key))
        self._ctx = ctx
        self._srv = socket.socket()
        self._srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._srv.bind(("127.0.0.1", 0))          # port 0 = kernel picks a scratch port
        self._srv.listen(8)
        self.port = self._srv.getsockname()[1]
        self._stop = threading.Event()
        self._t = threading.Thread(target=self._loop, daemon=True)
        self._t.start()

    def _loop(self):
        while not self._stop.is_set():
            try:
                c, _ = self._srv.accept()
            except OSError:
                return
            try:
                with self._ctx.wrap_socket(c, server_side=True) as s:
                    s.send(b"ok")
            except Exception:
                pass

    def close(self):
        self._stop.set()
        self._srv.close()


def _connect(port: int, ctx: ssl.SSLContext):
    with socket.create_connection(("127.0.0.1", port), timeout=5) as s:
        with ctx.wrap_socket(s, server_hostname="localhost") as ss:
            return ss.recv(2)


def test_strict_flag_cleared_but_chain_verified(tmp_path, monkeypatch):
    """The AKI-less fixture MUST fail a strict context (proves it reproduces the corporate
    failure), and fa_tls.client_context() must still verify the chain and complete the
    handshake with ONLY VERIFY_X509_STRICT cleared."""
    monkeypatch.setitem(sys.modules, "truststore", None)   # force the stdlib branch here
    import fa_tls
    ca, leaf, key = _openssl_gen(tmp_path)
    srv = _TLSServer(leaf, key)
    try:
        strict = ssl.create_default_context(cafile=str(ca))
        strict.verify_flags |= ssl.VERIFY_X509_STRICT
        # LibreSSL (the system python 3.15 backend) rejects the AKI-less leaf under STRICT with
        # "Missing Authority Key Identifier" — that is the corporate-network failure reproduced.
        # OpenSSL 3.x backends may accept an AKI-less leaf, so the rejection is asserted only on
        # LibreSSL; on every backend, verification staying ON is proven by the untrusted-CA and
        # hostname-mismatch tests below.
        strict_rejects = False
        try:
            _connect(srv.port, strict)
        except ssl.SSLError:
            strict_rejects = True
        if "LibreSSL" in ssl.OPENSSL_VERSION:
            assert strict_rejects, "LibreSSL+STRICT must reject the AKI-less fixture cert"

        ctx = fa_tls.client_context(cafile=str(ca))          # trust the temp CA (test-only kwarg)
        assert not (ctx.verify_flags & ssl.VERIFY_X509_STRICT), "strict flag must be cleared"
        assert ctx.check_hostname, "hostname verification must stay ON"
        assert ctx.verify_mode == ssl.CERT_REQUIRED, "chain verification must stay ON"
        assert _connect(srv.port, ctx) == b"ok"
    finally:
        srv.close()


def test_untrusted_ca_still_rejected(tmp_path, monkeypatch):
    """Clearing the strict flag must not switch verification off: a leaf from a CA the context
    does NOT trust is still rejected."""
    monkeypatch.setitem(sys.modules, "truststore", None)
    import fa_tls
    ca, leaf, key = _openssl_gen(tmp_path)
    other = tmp_path / "other"
    other.mkdir()
    _openssl_gen(other)                                       # a SECOND, different CA
    srv = _TLSServer(leaf, key)
    try:
        ctx = fa_tls.client_context(cafile=str(other / "ca.crt"))  # trust the WRONG CA
        with pytest.raises(ssl.SSLError):
            _connect(srv.port, ctx)
    finally:
        srv.close()


def test_hostname_mismatch_still_rejected(tmp_path, monkeypatch):
    """A cert valid for localhost must not verify for a different hostname."""
    monkeypatch.setitem(sys.modules, "truststore", None)
    import fa_tls
    ca, leaf, key = _openssl_gen(tmp_path)
    srv = _TLSServer(leaf, key)
    try:
        ctx = fa_tls.client_context(cafile=str(ca))
        with socket.create_connection(("127.0.0.1", srv.port), timeout=5) as s:
            with pytest.raises(ssl.SSLError):
                ctx.wrap_socket(s, server_hostname="example.com")
    finally:
        srv.close()


def test_client_context_branch_matches_truststore_availability():
    """truststore branch when importable, stdlib-with-cleared-strict otherwise; either way the
    strict flag is off and chain+hostname checks are on."""
    import importlib.util
    import fa_tls
    ctx = fa_tls.client_context()
    if importlib.util.find_spec("truststore") is not None:
        import truststore
        assert isinstance(ctx, truststore.SSLContext), "truststore importable -> truststore branch"
    assert not (ctx.verify_flags & ssl.VERIFY_X509_STRICT)
    assert ctx.check_hostname and ctx.verify_mode == ssl.CERT_REQUIRED


# --- source scan: every network call site in bin/ goes through fa_tls (loopback allowed) ---

# loopback-only callers, each allowed because its URL is built from localhost in the same call
ALLOW_LOOPBACK = {
    "cancellation-matrix.py": "localhost",
    "direct-route-acceptance.py": "localhost",
    "lowkey-cli.py": "localhost",
}


def test_every_network_call_site_uses_fa_tls():
    bad = []
    for f in sorted(BIN.glob("*.py")):
        if f.name == "fa_tls.py":
            continue
        text = f.read_text(encoding="utf-8")
        for m in re.finditer(r"urlopen\(|HTTPSConnection\(", text):
            # the URL/host is usually built on the lines ABOVE the call — look around the match
            window = text[max(0, m.start() - 300):m.start() + 500]
            if "fa_tls.client_context()" in window:
                continue
            marker = ALLOW_LOOPBACK.get(f.name)
            if marker and marker in window:
                continue
            line = text.count("\n", 0, m.start()) + 1
            bad.append(f"{f.name}:{line}: {window[:90]!r}")
    assert not bad, "network call sites bypassing fa_tls.client_context():\n" + "\n".join(bad)


# --- honest hotswap failure messages (free-agent-tool.py) ---

def _write_stub(path: Path, body: str):
    path.write_text(body)
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def test_hotswap_failure_message_carries_reason_and_exit(tmp_path, monkeypatch):
    """A hotswap that prints `boom` to stderr and exits 3 must produce an error containing
    BOTH — never the empty `Hotswap failed: ` of 0.53.1."""
    stub = tmp_path / "hotswap-stub.sh"
    _write_stub(stub, "#!/bin/sh\necho boom >&2\nexit 3\n")
    monkeypatch.setenv("FA_HOTSWAP_SCRIPT", str(stub))
    with pytest.raises(RuntimeError) as ei:
        fat.execute_local_dispatch("some-alias", "hi", "spoof-id")
    msg = str(ei.value)
    assert "boom" in msg, f"stderr tail missing from: {msg!r}"
    assert "exit 3" in msg, f"exit status missing from: {msg!r}"


def test_hotswap_set_but_missing_script_aborts_loudly(tmp_path, monkeypatch):
    """A SET FA_HOTSWAP_SCRIPT that does not exist must abort naming the var — never fall
    through to the default."""
    monkeypatch.setenv("FA_HOTSWAP_SCRIPT", str(tmp_path / "nope.sh"))
    with pytest.raises(RuntimeError, match="FA_HOTSWAP_SCRIPT"):
        fat.execute_local_dispatch("some-alias", "hi", "spoof-id")


# 0.57.1: repeat gate flake — the 1 s timeout path races process spawn under the gate's
# 12-way parallel load (failed in 2 of 3 gate runs 2026-11-13, passes standalone), so it is
# deselected there like the other load_sensitive items; the contract still runs in full runs.
@pytest.mark.load_sensitive
def test_hotswap_timeout_says_so_and_names_orphan_pid(tmp_path, monkeypatch):
    """On timeout the message says it timed out, carries the partial output, and names any
    server the attempt started that is STILL RUNNING (reported, never killed by us)."""
    stub = tmp_path / "hotswap-slow.sh"
    _write_stub(stub, "#!/bin/sh\necho starting-load >&2\nsleep 30\n")
    meta_dir = tmp_path / "metas"
    meta_dir.mkdir()
    alive = os.getpid()
    (meta_dir / "server_8123.meta").write_text(f"pid={alive}\nport=8123\nmodel=x\n")
    (meta_dir / "server_8124.meta").write_text("pid=999999\nport=8124\n")  # dead pid
    monkeypatch.setenv("FA_HOTSWAP_SCRIPT", str(stub))
    monkeypatch.setenv("FA_HOTSWAP_TIMEOUT_S", "1")
    monkeypatch.setenv("FA_HOTSWAP_META_DIR", str(meta_dir))
    with pytest.raises(RuntimeError) as ei:
        fat.execute_local_dispatch("some-alias", "hi", "spoof-id")
    msg = str(ei.value)
    assert "timed out" in msg.lower(), f"timeout not stated: {msg!r}"
    assert "starting-load" in msg, f"partial output missing: {msg!r}"
    assert f"pid {alive}" in msg, f"live orphan pid not named: {msg!r}"
    assert "999999" not in msg, f"dead pid must not be named: {msg!r}"
