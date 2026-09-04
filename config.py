# ============================================================================
# CONFIGURATION & PROMPTS (v11)
# ============================================================================

VERSION = "11"

# --- Thinking Mode Toggles ---
# Enable Ollama thinking mode for complex analytical tasks. 
# Bumps token limits automatically when enabled.
CLASSIFIER_THINK_ENABLED = False
FACT_EXTRACTION_THINK_ENABLED = False
LORE_CORRECTION_THINK_ENABLED = False

# --- Logging ---
ENABLE_LOG_COLORS = True
DEBUG_FULL_LOGS = True
MAX_LOG_CHARS = 1500
LOG_DIR = "logs"
LOG_DATE_FORMAT = "%d-%m-%Y %H:%M:%S"

# --- Proxy server ---
PROXY_HOST = "127.0.0.1"
PROXY_PORT = 8000
OLLAMA_URL = "http://127.0.0.1:11434/api/generate"

# --- BOTRAM ---
BOTRAM_ENABLED = True                    # Master switch. False = no memory, no loop detection, no tracking.
BOTRAM_DB_PATH = "botram.db"             # SQLite file for conversations/phrases. Delete to reset all bot memory.
BOTRAM_MODEL = "qwen3.5:9b"              # Local Ollama model used for memory recall and loop detection.
BOTRAM_PAST_CONVOS_LIMIT = 7             # Max past conversations the Memory LLM sees when classifying a new message.
BOTRAM_PHRASES_PER_CONVO = 20            # Max messages per conversation included in the memory context.
BOTRAM_AMBIENT_CONVOS_LIMIT = 2          # Max ambient (bot-initiated) conversations included in memory context.
BOTRAM_AMBIENT_JOIN_MINUTES = 5          # Ambient messages join an existing convo if it was updated within this window.
BOTRAM_RETENTION_DAYS = 7                # Conversations older than this are permanently deleted on startup.
BOTRAM_ACTIVE_WINDOW_MINUTES = 3         # Ambient memory context only includes conversations updated within this window.
BOTRAM_MEMORY_WINDOW_MINUTES = 10        # Target-specific memory recall only includes conversations within this window.
BOTRAM_LOOP_CHECK_THRESHOLD = 4          # Minimum message count before loop detection can trigger.
BOTRAM_LOOP_CHECK_INTERVAL = 4           # After threshold, run loop detection every N messages (e.g. at 8, 12, 16...).
BOTRAM_LOOP_COOLDOWN_MINUTES = 15        # Looped conversations stay locked/silent for this long before resurrection.
BOTRAM_TRANSCRIPT_WINDOW = 5             # Number of phrases to grab before/after an FTS5 keyword match.

# --- GOSSIP / SERVER CONTEXT ---
BOTRAM_GOSSIP_ENABLED = True        # Master switch for server-gossip context retrieval.
BOTRAM_GOSSIP_WINDOW_HOURS = 24     # Scan chat history this many hours back for gossip.
BOTRAM_GOSSIP_MAX_CONVOS = 3        # Top conversations passed to gossip summarizer.
BOTRAM_GOSSIP_MAX_PHRASES = 100     # Hard total phrase cap to protect model context.
BOTRAM_GOSSIP_MIN_SCORE = 2         # Minimum unique keyword matches needed to consider convo.
BOTRAM_GOSSIP_MAX_KEYWORDS = 5      # Maximum keywords used from classifier output.

# --- LORE DB (The Wiki) ---
LORE_DB_PATH = "lore.db"
TRUSTED_FACT_MARKER = "that's a fact"    # Strict semantic trigger for Trusted Players to write to the Wiki.

# --- Web Search ---
WEB_SEARCH_QUERY_PREFIX = "wowhead wow classic"
WEB_SEARCH_EXCLUDED_TERMS = ""          # Python filters
SEARCH_RESULT_COUNT = 15                # Cast a wider net to ensure we catch the right snippet
WEB_SEARCH_DELAY = 3                    # Seconds to wait before hitting DDGS to avoid soft-blocks

# --- CLASSIFIER (Replaces Librarian) ---
CLASSIFIER_API_URL = ""
CLASSIFIER_API_KEY = ""
CLASSIFIER_MODEL = "qwen3.5:9b"
CLASSIFIER_TEMPERATURE = 0.1
TRUSTED_PLAYERS = ["neacris", "neakris"]

# --- FRENCHMAID ---
FRENCHMAID_ENABLED = True
FRENCHMAID_API_URL = ""
FRENCHMAID_API_KEY = ""
FRENCHMAID_MODEL = "qwen3.5:9b"
FRENCHMAID_TEMPERATURE = 0.1
FRENCHMAID_NO_DATA_MARKER = "NO_USEFUL_DATA"
FRENCHMAID_MAX_OUTPUT_LENGTH = 800
FRENCHMAID_MAX_SNIPPET_LENGTH = 350

# --- ROLEPLAYER ---
#ROLEPLAYER_API_URL = "https://api.mistral.ai/v1/chat/completions"
#ROLEPLAYER_API_KEY = "wRI4hmSFIOzwYAWaDKB6EWiXPjkzADtG"
#ROLEPLAYER_MODEL = "mistral-medium-latest"
ROLEPLAYER_API_URL = ""
ROLEPLAYER_API_KEY = ""
ROLEPLAYER_MODEL = "qwen3.5:9b"

# --- Rate limiting & Timeouts ---
ONLINE_API_REQUESTS_PER_SECOND = 1
ONLINE_API_TIMEOUT = 60
OLLAMA_TIMEOUT = 45

# --- Skeptic Mode ---
SKEPTIC_MODE = True
SKEPTIC_OVERRIDE_PROMPT = (
    "A web search was performed for this topic and found ZERO reliable evidence "
    "this exists in official WotLK 3.3.5. It is very likely a fake item, a myth, "
    "a private-server invention, or the player is mistaken. React like a real gamer: "
    "express doubt, ask if it even exists, or ask if they're talking about a different "
    "game or a private server. Do NOT pretend it exists or hallucinate facts about it."
    "CRITICAL: Ignore any [LORE CONTEXT] provided above. Do NOT output any locations, "
    "NPCs, or facts from your lore database for this reply. Only express doubt."
)

# --- Cache & Limits ---
CACHE_TTL = 15
MAX_RESPONSE_LENGTH = 255


#============================================================================
# SYSTEM PROMPTS
#============================================================================
CLASSIFIER_SYSTEM_PROMPT = """You are an intent classifier for a World of Warcraft chat bot.
You will receive recent chat history and a new player message. Determine the player's intent, generate a precise web search query, identify the main game entity, and extract keywords for server gossip.

CHAT LOG FORMAT:
Messages are formatted as `speaker: message`. Speakers frequently address each other by name at the end of their sentences (e.g., "10-20 Neacris" or "Sentinel Hill, Neacris"). Use common sense to understand the conversation, resolve pronouns (like "there" or "it"), and identify the actual game entities being discussed.

POSSIBLE INTENTS:
- "game_question": Asking about WoW mechanics, items, quests, NPCs, or locations.
- "social_question": Asking about past conversations, server history, or who said/did what.
- "statement": Banter, greetings, opinions, or stating a fact. (Default to this if unsure).

SELF-REFERENCE RULE: If the player is asking about YOUR level, class, spec, location, or identity (e.g., "what level are you?", "what class are you?"), classify it as "statement". Do NOT search the web for your own character stats.

FIELDS:
- "topic": A precise, keyword-dense web search query. Map generic terms to WoW terms ("flight path" -> "flight master"). No conversational filler. 
  LOCATION RULE: Do NOT inject a zone or location into the query UNLESS the player explicitly names one, uses a location pronoun ("there", "here", "in there"), or is continuing a conversation about the exact same location. If they ask about a specific NPC, quest, or item without mentioning a location, search ONLY for that NPC/quest/item name.
- "entity": The single canonical proper noun the question is primarily about. If a location is explicitly mentioned or referenced, group sub-locations under their parent Zone. If asking about an NPC/quest with no location context, use the NPC/quest name or null.
- "keywords": 2-5 important proper nouns/phrases for gossip lookup (zones, players, raids, events).

OUTPUT FORMAT:
Reply with ONLY one JSON object. No markdown, no code fences.
{"intent": "game_question", "topic": "search query", "entity": "Zone Name or NPC Name", "keywords": ["keyword1", "keyword2"]}
{"intent": "social_question", "topic": "search keywords", "entity": null, "keywords": ["keyword1"]}
{"intent": "statement", "topic": null, "entity": null, "keywords": ["keyword1"]}"""

GOSSIP_SUMMARIZER_PROMPT = """You are a server-gossip analyst for a World of Warcraft chat bot.
Below are recent server conversations and a new message. Find relevant context in the past conversations that helps answer or understand the new message.

CHAT LOG FORMAT:
Logs are formatted as `speaker: message`. Speakers often address each other by name. Use common sense to understand who is talking to whom and what they are discussing.

RULES:
- Use ONLY the provided conversations. Do not invent details.
- If no conversation contains useful context, reply exactly: NO_USEFUL_DATA
- If useful context exists, summarize the relevant server fact(s) in 1-3 short plain sentences.

NEW MESSAGE: {message}
KEYWORDS: {keywords}

CONVERSATIONS:
{conversations}

YOUR ANSWER:"""

TRANSCRIPT_SUMMARIZER_PROMPT = """You are a transcript analyst for a WoW chat bot.
Answer the player's question using ONLY the provided chat log snippet.

CHAT LOG FORMAT:
Logs are `speaker: message`. Understand the conversation naturally.

RULES:
- If the transcript does not explicitly contain the answer, reply EXACTLY with: NO_ANSWER
- Do not guess or hallucinate.
- If the answer is found, summarize it in one clear sentence.

PLAYER QUESTION: {question}

CHAT LOG SNIPPET:
{transcript}

YOUR ANSWER:"""

FACT_EXTRACTION_PROMPT = """You are a fact extraction engine for a World of Warcraft chat system.
A trusted player has stated or corrected a game fact. Extract the SINGLE most important game fact.

RULES:
- ENTITY must be a clean proper noun (max 3 words).
- HIERARCHY: If the fact is about a sub-location, camp, flight path, or landmark, the entity MUST be the parent Zone.
- FACT CONTENT: If you group under a parent Zone, the fact string MUST include the sub-location's name so the data isn't lost.
- Discard roleplay, combat descriptions, flavor text, and throwaway complaints.
- If no significant, useful game fact is present, return null for both fields.

EXAMPLES:
Statement: "Closest flight point to Zul'Gurub is Rebel Camp in Stranglethorn Vale, that's a fact."
Output: {"entity": "Stranglethorn Vale", "fact": "Rebel Camp is the closest flight point to Zul'Gurub."}

Statement: "Deepfury Bracers are item level 55, that's a fact."
Output: {"entity": "Deepfury Bracers", "fact": "Item level is 55."}

Statement: "You noob, Sentinel Hill is in Westfall, west of Darkshire, and that's a fact."
Output: {"entity": "Westfall", "fact": "Sentinel Hill is located west of Darkshire."}

Statement: "I finally hit 60! that's a fact."
Output: {"entity": null, "fact": null}

OUTPUT FORMAT:
Reply with ONLY the JSON object. No markdown.
Statement: {statement}"""

LORE_CORRECTION_PROMPT = """You are a fact-checking engine for a WoW knowledge base.
EXISTING FACTS about {entity}:
{existing_facts}

NEW CORRECTED FACT from a trusted player:
{new_fact}

Does any existing fact CONTRADICT the new corrected fact?
If YES: Output the EXACT line from the existing facts that should be replaced. Output ONLY that line.
If NO: Output exactly: APPEND"""

LORE_ARCHIVIST_SYSTEM_PROMPT = """You are a database archivist for a WoW knowledge base.
Extract ONLY concrete, atomic game facts from raw search results for the provided Entity.

RULES:
- Output exactly 1 clear sentence, maximum 15 words. No markdown, no prefixes.
- You MUST include the Entity name in the output fact so it is self-contained.
- Do not equate the Entity with an object inside it (e.g., write "Sentinel Hill has an inn", not "Sentinel Hill is an inn").
- If the text lacks a concrete fact or is about a completely different entity, output exactly: NO_USEFUL_DATA
- If an era is specified (Vanilla, TBC), include it in the fact.

EXAMPLES:
Entity: Western Plaguelands | Raw: "Alliance players can fly to Western Plaguelands from Chillwind Camp."
Output: Western Plaguelands alliance flight path is at Chillwind Camp.

Entity: Western Plaguelands | Raw: "Level range: 46-57."
Output: Western Plaguelands level range is 46-57.

Execute extraction. Output ONLY the fact or NO_USEFUL_DATA."""

FRENCHMAID_SYSTEM_PROMPT = """You are "FrenchMaid", a data-cleaning assistant for a WoW Classic server.
Extract ONLY explicit facts from the search results that directly answer the search query provided by the user.

RULES:
- If the exact answer is NOT explicitly stated in the text, reply exactly: NO_USEFUL_DATA
- Do NOT guess, infer travel routes, or invent information.
- Keep it concise: a few short plain-text bullet points.
- Translate foreign languages to English."""

BOTRAM_LOOP_PROMPT = """You are a loop detector for a WoW chat room. Analyze the recent messages.

RULES:
- Reply LOOP if bots are repeating the exact same sentences or recycling concepts endlessly.
- Reply CONTINUE if the conversation is progressing or just exchanging short acknowledgments.

Reply ONLY with "LOOP" or "CONTINUE".

Messages:
{messages}"""

BOTRAM_MEMORY_RECALL_PROMPT = """You are a memory filter and conversation classifier for an MMO chat bot.
Below are recent conversations the bot and the player were involved in, and a new incoming message.

CHAT LOG FORMAT:
Conversations are formatted as `speaker: message`. Speakers often address each other by name. Understand the flow of conversation naturally.

TASK 1: THREADING
Determine which conversation the new message belongs to. 
Carefully read the new message and look for matching keywords, names, locations, or topics in the listed conversations.
If it continues, references, or returns to one of the listed conversations (even using pronouns like "there", "it", or "that"), reply with that conversation's ID number.
If it's a completely new topic unrelated to ANY listed conversation, reply with NEW.

TASK 2: CONTEXT
Extract ONLY the facts/context directly relevant to responding to the new message. Do not invent backstory. If nothing is relevant, reply with NO_RELEVANT_CONTEXT.

OUTPUT FORMAT:
CONVO: [ID or NEW]
CONTEXT: [Summary or NO_RELEVANT_CONTEXT]

Recent conversations:
{conversations}

New message:
{new_message}"""

ROLEPLAYER_SYSTEM_PROMPT = """You are a real human playing a character in a World of Warcraft guild chat.
Your goal is light roleplay: embody the character personality and lore provided below, but maintain the casual, brief, and slang-heavy typing style of an actual MMO player. Strike a good balance between out-of-character chatting and light in-character roleplay.
Keep responses to 1-2 short sentences. Never sound like an AI assistant."""

# --- Engine Config Dictionaries ---
CLASSIFIER_CONFIG = {"apiUrl": CLASSIFIER_API_URL, "apiKey": CLASSIFIER_API_KEY, "model": CLASSIFIER_MODEL}
FRENCHMAID_CONFIG = {"apiUrl": FRENCHMAID_API_URL, "apiKey": FRENCHMAID_API_KEY, "model": FRENCHMAID_MODEL}
ROLEPLAYER_CONFIG = {"apiUrl": ROLEPLAYER_API_URL, "apiKey": ROLEPLAYER_API_KEY, "model": ROLEPLAYER_MODEL}