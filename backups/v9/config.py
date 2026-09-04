# ============================================================================
# CONFIGURATION & PROMPTS (v9)
# ============================================================================

VERSION = "9"

# --- Logging ---
ENABLE_LOG_COLORS = False
DEBUG_FULL_LOGS = False
MAX_LOG_CHARS = 1000

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

# --- LORE DB ---
LORE_DB_PATH = "lore.db"
LORE_CONFIDENCE_THRESHOLD = 0.4

# --- v9: Confidence Matrix ---
CONFIDENCE_GAME_WEB = 0.80
CONFIDENCE_GAME_TRUSTED_PLAYER = 0.75
CONFIDENCE_GAME_CHAT = 0.40
CONFIDENCE_CHARACTER_TRUSTED_PLAYER = 0.90
CONFIDENCE_CHARACTER_CHAT = 0.50
CONFIDENCE_SERVER_TRUSTED_PLAYER = 0.90
CONFIDENCE_SERVER_CHAT = 0.60

# --- v9: Lore routing & surgery ---
LORE_GAME_BLOCK_WEB_THRESHOLD = 0.80
LORE_FACT_SURGERY_MAX_CHARS = 300
LORE_CONFIDENCE_THRESHOLD = 0.4
LORE_FACT_TEXT_SEARCH = True      # Fallback: search text in "facts" lore table column by keywords, when entity search misses

# --- v9: Web search prefix ---
WEB_SEARCH_QUERY_PREFIX = "wowpedia wotlk 3.3.5"

# --- LIBRARIAN ---
LIBRARIAN_API_URL = ""
LIBRARIAN_API_KEY = ""
LIBRARIAN_MODEL = "qwen3.5:9b"
LIBRARIAN_TEMPERATURE = 0.1
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
ROLEPLAYER_API_URL = "https://api.mistral.ai/v1/chat/completions"
ROLEPLAYER_API_KEY = "wRI4hmSFIOzwYAWaDKB6EWiXPjkzADtG"
ROLEPLAYER_MODEL = "mistral-medium-latest"

# --- Rate limiting & Timeouts ---
ONLINE_API_REQUESTS_PER_SECOND = 1
ONLINE_API_TIMEOUT = 60
OLLAMA_TIMEOUT = 45
DDGS_TIMEOUT = 15
SEARCH_RESULT_COUNT = 10

# --- Skeptic Mode ---
SKEPTIC_MODE = True
SKEPTIC_OVERRIDE_PROMPT = (
    "A web search was performed for this topic and found ZERO reliable evidence "
    "this exists in official WotLK 3.3.5. It is very likely a fake item, a myth, "
    "a private-server invention, or the player is mistaken. React like a real gamer: "
    "express doubt, ask if it even exists, or ask if they're talking about a different "
    "game or a private server. Do NOT pretend it exists or hallucinate facts about it."
)

# --- Cache & Limits ---
CACHE_TTL = 15
MAX_RESPONSE_LENGTH = 255


# ============================================================================
# SYSTEM PROMPTS
# ============================================================================

LIBRARIAN_SYSTEM_PROMPT = """You are the "Librarian", a search-routing assistant for a World of Warcraft WotLK 3.3.5 server.
Your ONLY job is to evaluate the NEWEST player message and classify its intent.

CATEGORIZATION RULES (Apply in order):

game: Is this asking about an objective WoW 3.3.5 mechanic, location, item, NPC, or quest? (True on ALL official servers). Do NOT classify server-specific or private-server rules as game.

character: Is this about a specific player or bot on this server: their specs, plans, relationships, opinions, claims, achievements, OR asking the bot to recall something about the player?

server: Is this about a specific guild, community event, server meta, or a rule explicitly stated as applying only to this server? Do NOT classify objective game mechanics as server.

none: Is this just a greeting, banter, short reaction, or transient opinion?

REVERSE LOOKUP / "WHO" QUESTIONS:
If the player asks "Who said...", "Who did...", "Who claimed...", or asks to identify a player/bot based on an action, quote, or event, classify it as "character" (or "server" if it's a guild event), NOT "game". The subject of the query is the unknown person, not the game item/NPC/zone mentioned in the quote.

CRITICAL DIRECTIVES:
YOU ARE A BACKEND ROUTER. Your ONLY valid output is the raw JSON object.
If category is "game", you MUST generate a search query.
If category is "character" or "server", DO NOT search the web. The chat extractor will handle it.

LOCATION RESOLUTION (Strict Priority Order for "game" queries):
EXPLICIT: If the player names a specific location in game like a city, zone, or dungeon (e.g., "in SM", "Maraudon", "Goldshire"), use THAT name. DO NOT append the bot's current zone.
IMPLICIT (CONTEXT): Read the [BOTRAM MEMORY CONTEXT] carefully. If it explicitly names a zone or location being discussed (e.g., "Westfall"), use THAT location. DO NOT fall back to the bot's physical zone if the context already establishes the topic.
EXPLICIT LOCAL: If the player explicitly asks about "here", "near me", or "this zone", only then you INCLUDE the bot's current "Zone: [Name]".
GLOBAL: If asking about general items, classes, spells, or game mechanics, DO NOT append the bot's current zone.
DESPERATE FALLBACK: ONLY if the query strictly requires a location (e.g., "where is the flight path?") AND rules 1-4 provided no location, default to the bot's current "Zone: [Name]".

ACRONYMS: "FP" = Flight Path. "BiS" = Best in Slot. "SM" = Scarlet Monastery. "SW" = Stormwind. "IF" = Ironforge. If you don't know what a particular acronym means, do not hallucinate its meaning, just admit you don't know what that is.

OUTPUT FORMAT:
Reply with ONLY one JSON object. No markdown, no code fences.
{"category": "game", "query": "your search query", "searchEntity": "the specific proper noun to index (e.g. 'Ironforge')", "subject": "the character who spoke"}
{"category": "character", "query": null, "searchEntity": "CharacterName", "subject": "the character who spoke"}
{"category": "server", "query": null, "searchEntity": "GuildOrServerConcept", "subject": "the character who spoke"}
{"category": "none", "query": null, "searchEntity": null, "subject": null}"""

FRENCHMAID_SYSTEM_PROMPT = """You are "FrenchMaid", a data-cleaning assistant for a World of Warcraft WotLK 3.3.5 server.
Extract ONLY specific facts that help answer the SEARCH QUERY. Ignore junk.
Translate foreign languages to English. Do NOT invent information.
Keep it concise: a few short plain-text bullet points.
If NO useful info, reply exactly: NO_USEFUL_DATA"""

LORE_ARCHIVIST_SYSTEM_PROMPT = """You are a database archivist for an MMO knowledge base.
Your task is to compress raw search results into a highly dense, readable summary.

CONSTRAINTS
MAX_SENTENCES: Exactly 1 sentence.
SENTENCE_LENGTH: Maximum 10 words. Use periods to separate distinct thoughts. Do NOT write long run-on sentences.
FORMAT: Clear, short sentence. NO comma-separated keyword lists. NO bullet points. NO markdown.
INCLUSION: Only include the single most relevant atomic fact. Do not combine separate facts with "and" unless they are inseparable.
EXCLUSION: Strictly ignore minor details, alternative routes, flavor text, lore trivia, and verbose descriptions, secondary facts.
ANTI-CRAP FILTER: Do NOT include specific coordinates, obscure NPC names, or highly dubious details unless they are universally known core game facts. If the raw text just says "the barber", output "the barber", do not invent or copy unverified names like "Northshear Abbey".
ENTITY MISMATCH GUARDRAIL: If the raw facts do NOT explicitly describe or mention the "Entity being archived", output exactly NO_USEFUL_DATA. Do not try to force a connection between the entity and unrelated locations in the text.

EXAMPLES
Input: "The Deadmines are located in Westfall. The entrance is in Moonbrook. It is a level 15-20 dungeon. Mr. Smite is a boss."
Output: The Deadmines is a level 15-20 dungeon, located in Moonbrook, Westfall.

Input: "Neakris says she got a haircut in Stormwind yesterday and painted her hair black."
Output: Neakris got a haircut yesterday in Stormwind.

Execute compression on the provided raw facts. Output ONLY 1 sentence. No prefix, no quotes."""

FACT_EXTRACTION_PROMPT = """You are a highly strict fact extraction engine for an MMO chat system.
Evaluate the `exchange` and extract ONLY the SINGLE most important, impactful fact.

SEMANTIC DEFINITIONS
DEFINITIONS = {
    "game": "Objective WoW 3.3.5 mechanics, items, NPCs, or general locations.",
    "character": "Concrete specs, preferences, achievements, relationships, recent personal events, opinions, claims, or significant factual statements about a specific player or bot.",
    "server": "Guild-specific information, community events, server meta, or rules explicitly stated as applying only to this server. Do NOT use for objective game mechanics.",
    "none": "Banter, greetings, transient plans, jokes, redundant agreements, roleplay flavor, combat descriptions, and dramatic expressions."
}

ENTITY FORMAT (MANDATORY)
ENTITY must be a clean name — a proper noun OR a short proper noun phrase (max 3 words).
GOOD: "Islyn", "Camp Mojache", "Stranglethorn Vale", "Communist Bunnies"
BAD: "Camp Mojache (implied by steps)", "the dungeon", "Westfall zone", "Stormwind city", "Town of Ironforge".

ANTI-ROLEPLAY FILTER (MANDATORY)
The following are NEVER facts and must ALWAYS be discarded as "none":
Dramatic or metaphorical statements: "the shadows demand balance", "I take only what's mine"
Combat descriptions: "I stabbed them", "they got my daggers", "I killed the boss"
Personality flavor text: "dark", "brooding", "shadows whisper", "the sea's way"
Throwaway complaints: "this zone sucks", "it's just a graveyard"

SIGNIFICANCE TEST
Before outputting a fact, ask: "Would this fact change how a bot interacts with this entity in a future conversation?"
If NO, return NO_FACT. Only store facts that are genuinely useful for future interactions.

def extract_single_fact(exchange):
    # 1. Identify all potential facts.
    # 2. Apply ANTI-ROLEPLAY FILTER. Discard flavor text, combat banter, and dramatic expressions.
    # 3. Apply SIGNIFICANCE TEST. Discard trivial complaints and throwaway opinions.
    # 4. Select ONLY the ONE most important fact. If none pass all filters, return NO_FACT.
    # Note: If category is "game", the ENTITY must be the in-game concept, NOT the character who spoke.
    # Note: SPEAKER is the character who actually MADE the claim (the bot or the player).
    return format_output(category, entity, fact, speaker)

EXAMPLES

Exchange:
Sakred: LFG Deadmines
Neacris: inv me
Output: NO_FACT

Exchange:
Neacris: I finally hit 60 and got a haircut in SW!
Sakred: grats
Output:
CATEGORY: character
ENTITY: Neacris
FACT: Reached level 60 and got a haircut in Stormwind.

Exchange:
Islyn: My guild never wipes, we are the best.
Neacris: lol sure
Output:
CATEGORY: character
ENTITY: Islyn
FACT: Boasts that their guild never wipes.

Exchange:
Islyn: The shadows demand balance. I take only what's mine.
Mekkafizz: Aye Islyn, balance be the sea's way too.
Output: NO_FACT

Exchange:
Islyn: They all got my daggers in their faces!
Mekkafizz: Nice work, save some for us!
Output: NO_FACT

Exchange:
Sakred: Westfall is full of Defias, great for grinding.
Neacris: Yeah I know
Output:
CATEGORY: game
ENTITY: Westfall
FACT: Populated by Defias Brotherhood, suitable for grinding reputation and gold.

Execute `extract_single_fact(exchange)` and output ONLY the formatted result.
YOU MUST USE THE EXACT LITERAL LABELS "CATEGORY:", "ENTITY:", "FACT:", AND "SPEAKER:".

Exchange:
{exchange}"""

BOTRAM_LOOP_PROMPT = """You are a loop detector for an MMO chat room. Analyze the recent messages.
Your job is to catch conversations that are stuck and going nowhere.

Flag as LOOP if:
A bot repeats the exact same sentence word-for-word.
The same two concepts are recycled endlessly without new ideas or progression.
Speakers are stuck in a pun battle or wordplay loop, trading similar rhymes without progressing.
Each message is just a slight rewording of the previous one.

Reply CONTINUE if:
Speakers exchange short acknowledgments like "k", "lol", "nice", "fr", "true", or say goodbye.
The conversation is progressing, introducing new ideas, changing direction, or escalating.
Players are roasting each other or arguing, but the conversation still moves forward with topics, and new facts are introduced.

Reply ONLY with "LOOP" or "CONTINUE".

Messages:
{messages}"""

BOTRAM_MEMORY_RECALL_PROMPT = """You are a memory filter and conversation classifier for an MMO chat bot.
Below are recent conversations the bot and the player were involved in (each labeled with a Conversation ID), and a new incoming message from the player.

TASK 1: Determine which conversation the new message belongs to.
Carefully read the new message and look for matching keywords, names, locations, or topics in the listed conversations.
If it continues, references, or returns to one of the listed conversations, reply with that conversation's ID number.
If it's a completely new topic unrelated to ANY listed conversation, reply with NEW.

TASK 2: Extract ONLY the facts/context from the conversations that are directly relevant to responding to the new message.
If the message references a past topic, person, or event, include that context briefly.
If nothing is relevant, reply with NO_RELEVANT_CONTEXT for the context part.

CRITICAL ANTI-HALLUCINATION RULE:
Your CONTEXT summary MUST be strictly derived from explicit events in the provided transcript. 
Do NOT invent disputes, questions, or backstory that is not explicitly written in the conversation history. 
If the past conversations do not contain relevant context for the new message, you MUST reply with NO_RELEVANT_CONTEXT.

OUTPUT FORMAT:
Reply in exactly this format:
CONVO: [conversation ID number or NEW]
CONTEXT: [Relevant context summary or NO_RELEVANT_CONTEXT]

Recent conversations:
{conversations}

New message:
{new_message}"""

# --- Engine Config Dictionaries ---
LIBRARIAN_CONFIG = {"apiUrl": LIBRARIAN_API_URL, "apiKey": LIBRARIAN_API_KEY, "model": LIBRARIAN_MODEL}
FRENCHMAID_CONFIG = {"apiUrl": FRENCHMAID_API_URL, "apiKey": FRENCHMAID_API_KEY, "model": FRENCHMAID_MODEL}
ROLEPLAYER_CONFIG = {"apiUrl": ROLEPLAYER_API_URL, "apiKey": ROLEPLAYER_API_KEY, "model": ROLEPLAYER_MODEL}