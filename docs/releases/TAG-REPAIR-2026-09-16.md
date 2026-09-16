# Tag repair — 2026-09-16

Pre-change snapshot of every tag involved, captured BEFORE any deletion or retag.
Purpose: deleting a tag destroys its annotated message and its object. This file plus the
`refs/backup/` namespace below make the operation reversible and auditable.

## Local tags in the affected range

| Tag | Tag-object sha | Commit sha | Commit subject | Type |
|---|---|---|---|---|
| `v0.13.11` | `7684a0d086e92412028572cebb008f485582c559` | `472a7ed9649eb63b0c835d9e1b64f2a68c97a50b` | release: 0.13.11 | tag |
| `v0.13.12` | `310bca2243344db213bf8c00330a36db29a0ff05` | `f18b86bcb627454a88f4b49cbe57464d1fb7a812` | docs: sync README, CHANGELOG, and plugin manifest for 0.13.12 | tag |
| `v0.13.13` | `9a98d5819747a0936bfc3d964927f405bc5d5aec` | `9a98d5819747a0936bfc3d964927f405bc5d5aec` | Merge feat: plugin-scoped stop hook for queued prompts | commit |
| `v0.13.14` | `f4cf2bf35a5587316372700cc28fc74061858a7a` | `9a98d5819747a0936bfc3d964927f405bc5d5aec` | Merge feat: plugin-scoped stop hook for queued prompts | tag |

## Remote tags (origin) in the affected range

| Tag | Sha on origin |
|---|---|
| `v0.13.11` | `7684a0d086e92412028572cebb008f485582c559` |
| `v0.13.11^{}` | `472a7ed9649eb63b0c835d9e1b64f2a68c97a50b` |
| `v0.13.12` | `310bca2243344db213bf8c00330a36db29a0ff05` |
| `v0.13.12^{}` | `f18b86bcb627454a88f4b49cbe57464d1fb7a812` |
| `v0.13.13` | `e48ee30582bdb7a9e5044f02c5dc5078cd111b82` |
| `v0.13.13^{}` | `a460e8eb1b706f32716370bf336263d158d35884` |
| `v0.14.0` | `40b8646927a7064000bbf6a5ffe72ddbe94fe5dd` |
| `v0.14.0^{}` | `5efb63b96e77f1e4a0f7cb0e2d28d8d0cb901897` |

## Annotated-tag messages (destroyed by deletion — preserved here)

### `v0.13.12`
```
2026-09-15 03:10:42 +0200
Release 0.13.12: blind-trust auto mode, remote session lane, la-reboot.sh

```

### `v0.13.14`
```
2026-09-15 19:41:22 +0200
Release v0.13.14 - contains stop hook merge commit 9a98d58

```

### `_remote_v0.14.0`
```
2026-09-15 08:57:00 +0200
Release v0.14.0: Enhance remote-session.sh to match csl interactive capabilities

```


### `v0.13.13` (as published on origin — NOT the same object as the local tag)

```
2026-09-15 09:54:09 +0200
Release 0.13.13: CSL picker state synchronization for remote sessions
```

## ⚠️ Divergence: local vs published `v0.13.13`

These are two DIFFERENT tags sharing one name — the single most dangerous fact in this repair:

| | Type | Points at | Commit subject |
|---|---|---|---|
| **local** `v0.13.13` | lightweight (commit) | `9a98d58` | Merge feat: plugin-scoped stop hook |
| **origin** `v0.13.13` | annotated | `a460e8e` | csl: add picker state synchronization |

A local `git tag -d v0.13.13` followed by a naive `git push --tags` would therefore have MOVED the
published tag from `a460e8e` to `9a98d58` silently — the failure mode this record exists to prevent.
The published object is preserved at `refs/backup/origin-v0.13.13`.

## Backup refs created (recovery source)

| Ref | Object | Type |
|---|---|---|
| `refs/backup/local-v0.13.13` | `9a98d5819747a0936bfc3d964927f405bc5d5aec` | commit |
| `refs/backup/local-v0.13.14` | `f4cf2bf35a5587316372700cc28fc74061858a7a` | tag |
| `refs/backup/origin-v0.13.13` | `e48ee30582bdb7a9e5044f02c5dc5078cd111b82` | tag |
| `refs/backup/origin-v0.14.0` | `40b8646927a7064000bbf6a5ffe72ddbe94fe5dd` | tag |

Recover any of them with:
`git update-ref refs/tags/<name> $(git rev-parse refs/backup/<backup-name>)`

Note `refs/backup/*` is a non-standard namespace and is NOT pushed or fetched by default, so these
survive locally regardless of what happens to `refs/tags/*` on origin.
