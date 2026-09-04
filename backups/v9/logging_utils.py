import threading
from datetime import datetime
from config import DEBUG_FULL_LOGS, MAX_LOG_CHARS, ENABLE_LOG_COLORS

printLock = threading.Lock()


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


def log(tag, message):
    ts = datetime.now().strftime("%H:%M:%S")
    color = tagColor(tag)
    with printLock:
        print(f"{C.GRAY}[{ts}]{C.RESET} {color}[{tag}]{C.RESET} {message}", flush=True)


def logPayload(tag, text, maxChars=MAX_LOG_CHARS):
    if not text:
        log(tag, "[Empty payload]")
        return

    if DEBUG_FULL_LOGS:
        maxChars = 999999

    text = str(text).replace('\\n', '\n')
    if len(text) > maxChars:
        text = text[:maxChars] + f"\n... [truncated {len(text) - maxChars} chars]"

    lines = text.split('\n')
    ts = datetime.now().strftime("%H:%M:%S")
    color = tagColor(tag)
    firstLine = f"{C.GRAY}[{ts}]{C.RESET} {color}[{tag}]{C.RESET} {C.GRAY}| {lines[0]}{C.RESET}"
    indent = " " * (16 + len(tag))

    with printLock:
        print(firstLine, flush=True)
        for line in lines[1:]:
            print(f"{indent}{C.GRAY}| {line}{C.RESET}", flush=True)


def logRequestHeader(charCount, chatMsg):
    ts = datetime.now().strftime("%H:%M:%S")
    header = f"\n{C.GRAY}[{ts}]{C.RESET} {C.BOLD}{C.RED}=== NEW REQUEST RECEIVED ({charCount} chars) ==={C.RESET}"
    trigger = f"{C.GRAY}[{ts}]{C.RESET} {C.BOLD}{C.RED}>>> TRIGGER: {chatMsg}{C.RESET}"
    with printLock:
        print(header, flush=True)
        print(trigger, flush=True)