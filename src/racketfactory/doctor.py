"""
Racket Factory — Health & Sanity Checks
Single source of truth for repo health.
Run with: python3 -m racketfactory.doctor
"""

import sys
from pathlib import Path

# Allow running without PYTHONPATH=src
ROOT = Path(__file__).resolve().parents[2]
SRC_PATH = ROOT / "src"
if str(SRC_PATH) not in sys.path:
    sys.path.insert(0, str(SRC_PATH))

LOCALDATA = ROOT / "localdata"


def check_localdata_exists() -> bool:
    """Ensure localdata directory exists."""
    return LOCALDATA.exists()


def check_localdata_writable() -> bool:
    """Check that we can write to localdata."""
    if not LOCALDATA.exists():
        return False
    try:
        test_file = LOCALDATA / ".write_test"
        test_file.touch()
        test_file.unlink()
        return True
    except Exception:
        return False


def run_health_checks() -> dict:
    """Run all health checks and return results."""
    checks = {
        "localdata_exists": check_localdata_exists(),
        "localdata_writable": check_localdata_writable(),
    }
    return checks


def main():
    print("🏥 Racket Factory Doctor")
    print("=" * 40)

    results = run_health_checks()

    all_passed = True
    for name, passed in results.items():
        status = "✅ PASS" if passed else "❌ FAIL"
        print(f"{name:25} {status}")
        if not passed:
            all_passed = False

    print("=" * 40)
    if all_passed:
        print("All checks passed. Repo is healthy.")
        return 0
    else:
        print("Some checks failed. See above.")
        return 1


if __name__ == "__main__":
    sys.exit(main())