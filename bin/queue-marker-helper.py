#!/usr/bin/env python3
"""
queue-marker-helper.py — Deterministic helper for generating QUEUE_ANSWERED markers.

USAGE:
    queue-marker-helper.py "<queued prompt content>"

EXAMPLE:
    $ queue-marker-helper.py "/run-to-completion"
    [[QUEUE_ANSWERED:ae414ae0]]

DESCRIPTION:
    This helper script generates the agreed marker format that satisfies
    the local-queue-stop-hook.py for preventing re-notification of 
    already-addressed queued prompts.

    The stop hook considers a queued prompt addressed when the assistant's
    response contains a marker of the form:
        [[QUEUE_ANSWERED:<8-char-hex>]]
    
    where <8-char-hex> is the first 8 hexadecimal characters of the SHA256
    hash of the queued prompt's exact content.

    Rather than requiring manual hash calculation and formatting (which is
    error-prone), this script provides a deterministic way to generate
    the correct marker.

OUTPUT:
    Prints exactly: [[QUEUE_ANSWERED:<8-char-hex>]]
    where <8-char-hex> = first 8 chars of SHA256(input content)

EXIT CODES:
    0  - Success
    1  - Usage error (wrong number of arguments)
"""
import hashlib
import sys

def main():
    if len(sys.argv) != 2:
        print(__doc__, file=sys.stderr)
        sys.exit(1)
    
    content = sys.argv[1]
    # Calculate SHA256 hash and take first 8 hex characters
    hash_8char = hashlib.sha256(content.encode()).hexdigest()[:8]
    # Output the agreed marker format
    print(f"[[QUEUE_ANSWERED:{hash_8char}]]")

if __name__ == "__main__":
    main()
