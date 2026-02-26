#!/usr/bin/env python3
"""Fix agent.py to handle MINIMAX_RATE_LIMITED errors."""
import re

with open('ouroboros/agent.py', 'r') as f:
    content = f.read()

# Find the except block and add MINIMAX_RATE_LIMITED handling
old_block = '''            except Exception as e:
                tb = traceback.format_exc()
                append_jsonl(drive_logs / "events.jsonl", {
                    "ts": utc_now_iso(), "type": "task_error",
                    "task_id": task.get("id"), "error": repr(e),
                    "traceback": truncate_for_log(tb, 2000),
                })
                text = f"⚠️ Error during processing: {type(e).__name__}: {e}"'''

new_block = '''            except Exception as e:
                tb = traceback.format_exc()
                append_jsonl(drive_logs / "events.jsonl", {
                    "ts": utc_now_iso(), "type": "task_error",
                    "task_id": task.get("id"), "error": repr(e),
                    "traceback": truncate_for_log(tb, 2000),
                })
                
                # Critical: MiniMax rate limited - alert owner via Telegram (OpenRouter)
                if isinstance(e, RuntimeError) and "MINIMAX_RATE_LIMITED" in str(e):
                    alert_msg = (
                        "🚨 **MiniMax 限额触发！**\\n\\n"
                        "MiniMax API 已达到限额，已停止所有任务。\\n"
                        "请等待限额恢复或手动切换到其他模型。\\n\\n"
                        f"错误详情: {e}"
                    )
                    # Send alert to owner via supervisor's Telegram (uses OpenRouter)
                    try:
                        from supervisor.telegram import send_telegram_message
                        if hasattr(self, '_owner_id') and self._owner_id:
                            send_telegram_message(self._owner_id, alert_msg)
                    except Exception:
                        pass  # Don't fail the task if alert fails
                
                text = f"⚠️ Error during processing: {type(e).__name__}: {e}"'''

if old_block in content:
    content = content.replace(old_block, new_block)
    with open('ouroboros/agent.py', 'w') as f:
        f.write(content)
    print("SUCCESS: agent.py updated")
else:
    print("ERROR: Could not find target block")
    # Print nearby to debug
    if "except Exception as e:" in content:
        idx = content.find("except Exception as e:")
        print(f"Found at index {idx}")
        print(content[idx:idx+200])
