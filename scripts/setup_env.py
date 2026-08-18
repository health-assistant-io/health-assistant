#!/usr/bin/env python3
"""Interactive environment-setup wizard for Health Assistant.

Copies ``.env.example`` to ``.env`` and writes auto-generated secure
values into it: ``SECRET_KEY``, ``INTEGRATION_SECRET_KEY`` (Fernet),
``POSTGRES_PASSWORD``, ``FLOWER_PASSWORD``, a VAPID P-256 key pair
for Web Push, and (in Full Setup mode) ``VAPID_ADMIN_EMAIL`` plus the
first-run ``SETUP_TOKEN_MODE`` + ``SETUP_BOOTSTRAP_TOKEN``.

Usage:
    python scripts/setup_env.py             # interactive (full or keys-only)
    python scripts/setup_env.py --help      # print this help and exit

Modes (chosen interactively unless ``--mode`` is given):
    1) Full Setup       — prompts for environment, URLs, workers, VAPID email,
                          setup-token mode. Generates all keys.
    2) Keys Only Setup  — generates keys, leaves everything else at defaults
                          in the generated .env.

Idempotent guard: refuses to overwrite an existing .env. Delete .env first
to regenerate. Run from the project root.

Exit codes:
    0   success or --help
    1   missing .env.example, or .env already exists
"""
import os
import sys
import secrets
import base64
from datetime import datetime
from urllib.parse import urlparse


_HELP_TEXT = __doc__ or ""


def _print_help_and_exit() -> None:
    sys.stdout.write(_HELP_TEXT + "\n")
    sys.exit(0)


if any(arg in ("-h", "--help") for arg in sys.argv[1:]):
    _print_help_and_exit()


def _parse_mode_arg() -> str | None:
    """Return the mode number chosen via ``--mode=1|2`` or ``None`` if not set.

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
            if value not in ("1", "2"):
                print(f"Invalid --mode value: {value!r}. Use 1 (Full) or 2 (Keys Only).")
                sys.exit(1)
            return value
        if a == "--mode":
            if i + 1 >= len(args) or args[i + 1] not in ("1", "2"):
                print("--mode requires a value: 1 (Full) or 2 (Keys Only).")
                sys.exit(1)
            return args[i + 1]
        i += 1
    return None


_PRESET_MODE = _parse_mode_arg()


def _derive_email_default(app_url: str) -> str:
    """Derive a sensible VAPID contact email default from the APP_URL hostname.

    For ``https://health.example.com`` → ``admin@health.example.com``.
    For ``http://localhost:3000`` → ``admin@example.com`` (localhost isn't a
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


def generate_vapid_keys():
    """Generate a VAPID P-256 key pair for Web Push.

    Returns ``(public_key_b64, private_key_b64)`` as base64url strings
    without padding, matching the format produced by
    ``npx web-push generate-vapid-keys`` and consumed by ``pywebpush`` /
    the browser ``PushManager.subscribe`` API. Uses the ``cryptography``
    package (already a dependency via Fernet) — no Node.js / pywebpush
    CLI needed.
    """
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives import serialization

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
print("  1) Full Setup (Interactively configure environments, URLs, and workers)")
print("  2) Keys Only Setup (Just generate keys, I'll manually configure the rest later)")

setup_mode = _PRESET_MODE or prompt("\nSelect setup mode", default="1", options=["1", "2"])

# Default configs
config = {
    "SECRET_KEY": secret_key,
    "POSTGRES_PASSWORD": postgres_password,
    "FLOWER_PASSWORD": flower_password,
    "REDIS_PASSWORD": redis_password,
    "INTEGRATION_SECRET_KEY": integration_secret_key,
    "VAPID_PUBLIC_KEY": vapid_public_key,
    "VAPID_PRIVATE_KEY": vapid_private_key,
    "APP_ENV": "development",
    "DEBUG": "true",
    "APP_URL": "http://localhost:3000",
    "CELERY_WORKER_CONCURRENCY": "2",
    # Dev tooling — demo credentials for UI screenshot capture
    # (see scripts/capture_ui.sh + backend/scripts/seed_demo.py).
    "HA_DEMO_EMAIL": "demo@healthassistant.local",
    "HA_DEMO_PASSWORD": "Demo1234!",
}

# Full interactive overrides
if setup_mode == "1":
    print("\n--- Full Configuration ---")
    
    print("\nChoose your environment type:")
    print("  1) development (Default)")
    print("  2) production")
    env_choice_num = prompt("Select environment", default="1", options=["1", "2"])
    
    env_choice = "production" if env_choice_num == "2" else "development"
    config["APP_ENV"] = env_choice
    
    # Intelligently default DEBUG based on environment
    default_debug = "n" if env_choice == "production" else "y"
    config["DEBUG"] = prompt_bool("Enable Debug mode? (Set 'n' for production)", default=default_debug)
    
    print("\nApp URL is the public base URL of the deployment (e.g., https://health.example.com).")
    print("This is required for OAuth redirects, Web Push notifications, and external integrations.")
    config["APP_URL"] = prompt("Public App URL", default="http://localhost:3000")

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
    worker_concurrency = prompt("Worker concurrency", default="2")
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
    mode_choice = prompt("Select setup-token mode", default="1", options=["1", "2", "3", "4"])
    mode_map = {"1": "log", "2": "env", "3": "time", "4": "disabled"}
    config["SETUP_TOKEN_MODE"] = mode_map[mode_choice]
    if mode_choice == "2":
        bootstrap_token = secrets.token_urlsafe(24)
        config["SETUP_BOOTSTRAP_TOKEN"] = bootstrap_token
        print("\n✨ Generated SETUP_BOOTSTRAP_TOKEN. Compose your launcher URL as:")
        print(f"   {config['APP_URL']}/setup?token={bootstrap_token}")
        print("   The wizard auto-fills the token; the user clicks once and is done.")
    elif mode_choice == "3":
        grace = prompt(
            "Tokenless grace window (minutes)", default="30"
        )
        if not grace.isdigit() or int(grace) < 1:
            print("Invalid number provided, defaulting to 30.")
            grace = "30"
        config["SETUP_TOKEN_GRACE_MINUTES"] = grace

print("\nGenerating .env file...")

try:
    with open(".env.example", "r") as example_file:
        lines = example_file.readlines()
        
    with open(".env", "w") as env_file:
        env_file.write(f"# Auto-generated by scripts/setup_env.py on {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
        
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
                if line.startswith(f"# {key}=") or line.startswith(f"#{key}="):
                    env_file.write(f"{key}={val}\n")
                    replaced = True
                    break
            
            # If not a managed key, just write the original line
            if not replaced:
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
        print("✨ Web Push contact email (VAPID_ADMIN_EMAIL) also configured —")
        print("   no further VAPID setup needed.")
    
    if setup_mode == "1":
        print("✨ Your custom configurations have also been saved.")
        
        if env_choice == "production":
            print("\n⚠️  Next steps (Production):")
            print("   1. Please review the 'Production Deployment' section in docs/INSTALL.md")
            print("      (or https://health-assistant.io/docs/install#production-deployment)")
            print("   2. Once ready, you can start the application with:")
            print("      docker compose --env-file .env -f docker/docker-compose.prod.yml up -d")
        else:
            print("\n🚀 You can now start the application with:")
            print("   docker compose --env-file .env -f docker/docker-compose.dev.yml up -d")
    else:
        print("\n⚠️  Next steps:")
        print("   Please open the newly created '.env' file in your text editor and review")
        print("   the remaining configurations (such as APP_URL, ports, or optional settings).")
        print("   Once configured, refer to docs/INSTALL.md or https://health-assistant.io/docs/install")
        print("   for the correct start commands based on your environment.")

except Exception as e:
    print(f"\n❌ Error during setup: {e}")
    sys.exit(1)
