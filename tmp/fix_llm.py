import re

with open('/content/ouroboros_repo/ouroboros/llm.py', 'r') as f:
    content = f.read()

# Find where to insert the helper (before _chat_minimax)
old_pattern = r'(    def _chat_minimax\()'
replacement = '''    def _calc_minimax_cost(self, model: str, usage: dict) -> None:
        \"\"\"Calculate cost for MiniMax API usage.\"\"\"
        pricing = MINIMAX_PRICING.get(model)
        if pricing:
            prompt_price, completion_price = pricing
            prompt_cost = (usage[\"prompt_tokens\"] / 1_000_000) * prompt_price
            completion_cost = (usage[\"completion_tokens\"] / 1_000_000) * completion_price
            usage[\"cost\"] = round(prompt_cost + completion_cost, 6)
            usage[\"_model\"] = f\"minimax/{model}\"

    def _chat_minimax('''

content = re.sub(old_pattern, replacement, content)

# Now simplify the cost calculation in _chat_minimax
old_cost_calc = r'''        # Calculate cost based on MINIMAX_PRICING
        pricing = MINIMAX_PRICING\.get\(model\)
        if pricing:
            prompt_price, completion_price = pricing
            prompt_cost = \(usage\[\"prompt_tokens\"\] / 1_000_000\) \* prompt_price
            completion_cost = \(usage\[\"completion_tokens\"\] / 1_000_000\) \* completion_price
            usage\[\"cost\"\] = round\(prompt_cost \+ completion_cost, 6\)
            usage\[\"_model\"\] = f\"minimax/\{model\}\"

        return out_msg, usage'''

new_cost_calc = '''        # Calculate cost
        self._calc_minimax_cost(model, usage)

        return out_msg, usage'''

content = re.sub(old_cost_calc, new_cost_calc, content)

with open('/content/ouroboros_repo/ouroboros/llm.py', 'w') as f:
    f.write(content)

print('Done')
