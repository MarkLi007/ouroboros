import re

with open('ouroboros/llm.py', 'r') as f:
    content = f.read()

# Check if already has _calc_minimax_cost
if '_calc_minimax_cost' in content:
    print('Already has helper method')
else:
    # Add helper before _chat_minimax
    old = '    def _chat_minimax('
    new = '''    def _calc_minimax_cost(self, model: str, usage: dict) -> None:
        """Calculate cost for MiniMax API usage."""
        pricing = MINIMAX_PRICING.get(model)
        if pricing:
            prompt_price, completion_price = pricing
            prompt_cost = (usage["prompt_tokens"] / 1_000_000) * prompt_price
            completion_cost = (usage["completion_tokens"] / 1_000_000) * completion_price
            usage["cost"] = round(prompt_cost + completion_cost, 6)
            usage["_model"] = f"minimax/{model}"

    def _chat_minimax('''
    content = content.replace(old, new)
    
    # Replace inline cost calc
    old_cost = '''        # Calculate cost based on MINIMAX_PRICING
        pricing = MINIMAX_PRICING.get(model)
        if pricing:
            prompt_price, completion_price = pricing
            prompt_cost = (usage["prompt_tokens"] / 1_000_000) * prompt_price
            completion_cost = (usage["completion_tokens"] / 1_000_000) * completion_price
            usage["cost"] = round(prompt_cost + completion_cost, 6)
            usage["_model"] = f"minimax/{model}"

        return out_msg, usage'''
    
    new_cost = '''        # Calculate cost
        self._calc_minimax_cost(model, usage)

        return out_msg, usage'''
    
    content = content.replace(old_cost, new_cost)
    
    with open('ouroboros/llm.py', 'w') as f:
        f.write(content)
    
    print('Done')
