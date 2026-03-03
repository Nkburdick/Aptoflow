#!/usr/bin/env python3
"""Bootstrap script: creates .venv, installs deps, validates lib/ imports, checks .env."""

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).parent
VENV = ROOT / ".venv"
PYTHON = VENV / "bin" / "python"
PIP = VENV / "bin" / "pip"


def run(cmd: list[str], **kwargs) -> subprocess.CompletedProcess:
    print(f"  → {' '.join(cmd)}")
    return subprocess.run(cmd, check=True, **kwargs)


def main() -> None:
    print("=== Aptoflow Bootstrap ===\n")

    # 1. Create venv
    if not VENV.exists():
        print("Creating virtual environment...")
        run([sys.executable, "-m", "venv", str(VENV)])
    else:
        print("Virtual environment already exists.")

    # 2. Install dependencies
    print("\nInstalling dependencies...")
    run([str(PIP), "install", "-r", str(ROOT / "requirements.txt")])

    # 3. Validate lib/ imports
    print("\nValidating lib/ imports...")
    result = subprocess.run(
        [str(PYTHON), "-c", "from lib import get_client, chat, get_logger, run_agent_loop, CostTracker"],
        capture_output=True,
        text=True,
    )
    if result.returncode == 0:
        print("  ✓ All lib/ imports successful")
    else:
        print(f"  ✗ Import error:\n{result.stderr}")
        sys.exit(1)

    # 4. Check .env
    env_file = ROOT / ".env"
    if env_file.exists():
        print("\n  ✓ .env file found")
    else:
        print("\n  ⚠ No .env file found. Copy .env.example to .env and fill in your keys.")

    print("\n=== Bootstrap complete ===")


if __name__ == "__main__":
    main()
