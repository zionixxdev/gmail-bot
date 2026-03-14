"""
generate_key.py — One-time utility to generate a Fernet encryption key.

Run this ONCE before first deployment and copy the output into your .env:
    ENCRYPTION_KEY=<output>

Usage:
    python generate_key.py
"""

from cryptography.fernet import Fernet

key = Fernet.generate_key().decode()
print("=" * 60)
print("Your ENCRYPTION_KEY (copy to .env):")
print()
print(key)
print()
print("=" * 60)
print("⚠️  Store this key securely. Losing it means losing access")
print("   to all encrypted refresh tokens in the database.")
