#!/usr/bin/env python3
import os
import sys
import secrets
import base64
from datetime import datetime

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

if not os.path.exists("docker/.env.example"):
    print("\n❌ Error: docker/.env.example not found.")
    print("Please run this script from the root of the repository.")
    sys.exit(1)

# Generate secure keys (always done)
secret_key = secrets.token_urlsafe(48)
postgres_password = secrets.token_urlsafe(24)
flower_password = secrets.token_urlsafe(24)

# Generate a valid Fernet key (32 bytes, base64url encoded)
fernet_key_bytes = os.urandom(32)
integration_secret_key = base64.urlsafe_b64encode(fernet_key_bytes).decode('utf-8')

# Interactive Setup Choice
print("\nThis script will automatically generate secure cryptographic keys for your installation.")
print("How would you like to configure the rest of the environment?")
print("  1) Full Setup (Interactively configure environments, URLs, and workers)")
print("  2) Keys Only Setup (Just generate keys, I'll manually configure the rest later)")

setup_mode = prompt("\nSelect setup mode", default="1", options=["1", "2"])

# Default configs
config = {
    "SECRET_KEY": secret_key,
    "POSTGRES_PASSWORD": postgres_password,
    "FLOWER_PASSWORD": flower_password,
    "INTEGRATION_SECRET_KEY": integration_secret_key,
    "APP_ENV": "development",
    "DEBUG": "true",
    "APP_URL": "http://localhost:3000",
    "CELERY_WORKER_CONCURRENCY": "2"
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

print("\nGenerating .env file...")

try:
    with open("docker/.env.example", "r") as example_file:
        lines = example_file.readlines()
        
    with open(".env", "w") as env_file:
        env_file.write(f"# Auto-generated by scripts/setup_env.py on {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
        
        for line in lines:
            # We check and replace known keys
            replaced = False
            for key, val in config.items():
                if line.startswith(f"{key}="):
                    env_file.write(f"{key}={val}\n")
                    replaced = True
                    break
            
            # If not a managed key, just write the original line
            if not replaced:
                env_file.write(line)

    print("\n✅ Environment configured successfully!")
    print("✨ Secure keys have been automatically generated for:")
    print("   - SECRET_KEY")
    print("   - INTEGRATION_SECRET_KEY")
    print("   - POSTGRES_PASSWORD")
    print("   - FLOWER_PASSWORD")
    
    if setup_mode == "1":
        print("✨ Your custom configurations have also been saved.")
        
        if env_choice == "production":
            print("\n⚠️  Next steps (Production):")
            print("   1. Please review the 'Production Deployment' section in docs/INSTALL.md")
            print("      (or https://health-assistant.io/docs/install#production-deployment)")
            print("   2. Once ready, you can start the application with:")
            print("      docker compose -f docker/docker-compose.prod.yml up -d")
        else:
            print("\n🚀 You can now start the application with:")
            print("   docker compose -f docker/docker-compose.yml up -d")
    else:
        print("\n⚠️  Next steps:")
        print("   Please open the newly created '.env' file in your text editor and review")
        print("   the remaining configurations (such as APP_URL, ports, or optional settings).")
        print("   Once configured, refer to docs/INSTALL.md or https://health-assistant.io/docs/install")
        print("   for the correct start commands based on your environment.")

except Exception as e:
    print(f"\n❌ Error during setup: {e}")
    sys.exit(1)
