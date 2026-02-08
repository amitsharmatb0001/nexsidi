import re
import html
from typing import Optional


def sanitize_user_input(text: str) -> str:
    """Sanitize user input to prevent XSS and prompt injection."""
    
    # HTML escape
    text = html.escape(text)
    
    # Remove potential script tags (even after escaping, be safe)
    text = re.sub(r'<script[^>]*>.*?</script>', '', text, flags=re.DOTALL | re.IGNORECASE)
    
    # Basic prompt injection defense
    # Remove attempts to override system prompts
    injection_patterns = [
        r'ignore\s+(all\s+)?previous\s+instructions',
        r'you\s+are\s+now\s+',
        r'new\s+system\s+prompt',
        r'override\s+instructions',
    ]
    
    for pattern in injection_patterns:
        text = re.sub(pattern, '[filtered]', text, flags=re.IGNORECASE)
    
    return text
