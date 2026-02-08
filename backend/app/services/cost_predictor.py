
class CostPredictor:
    """Predict AI costs before running pipeline."""
    
    # Average costs per agent per complexity level (in INR)
    COST_TABLE = {
        "saanvi": {1: 5, 3: 10, 5: 20, 7: 35, 10: 50},
        "shubham": {1: 20, 3: 50, 5: 100, 7: 200, 10: 350},
        "aanya": {1: 15, 3: 40, 5: 80, 7: 150, 10: 250},
        "navya": {1: 10, 3: 25, 5: 50, 7: 100, 10: 180},
        "karan": {1: 10, 3: 25, 5: 50, 7: 100, 10: 180},
        "deepika": {1: 10, 3: 25, 5: 50, 7: 100, 10: 180},
        "aarav": {1: 5, 3: 10, 5: 20, 7: 35, 10: 50},
        "pranav": {1: 5, 3: 10, 5: 15, 7: 25, 10: 40},
        "vanya": {1: 10, 3: 20, 5: 40, 7: 70, 10: 100},
    }
    
    # Retry multiplier (average retries per complexity)
    RETRY_MULTIPLIER = {1: 1.0, 3: 1.2, 5: 1.5, 7: 2.0, 10: 2.5}
    
    def predict_cost(self, complexity: int) -> dict:
        """Predict total AI cost for a project."""
        base_cost = sum(
            self._interpolate_cost(agent, complexity)
            for agent in self.COST_TABLE
        )
        
        retry_mult = self.RETRY_MULTIPLIER.get(complexity, 1.5)
        predicted = base_cost * retry_mult
        
        return {
            "complexity": complexity,
            "base_cost_inr": round(base_cost, 2),
            "with_retries_inr": round(predicted, 2),
            "margin_at_price": None,  # Filled by caller
            "breakdown": {
                agent: round(self._interpolate_cost(agent, complexity) * retry_mult, 2)
                for agent in self.COST_TABLE
            }
        }
    
    def _interpolate_cost(self, agent: str, complexity: int) -> float:
        table = self.COST_TABLE[agent]
        keys = sorted(table.keys())
        
        for i, key in enumerate(keys):
            if complexity <= key:
                if i == 0:
                    return table[key]
                prev_key = keys[i-1]
                ratio = (complexity - prev_key) / (key - prev_key)
                return table[prev_key] + ratio * (table[key] - table[prev_key])
        
        return table[keys[-1]]


cost_predictor = CostPredictor()
