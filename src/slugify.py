import unicodedata
import re

def slugify(title: str) -> str:
    if not title:
        return ""
    
    # Remove accents
    normalized = unicodedata.normalize("NFKD", title)
    no_accents = ''.join(c for c in normalized if not unicodedata.combining(c))
    
    # Lowercase
    lower = no_accents.lower()
    
    # Keep only ASCII letters and digits, treat everything else as separator
    # Apostrophes are separators, not deleted
    cleaned = re.sub(r"[^a-z0-9]+", "-", lower)
    
    # Remove leading/trailing hyphens
    result = cleaned.strip("-")
    
    # Return empty string if no letters or digits
    if not re.search(r"[a-z0-9]", result):
        return ""
    
    return result
