#!/usr/bin/env python3
"""Export cookies from your Chrome profile for paywalled site access.

Reads cookies directly from Chrome's cookie database — no browser window
needed, no bot detection issues. Works while Chrome is running.

Usage:
    uv sync --extra scraping
    uv run python scripts/save_browser_auth.py
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

AUTH_STATE_FILE = Path(__file__).parent.parent / "data" / "browser_auth.json"
CHROME_USER_DATA = Path.home() / "Library" / "Application Support" / "Google" / "Chrome"
CHROME_PROFILE = "Default"

# Domains to export cookies for (leading dot = include subdomains)
COOKIE_DOMAINS = [
    ".wsj.com",
    ".dowjones.com",
    ".nytimes.com",
]

# Chrome epoch: microseconds from 1601-01-01 to Unix epoch (1970-01-01)
_CHROME_EPOCH_OFFSET = 11644473600


def _get_chrome_key() -> bytes:
    """Get Chrome's cookie encryption key from macOS Keychain."""
    result = subprocess.run(
        ["security", "find-generic-password", "-s", "Chrome Safe Storage", "-w"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        print("Failed to read Chrome Safe Storage key from Keychain.")
        print("You may need to click 'Allow' when macOS prompts for Keychain access.")
        sys.exit(1)

    password = result.stdout.strip().encode()
    # Chrome derives a 16-byte AES key via PBKDF2-SHA1
    return hashlib.pbkdf2_hmac("sha1", password, b"saltysalt", 1003, dklen=16)


def _decrypt_cookie(encrypted: bytes, key: bytes) -> str:
    """Decrypt a Chrome cookie value (v10 AES-128-CBC on macOS)."""
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

    if not encrypted:
        return ""

    # v10 prefix = macOS Chrome AES encryption
    if encrypted[:3] != b"v10":
        return encrypted.decode("utf-8", errors="replace")

    encrypted = encrypted[3:]
    if len(encrypted) < 16:
        return ""

    cipher = Cipher(algorithms.AES128(key), modes.CBC(b" " * 16))
    decryptor = cipher.decryptor()
    decrypted = decryptor.update(encrypted) + decryptor.finalize()

    # Strip PKCS7 padding
    pad_len = decrypted[-1]
    if 1 <= pad_len <= 16:
        return decrypted[:-pad_len].decode("utf-8", errors="replace")
    return ""


def main() -> None:
    try:
        import cryptography  # noqa: F401
    except ImportError:
        print("cryptography package required. Run: uv sync --extra scraping")
        sys.exit(1)

    cookies_db = CHROME_USER_DATA / CHROME_PROFILE / "Cookies"
    if not cookies_db.exists():
        print(f"Chrome cookies database not found: {cookies_db}")
        sys.exit(1)

    AUTH_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)

    # Copy the database to a temp file (Chrome may have it locked via WAL)
    tmp_dir = tempfile.mkdtemp()
    tmp_db = Path(tmp_dir) / "Cookies"
    shutil.copy2(cookies_db, tmp_db)
    for ext in ("-wal", "-shm"):
        src = Path(str(cookies_db) + ext)
        if src.exists():
            shutil.copy2(src, Path(str(tmp_db) + ext))

    key = _get_chrome_key()

    # Query cookies for target domains
    domain_clauses = " OR ".join(f"host_key LIKE '%{d}'" for d in COOKIE_DOMAINS)
    conn = sqlite3.connect(str(tmp_db))
    rows = conn.execute(
        f"SELECT host_key, name, encrypted_value, path, "
        f"is_secure, is_httponly, expires_utc, samesite "
        f"FROM cookies WHERE {domain_clauses}"
    ).fetchall()
    conn.close()

    # Clean up temp files
    shutil.rmtree(tmp_dir, ignore_errors=True)

    samesite_map = {-1: "None", 0: "None", 1: "Lax", 2: "Strict"}
    cookies = []
    for host, name, enc_value, path, secure, httponly, expires_utc, samesite in rows:
        value = _decrypt_cookie(enc_value, key)

        # Convert Chrome timestamp (microseconds since 1601) to Unix epoch
        if expires_utc and expires_utc > 0:
            expires_unix = (expires_utc / 1_000_000) - _CHROME_EPOCH_OFFSET
        else:
            expires_unix = -1  # session cookie

        cookies.append({
            "name": name,
            "value": value,
            "domain": host,
            "path": path or "/",
            "expires": expires_unix,
            "httpOnly": bool(httponly),
            "secure": bool(secure),
            "sameSite": samesite_map.get(samesite, "None"),
        })

    if not cookies:
        print("No cookies found for target domains.")
        print(f"Searched: {', '.join(COOKIE_DOMAINS)}")
        print("Make sure you're logged into these sites in Chrome.")
        sys.exit(1)

    # Save in Playwright storage_state format
    state = {"cookies": cookies, "origins": []}
    with open(AUTH_STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)
    os.chmod(AUTH_STATE_FILE, 0o600)

    domains_found = sorted({c["domain"] for c in cookies})
    print(f"Exported {len(cookies)} cookies from Chrome ({CHROME_PROFILE} profile)")
    print(f"Domains: {', '.join(domains_found)}")
    print(f"Saved to: {AUTH_STATE_FILE}")


if __name__ == "__main__":
    main()
