#!/usr/bin/env python3
"""
Quick script to check if environment variables are being loaded correctly
"""
import os
from pathlib import Path
from api.config import settings

print("=" * 60)
print("Environment Variable Check")
print("=" * 60)

# Check if .env file exists
env_file = Path(".env")
if env_file.exists():
    print(f"✅ .env file exists: {env_file.absolute()}")
else:
    print(f"❌ .env file NOT found at: {env_file.absolute()}")

print()

# Check from environment
env_key = os.getenv("OPENAI_API_KEY")
print(f"From os.getenv('OPENAI_API_KEY'):")
if env_key:
    print(f"  ✅ Found (length: {len(env_key)})")
    print(f"  Starts with: {env_key[:10]}...")
else:
    print(f"  ❌ Not set in environment")

print()

# Check from settings
settings_key = settings.OPENAI_API_KEY
print(f"From settings.OPENAI_API_KEY:")
if settings_key:
    print(f"  ✅ Found (length: {len(settings_key)})")
    print(f"  Starts with: {settings_key[:10]}...")
    if len(settings_key) < 20:
        print(f"  ⚠️  WARNING: Key seems too short (should be ~50+ characters)")
else:
    print(f"  ❌ Not set or empty")
    print(f"  ⚠️  This is why you're getting 401 errors!")

print()

# Check .env file content (without showing the key)
if env_file.exists():
    with open(env_file) as f:
        lines = f.readlines()
        for line in lines:
            if line.strip().startswith("OPENAI_API_KEY"):
                parts = line.split("=", 1)
                if len(parts) == 2:
                    value = parts[1].strip()
                    if value:
                        print(f"✅ Found in .env file (length: {len(value)})")
                        if value.startswith('"') and value.endswith('"'):
                            print(f"  ⚠️  Key is wrapped in quotes - this might be the issue!")
                            print(f"  Remove quotes from .env file")
                        if len(value) < 20:
                            print(f"  ⚠️  WARNING: Key seems too short")
                    else:
                        print(f"❌ OPENAI_API_KEY is empty in .env file")
                break
        else:
            print(f"❌ OPENAI_API_KEY not found in .env file")

print()
print("=" * 60)
print("Troubleshooting:")
print("=" * 60)
print("1. Make sure .env file is in the project root")
print("2. Format should be: OPENAI_API_KEY=sk-... (no quotes)")
print("3. If key has special characters, wrap in quotes: OPENAI_API_KEY=\"sk-...\"")
print("4. Restart the API after changing .env file")
print("5. Check for trailing spaces or newlines in the key")
