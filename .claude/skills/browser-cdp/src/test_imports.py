import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))

def test_imports():
    print("Testing module imports...")
    
    # Test core modules
    modules = [
        "core.auth_module",
        "core.content_service",
        "core.web_interface",
        "data.pipeline",
        "searchers.universal_crawler",
    ]
    
    failed = []
    for mod in modules:
        try:
            __import__(mod)
            print(f"✓ {mod}")
        except Exception as e:
            print(f"✗ {mod}: {e}")
            failed.append(mod)
    
    if failed:
        print(f"\nFailed: {len(failed)}")
        return False
    print("\nAll imports passed!")
    return True

if __name__ == "__main__":
    success = test_imports()
    sys.exit(0 if success else 1)
