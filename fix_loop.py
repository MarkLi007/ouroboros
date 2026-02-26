#!/usr/bin/env python3
"""Fix loop.py to re-raise MINIMAX_RATE_LIMITED errors."""

with open('ouroboros/loop.py', 'r') as f:
    content = f.read()

old = '''except Exception as e:
            last_error = e'''

new = '''except Exception as e:
            # Re-raise critical errors that should not be retried
            if isinstance(e, RuntimeError) and "MINIMAX_RATE_LIMITED" in str(e):
                raise
            last_error = e'''

if old in content:
    content = content.replace(old, new, 1)
    with open('ouroboros/loop.py', 'w') as f:
        f.write(content)
    print('SUCCESS')
else:
    print('NOT FOUND')
