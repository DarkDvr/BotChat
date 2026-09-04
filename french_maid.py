import re
import sqlite3
from config import MAX_RESPONSE_LENGTH, CLASSIFIER_CONFIG, FRENCHMAID_CONFIG, ROLEPLAYER_CONFIG
from logging_utils import log
from config import LORE_DB_PATH

# --- Slang & Abbreviation Engine ---
_slangCache = {}

def loadSlangTable():
    """Loads the slang table from lore.db into memory at startup."""
    global _slangCache
    try:
        conn = sqlite3.connect(LORE_DB_PATH)
        rows = conn.execute("SELECT abbr, full_name FROM slang").fetchall()
        conn.close()
        _slangCache = {abbr.lower(): full_name for abbr, full_name in rows}
        log("FRENCHMAID", f"Loaded {len(_slangCache)} slang terms into memory.")
    except Exception as e:
        log("FRENCHMAID", f"Failed to load slang table: {e}")
        _slangCache = {}

def expandSlang(text):
    """Strict word-boundary expansion of slang/abbreviations. 
    Case-sensitive for risky abbreviations that collide with English words.
    Case-insensitive for everything else."""
    if not text or not _slangCache:
        return text
    
    # Abbreviations that MUST match exact casing to avoid hitting English words
    # "an" != "AN", "hot" != "HoT", "dot" != "DoT", etc.
    caseSensitive = {"an", "hot", "dot", "cd", "ms", "os"}
    exactCasing = {
        "an": "AN", "hot": "HoT", "dot": "DoT",
        "cd": "CD", "ms": "MS", "os": "OS"
    }
    
    words = text.split()
    result = []
    for word in words:
        cleanWord = word.strip(".,!?;:\"'()[]")
        lowerWord = cleanWord.lower()
        
        if lowerWord in _slangCache:
            if lowerWord in caseSensitive:
                # Only expand if casing matches exactly
                if cleanWord == exactCasing[lowerWord]:
                    replacement = _slangCache[lowerWord]
                    word = word.replace(cleanWord, replacement, 1)
            else:
                # Safe abbreviations: case-insensitive match
                replacement = _slangCache[lowerWord]
                word = word.replace(cleanWord, replacement, 1)
        
        result.append(word)
    
    return " ".join(result)

def cleanWowLinks(text):
    if not text:
        return text

    # Extract [Item Name] from valid WoW hyperlinks
    text = re.sub(
        r'\|c[0-9a-fA-F]{8}\|H[^|]*\|h\[(.*?)\]\|h\|r',
        r'[\1]',
        text
    )

    # Strip WoW color codes safely.
    # IMPORTANT: exactly 8 hex chars only.
    # Old regex used [0-9a-fA-F]* and could eat talent digits after color codes,
    # e.g. |cff00ff0034 -> removed the "34".
    text = re.sub(r'\|c[0-9a-fA-F]{8}', '', text)

    # Strip hyperlink openers, hover tags, and reset tags without touching nearby text.
    text = re.sub(r'\|H[^|]*', '', text)
    text = text.replace('|h', '')
    text = text.replace('|r', '')

    return text

def cleanFinalResponse(text):
    if not text: return "Hmm... "
    
    # Strip Markdown formatting
    text = re.sub(r"\*\*(.*?)\*\*", r"\1", text)  # Bold
    text = re.sub(r"\*(.*?)\*", r"\1", text)      # Italics
    text = re.sub(r"`(.*?)`", r"\1", text)        # Inline code
    
    # Strip leading Markdown symbols (headings, lists, quotes)
    text = re.sub(r"^\s*[>#*\-]+\s*", "", text, flags=re.MULTILINE)
    
    text = text.strip()
    if len(text) > MAX_RESPONSE_LENGTH:
        text = text[:MAX_RESPONSE_LENGTH - 3] + "..."
    return text

def cleanEntityName(entity):
    if not entity: return ""
    cleaned = re.sub(r'\b(wowhead|wotlk|location|guide|farming|where|how|to|get|quest|buy|sell|find|is|the|in|for|and)\b', '', entity, flags=re.IGNORECASE).strip()
    cleaned = re.sub(r"[^a-zA-Z0-9\s',\-/]", '', cleaned)
    cleaned = re.sub(r'\s*,\s*', ', ', cleaned)
    cleaned = re.sub(r'\s+', ' ', cleaned).strip()

    parts = []
    for name in cleaned.split(','):
        name = name.strip()
        if not name:
            continue
        # Preserve names that contain special patterns (like OOX-17/TN)
        if re.search(r'[A-Z]{2,}|\d', name):
            parts.append(name)
        else:
            parts.append(name.title())
    return ', '.join(parts)

def validateConfig():
    for name, cfg in [("CLASSIFIER", CLASSIFIER_CONFIG), ("FRENCHMAID", FRENCHMAID_CONFIG), ("ROLEPLAYER", ROLEPLAYER_CONFIG)]:
        hasUrl = bool(cfg["apiUrl"])
        hasKey = bool(cfg["apiKey"])
        if hasUrl and hasKey:
            log("CONFIG", f"{name}: ONLINE model '{cfg['model']}'")
        elif hasUrl or hasKey:
            log("CONFIG", f"WARNING: {name} partial API config. Falling back to local Ollama '{cfg['model']}'.")
        else:
            log("CONFIG", f"{name}: LOCAL Ollama model '{cfg['model']}'")