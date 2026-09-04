# ============================================================================
#  CONFIGURATION & PROMPTS (v7a)
# ============================================================================

VERSION = "7a"
DEBUG_FULL_LOGS = False  # Set to True to disable log truncation

# --- Proxy server ---
PROXY_HOST = "127.0.0.1"
PROXY_PORT = 8000
OLLAMA_URL = "http://127.0.0.1:11434/api/generate"

# --- BOTRAM ---
BOTRAM_ENABLED = True
BOTRAM_DB_PATH = "botram.db"
BOTRAM_MODEL = "qwen2.5:14b"
BOTRAM_PAST_CONVOS_LIMIT = 10
BOTRAM_PHRASES_PER_CONVO = 7
BOTRAM_AMBIENT_CONVOS_LIMIT = 2
BOTRAM_AMBIENT_JOIN_MINUTES = 5
BOTRAM_RETENTION_DAYS = 7
BOTRAM_ACTIVE_WINDOW_MINUTES = 5
BOTRAM_LOOP_CHECK_THRESHOLD = 6
BOTRAM_LOOP_CHECK_INTERVAL = 4
BOTRAM_LOOP_COOLDOWN_MINUTES = 30

# --- LIBRARIAN ---
LIBRARIAN_API_URL = ""
LIBRARIAN_API_KEY = ""
LIBRARIAN_MODEL = "qwen2.5:7b"
LIBRARIAN_TEMPERATURE = 0.1

# --- FRENCHMAID ---
FRENCHMAID_ENABLED = True
FRENCHMAID_API_URL = ""
FRENCHMAID_API_KEY = ""
FRENCHMAID_MODEL = "qwen2.5:7b"
FRENCHMAID_TEMPERATURE = 0.1
FRENCHMAID_NO_DATA_MARKER = "NO_USEFUL_DATA"
FRENCHMAID_MAX_OUTPUT_LENGTH = 800
FRENCHMAID_MAX_SNIPPET_LENGTH = 350

# --- ROLEPLAYER ---
ROLEPLAYER_API_URL = "https://api.mistral.ai/v1/chat/completions"
ROLEPLAYER_API_KEY = "wRI4hmSFIOzwYAWaDKB6EWiXPjkzADtG"
ROLEPLAYER_MODEL = "mistral-medium-latest"

# --- Rate limiting & Timeouts ---
ONLINE_API_REQUESTS_PER_SECOND = 1
ONLINE_API_TIMEOUT = 60
OLLAMA_TIMEOUT = 30
DDGS_TIMEOUT = 15
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
2. BE AGGRESSIVE WITH SEARCHES. If the player asks about a location, NPC, quest, item, flight path, dungeon, or game mechanic, you MUST search. 
3. SMART CONTEXT RESOLUTION. Decide whether a query needs the bot's current zone or should be a global search.

CONTEXT RESOLUTION & QUERY BUILDING:
- LOCAL QUERIES: If the player asks about "here", "this zone", "nearby", or "around me", INCLUDE the bot's current "Zone: [Name]" from the prompt context in your query.
- EXPLICIT LOCATIONS: If the player explicitly names a specific zone, city, town, village, dungeon, or NPC (e.g., "Goldshire", "Ratchet", "Stormwind", "Maraudon", "Thrall"), use THAT name in the query and DO NOT append the bot's current zone. (Note: Players frequently misspell names, e.g., "Ratched" instead of "Ratchet". Deduce the intended name and search for that).
- GLOBAL QUERIES: If the player asks about general items (potions, food, materials), general game mechanics, class trainers, or a specific quest name, DO NOT append the bot's current zone. Search globally.
- PRONOUNS & HISTORY: Use the chat history to resolve pronouns like "he", "she", "it", or "that quest".
- ACRONYMS: "FP" means Flight Path. "BiS" means Best in Slot.
- CLASS CONTEXT: If they say "for me" or "my class", extract their Class from the "Player Info" block.
- WoW LINKS: Extract names from brackets like |Hquest:123:40|h[Name]|h|r.

WHEN TO SET search = false:
- The message is just a greeting, goodbye, or short reaction (e.g., "lol", "true", "k", "thanks", "what").
- The message is purely conversational banter with no factual question.

QUERY RULES (only when search = true):
- Write a concise query, under 12 words, plain text.
- Always append "wowhead wotlk" to the query.

OUTPUT FORMAT:
Reply with ONLY one JSON object. No markdown, no code fences.
You must include the "subject" key, which is the core person, place, item, or concept the player is asking about (e.g., "Ratchet", "Super Healing Potion", "Maraudon").
{"search": true, "query": "your query here", "subject": "the core subject"} 
or {"search": false, "query": null, "subject": null}"""

LORE_ARCHIVIST_SYSTEM_PROMPT = """You are a database archivist for an MMO knowledge database.
Condense the provided raw facts into a single, highly concise, comma-separated string of keywords and attributes.
Remove all filler words, bullet points, and conversational text.
Focus ONLY on locations, directions, NPC names, level requirements, and specific game mechanics.
Do NOT write full sentences. Just raw data points.
Output ONLY the condensed string. No quotes, no markdown, no prefix."""

FRENCHMAID_SYSTEM_PROMPT = """You are "FrenchMaid", a data-cleaning assistant for a World of Warcraft WotLK 3.3.5 server.
Extract ONLY specific facts that help answer the SEARCH QUERY. Ignore junk.
Translate foreign languages to English. Do NOT invent information.
Keep it concise: a few short plain-text bullet points.
If NO useful info, reply exactly: NO_USEFUL_DATA"""

BOTRAM_LOOP_PROMPT = """You are a loop detector for an MMO chat room. Analyze the recent messages.
Your job is to catch conversations that are stuck and going nowhere.

Flag as LOOP if:
- A bot repeats the exact same sentence word-for-word.
- The same two concepts are recycled endlessly without new ideas or progression.
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

FACT_EXTRACTION_PROMPT = """You are a fact extractor for an MMO chat system.
Given this conversation exchange, determine if any NEW, specific facts were established about the players, the guild, or the server.

A fact is: a personal preference, an achievement, a relationship, a server-specific rule, a recent personal event, guild or community related lore or news.
NOT a fact: greetings, jokes, vague opinions, banter without substance, or general WoW game mechanics/locations (those are handled elsewhere).

If a fact exists, output EXACTLY in this format:
CATEGORY: [player, guild, or server]
ENTITY: [comma-separated names involved]
FACT: [one-line summary]

If no new fact was established, output EXACTLY:
NO_FACT

Exchange:
{exchange}"""

# --- Engine Config Dictionaries ---
LIBRARIAN_CONFIG = {"apiUrl": LIBRARIAN_API_URL, "apiKey": LIBRARIAN_API_KEY, "model": LIBRARIAN_MODEL}
FRENCHMAID_CONFIG = {"apiUrl": FRENCHMAID_API_URL, "apiKey": FRENCHMAID_API_KEY, "model": FRENCHMAID_MODEL}
ROLEPLAYER_CONFIG = {"apiUrl": ROLEPLAYER_API_URL, "apiKey": ROLEPLAYER_API_KEY, "model": ROLEPLAYER_MODEL}
LORE_DB_PATH = "lore.db"
LORE_CONFIDENCE_THRESHOLD = 0.7   # Minimum confidence to accept a lore.db match (0.0 .. 1.0)