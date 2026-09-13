#!/usr/bin/env python3
"""remote-agent-dispatch — dispatch a prompt to a REMOTE provider over OpenAI-compatible HTTP.

This is the remote half of the emergency-fallback lane. It is the mirror of
`local-agent-dispatch.py` (the local lane) but pointed at an external provider.
It exists for ONE job: keep work moving when the local model can't serve and
the cloud budget is spent, by sending a self-contained prompt to a free /
renewing remote allowance.

Two non-negotiables, in code:

  1. LOUD IDENTITY. Before any payload leaves the machine, it prints exactly what
     it is about to send, to where, and that the destination is REMOTE — e.g.:

       ⚠ REMOTE  provider=gemini (Gemini API, tier=renewing_free)
                 model=gemini-2.5-flash  key=AIza…9x (39 chars)
                 destination=https://generativelanguage.googleapis.com/.../chat/completions

     You must see that your words are going to a third party. There is no silent
     remote path. --dry-run prints this banner and stops, sending nothing.

  2. EXPLICIT FILE APPROVAL. `--files` is REFUSED by default. Files are content
     you did not author in the prompt, and sending them to a remote provider is
     a bigger privacy step than sending the prompt. You must pass
     `--allow-remote-files` to attach files, and the banner then lists every
     file path so you see what is leaving. Text-only prompts go out freely.

Stdlib only (the transport is `remote_http`); no packages. The plan pins the
Gemini endpoint as the MVP; the other providers are declared in
`remote_provider_core` but reported "not implemented" until their dispatch path
lands in a later phase.

Usage:
    remote-agent-dispatch.py --provider gemini --prompt "TEXT" [--model M]
                             [--max-tokens N] [--files F ... --allow-remote-files]
                             [--outdir DIR] [--dry-run] [--json-out PATH]

Exit: 0 success · 2 provider error that is NOT a clean failover (auth/malformed/
policy) · 3 the provider refused to start (429/timeout/unavailable) — the caller
(agent-fallback) decides whether to fail over.
"""
import argparse
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))
from remote_provider_core import PROVIDERS, IMPLEMENTED, get  # noqa: E402
import remote_http  # noqa: E402

DEFAULT_MAX_FILE_CHARS = 48000


def _read_files(files, max_file_chars):
    """Read attachment files into a single context block, enforcing the per-file cap.

    Mirrors the local dispatcher's read_files_context: oversized files are REJECTED,
    never silently truncated, so a review never runs on a partial file.
    """
    blocks = []
    for fp in files:
        ap = os.path.abspath(os.path.expanduser(fp))
        if not os.path.isfile(ap):
            print("[!] file not found, skipping: %s" % ap, file=sys.stderr)
            continue
        content = open(ap, "r", encoding="utf-8", errors="replace").read()
        if max_file_chars > 0 and len(content) > max_file_chars:
            print("[!] file exceeds --max-file-chars, NOT attached: %s (%d > %d)"
                  % (ap, len(content), max_file_chars), file=sys.stderr)
            continue
        blocks.append("--- File: %s ---\n%s\n--- End File ---" % (ap, content))
    return blocks


def build_body(prompt, files, model, max_tokens, system, allow_files, max_file_chars):
    """Assemble the chat-completions body. Returns (body, attached_paths)."""
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    text = prompt
    attached = []
    if files:
        if not allow_files:
            raise SystemExit(
                "✖ refusing to attach %d file(s) to a REMOTE provider without "
                "--allow-remote-files. Files leave your machine; pass the flag to "
                "approve, or drop --files to send the prompt only.\n  files:\n    %s"
                % (len(files), "\n    ".join(files)))
        attached = _read_files(files, max_file_chars)
        if attached:
            text = (text + "\n\n" + "\n\n".join(attached)) if text else "\n\n".join(attached)
    messages.append({"role": "user", "content": text})
    body = {"model": model, "max_tokens": max_tokens, "messages": messages}
    return body, attached


def banner(provider, body, attached, dry_run, allow_files):
    """The loud REMOTE identity line. Printed to stderr BEFORE the network call."""
    model = body.get("model", "?")
    marker = " (DRY-RUN — nothing will be sent)" if dry_run else ""
    print("⚠ REMOTE%s  provider=%s (%s, tier=%s)" % (
        marker, provider.id, provider.display, provider.tier), file=sys.stderr)
    print("            model=%s  key=%s" % (
        model, remote_http._mask(provider.key_env)), file=sys.stderr)
    print("            destination=%s%s" % (
        provider.base(), provider.chat_path), file=sys.stderr)
    total_chars = sum(len(str(m.get("content") or "")) for m in body.get("messages", []))
    print("            payload≈%d chars, %d message(s)%s" % (
        total_chars, len(body.get("messages", [])),
        (", %d attached file(s) APPROVED via --allow-remote-files: %s"
         % (len(attached), ", ".join(attached)) if attached else "")))
    print(file=sys.stderr)


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Dispatch a prompt to a remote provider (OpenAI-compatible). "
                    "Prints a loud REMOTE banner before sending; files need "
                    "--allow-remote-files.")
    ap.add_argument("--provider", required=True,
                    help="provider id: %s" % ", ".join(sorted(PROVIDERS)))
    ap.add_argument("--prompt", help="the prompt text")
    ap.add_argument("--payload", help="JSON body file (overrides --prompt); the body as-is")
    ap.add_argument("--model", help="model id (default: provider's first known-good)")
    ap.add_argument("--max-tokens", type=int, default=2048)
    ap.add_argument("--system", default="", help="optional system message")
    ap.add_argument("--files", nargs="*", default=[], help="files to attach")
    ap.add_argument("--allow-remote-files", action="store_true",
                    help="approve sending --files to the remote provider")
    ap.add_argument("--max-file-chars", type=int, default=DEFAULT_MAX_FILE_CHARS)
    ap.add_argument("--outdir", help="output dir (default: a fresh temp dir)")
    ap.add_argument("--timeout", type=float, default=300.0)
    ap.add_argument("--dry-run", action="store_true",
                    help="print the REMOTE banner and stop; send nothing")
    ap.add_argument("--json-out", help="also write the structured Result to this path")
    a = ap.parse_args(argv)

    provider = get(a.provider)
    if provider is None:
        print("unknown provider %r (known: %s)" % (a.provider, ", ".join(sorted(PROVIDERS))),
              file=sys.stderr)
        return 2
    if provider.id not in IMPLEMENTED:
        print("✖ provider %r is declared but not implemented in the MVP (only %s)."
              % (provider.id, ", ".join(sorted(IMPLEMENTED))), file=sys.stderr)
        return 2
    if not provider.config_present():
        print("✖ provider %s needs env %s to be set. The remote lane is disabled until "
              "you provide a key (config/remote-providers.example.json documents each)."
              % (provider.id, ", ".join(provider.required_env())), file=sys.stderr)
        return 2
    if not a.payload and not a.prompt:
        print("provide --prompt TEXT or --payload BODY.json", file=sys.stderr)
        return 2

    model = a.model or (provider.default_models[0] if provider.default_models else "")
    if not model:
        print("✖ no --model given and provider %s has no known-good default; "
              "run `remote-provider-doctor.py %s` to see live models."
              % (provider.id, provider.id), file=sys.stderr)
        return 2

    if a.payload:
        body = json.load(open(a.payload))
        body.setdefault("model", model)
        attached = []
    else:
        body, attached = build_body(a.prompt, a.files, model, a.max_tokens,
                                    a.system, a.allow_remote_files, a.max_file_chars)

    outdir = a.outdir or tempfile.mkdtemp(prefix="remote-agent-")
    os.makedirs(outdir, exist_ok=True)

    banner(provider, body, attached, a.dry_run, a.allow_remote_files)

    out_path = os.path.join(outdir, "output.txt")
    out_fd = open(out_path, "w")
    try:
        result = remote_http.stream_chat(provider, body, outdir, out_fd, rfd=None,
                                         timeout=a.timeout, dry_run=a.dry_run)
    finally:
        out_fd.close()

    result.output_path = out_path
    if a.dry_run:
        print("dry-run complete — nothing was transmitted.", file=sys.stderr)
        if a.json_out:
            json.dump(result.to_dict(), open(a.json_out, "w"), indent=2)
        return 0

    if a.json_out:
        json.dump(result.to_dict(), open(a.json_out, "w"), indent=2)

    if result.ok:
        print("[✓] REMOTE %s returned %d chars in %.1fs (model=%s)" % (
            provider.id, result.output_chars, result.elapsed, result.model_effective),
            file=sys.stderr)
        print("[✓] output → %s" % out_path, file=sys.stderr)
        return 0

    print("✖ REMOTE %s failed: [%s] HTTP %d — %s" % (
        provider.id, result.error_class, result.status,
        (result.error_reason or "no reason")[:300]), file=sys.stderr)
    # Exit-code contract for the router:
    #   3 = a failover-class failure (quota/transient/timeout/model_unavailable)
    #   2 = a hard failure (auth/malformed/policy/transport) — do NOT retry this class
    return 3 if result.retriable else 2


if __name__ == "__main__":
    sys.exit(main())