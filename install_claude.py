#!/usr/bin/env python3
"""
Auto-installer for Google Workspace MCP in Claude Desktop
Enhanced version with OAuth configuration and installation options
"""

import json
import os
import platform
import sys
from pathlib import Path
from typing import Dict, Optional, Tuple


def get_claude_config_path() -> Path:
    """Get the Claude Desktop config file path for the current platform."""
    system = platform.system()
    if system == "Darwin":  # macOS
        return Path.home() / "Library/Application Support/Claude/claude_desktop_config.json"
    elif system == "Windows":
        appdata = os.environ.get("APPDATA")
        if not appdata:
            raise RuntimeError("APPDATA environment variable not found")
        return Path(appdata) / "Claude/claude_desktop_config.json"
    else:
        raise RuntimeError(f"Unsupported platform: {system}")


def prompt_yes_no(question: str, default: bool = True) -> bool:
    """Prompt user for yes/no question."""
    default_str = "Y/n" if default else "y/N"
    while True:
        response = input(f"{question} [{default_str}]: ").strip().lower()
        if not response:
            return default
        if response in ['y', 'yes']:
            return True
        if response in ['n', 'no']:
            return False
        print("Please answer 'y' or 'n'")


def get_oauth_credentials() -> Tuple[Optional[Dict[str, str]], Optional[str]]:
    """Get OAuth credentials from user."""
    print("\n🔑 OAuth Credentials Setup")
    print("You need Google OAuth 2.0 credentials to use this server.")
    print("\nYou can provide credentials in two ways:")
    print("1. Environment variables (recommended for production)")
    print("2. Client secrets JSON file")

    use_env = prompt_yes_no("\nDo you want to use environment variables?", default=True)

    env_vars = {}
    client_secret_path = None

    if use_env:
        print("\n📝 Enter your OAuth credentials:")
        client_id = input("Client ID (ends with .apps.googleusercontent.com): ").strip()
        client_secret = input("Client Secret: ").strip()

        if not client_id or not client_secret:
            print("❌ Both Client ID and Client Secret are required!")
            return None, None

        env_vars["GOOGLE_OAUTH_CLIENT_ID"] = client_id
        env_vars["GOOGLE_OAUTH_CLIENT_SECRET"] = client_secret

        # Optional redirect URI
        custom_redirect = input("Redirect URI (press Enter for default http://localhost:8000/oauth2callback): ").strip()
        if custom_redirect:
            env_vars["GOOGLE_OAUTH_REDIRECT_URI"] = custom_redirect

    else:
        print("\n📁 Client secrets file setup:")
        default_path = "client_secret.json"
        file_path = input(f"Path to client_secret.json file [{default_path}]: ").strip()

        if not file_path:
            file_path = default_path

        # Check if file exists
        if not Path(file_path).exists():
            print(f"❌ File not found: {file_path}")
            return None, None

        client_secret_path = file_path

    # Optional: Default user email
    print("\n📧 Optional: Default user email (for single-user setups)")
    user_email = input("Your Google email (press Enter to skip): ").strip()
    if user_email:
        env_vars["USER_GOOGLE_EMAIL"] = user_email

    # Development mode
    if prompt_yes_no("\n🔧 Enable development mode (OAUTHLIB_INSECURE_TRANSPORT)?", default=False):
        env_vars["OAUTHLIB_INSECURE_TRANSPORT"] = "1"

    return env_vars, client_secret_path


def get_installation_options() -> Dict[str, any]:
    """Get installation options from user."""
    options = {}

    print("\n⚙️  Installation Options")

    # Installation method
    print("\nChoose installation method:")
    print("1. uvx (recommended - auto-installs from PyPI)")
    print("2. Development mode (requires local repository)")

    method = input("Select method [1]: ").strip()
    if method == "2":
        options["dev_mode"] = True
        cwd = input("Path to google_workspace_mcp repository [current directory]: ").strip()
        options["cwd"] = cwd if cwd else os.getcwd()
    else:
        options["dev_mode"] = False

    # Single-user mode
    if prompt_yes_no("\n👤 Enable single-user mode (simplified authentication)?", default=False):
        options["single_user"] = True

    # Tool selection
    print("\n🛠️  Tool Selection")
    print("Available tools: gmail, drive, calendar, docs, sheets, forms, chat")
    print("Leave empty to enable all tools")
    tools = input("Enter tools to enable (comma-separated): ").strip()
    if tools:
        options["tools"] = [t.strip() for t in tools.split(",")]

    # Transport mode
    if prompt_yes_no("\n🌐 Use HTTP transport mode (for debugging)?", default=False):
        options["http_mode"] = True

    return options


def create_server_config(options: Dict, env_vars: Dict, client_secret_path: Optional[str]) -> Dict:
    """Create the server configuration."""
    config = {}

    if options.get("dev_mode"):
        config["command"] = "uv"
        config["args"] = ["run", "--directory", options["cwd"], "main.py"]
    else:
        config["command"] = "uvx"
        config["args"] = ["workspace-mcp"]

    # Add command line arguments
    if options.get("single_user"):
        config["args"].append("--single-user")

    if options.get("tools"):
        config["args"].extend(["--tools"] + options["tools"])

    if options.get("http_mode"):
        config["args"].extend(["--transport", "streamable-http"])

    # Add environment variables
    if env_vars or client_secret_path:
        config["env"] = {}

    if env_vars:
        config["env"].update(env_vars)

    if client_secret_path:
        config["env"]["GOOGLE_CLIENT_SECRET_PATH"] = client_secret_path

    return config


def main():
    print("🚀 Google Workspace MCP Installer for Claude Desktop")
    print("=" * 50)

    try:
        config_path = get_claude_config_path()

        # Check if config already exists
        existing_config = {}
        if config_path.exists():
            with open(config_path, 'r') as f:
                existing_config = json.load(f)

            if "mcpServers" in existing_config and "Google Workspace" in existing_config["mcpServers"]:
                print(f"\n⚠️  Google Workspace MCP is already configured in {config_path}")
                if not prompt_yes_no("Do you want to reconfigure it?", default=True):
                    print("Installation cancelled.")
                    return

        # Get OAuth credentials
        env_vars, client_secret_path = get_oauth_credentials()
        if env_vars is None and client_secret_path is None:
            print("\n❌ OAuth credentials are required. Installation cancelled.")
            sys.exit(1)

        # Get installation options
        options = get_installation_options()

        # Create server configuration
        server_config = create_server_config(options, env_vars, client_secret_path)

        # Prepare final config
        if "mcpServers" not in existing_config:
            existing_config["mcpServers"] = {}

        existing_config["mcpServers"]["Google Workspace"] = server_config

        # Create directory if needed
        config_path.parent.mkdir(parents=True, exist_ok=True)

        # Write configuration
        with open(config_path, 'w') as f:
            json.dump(existing_config, f, indent=2)

        print("\n✅ Successfully configured Google Workspace MCP!")
        print(f"📁 Config file: {config_path}")

        print("\n📋 Configuration Summary:")
        print(f"  • Installation method: {'Development' if options.get('dev_mode') else 'uvx (PyPI)'}")
        print(f"  • Authentication: {'Environment variables' if env_vars else 'Client secrets file'}")
        if options.get("single_user"):
            print("  • Single-user mode: Enabled")
        if options.get("tools"):
            print(f"  • Tools: {', '.join(options['tools'])}")
        else:
            print("  • Tools: All enabled")
        if options.get("http_mode"):
            print("  • Transport: HTTP mode")
        else:
            print("  • Transport: stdio (default)")

        print("\n🚀 Next steps:")
        print("1. Restart Claude Desktop")
        print("2. The Google Workspace tools will be available in your chats!")
        print("\n💡 The server will start automatically when Claude Desktop needs it.")

        if options.get("http_mode"):
            print("\n⚠️  Note: HTTP mode requires additional setup.")
            print("   You may need to install and configure mcp-remote.")
            print("   See the README for details.")

    except KeyboardInterrupt:
        print("\n\nInstallation cancelled by user.")
        sys.exit(0)
    except Exception as e:
        print(f"\n❌ Error: {e}")
        print("\n📋 Manual installation:")
        print("1. Open Claude Desktop Settings → Developer → Edit Config")
        print("2. Add the server configuration shown in the README")
        sys.exit(1)


if __name__ == "__main__":
    main()