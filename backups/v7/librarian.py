import json
import re
import sqlite3
import threading
from config import (
    LIBRARIAN_CONFIG, LIBRARIAN_SYSTEM_PROMPT, LIBRARIAN_TEMPERATURE,
    FRENCHMAID_CONFIG, FRENCHMAID_SYSTEM_PROMPT, FRENCHMAID_TEMPERATURE,
    FRENCHMAID_ENABLED, FRENCHMAID_NO_DATA_MARKER, FRENCHMAID_MAX_OUTPUT_LENGTH,
    FRENCHMAID_MAX_SNIPPET_LENGTH, SEARCH_RESULT_COUNT,
    SKEPTIC_MODE, SKEPTIC_OVERRIDE_PROMPT,
    LORE_DB_PATH, LORE_CONFIDENCE_THRESHOLD,
    LORE_ARCHIVIST_SYSTEM_PROMPT, FACT_EXTRACTION_PROMPT
)
from logging_utils import log, logPayload
from engine import callLlm, cacheGet, cacheSet, getQueryLock
from french_maid import cleanEntityName

try:
    from ddgs import DDGS
except ImportError:
    DDGS = None

# ============================================================================
#  LORE DB INITIALIZATION
# ============================================================================

loreConn = sqlite3.connect(LORE_DB_PATH, check_same_thread=False)
loreLock = threading.Lock()

def initLoreDb():
    with loreLock:
        loreConn.execute("""CREATE TABLE IF NOT EXISTS lore (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            category TEXT NOT NULL,
            entity TEXT NOT NULL,
            fact TEXT NOT NULL,
            source TEXT DEFAULT 'chat',
            confidence REAL DEFAULT 0.6,
            updatedAt DATETIME DEFAULT CURRENT_TIMESTAMP
        )""")

        loreConn.execute("""CREATE VIRTUAL TABLE IF NOT EXISTS loreFts
            USING fts5(entity, fact, content='lore', content_rowid='id')""")

        loreConn.execute("""CREATE TRIGGER IF NOT EXISTS lore_ai AFTER INSERT ON lore BEGIN
            INSERT INTO loreFts(rowid, entity, fact) VALUES (new.id, new.entity, new.fact);
        END""")
        loreConn.execute("""CREATE TRIGGER IF NOT EXISTS lore_ad AFTER DELETE ON lore BEGIN
            INSERT INTO loreFts(loreFts, rowid, entity, fact) VALUES('delete', old.id, old.entity, old.fact);
        END""")
        loreConn.execute("""CREATE TRIGGER IF NOT EXISTS lore_au AFTER UPDATE ON lore BEGIN
            INSERT INTO loreFts(loreFts, rowid, entity, fact) VALUES('delete', old.id, old.entity, old.fact);
            INSERT INTO loreFts(rowid, entity, fact) VALUES (new.id, new.entity, new.fact);
        END""")
        loreConn.commit()
        log("LIBRARIAN", "Initialized lore.db.")

initLoreDb()

# ============================================================================
#  LORE DB HELPERS
# ============================================================================

def extractSearchTerms(query):
    q = re.sub(r'wowhead|wotlk|guide|farming', '', query, flags=re.IGNORECASE)
    words = [w for w in q.split() if len(w) > 2]
    # Wrap each word in double quotes to escape special characters (like apostrophes) for FTS5
    quotedWords = [f'"{w}"' for w in words]
    return " OR ".join(quotedWords)

def searchLore(query):
    terms = extractSearchTerms(query)
    if not terms:
        return None

    with loreLock:
        try:
            rows = loreConn.execute("""
                SELECT l.fact, l.confidence
                FROM loreFts f
                JOIN lore l ON l.id = f.rowid
                WHERE loreFts MATCH ?
                ORDER BY rank
                LIMIT 3
            """, (terms,)).fetchall()

            if rows:
                facts = []
                for fact, conf in rows:
                    if conf >= LORE_CONFIDENCE_THRESHOLD:
                        facts.append(fact)
                if facts:
                    return "\n".join(facts)
        except Exception as e:
            log("LIBRARIAN", f"Lore search error: {e}")
    return None

def insertLore(category, entity, fact, source, confidence):
    with loreLock:
        try:
            # Check if ANY entry exists for this entity
            existing = loreConn.execute("SELECT id, fact FROM lore WHERE entity = ?", (entity,)).fetchone()
            if existing:
                existingId = existing[0]
                existingFact = existing[1]
                
                # If the new fact is already contained in the existing one, just bump the timestamp
                if fact in existingFact:
                    loreConn.execute("UPDATE lore SET updatedAt = CURRENT_TIMESTAMP WHERE id = ?", (existingId,))
                else:
                    # Merge the new fact into the existing one
                    mergedFact = f"{existingFact}, {fact}"
                    loreConn.execute("UPDATE lore SET fact = ?, updatedAt = CURRENT_TIMESTAMP WHERE id = ?", (mergedFact, existingId))
                    log("LIBRARIAN", f"Merged new lore into existing entity: {entity}")
            else:
                loreConn.execute("""INSERT INTO lore (category, entity, fact, source, confidence) 
                                    VALUES (?, ?, ?, ?, ?)""", (category, entity, fact, source, confidence))
                log("LIBRARIAN", f"Stored new {category} lore: {entity}")
            loreConn.commit()
        except Exception as e:
            log("LIBRARIAN", f"Lore insert error: {e}")

def archiveForLore(rawFacts):
    """Pass raw facts through the Archivist LLM to condense them for DB storage."""
    logPayload("ARCHIVIST_RAW", rawFacts)

    archived = callLlm(
        LIBRARIAN_CONFIG,
        LORE_ARCHIVIST_SYSTEM_PROMPT,
        rawFacts,
        {"temperature": 0.1, "num_predict": 150}
    ).strip()

    if not archived or "NO_USEFUL_DATA" in archived:
        archived = rawFacts  # Fallback to raw if archivist fails

    logPayload("ARCHIVIST_CLEAN", archived)
    return archived
    
def extractFactsFromChat(payload):
    """Post-pipeline step: Evaluates the chat exchange and stores new social/server facts."""
    try:
        playerMsg = payload.get("playerMsg")
        botResponse = payload.get("response")
        botName = payload.get("botName")
        targetName = payload.get("targetName")

        # Skip if it's ambient chatter, an event, or an empty response
        if not playerMsg or not botResponse or targetName == "-ambient-":
            return

        exchangeText = f"{botName} and {targetName} are talking.\n{targetName}: {playerMsg}\n{botName}: {botResponse}"
        userPrompt = FACT_EXTRACTION_PROMPT.replace("{exchange}", exchangeText)
        
        log("LIBRARIAN", "Running post-pipeline fact extraction...")
        
        # FIX: Pass the formatted prompt as the userPrompt (2nd arg), and leave systemPrompt (1st arg) empty
        raw = callLlm(LIBRARIAN_CONFIG, "", userPrompt, {"temperature": 0.1, "num_predict": 100}).strip()
        logPayload("FACT_EXTRACTOR_RAW", raw)
        
        if "NO_FACT" in raw.upper():
            log("LIBRARIAN", "Fact extractor found no new social/server facts.")
            return
            
        category = "player"
        entity = ""
        fact = ""
        
        for line in raw.split('\n'):
            if line.upper().startswith("CATEGORY:"):
                category = line.split(':', 1)[1].strip().lower()
            elif line.upper().startswith("ENTITY:"):
                entity = line.split(':', 1)[1].strip()
            elif line.upper().startswith("FACT:"):
                fact = line.split(':', 1)[1].strip()
                
        if entity and fact:
            dbEntity = cleanEntityName(entity)
            # Insert with 'chat' source and 0.6 base confidence
            insertLore(category, dbEntity, fact, "chat", 0.6)
            log("LIBRARIAN", f"Extracted {category} fact from chat: {dbEntity} -> {fact}")
        else:
            log("LIBRARIAN", "Fact extractor returned data, but couldn't parse Entity/Fact.")
            
    except Exception as e:
        # Fail-soft: If extraction breaks, we just log it and don't crash the proxy
        log("LIBRARIAN", f"Fact extraction failed (non-fatal): {e}")

# ============================================================================
#  LIBRARIAN LOGIC
# ============================================================================

def parseLibrarianJson(raw):
    default = {"search": False, "query": None, "subject": None}
    if not raw:
        return default
    try:
        data = json.loads(raw.strip())
        return {
            "search": bool(data.get("search", False)),
            "query": data.get("query"),
            "subject": data.get("subject")
        }
    except Exception:
        pass
    match = re.search(r"\{.*\}", raw, re.DOTALL)
    if match:
        try:
            data = json.loads(match.group(0))
            return {
                "search": bool(data.get("search", False)),
                "query": data.get("query"),
                "subject": data.get("subject")
            }
        except Exception:
            pass
    return default

def runLibrarianAndSearch(payload):
    # Ambient chatter is random flavor text; skip web searches to save DDGS calls
    if payload.get("targetName") == "-ambient-":
        payload["webContext"] = ""
        return

    fullPrompt = payload["cleanPrompt"]
    options = {"temperature": LIBRARIAN_TEMPERATURE, "num_predict": 150}
    raw = callLlm(LIBRARIAN_CONFIG, LIBRARIAN_SYSTEM_PROMPT, fullPrompt, options)
    logPayload("LIBRARIAN_RAW", raw)
    decision = parseLibrarianJson(raw)
    log("LIBRARIAN", f"DECISION: search={decision['search']} | query={decision['query']!r} | subject={decision['subject']!r}")

    # Build a clean entity name for DB storage
    rawEntity = decision["subject"] if decision.get("subject") else decision["query"]
    dbEntity = cleanEntityName(rawEntity)

    webContext = ""

    if decision["search"] and decision["query"]:
        query = decision["query"]

        # 1. CHECK LORE DB FIRST (use clean subject if available for better matching)
        loreQuery = dbEntity if dbEntity else query
        loreMatch = searchLore(loreQuery)
        if loreMatch:
            log("LIBRARIAN", "LORE HIT: Found confident match in lore.db. Skipping DDGS.")
            payload["loreContext"] = f"\n\n[LORE CONTEXT]:\n{loreMatch}\nUse this factual information to answer accurately."
            return

        # 2. CHECK CACHE
        cached = cacheGet(query)
        if cached is not None:
            webContext = cached
            log("CACHE", f"HIT for query: {query!r}")
        else:
            lock = getQueryLock(query)
            with lock:
                cached = cacheGet(query)
                if cached is not None:
                    webContext = cached
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
                                maidInput = f"SEARCH QUERY: {query}\n\nSEARCH RESULTS:\n"
                                for i, r in enumerate(results, 1):
                                    maidInput += f"[Result {i}] Title: {r.get('title', '')}\n{r.get('body', '')}\n\n"

                                maidRaw = callLlm(FRENCHMAID_CONFIG, FRENCHMAID_SYSTEM_PROMPT, maidInput, {"temperature": FRENCHMAID_TEMPERATURE, "num_predict": 500})
                                logPayload("FRENCHMAID_RAW", maidRaw)

                                if FRENCHMAID_NO_DATA_MARKER not in maidRaw:
                                    cleaned = re.sub(r'\n+', '\n', maidRaw.strip())
                                    if len(cleaned) > FRENCHMAID_MAX_OUTPUT_LENGTH:
                                        cleaned = cleaned[:FRENCHMAID_MAX_OUTPUT_LENGTH]
                                    webContext = f"\n\n[WEB SEARCH CONTEXT]:\n{cleaned}\nUse this factual information to answer accurately."
                                    logPayload("FRENCHMAID_EXTRACTED", cleaned)

                                    # ARCHIVE FOR LORE DB (with before/after logging)
                                    archivedFact = archiveForLore(cleaned)
                                    insertLore("game", dbEntity, archivedFact, "web", 0.85)
                                else:
                                    log("FRENCHMAID", "Model reported NO_USEFUL_DATA.")
                                    if SKEPTIC_MODE:
                                        webContext = f"\n\n[SYSTEM OVERRIDE - SKEPTIC MODE]:\n{SKEPTIC_OVERRIDE_PROMPT}"
                                        log("SKEPTIC", "No data found. Injecting Skeptic Mode override.")
                            else:
                                kept = [f"- {r.get('title', '')}: {r.get('body', '')[:FRENCHMAID_MAX_SNIPPET_LENGTH]}" for r in results if r.get('body')]
                                if kept:
                                    joined = "\n".join(kept)
                                    webContext = "\n\n[WEB SEARCH CONTEXT]:\n" + joined

                                    # ARCHIVE FOR LORE DB (with before/after logging)
                                    archivedFact = archiveForLore(joined)
                                    insertLore("game", dbEntity, archivedFact, "web", 0.85)

                        except Exception as e:
                            log("DDGS", f"DDGS error: {e}")

                    cacheSet(query, webContext)
                    log("CACHE", f"SET for query: {query!r}")

    payload["webContext"] = webContext