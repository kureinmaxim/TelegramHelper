#!/usr/bin/env python3
"""AI provider/model settings - SSH callable version of /ai_provider and /ch_model."""
import os
import sys

PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(PROJECT_DIR)
sys.path.insert(0, PROJECT_DIR)

def show_ai_settings():
    """Show current AI provider and model settings."""
    try:
        from utils import get_current_model, get_available_models
        
        current = get_current_model()
        available = get_available_models()
        
        print("\n🤖 AI Settings")
        print("=" * 40)
        print(f"Current Model: {current}")
        print(f"\nAvailable Models:")
        for model in available:
            mark = " ✓" if model == current else ""
            print(f"  • {model}{mark}")
        print()
    except Exception as e:
        print(f"❌ Error: {e}")

def set_model(model_name: str):
    """Set the AI model."""
    try:
        from utils import set_current_model, get_available_models
        
        available = get_available_models()
        if model_name not in available:
            print(f"❌ Unknown model: {model_name}")
            print(f"Available: {', '.join(available)}")
            return
        
        set_current_model(model_name)
        print(f"✅ Model set to: {model_name}")
    except Exception as e:
        print(f"❌ Error: {e}")

if __name__ == "__main__":
    if len(sys.argv) > 1:
        set_model(sys.argv[1])
    else:
        show_ai_settings()
        print("Usage: python3 ai_model.py [model_name]")
