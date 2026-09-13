#!/usr/bin/env python3
"""OpenAI-compatible streaming HTTP client for the remote lane.

Standard library only (http.client) — no packages, per the plan. The reason for
hand-rolling the stream instead of using curl like librarian-dispatch.py does:
we must capture the *response headers* (Retry-After, the effective model id in a
non-streaming fallback) and the *terminal status* as a first-class part of the
result record, and stream the body one line at a time so a cut stream is
distinguishable from a clean one. curl can do the stream but surfaces headers
and status only through awkward flags; http.client gives us both cleanly.

This module is transport only. It knows nothing about providers or policy.
Call `stream_chat` and it returns a `remote_provider_core.Result` plus the
streamed text (and reasoning text) the caller persists to disk.
"""
import http.client
import json
import os
import time
from urllib.parse import urlparse

from remote_provider_core import Result, classify


class _CountingFD:
    """Transparent proxy around `inner` that counts the characters written.

    Lets `stream_chat` report `output_chars` even when the stream is cut by an
    exception (the content already flushed to disk is real, recoverable output —
    it is NOT to be discarded). A `None` inner yields an inert, still-counting
    sink so the transport never has to special-case it.
    """
    def __init__(self, inner):
        self.inner = inner
        self.chars = 0

    def write(self, text):
        n = len(text)
        self.chars += n
        if self.inner is not None:
            self.inner.write(text)
        return n

    def flush(self):
        if self.inner is not None:
            self.inner.flush()

    def __getattr__(self, name):
        # Any other method/attr is delegated to the real file (seek, close, ...).
        if self.inner is None:
            raise AttributeError(name)
        return getattr(self.inner, name)


def _is_loopback(url):
    """True iff the URL points at a local address. The router and tests use this
    to *prove* local-only mode makes no non-loopback connection: a local-mode
    run must never call the remote transport against a non-loopback host."""
    host = urlparse(url).hostname or ""
    return host in ("localhost", "127.0.0.1", "::1", "[::1]") or host.endswith(".local") \
        or host.startswith("127.")


def stream_chat(provider, body, outdir, out_fd, rfd=None, timeout=120.0,
                dry_run=False):
    """POST `body` to `provider`'s chat endpoint, streaming.

    `out_fd`/`rfd` are open files (or None) that content / reasoning deltas are
    written to as they arrive, so the streamed output is on disk even if the
    stream is later cut. Returns a `Result`. Never raises for a provider error —
    it classifies into `Result.error_class` so the router can decide.
    """
    from remote_provider_core import LOCAL

    url = provider.base() + provider.chat_path
    parsed = urlparse(url)
    host = parsed.hostname
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    path = parsed.path + ("?" + parsed.query if parsed.query else "")
    method = "POST"

    if dry_run:
        # Report exactly what WOULD be sent, to where — and stop. No socket.
        return Result(ok=False, provider=provider.id, tier=provider.tier,
                      model_requested=body.get("model", ""),
                      model_effective="", status=0, error_class="dry_run",
                      error_reason="dry-run: would POST %s %s (key=%s)" % (
                          method, url, _mask(provider.key_env)))

    started = time.time()
    headers = provider.auth_headers()
    headers["Content-Type"] = "application/json"
    # Force streaming so a cut stream is detectable, and ask for usage.
    payload = dict(body)
    payload["stream"] = True
    payload.setdefault("stream_options", {"include_usage": True})
    data = json.dumps(payload).encode("utf-8")

    conn = None
    model_effective = body.get("model", "")
    status = 0
    retry_after = 0.0
    # Content is written through counting proxies so output_chars is known even
    # on a cut stream; the proxies are the fds the stream consumer receives.
    out_proxy = _CountingFD(out_fd)
    rfd_proxy = _CountingFD(rfd)

    try:
        conn = http.client.HTTPSConnection(host, port, timeout=timeout) \
            if parsed.scheme == "https" else http.client.HTTPConnection(host, port, timeout=timeout)
        conn.request(method, path, body=data, headers=headers)
        resp = conn.getresponse()
        status = resp.status

        # Capture Retry-After if the provider sent one (quota/429 path).
        ra = resp.getheader("Retry-After")
        if ra is not None:
            try:
                retry_after = float(ra)
            except ValueError:
                # HTTP-date form: seconds until that instant.
                try:
                    from email.utils import parsedate_to_datetime
                    retry_after = max(0.0, (parsedate_to_datetime(ra) -
                                             __import__("datetime").datetime.now(
                             __import__("datetime").timezone.utc)).total_seconds())
                except Exception:
                    retry_after = 0.0

        if 200 <= status < 300:
            ok_result = _consume_stream(resp, out_proxy, rfd_proxy)
            # A 2xx that yielded content but never reached [DONE] is a cut stream:
            # it produced real, kept output but did not finish. Mark it partial so
            # the Result and the router distinguish "complete" from "got some,
            # stream broke". Per the plan this is NEVER retried (the partial
            # output is preserved), so retriable stays False via the policy.
            got = out_proxy.chars > 0
            is_partial = got and not ok_result["complete"]
            return Result(
                ok=ok_result["complete"], provider=provider.id, tier=provider.tier,
                model_requested=body.get("model", ""),
                model_effective=ok_result["model"], status=status,
                error_class="partial" if is_partial else "",
                error_reason=("stream cut after %d chars, before [DONE]" % out_proxy.chars
                              if is_partial else ""),
                usage=ok_result["usage"], stream_complete=ok_result["complete"],
                partial=is_partial, output_chars=out_proxy.chars,
                elapsed=time.time() - started, retry_after=retry_after,
            )

        # Non-2xx: read the (usually short) error body for classification.
        body_bytes = resp.read()
        error_text = body_bytes.decode("utf-8", "replace")[:600]
        eclass = classify(status, body=error_text)
        return Result(
            ok=False, provider=provider.id, tier=provider.tier,
            model_requested=body.get("model", ""), model_effective=model_effective,
            status=status, error_class=eclass, error_reason=error_text.strip(),
            elapsed=time.time() - started, retry_after=retry_after,
        )
    except (http.client.HTTPException, OSError) as exc:
        # Network / timeout / connection-level failure. A pre-output timeout is
        # retriable; classify by whether we saw any data.
        import socket as _socket
        is_timeout = isinstance(exc, _socket.timeout) or \
            "timed out" in str(exc).lower() or "timeout" in str(exc).lower()
        eclass = "timeout" if is_timeout else "transient"
        # If content already flushed before the socket broke, this is a cut
        # stream (`partial`), NOT retriable — the partial output must be kept.
        if out_proxy.chars:
            eclass = "partial"
        return Result(
            ok=False, provider=provider.id, tier=provider.tier,
            model_requested=body.get("model", ""), model_effective=model_effective,
            status=status, error_class=eclass, error_reason=str(exc),
            output_chars=out_proxy.chars, elapsed=time.time() - started,
            retry_after=retry_after,
        )
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass


def _consume_stream(resp, out_fd, rfd):
    """Read an SSE (or plain-JSON) 200 response to completion.

    Distinguishes: clean [DONE] (stream_complete), a cut stream (complete False
    but chars>0 → the caller records `partial`), and an error body that arrived
    instead of a stream. Returns dict(model, usage, complete, chars, rchars).
    """
    import json as _json
    model = ""
    usage = {}
    complete = False
    chars = 0
    rchars = 0

    # Peek the first line to detect a non-SSE body (some providers return a
    # plain JSON error even on 200, or a plain JSON completion when stream is
    # ignored).
    raw_lines = []
    first = None
    try:
        while True:
            raw = resp.readline()
            if not raw:
                break
            first = raw
            raw_lines.append(first)
            break
    except Exception:
        pass

    if first is None:
        return {"model": model, "usage": usage, "complete": False,
                "chars": 0, "rchars": 0}

    first_text = first.decode("utf-8", "replace").strip()

    # Non-SSE JSON body (a single completed object)? Parse the whole stream as
    # the remaining buffer.
    if not first_text.startswith("data:"):
        rest = b""
        try:
            rest = resp.read()
        except Exception:
            pass
        blob = (first + rest).decode("utf-8", "replace").strip()
        return _parse_full_json(blob, out_fd, rfd)

    # SSE stream. The first line was already consumed by the peek above, so we
    # must process it too — feed it back as the first element of the stream, then
    # iterate the remainder. A helper writes one decoded SSE line's payload to the
    # fds and updates the running counts, so first-line and later lines share one code path.
    def _process_line(raw):
        """Handle one SSE line in place. Returns True if it is the terminal
        [DONE] (stop), None if the stream was cut (the caller sets complete False)."""
        nonlocal complete, chars, rchars, model, usage
        line = raw.decode("utf-8", "replace").strip()
        if not line.startswith("data:"):
            return None
        chunk = line[5:].strip()
        if chunk == "[DONE]":
            complete = True
            return True
        try:
            obj = _json.loads(chunk)
        except Exception:
            return None
        choices = obj.get("choices") or [{}]
        dobj = (choices[0] or {})
        m = dobj.get("message") or dobj.get("delta") or {}
        if m.get("role"):
            model = model or obj.get("model", "")
        if obj.get("model"):
            model = model or obj["model"]
        rdelta = m.get("reasoning_content") or m.get("reasoning") or ""
        if rdelta:
            if rfd is not None:
                rfd.write(rdelta)
                rfd.flush()
            rchars += len(rdelta)
        delta = m.get("content") or ""
        if delta:
            if out_fd is not None:
                out_fd.write(delta)
                out_fd.flush()
            chars += len(delta)
        if obj.get("usage"):
            usage = obj["usage"]
        return None

    try:
        _process_line(first)
        if complete:
            pass
        for raw in resp:
            if _process_line(raw) is True:
                break
    except Exception:
        # Stream cut mid-way: whatever is on disk is the (partial) output.
        complete = False

    return {"model": model, "usage": usage, "complete": complete,
            "chars": chars, "rchars": rchars}


def _parse_full_json(blob, out_fd, rfd):
    """Handle a non-streaming JSON completion (one full object)."""
    import json as _json
    model = ""
    usage = {}
    complete = False
    chars = 0
    try:
        obj = _json.loads(blob)
    except Exception:
        return {"model": model, "usage": usage, "complete": False,
                "chars": 0, "rchars": 0}
    if obj.get("error"):
        # A 200 that carries an error object.
        return {"model": model, "usage": usage, "complete": False,
                "chars": 0, "rchars": 0}
    model = obj.get("model", "")
    usage = obj.get("usage") or {}
    choices = obj.get("choices") or [{}]
    m = (choices[0] or {}).get("message") or {}
    content = m.get("content") or ""
    if content and out_fd is not None:
        out_fd.write(content)
        out_fd.flush()
    chars = len(content)
    complete = True
    return {"model": model, "usage": usage, "complete": complete,
            "chars": chars, "rchars": 0}


def _mask(env_name):
    """A safe reference to the key for dry-run/diagnostic output. Never the key."""
    v = os.environ.get(env_name, "")
    if not v:
        return "<unset>"
    return "%s…%s (%d chars)" % (v[:4], v[-2:], len(v))


if __name__ == "__main__":
    import sys as _sys
    if "-h" in _sys.argv or "--help" in _sys.argv:
        print("remote_http — transport-only OpenAI-compatible streaming client. "
              "This is a library module (import it; it is not a CLI).")
        print("Public functions: stream_chat(), _is_loopback(url).")
        print("It is exercised through remote-agent-dispatch.py, not run directly.")
    else:
        # Import-only self-test: prove the loopback helper classifies correctly.
        print("loopback(localhost)=", _is_loopback("http://localhost:8000/v1/chat/completions"))
        print("loopback(gemini)=", _is_loopback("https://generativelanguage.googleapis.com/v1beta/openai/chat/completions"))