# Release Process

This document describes how Health Assistant releases and release candidates
are cut, versioned, and tracked. The goal is a single, low-ceremony workflow
where the *scope of the next release* is captured **as work lands**, not
assembled retroactively by diffing branches.

## Principles

1. **`CHANGELOG.md` is the single source of truth for release scope.** No
   separate scope file, no `dev/`-side drafts. The `## [Unreleased]` section
   *is* the live scope of the next release.
2. **Write changelog entries at commit time**, not at release time. Every
   PR/commit that changes user-visible behavior adds one line under
   `## [Unreleased]` in the same commit.
3. **Git tags + `git log` are the source of truth for what changed between
   releases.** Never store diff manifests manually — use
   `git log vA..vB --oneline` / `git diff vA..vB --stat`.
4. **`backend/app/core/config.py` (`VERSION: str`) is the single version source.**
   The `scripts/version_manager.py` CLI propagates that string to all
   user-facing surfaces (README badge, `package.json`, `package-lock.json`,
   `docs/INSTALL.md`, `AboutPage.tsx`). The version is **not** set via `.env` —
   `.env` holds deployment configuration, not code version.

## Changelog format

`CHANGELOG.md` follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Group every entry under one of these headings inside `## [Unreleased]`:

| Heading     | When to use it                                                       |
| ----------- | --------------------------------------------------------------------- |
| `Added`     | New features, endpoints, UI, config options.                          |
| `Changed`   | Changes in existing functionality, refactors with user impact.        |
| `Deprecated`| Features scheduled for removal (announce the sunset).               |
| `Removed`   | Deleted features, dead code, dropped endpoints.                     |
| `Fixed`     | Bug fixes, crash fixes, correctness fixes.                            |
| `Security`  | Vulnerability fixes, hardening, auth/authz tightening.               |

Optional sub-headings (e.g. `### DB foundation`, `### P0 stabilization pass`)
are fine for grouping a large batch — keep them under the top-level section
headings so the structure stays scannable.

### Entry style

- One bullet per change. Reference the audit item / issue / PR when useful
  (e.g. `(audit B7)`, `(#42)`).
- Past tense, imperative mood: "**Fixed** `list_observations` filter...",
  not "Fixes..." or "This fixes...".
- Call out breaking changes explicitly with a **Breaking changes** note at
  the top of the release section.
- If the change needs operators to act at deploy time, add a numbered step
  under an `### Operational notes for deploy` sub-section.

## Versioning

[Semantic Versioning 2.0.0](https://semver.org/spec/v2.0.0.html):

- `MAJOR` — incompatible API / data-model changes.
- `MINOR` — new features, backward-compatible.
- `PATCH` — bug fixes only, backward-compatible.
- Suffix `-rc.N` — release candidate (`0.3.0-rc.1`, `0.3.0-rc.2`, ...).
  Promoting an rc to the same version with no suffix is the final release.

The version string currently lives in `backend/app/core/config.py` as
`VERSION: str = "X.Y.Z[-suffix]"`. It is read by FastAPI (`app.main`) and the
FHIR R4 `CapabilityStatement` (`fhir_facade_service.py`). The version is **not**
sourced from `.env` — operators should not override the code version per
deployment.

## Cutting a release candidate

```bash
# 1. Review what's accumulated under ## [Unreleased]
git log v0.3.0-rc.1..HEAD --oneline       # commits since last tag
git diff v0.3.0-rc.1..HEAD --stat         # files touched

# 2. In CHANGELOG.md: rename ## [Unreleased] → ## [vX.Y.Z-rc.N] - YYYY-MM-DD
#    and insert a fresh ## [Unreleased] block above it.

# 3. Bump the version string everywhere via the manager (commit + tag + push)
python3 scripts/version_manager.py bump rc --git --push
#    (or set explicitly:)  python3 scripts/version_manager.py set X.Y.Z-rc.N --git --push
```

`version_manager.py` updates: `backend/app/core/config.py`,
`frontend/package.json`, `frontend/package-lock.json`, `docs/INSTALL.md`,
`README.md` badge, `frontend/src/pages/About/AboutPage.tsx`, `CHANGELOG.md`,
`docs/RELEASE_PROCESS.md`. It then commits
(`chore(release): bump version to X.Y.Z-rc.N`) and creates an annotated git
tag `vX.Y.Z-rc.N`. With `--push`, the commit + tag are pushed to **every**
configured remote (see [GitHub Release automation](#github-release-automation)
below for what happens next).

## Promoting a release candidate to a final release

```bash
# 1. In CHANGELOG.md: rename ## [vX.Y.Z-rc.N] → ## [vX.Y.Z] - YYYY-MM-DD
#    (drop the -rc.N suffix). Leave the body intact.

# 2. Bump patch → promotes rc to final (strips the -rc.N suffix)
python3 scripts/version_manager.py bump patch --git --push
```

## Catch-up: the `release` subcommand

If you ran `set`/`bump` **without** `--git --push`, or edited `CHANGELOG.md`
after the version bump, run:

```bash
python3 scripts/version_manager.py release --git --push
```

This reads the version already recorded in `config.py`, stages the version
files + release docs, commits, tags `vX.Y.Z`, and pushes to every remote —
catching up to a fully released state without re-bumping the version.

## GitHub Release automation

Pushing a `v*` tag triggers two GitHub workflows (in
`.github/workflows/`):

1. **`docker-publish.yml`** — builds and publishes backend + frontend Docker
   images to `ghcr.io/health-assistant-io/health-assistant`. Tags follow
   semver (`X.Y.Z`, `X.Y`, `latest` on main).
2. **`release.yml`** — creates a **GitHub Release** attached to the tag with:
   - **Release notes** auto-extracted from the matching `## [vX.Y.Z]` section
     in `CHANGELOG.md`.
   - **Prerelease flag** set automatically when the version suffix matches
     `-rc.*`, `-beta`, `-alpha`, `-pre`, or `-dev`. Final releases (no
     suffix) are published as full releases.

So the complete release flow is: edit `CHANGELOG.md` → run
`version_manager.py ... --git --push` → both workflows fire → Docker images
land on GHCR + GitHub Release appears with notes, marked as prerelease for
RCs. **No manual GitHub UI action needed.**

The release workflow can also be triggered manually from the GitHub Actions
UI (`workflow_dispatch`) with a tag name input — useful for backfilling a
release for an older tag that predates the workflow.

## Diffing between releases

Never store "files changed between releases" manually — git already tracks it:

```bash
git log v0.2.0..v0.3.0-rc.1 --oneline          # commits between two tags
git diff v0.2.0..v0.3.0-rc.1 --stat             # files changed, +/- counts
git diff v0.2.0..v0.3.0-rc.1 -- backend/app     # scope to a subtree
git log --stat v0.2.0..v0.3.0-rc.1              # per-commit file lists
```

The CHANGELOG narrative + these git commands together give the full picture.

## What `dev/` is for

`dev/` is gitignored (`.gitignore:1`) and holds **ephemeral, personal working
state only**:

- Audit drafts (`dev/audits/AUDIT-YYYY-MM-DD.md`)
- Plan drafts (`dev/plans/*.md`)
- Scratch notes (`dev/notes-*`, `dev/last-question.md`)
- Sample files, ad-hoc fix scripts

**Release scope never lives in `dev/`.** If you draft release notes there
while working, promote the text into `CHANGELOG.md`'s `## [Unreleased]`
section before the commit lands. Anything left in `dev/` is invisible to git
history, is lost across machines, and cannot be diffed against tags.

## Quick checklist (per commit/PR with user-visible impact)

- [ ] Added a bullet under `## [Unreleased]` → correct heading in `CHANGELOG.md`.
- [ ] Referenced audit item / issue if applicable.
- [ ] Breaking change flagged with a **Breaking changes** note.
- [ ] Deploy-time action listed under `### Operational notes for deploy`.

## Quick checklist (cutting a release)

- [ ] Reviewed `git log <last-tag>..HEAD --oneline` against `## [Unreleased]`.
- [ ] Renamed `## [Unreleased]` → `## [vX.Y.Z(-rc.N)?] - YYYY-MM-DD`.
- [ ] Added a fresh `## [Unreleased]` block above it.
- [ ] Ran `python3 scripts/version_manager.py bump {rc|patch|minor|major} --git --push`.
- [ ] Confirmed the new tag exists: `git tag --list 'vX.Y.Z*'`.
- [ ] (If you forgot `--git --push`) Ran `python3 scripts/version_manager.py release --git --push`.
- [ ] Confirmed the GitHub Release appeared (Actions tab → "Create GitHub Release" workflow).
- [ ] Confirmed Docker images published (Packages tab on GitHub).
