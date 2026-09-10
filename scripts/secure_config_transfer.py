#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
🔐 Secure Config Transfer — encrypt a VLESS config for safe hand-off

Encrypts vless_client_config.json so it can travel over
insecure channels (email, messengers, cloud storage).

Uses AES-256-GCM (compatible with SecureMessenger from encryption.py).

Usage:
    # Encrypt
    python3 secure_config_transfer.py encrypt config.json
    python3 secure_config_transfer.py encrypt config.json --output encrypted.bin

    # Decrypt
    python3 secure_config_transfer.py decrypt encrypted.bin
    python3 secure_config_transfer.py decrypt encrypted.bin --output config.json

    # With a given password
    python3 secure_config_transfer.py encrypt config.json --password "mypass"

    # Generate a password
    python3 secure_config_transfer.py generate-password
"""

import os
import sys
import json
import base64
import hashlib
import argparse
import getpass
from pathlib import Path
from typing import Tuple, Optional

try:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
except ImportError:
    print("❌ cryptography library is required")
    print("   pip install cryptography")
    sys.exit(1)


# ═══════════════════════════════════════════════════════════════
# Constants
# ═══════════════════════════════════════════════════════════════

NONCE_SIZE = 12  # 96 bits for AES-GCM (NIST standard)
SALT_SIZE = 16   # For PBKDF2
ITERATIONS = 100_000  # PBKDF2 iterations

# Magic bytes to identify the format
MAGIC_BYTES = b'VLESS_ENC_V1'


# ═══════════════════════════════════════════════════════════════
# Crypto helpers
# ═══════════════════════════════════════════════════════════════

def derive_key(password: str, salt: bytes) -> bytes:
    """
    Derive a 256-bit key from a password using PBKDF2-SHA256.

    Args:
        password: User password
        salt: Random salt (16 bytes)

    Returns:
        32-byte key for AES-256
    """
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        iterations=ITERATIONS,
    )

    return kdf.derive(password.encode('utf-8'))


def encrypt_data(data: bytes, password: str) -> bytes:
    """
    Encrypt data with AES-256-GCM.

    Output format:
    [MAGIC_BYTES(12)][SALT(16)][NONCE(12)][CIPHERTEXT+TAG]

    Args:
        data: Data to encrypt
        password: Password

    Returns:
        Encrypted packet
    """
    # Generate salt and nonce
    salt = os.urandom(SALT_SIZE)
    nonce = os.urandom(NONCE_SIZE)

    # Derive key from password
    key = derive_key(password, salt)

    # Encrypt
    aesgcm = AESGCM(key)
    ciphertext = aesgcm.encrypt(nonce, data, None)

    # Assemble packet
    return MAGIC_BYTES + salt + nonce + ciphertext


def decrypt_data(encrypted: bytes, password: str) -> bytes:
    """
    Decrypt AES-256-GCM data.

    Args:
        encrypted: Encrypted packet
        password: Password

    Returns:
        Decrypted data

    Raises:
        ValueError: On wrong format or password
    """
    # Check magic bytes
    if not encrypted.startswith(MAGIC_BYTES):
        raise ValueError("Invalid file format (not VLESS_ENC_V1)")

    # Extract components
    offset = len(MAGIC_BYTES)
    salt = encrypted[offset:offset + SALT_SIZE]
    offset += SALT_SIZE
    nonce = encrypted[offset:offset + NONCE_SIZE]
    offset += NONCE_SIZE
    ciphertext = encrypted[offset:]

    # Derive key from password
    key = derive_key(password, salt)

    # Decrypt
    try:
        aesgcm = AESGCM(key)
        return aesgcm.decrypt(nonce, ciphertext, None)
    except Exception as e:
        raise ValueError("Wrong password or corrupted data") from e


def generate_password(length: int = 24) -> str:
    """
    Generate a cryptographically strong password.

    Args:
        length: Password length (default 24)

    Returns:
        Safe password
    """
    import secrets
    import string

    # Letters, digits, and a few specials
    alphabet = string.ascii_letters + string.digits + "-_!@#"
    return ''.join(secrets.choice(alphabet) for _ in range(length))


# ═══════════════════════════════════════════════════════════════
# CLI commands
# ═══════════════════════════════════════════════════════════════

def cmd_encrypt(args):
    """Encrypt command"""
    input_path = Path(args.input)

    if not input_path.exists():
        print(f"❌ File not found: {input_path}")
        return 1

    # Output file
    if args.output:
        output_path = Path(args.output)
    else:
        output_path = input_path.with_suffix('.enc')

    # Password
    if args.password:
        password = args.password
    else:
        password = getpass.getpass("🔑 Enter encryption password: ")
        password2 = getpass.getpass("🔑 Confirm password: ")

        if password != password2:
            print("❌ Passwords do not match!")
            return 1

    if len(password) < 8:
        print("⚠️  Warning: password is too short (12+ characters recommended)")

    # Read and encrypt
    print(f"📄 Reading: {input_path}")
    data = input_path.read_bytes()

    print("🔐 Encrypting...")
    encrypted = encrypt_data(data, password)

    # Write
    output_path.write_bytes(encrypted)

    # Also write a base64 version for easier transfer
    b64_path = output_path.with_suffix('.enc.txt')
    b64_data = base64.b64encode(encrypted).decode('utf-8')
    b64_path.write_text(b64_data)

    print()
    print("═══════════════════════════════════════════════════════════════")
    print("   ✅ File encrypted!")
    print("═══════════════════════════════════════════════════════════════")
    print()
    print(f"📦 Binary:  {output_path} ({len(encrypted)} bytes)")
    print(f"📝 Base64:    {b64_path} ({len(b64_data)} chars)")
    print()
    print("💡 To decrypt:")
    print(f"   python3 {sys.argv[0]} decrypt {output_path}")
    print()
    print("⚠️  IMPORTANT: Send the password SEPARATELY from the file!")
    print("   e.g. password by phone, file by email")

    return 0


def cmd_decrypt(args):
    """Decrypt command"""
    input_path = Path(args.input)

    if not input_path.exists():
        print(f"❌ File not found: {input_path}")
        return 1

    # Output file
    if args.output:
        output_path = Path(args.output)
    else:
        # Strip .enc or .enc.txt
        name = input_path.stem
        if name.endswith('.enc'):
            name = name[:-4]
        output_path = input_path.parent / f"{name}_decrypted.json"

    # Password
    if args.password:
        password = args.password
    else:
        password = getpass.getpass("🔑 Enter decryption password: ")

    # Read file
    print(f"📄 Reading: {input_path}")
    data = input_path.read_bytes()

    # Maybe it is base64
    if not data.startswith(MAGIC_BYTES):
        try:
            # Try decoding base64
            data = base64.b64decode(data)
        except Exception:
            pass

    # Decrypt
    print("🔓 Decrypting...")
    try:
        decrypted = decrypt_data(data, password)
    except ValueError as e:
        print(f"❌ {e}")
        return 1

    # Write
    output_path.write_bytes(decrypted)

    print()
    print("═══════════════════════════════════════════════════════════════")
    print("   ✅ File decrypted!")
    print("═══════════════════════════════════════════════════════════════")
    print()
    print(f"📄 Saved: {output_path}")

    # Show contents if JSON
    try:
        config = json.loads(decrypted)
        print()
        print("📋 Config contents:")
        print("───────────────────────────────────────────────────────────────")

        if 'vless_link' in config:
            print(f"🔗 VLESS Link: {config['vless_link'][:50]}...")
        if 'server' in config:
            print(f"📍 Server: {config['server']}:{config.get('port', 443)}")
        if 'uuid' in config:
            uuid = config['uuid']
            print(f"🆔 UUID: {uuid[:8]}...{uuid[-4:]}")

    except json.JSONDecodeError:
        pass

    return 0


def cmd_generate_password(args):
    """Generate-password command"""
    password = generate_password(args.length)

    print()
    print("═══════════════════════════════════════════════════════════════")
    print("   🔑 Generated a safe password")
    print("═══════════════════════════════════════════════════════════════")
    print()
    print(f"   {password}")
    print()
    print("💡 Use with --password when encrypting:")
    print(f"   python3 {sys.argv[0]} encrypt config.json --password '{password}'")

    return 0


def cmd_info(args):
    """Show info about an encrypted file"""
    input_path = Path(args.input)

    if not input_path.exists():
        print(f"❌ File not found: {input_path}")
        return 1

    data = input_path.read_bytes()

    # Check base64
    is_base64 = False
    if not data.startswith(MAGIC_BYTES):
        try:
            data = base64.b64decode(data)
            is_base64 = True
        except Exception:
            print("❌ File is not an encrypted VLESS config")
            return 1

    if not data.startswith(MAGIC_BYTES):
        print("❌ File is not an encrypted VLESS config")
        return 1

    # Extract metadata
    offset = len(MAGIC_BYTES)
    salt = data[offset:offset + SALT_SIZE]
    offset += SALT_SIZE
    nonce = data[offset:offset + NONCE_SIZE]
    offset += NONCE_SIZE
    ciphertext_len = len(data) - offset

    print()
    print("═══════════════════════════════════════════════════════════════")
    print("   📦 Encrypted file info")
    print("═══════════════════════════════════════════════════════════════")
    print()
    print(f"📄 File: {input_path}")
    print(f"📊 Size: {len(data)} bytes")
    print(f"🏷️  Format: VLESS_ENC_V1")
    print(f"🔤 Base64: {'Yes' if is_base64 else 'No'}")
    print()
    print("🔐 Crypto:")
    print(f"   • Algorithm: AES-256-GCM")
    print(f"   • KDF: PBKDF2-SHA256 ({ITERATIONS:,} iterations)")
    print(f"   • Salt: {SALT_SIZE} bytes")
    print(f"   • Nonce: {NONCE_SIZE} bytes")
    print(f"   • Ciphertext: {ciphertext_len} bytes")

    return 0


# ═══════════════════════════════════════════════════════════════
# Entry point
# ═══════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(
        description="🔐 Secure transfer of a VLESS configuration",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s encrypt vless_config.json
  %(prog)s decrypt vless_config.enc
  %(prog)s generate-password
  %(prog)s info vless_config.enc
        """
    )

    subparsers = parser.add_subparsers(dest='command', help='Command')

    # encrypt
    enc_parser = subparsers.add_parser('encrypt', help='Encrypt a config file')
    enc_parser.add_argument('input', help='Input JSON file')
    enc_parser.add_argument('--output', '-o', help='Output file (default: input.enc)')
    enc_parser.add_argument('--password', '-p', help='Password (or you will be prompted)')

    # decrypt
    dec_parser = subparsers.add_parser('decrypt', help='Decrypt a file')
    dec_parser.add_argument('input', help='Encrypted file (.enc or .enc.txt)')
    dec_parser.add_argument('--output', '-o', help='Output file')
    dec_parser.add_argument('--password', '-p', help='Password (or you will be prompted)')

    # generate-password
    gen_parser = subparsers.add_parser('generate-password', help='Generate a safe password')
    gen_parser.add_argument('--length', '-l', type=int, default=24, help='Password length (default: 24)')

    # info
    info_parser = subparsers.add_parser('info', help='Info about an encrypted file')
    info_parser.add_argument('input', help='Encrypted file')

    args = parser.parse_args()

    if args.command == 'encrypt':
        return cmd_encrypt(args)
    elif args.command == 'decrypt':
        return cmd_decrypt(args)
    elif args.command == 'generate-password':
        return cmd_generate_password(args)
    elif args.command == 'info':
        return cmd_info(args)
    else:
        parser.print_help()
        return 0


if __name__ == '__main__':
    sys.exit(main())
