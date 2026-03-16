#!/usr/bin/env python3
"""Simple verification script for llava vision model."""

import sys
import os

# Add src to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src'))

from education.image_alt_text import ImageAltTextGenerator


def main():
    print("=" * 60)
    print("Aelira Image Alt Text Generator - Verification")
    print("=" * 60)

    gen = ImageAltTextGenerator()

    # Health check
    print("\n🔍 Checking Ollama and llava model availability...")
    health = gen.health_check()

    print(f"\nStatus: {health['status']}")
    print(f"Ollama Host: {health['ollama_host']}")

    if health['status'] == 'unhealthy':
        print(f"\n⚠️  Error: {health.get('error', 'Unknown error')}")
        print("\nOllama is not running or not accessible.")
        print("\nTo start Ollama:")
        print("  1. Docker (recommended): cd backend && docker-compose up -d")
        print("  2. Or: brew services start ollama")
        return 1

    print(f"Vision Model: {health['vision_model']}")
    print(f"Vision Available: {health['vision_available']}")
    print(f"Total Models: {health.get('total_models', 0)}")

    if health.get('available_models'):
        print("\nAvailable Models:")
        for model in health['available_models']:
            marker = "✅" if "llava" in model else "  "
            print(f"  {marker} {model}")

    print("\n" + "=" * 60)

    if health['vision_available']:
        print("✅ SUCCESS: llava model is ready for image analysis!")
        print("\nNext steps:")
        print("1. Create sample images in tests/fixtures/")
        print("2. Run: pytest tests/test_image_alt_text.py")
        print("3. Add API endpoint for image alt text generation")
        return 0
    else:
        print("❌ MISSING: llava model not found")
        print("\nTo install:")
        print("  ollama pull llava:7b")
        print("\nThis will download ~4.1GB")
        return 1


if __name__ == "__main__":
    sys.exit(main())
