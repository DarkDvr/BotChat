# BotChat System

BotChat is a Python middleware proxy for World of Warcraft WotLK 3.3.5 chat bots.

*Hard requirement: AzerothCore, mod-ollama-chat, mod-playerbots.*

It receives chat events from the game module, enriches them with memory, gossip, lore, transcripts, and web search, then asks a local LLM to respond like a real player.

By itself, playerbots are quite bad conversationalists. BotChat makes bots feel alive by adding:

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
