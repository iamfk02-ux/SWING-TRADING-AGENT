"""
Config & Secrets Loader:
Loads settings.yaml and merges local uncommitted secrets:
1. config/settings.yaml (base public configuration)
2. config/settings.local.yaml (optional local overrides, git-ignored)
3. .env file in root directory (optional environment variables, git-ignored)
4. System environment variables (TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, MT5_LOGIN, MT5_PASSWORD, MT5_SERVER)
"""

import os
from pathlib import Path
from typing import Dict, Any, Optional
import yaml

ROOT_DIR = Path(__file__).resolve().parent.parent
BASE_CONFIG_PATH = ROOT_DIR / "config" / "settings.yaml"
LOCAL_CONFIG_PATH = ROOT_DIR / "config" / "settings.local.yaml"
ENV_PATH = ROOT_DIR / ".env"


def _parse_env_file(env_path: Path):
    """Simple parser for .env files without requiring third-party libraries."""
    if not env_path.exists():
        return
    try:
        with open(env_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                k = k.strip()
                v = v.strip().strip("'\"")
                if k:
                    os.environ[k] = v
    except Exception:
        pass


def _deep_merge(base: dict, override: dict) -> dict:
    """Recursively merges override dictionary into base."""
    merged = base.copy()
    for k, v in override.items():
        if k in merged and isinstance(merged[k], dict) and isinstance(v, dict):
            merged[k] = _deep_merge(merged[k], v)
        else:
            merged[k] = v
    return merged


def load_config(config_path: Optional[Path] = None) -> Dict[str, Any]:
    """
    Loads consolidated configuration with all secrets safely applied.
    """
    path_to_use = config_path or BASE_CONFIG_PATH
    config: Dict[str, Any] = {}

    if path_to_use.exists():
        with open(path_to_use, "r", encoding="utf-8") as f:
            config = yaml.safe_load(f) or {}

    # 1. Merge settings.local.yaml if present
    if LOCAL_CONFIG_PATH.exists():
        try:
            with open(LOCAL_CONFIG_PATH, "r", encoding="utf-8") as f:
                local_cfg = yaml.safe_load(f) or {}
                if isinstance(local_cfg, dict):
                    config = _deep_merge(config, local_cfg)
        except Exception:
            pass

    # 2. Parse .env file into environment
    _parse_env_file(ENV_PATH)

    # 3. Apply Environment Variable Overrides
    tg_token = os.environ.get("TELEGRAM_BOT_TOKEN")
    tg_chat = os.environ.get("TELEGRAM_CHAT_ID")
    if tg_token or tg_chat:
        config.setdefault("telegram", {})
        if tg_token:
            config["telegram"]["bot_token"] = tg_token
        if tg_chat:
            config["telegram"]["chat_id"] = tg_chat

    mt5_login = os.environ.get("MT5_LOGIN")
    mt5_pass = os.environ.get("MT5_PASSWORD")
    mt5_server = os.environ.get("MT5_SERVER")
    mt5_path = os.environ.get("MT5_TERMINAL_PATH")
    if mt5_login or mt5_pass or mt5_server or mt5_path:
        config.setdefault("mt5", {})
        if mt5_login:
            try:
                config["mt5"]["login"] = int(mt5_login)
            except ValueError:
                config["mt5"]["login"] = mt5_login
        if mt5_pass:
            config["mt5"]["password"] = mt5_pass
        if mt5_server:
            config["mt5"]["server"] = mt5_server
        if mt5_path:
            config["mt5"]["terminal_path"] = mt5_path

    return config
