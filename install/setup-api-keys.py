#!/usr/bin/env python3
"""Guide provider signup and store new credentials without making API calls."""

import argparse
import getpass
import os
from pathlib import Path
import re
import secrets
import stat
import sys
import termios
import warnings
import webbrowser
from dataclasses import dataclass


@dataclass(frozen=True)
class Provider:
    slug: str
    name: str
    url: str
    steps: str
    files: tuple
    note: str = "session available; choose model"
    optional: bool = False


# Flat filenames match existing credentials. No secrets or inferred model access here.
PROVIDERS = (
    Provider("gemini", "Google Gemini", "https://aistudio.google.com/apikey",
             "Sign in, choose Create API key, and select a project with free-tier access.",
             ("gemini",), "session available"),
    Provider("groq", "Groq", "https://console.groq.com/keys",
             "Create an account, then Create API Key. Name it Local Agents.",
             ("groq",), "session available"),
    Provider("openrouter", "OpenRouter", "https://openrouter.ai/settings/keys",
             "Create Key, name it Local Agents. Later, select only explicitly free models.",
             ("openrouter",), "session available"),
    Provider("cloudflare", "Cloudflare Workers AI", "https://dash.cloudflare.com/profile/api-tokens",
             "Create Token with Account > Workers AI > Read and Edit for your account.\n"
             "Copy Account ID from the account dashboard (Workers AI > Use REST API).",
             ("cloudflare", "cloudflare-account-id"), "session available"),
    Provider("mistral", "Mistral", "https://console.mistral.ai/home?profile_dialog=api-keys",
             "Sign up, open API Keys in your profile, and create a Studio key for your free plan.",
             ("mistral",)),
    Provider("zai", "Z.AI", "https://z.ai/manage-apikey/apikey-list",
             "Create an API key. Later, use only models currently listed as zero-priced.",
             ("zai",)),
    Provider("siliconflow", "SiliconFlow", "https://cloud.siliconflow.com/account/ak",
             "Sign up, then Create API Key. Later, select a zero-priced model.",
             ("siliconflow",)),
    Provider("llm7", "LLM7", "https://dash.llm7.io",
             "Sign in to the dashboard and create or copy your API token.", ("llm7",)),
    Provider("kilo", "Kilo", "https://app.kilo.ai",
             "Sign in to your personal account. Open Your Profile; copy the API key at the bottom.",
             ("kilo",)),
    Provider("vercel", "Vercel AI Gateway",
             "https://vercel.com/d?title=AI+Gateway+API+Keys&to=%2F%5Bteam%5D%2F~%2Fai-gateway%2Fapi-keys",
             "Select your team, then Create key in AI Gateway. Check its budget before later use.",
             ("vercel",)),
    Provider("sambanova", "SambaNova", "https://cloud.sambanova.ai/dashboard",
             "Sign up, open API Keys, and create a key. Check current trial/credit eligibility.",
             ("sambanova",), "optional credits; choose model", True),
    Provider("modelscope", "ModelScope", "https://modelscope.cn/my/myaccesstoken",
             "Sign in and create/copy an access token. Check regional and verification requirements.",
             ("modelscope",), "regional eligibility; choose model", True),
    Provider("cerebras", "Cerebras", "https://cloud.cerebras.ai/platform/",
             "Open API Keys and create a key. Check current trial or account credits.",
             ("cerebras",), "optional trial; session available", True),
    Provider("nvidia", "NVIDIA", "https://build.nvidia.com/settings/api-keys",
             "Sign in, then generate an API key. Check the account's available API credits.",
             ("nvidia",), "account credits; session available", True),
)


class StoreError(Exception):
    """Safe, value-free diagnostic suitable for displaying to the user."""


class Store:
    def __init__(self, path):
        self.path = Path(path).expanduser().absolute()
        self.fd = None

    def __enter__(self):
        # Refuse symlink traversal, including a symlink in a parent directory.
        if self.path.resolve() != self.path:
            raise StoreError("Credential directory must not contain symlinks.")
        try:
            self.fd = os.open(self.path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        except FileNotFoundError:
            return self
        info = os.fstat(self.fd)
        if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) & 0o077:
            os.close(self.fd)
            self.fd = None
            raise StoreError("Credential directory must be owned by you with mode 700; no permissions changed.")
        return self

    def __exit__(self, *args):
        if self.fd is not None:
            os.close(self.fd)

    def state(self, name):
        if self.fd is None:
            return "missing"
        try:
            info = os.stat(name, dir_fd=self.fd, follow_symlinks=False)
        except FileNotFoundError:
            return "missing"
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                or stat.S_IMODE(info.st_mode) & 0o077 or info.st_nlink != 1):
            return "protected"
        return "saved" if info.st_size else "empty (kept)"

    def status(self, provider):
        states = [self.state(name) for name in provider.files]
        if all(s == "saved" for s in states):
            return "saved / not tested"
        if all(s == "missing" for s in states):
            return "missing"
        return "partial / kept" if "saved" in states else "check files / kept"

    def save_new(self, name, value):
        if name not in {n for p in PROVIDERS for n in p.files}:
            raise StoreError("Unknown credential filename.")
        validate_value(name, value)
        if self.fd is None:
            try:
                self.path.mkdir(mode=0o700)
            except FileExistsError:
                pass
            self.__enter__()
        temp = ".setup-" + secrets.token_hex(12)
        fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                     0o600, dir_fd=self.fd)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(value.encode("ascii") + b"\n")
                stream.flush()
                os.fsync(stream.fileno())
            # Atomic publish without replacement: even concurrent additions are preserved.
            os.link(temp, name, src_dir_fd=self.fd, dst_dir_fd=self.fd,
                    follow_symlinks=False)
        except FileExistsError:
            raise StoreError("A file already exists; it was kept.") from None
        finally:
            os.unlink(temp, dir_fd=self.fd)
        os.fsync(self.fd)


def validate_value(name, value):
    if not value or len(value) > 8192 or any(ord(c) < 33 or ord(c) > 126 for c in value):
        raise StoreError("Paste just the key: no spaces, newlines, or control characters.")
    if value.startswith(("export ", "Bearer ", "\"", "'")):
        raise StoreError("Paste the raw key without quotes or a command.")
    if name == "cloudflare-account-id" and not re.fullmatch(r"[a-fA-F0-9]{32}", value):
        raise StoreError("Account ID should contain 32 hexadecimal characters.")


def hidden_input(label):
    if not sys.stdin.isatty() or not sys.stderr.isatty():
        raise StoreError("Hidden input needs a terminal. Run csl setup-remote in Terminal.")
    with warnings.catch_warnings():
        warnings.simplefilter("error", getpass.GetPassWarning)
        try:
            return getpass.getpass(label)
        except getpass.GetPassWarning:
            raise StoreError("Hidden input is unavailable; nothing was saved.") from None


class TerminalInput:
    """Receive complete bracketed pastes without echo or a second Enter press."""

    def __enter__(self):
        if not sys.stdin.isatty() or not sys.stderr.isatty():
            raise StoreError("Setup needs a terminal with hidden input.")
        self.fd = sys.stdin.fileno()
        self.original = termios.tcgetattr(self.fd)
        settings = termios.tcgetattr(self.fd)
        settings[3] &= ~(termios.ECHO | termios.ICANON)
        settings[6][termios.VMIN] = 1
        settings[6][termios.VTIME] = 0
        termios.tcsetattr(self.fd, termios.TCSADRAIN, settings)
        self.last_paste = None
        sys.stderr.write("\x1b[?2004h")
        sys.stderr.flush()
        return self

    def __exit__(self, *args):
        try:
            # Queued duplicate pastes must never be delivered to the caller's shell.
            termios.tcflush(self.fd, termios.TCIFLUSH)
            sys.stderr.write("\x1b[?2004l")
            sys.stderr.flush()
        finally:
            self.last_paste = None
            termios.tcsetattr(self.fd, termios.TCSADRAIN, self.original)

    def byte(self):
        value = os.read(self.fd, 1)
        if not value or value == b"\x04":
            raise EOFError
        if value == b"\x03":
            raise KeyboardInterrupt
        return value

    def paste(self):
        value = bytearray()
        tail = bytearray()
        overflow = False
        while True:
            tail.extend(self.byte())
            if tail.endswith(b"\x1b[201~"):
                value.extend(tail[:-6])
                break
            if len(tail) > 6:
                if len(value) < 8192:
                    value.append(tail[0])
                else:
                    overflow = True
                del tail[0]
        if overflow:
            raise StoreError("Paste is too long; paste only the raw key.")
        try:
            return value.decode("ascii").rstrip("\r\n")
        except UnicodeDecodeError:
            raise StoreError("Paste only the raw API key (ASCII characters).") from None

    def read(self, label, secret=False):
        sys.stderr.write(label)
        sys.stderr.flush()
        value = bytearray()
        while True:
            char = self.byte()
            if char == b"\x1b":
                # CSI is used for both bracketed paste and arrow/navigation keys.
                if self.byte() != b"[":
                    continue
                sequence = bytearray()
                while len(sequence) < 32:
                    char = self.byte()
                    sequence.extend(char)
                    if 0x40 <= char[0] <= 0x7e:
                        break
                if sequence != b"200~":
                    continue
                try:
                    pasted = self.paste()
                except StoreError as exc:
                    sys.stderr.write(f"\n{exc}\n{label}")
                    sys.stderr.flush()
                    continue
                # A late second paste may reach a menu or the next field. Never echo it.
                if not secret or pasted == self.last_paste or value:
                    sys.stderr.write("\nPaste ignored here; use the requested field or menu choice.\n" + label)
                    sys.stderr.flush()
                    continue
                self.last_paste = pasted
                termios.tcflush(self.fd, termios.TCIFLUSH)
                sys.stderr.write("\n")
                sys.stderr.flush()
                return pasted
            if char in (b"\r", b"\n"):
                sys.stderr.write("\n")
                sys.stderr.flush()
                return value.decode("ascii")
            if char in (b"\x7f", b"\x08"):
                if value:
                    value.pop()
                    if not secret:
                        sys.stderr.write("\b \b")
                        sys.stderr.flush()
            elif 32 <= char[0] <= 126 and len(value) < 8192:
                value.extend(char)
                if not secret:
                    sys.stderr.write(char.decode("ascii"))
                    sys.stderr.flush()


def select(text, store):
    if text.lower() == "missing":
        # Optional trials and regional accounts remain deliberate individual choices.
        return [p for p in PROVIDERS if not p.optional and any(store.state(n) == "missing" for n in p.files)]
    result = []
    for item in re.split(r"[,\s]+", text.strip().lower()):
        provider = next((p for i, p in enumerate(PROVIDERS, 1)
                         if item in (str(i), p.slug)), None)
        if provider is None:
            raise StoreError("Choose listed numbers or names, separated by commas, or 'missing'.")
        if provider not in result:
            result.append(provider)
    return result


def show(store):
    print(f"\nRemote API key setup\nSave to: {store.path}")
    print("Saved means stored, not tested. Account quotas and billing still apply.\n")
    for i, provider in enumerate(PROVIDERS, 1):
        if provider.optional and not PROVIDERS[i - 2].optional:
            print("\nOptional accounts (select individually):")
        print(f" {i:2}) {provider.name:23} {store.status(provider):21} {provider.note}")


def wizard(store, providers, terminal=None):
    added = []
    ask = terminal.read if terminal else input
    try:
        for i, provider in enumerate(providers, 1):
            missing = [n for n in provider.files if store.state(n) == "missing"]
            print(f"\n[{i}/{len(providers)}] {provider.name}")
            if not missing:
                print("Existing files kept; nothing to add.")
                continue
            print(provider.url)
            print(provider.steps)
            action = ask("[o] Open page  [Enter] Paste key  [s] Skip  [q] Finish: ").strip().lower()
            while action not in ("", "o", "s", "q"):
                action = ask("Choose o, Enter, s, or q: ").strip().lower()
            if action == "q":
                return True
            if action == "s":
                continue
            if action == "o":
                try:
                    if not webbrowser.open(provider.url):
                        print("Open the URL above in your browser.")
                except webbrowser.Error:
                    print("Open the URL above in your browser.")
            # Gather this provider's missing fields before creating any files.
            pending = {}
            for name in missing:
                while True:
                    label = "Account ID" if name == "cloudflare-account-id" else "API key/token"
                    prompt = f"{label} (hidden; paste advances automatically; Enter skips provider): "
                    value = terminal.read(prompt, secret=True) if terminal else hidden_input(prompt)
                    if not value:
                        break
                    try:
                        validate_value(name, value)
                    except StoreError as exc:
                        print(exc)
                        continue
                    pending[name] = value
                    print("\033[32m✓ Key received\033[0m" if label == "API key/token"
                          else "\033[32m✓ Account ID received\033[0m", flush=True)
                    break
                if not value:
                    pending.clear()
                    break
            for name, value in pending.items():
                try:
                    store.save_new(name, value)
                    added.append(name)
                    print(f"Saved {name} (not tested).")
                except StoreError as exc:
                    print(f"{name}: {exc}")
                except OSError:
                    print(f"Could not finish saving {name}; check file status on the next run.")
            pending.clear()
    finally:
        print(f"\nSaved {len(added)} new file(s). Existing files kept. No API calls made.")
        if added:
            print("Added: " + ", ".join(added))
        print("Stored keys remain untested; session access depends on the provider and account.")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, epilog=(
        "Environment: LA_API_KEYS_DIR overrides ~/.api_keys; HOME supplies the default. "
        "BROWSER controls the optional browser opener. Keys are accepted only in hidden terminal prompts. "
        "Existing files are never overwritten. No validation or inference requests are made."))
    parser.add_argument("providers", nargs="*", help="optional provider names or numbers; otherwise show picker")
    parser.add_argument("--list", action="store_true", help="show providers and file status without changing anything")
    parser.add_argument("--keys-dir", type=Path, default=Path(os.environ.get("LA_API_KEYS_DIR", "~/.api_keys")),
                        help="credential directory (default: LA_API_KEYS_DIR or ~/.api_keys)")
    args = parser.parse_args(argv)
    try:
        with Store(args.keys_dir) as store:
            show(store)
            if args.list:
                return 0
            if not sys.stdin.isatty() or not sys.stderr.isatty():
                raise StoreError("Setup needs a terminal. Use --list for a read-only inventory.")
            with TerminalInput() as terminal:
                providers = select(",".join(args.providers), store) if args.providers else None
                while True:
                    if all(all(store.state(n) == "saved" for n in p.files) for p in PROVIDERS):
                        print("\n✓ All available providers have credentials saved. Setup complete.")
                        return 0
                    if providers is None:
                        print("\nSelect numbers/names, e.g. 5,6,8. 'missing' selects missing keys in the main group.")
                        answer = terminal.read("Providers [Enter to quit]: ").strip()
                        if not answer or answer.lower() == "q":
                            return 0
                        try:
                            providers = select(answer, store)
                        except StoreError as exc:
                            print(exc)
                            continue
                    if wizard(store, providers, terminal):
                        return 0
                    providers = None
                    show(store)
        return 0
    except (EOFError, KeyboardInterrupt):
        print("\nSetup stopped. Previously saved files remain available.")
        return 130
    except StoreError as exc:
        print(f"Setup: {exc}", file=sys.stderr)
        return 1
    except OSError:
        print("Setup could not access the credential directory; check its location and permissions.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
