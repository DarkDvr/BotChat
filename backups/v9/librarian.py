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
    LORE_DB_PATH, LORE_CONFIDENCE_THRESHOLD, LORE_FACT_TEXT_SEARCH,
    LORE_ARCHIVIST_SYSTEM_PROMPT, FACT_EXTRACTION_PROMPT,
    TRUSTED_PLAYERS,
    CONFIDENCE_GAME_WEB, CONFIDENCE_GAME_TRUSTED_PLAYER, CONFIDENCE_GAME_CHAT,
    CONFIDENCE_CHARACTER_TRUSTED_PLAYER, CONFIDENCE_CHARACTER_CHAT,
    CONFIDENCE_SERVER_TRUSTED_PLAYER, CONFIDENCE_SERVER_CHAT,
    LORE_GAME_BLOCK_WEB_THRESHOLD, LORE_FACT_SURGERY_MAX_CHARS,
    WEB_SEARCH_QUERY_PREFIX,
)

from logging_utils import log, logPayload
from engine import callLlm, cacheGet, cacheSet, getQueryLock
from french_maid import cleanEntityName

try:
    from ddgs import DDGS
except ImportError:
    DDGS = None


# ============================================================================
# LORE DB INITIALIZATION (v9: FTS5 removed)
# ============================================================================

loreConn = sqlite3.connect(LORE_DB_PATH, check_same_thread=False)
loreLock = threading.Lock()


def initLoreDb():
    with loreLock:
        # Drop legacy FTS5 objects if they exist
        loreConn.execute("DROP TRIGGER IF EXISTS lore_ai")
        loreConn.execute("DROP TRIGGER IF EXISTS lore_ad")
        loreConn.execute("DROP TRIGGER IF EXISTS lore_au")
        loreConn.execute("DROP TABLE IF EXISTS loreFts")

        loreConn.execute("""CREATE TABLE IF NOT EXISTS lore (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            category TEXT NOT NULL,
            entity TEXT NOT NULL,
            fact TEXT NOT NULL,
            source TEXT DEFAULT 'chat',
            confidence REAL DEFAULT 0.6,
            updatedAt DATETIME DEFAULT CURRENT_TIMESTAMP
        )""")
        loreConn.commit()
        log("LIBRARIAN", "Initialized lore.db (FTS5 removed).")


initLoreDb()


# ============================================================================
# v9: FACT POLICY
# ============================================================================

def getFactConfidence(category, source, speakerName=""):
    trusted = speakerName.lower() in TRUSTED_PLAYERS if speakerName else False
    if category == "game":
        if source == "web":
            return CONFIDENCE_GAME_WEB
        return CONFIDENCE_GAME_TRUSTED_PLAYER if trusted else CONFIDENCE_GAME_CHAT
    elif category == "character":
        return CONFIDENCE_CHARACTER_TRUSTED_PLAYER if trusted else CONFIDENCE_CHARACTER_CHAT
    elif category == "server":
        return CONFIDENCE_SERVER_TRUSTED_PLAYER if trusted else CONFIDENCE_SERVER_CHAT
    return 0.0


# ============================================================================
# LORE DB HELPERS
# ============================================================================

def searchLore(entityName, category=None):
    """Returns (factsText, maxConfidence) or (None, 0.0)."""
    if not entityName:
        return None, 0.0
    with loreLock:
        try:
            if category:
                rows = loreConn.execute(
                    "SELECT fact, confidence FROM lore WHERE entity = ? AND category = ?",
                    (entityName, category)
                ).fetchall()
                if not rows:
                    rows = loreConn.execute(
                        "SELECT fact, confidence FROM lore WHERE entity LIKE ? AND category = ?",
                        (f"%{entityName}%", category)
                    ).fetchall()
            else:
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
                validRows = [(fact, conf) for fact, conf in rows if conf >= LORE_CONFIDENCE_THRESHOLD]
                if validRows:
                    facts = [fact for fact, conf in validRows]
                    maxConf = max(conf for fact, conf in validRows)
                    return "\n".join(facts), maxConf
        except Exception as e:
            log("LIBRARIAN", f"Lore search error: {e}")
    return None, 0.0
    
    
def searchLoreByKeywords(playerMsg, category=None):
    """Fallback: search fact text for keywords from the player's message.
    Returns a list of (entity, fact, confidence) tuples, or None."""
    if not playerMsg or not LORE_FACT_TEXT_SEARCH:
        return None

    stopWords = {'who', 'what', 'where', 'when', 'why', 'how', 'the', 'a', 'an',
                 'is', 'are', 'was', 'were', 'do', 'does', 'did', 'that', 'this',
                 'it', 'i', 'you', 'he', 'she', 'we', 'they', 'my', 'your', 'his',
                 'her', 'our', 'their', 'me', 'him', 'us', 'them', 'am', 'of',
                 'in', 'on', 'at', 'to', 'for', 'with', 'about', 'and', 'or',
                 'but', 'not', 'so', 'if', 'then', 'than', 'too', 'very', 'can',
                 'will', 'just', 'should', 'now', 'lol', 'wtf', 'fr', ' demanded',
                 'proof', 'leader', 'guild', 'this', 'have', 'been', 'some'}
    words = [w.strip('.,!?"\'').lower() for w in playerMsg.split()]
    keywords = [w for w in words if len(w) > 3 and w not in stopWords]

    if not keywords:
        return None

    # Cap keywords to avoid overly broad queries
    keywords = keywords[:4]

    with loreLock:
        try:
            conditions = " OR ".join(["fact LIKE ?" for _ in keywords])
            params = [f"%{kw}%" for kw in keywords]

            if category:
                sql = f"SELECT entity, fact, confidence FROM lore WHERE category = ? AND ({conditions})"
                params = [category] + params
            else:
                sql = f"SELECT entity, fact, confidence FROM lore WHERE ({conditions})"

            rows = loreConn.execute(sql, params).fetchall()
            if not rows:
                return None

            # Deduplicate by entity, cap at 5 candidates
            seen = set()
            uniqueRows = []
            for entity, fact, conf in rows:
                if entity not in seen and conf >= LORE_CONFIDENCE_THRESHOLD:
                    seen.add(entity)
                    uniqueRows.append((entity, fact, conf))
                if len(uniqueRows) >= 5:
                    break

            return uniqueRows if uniqueRows else None
        except Exception as e:
            log("LIBRARIAN", f"Lore keyword search error: {e}")
    return None

def filterLoreRelevance(playerMsg, candidates):
    """Strict LLM filter: only return facts that DIRECTLY answer the player's question."""
    candidateText = ""
    for i, (entity, fact, conf) in enumerate(candidates, 1):
        candidateText += f"[{i}] (Entity: {entity}) {fact}\n"

    filterPrompt = f"""You are a strict relevance filter for an MMO knowledge database.
A player asked a question. A keyword search found these candidate facts.

PLAYER QUESTION: {playerMsg}

CANDIDATE FACTS:
{candidateText}
STRICT RULES:
- A fact is ONLY relevant if it DIRECTLY answers or relates to the specific question asked.
- Vague thematic overlap is NOT relevance. Shared keywords in a different context is NOT relevance.
- If you are not 100% sure a fact answers the question, exclude it.
- It is perfectly fine and expected to reply NONE most of the time.

Reply with ONLY the numbers of the relevant facts separated by commas (e.g., "1,3"), or "NONE" if nothing is directly relevant."""

    result = callLlm(LIBRARIAN_CONFIG, "", filterPrompt, {"temperature": 0.1, "num_predict": 20}).strip()

    if not result or result.upper() == "NONE":
        return None

    try:
        indices = [int(x.strip()) for x in result.split(',') if x.strip().isdigit()]
        relevant = [candidates[i - 1] for i in indices if 1 <= i <= len(candidates)]
        return relevant if relevant else None
    except Exception:
        return None

def insertLore(category, entity, fact, source, confidence):
    if not entity or not fact:
        log("LIBRARIAN", "Skipped lore insert: empty entity or fact.")
        return
    with loreLock:
        try:
            existingRows = loreConn.execute(
                "SELECT id, fact, confidence FROM lore WHERE entity = ? ORDER BY id",
                (entity,)
            ).fetchall()

            if existingRows:
                existingId = existingRows[0][0]
                existingFact = "\n".join(r[1] for r in existingRows)
                existingConf = max(r[2] for r in existingRows)

                # Self-heal: consolidate duplicate rows into primary
                if len(existingRows) > 1:
                    dupIds = [r[0] for r in existingRows[1:]]
                    placeholders = ','.join('?' * len(dupIds))
                    loreConn.execute(f"DELETE FROM lore WHERE id IN ({placeholders})", dupIds)
                    loreConn.execute(
                        "UPDATE lore SET fact = ?, confidence = ? WHERE id = ?",
                        (existingFact, existingConf, existingId))
                    log("LIBRARIAN", f"Consolidated {len(existingRows)} lore rows for '{entity}'.")

                # SEMANTIC DEDUPLICATION
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
                    if len(existingFact) <= LORE_FACT_SURGERY_MAX_CHARS:
                        # v9: Surgical replacement
                        surgeryPrompt = f"""You are a fact editor. Below are existing facts about an entity.
Each existing line is one atomic fact.
One of them is contradicted by a new, higher-confidence fact.
Replace ONLY the line that directly contradicts the new fact. Keep all other lines unchanged.
Output ONLY the updated lines, one per line. No commentary.

EXISTING FACTS:
{existingFact}

NEW FACT (higher confidence):
{fact}"""
                        surgeryResult = callLlm(LIBRARIAN_CONFIG, "", surgeryPrompt, {"temperature": 0.1, "num_predict": 200}).strip()
                        if surgeryResult:
                            loreConn.execute(
                                "UPDATE lore SET fact = ?, confidence = ?, updatedAt = CURRENT_TIMESTAMP WHERE id = ?",
                                (surgeryResult, confidence, existingId))
                            log("LIBRARIAN", f"Surgical overwrite for '{entity}'.")
                        else:
                            loreConn.execute(
                                "UPDATE lore SET fact = ?, confidence = ?, updatedAt = CURRENT_TIMESTAMP WHERE id = ?",
                                (fact, confidence, existingId))
                            log("LIBRARIAN", f"Surgery failed, full overwrite for: {entity}")
                    else:
                        loreConn.execute(
                            "UPDATE lore SET fact = ?, confidence = ?, updatedAt = CURRENT_TIMESTAMP WHERE id = ?",
                            (fact, confidence, existingId))
                        log("LIBRARIAN", f"Full overwrite for: {entity} (blob too long for surgery)")

                elif verdict.startswith("NEW"):
                    mergedFact = f"{existingFact}\n{fact}"
                    newConf = max(existingConf, confidence)
                    loreConn.execute(
                        "UPDATE lore SET fact = ?, confidence = ?, updatedAt = CURRENT_TIMESTAMP WHERE id = ?",
                        (mergedFact, newConf, existingId))
                    log("LIBRARIAN", f"Appended new lore to: {entity}")

                else:  # DUPLICATE or fallback
                    loreConn.execute(
                        "UPDATE lore SET updatedAt = CURRENT_TIMESTAMP WHERE id = ?",
                        (existingId,))
            else:
                loreConn.execute(
                    "INSERT INTO lore (category, entity, fact, source, confidence) VALUES (?, ?, ?, ?, ?)",
                    (category, entity, fact, source, confidence))
                log("LIBRARIAN", f"Stored new {category} lore: {entity}")
            loreConn.commit()
        except Exception as e:
            log("LIBRARIAN", f"Lore insert error: {e}")


def archiveForLore(entityName, rawFacts):
    logPayload("ARCHIVIST_RAW", rawFacts)
    userPrompt = f"Entity being archived: {entityName}\n\nRaw facts:\n{rawFacts}"
    archived = callLlm(LIBRARIAN_CONFIG, LORE_ARCHIVIST_SYSTEM_PROMPT, userPrompt, {"temperature": 0.1, "num_predict": 150}).strip()
    negative_markers = ["no_useful_data", "do not contain information", "no information", "does not contain", "not contain", "provided facts describe"]
    if not archived or any(marker in archived.lower() for marker in negative_markers):
        log("LIBRARIAN", "Archivist returned negative/empty fact. Skipping insert.")
        return None
    logPayload("ARCHIVIST_CLEAN", archived)
    return archived


# ============================================================================
# LIBRARIAN LOGIC
# ============================================================================

def parseLibrarianJson(raw):
    default = {"category": "none", "query": None, "searchEntity": None, "subject": None}
    if not raw:
        return default
    try:
        data = json.loads(raw.strip())
        return {
            "category": data.get("category", "none").lower(),
            "query": data.get("query"),
            "searchEntity": data.get("searchEntity"),
            "subject": data.get("subject")
        }
    except Exception:
        pass
    match = re.search(r'{.*}', raw, re.DOTALL)
    if match:
        try:
            data = json.loads(match.group(0))
            return {
                "category": data.get("category", "none").lower(),
                "query": data.get("query"),
                "searchEntity": data.get("searchEntity"),
                "subject": data.get("subject")
            }
        except Exception:
            pass
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
    dbEntity = cleanEntityName(rawEntity) if rawEntity else None

    loreFactsText = ""
    loreMaxConf = 0.0

    # 1. Exact entity search
    if dbEntity and category != "none":
        facts, conf = searchLore(dbEntity, category)
        if facts:
            loreFactsText = facts
            loreMaxConf = conf
            log("LIBRARIAN", f"ENTITY LORE HIT ({category}): '{dbEntity}' (conf={conf:.2f}).")

    # 2. Keyword fallback (searches ALL categories for reverse-lookup questions)
    if LORE_FACT_TEXT_SEARCH:
        keywordResults = searchLoreByKeywords(payload.get("playerMsg", ""), None)
        if keywordResults:
            relevantFacts = filterLoreRelevance(payload.get("playerMsg", ""), keywordResults)
            if relevantFacts:
                kwFactsText = "\n".join([fact for _, fact, _ in relevantFacts])
                kwMaxConf = max([conf for _, _, conf in relevantFacts])
                
                # Merge keyword facts with entity facts
                if loreFactsText:
                    loreFactsText += "\n" + kwFactsText
                else:
                    loreFactsText = kwFactsText
                loreMaxConf = max(loreMaxConf, kwMaxConf)
                log("LIBRARIAN", f"KEYWORD LORE HIT: {len(relevantFacts)} relevant fact(s) via text search.")
            else:
                log("LIBRARIAN", f"KEYWORD LORE: {len(keywordResults)} candidates found, LLM filtered all out.")

    # 3. Routing decision
    if loreFactsText:
        payload["loreContext"] = f"\n\n[LORE CONTEXT]:\n{loreFactsText}\nUse this factual information to answer accurately."
        if category != "game":
            log("LIBRARIAN", f"LORE HIT ({category}): Skipping web.")
            return
        elif loreMaxConf >= LORE_GAME_BLOCK_WEB_THRESHOLD:
            log("LIBRARIAN", f"LORE HIT (game, high conf={loreMaxConf:.2f}): Skipping web.")
            return
        else:
            log("LIBRARIAN", f"LORE HIT (game, low conf={loreMaxConf:.2f}): Continuing to web search.")
            
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
        current_speaker = None
        facts_found = 0

        for line in raw.split('\n'):
            line = line.strip()
            if not line:
                continue
            if line.upper().startswith("CATEGORY:"):
                current_category = line.split(':', 1)[1].strip().lower()
            elif line.upper().startswith("ENTITY:"):
                current_entity = line.split(':', 1)[1].strip()
            elif line.upper().startswith("SPEAKER:"):
                current_speaker = line.split(':', 1)[1].strip()
            elif line.upper().startswith("FACT:"):
                current_fact = line.split(':', 1)[1].strip()

        # Process after all fields are collected (order-independent)
        if current_category and current_entity and current_fact and current_category in ["character", "server", "game"]:
            dbEntity = cleanEntityName(current_entity)
            if not dbEntity:
                log("LIBRARIAN", f"Skipped fact with empty cleaned entity: {current_entity}")
            else:
                confidenceSpeaker = current_speaker if current_speaker else targetName
                factConfidence = getFactConfidence(current_category, "chat", confidenceSpeaker)
                insertLore(current_category, dbEntity, current_fact, "chat", factConfidence)
                log("LIBRARIAN", f"Extracted {current_category} fact from chat: {dbEntity} -> {current_fact} (speaker: {confidenceSpeaker}, conf: {factConfidence})")
                facts_found += 1

        if facts_found == 0:
            log("LIBRARIAN", "Fact extractor returned data, but couldn't parse valid Entity/Fact/Category.")
    except Exception as e:
        log("LIBRARIAN", f"Fact extraction failed (non-fatal): {e}")