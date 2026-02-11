# File: app/utils/json_utils.py

import json
import re
import logging
from typing import Any, Optional

logger = logging.getLogger("utils.json")

def safe_json_parse(content: str) -> Any:
    """
    Safely parse JSON from a string, handling markdown fences and potential invalid JSON.
    
    Args:
        content: The string to parse
        
    Returns:
        The parsed object or a dictionary with error info if parsing fails.
    """
    if not content:
        return {}
        
    content = content.strip()
    
    # 1. Handle Markdown Code Fences
    if "```json" in content:
        try:
            # Extract content between ```json and ```
            pattern = r"```json\s*(.*?)\s*```"
            match = re.search(pattern, content, re.DOTALL)
            if match:
                content = match.group(1).strip()
        except Exception as e:
            logger.warning(f"Failed to extract JSON from markdown: {e}")
            
    # 2. Heuristic extraction of first { and last } if raw parse fails
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        try:
            # Look for the first occurrence of '{' and the last occurrence of '}'
            start = content.find('{')
            end = content.rfind('}') + 1
            if start != -1 and end != 0:
                extracted = content[start:end]
                return json.loads(extracted)
        except Exception as e:
            logger.error(f"[ERROR] safe_json_parse: Total failure to parse content: {e}")
            return {}

    return {}
