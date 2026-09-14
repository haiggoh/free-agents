# Add remote API keys

Run `csl setup-remote`, or choose **i — install / set up remote API keys** in `csl`.
The installer also runs directly: `python3 install/setup-api-keys.py`.
Python 3.9+ on macOS/Linux is sufficient; no additional packages are required.

1. Select provider numbers or names, separated by commas. `missing` selects
   missing credentials from the first eleven rows. Optional trial/credit and
   regional accounts are selected individually.
2. Follow the short signup/key instructions. Press `o` to open the official page,
   or Enter if you already have it open.
3. Paste the raw key into the hidden prompt. Nothing appears while typing/pasting.
   Enter with no key skips that provider; Ctrl-C ends setup.

You can also start with `csl setup-remote mistral siliconflow`, or inspect status
without changing anything using `csl setup-remote --list`.

Files are saved under `~/.api_keys` (underscore), one bare value per file.
`--keys-dir PATH` or `LA_API_KEYS_DIR` selects another directory. Its parent must
already exist. A new store uses mode 700, and new files use mode 600.
The installer refuses symlinked or nonprivate directories, and never overwrites
existing files, including empty files. It does not change their permissions.
Partially configured Cloudflare accounts prompt only for the missing field.
Both missing fields are collected before saving; each file is published atomically
without replacement. A disk failure between writes can leave a partial account;
the next run displays that state and offers the missing field.

**Saved means stored, not authenticated or tested.** No validation/inference call,
session launch, shell export, or shell configuration change happens during setup.
Browser pages open only when requested. New provider session integrations,
credential rotation/removal, validation and expiry metadata are separate work.
Existing session integrations still require their normal account/model checks.
Account quotas and billing apply; the list does not guarantee free access.

## Provider key pages and filenames

Provider | Key page | Filename
--- | --- | ---
Google Gemini | [AI Studio](https://aistudio.google.com/apikey) | `gemini`
Groq | [API Keys](https://console.groq.com/keys) | `groq`
OpenRouter | [Keys](https://openrouter.ai/settings/keys) | `openrouter`
Cloudflare | [API Tokens](https://dash.cloudflare.com/profile/api-tokens) | `cloudflare`, `cloudflare-account-id`
GitHub Models | [Fine-grained token](https://github.com/settings/personal-access-tokens/new) | `github-models`
Mistral | [Studio profile API Keys](https://console.mistral.ai/home?profile_dialog=api-keys) | `mistral`
Z.AI | [API Keys](https://z.ai/manage-apikey/apikey-list) | `zai`
SiliconFlow | [API Keys](https://cloud.siliconflow.com/account/ak) | `siliconflow`
LLM7 | [Dashboard](https://dash.llm7.io) | `llm7`
Kilo | [Personal account, Your Profile](https://app.kilo.ai) | `kilo`
Vercel | [AI Gateway API Keys](https://vercel.com/d?title=AI+Gateway+API+Keys&to=%2F%5Bteam%5D%2F~%2Fai-gateway%2Fapi-keys) | `vercel`
SambaNova | [Dashboard, API Keys](https://cloud.sambanova.ai/dashboard) | `sambanova`
ModelScope | [Access token](https://modelscope.cn/my/myaccesstoken) | `modelscope`
Cerebras | [Platform, API Keys](https://cloud.cerebras.ai/platform/) | `cerebras`
NVIDIA | [API Keys](https://build.nvidia.com/settings/api-keys) | `nvidia`

Provider guidance checked on 2026-09-14 against official documentation:
[Mistral](https://docs.mistral.ai/admin/identity-access/api-keys),
[Z.AI](https://docs.z.ai/api-reference/introduction),
[SiliconFlow](https://docs.siliconflow.com/en/userguide/quickstart),
[LLM7](https://docs.llm7.io/quickstart),
[Kilo](https://kilo.ai/docs/getting-started/setup-authentication),
[Vercel](https://vercel.com/docs/ai-gateway/authentication-and-byok),
[SambaNova](https://docs.sambanova.ai/docs/en/get-started/quickstart),
[ModelScope](https://community.modelscope.cn/675262372db35d1195183bdb.html).
Login, verification requirements and page layouts may change.

## Offline verification

`python3 tests/test_setup_api_keys.py` covers fake-HOME storage, permissions,
non-overwrite behavior, partial Cloudflare setup, cancellation, invalid input,
symlink/owner checks, CLI help and a real pseudo-terminal hidden-paste flow.
All test credentials are synthetic. No provider requests are made.
