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
    LORE_ARCHIVIST_SYSTEM_PROMPT, FACT_EXTRACTION_PROMPT,
    TRUSTED_PLAYERS # <-- ADDED MISSING IMPORT
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
    quotedWords = [f'"{w}"' for w in words]
    return " OR ".join(quotedWords)

def searchLore(entityName):
    if not entityName: return None

    with loreLock:
        try:
            rows = loreConn.execute(
                "SELECT fact, confidence FROM lore WHERE entity = ?", 
                (entityName,)
            ).fetchall()
            
            if not rows:
                rows = loreConn.execute(
                    "SELECT fact, confidence FROM lore WHERE entity LIKE ?", 
                    (f"%{entityName}%",)
                ).fetchall()

            if rows:
                facts = [fact for fact, conf in rows if conf >= LORE_CONFIDENCE_THRESHOLD]
                if facts: return "\n".join(facts)
        except Exception as e:
            log("LIBRARIAN", f"Lore search error: {e}")
    return None

def insertLore(category, entity, fact, source, confidence):
    with loreLock:
        try:
            # Fetch ALL existing rows for this entity (defensive vs. legacy dupes / races)
            existingRows = loreConn.execute(
                "SELECT id, fact, confidence FROM lore WHERE entity = ?", (entity,)
            ).fetchall()

            if existingRows:
                existingId = existingRows[0][0]                       # primary row we write to
                existingFact = " ".join(r[1] for r in existingRows)   # concatenate all facts
                existingConf = max(r[2] for r in existingRows)        # highest confidence wins

                # Self-heal: collapse any duplicate rows into the primary row
                if len(existingRows) > 1:
                    dupIds = [r[0] for r in existingRows[1:]]
                    loreConn.execute(
                        f"DELETE FROM lore WHERE id IN ({','.join('?' * len(dupIds))})",
                        dupIds
                    )
                    loreConn.execute(
                        "UPDATE lore SET fact = ?, confidence = ? WHERE id = ?",
                        (existingFact, existingConf, existingId)
                    )
                    log("LIBRARIAN", f"Consolidated {len(existingRows)} lore rows for '{entity}'.")

                # SEMANTIC DEDUPLICATION (unchanged)
                dedupPrompt = f"""You are a strict database deduplication engine.
Compare the NEW FACT to the EXISTING FACTS for this entity.
EXISTING FACTS (Confidence: {existingConf}): {existingFact}
NEW FACT (Confidence: {confidence}): {fact}
RULES:
If the NEW FACT describes the same event, attribute, or opinion as the EXISTING FACTS, reply DUPLICATE.
If the NEW FACT directly contradicts the EXISTING FACTS, AND the NEW FACT has a HIGHER confidence score, reply OVERWRITE.
If the NEW FACT directly contradicts the EXISTING FACTS, BUT the EXISTING FACTS have a HIGHER or EQUAL confidence score, reply DUPLICATE (discard the lower quality new fact).
ONLY reply NEW if the NEW FACT introduces a completely different, unrelated event or attribute.
WHEN IN DOUBT, REPLY DUPLICATE.
Reply with ONLY ONE WORD: DUPLICATE, OVERWRITE, or NEW."""
                verdict = callLlm(LIBRARIAN_CONFIG, "", dedupPrompt, {"temperature": 0.1, "num_predict": 10}).strip().upper()
                log("LIBRARIAN", f"Semantic Dedup for '{entity}': {verdict}")

                if verdict.startswith("OVERWRITE"):
                    loreConn.execute(
                        "UPDATE lore SET fact = ?, confidence = ?, updatedAt = CURRENT_TIMESTAMP WHERE id = ?",
                        (fact, confidence, existingId))
                    log("LIBRARIAN", f"Overwrote contradicted lore for: {entity} (Higher confidence)")
                elif verdict.startswith("NEW"):
                    mergedFact = f"{existingFact} {fact}"
                    newConf = max(existingConf, confidence)
                    loreConn.execute(
                        "UPDATE lore SET fact = ?, confidence = ?, updatedAt = CURRENT_TIMESTAMP WHERE id = ?",
                        (mergedFact, newConf, existingId))
                    log("LIBRARIAN", f"Appended new lore to existing entity: {entity}")
                else:  # DUPLICATE or fallback
                    loreConn.execute(
                        "UPDATE lore SET updatedAt = CURRENT_TIMESTAMP WHERE id = ?", (existingId,))
            else:
                loreConn.execute(
                    """INSERT INTO lore (category, entity, fact, source, confidence)
                       VALUES (?, ?, ?, ?, ?)""",
                    (category, entity, fact, source, confidence))
                log("LIBRARIAN", f"Stored new {category} lore: {entity}")
            loreConn.commit()
        except Exception as e:
            log("LIBRARIAN", f"Lore insert error: {e}")

def archiveForLore(entityName, rawFacts):
    logPayload("ARCHIVIST_RAW", rawFacts)
    userPrompt = f"Entity being archived: {entityName}\n\nRaw facts:\n{rawFacts}"
    
    archived = callLlm(LIBRARIAN_CONFIG, LORE_ARCHIVIST_SYSTEM_PROMPT, userPrompt, {"temperature": 0.1, "num_predict": 150}).strip()
    
    # Catch empty, NO_USEFUL_DATA, or "negative" facts
    negative_markers = ["no_useful_data", "do not contain information", "no information", "does not contain", "not contain", "provided facts describe"]
    if not archived or any(marker in archived.lower() for marker in negative_markers):
        log("LIBRARIAN", "Archivist returned negative/empty fact. Skipping insert.")
        return None
        
    logPayload("ARCHIVIST_CLEAN", archived)
    return archived

# ============================================================================
#  LIBRARIAN LOGIC
# ============================================================================

def parseLibrarianJson(raw):
    default = {"category": "none", "query": None, "searchEntity": None, "subject": None}
    if not raw: return default
    try:
        data = json.loads(raw.strip())
        return {
            "category": data.get("category", "none").lower(),
            "query": data.get("query"),
            "searchEntity": data.get("searchEntity"),
            "subject": data.get("subject")
        }
    except Exception: pass
    match = re.search(r"\{.*\}", raw, re.DOTALL)
    if match:
        try:
            data = json.loads(match.group(0))
            return {
                "category": data.get("category", "none").lower(),
                "query": data.get("query"),
                "searchEntity": data.get("searchEntity"),
                "subject": data.get("subject")
            }
        except Exception: pass
    return default

def runLibrarianAndSearch(payload):
    if payload.get("targetName") == "-ambient-":
        payload["webContext"] = ""
        return

    fullPrompt = payload["cleanPrompt"]
    options = {"temperature": LIBRARIAN_TEMPERATURE, "num_predict": 150}
    raw = callLlm(LIBRARIAN_CONFIG, LIBRARIAN_SYSTEM_PROMPT, fullPrompt, options)
    logPayload("LIBRARIAN_RAW", raw)
    decision = parseLibrarianJson(raw)
    log("LIBRARIAN", f"DECISION: category={decision['category']} | query={decision['query']!r} | searchEntity={decision.get('searchEntity')!r}")

    category = decision["category"]
    searchEntity = decision.get("searchEntity")
    subject = decision.get("subject")
    query = decision.get("query")

    rawEntity = searchEntity if searchEntity else (subject if subject else query)
    
    if rawEntity:
        dbEntity = cleanEntityName(rawEntity)
        loreMatch = searchLore(dbEntity)
        if loreMatch:
            log("LIBRARIAN", f"LORE HIT: Found confident match in lore.db for '{dbEntity}'.")
            payload["loreContext"] = f"\n\n[LORE CONTEXT]:\n{loreMatch}\nUse this factual information to answer accurately."
            return

    if category == "game" and query:
        cached = cacheGet(query)
        webContext = ""
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

                                    archivedFact = archiveForLore(dbEntity, cleaned)
                                    insertEntity = cleanEntityName(rawEntity)

                                    if archivedFact and insertEntity:
                                        insertLore("game", insertEntity, archivedFact, "web", 0.8)
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
                                    
                                    archivedFact = archiveForLore(dbEntity, joined)
                                    insertEntity = cleanEntityName(rawEntity)

                                    if archivedFact and insertEntity:
                                        insertLore("game", insertEntity, archivedFact, "web", 0.8)

                        except Exception as e:
                            log("DDGS", f"DDGS error: {e}")

                    cacheSet(query, webContext)
                    log("CACHE", f"SET for query: {query!r}")
                    
        payload["webContext"] = webContext
    else:
        log("LIBRARIAN", f"Routing: {category} category. Skipping web search.")

def extractFactsFromChat(payload):
    try:
        playerMsg = payload.get("playerMsg")
        botResponse = payload.get("response")
        botName = payload.get("botName")
        targetName = payload.get("targetName")

        if not playerMsg or not botResponse or targetName == "-ambient-":
            return

        exchangeText = f"{botName} and {targetName} are talking.\n{targetName}: {playerMsg}\n{botName}: {botResponse}"
        userPrompt = FACT_EXTRACTION_PROMPT.replace("{exchange}", exchangeText)
        
        log("LIBRARIAN", "Running post-pipeline fact extraction...")
        raw = callLlm(LIBRARIAN_CONFIG, "", userPrompt, {"temperature": 0.1, "num_predict": 150}).strip()
        logPayload("FACT_EXTRACTOR_RAW", raw)
        
        if "NO_FACT" in raw.upper():
            log("LIBRARIAN", "Fact extractor found no new current facts.")
            return
            
        current_category = None
        current_entity = None
        current_fact = None
        facts_found = 0

        for line in raw.split('\n'):
            line = line.strip()
            if not line: continue
            
            if line.upper().startswith("CATEGORY:"):
                current_category = line.split(':', 1)[1].strip().lower()
            elif line.upper().startswith("ENTITY:"):
                current_entity = line.split(':', 1)[1].strip()
            elif line.upper().startswith("FACT:"):
                current_fact = line.split(':', 1)[1].strip()
                
                if current_category and current_entity and current_fact and current_category in ["character", "server", "game"]:
                    dbEntity = cleanEntityName(current_entity)

                    if not dbEntity:
                        log("LIBRARIAN", f"Skipped chat fact because cleaned entity was empty: {current_entity}")
                    else:
                        # TIERED CONFIDENCE LOGIC
                        if current_category == "character":
                            if targetName.lower() in TRUSTED_PLAYERS:
                                factConfidence = 0.85
                            else:
                                factConfidence = 0.5
                        elif current_category == "server":
                            factConfidence = 0.6
                        elif current_category == "game":
                            factConfidence = 0.4
                        else:
                            factConfidence = 0.5

                        insertLore(current_category, dbEntity, current_fact, "chat", factConfidence)
                        log("LIBRARIAN", f"Extracted {current_category} fact from chat: {dbEntity} -> {current_fact}")
                        facts_found += 1
                
                current_category = None
                current_entity = None
                current_fact = None
                
        if facts_found == 0:
            log("LIBRARIAN", "Fact extractor returned data, but couldn't parse valid Entity/Fact/Category.")
            
    except Exception as e:
        log("LIBRARIAN", f"Fact extraction failed (non-fatal): {e}")