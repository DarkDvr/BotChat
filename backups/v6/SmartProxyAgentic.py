#!/usr/bin/env python3
# ============================================================================
#  SmartProxyAgentic.py (v6)
# ----------------------------------------------------------------------------
#  Agentic web-search proxy with unified LLM Memory Parser (BotRAM) for 
#  AzerothCore mod-ollama-chat.
#
#  Pipeline:
#    PASS 1  BOTRAM      -> Identity, strip C++ history, unified memory recall & topic check
#    PASS 2  LIBRARIAN   -> decides if a search is needed + DDGS fetch
#    PASS 3  FRENCHMAID  -> LLM-based extractor: pulls facts from DDGS snippets
#    PASS 4  ROLEPLAYER  -> generates the final in-character reply
#    POST    BOTRAM      -> Stores the interaction in SQLite
# ============================================================================

import json
import re
import time
import threading
import sqlite3
from datetime import datetime, timedelta

import requests
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import os
if os.name == "nt":
    os.system("")  # Enables ANSI escape sequences on Windows consoles

try:
    from ddgs import DDGS
except ImportError:
    DDGS = None

# ============================================================================
#  CONFIGURATION BLOCK
# ============================================================================

VERSION = "6"
DEBUG_FULL_LOGS = True            # Set to True to disable log truncation and see EVERYTHING

# --- Proxy server ---
PROXY_HOST = "127.0.0.1"
PROXY_PORT = 8000
OLLAMA_URL = "http://127.0.0.1:11434/api/generate"

# --- BOTRAM (Pass 1 & Post-Pipeline) ---
BOTRAM_ENABLED = True
BOTRAM_DB_PATH = "botram.db"
BOTRAM_MODEL = "qwen2.5:7b"         # Local model for memory parsing & loop detection
BOTRAM_PAST_CONVOS_LIMIT = 10       # Number of past shared convos to gather for memory (+2 added in query)
BOTRAM_PHRASES_PER_CONVO = 7        # Max phrases to pull per conversation
BOTRAM_AMBIENT_CONVOS_LIMIT = 2     # Number of recent ambient convos to gather
BOTRAM_AMBIENT_JOIN_MINUTES = 5     # Ambient thoughts join an active convo if one exists within this window
BOTRAM_RETENTION_DAYS = 7           # Delete memories older than this on startup
BOTRAM_ACTIVE_WINDOW_MINUTES = 5    # Convo dies of silence if no messages for this long
BOTRAM_LOOP_CHECK_THRESHOLD = 6     # Min messages before loop checks begin
BOTRAM_LOOP_CHECK_INTERVAL = 4      # Check every N messages after threshold
BOTRAM_LOOP_COOLDOWN_MINUTES = 30   # How long a convo stays "OVER" before resurrecting

# --- LIBRARIAN engine (Pass 2) ---
LIBRARIAN_API_URL = ""                      
LIBRARIAN_API_KEY = ""                      
LIBRARIAN_MODEL   = "qwen2.5:7b"            
LIBRARIAN_TEMPERATURE = 0.1                 

# --- FRENCHMAID engine (Pass 3) ---
FRENCHMAID_ENABLED = True
FRENCHMAID_API_URL = ""                     
FRENCHMAID_API_KEY = ""                     
FRENCHMAID_MODEL   = "qwen2.5:7b"           
FRENCHMAID_TEMPERATURE = 0.1                
FRENCHMAID_NO_DATA_MARKER = "NO_USEFUL_DATA"
FRENCHMAID_MAX_OUTPUT_LENGTH = 800     
FRENCHMAID_MAX_SNIPPET_LENGTH = 350    

# --- ROLEPLAYER engine (Pass 4) ---
ROLEPLAYER_API_URL = "https://api.mistral.ai/v1/chat/completions"
ROLEPLAYER_API_KEY = "wRI4hmSFIOzwYAWaDKB6EWiXPjkzADtG"
ROLEPLAYER_MODEL   = "mistral-medium-latest"

# --- Rate limiting & Timeouts ---
ONLINE_API_REQUESTS_PER_SECOND = 1
ONLINE_API_TIMEOUT = 60
OLLAMA_TIMEOUT     = 30
DDGS_TIMEOUT       = 15
SEARCH_RESULT_COUNT = 10

# --- Skeptic Mode ---
SKEPTIC_MODE = True
SKEPTIC_OVERRIDE_PROMPT = (
    "A web search was performed for this topic and found ZERO evidence "
    "this exists in WotLK 3.3.5. It is very likely a fake item, a myth, or the "
    "player is mistaken. React like a real gamer: express doubt, "
    "ask if it even exists or ask if they're talking about a different game. "
    "Do NOT pretend it exists or hallucinate facts about it."
)

# --- Cache & Limits ---
CACHE_TTL = 15  
MAX_RESPONSE_LENGTH = 255   
MAX_LOG_CHARS = 1500 

# ============================================================================
#  SYSTEM PROMPTS
# ============================================================================

LIBRARIAN_SYSTEM_PROMPT = """You are the "Librarian", a search-routing assistant for a World of Warcraft WotLK 3.3.5 server.
Your ONLY job is to look at the NEWEST player message and decide whether it needs a factual web search.

CRITICAL DIRECTIVES:
1. YOU ARE A BACKEND ROUTER. Your ONLY valid output is the raw JSON object.
2. BE AGGRESSIVE WITH SEARCHES. If the player asks about a location, NPC, quest, item, stats or numbers, flight path, dungeon, or game mechanic, you MUST search. 
3. SMART CONTEXT RESOLUTION. Decide whether a query needs the character's current zone or should be a global search.

CONTEXT RESOLUTION & QUERY BUILDING:
- LOCAL QUERIES: If the player asks about "here", "this zone", "nearby", "around me", or explicitly mentions a zone name, INCLUDE the bot's current "Zone: [Name]" from the prompt context in your query. (e.g., "Searing Gorge alliance flight path").
- GLOBAL QUERIES: If the player asks about general items (potions, food, materials), general game mechanics, class trainers, or a specific quest name, DO NOT append the bot's current zone, as it is irrelevant in that case. Search globally. (e.g., "where to buy super healing potion", "how to get to thousand needles").
- PRONOUNS & HISTORY: Use the chat history to resolve pronouns like "he", "she", "it", or "that quest".
- ACRONYMS: "FP" means Flight Path. "BiS" means Best in Slot. "Dungeon" and "Instance" are synonyms.
- CLASS CONTEXT: If they say "for me" or "my class", extract their Class from the "Player Info" block.
- WoW LINKS: Extract names from brackets like |Hquest:123:40|h[Name]|h|r.

WHEN TO SET search = false:
- The message is just a greeting, goodbye, or short reaction (e.g., "lol", "true", "k", "thanks", "what").
- The message is purely conversational banter with no factual question.

QUERY RULES (only when search = true):
- Write a concise query, under 12 words, plain text.
- Always append "wowpedia wotlk" to the query.

OUTPUT FORMAT:
Reply with ONLY one JSON object. No markdown, no code fences.
{"search": true, "query": "your query here"} or {"search": false, "query": null}"""

FRENCHMAID_SYSTEM_PROMPT = """You are "FrenchMaid", a data-cleaning assistant for a World of Warcraft WotLK 3.3.5 server.
Extract ONLY specific facts that help answer the SEARCH QUERY. Ignore junk.
Translate foreign languages to English. Do NOT invent information.
Keep it concise: a few short plain-text bullet points.
If NO useful info, reply exactly: NO_USEFUL_DATA"""

BOTRAM_LOOP_PROMPT = """You are a loop detector for an MMO chat room. Analyze the recent messages.
Your job is to catch conversations that are stuck and going nowhere.

Flag as LOOP if:
- A bot repeats the exact same sentence word-for-word.
- The same two concepts are recycled endlessly without new ideas or progression (e.g., "gold vs secrets" traded back and forth without resolution).
- Speakers are stuck in a pun battle or wordplay loop, trading similar rhymes without progressing.
- Each message is just a slight rewording of the previous one.

Reply CONTINUE if:
- Speakers exchange short acknowledgments like "k", "lol", "nice", "fr", "true", or say goodbye.
- The conversation is progressing, introducing new ideas, changing direction, or escalating.
- Players are roasting each other but the conversation still moves forward with new angles or topics.

Reply ONLY with "LOOP" or "CONTINUE".

Messages:
{messages}"""

BOTRAM_MEMORY_RECALL_PROMPT = """You are a memory filter and conversation classifier for an MMO chat bot.
Below are recent conversations the bot and the player were involved in (each labeled with a Conversation ID), and a new incoming message from the player.

TASK 1: Determine which conversation the new message belongs to.
- Carefully read the new message and look for matching keywords, names, locations, or topics in the listed conversations.
- If it continues, references, or returns to one of the listed conversations, reply with that conversation's ID number.
- If it's a completely new topic unrelated to ANY listed conversation, reply with NEW.

TASK 2: Extract ONLY the facts/context from the conversations that are directly relevant to responding to the new message.
- If the message references a past topic, person, or event, include that context briefly.
- If nothing is relevant, reply with NO_RELEVANT_CONTEXT for the context part.

OUTPUT FORMAT:
Reply in exactly this format:
CONVO: [conversation ID number or NEW]
CONTEXT: [Relevant context summary or NO_RELEVANT_CONTEXT]

Recent conversations:
{conversations}

New message:
{new_message}"""


# ============================================================================
#  STRUCTURED LOGGING (Thread-safe, Block-formatted)
# ============================================================================

print_lock = threading.Lock()

class C:
    RESET   = "\033[0m"
    BOLD    = "\033[1m"
    GRAY    = "\033[90m"
    RED     = "\033[91m"
    GREEN   = "\033[92m"
    YELLOW  = "\033[93m"
    BLUE    = "\033[94m"
    MAGENTA = "\033[95m"
    CYAN    = "\033[96m"
    WHITE   = "\033[97m"
    BRIGHT_PURPLE = "\033[1;35m"

def tag_color(tag):
    t = tag.upper()
    if t.startswith("BOTRAM"):     return C.BRIGHT_PURPLE
    if t.startswith("LIBRARIAN"):   return C.CYAN
    if t.startswith("DDGS"):        return C.YELLOW
    if t.startswith("FRENCHMAID"):  return C.MAGENTA
    if t.startswith("ROLEPLAYER"):  return C.GREEN
    if t.startswith("CACHE"):       return C.BLUE
    if t in ("ERROR", "SKEPTIC"):   return C.RED
    if t == "RATE":                 return C.GRAY
    return C.WHITE

def log(tag, message):
    ts = datetime.now().strftime("%H:%M:%S")
    color = tag_color(tag)
    with print_lock:
        print(f"{C.GRAY}[{ts}]{C.RESET} {color}[{tag}]{C.RESET} {message}", flush=True)

def log_payload(tag, text, max_chars=MAX_LOG_CHARS):
    if not text:
        log(tag, "[Empty payload]")
        return
    
    # Robustly check the global DEBUG_FULL_LOGS flag
    if globals().get('DEBUG_FULL_LOGS', False):
        max_chars = 999999  # Effectively disable truncation
        
    text = str(text).replace('\\n', '\n')
    if len(text) > max_chars:
        text = text[:max_chars] + f"\n... [truncated {len(text) - max_chars} chars]"
        
    lines = text.split('\n')
    ts = datetime.now().strftime("%H:%M:%S")
    color = tag_color(tag)
    first_line = f"{C.GRAY}[{ts}]{C.RESET} {color}[{tag}]{C.RESET} {C.GRAY}| {lines[0]}{C.RESET}"
    indent = " " * (16 + len(tag))
    
    with print_lock:
        print(first_line, flush=True)
        for line in lines[1:]:
            print(f"{indent}{C.GRAY}| {line}{C.RESET}", flush=True)

def log_request_header(char_count, chat_msg):
    ts = datetime.now().strftime("%H:%M:%S")
    header = f"\n{C.GRAY}[{ts}]{C.RESET} {C.BOLD}{C.RED}=== NEW REQUEST RECEIVED ({char_count} chars) ==={C.RESET}"
    trigger = f"{C.GRAY}[{ts}]{C.RESET} {C.BOLD}{C.RED}>>> TRIGGER: {chat_msg}{C.RESET}"
    with print_lock:
        print(header, flush=True)
        print(trigger, flush=True)

# ============================================================================
#  RATE LIMITER & CACHE
# ============================================================================

class RateLimiter:
    def __init__(self, rps):
        self.min_interval = (1.0 / rps) if rps > 0 else 0
        self.last_request = 0.0
        self.lock = threading.Lock()
    def wait(self):
        if self.min_interval <= 0: return
        with self.lock:
            now = time.time()
            wait_time = self.min_interval - (now - self.last_request)
            if wait_time > 0:
                log("RATE", f"Waiting {wait_time:.1f}s to respect API rate limit...")
                time.sleep(wait_time)
            self.last_request = time.time()

rate_limiter = RateLimiter(ONLINE_API_REQUESTS_PER_SECOND)

_cache = {}
_cache_lock = threading.Lock()
_query_locks = {}
_query_locks_lock = threading.Lock()

def cache_get(key):
    with _cache_lock:
        if key in _cache:
            value, ts = _cache[key]
            if time.time() - ts < CACHE_TTL: return value
            del _cache[key]
    return None

def cache_set(key, value):
    with _cache_lock: _cache[key] = (value, time.time())

def get_query_lock(query):
    with _query_locks_lock:
        if query not in _query_locks: _query_locks[query] = threading.Lock()
        return _query_locks[query]

# ============================================================================
#  ENGINE CALLS
# ============================================================================

def call_online_api(api_url, api_key, model, system_prompt, user_prompt, options):
    rate_limiter.wait()
    url = api_url.rstrip("/")
    if not url.endswith("/chat/completions"): url += "/chat/completions"
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    payload = {
        "model": model,
        "messages": [{"role": "system", "content": system_prompt}, {"role": "user", "content": user_prompt}],
        "temperature": options.get("temperature", 0.7),
        "max_tokens": options.get("num_predict", 256),
        "stream": False,
    }
    for attempt in range(3):
        try:
            resp = requests.post(url, headers=headers, json=payload, timeout=ONLINE_API_TIMEOUT)
            if resp.status_code in (500, 502, 503, 504, 429) and attempt < 2:
                log("API", f"Online API error ({model}): {resp.status_code}. Retrying...")
                time.sleep(2)
                continue
            resp.raise_for_status()
            return resp.json()["choices"][0]["message"]["content"]
            
        except (requests.exceptions.SSLError, requests.exceptions.ConnectionError, requests.exceptions.Timeout) as e:
            if attempt < 2:
                log("API", f"Network/SSL drop ({model}). Retrying ({attempt+1}/3)...")
                time.sleep(3)
                continue
            log("API", f"Network/SSL fatal ({model}): {e}")
            return ""
            
        except Exception as e:
            log("API", f"Online API error ({model}): {e}")
            return ""

def call_ollama(model, system_prompt, user_prompt, options):
    payload = {
        "model": model, "prompt": user_prompt, "system": system_prompt, "stream": False,
        "options": {"temperature": options.get("temperature", 0.7), "num_predict": options.get("num_predict", 256)},
    }
    try:
        resp = requests.post(OLLAMA_URL, json=payload, timeout=OLLAMA_TIMEOUT)
        resp.raise_for_status()
        return resp.json().get("response", "")
    except Exception as e:
        log("OLLAMA", f"Ollama error ({model}): {e}")
        return ""

def call_llm(engine, system_prompt, user_prompt, options):
    if engine["api_url"] and engine["api_key"]:
        return call_online_api(engine["api_url"], engine["api_key"], engine["model"], system_prompt, user_prompt, options)
    return call_ollama(engine["model"], system_prompt, user_prompt, options)

LIBRARIAN_CONFIG = {"api_url": LIBRARIAN_API_URL, "api_key": LIBRARIAN_API_KEY, "model": LIBRARIAN_MODEL}
FRENCHMAID_CONFIG = {"api_url": FRENCHMAID_API_URL, "api_key": FRENCHMAID_API_KEY, "model": FRENCHMAID_MODEL}
ROLEPLAYER_CONFIG = {"api_url": ROLEPLAYER_API_URL, "api_key": ROLEPLAYER_API_KEY, "model": ROLEPLAYER_MODEL}


def clean_wow_links(text):
    """Strips WoW color/link codes and replaces them with just the bracketed name."""
    if not text:
        return text
    # Replace standard WoW link format |cXXXXXXXX|H...|h[Name]|h|r with [Name]
    text = re.sub(r'\|c[0-9a-fA-F]{8}\|H.*?\|h\[(.*?)\]\|h\|r', r'[\1]', text)
    # Clean up any stray formatting codes just in case
    text = re.sub(r'\|[cHhr][0-9a-fA-F]*', '', text)
    return text

# ============================================================================
#  PASS 1 & POST: BOTRAM (Unified Memory Parser)
# ============================================================================

class BotRAM:
    def __init__(self):
        self.conn = sqlite3.connect(BOTRAM_DB_PATH, check_same_thread=False)
        self.lock = threading.Lock()
        self._init_db()

    def _init_db(self):
        with self.lock:
            self.conn.execute("""CREATE TABLE IF NOT EXISTS conversations (
                id INTEGER PRIMARY KEY AUTOINCREMENT, status TEXT DEFAULT 'active',
                last_valid_timestamp DATETIME DEFAULT CURRENT_TIMESTAMP, participants TEXT)""")
            self.conn.execute("""CREATE TABLE IF NOT EXISTS phrases (
                id INTEGER PRIMARY KEY AUTOINCREMENT, convo_id INTEGER, speaker TEXT,
                text TEXT, timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(convo_id) REFERENCES conversations(id))""")
            
            self.conn.execute("DELETE FROM phrases WHERE timestamp < datetime('now', ?)", (f'-{BOTRAM_RETENTION_DAYS} days',))
            self.conn.execute("DELETE FROM conversations WHERE last_valid_timestamp < datetime('now', ?)", (f'-{BOTRAM_RETENTION_DAYS} days',))
            self.conn.commit()
            log("BOTRAM", f"Initialized DB. Cleaned records older than {BOTRAM_RETENTION_DAYS} days.")

    def extract_identity(self, prompt):
        # Clean WoW links FIRST, before any regex parsing
        prompt = clean_wow_links(prompt)
        prompt = prompt.replace('\\n', '\n')
        bot_match = re.search(r"^You are (\w+)", prompt)
        bot_name = bot_match.group(1) if bot_match else "UnknownBot"
        
        target_match = re.search(r"NEW MESSAGE from (\w+):", prompt)
        if not target_match:
            target_match = re.search(r"(\w+) says: '.*?'\.\s*Your Info:", prompt)
        if not target_match:
            target_match = re.search(r"Event:\s*(\w+)", prompt)
        
        target_name = target_match.group(1) if target_match else "-ambient-"
        
        zone_match = re.search(r"Zone:\s*([\w\s]+),", prompt)
        zone = zone_match.group(1).strip() if zone_match else "Unknown"
        
        return bot_name, target_name, zone, prompt

    def strip_cpp_history(self, prompt):
        return re.sub(r"Recent chats with .*?(?=NEW MESSAGE from|\w+ says: ')", "", prompt, flags=re.DOTALL)

    def gather_memory_context(self, bot_name, target_name):
        history_text = ""
        seen_ids = set()
        
        # 1. Recent convos involving the TARGET (player) OR the BOT.
        # This allows the bot to "remember" its own recent chats even if the player wasn't in them.
        if target_name != "-ambient-":
            convos = self.conn.execute("""
                SELECT c.id FROM conversations c
                WHERE c.participants LIKE ? OR c.participants LIKE ?
                ORDER BY c.last_valid_timestamp DESC LIMIT ?
            """, (f'%{target_name}%', f'%{bot_name}%', BOTRAM_PAST_CONVOS_LIMIT)).fetchall()
            
            for convo_row in convos:
                cid = convo_row[0]
                seen_ids.add(cid)
                rows = self.conn.execute("SELECT speaker, text FROM phrases WHERE convo_id=? ORDER BY timestamp DESC LIMIT ?", 
                                         (cid, BOTRAM_PHRASES_PER_CONVO)).fetchall()
                if rows:
                    history_text += f"\n[Conversation ID {cid}]:\n"
                    history_text += "\n".join([f"{r[0]}: {r[1]}" for r in reversed(rows)]) + "\n"

        # 2. Ambient convos (bot only, within 5 mins)
        ambient_convos = self.conn.execute("""
            SELECT c.id FROM conversations c
            WHERE c.participants = ? AND c.last_valid_timestamp > datetime('now', '-5 minutes')
            ORDER BY c.last_valid_timestamp DESC LIMIT ?
        """, (bot_name, BOTRAM_AMBIENT_CONVOS_LIMIT)).fetchall()
        
        for convo_row in ambient_convos:
            cid = convo_row[0]
            if cid in seen_ids:
                continue
            seen_ids.add(cid)
            rows = self.conn.execute("SELECT speaker, text FROM phrases WHERE convo_id=? ORDER BY timestamp DESC LIMIT ?", 
                                     (cid, BOTRAM_PHRASES_PER_CONVO)).fetchall()
            if rows:
                history_text += f"\n[Conversation ID {cid} - Ambient]:\n"
                history_text += "\n".join([f"{r[0]}: {r[1]}" for r in reversed(rows)]) + "\n"
        
        if history_text:
            log("BOTRAM", f"Gathered {len(seen_ids)} conversations for memory parsing.")
            
        return history_text.strip()

    def process_incoming(self, prompt, system_prompt):
        if not BOTRAM_ENABLED:
            return prompt.replace('\\n', '\n'), system_prompt, None, None, None, None, None

        bot_name, target_name, zone, clean_prompt = self.extract_identity(prompt)
        clean_prompt = self.strip_cpp_history(clean_prompt)
        
        player_msg = ""
        is_event = False
        
        msg_match = re.search(r"NEW MESSAGE from \w+:\s*(.*?)\s*\w+ says:", clean_prompt, re.DOTALL)
        if not msg_match:
            msg_match = re.search(r"\w+ says:\s*'(.*?)'", clean_prompt)
        if not msg_match:
            # NEW: Capture event text so events can join existing conversations
            msg_match = re.search(r"Event:\s*(.*?)(?=\.\s+React|\.\s+Avoid|\.\s+You are playing|$)", clean_prompt, re.DOTALL)
            if msg_match:
                is_event = True
                
        if msg_match:
            player_msg = clean_wow_links(msg_match.group(1).strip())

        with self.lock:
            now = datetime.utcnow()
            convo_id = None
            is_short_circuit = False
            memory_context = ""
            
            # 1. Ambient chatter: join active convo or create new
            if target_name == "-ambient-":
                row = self.conn.execute("""SELECT id FROM conversations 
                    WHERE participants LIKE ? AND status = 'active' 
                    AND last_valid_timestamp > datetime('now', ?)
                    ORDER BY last_valid_timestamp DESC LIMIT 1""", 
                    (f'%{bot_name}%', f'-{BOTRAM_AMBIENT_JOIN_MINUTES} minutes')).fetchone()
                if row:
                    convo_id = row[0]
                    log("BOTRAM", f"Ambient chatter joining active convo {convo_id}.")

            # 2. Direct messages (and events): run Memory Parser to find the right convo
            elif player_msg:
                history_text = self.gather_memory_context(bot_name, target_name)
                if history_text:
                    log_payload("BOTRAM_CONTEXT", history_text)
                    
                    recall_prompt = BOTRAM_MEMORY_RECALL_PROMPT.replace("{conversations}", history_text).replace("{new_message}", player_msg)
                    recall_resp = call_ollama(BOTRAM_MODEL, "", recall_prompt, {"temperature": 0.1, "num_predict": 150}).strip()
                    log_payload("BOTRAM_MEMORY_RAW", recall_resp)
                    
                    target_convo_id = None
                    context = ""
                    for line in recall_resp.split('\n'):
                        if line.upper().startswith("CONVO:"):
                            convo_val = line.split(':', 1)[1].strip().upper()
                            if convo_val != "NEW":
                                # Strip any non-numeric characters (like brackets) before parsing
                                digits = re.sub(r'\D', '', convo_val)
                                if digits:
                                    target_convo_id = int(digits)
                                else:
                                    target_convo_id = None
                        elif line.upper().startswith("CONTEXT:"):
                            context = line.split(':', 1)[1].strip()
                    
                    # Resolve the target conversation
                    if target_convo_id is not None:
                        row = self.conn.execute("SELECT id, status, last_valid_timestamp, participants FROM conversations WHERE id=?", (target_convo_id,)).fetchone()
                        if row:
                            cid, db_status, last_valid_str, participants = row
                            last_valid = datetime.strptime(last_valid_str, "%Y-%m-%d %H:%M:%S")
                            time_diff = (now - last_valid).total_seconds() / 60.0
                            
                            if db_status == 'over':
                                if time_diff < BOTRAM_LOOP_COOLDOWN_MINUTES:
                                    is_short_circuit = True
                                    convo_id = cid
                                    log("BOTRAM", f"Memory Parser matched convo {cid}, but it's OVER (cooldown). Short-circuiting.")
                                else:
                                    self.conn.execute("UPDATE conversations SET status='active' WHERE id=?", (cid,))
                                    convo_id = cid
                                    log("BOTRAM", f"Resurrecting expired cooldown convo {cid}.")
                            else:
                                convo_id = cid
                                log("BOTRAM", f"Memory Parser matched convo {cid}. Joining.")
                                new_parts = set(participants.split(','))
                                new_parts.add(bot_name)
                                if target_name != "-ambient-": new_parts.add(target_name)
                                self.conn.execute("UPDATE conversations SET participants=? WHERE id=?", (','.join(new_parts), cid))
                        else:
                            log("BOTRAM", f"Memory Parser returned convo {target_convo_id}, but it doesn't exist. Starting fresh.")
                    
                    # Inject context
                    if not is_short_circuit and context and "NO_RELEVANT_CONTEXT" not in context.upper():
                        memory_context = f"\n\n[BOTRAM MEMORY CONTEXT]:\n{context}\nUse this memory naturally. Do not reference it unless relevant."
                        log("BOTRAM", f"Memory Parser injected context for {bot_name}.")

            # 3. Create new convo if needed
            if not convo_id and not is_short_circuit:
                parts = f"{bot_name},{target_name}" if target_name != "-ambient-" else bot_name
                cur = self.conn.execute("INSERT INTO conversations (status, participants) VALUES ('active', ?)", (parts,))
                convo_id = cur.lastrowid
                log("BOTRAM", f"Started new conversation {convo_id} ({bot_name} -> {target_name})")

            # 4. Store incoming phrase
            if player_msg:
                if is_event:
                    speaker_name = "[EVENT]"
                else:
                    speaker_name = target_name if target_name != "-ambient-" else bot_name
                    
                is_bot_echo = self.conn.execute("""
                    SELECT 1 FROM phrases 
                    WHERE speaker = ? AND text = ? AND timestamp > datetime('now', '-15 seconds')
                    LIMIT 1
                """, (speaker_name, player_msg)).fetchone()
                
                if not is_bot_echo:
                    self.conn.execute("INSERT INTO phrases (convo_id, speaker, text) VALUES (?, ?, ?)", 
                                      (convo_id, speaker_name, player_msg))
                    
                    if not is_short_circuit:
                        self.conn.execute("UPDATE conversations SET last_valid_timestamp=CURRENT_TIMESTAMP WHERE id=?", (convo_id,))
                else:
                    log("BOTRAM", f"Skipped duplicate event/echo from {speaker_name}.")

            # 5. Loop Detection Check
            if not is_short_circuit and convo_id:
                count = self.conn.execute("SELECT COUNT(*) FROM phrases WHERE convo_id=?", (convo_id,)).fetchone()[0]
                if count > BOTRAM_LOOP_CHECK_THRESHOLD and count % BOTRAM_LOOP_CHECK_INTERVAL == 0:
                    log("BOTRAM", f"Running loop detection for convo {convo_id}...")
                    last_msgs = self.conn.execute("SELECT speaker, text FROM phrases WHERE convo_id=? ORDER BY timestamp DESC LIMIT 8", (convo_id,)).fetchall()
                    msg_text = "\n".join([f"{m[0]}: {m[1]}" for m in reversed(last_msgs)])
                    
                    loop_prompt = BOTRAM_LOOP_PROMPT.replace("{messages}", msg_text)
                    loop_resp = call_ollama(BOTRAM_MODEL, "", loop_prompt, {"temperature": 0.1, "num_predict": 20}).strip().upper()
                    
                    if "LOOP" in loop_resp:
                        self.conn.execute("UPDATE conversations SET status='over' WHERE id=?", (convo_id,))
                        is_short_circuit = True
                        log("BOTRAM", f"LOOP DETECTED in convo {convo_id}. Marking OVER and short-circuiting.")

            self.conn.commit()

        final_system = system_prompt + memory_context if memory_context else system_prompt
        short_circuit_response = "" if is_short_circuit else None
        return clean_prompt, final_system, short_circuit_response, convo_id, bot_name, target_name, player_msg

    def process_outgoing(self, convo_id, bot_name, target_name, player_msg, bot_reply):
        if not BOTRAM_ENABLED or not convo_id: return
        
        # Clean links from bot reply before storing
        clean_reply = clean_wow_links(str(bot_reply))
        
        if clean_reply is None or not clean_reply.strip():
            if target_name == "-ambient-":
                log("BOTRAM", f"{bot_name} stayed silent. No phrase stored.")
            else:
                log("BOTRAM", f"{bot_name} stayed silent toward {target_name}. No phrase stored.")
            return
            
        with self.lock:
            row = self.conn.execute("SELECT status FROM conversations WHERE id=?", (convo_id,)).fetchone()
            if row and row[0] == 'over':
                self.conn.execute("INSERT INTO phrases (convo_id, speaker, text) VALUES (?, ?, ?)", 
                                  (convo_id, bot_name, clean_reply))
                if target_name == "-ambient-":
                    log("BOTRAM", f"{bot_name} posted an ignored ambient thought: \"{clean_reply}\"")
                else:
                    log("BOTRAM", f"{bot_name} said to {target_name} (ignored, convo over): \"{clean_reply}\"")
            else:
                self.conn.execute("INSERT INTO phrases (convo_id, speaker, text) VALUES (?, ?, ?)", 
                                  (convo_id, bot_name, clean_reply))
                self.conn.execute("UPDATE conversations SET last_valid_timestamp=CURRENT_TIMESTAMP WHERE id=?", (convo_id,))
                if target_name == "-ambient-":
                    log("BOTRAM", f"{bot_name} posted an ambient thought: \"{clean_reply}\"")
                else:
                    log("BOTRAM", f"{bot_name} said to {target_name}: \"{clean_reply}\"")
            self.conn.commit()

botram = BotRAM()

# ============================================================================
#  PASS 2: LIBRARIAN + DDGS
# ============================================================================

def parse_librarian_json(raw):
    default = {"search": False, "query": None}
    if not raw: return default
    try:
        data = json.loads(raw.strip())
        return {"search": bool(data.get("search", False)), "query": data.get("query")}
    except Exception: pass
    match = re.search(r"\{.*\}", raw, re.DOTALL)
    if match:
        try:
            data = json.loads(match.group(0))
            return {"search": bool(data.get("search", False)), "query": data.get("query")}
        except Exception: pass
    return default

def run_librarian_and_search(full_prompt):
    options = {"temperature": LIBRARIAN_TEMPERATURE, "num_predict": 150}
    raw = call_llm(LIBRARIAN_CONFIG, LIBRARIAN_SYSTEM_PROMPT, full_prompt, options)
    log_payload("LIBRARIAN_RAW", raw)
    decision = parse_librarian_json(raw)
    log("LIBRARIAN", f"DECISION: search={decision['search']} | query={decision['query']!r}")
    
    web_context = ""
    if decision["search"] and decision["query"]:
        query = decision["query"]
        cached = cache_get(query)
        if cached is not None:
            web_context = cached
            log("CACHE", f"HIT for query: {query!r}")
        else:
            lock = get_query_lock(query)
            with lock:
                cached = cache_get(query)
                if cached is not None:
                    web_context = cached
                    log("CACHE", f"HIT (after wait) for query: {query!r}")
                else:
                    if DDGS is not None:
                        try:
                            results = []
                            with DDGS() as ddgs:
                                for r in ddgs.text(query, max_results=SEARCH_RESULT_COUNT):
                                    results.append(r)
                            log("DDGS", f"Fetched {len(results)} raw results for {query!r}")
                            
                            if FRENCHMAID_ENABLED and results:
                                maid_input = f"SEARCH QUERY: {query}\n\nSEARCH RESULTS:\n"
                                for i, r in enumerate(results, 1):
                                    maid_input += f"[Result {i}] Title: {r.get('title', '')}\n{r.get('body', '')}\n\n"
                                
                                maid_raw = call_llm(FRENCHMAID_CONFIG, FRENCHMAID_SYSTEM_PROMPT, maid_input, {"temperature": FRENCHMAID_TEMPERATURE, "num_predict": 500})
                                log_payload("FRENCHMAID_RAW", maid_raw)
                                
                                if FRENCHMAID_NO_DATA_MARKER not in maid_raw:
                                    cleaned = re.sub(r'\n+', '\n', maid_raw.strip())
                                    if len(cleaned) > FRENCHMAID_MAX_OUTPUT_LENGTH:
                                        cleaned = cleaned[:FRENCHMAID_MAX_OUTPUT_LENGTH]
                                    web_context = f"\n\n[WEB SEARCH CONTEXT]:\n{cleaned}\nUse this factual information to answer accurately."
                                    log_payload("FRENCHMAID_EXTRACTED", cleaned)
                                else:
                                    log("FRENCHMAID", "Model reported NO_USEFUL_DATA.")
                                    if SKEPTIC_MODE:
                                        web_context = f"\n\n[SYSTEM OVERRIDE - SKEPTIC MODE]:\n{SKEPTIC_OVERRIDE_PROMPT}"
                                        log("SKEPTIC", "No data found. Injecting Skeptic Mode override.")
                            else:
                                kept = [f"- {r.get('title', '')}: {r.get('body', '')[:FRENCHMAID_MAX_SNIPPET_LENGTH]}" for r in results if r.get('body')]
                                if kept:
                                    web_context = "\n\n[WEB SEARCH CONTEXT]:\n" + "\n".join(kept)
                                    
                        except Exception as e:
                            log("DDGS", f"DDGS error: {e}")
                            
                    cache_set(query, web_context)
                    log("CACHE", f"SET for query: {query!r}")
    return web_context

# ============================================================================
#  PASS 4: ROLEPLAYER
# ============================================================================

def clean_final_response(text):
    if not text: return "Hmm..."
    text = re.sub(r"\*\*(.*?)\*\*", r"\1", text)   
    text = re.sub(r"\*(.*?)\*", r"\1", text)        
    text = re.sub(r"`(.*?)`", r"\1", text)          
    text = re.sub(r"^[\s>#\-\*]+", "", text, flags=re.MULTILINE)
    text = text.strip()
    if len(text) > MAX_RESPONSE_LENGTH:
        text = text[:MAX_RESPONSE_LENGTH - 3] + "..."
    return text

def run_roleplayer(prompt, system_prompt, options):
    log_payload("ROLEPLAYER_SYS_PROMPT", system_prompt)
    raw = call_llm(ROLEPLAYER_CONFIG, system_prompt, prompt, options)
    log_payload("ROLEPLAYER_RAW", raw)
    return clean_final_response(raw)

# ============================================================================
#  REQUEST PIPELINE
# ============================================================================

def process_request(prompt, system, options):
    clean_prompt, final_system, short_circuit, convo_id, bot_name, target_name, player_msg = botram.process_incoming(prompt, system)
    
    log_payload("PROMPT_IN", clean_prompt)
    
    if short_circuit is not None:
        log("ROLEPLAYER", f"FINAL REPLY (SHORT-CIRCUIT): [Silent/Empty]")
        botram.process_outgoing(convo_id, bot_name, target_name, player_msg, short_circuit)
        return short_circuit

    web_context = run_librarian_and_search(clean_prompt)
    if web_context:
        final_system += web_context

    response = run_roleplayer(clean_prompt, final_system, options)
    log("ROLEPLAYER", f"FINAL REPLY: '{response}'")
    
    botram.process_outgoing(convo_id, bot_name, target_name, player_msg, response)
    
    return response

# ============================================================================
#  HTTP SERVER
# ============================================================================

class ProxyHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        if self.path != "/api/generate":
            self.send_error(404)
            return

        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length)
        try:
            data = json.loads(body.decode("utf-8"))
        except Exception:
            self.send_error(400, "Invalid JSON")
            return

        prompt = data.get("prompt", "")
        system = data.get("system", "")
        options = data.get("options", {})

        chat_msg = ""
        clean_prompt = prompt.replace('\\n', '\n')
        
        match_player = re.search(r"(\w+) says: '(.*?)'\.\s*Your Info:", clean_prompt, re.DOTALL)
        if match_player:
            chat_msg = f"[CHAT -> {match_player.group(1)}] {match_player.group(2).strip()}"
        else:
            match_event = re.search(r"Event:\s*(.*?)(?=\.\s+React|\.\s+Avoid|\.\s+You are playing|$)", clean_prompt, re.DOTALL)
            if match_event:
                chat_msg = f"[EVENT] {match_event.group(1).strip()}"
            else:
                if clean_prompt.startswith("You are "):
                    instr_match = re.search(r"(Talk about|Ask|Share|Comment on|Plan your|Evaluate|Discuss|Say|Rant about|Suggest|Look at|Mention|Complain about|Brag about|Debate|Tell|Describe) [^.]+\.?", clean_prompt)
                    if instr_match:
                        chat_msg = f"[BOT INITIATED] {instr_match.group(0).strip()}"
                    else:
                        chat_msg = f"[BOT INITIATED] {clean_prompt[:100].replace(chr(10), ' ')}..."
                else:
                    chat_msg = f"[UNKNOWN] {clean_prompt[:100].replace(chr(10), ' ')}..."

        log_request_header(len(prompt), chat_msg)

        try:
            response = process_request(prompt, system, options)
        except Exception as e:
            log("ERROR", f"Pipeline failed: {e}")
            response = ""

        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps({"response": response}).encode("utf-8"))

    def log_message(self, fmt, *args):
        pass

# ============================================================================
#  STARTUP
# ============================================================================

def validate_config():
    for name, cfg in [("LIBRARIAN", LIBRARIAN_CONFIG), ("FRENCHMAID", FRENCHMAID_CONFIG), ("ROLEPLAYER", ROLEPLAYER_CONFIG)]:
        has_url = bool(cfg["api_url"])
        has_key = bool(cfg["api_key"])
        if has_url and has_key:
            log("CONFIG", f"{name}: ONLINE model '{cfg['model']}'")
        elif has_url or has_key:
            log("CONFIG", f"WARNING: {name} partial API config. Falling back to local Ollama '{cfg['model']}'.")
        else:
            log("CONFIG", f"{name}: LOCAL Ollama model '{cfg['model']}'")

def main():
    log("STARTUP", f"SmartProxyAgentic v{VERSION} starting...")
    validate_config()
    server = ThreadingHTTPServer((PROXY_HOST, PROXY_PORT), ProxyHandler)
    log("STARTUP", f"Listening on http://{PROXY_HOST}:{PROXY_PORT}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        log("STARTUP", "Shutting down.")
        server.shutdown()

if __name__ == "__main__":
    main()