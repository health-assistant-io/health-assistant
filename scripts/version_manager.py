#!/usr/bin/env python3
import sys
import os
import re
import json
import argparse
import subprocess

# Get the root directory of the project
ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))

# Files to update
CONFIG_PY = os.path.join(ROOT_DIR, "backend/app/core/config.py")
FRONTEND_PKG = os.path.join(ROOT_DIR, "frontend/package.json")
FRONTEND_LOCK = os.path.join(ROOT_DIR, "frontend/package-lock.json")
INSTALL_MD = os.path.join(ROOT_DIR, "docs/INSTALL.md")
README_MD = os.path.join(ROOT_DIR, "README.md")
ABOUT_PAGE = os.path.join(ROOT_DIR, "frontend/src/pages/About/AboutPage.tsx")

# Files that should always be staged together with version bumps (the
# release-scope documentation). These are not version-string files but must
# ship in the same release commit so the tag points at a consistent tree.
RELEASE_DOCS = [
    os.path.join(ROOT_DIR, "CHANGELOG.md"),
    os.path.join(ROOT_DIR, "docs/RELEASE_PROCESS.md"),
]

def get_current_version():
    """Reads the current version from the backend core config file"""
    if not os.path.exists(CONFIG_PY):
        sys.exit(f"Error: Backend config file not found at {CONFIG_PY}")
    
    with open(CONFIG_PY, "r", encoding="utf-8") as f:
        content = f.read()
    
    match = re.search(r'VERSION:\s*str\s*=\s*"([^"]+)"', content)
    if not match:
        sys.exit("Error: Could not find VERSION string in backend config file.")
    
    return match.group(1)

def parse_version(version_str):
    """Parses semver string into major, minor, patch, and optional suffix parts"""
    # Supporting standard semver and common suffixes like -rc.N, -beta, etc.
    match = re.match(r"^(\d+)\.(\d+)\.(\d+)(?:-([a-zA-Z0-9.]+))?$", version_str)
    if not match:
        raise ValueError(f"Invalid semantic version: '{version_str}'. Must match format: X.Y.Z or X.Y.Z-suffix")
    
    major, minor, patch, suffix = match.groups()
    return int(major), int(minor), int(patch), suffix

def format_version(major, minor, patch, suffix=None):
    """Formats parts back into standard semver string"""
    if suffix is not None:
        return f"{major}.{minor}.{patch}-{suffix}"
    return f"{major}.{minor}.{patch}"

def bump_version(current_str, bump_type):
    """Calculates the new version string based on current and bump type"""
    major, minor, patch, suffix = parse_version(current_str)
    
    if bump_type == "major":
        return format_version(major + 1, 0, 0)
    elif bump_type == "minor":
        return format_version(major, minor + 1, 0)
    elif bump_type == "patch":
        if suffix is not None:
            # Promoting a pre-release to a full release of the same version
            return format_version(major, minor, patch)
        else:
            return format_version(major, minor, patch + 1)
    elif bump_type == "rc":
        if suffix and suffix.startswith("rc."):
            try:
                rc_num = int(suffix.split(".")[1])
                return format_version(major, minor, patch, f"rc.{rc_num + 1}")
            except (ValueError, IndexError):
                return format_version(major, minor, patch, "rc.1")
        else:
            # Adding RC to the next patch version
            return format_version(major, minor, patch + 1, "rc.1")
    else:
        raise ValueError(f"Unknown bump type: {bump_type}")

def update_backend_config(new_version):
    """Updates VERSION variable in config.py"""
    if not os.path.exists(CONFIG_PY):
        return False
    with open(CONFIG_PY, "r", encoding="utf-8") as f:
        content = f.read()
    
    # Use regex with grouping to preserve comments and layout
    updated, count = re.subn(
        r'(VERSION:\s*str\s*=\s*")[^"]+(")',
        rf'\g<1>{new_version}\g<2>',
        content
    )
    if count > 0:
        with open(CONFIG_PY, "w", encoding="utf-8") as f:
            f.write(updated)
        return True
    return False

def update_frontend_package(new_version):
    """Updates version in package.json"""
    if not os.path.exists(FRONTEND_PKG):
        return False
    with open(FRONTEND_PKG, "r", encoding="utf-8") as f:
        data = json.load(f)
    
    data["version"] = new_version
    
    with open(FRONTEND_PKG, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
        f.write("\n")  # Add trailing newline
    return True

def update_frontend_package_lock(new_version):
    """Updates version in package-lock.json if it exists"""
    if not os.path.exists(FRONTEND_LOCK):
        return False
    with open(FRONTEND_LOCK, "r", encoding="utf-8") as f:
        data = json.load(f)
    
    # Update root package-lock version
    if "version" in data:
        data["version"] = new_version
    
    # Update packages[""] version
    if "packages" in data and "" in data["packages"]:
        if "version" in data["packages"][""]:
            data["packages"][""]["version"] = new_version
            
    with open(FRONTEND_LOCK, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
        f.write("\n")
    return True

def update_install_docs(new_version):
    """Updates expected version string in INSTALL.md"""
    if not os.path.exists(INSTALL_MD):
        return False
    with open(INSTALL_MD, "r", encoding="utf-8") as f:
        content = f.read()
        
    updated, count = re.subn(
        r'(\{"name"\s*:\s*"Health Assistant"\s*,\s*"version"\s*:\s*")[^"]+("\s*,\s*"docs"\s*:\s*"/docs"\})',
        rf'\g<1>{new_version}\g<2>',
        content
    )
    if count > 0:
        with open(INSTALL_MD, "w", encoding="utf-8") as f:
            f.write(updated)
        return True
    return False

def update_readme_badge(new_version):
    """Updates the version badge in README.md"""
    if not os.path.exists(README_MD):
        return False
    with open(README_MD, "r", encoding="utf-8") as f:
        content = f.read()
    
    # Shields.io uses double dash for a single dash in labels
    badge_version = new_version.replace("-", "--")
    
    updated, count = re.subn(
        r'(img\.shields\.io/badge/version-v)[^-\)]+(--?[^-\)]*)?(-blue\.svg)',
        rf'\g<1>{badge_version}\g<3>',
        content
    )
    
    if count > 0:
        with open(README_MD, "w", encoding="utf-8") as f:
            f.write(updated)
        return True
    return False

def update_about_page(new_version):
    """Updates the version string in AboutPage.tsx"""
    if not os.path.exists(ABOUT_PAGE):
        return False
    with open(ABOUT_PAGE, "r", encoding="utf-8") as f:
        content = f.read()
        
    updated, count = re.subn(
        r'(Health Assistant Version\s+)[^<]+(<br\s*/>)',
        rf'\g<1>{new_version}\g<2>',
        content
    )
    
    if count > 0:
        with open(ABOUT_PAGE, "w", encoding="utf-8") as f:
            f.write(updated)
        return True
    return False

def apply_version_update(new_version):
    """Updates all files with the new version string"""
    # Validate the version string format first
    parse_version(new_version)
    
    print(f"Updating project files to version: {new_version}...")
    
    updates = {
        "Backend Core Config": update_backend_config(new_version),
        "Frontend package.json": update_frontend_package(new_version),
        "Frontend package-lock.json": update_frontend_package_lock(new_version),
        "docs/INSTALL.md": update_install_docs(new_version),
        "README.md Badge": update_readme_badge(new_version),
        "About Page": update_about_page(new_version),
    }
    
    for name, success in updates.items():
        status = "Updated" if success else "Skipped (Not Found or No Match)"
        print(f"  - {name}: {status}")
    
    print("Project version update complete! 🎉")

def run_cmd(args, check=True):
    """Runs a shell command in the project root and returns stdout/stderr"""
    result = subprocess.run(args, capture_output=True, text=True, cwd=ROOT_DIR)
    if check and result.returncode != 0:
        raise RuntimeError(f"Command {' '.join(args)} failed with exit code {result.returncode}:\n{result.stderr}")
    return result.stdout.strip(), result.stderr.strip()

def check_git_repo():
    """Checks if the project is a git repository"""
    try:
        run_cmd(["git", "rev-parse", "--is-inside-work-tree"])
        return True
    except (RuntimeError, FileNotFoundError):
        return False

def get_git_remotes():
    """Returns the list of remote names configured in the repository."""
    try:
        out, _ = run_cmd(["git", "remote"], check=False)
        remotes = [r.strip() for r in out.splitlines() if r.strip()]
        return remotes
    except (RuntimeError, FileNotFoundError):
        return []

def git_operations(new_version, push=False):
    """Saves changes to git, creates a tag, and optionally pushes to remotes.

    When ``push=True`` the commit and tag are pushed to **every** configured
    remote (not just ``origin``) so e.g. a self-hosted Gitea mirror and the
    public GitHub remote both receive the tag — which is what triggers the
    CI release workflow.
    """
    if not check_git_repo():
        print("Warning: Not a git repository or git is not installed. Skipping git operations.")
        return False
    
    print("Performing Git operations...")
    
    # Files to add: version-string files + release-scope docs
    files_to_add = []
    trackable_files = [CONFIG_PY, FRONTEND_PKG, FRONTEND_LOCK, INSTALL_MD, README_MD, ABOUT_PAGE]
    # Release docs are staged if they have pending changes (they often do at
    # release time — that's the whole point of the commit-time changelog rule).
    trackable_files += RELEASE_DOCS
    for filepath in trackable_files:
        if os.path.exists(filepath):
            rel_path = os.path.relpath(filepath, ROOT_DIR)
            files_to_add.append(rel_path)
            
    if not files_to_add:
        print("No files to commit.")
        return False
        
    try:
        # git add
        print(f"  - Staging updated files: {', '.join(files_to_add)}")
        run_cmd(["git", "add"] + files_to_add)
        
        # Check if there are actually staged changes to commit
        # git diff --cached --quiet returns exit code 1 if there are changes, 0 if clean
        res = subprocess.run(["git", "diff", "--cached", "--quiet"], cwd=ROOT_DIR)
        if res.returncode != 0:
            # git commit
            commit_msg = f"chore(release): bump version to {new_version}"
            print(f"  - Committing: '{commit_msg}'")
            run_cmd(["git", "commit", "-m", commit_msg])
        else:
            print("  - No version file changes to commit (already up-to-date in git).")
        
        # git tag
        tag_name = f"v{new_version}"
        print(f"  - Tagging: '{tag_name}'")
        # Check if tag already exists to avoid tagging error
        tag_exists = False
        try:
            run_cmd(["git", "rev-parse", tag_name])
            tag_exists = True
        except RuntimeError:
            pass
            
        if not tag_exists:
            run_cmd(["git", "tag", "-a", tag_name, "-m", f"Release version {new_version}"])
        else:
            print(f"  - Tag '{tag_name}' already exists. Skipping tagging.")
        
        if push:
            remotes = get_git_remotes()
            if not remotes:
                print("  - No git remotes configured. Skipping push.")
            else:
                branch, _ = run_cmd(["git", "rev-parse", "--abbrev-ref", "HEAD"])
                for remote in remotes:
                    print(f"  - Pushing to remote '{remote}' (branch: {branch}, tag: {tag_name})")
                    # Push commit if we committed something
                    if res.returncode != 0:
                        run_cmd(["git", "push", remote, branch])
                    run_cmd(["git", "push", remote, tag_name])
                print("Git push successful! 🚀")
            
        print("Git operations complete!")
        return True
    except Exception as e:
        sys.exit(f"Error during Git operations: {e}")

def release_current_version(push=False):
    """Commit + tag + (optionally) push the version already recorded in
    ``config.py``.

    This is the catch-up path for when you ran ``set``/``bump`` without
    ``--git --push`` (or edited ``CHANGELOG.md`` after the version bump).
    It stages the version files + release docs, commits, tags ``vX.Y.Z``,
    and pushes to every remote.
    """
    version = get_current_version()
    print(f"Releasing current version: {version}")
    return git_operations(version, push=push)

def main():
    parser = argparse.ArgumentParser(
        description="Health Assistant Project Version Manager",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python3 scripts/version_manager.py show
  python3 scripts/version_manager.py set 1.2.0-rc.1
  python3 scripts/version_manager.py set 1.2.0-rc.1 --git
  python3 scripts/version_manager.py bump rc
  python3 scripts/version_manager.py bump patch --git --push
  python3 scripts/version_manager.py release --push
"""
    )
    
    # Common parser for git actions
    git_parser = argparse.ArgumentParser(add_help=False)
    git_parser.add_argument(
        "--git", "-g",
        action="store_true",
        help="Stage updated files, commit and create a git tag (e.g. vX.Y.Z)"
    )
    git_parser.add_argument(
        "--push", "-p",
        action="store_true",
        help="Push the commit and tag to every configured remote (implies --git)"
    )
    
    subparsers = parser.add_subparsers(dest="command", required=True)
    
    # Show command
    subparsers.add_parser("show", help="Display the current version of the project")
    
    # Set command
    set_parser = subparsers.add_parser(
        "set", 
        parents=[git_parser],
        help="Explicitly set the project version to a specific semver value"
    )
    set_parser.add_argument("version", type=str, help="New semantic version string (e.g. 1.1.0-rc.3)")
    
    # Bump command
    bump_parser = subparsers.add_parser(
        "bump", 
        parents=[git_parser],
        help="Automatically bump a part of the version"
    )
    bump_parser.add_argument(
        "type", 
        choices=["major", "minor", "patch", "rc"], 
        help="Type of bump: major (X.0.0), minor (X.Y.0), patch (X.Y.Z), or rc (X.Y.Z-rc.N)"
    )
    
    # Release command — catch-up: commit + tag + push the version already in config.py
    release_parser = subparsers.add_parser(
        "release",
        parents=[git_parser],
        help="Commit, tag, and optionally push the version already recorded in config.py"
             " — use this when you ran set/bump without --git --push, or edited"
             " CHANGELOG.md after the bump."
    )
    
    args = parser.parse_args()
    
    current_v = get_current_version()
    
    if args.command == "show":
        print(f"Current project version: {current_v}")
        
    elif args.command == "set":
        try:
            apply_version_update(args.version)
            if args.git or args.push:
                git_operations(args.version, push=args.push)
        except ValueError as e:
            sys.exit(f"Error: {e}")
            
    elif args.command == "bump":
        try:
            new_v = bump_version(current_v, args.type)
            print(f"Bumping version from {current_v} -> {new_v}")
            apply_version_update(new_v)
            if args.git or args.push:
                git_operations(new_v, push=args.push)
        except ValueError as e:
            sys.exit(f"Error: {e}")
    
    elif args.command == "release":
        release_current_version(push=args.push)

if __name__ == "__main__":
    main()
