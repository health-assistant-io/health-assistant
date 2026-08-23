#!/usr/bin/env python3
"""Interactive environment-setup wizard for Health Assistant.

Copies ``.env.example`` to ``.env`` and writes auto-generated secure
values into it: ``SECRET_KEY``, ``INTEGRATION_SECRET_KEY`` (Fernet),
``POSTGRES_PASSWORD``, ``FLOWER_PASSWORD``, a VAPID P-256 key pair
for Web Push, and (in Quick Start / Full Setup mode)
``VAPID_ADMIN_EMAIL`` plus the first-run ``SETUP_TOKEN_MODE`` (+
``SETUP_BOOTSTRAP_TOKEN`` when ``env`` mode).

Usage:
    python3 scripts/setup_env.py            # interactive (quick / full / keys-only)
    python3 scripts/setup_env.py --help     # print this help and exit
    python3 scripts/setup_env.py --mode=1   # Quick Start, non-interactive

Modes (chosen interactively unless ``--mode`` is given):
    1) Quick Start (recommended) — asks only the public app URL; everything
       else is auto-configured with production-oriented values for the
       standalone Docker stack (docker-compose.standalone.yml).
    2) Full Setup — prompts for environment, URLs, workers, VAPID email,
       setup-token mode. Generates all keys.
    3) Keys Only Setup — generates keys, leaves everything else at the
       defaults in the generated .env.

Idempotent guard: refuses to overwrite an existing .env. Delete .env first
to regenerate. Run from the project root.

Exit codes:
    0   success or --help
    1   missing .env.example, or .env already exists
"""
import base64
import os
import secrets
import sys
from datetime import datetime
from urllib.parse import urlparse

_HELP_TEXT = __doc__ or ""


def _print_help_and_exit() -> None:
    sys.stdout.write(_HELP_TEXT + "\n")
    sys.exit(0)


if any(arg in ("-h", "--help") for arg in sys.argv[1:]):
    _print_help_and_exit()


def _parse_mode_arg() -> str | None:
    """Return the mode number chosen via ``--mode=1|2|3`` or ``None`` if not set.

    Accepts ``--mode=1`` and ``--mode 1``. Rejects unknown values by exiting
    with a usage hint. Non-``--mode`` args are left untouched for the
    interactive flow (this script is interactive by design).
    """
    args = sys.argv[1:]
    i = 0
    while i < len(args):
        a = args[i]
        if a in ("-h", "--help"):
            _print_help_and_exit()
        if a.startswith("--mode="):
            value = a.split("=", 1)[1]
            if value not in ("1", "2", "3"):
                print(f"Invalid --mode value: {value!r}. Use 1 (Quick Start), 2 (Full) or 3 (Keys Only).")
                sys.exit(1)
            return value
        if a == "--mode":
            if i + 1 >= len(args) or args[i + 1] not in ("1", "2", "3"):
                print("--mode requires a value: 1 (Quick Start), 2 (Full) or 3 (Keys Only).")
                sys.exit(1)
            return args[i + 1]
        i += 1
    return None


_PRESET_MODE = _parse_mode_arg()


def _derive_email_default(app_url: str) -> str:
    """Derive a sensible VAPID contact email default from the APP_URL hostname.

    For ``https://health.example.com`` → ``admin@health.example.com``.
    For ``http://localhost`` → ``admin@example.com`` (localhost isn't a
    deliverable domain, so fall back to the conventional placeholder rather
    than minting an undeliverable ``admin@localhost``).
    """
    try:
        host = urlparse(app_url).hostname or ""
    except Exception:
        host = ""
    if host and host not in ("localhost", "127.0.0.1", "0.0.0.0"):
        return f"admin@{host}"
    return "admin@example.com"


def _normalize_app_url(raw: str) -> str:
    """Normalize a user-entered app URL into an absolute ``http(s)://`` URL.

    Strips a trailing ``/`` and prepends ``http://`` to bare hosts
    (``health.example.com`` → ``http://health.example.com``). Schemes the user
    typed (``https://``) are left untouched.
    """
    url = raw.strip().rstrip("/")
    if not url:
        return ""
    if "://" not in url:
        url = "http://" + url
    return url


def _is_localhost_host(url: str) -> bool:
    """Return True when the URL's host is a localhost/loopback host."""
    try:
        host = urlparse(url).hostname or ""
    except Exception:
        host = ""
    return host in ("localhost", "127.0.0.1", "::1", "0.0.0.0")


def generate_vapid_keys():
    """Generate a VAPID P-256 key pair for Web Push.

    Returns ``(public_key_b64, private_key_b64)`` as base64url strings
    without padding, matching the format produced by
    ``npx web-push generate-vapid-keys`` and consumed by ``pywebpush`` /
    the browser ``PushManager.subscribe`` API. Uses the ``cryptography``
    package (already a dependency via Fernet) — no Node.js / pywebpush
    CLI needed.
    """
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec

    private_key = ec.generate_private_key(ec.SECP256R1())
    # Private key: the 32-byte EC scalar.
    private_bytes = private_key.private_numbers().private_value.to_bytes(32, "big")
    # Public key: X9.62 uncompressed point (0x04 || X || Y, 65 bytes).
    public_bytes = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.X962,
        format=serialization.PublicFormat.UncompressedPoint,
    )
    # base64url without padding — what pywebpush and the browser expect.
    public_b64 = base64.urlsafe_b64encode(public_bytes).rstrip(b"=").decode("ascii")
    private_b64 = base64.urlsafe_b64encode(private_bytes).rstrip(b"=").decode("ascii")
    return public_b64, private_b64


# ─── Utility Functions ────────────────────────────────────────────────────────
def prompt(text, default="", options=None):
    """Interactive prompt with optional default and specific allowed options."""
    default_text = f" [{default}]" if default else ""
    options_text = f" ({'/'.join(options)})" if options else ""
    
    while True:
        response = input(f"{text}{options_text}{default_text}: ").strip()
        
        # Use default if empty
        if not response and default:
            return default
            
        # If options are provided, validate
        if options:
            # Case insensitive check for convenience, but return the exact option match
            lower_options = [str(o).lower() for o in options]
            if response.lower() in lower_options:
                return response
            print(f"Invalid choice. Please enter one of: {', '.join(options)}")
        else:
            # Any input is fine if no specific options are required
            if response or default: 
                return response
            
def prompt_bool(text, default="y"):
    """Interactive prompt for boolean values."""
    default_text = "[Y/n]" if default.lower() == 'y' else "[y/N]"
    while True:
        response = input(f"{text} {default_text}: ").strip().lower()
        if not response:
            return "true" if default.lower() == 'y' else "false"
        if response in ['y', 'yes', 'true']:
            return "true"
        if response in ['n', 'no', 'false']:
            return "false"
        print("Please answer y or n.")


# ─── Mode flows ───────────────────────────────────────────────────────────────
def _run_quick_start(config):
    """Quick Start — ask only the app URL; production-oriented defaults.

    Host-based first-run setup-token choice: localhost → ``log`` (localhost
    skips the token anyway); any other host → ``env`` with a generated
    bootstrap token and a printed one-click wizard URL. Writes ``DEMO_MODE``
    off and never writes the demo/dev-tooling credentials.
    """
    print("\n--- Quick Start (recommended) ---")
    print("Production-oriented configuration for the standalone Docker stack.")
    print("You'll be asked for the public app URL; everything else is")
    print("auto-configured with secure defaults.")
    print("\nApp URL is the public base URL of the deployment (e.g., https://health.example.com).")
    print("On this machine, press Enter to accept the localhost default.")

    app_url = prompt("Public App URL", default="http://localhost")
    config["APP_URL"] = _normalize_app_url(app_url) or "http://localhost"

    config["APP_ENV"] = "production"
    config["DEBUG"] = "false"
    config["VAPID_ADMIN_EMAIL"] = _derive_email_default(config["APP_URL"])
    config["TRUSTED_PROXY_COUNT"] = "1"
    config["CELERY_WORKER_CONCURRENCY"] = "2"
    config["DEMO_MODE"] = "false"

    if _is_localhost_host(config["APP_URL"]):
        config["SETUP_TOKEN_MODE"] = "log"
        print("\nLocalhost detected — the first-run setup wizard needs no token.")
    else:
        config["SETUP_TOKEN_MODE"] = "env"
        config["SETUP_BOOTSTRAP_TOKEN"] = secrets.token_urlsafe(24)
        print("\n✨ Generated SETUP_BOOTSTRAP_TOKEN — one-click first-run setup:")
        print(f"   {config['APP_URL']}/setup?token={config['SETUP_BOOTSTRAP_TOKEN']}")

    # Advanced gate — skipped entirely on a non-interactive --mode=1 run so a
    # scripted invocation never hangs on a prompt.
    if _PRESET_MODE is None and prompt_bool("Configure advanced settings (workers, ports, setup-token mode, debug)?", default="n") == "true":
        _run_full_setup(config)


def _run_full_setup(config):
    """Full Setup — existing detailed flow; existing config values prefill.

    Any value already present in ``config`` (e.g. from the Quick Start
    advanced gate) is used as the prompt default, so nothing a beginner
    answered is lost. Defaults otherwise lean production (the recommended
    standalone stack hardcodes ``APP_ENV=production`` / ``DEBUG=false``).
    """
    print("\n--- Full Configuration ---")

    print("\nChoose your environment type:")
    print("  1) production (Default)")
    print("  2) development")
    current_env = str(config.get("APP_ENV", "production") or "production").lower()
    default_env_num = "1" if current_env == "production" else "2"
    env_choice_num = prompt("Select environment", default=default_env_num, options=["1", "2"])

    env_choice = "production" if env_choice_num == "1" else "development"
    config["APP_ENV"] = env_choice

    # Intelligently default DEBUG based on environment (respect an existing
    # value when prefilled from the Quick Start gate).
    current_debug = str(config.get("DEBUG", "")).lower() in ("true", "yes", "1")
    if env_choice == "production":
        default_debug = "y" if current_debug else "n"
    else:
        default_debug = "y"
    config["DEBUG"] = prompt_bool("Enable Debug mode? (Set 'n' for production)", default=default_debug)

    print("\nApp URL is the public base URL of the deployment (e.g., https://health.example.com).")
    print("This is required for OAuth redirects, Web Push notifications, and external integrations.")
    config["APP_URL"] = prompt("Public App URL", default=config.get("APP_URL") or "http://localhost")

    # VAPID contact email — becomes the `sub` claim in the signed JWT.
    # Push services (Google/Mozilla/Apple) use it to reach the operator
    # about delivery issues; a placeholder like admin@healthassistant.local
    # is not reachable, so derive a real default from the APP_URL hostname.
    print("\nWeb Push (VAPID) requires a contact email that push services can use")
    print("to reach you about notification delivery issues (becomes the JWT `sub` claim).")
    vapid_email_default = _derive_email_default(config["APP_URL"])
    config["VAPID_ADMIN_EMAIL"] = prompt(
        "Contact email for Web Push", default=vapid_email_default
    )

    print("\nCelery workers process background tasks (OCR, AI generation, integration fetching).")
    print("Concurrency dictates how many simultaneous tasks a worker container can handle.")
    print("  - 2 is fine for most homelabs.")
    print("  - 4+ is recommended for heavier loads or multi-tenant deployments.")
    worker_concurrency = prompt("Worker concurrency", default=config.get("CELERY_WORKER_CONCURRENCY") or "2")
    # Ensure it's a number, fallback if user typed garbage
    if not worker_concurrency.isdigit():
        print("Invalid number provided, defaulting to 2.")
        worker_concurrency = "2"
    config["CELERY_WORKER_CONCURRENCY"] = worker_concurrency

    # First-run setup-token mode (see dev/audits/setup-token-modes.md).
    print("\nFirst-run setup wizard — how should the first admin account be bootstrapped?")
    print("  1) log     (default) — backend prints a one-time token to the container logs;")
    print("                          you retrieve it with `docker compose logs backend | grep`.")
    print("  2) env     — store/automated installs. A bootstrap token is generated here and")
    print("                injected into the container env; the launcher URL carries it as")
    print("                ?token=<value> for a one-click wizard (no log-grep).")
    print("  3) time    — tokenless for the first N minutes after boot, then required.")
    print("                Best when only the operator can reach the app in that window.")
    print("  4) disabled — never require a token. ONLY safe behind a firewall / VPN / 127.0.0.1.")
    mode_map = {"1": "log", "2": "env", "3": "time", "4": "disabled"}
    current_mode = str(config.get("SETUP_TOKEN_MODE", "log")).lower()
    default_mode_num = {v: k for k, v in mode_map.items()}.get(current_mode, "1")
    mode_choice = prompt("Select setup-token mode", default=default_mode_num, options=["1", "2", "3", "4"])
    config["SETUP_TOKEN_MODE"] = mode_map[mode_choice]
    if mode_choice == "2":
        if not config.get("SETUP_BOOTSTRAP_TOKEN"):
            config["SETUP_BOOTSTRAP_TOKEN"] = secrets.token_urlsafe(24)
        bootstrap_token = config["SETUP_BOOTSTRAP_TOKEN"]
        print("\n✨ Generated SETUP_BOOTSTRAP_TOKEN. Compose your launcher URL as:")
        print(f"   {config['APP_URL']}/setup?token={bootstrap_token}")
        print("   The wizard auto-fills the token; the user clicks once and is done.")
    else:
        config.pop("SETUP_BOOTSTRAP_TOKEN", None)
        if mode_choice == "3":
            grace = prompt(
                "Tokenless grace window (minutes)", default="30"
            )
            if not grace.isdigit() or int(grace) < 1:
                print("Invalid number provided, defaulting to 30.")
                grace = "30"
            config["SETUP_TOKEN_GRACE_MINUTES"] = grace
        else:
            config.pop("SETUP_TOKEN_GRACE_MINUTES", None)


# ─── Main Setup ───────────────────────────────────────────────────────────────

print("==================================================")
print("     🏥 Health Assistant - Environment Setup      ")
print("==================================================")

if os.path.exists(".env"):
    print("\n⚠️  .env file already exists.")
    print("Skipping auto-generation to prevent overwriting your existing configuration.")
    print("If you want to regenerate, please delete or rename your current .env file and run this script again.")
    sys.exit(0)

if not os.path.exists(".env.example"):
    print("\n❌ Error: .env.example not found.")
    print("Please run this script from the root of the repository.")
    sys.exit(1)

# Generate secure keys (always done)
secret_key = secrets.token_urlsafe(48)
postgres_password = secrets.token_urlsafe(24)
flower_password = secrets.token_urlsafe(24)
redis_password = secrets.token_urlsafe(24)

# Generate a valid Fernet key (32 bytes, base64url encoded)
fernet_key_bytes = os.urandom(32)
integration_secret_key = base64.urlsafe_b64encode(fernet_key_bytes).decode('utf-8')

# Generate a VAPID P-256 key pair for Web Push (browser notifications).
# Required in production — the app refuses to boot without these when
# APP_ENV != "development" (see config.py prod-guard validator).
vapid_public_key, vapid_private_key = generate_vapid_keys()

# Interactive Setup Choice
print("\nThis script will automatically generate secure cryptographic keys for your installation.")
print("How would you like to configure the rest of the environment?")
print("  1) Quick Start  (recommended) — just answer the app URL; everything else")
print("                    auto-configured for the standalone Docker stack")
print("  2) Full Setup   — configure environment, URLs, workers, setup-token mode")
print("  3) Keys Only    — generate keys only; edit .env manually later")

setup_mode = _PRESET_MODE or prompt("\nSelect setup mode", default="1", options=["1", "2", "3"])

# Default configs — just the keys. Quick Start / Full Setup add the rest;
# Keys Only keeps everything else at the .env.example defaults.
config = {
    "SECRET_KEY": secret_key,
    "POSTGRES_PASSWORD": postgres_password,
    "FLOWER_PASSWORD": flower_password,
    "REDIS_PASSWORD": redis_password,
    "INTEGRATION_SECRET_KEY": integration_secret_key,
    "VAPID_PUBLIC_KEY": vapid_public_key,
    "VAPID_PRIVATE_KEY": vapid_private_key,
}

if setup_mode == "1":
    _run_quick_start(config)
elif setup_mode == "2":
    _run_full_setup(config)
# Mode 3 (Keys Only): keys only, nothing further.

print("\nGenerating .env file...")

try:
    with open(".env.example", "r") as example_file:
        lines = example_file.readlines()
        
    with open(".env", "w") as env_file:
        env_file.write(f"# Auto-generated by scripts/setup_env.py on {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")

        # In Quick Start the demo/dev-tooling credentials stay hidden so a
        # production .env carries no demo cruft (they remain available in
        # .env.example for the screenshot-capture tooling).
        hide_demo_creds = setup_mode == "1"
        
        for line in lines:
            # We check and replace known keys. Two forms are handled:
            #   KEY=...          → active line, replace value
            #   # KEY=...         → commented-out line (e.g. VAPID_* in .env.example),
            #                       uncomment + replace value
            replaced = False
            for key, val in config.items():
                if line.startswith(f"{key}="):
                    env_file.write(f"{key}={val}\n")
                    replaced = True
                    break
                stripped = line.strip()
                if stripped.startswith((f"# {key}=", f"#{key}=")):
                    # Only uncomment a real assignment, not a prose comment
                    # like "# APP_ENV=production + DEMO_MODE=true)." — real
                    # values are single tokens (no whitespace).
                    if " " not in stripped.split("=", 1)[1]:
                        env_file.write(f"{key}={val}\n")
                        replaced = True
                    break
            
            # If not a managed key, just write the original line
            if not replaced:
                if hide_demo_creds and line.startswith(("HA_DEMO_EMAIL=", "HA_DEMO_PASSWORD=")):
                    env_file.write(f"# {line}")
                else:
                    env_file.write(line)

    # Audit 2026-08 CFG-L3: the .env holds SECRET_KEY / DB / Fernet / VAPID
    # secrets — restrict to owner-only permissions.
    os.chmod(".env", 0o600)
    print("\n✅ Environment configured successfully! (permissions set to 600)")
    print("✨ Secure keys have been automatically generated for:")
    print("   - SECRET_KEY")
    print("   - INTEGRATION_SECRET_KEY")
    print("   - VAPID_PUBLIC_KEY / VAPID_PRIVATE_KEY (Web Push)")
    print("   - POSTGRES_PASSWORD")
    print("   - FLOWER_PASSWORD")
    
    if setup_mode == "1":
        print("✨ Production-oriented defaults applied (APP_ENV=production, DEBUG=false).")
        print("✨ Web Push contact email (VAPID_ADMIN_EMAIL) also configured —")
        print("   no further VAPID setup needed.")
        if config.get("SETUP_TOKEN_MODE") == "env":
            print("\n✨ One-click first-run setup URL:")
            print(f"   {config['APP_URL']}/setup?token={config['SETUP_BOOTSTRAP_TOKEN']}")
        print("\n🚀 Start the application with:")
        print("   docker compose --env-file .env -f docker/docker-compose.standalone.yml up -d")
        print("   (or run ./scripts/install.sh which does this for you)")
        print("\n💡 AI features (OCR, document extraction, chat assistant) need an AI provider")
        print("   key configured in-app (System Admin → AI) or via the OPENAI_* env vars.")
        print("   The app runs fine without one — you just don't get the AI features.")
    elif setup_mode == "2":
        if config.get("VAPID_ADMIN_EMAIL"):
            print("✨ Web Push contact email (VAPID_ADMIN_EMAIL) also configured —")
            print("   no further VAPID setup needed.")
        print("✨ Your custom configurations have also been saved.")
        
        if config.get("APP_ENV") == "production":
            print("\n⚠️  Next steps (Production):")
            print("   1. Please review the 'Production Deployment' section in docs/INSTALL.md")
            print("      (or https://health-assistant.io/docs/install#production-deployment)")
            print("   2. Once ready, you can start the application with:")
            print("      docker compose --env-file .env -f docker/docker-compose.standalone.yml up -d")
            print("      (docker-compose.prod.yml is the bring-your-own-proxy alternative)")
        else:
            print("\n🚀 You can now start the application with:")
            print("   docker compose --env-file .env -f docker/docker-compose.dev.yml up -d")
        if config.get("SETUP_TOKEN_MODE") == "env":
            print("\n✨ One-click first-run setup URL:")
            print(f"   {config['APP_URL']}/setup?token={config['SETUP_BOOTSTRAP_TOKEN']}")
    else:
        print("\n⚠️  Next steps:")
        print("   Please open the newly created '.env' file in your text editor and review")
        print("   the remaining configurations (such as APP_URL, ports, or optional settings).")
        print("   Once configured, refer to docs/INSTALL.md or https://health-assistant.io/docs/install")
        print("   for the correct start commands based on your environment.")

except Exception as e:
    print(f"\n❌ Error during setup: {e}")
    sys.exit(1)