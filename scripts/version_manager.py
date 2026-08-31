#!/usr/bin/env python3
r"""Neuronection family version manager — canonical, config-driven.

One implementation for all family repos. All repo-specific facts live in
``version_manager.toml`` at the repo root (family config example is
distributed internally with the standards):

    [project]
    name = "App Name"

    [version]
    file = "backend/app/__init__.py"
    pattern = '__version__ = "(?P<version>[^"]+)"'

    [[propagate]]
    name = "Frontend package.json"
    file = "frontend/package.json"
    type = "json"                # json | npm-lock | regex
    field = "version"

    [[propagate]]
    name = "README badge"
    file = "README.md"
    type = "regex"
    pattern = '(?P<version>[^-\)]+)'
    dash_escape = true           # shields.io: "-" becomes "--"

    [release]
    docs = ["CHANGELOG.md"]      # staged together with version files
    tag_prefix = "v"

Contract for regex types: the pattern must contain exactly one named group
``version``; the script splices the new version into that span (context is
preserved automatically).

Commands:
    show                     print the current version
    set X.Y.Z[-suffix]       set an explicit version (--git/--push optional)
    bump major|minor|patch|rc
    release                  commit/tag/push the version already on disk

Flags for set/bump/release:
    --git, -g   stage version files + release docs, commit, create tag
    --push, -p  implies --git; push branch + tag to EVERY remote (opt-in;
                the default keeps the release local)
    --dry       print what would change without writing (set/bump only)
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover
    sys.exit("version_manager requires Python 3.11+ (tomllib)")

ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "version_manager.toml"

SEMVER_RE = re.compile(r"^(\d+)\.(\d+)\.(\d+)(?:-([a-zA-Z0-9.]+))?$")


def load_config() -> dict:
    if not CONFIG_PATH.is_file():
        sys.exit(f"Error: config not found at {CONFIG_PATH}")
    with open(CONFIG_PATH, "rb") as fh:
        cfg = tomllib.load(fh)
    for key in ("project", "version"):
        if key not in cfg:
            sys.exit(f"Error: [{key}] section missing in {CONFIG_PATH}")
    if "file" not in cfg["version"] or "pattern" not in cfg["version"]:
        sys.exit("Error: [version] needs 'file' and 'pattern'")
    if "version" not in cfg["version"]["pattern"]:
        sys.exit("Error: [version].pattern must contain a named group 'version'")
    return cfg


def read_version(cfg: dict) -> str:
    path = ROOT / cfg["version"]["file"]
    if not path.is_file():
        sys.exit(f"Error: version file not found at {path}")
    pattern = re.compile(cfg["version"]["pattern"])
    match = pattern.search(path.read_text(encoding="utf-8"))
    if not match:
        sys.exit(f"Error: version pattern not found in {path}")
    return match.group("version")


def parse_version(version: str) -> tuple[int, int, int, str | None]:
    match = SEMVER_RE.match(version)
    if not match:
        raise ValueError(f"Invalid semantic version: '{version}'. Must match X.Y.Z or X.Y.Z-suffix")
    major, minor, patch, suffix = match.groups()
    return int(major), int(minor), int(patch), suffix


def format_version(major: int, minor: int, patch: int, suffix: str | None = None) -> str:
    if suffix is not None:
        return f"{major}.{minor}.{patch}-{suffix}"
    return f"{major}.{minor}.{patch}"


def bump_version(current: str, bump_type: str) -> str:
    major, minor, patch, suffix = parse_version(current)
    if bump_type == "major":
        return format_version(major + 1, 0, 0)
    if bump_type == "minor":
        return format_version(major, minor + 1, 0)
    if bump_type == "patch":
        return format_version(major, minor, patch) if suffix else format_version(major, minor, patch + 1)
    if bump_type == "rc":
        if suffix and suffix.startswith("rc."):
            try:
                rc_num = int(suffix.split(".")[1])
            except (ValueError, IndexError):
                rc_num = 0
            return format_version(major, minor, patch, f"rc.{rc_num + 1}")
        return format_version(major, minor, patch + 1, "rc.1")
    raise ValueError(f"Unknown bump type: {bump_type}")


def splice_version(content: str, pattern_str: str, new_version: str) -> tuple[str, int]:
    pattern = re.compile(pattern_str)

    def _sub(match: re.Match) -> str:
        start, end = match.span("version")
        return match.group(0)[: start - match.start()] + new_version + match.group(0)[end - match.start() :]

    return pattern.subn(_sub, content)


def apply_propagation(cfg: dict, new_version: str) -> list[str]:
    updated: list[str] = []
    for prop in cfg.get("propagate", []):
        rel = prop["file"]
        path = ROOT / rel
        label = prop.get("name", rel)
        if not path.is_file():
            print(f"  - {label}: Skipped (not found)")
            continue
        ptype = prop.get("type", "regex")
        text = path.read_text(encoding="utf-8")
        if ptype == "json":
            data = json.loads(text)
            data[prop["field"]] = new_version
            new_text = json.dumps(data, indent=2) + "\n"
        elif ptype == "npm-lock":
            data = json.loads(text)
            if "version" in data:
                data["version"] = new_version
            if data.get("packages", {}).get("", {}).get("version") is not None:
                data["packages"][""]["version"] = new_version
            new_text = json.dumps(data, indent=2) + "\n"
        elif ptype == "regex":
            version_text = new_version.replace("-", "--") if prop.get("dash_escape") else new_version
            template = prop.get("template")
            if template:
                replacement = template.replace("{version}", version_text)
                new_text, count = re.compile(prop["pattern"]).subn(replacement, text)
            else:
                new_text, count = splice_version(text, prop["pattern"], version_text)
            if count == 0:
                print(f"  - {label}: Skipped (pattern not matched)")
                continue
        else:
            sys.exit(f"Error: unknown propagate type '{ptype}' for {rel}")
        if new_text != text:
            path.write_text(new_text, encoding="utf-8")
            updated.append(rel)
            print(f"  - {label}: Updated")
        else:
            print(f"  - {label}: Unchanged")
    return updated


def release_paths(cfg: dict) -> list[str]:
    paths = [cfg["version"]["file"]]
    paths += [p["file"] for p in cfg.get("propagate", [])]
    paths += cfg.get("release", {}).get("docs", [])
    return paths


def run(args: list[str], check: bool = True, strip: bool = True) -> str:
    result = subprocess.run(args, capture_output=True, text=True, cwd=ROOT)
    if check and result.returncode != 0:
        raise RuntimeError(f"Command {' '.join(args)} failed (exit {result.returncode}): {result.stderr.strip()}")
    return result.stdout.strip() if strip else result.stdout


def git_release(cfg: dict, version: str, push: bool) -> None:
    run(["git", "rev-parse", "--is-inside-work-tree"])
    allowed = {str(Path(p).as_posix()) for p in release_paths(cfg)}
    dirty = [
        line[3:].strip().strip('"')
        for line in run(["git", "status", "--porcelain"], strip=False).splitlines()
        if line.strip()
    ]
    unexpected = [p for p in dirty if p not in allowed]
    if unexpected:
        sys.exit(
            "Error: refusing to release with unrelated dirty files:\n  "
            + "\n  ".join(unexpected)
            + "\nCommit or stash them first."
        )

    prefix = cfg.get("release", {}).get("tag_prefix", "v")
    tag = f"{prefix}{version}"
    existing = [p for p in allowed if (ROOT / p).is_file()]
    if existing:
        run(["git", "add", *existing])
    diff = subprocess.run(["git", "diff", "--cached", "--quiet"], cwd=ROOT, capture_output=True)
    if diff.returncode != 0:
        message = cfg.get("release", {}).get(
            "commit_message", "chore(release): bump version to {version}"
        ).format(version=version)
        run(["git", "commit", "-m", message])
        print(f"  committed: {message}")
    else:
        print("  nothing to commit")

    tag_exists = subprocess.run(
        ["git", "rev-parse", "--verify", f"refs/tags/{tag}"], cwd=ROOT, capture_output=True
    ).returncode == 0
    if not tag_exists:
        run(["git", "tag", "-a", tag, "-m", f"Release {version}"])
        print(f"  tagged: {tag}")
    else:
        print(f"  tag {tag} already exists, skipping")

    if not push:
        print("Local only — run with --push to publish and trigger CI.")
        return

    remotes = [r for r in run(["git", "remote"]).splitlines() if r.strip()]
    if not remotes:
        print("No git remotes configured; skipping push.")
        return
    branch = run(["git", "rev-parse", "--abbrev-ref", "HEAD"])
    for remote in remotes:
        run(["git", "push", remote, branch])
        run(["git", "push", remote, tag])
        print(f"  pushed branch + {tag} to {remote}")
    print(f"Release {version} published.")


def main() -> None:
    cfg = load_config()
    parser = argparse.ArgumentParser(
        description=f"{cfg['project']['name']} version manager (family-unified)",
        epilog="Config: version_manager.toml (family-unified script — edit the config, not the script)",
    )
    git_parser = argparse.ArgumentParser(add_help=False)
    git_parser.add_argument("--git", "-g", action="store_true", help="commit the bump and create the tag")
    git_parser.add_argument("--push", "-p", action="store_true", help="push commit + tag to every remote (implies --git)")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("show", help="print the current version")
    set_parser = subparsers.add_parser("set", parents=[git_parser], help="set an explicit version (X.Y.Z or X.Y.Z-suffix)")
    set_parser.add_argument("version")
    set_parser.add_argument("--dry", action="store_true", help="print the change without writing")
    bump_parser = subparsers.add_parser("bump", parents=[git_parser], help="bump major/minor/patch/rc")
    bump_parser.add_argument("type", choices=["major", "minor", "patch", "rc"])
    bump_parser.add_argument("--dry", action="store_true", help="print the change without writing")
    subparsers.add_parser("release", parents=[git_parser], help="commit/tag/push the version already recorded on disk")
    args = parser.parse_args()

    current = read_version(cfg)
    if args.command == "show":
        print(current)
        return
    try:
        if args.command == "set":
            parse_version(args.version)
            new_version = args.version
        elif args.command == "bump":
            new_version = bump_version(current, args.type)
        else:
            git_release(cfg, current, push=args.push)
            return

        print(f"version: {current} -> {new_version}")
        if args.dry:
            return

        path = ROOT / cfg["version"]["file"]
        text = path.read_text(encoding="utf-8")
        new_text, count = splice_version(text, cfg["version"]["pattern"], new_version)
        if count == 0:
            sys.exit(f"Error: version pattern not found in {path}")
        path.write_text(new_text, encoding="utf-8")
        print(f"  - {cfg['version']['file']}: Updated")
        apply_propagation(cfg, new_version)

        if args.git or args.push:
            git_release(cfg, new_version, push=args.push)
    except (ValueError, RuntimeError) as exc:
        sys.exit(f"Error: {exc}")


if __name__ == "__main__":
    main()
