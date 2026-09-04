import re
from config import MAX_RESPONSE_LENGTH, LIBRARIAN_CONFIG, FRENCHMAID_CONFIG, ROLEPLAYER_CONFIG
from logging_utils import log

def cleanWowLinks(text):
    if not text: return text
    text = re.sub(r'\|c[0-9a-fA-F]{8}\|H.*?\|h\[(.*?)\]\|h\|r', r'[\1]', text)
    text = re.sub(r'\|[cHhr][0-9a-fA-F]*', '', text)
    return text

def cleanFinalResponse(text):
    if not text: return "Hmm..."
    text = re.sub(r"\*\*(.*?)\*\*", r"\1", text)
    text = re.sub(r"\*(.*?)\*", r"\1", text)
    text = re.sub(r"`(.*?)`", r"\1", text)
    text = re.sub(r"^[\s>#\-\*]+", "", text, flags=re.MULTILINE)
    text = text.strip()
    if len(text) > MAX_RESPONSE_LENGTH:
        text = text[:MAX_RESPONSE_LENGTH - 3] + "..."
    return text

def validateConfig():
    for name, cfg in [("LIBRARIAN", LIBRARIAN_CONFIG), ("FRENCHMAID", FRENCHMAID_CONFIG), ("ROLEPLAYER", ROLEPLAYER_CONFIG)]:
        hasUrl = bool(cfg["apiUrl"])
        hasKey = bool(cfg["apiKey"])
        if hasUrl and hasKey:
            log("CONFIG", f"{name}: ONLINE model '{cfg['model']}'")
        elif hasUrl or hasKey:
            log("CONFIG", f"WARNING: {name} partial API config. Falling back to local Ollama '{cfg['model']}'.")
        else:
            log("CONFIG", f"{name}: LOCAL Ollama model '{cfg['model']}'")
            
def cleanEntityName(entity):
    if not entity:
        return ""
    # Remove common search junk and stop words
    cleaned = re.sub(r'\b(wowhead|wotlk|location|guide|farming|where|how|to|get|quest|buy|sell|find|is|the|in|for|and)\b', '', entity, flags=re.IGNORECASE).strip()
    
    # Keep letters, numbers, spaces, apostrophes, AND COMMAS
    cleaned = re.sub(r'[^a-zA-Z0-9\s\',]', '', cleaned)
    
    # Normalize comma spacing (e.g., "sakred ,neacris" -> "sakred, neacris")
    cleaned = re.sub(r'\s*,\s*', ', ', cleaned)
    cleaned = re.sub(r'\s+', ' ', cleaned).strip()
    
    # Title case each name individually, filter empties, then rejoin
    return ', '.join([name.strip().title() for name in cleaned.split(',') if name.strip()])