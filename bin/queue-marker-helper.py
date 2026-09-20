#!/usr/bin/env python3
"""
queue-marker-helper.py — Deterministic helper for generating QUEUE_ANSWERED markers.

USAGE:
    queue-marker-helper.py "<queued prompt content>"

EXAMPLE:
    $ queue-marker-helper.py "/run-to-completion"
    [[QUEUE_ANSWERED:ae414ae0]]

DESCRIPTION:
    This helper prints the marker that satisfies local-queue-stop-hook.py, so an
    already-addressed queued prompt is not re-notified.

    The stop hook considers a queued prompt addressed when the assistant's response
    contains a marker of the form:
        [[QUEUE_ANSWERED:<8-char-hex>]]

    The hash, the marker syntax and the pattern that parses it are NOT defined here.
    They live in bin/queue_marker.py, which this script and the stop hook both import —
    previously each file restated the contract independently, so a change on one side
    could silently produce markers the other side rejects.

OUTPUT:
    Prints exactly: [[QUEUE_ANSWERED:<8-char-hex>]]

EXIT CODES:
    0  - Success
    1  - Usage error (wrong number of arguments)
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import queue_marker  # noqa: E402


def main():
    args = [a for a in sys.argv[1:]]
    if len(args) == 1 and args[0] in ("-h", "--help"):
        print(__doc__)
        sys.exit(0)
    if len(args) != 1:
        print(__doc__, file=sys.stderr)
        sys.exit(1)

    print(queue_marker.format_marker(args[0]))


if __name__ == "__main__":
    main()
