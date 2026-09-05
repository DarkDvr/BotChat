# BotChat System

BotChat is a Python middleware proxy for World of Warcraft WotLK 3.3.5 chat bots.

It receives chat events from the game module, enriches them with memory, gossip, lore, transcripts, and web search, then asks a local LLM to respond like a real player.

By itself, an LLM is stateless. BotChat makes bots feel alive by adding:

- Conversation threading
- SQLite-backed memory
- Server gossip recall
- Transcript search
- Local lore database
- Web-backed game knowledge
- Bot self-awareness
- Loop prevention
- Addon garbage filtering
- Natural roleplay formatting

---

## How It Works

1. The game module sends a chat prompt to BotChat.
2. BotRAM filters garbage, extracts bot/player/message info, and joins or starts a conversation.
3. The message is stored for future memory/gossip/transcript search.
4. The classifier decides whether it is:
   - a game question,
   - a social question,
   - or normal chat.
5. Relevant context is fetched:
   - local lore DB,
   - web search,
   - past transcripts,
   - server gossip.
6. The roleplayer model writes a short, human-style reply.
7. The reply is sent back to the game module.

---

## One Local Ollama Model

BotChat can use hosted APIs, but the intended setup is one local Ollama model.

The different modules are logical roles, not separate required models:

- `BOTRAM_MODEL` — memory recall, threading, loop detection
- `CLASSIFIER_MODEL` — intent classification, fact extraction
- `FRENCHMAID_MODEL` — web result cleaning
- `ROLEPLAYER_MODEL` — final roleplay response

To run everything locally, leave API URL/key fields blank and point all model settings to the same Ollama model.

---

## `config.py`

### Core / Logging

- `VERSION` — current BotChat version.
- `ENABLE_LOG_COLORS` — console colors. Usually `False` for clean file logs.
- `DEBUG_FULL_LOGS` — logs full payloads and intermediate output.
- `MAX_LOG_CHARS` — console payload truncation limit.
- `LOG_DIR` — log folder.
- `LOG_DATE_FORMAT` — timestamp format.

### Proxy / LLM

- `PROXY_HOST` / `PROXY_PORT` — proxy listen address.
- `OLLAMA_URL` — local Ollama endpoint.
- `OLLAMA_TIMEOUT` — local model timeout.
- `ONLINE_API_TIMEOUT` — external API timeout.
- `ONLINE_API_REQUESTS_PER_SECOND` — external API rate limit.
- `CACHE_TTL` — short-lived cache for web/context results.

### BotRAM Memory

- `BOTRAM_ENABLED` — master memory switch.
- `BOTRAM_DB_PATH` — SQLite memory database.
- `BOTRAM_MODEL` — model for memory/threading/loop checks.
- `BOTRAM_PAST_CONVOS_LIMIT` — recent conversations shown to memory parser.
- `BOTRAM_PHRASES_PER_CONVO` — messages per conversation in memory context.
- `BOTRAM_AMBIENT_CONVOS_LIMIT` — ambient conversations included.
- `BOTRAM_AMBIENT_JOIN_MINUTES` — ambient messages can join recent convos within this window.
- `BOTRAM_RETENTION_DAYS` — memory older than this is deleted.
- `BOTRAM_ACTIVE_WINDOW_MINUTES` — ambient memory freshness window.
- `BOTRAM_MEMORY_WINDOW_MINUTES` — targeted memory freshness window.
- `BOTRAM_LOOP_CHECK_THRESHOLD` — message count before loop checks start.
- `BOTRAM_LOOP_CHECK_INTERVAL` — how often loop checks run after threshold.
- `BOTRAM_LOOP_COOLDOWN_MINUTES` — locked conversations stay silent for this long.
- `BOTRAM_TRANSCRIPT_WINDOW` — surrounding phrases retrieved around transcript matches.

### Gossip

- `BOTRAM_GOSSIP_ENABLED` — enables server gossip lookup.
- `BOTRAM_GOSSIP_WINDOW_HOURS` — how far back to search.
- `BOTRAM_GOSSIP_MAX_CONVOS` — max conversations passed to gossip summarizer.
- `BOTRAM_GOSSIP_MAX_PHRASES` — phrase cap.
- `BOTRAM_GOSSIP_MIN_SCORE` — minimum keyword matches required.
- `BOTRAM_GOSSIP_MAX_KEYWORDS` — max keywords used.

### Lore DB

- `LORE_DB_PATH` — local lore/wiki database.
- `TRUSTED_FACT_MARKER` — phrase that lets trusted players force-store facts.
- `TRUSTED_PLAYERS` — players allowed to use the trusted fact marker.

### Web Search

- `WEB_SEARCH_QUERY_PREFIX` — prefix added to searches.
- `SEARCH_RESULT_COUNT` — number of search results fetched.
- `WEB_SEARCH_DELAY` — delay before searches to reduce blocking.
- `WEB_SEARCH_EXCLUDED_TERMS` — reserved for future filtering.

### Classifier

- `CLASSIFIER_API_URL` / `CLASSIFIER_API_KEY` — optional external API. Blank = local Ollama.
- `CLASSIFIER_MODEL` — intent/fact extraction model.
- `CLASSIFIER_TEMPERATURE` — lower is more stable.
- `CLASSIFIER_THINK_ENABLED` — thinking mode for supported models.

### FrenchMaid

- `FRENCHMAID_ENABLED` — if off, raw snippets are used instead of cleaned summaries.
- `FRENCHMAID_API_URL` / `FRENCHMAID_API_KEY` — optional external API.
- `FRENCHMAID_MODEL` — web cleaning model.
- `FRENCHMAID_TEMPERATURE` — low recommended.
- `FRENCHMAID_NO_DATA_MARKER` — marker returned when no useful data is found.
- `FRENCHMAID_MAX_OUTPUT_LENGTH` — max cleaned web context length.
- `FRENCHMAID_MAX_SNIPPET_LENGTH` — snippet length when FrenchMaid is disabled.
- `FACT_EXTRACTION_THINK_ENABLED` — thinking mode for trusted fact extraction.
- `LORE_CORRECTION_THINK_ENABLED` — thinking mode for lore conflict checks.

### Roleplayer

- `ROLEPLAYER_API_URL` / `ROLEPLAYER_API_KEY` — optional external API.
- `ROLEPLAYER_MODEL` — final chat response model.

### Safety / Limits

- `BREAK_ON_STRINGS` — messages containing these strings are discarded immediately.
- `MAX_RESPONSE_LENGTH` — max outgoing chat length.

### Skeptic Mode

- `SKEPTIC_MODE` — reduces hallucination when web search finds no evidence.
- `SKEPTIC_OVERRIDE_PROMPT` — prompt used when a topic appears fake/unverifiable.

---

## System Prompts

`config.py` also contains the prompts used by each role:

- `CLASSIFIER_SYSTEM_PROMPT` — intent classification.
- `GOSSIP_SUMMARIZER_PROMPT` — summarizes relevant server gossip.
- `TRANSCRIPT_SUMMARIZER_PROMPT` — answers from past chat snippets.
- `FACT_EXTRACTION_PROMPT` — extracts trusted-player facts.
- `LORE_CORRECTION_PROMPT` — checks if a trusted fact contradicts existing lore.
- `LORE_ARCHIVIST_SYSTEM_PROMPT` — stores web facts into lore DB.
- `FRENCHMAID_SYSTEM_PROMPT` — cleans web search results.
- `BOTRAM_LOOP_PROMPT` — detects conversation loops.
- `BOTRAM_MEMORY_RECALL_PROMPT` — threads conversations and recalls context.
- `ROLEPLAYER_SYSTEM_PROMPT` — final roleplay style.

---

## Files

- `main.py` — HTTP proxy entry point. Receives requests and runs the pipeline.
- `config.py` — settings, prompts, model config, safety filters.
- `logging_utils.py` — console + daily rotating file logs.
- `engine.py` — LLM calls, Ollama/API support, caching, rate limiting.
- `french_maid.py` — text cleaning, addon filtering, slang expansion, entity cleanup.
- `classifier.py` — intent detection, lore DB, web search, gossip, trusted facts.
- `botram.py` — memory engine: conversations, phrases, gossip, transcripts, loops, self-awareness.
- `roleplayer.py` — generates the final chat reply.

---

## Data Files

- `botram.db` — conversations and chat history.
- `lore.db` — verified lore facts and slang expansions.
- `logs/` — daily log files.

---

## Basic Setup

1. Install Python 3.
2. Install Ollama.
3. Pull your chosen local model.
4. Edit `config.py`.
5. Run:

```bash
python main.py
