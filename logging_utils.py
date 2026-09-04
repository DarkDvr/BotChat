import threading
import os
import logging
import re
from datetime import datetime
from config import DEBUG_FULL_LOGS, MAX_LOG_CHARS, ENABLE_LOG_COLORS, LOG_DIR, LOG_DATE_FORMAT

# Auto-create logs directory
if not os.path.exists(LOG_DIR):
    os.makedirs(LOG_DIR)

printLock = threading.Lock()

# ============================================================================
# COLOR DEFINITIONS
# ============================================================================
class C:
    if ENABLE_LOG_COLORS:
        RESET = "\033[0m"
        BOLD = "\033[1m"
        GRAY = "\033[90m"
        RED = "\033[91m"
        GREEN = "\033[92m"
        YELLOW = "\033[93m"
        BLUE = "\033[94m"
        MAGENTA = "\033[95m"
        CYAN = "\033[96m"
        WHITE = "\033[97m"
        BRIGHT_PURPLE = "\033[1;35m"
    else:
        RESET = ""
        BOLD = ""
        GRAY = ""
        RED = ""
        GREEN = ""
        YELLOW = ""
        BLUE = ""
        MAGENTA = ""
        CYAN = ""
        WHITE = ""
        BRIGHT_PURPLE = ""


def tagColor(tag):
    t = tag.upper()
    if t.startswith("BOTRAM"): return C.BRIGHT_PURPLE
    if t.startswith("LIBRARIAN"): return C.CYAN
    if t.startswith("DDGS"): return C.YELLOW
    if t.startswith("FRENCHMAID"): return C.MAGENTA
    if t.startswith("ROLEPLAYER"): return C.GREEN
    if t.startswith("CACHE"): return C.BLUE
    if t in ("ERROR", "SKEPTIC"): return C.RED
    if t == "RATE": return C.GRAY
    return C.WHITE


# ============================================================================
# FILE LOGGER SETUP (Daily Rotation, Plain Text)
# ============================================================================
def _getFileLogger():
    """Returns a logger that writes to daily rotating file (plain text, no colors)."""
    logger = logging.getLogger("botchat_file")
    if not logger.handlers:
        logger.setLevel(logging.DEBUG if DEBUG_FULL_LOGS else logging.INFO)
        logger.propagate = False
        
        # Daily rotating file handler
        filename = f"botchat_{datetime.now().strftime('%d-%m-%Y')}.log"
        filepath = os.path.join(LOG_DIR, filename)
        fileHandler = logging.FileHandler(filepath, mode='a', encoding='utf-8')
        fileHandler.setLevel(logging.DEBUG if DEBUG_FULL_LOGS else logging.INFO)
        
        # Formatter: DD-MM-YYYY HH:MM:SS LEVEL TAG MESSAGE (no colors)
        formatter = logging.Formatter(fmt="%(asctime)s %(levelname)s: %(message)s", datefmt="%d-%m-%Y %H:%M:%S")
        fileHandler.setFormatter(formatter)
        
        logger.addHandler(fileHandler)
    
    return logger


_fileLogger = None
def _getGlobalFileLogger():
    global _fileLogger
    if _fileLogger is None:
        _fileLogger = _getFileLogger()
    return _fileLogger


def _stripAnsi(text):
    """Remove ANSI escape codes from text for clean file logging."""
    ansiPattern = r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])'
    return re.sub(ansiPattern, '', text)


# ============================================================================
# CONSOLE LOGGING FUNCTIONS
# ============================================================================
def log(tag, message):
    ts = datetime.now().strftime("%H:%M:%S")
    color = tagColor(tag)
    
    # Console output
    consoleLine = f"{C.GRAY}[{ts}]{C.RESET} {color}[{tag}]{C.RESET} {message}"
    
    # File output (plain text, full timestamp, no colors)
    fileLogger = _getGlobalFileLogger()
    fileLogger.info(f"[{tag}] {message}")
    
    with printLock:
        print(consoleLine, flush=True)


def logPayload(tag, text, maxChars=MAX_LOG_CHARS):
    if not text:
        log(tag, "[Empty payload]")
        return

    if DEBUG_FULL_LOGS:
        maxChars = 999999

    text = str(text).replace('\\n', '\n')
    originalLen = len(text)
    if len(text) > maxChars:
        text = text[:maxChars] + f"\n... [truncated {originalLen - maxChars} chars]"

    lines = text.split('\n')
    ts = datetime.now().strftime("%H:%M:%S")
    color = tagColor(tag)
    firstLine = f"{C.GRAY}[{ts}]{C.RESET} {color}[{tag}]{C.RESET} {C.GRAY}| {lines[0]}{C.RESET}"
    indent = " " * (16 + len(tag))

    # File output (full payload, no truncation, no colors)
    fileLogger = _getGlobalFileLogger()
    fileLogger.debug(f"[{tag}] Payload:\n{text}")

    with printLock:
        print(firstLine, flush=True)
        for line in lines[1:]:
            print(f"{indent}{C.GRAY}| {line}{C.RESET}", flush=True)


def logRequestHeader(charCount, chatMsg):
    ts = datetime.now().strftime("%H:%M:%S")
    header = f"\n{C.GRAY}[{ts}]{C.RESET} {C.BOLD}{C.RED}=== NEW REQUEST RECEIVED ({charCount} chars) ==={C.RESET}"
    trigger = f"{C.GRAY}[{ts}]{C.RESET} {C.BOLD}{C.RED}>>> TRIGGER: {chatMsg}{C.RESET}"
    
    # File output
    fileLogger = _getGlobalFileLogger()
    fileLogger.info(f"=== NEW REQUEST RECEIVED ({charCount} chars) ===")
    fileLogger.info(f">>> TRIGGER: {chatMsg}")
    
    with printLock:
        print(header, flush=True)
        print(trigger, flush=True)