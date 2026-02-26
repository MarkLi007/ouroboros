#!/usr/bin/env python3
import re

# Read the file
with open('/content/ouroboros_repo/ouroboros/llm.py', 'r') as f:
    content = f.read()

# Fix 1: Add prefix stripping before pricing lookup
old_pricing = '''        # Calculate cost based on MINIMAX_PRICING
        pricing = MINIMAX_PRICING.get(model)'''

new_pricing = '''        # Calculate cost based on MINIMAX_PRICING
        # Strip prefix if present (model could be 'MiniMax-M2.5' or 'minimax/MiniMax-M2.5')
        model_for_pricing = model[len(MINIMAX_MODEL_PREFIX):] if model.startswith(MINIMAX_MODEL_PREFIX) else model
        pricing = MINIMAX_PRICING.get(model_for_pricing)'''

if old_pricing in content:
    content = content.replace(old_pricing, new_pricing)
    print('Fix 1 applied: prefix stripping for pricing')
else:
    print('Fix 1: pattern not found')

# Fix 2: Fix the _model assignment to avoid double prefix
old_model = '''            usage["_model"] = f"minimax/{model}"'''
new_model = '''            usage["_model"] = f"minimax/{model_for_pricing}"'''

if old_model in content:
    content = content.replace(old_model, new_model)
    print('Fix 2 applied: use model_for_pricing')
else:
    print('Fix 2: pattern not found')

# Write back
with open('/content/ouroboros_repo/ouroboros/llm.py', 'w') as f:
    f.write(content)

print('Done')
