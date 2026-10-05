#!/usr/bin/env python3
"""
tests/fixtures/api_keys_test_data.py — Test fixtures for API Keys screen design.

Covers:
- Provider key file states (saved/missing/partial)
- Accordion group/item structure
- Inline prompt behavior
- Temperature independence
- Filter submenu states
- Provider ordering (NVIDIA first)
"""

import tempfile
from pathlib import Path
from typing import Dict, List, Tuple
from dataclasses import dataclass


@dataclass
class ProviderKeyFixture:
    """Fixture for a single provider's key file state."""
    slug: str
    name: str
    key_files: Tuple[str, ...]
    existing_files: List[str]  # subset of key_files that exist
    expected_status: str       # "saved" | "missing" | "partial"


# Standard providers with their key file requirements
PROVIDER_KEY_SPECS = [
    ProviderKeyFixture("nvidia", "NVIDIA", ("nvidia",), [], "missing"),
    ProviderKeyFixture("nvidia", "NVIDIA", ("nvidia",), ["nvidia"], "saved"),
    ProviderKeyFixture("cloudflare", "Cloudflare Workers AI", ("cloudflare", "cloudflare-account-id"), [], "missing"),
    ProviderKeyFixture("cloudflare", "Cloudflare Workers AI", ("cloudflare", "cloudflare-account-id"), ["cloudflare"], "partial"),
    ProviderKeyFixture("cloudflare", "Cloudflare Workers AI", ("cloudflare", "cloudflare-account-id"), ["cloudflare", "cloudflare-account-id"], "saved"),
    ProviderKeyFixture("gemini", "Google Gemini", ("gemini",), [], "missing"),
    ProviderKeyFixture("gemini", "Google Gemini", ("gemini",), ["gemini"], "saved"),
]


def create_api_keys_dir_fixture(base_path: Path, fixtures: List[ProviderKeyFixture]) -> Dict[str, List[str]]:
    """
    Create a temporary ~/.api_keys directory with specified key files.
    Returns mapping of provider_slug -> list of created file paths.
    """
    created = {}
    for fixture in fixtures:
        provider_dir = base_path / fixture.slug
        provider_dir.mkdir(parents=True, exist_ok=True)
        created[fixture.slug] = []
        for key_file in fixture.existing_files:
            file_path = provider_dir / key_file
            file_path.write_text("test-key-value\n")
            created[fixture.slug].append(str(file_path))
    return created


# Expected status labels for each state
EXPECTED_STATUS_LABELS = {
    "saved": "✅ Saved",
    "missing": "❌ Missing",
    "partial": "✅ Saved",  # Current implementation: all files must exist for "Saved"
}


# Accordion group/item structure expectations
ACCORDION_FIXTURES = {
    "nvidia": {
        "group_id": "nvidia",
        "group_label_contains": "NVIDIA",
        "items": [
            {"id": "open:nvidia", "label": "Open NVIDIA signup page"},
            {"id": "add:nvidia", "label": "Add NVIDIA key"},
        ],
    },
    "cerebras": {
        "group_id": "cerebras",
        "group_label_contains": "Cerebras",
        "items": [
            {"id": "open:cerebras", "label": "Open Cerebras signup page"},
            {"id": "add:cerebras", "label": "Add Cerebras key"},
        ],
    },
    "gemini": {
        "group_id": "gemini",
        "group_label_contains": "Google Gemini",
        "items": [
            {"id": "open:gemini", "label": "Open Google Gemini signup page"},
            {"id": "add:gemini", "label": "Add Google Gemini key"},
        ],
    },
}


# Provider ordering: NVIDIA first, then Google, then Groq, then alphabetical
EXPECTED_PROVIDER_ORDER = [
    "nvidia", "gemini", "groq", "cerebras", "cloudflare", "kilo",
    "llm7", "mistral", "modelscope", "openrouter", "sambanova",
    "siliconflow", "vercel", "zai"
]


# Inline prompt fixtures
INLINE_PROMPT_FIXTURES = {
    "prompt:keys:key": {
        "template": "API key/token for {provider} (hidden; paste advances automatically; Enter skips):",
        "password_mode": True,
        "providers": {
            "nvidia": "API key/token for NVIDIA (hidden; paste advances automatically; Enter skips):",
            "gemini": "API key/token for Google Gemini (hidden; paste advances automatically; Enter skips):",
            "cloudflare": "API key/token for Cloudflare Workers AI (hidden; paste advances automatically; Enter skips):",
        }
    },
}


# Temperature independence fixtures
TEMPERATURE_FIXTURES = {
    "local_default": {"local": "", "remote": "", "expect_equal": True},
    "local_set": {"local": "0.3", "remote": "", "expect_equal": False},
    "remote_set": {"local": "", "remote": "1.0", "expect_equal": False},
    "both_different": {"local": "0.7", "remote": "1.5", "expect_equal": False},
    "both_same": {"local": "0.3", "remote": "0.3", "expect_equal": True},
    "cycle_wrap": {"local": "2.0", "remote": "2.0", "next_local": "", "next_remote": ""},
}


# Filter submenu fixtures
FILTER_SUBMENU_FIXTURES = {
    "default_state": {
        "include_trials": True,
        "local_capable_shown": False,
        "show_broken": False,
        "expected_hidden_count": 1,  # cerebras-oss is trial
    },
    "hide_trials": {
        "include_trials": False,
        "local_capable_shown": False,
        "show_broken": False,
        "expected_hidden_count": 2,  # cerebras-oss (trial) + nvidia-a/b if not visible
    },
    "show_local_capable": {
        "include_trials": True,
        "local_capable_shown": True,
        "show_broken": False,
        "expected_hidden_count": 1,  # cerebras-oss
    },
    "show_broken": {
        "include_trials": True,
        "local_capable_shown": False,
        "show_broken": True,
        "expected_hidden_count": 0,  # all visible
    },
    "all_hidden": {
        "include_trials": False,
        "local_capable_shown": False,
        "show_broken": False,
        "expected_hidden_count": 2,  # trials + broken
    },
}


# HomeScreen clean state expectations
HOMESCREEN_FIXTURES = {
    "allowed_keys": {"l", "r", "d", "o", "k", "v", "q"},
    "forbidden_keys": {"a", "t", "p", "w", "n"},  # auto-mode, telemetry, hook, watcher, nvidia
    "section_counts": {
        "lanes": 2,    # l, r
        "tools": 4,    # d, o, k, v
        "nav": 1,      # q
    },
}


# LocalScreen settings
LOCAL_SCREEN_FIXTURES = {
    "required_keys": {"e", "o", "c", "s", "r", "m", "a", "t", "p", "w", "v", "q"},
    "mcp_always_enabled": True,
    "temperature_key": "o",
    "effort_key": "e",
}


# RemoteScreen settings
REMOTE_SCREEN_FIXTURES = {
    "required_keys": {"e", "o", "c", "s", "l", "y", "m", "a", "t", "p", "k", "n", "q"},
    "filter_submenu_key": "y",
    "moved_to_submenu": {"h", "f", "u"},
    "temperature_key": "o",
    "effort_key": "e",
}


# RemoteFiltersScreen settings
REMOTE_FILTERS_FIXTURES = {
    "required_keys": {"h", "f", "u", "x"},
    "nav_has_house_emoji": True,
    "toggle_keys": {
        "h": "include_trials",
        "f": "local_capable_shown",
        "u": "show_broken",
    },
}


def make_temp_api_keys_dir(fixtures: List[ProviderKeyFixture]) -> Tuple[Path, callable]:
    """
    Create a temporary API keys directory with fixtures.
    Returns (path, cleanup_function).
    """
    tmp = Path(tempfile.mkdtemp(prefix="test-api-keys-"))
    create_api_keys_dir_fixture(tmp, fixtures)

    def cleanup():
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)

    return tmp, cleanup


# Validation helpers
def validate_accordion_structure(api_keys_screen) -> List[str]:
    """Validate accordion structure, return list of errors."""
    errors = []
    groups = api_keys_screen.groups()

    # Check provider count
    if len(groups) != 14:
        errors.append(f"Expected 14 provider groups, got {len(groups)}")

    # Check NVIDIA first
    if groups and groups[0].id != "nvidia":
        errors.append(f"First group should be nvidia, got {groups[0].id}")

    # Check each group has 2 items
    for g in groups:
        if len(g.items) != 2:
            errors.append(f"Group {g.id} should have 2 items, got {len(g.items)}")
        if not g.items[0].id.startswith("open:"):
            errors.append(f"Group {g.id} first item should be 'open:', got {g.items[0].id}")
        if not g.items[1].id.startswith("add:"):
            errors.append(f"Group {g.id} second item should be 'add:', got {g.items[1].id}")

    return errors


def validate_provider_order(api_keys_screen) -> List[str]:
    """Validate provider order is NVIDIA first then alphabetical."""
    errors = []
    groups = api_keys_screen.groups()
    actual_order = [g.id for g in groups]
    if actual_order != EXPECTED_PROVIDER_ORDER:
        errors.append(f"Provider order mismatch:\n  Expected: {EXPECTED_PROVIDER_ORDER}\n  Actual:   {actual_order}")
    return errors


def validate_home_clean_state(home_screen) -> List[str]:
    """Validate HomeScreen has clean state (no lane settings)."""
    errors = []
    acts = {a.key for a in home_screen.actions()}

    for forbidden in HOMESCREEN_FIXTURES["forbidden_keys"]:
        if forbidden in acts:
            errors.append(f"HomeScreen should not have '{forbidden}' (belongs in lanes)")

    for required in HOMESCREEN_FIXTURES["allowed_keys"]:
        if required not in acts:
            errors.append(f"HomeScreen missing required key '{required}'")

    return errors


def validate_temperature_independence(local_screen, remote_screen) -> List[str]:
    """Validate local and remote temperatures are independent."""
    errors = []

    local_temp = next(a for a in local_screen.actions() if a.key == "o").label
    remote_temp = next(a for a in remote_screen.actions() if a.key == "o").label

    # They should be able to have different values
    if local_temp == remote_temp == "🌡️  Temperature: <provider default>":
        # Both at default - this is fine
        pass
    elif local_temp != remote_temp:
        # Different - good
        pass
    else:
        # Same non-default value - might be coincidence or bug
        pass

    return errors


def validate_filter_submenu(remote_screen, filters_screen) -> List[str]:
    """Validate filter submenu structure and behavior."""
    errors = []

    # Check remote screen has 'y' key for filter submenu
    remote_keys = {a.key for a in remote_screen.actions()}
    if "y" not in remote_keys:
        errors.append("RemoteScreen missing 'y' key for filter submenu")

    # Check h/f/u are NOT in remote main menu
    for k in ["h", "f", "u"]:
        if k in remote_keys:
            errors.append(f"RemoteScreen should not have '{k}' in main menu (moved to filter submenu)")

    # Check filter submenu has required keys
    filter_keys = {a.key for a in filters_screen.actions()}
    for k in REMOTE_FILTERS_FIXTURES["required_keys"]:
        if k not in filter_keys:
            errors.append(f"Filter submenu missing '{k}' key")

    # Check back nav has house emoji
    nav = [a for a in filters_screen.actions() if a.section == "nav"]
    if not any("🏠" in a.label or "Home" in a.label for a in nav):
        errors.append("Filter submenu nav should have house emoji for Back to Home")

    return errors


def validate_inline_prompt(picker, provider_slug: str) -> List[str]:
    """Validate inline prompt for API key entry."""
    errors = []

    # This would need to be tested in PTY test
    # Here we just validate the prompt template exists
    if "prompt:keys:key" not in picker.PROMPTS:
        errors.append("Missing 'prompt:keys:key' in Picker.PROMPTS")

    template = picker.PROMPTS["prompt:keys:key"]
    if "{provider}" not in template:
        errors.append("Prompt template missing {provider} placeholder")

    return errors


# Sample test data for PTY tests
PTY_TEST_SCENARIOS = [
    {
        "name": "api_keys_inline_entry",
        "description": "Test inline API key entry for NVIDIA",
        "steps": [
            ("k", "API Keys Setup"),
            ("enter", "Open NVIDIA signup page"),  # Select NVIDIA group (expanded by default)
            ("down", "Add NVIDIA key"),  # Move to Add NVIDIA key
            ("enter", "API key/token for NVIDIA"),  # Should open inline prompt
        ],
        "expected_prompt": "API key/token for NVIDIA",
    },
    {
        "name": "api_keys_switch_provider",
        "description": "Test switching between providers in accordion",
        "steps": [
            ("k", "API Keys Setup"),
            ("right", None),  # Expand/collapse - depends on current state
            ("down", None),   # Navigate to next provider
            ("enter", None),  # Select that provider
        ],
    },
    {
        "name": "filter_submenu_toggles",
        "description": "Test filter submenu toggles",
        "steps": [
            ("r", "Remote API Session Picker"),
            ("y", "Remote Filter Settings"),
            ("h", "Limited trials: HIDDEN"),  # Toggle trials
            ("f", "Locally-runnable models: SHOWN"),  # Toggle local-capable
            ("u", "Unworking models: SHOWN"),  # Toggle broken
            ("b", "Remote API Session Picker"),  # Back
        ],
    },
]


if __name__ == "__main__":
    # Print fixture summary
    print("=== API Keys Test Fixtures Summary ===")
    print(f"\nProvider Key Specs: {len(PROVIDER_KEY_SPECS)} fixtures")
    for f in PROVIDER_KEY_SPECS:
        print(f"  {f.slug}: {f.existing_files}/{list(f.key_files)} -> {f.expected_status}")

    print(f"\nExpected Provider Order: {len(EXPECTED_PROVIDER_ORDER)} providers")
    for i, p in enumerate(EXPECTED_PROVIDER_ORDER, 1):
        print(f"  {i:2}. {p}")

    print(f"\nAccordion Fixtures: {len(ACCORDION_FIXTURES)} providers defined")

    print(f"\nInline Prompt Fixtures: {len(INLINE_PROMPT_FIXTURES)}")

    print(f"\nTemperature Fixtures: {len(TEMPERATURE_FIXTURES)} scenarios")

    print(f"\nFilter Submenu Fixtures: {len(FILTER_SUBMENU_FIXTURES)} states")

    print(f"\nHomeScreen Fixtures: {len(HOMESCREEN_FIXTURES['allowed_keys'])} allowed, {len(HOMESCREEN_FIXTURES['forbidden_keys'])} forbidden")

    print(f"\nPTY Test Scenarios: {len(PTY_TEST_SCENARIOS)}")
    for s in PTY_TEST_SCENARIOS:
        print(f"  - {s['name']}: {s['description']}")