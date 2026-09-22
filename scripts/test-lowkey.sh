#!/usr/bin/env bash
# test-lowkey.sh — Reproducible automated test command for lowkey CLI
#
# Runs the complete test suite without requiring a live model server.
# All tests mock hotswap/librarian boundaries.
#
# Usage: ./scripts/test-lowkey.sh
#        ./scripts/test-lowkey.sh --verbose
#        ./scripts/test-lowkey.sh --unit-only
#
# Exit codes: 0 = all pass, 1 = any failure, 2 = usage error

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
TEST_DIR="${REPO_ROOT}/tests"

# Colors for output
if [ -t 1 ]; then
    GREEN='\033[0;32m'
    RED='\033[0;31m'
    YELLOW='\033[1;33m'
    BLUE='\033[0;34m'
    NC='\033[0m'  # No Color
else
    GREEN=''
    RED=''
    YELLOW=''
    BLUE=''
    NC=''
fi

VERBOSE=false
UNIT_ONLY=false

while [ $# -gt 0 ]; do
    case "$1" in
        -v|--verbose) VERBOSE=true; shift ;;
        --unit-only) UNIT_ONLY=true; shift ;;
        -h|--help)
            cat <<EOF
Usage: $0 [options]

Options:
    -v, --verbose    Show individual test output
    --unit-only      Run only unit tests (skip stateful tests)
    -h, --help       Show this help

Runs all lowkey test suites:
  1. test_local_agent_dispatch.py      - Pure helper functions (47 tests)
  2. test_local_agent_dispatch_state.py - Stateful: sessions, files, paste (45 tests)
  3. test_lowkey_cli_dispatch.py       - Dispatch/conversation logic mocked (48 tests)

Total: 140 tests, no live model required.
EOF
            exit 0
            ;;
        *) echo "Unknown option: $1" >&2; exit 2 ;;
    esac
done

run_test() {
    local name="$1"
    local script="$2"
    local start_time end_time duration

    printf "${BLUE}== %s ==${NC}\n" "$name"
    start_time=$(date +%s)

    if [ "$VERBOSE" = true ]; then
        python3 "$script"
    else
        python3 "$script" 2>&1 | tail -3
    fi

    local exit_code=${PIPESTATUS[0]}
    end_time=$(date +%s)
    duration=$((end_time - start_time))

    if [ $exit_code -eq 0 ]; then
        printf "${GREEN}✓ %s passed (%ds)${NC}\n\n" "$name" "$duration"
    else
        printf "${RED}✗ %s FAILED (%ds)${NC}\n\n" "$name" "$duration"
    fi
    return $exit_code
}

main() {
    echo
    printf "${BLUE}╔══════════════════════════════════════════════════════════╗${NC}\n"
    printf "${BLUE}║  lowkey CLI Test Suite — Reproducible Verification        ║${NC}\n"
    printf "${BLUE}╚══════════════════════════════════════════════════════════╝${NC}\n"
    echo

    local overall=0

    # Test 1: Pure helpers
    run_test "Pure Helper Tests (47)" "${TEST_DIR}/test_local_agent_dispatch.py" || overall=1

    # Test 2: Stateful (sessions, files, paste)
    if [ "$UNIT_ONLY" = false ]; then
        run_test "Stateful Tests (45)" "${TEST_DIR}/test_local_agent_dispatch_state.py" || overall=1
    else
        echo "Skipping stateful tests (--unit-only)"
        echo
    fi

    # Test 3: Dispatch/conversation mocked
    run_test "Dispatch/Conversation Mocked Tests (48)" "${TEST_DIR}/test_lowkey_cli_dispatch.py" || overall=1

    echo
    printf "${BLUE}╔══════════════════════════════════════════════════════════╗${NC}\n"
    if [ $overall -eq 0 ]; then
        printf "${BLUE}║  ${GREEN}ALL TESTS PASSED${BLUE}                                        ║${NC}\n"
    else
        printf "${BLUE}║  ${RED}SOME TESTS FAILED${BLUE}                                       ║${NC}\n"
    fi
    printf "${BLUE}╚══════════════════════════════════════════════════════════╝${NC}\n"
    echo

    exit $overall
}

main "$@"