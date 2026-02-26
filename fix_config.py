#!/usr/bin/env python3
"""Fix config defaults to prefer MiniMax."""

# Fix config.py defaults
with open('ouroboros/config.py', 'r') as f:
    config_content = f.read()

config_content = config_content.replace(
    '"OUROBOROS_MODEL_CODE": "anthropic/claude-sonnet-4.6"',
    '"OUROBOROS_MODEL_CODE": "minimax/MiniMax-M2.5"'
)
config_content = config_content.replace(
    '"OUROBOROS_FALLBACK_MODEL": "anthropic/claude-sonnet-4.6"',
    '"OUROBOROS_FALLBACK_MODEL": "minimax/MiniMax-M2"'
)

with open('ouroboros/config.py', 'w') as f:
    f.write(config_content)

print("SUCCESS: config.py updated")

# Fix vision.py defaults
with open('ouroboros/tools/vision.py', 'r') as f:
    vision_content = f.read()

vision_content = vision_content.replace(
    '_DEFAULT_VLM_MODEL = "anthropic/claude-sonnet-4.6"',
    '_DEFAULT_VLM_MODEL = "minimax/MiniMax-M2.5"'
)

with open('ouroboros/tools/vision.py', 'w') as f:
    f.write(vision_content)

print("SUCCESS: vision.py updated")
