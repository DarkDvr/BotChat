import json
import re
import sqlite3
import threading
import time
from ddgs import DDGS
from config import (
    CLASSIFIER_CONFIG, CLASSIFIER_SYSTEM_PROMPT, CLASSIFIER_TEMPERATURE,
    FRENCHMAID_CONFIG, FRENCHMAID_SYSTEM_PROMPT, FRENCHMAID_TEMPERATURE,
    FRENCHMAID_ENABLED, FRENCHMAID_NO_DATA_MARKER, FRENCHMAID_MAX_OUTPUT_LENGTH,
    FRENCHMAID_MAX_SNIPPET_LENGTH, SEARCH_RESULT_COUNT,
    SKEPTIC_MODE, SKEPTIC_OVERRIDE_PROMPT,
    LORE_DB_PATH, TRUSTED_FACT_MARKER, TRUSTED_PLAYERS,
    LORE_ARCHIVIST_SYSTEM_PROMPT, FACT_EXTRACTION_PROMPT, LORE_CORRECTION_PROMPT,
    WEB_SEARCH_QUERY_PREFIX, WEB_SEARCH_EXCLUDED_TERMS, DEBUG_FULL_LOGS,
    WEB_SEARCH_DELAY,
    BOTRAM_GOSSIP_ENABLED, BOTRAM_GOSSIP_MAX_KEYWORDS, GOSSIP_SUMMARIZER_PROMPT,
    CLASSIFIER_THINK_ENABLED, FACT_EXTRACTION_THINK_ENABLED, LORE_CORRECTION_THINK_ENABLED,
)
from logging_utils import log, logPayload
from engine import callLlm, cacheGet, cacheSet, getQueryLock
from french_maid import cleanEntityName
from french_maid import loadSlangTable, expandSlang

loadSlangTable()

try:
    from ddgs import DDGS
except ImportError:
    DDGS = None

# ============================================================================
# LORE DB (THE WIKI)
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
            source TEXT DEFAULT 'web',
            confidence REAL DEFAULT 1.0,
            updatedAt DATETIME DEFAULT CURRENT_TIMESTAMP
        )""")
        
        # Ensure the slang table exists so DB wipes don't break the expansion engine
        loreConn.execute("""CREATE TABLE IF NOT EXISTS slang (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            abbr TEXT UNIQUE NOT NULL,
            full_name TEXT NOT NULL
        )""")
        
        loreConn.commit()
        log("CLASSIFIER", "Initialized pristine Wiki (lore.db).")

initLoreDb()

def searchLore(entityName, category=None):
    if not entityName: return None
    with loreLock:
        try:
            if category:
                rows = loreConn.execute("SELECT fact FROM lore WHERE entity = ? AND category = ?", (entityName, category)).fetchall()
                if not rows:
                    rows = loreConn.execute("SELECT fact FROM lore WHERE entity LIKE ? AND category = ?", (f"%{entityName}%", category)).fetchall()
            else:
                rows = loreConn.execute("SELECT fact FROM lore WHERE entity = ?", (entityName,)).fetchall()
                if not rows:
                    rows = loreConn.execute("SELECT fact FROM lore WHERE entity LIKE ?", (f"%{entityName}%",)).fetchall()
            
            if rows:
                return "\n".join([r[0] for r in rows])
        except Exception as e:
            log("CLASSIFIER", f"Lore search error: {e}")
    return None

def insertLore(category, entity, fact, source):
    if not entity or not fact: return
    with loreLock:
        try:
            existing = loreConn.execute("SELECT id, fact FROM lore WHERE entity = ? AND category = ?", (entity, category)).fetchone()
            if existing:
                # Append new atomic facts on a new line
                if fact not in existing[1]:
                    merged = f"{existing[1]}\n{fact}"
                    loreConn.execute("UPDATE lore SET fact = ?, updatedAt = CURRENT_TIMESTAMP WHERE id = ?", (merged, existing[0]))
                    log("CLASSIFIER", f"Appended verified lore to: {entity}")
                else:
                    log("CLASSIFIER", f"Lore already contains fact for: {entity}")
            else:
                loreConn.execute("INSERT INTO lore (category, entity, fact, source) VALUES (?, ?, ?, ?)",
                                 (category, entity, fact, source))
                log("CLASSIFIER", f"Stored new verified lore: {entity}")
            loreConn.commit()
        except Exception as e:
            log("CLASSIFIER", f"Lore insert error: {e}")
            
def correctLoreWithTrustedFact(entity, newFact):
    """Trusted-player correction: find and replace contradicting facts, or append if none found."""
    if not entity or not newFact:
        return
    
    existingLore = searchLore(entity, "game")
    
    if not existingLore:
        insertLore("game", entity, newFact, "trusted")
        log("CLASSIFIER", f"Trusted correction: No existing lore for {entity}. Inserted new fact.")
        return
    
    prompt = LORE_CORRECTION_PROMPT.replace("{entity}", entity)
    prompt = prompt.replace("{existing_facts}", existingLore)
    prompt = prompt.replace("{new_fact}", newFact)
    
    options = {
        "temperature": 0.1, 
        "num_predict": 150 if LORE_CORRECTION_THINK_ENABLED else 50,
        "think": LORE_CORRECTION_THINK_ENABLED
    }
    llmResponse = callLlm(CLASSIFIER_CONFIG, "", prompt, options).strip()
    
    if DEBUG_FULL_LOGS:
        logPayload("LORE_CORRECTION_LLM", llmResponse)
    
    if llmResponse.upper() == "APPEND" or not llmResponse:
        insertLore("game", entity, newFact, "trusted")
        log("CLASSIFIER", f"Trusted correction: No contradiction found for {entity}. Appended.")
        return
    
    # LLM identified a contradicting line — replace it
    with loreLock:
        try:
            row = loreConn.execute("SELECT id, fact FROM lore WHERE entity = ? AND category = ?", (entity, "game")).fetchone()
            if not row:
                insertLore("game", entity, newFact, "trusted")
                return
            
            loreId, currentFact = row
            lines = currentFact.split("\n")
            replaced = False
            for i, line in enumerate(lines):
                if line.strip() == llmResponse.strip():
                    lines[i] = newFact
                    replaced = True
                    break
            
            if replaced:
                merged = "\n".join(lines)
                loreConn.execute("UPDATE lore SET fact = ?, source = 'trusted', updatedAt = CURRENT_TIMESTAMP WHERE id = ?", (merged, loreId))
                loreConn.commit()
                log("CLASSIFIER", f"Trusted correction: Replaced contradicting line for {entity}.")
            else:
                # LLM returned something but it didn't match any line — fallback to append
                insertLore("game", entity, newFact, "trusted")
                log("CLASSIFIER", f"Trusted correction: LLM line didn't match. Appended for {entity}.")
        except Exception as e:
            log("CLASSIFIER", f"Trusted correction error: {e}")

def archiveForLore(entityName, rawFacts):
    userPrompt = f"Entity being archived: {entityName}\n\nRaw facts:\n{rawFacts}"
    archived = callLlm(CLASSIFIER_CONFIG, LORE_ARCHIVIST_SYSTEM_PROMPT, userPrompt, {"temperature": 0.1, "num_predict": 150}).strip()

    if DEBUG_FULL_LOGS:
        logPayload("ARCHIVIST_RAW", archived)

    if not archived or "no_useful_data" in archived.lower():
        return None
    return archived
    
# ============================================================================
# PYTHON-SIDE FILTER (Blocks SEO spam, root homepages, and Retail data)
# ============================================================================
BLOCKED_DOMAINS = [
    "youtube.com", "twitch.tv", "tiktok.com", "pinterest.com",
    "similarweb.com", "vk.ru", "vk.com", "ok.ru", "github.com",
    "felbite.com", "emucoach.com", "gamefaqs.gamespot.com"
]

def filterDdgsResults(results):
    cleanResults = []
    for item in results:
        url = item.get("href", "")
        if not url: continue
        
        # 1. Skip blocked domains
        if any(domain in url.lower() for domain in BLOCKED_DOMAINS):
            continue
            
        # 2. Wowhead Era Enforcement
        if "wowhead.com" in url.lower():
            isClassicEra = any(era in url.lower() for era in ["/classic/", "/wotlk/", "/tbc/", "classic.wowhead.com"])
            if not isClassicEra:
                continue
            
        # 3. Skip root homepages
        try:
            path = url.split("://")[-1].split("/", 1)
            if len(path) < 2 or not path[1].strip():
                continue
        except Exception:
            continue
            
        cleanResults.append(item)
    return cleanResults

def cleanDdgsSnippet(text):
    if not text: return ""
    # Remove leading dates like "September 12, 2022 - "
    text = re.sub(r'^[A-Za-z]+ \d{1,2}, \d{4}\s*[-–—]\s*', '', text)
    # Remove common boilerplate
    boilerplate = [
        r'Sign in', r'WowheadWowhead', r'Click to view this NPC',
        r'Tip: Click map to zoom', r'Requires HandyNotes to be installed\.?',
        r'Added in patch \d+\.\d+\.?', r'Added in World of Warcraft.*?\.',
        r'Always up to date with the latest patch.*?\.',
        r'\[.*?\]' 
    ]
    for bp in boilerplate:
        text = re.sub(bp, '', text, flags=re.IGNORECASE)
    text = re.sub(r'\s+', ' ', text).strip()
    return text

# ============================================================================
# WEB SEARCH
# ============================================================================
def searchWeb(query, dbEntity):
    cached = cacheGet(query)
    if cached is not None:
        log("CACHE", f"HIT for query: {query!r} (Using CACHED results)")
        return cached

    lock = getQueryLock(f"web_{query}")
    with lock:
        cached = cacheGet(query)
        if cached is not None: return cached

        webContext = ""
        # Clean query: NO negative operators, letting DDGS rank naturally
        expandedQuery = expandSlang(query)
        searchQuery = f"{WEB_SEARCH_QUERY_PREFIX} {expandedQuery}".strip()
        log("DDGS", f"Executing search: {searchQuery!r}")
        
        rawResults = []
        try:
            time.sleep(WEB_SEARCH_DELAY) # Polite delay to prevent IP soft-bans
            with DDGS() as ddgs:
                rawResults = [r for r in ddgs.text(searchQuery, max_results=SEARCH_RESULT_COUNT)]
        except Exception as e:
            log("DDGS", f"DDGS request error: {e}")
            if SKEPTIC_MODE:
                webContext = f"\n\n[SYSTEM OVERRIDE - SKEPTIC MODE]:\n{SKEPTIC_OVERRIDE_PROMPT}"
                cacheSet(query, webContext)
                return webContext

        cleanResults = filterDdgsResults(rawResults)
        for r in cleanResults:
            r['body'] = cleanDdgsSnippet(r.get('body', ''))
        
        # Respecting DEBUG_FULL_LOGS
        if DEBUG_FULL_LOGS:
            logPayload("DDGS_RAW", str(rawResults))
            logPayload("DDGS_FILTERED", str(cleanResults))
        else:
            log("DDGS", f"Found {len(cleanResults)} clean results out of {len(rawResults)} raw.")
            
        if not cleanResults:
            log("DDGS", "Search returned 0 results after filtering.")
            if SKEPTIC_MODE:
                webContext = f"\n\n[SYSTEM OVERRIDE - SKEPTIC MODE]:\n{SKEPTIC_OVERRIDE_PROMPT}"
        elif FRENCHMAID_ENABLED:
            # Deterministic Era Detection based on URLs
            eraHint = ""
            for r in cleanResults:
                url = r.get("href", "").lower()
                if "classic" in url or "vanilla" in url:
                    eraHint = "\n[SYSTEM NOTE: The search results below are from Vanilla / Classic WoW databases, NOT WotLK.]"
                    break
                if "tbc" in url or "burning-crusade" in url:
                    eraHint = "\n[SYSTEM NOTE: The search results below are from The Burning Crusade (TBC) databases, NOT WotLK.]"
                    break

            maidInput = f"SEARCH QUERY: {query}{eraHint}\n\nSEARCH RESULTS:\n"
            for i, r in enumerate(cleanResults, 1):
                maidInput += f"[Result {i}] Title: {r.get('title', '')}\n{r.get('body', '')}\n\n"
            
            maidRaw = callLlm(FRENCHMAID_CONFIG, FRENCHMAID_SYSTEM_PROMPT, maidInput, {"temperature": FRENCHMAID_TEMPERATURE, "num_predict": 500})
            
            if DEBUG_FULL_LOGS:
                logPayload("FRENCHMAID_RAW", maidRaw)
            else:
                if FRENCHMAID_NO_DATA_MARKER not in maidRaw:
                    log("FRENCHMAID", f"Extracted {len(maidRaw)} chars of useful data.")
                else:
                    log("FRENCHMAID", "Model reported NO_USEFUL_DATA.")
                    
            if FRENCHMAID_NO_DATA_MARKER not in maidRaw:
                cleaned = re.sub(r'\n+', '\n', maidRaw.strip())[:FRENCHMAID_MAX_OUTPUT_LENGTH]
                webContext = f"\n\n[WEB SEARCH CONTEXT]:\n{cleaned}"
                if dbEntity:
                    expandedCleaned = expandSlang(cleaned)
                    archived = archiveForLore(dbEntity, expandedCleaned)
                    if archived: insertLore("game", dbEntity, archived, "web")
            else:
                if SKEPTIC_MODE: 
                    webContext = f"\n\n[SYSTEM OVERRIDE - SKEPTIC MODE]:\n{SKEPTIC_OVERRIDE_PROMPT}"
        else:
            kept = [f"- {r.get('title', '')}: {r.get('body', '')[:FRENCHMAID_MAX_SNIPPET_LENGTH]}" for r in cleanResults if r.get('body')]
            if kept:
                joined = "\n".join(kept)
                webContext = "\n\n[WEB SEARCH CONTEXT]:\n" + joined
                if dbEntity:
                    expandedJoined = expandSlang(joined)
                    archived = archiveForLore(dbEntity, expandedJoined)
                    if archived: insertLore("game", dbEntity, archived, "web")
                    
        cacheSet(query, webContext)
        return webContext

# ============================================================================
# CLASSIFIER LOGIC
# ============================================================================
def parseJson(raw):
    if not raw: return {}
    try: return json.loads(raw.strip())
    except: pass
    match = re.search(r'{.*}', raw, re.DOTALL)
    if match:
        try: return json.loads(match.group(0))
        except: pass
    return {}

def extractTrustedFact(cleanMsg, speaker):
    prompt = FACT_EXTRACTION_PROMPT.replace("{statement}", cleanMsg)
    options = {
        "temperature": 0.1, 
        "num_predict": 200 if FACT_EXTRACTION_THINK_ENABLED else 100,
        "think": FACT_EXTRACTION_THINK_ENABLED
    }
    raw = callLlm(CLASSIFIER_CONFIG, "", prompt, options).strip()
    data = parseJson(raw)
    entity = expandSlang(cleanEntityName(data.get("entity", "")))
    fact = expandSlang(data.get("fact", ""))
    if entity and fact:
        correctLoreWithTrustedFact(entity, fact)
        return fact
    return None

def checkLoreRelevance(question, lore):
    """Strict YES/NO check: Does the current Wiki fact answer this specific question?"""
    prompt = f"""You are a strict QA checker.
QUESTION: {question}
WIKI FACTS: {lore}
Does the WIKI FACTS contain the direct answer to the QUESTION?
Reply ONLY YES or NO."""
    try:
        result = callLlm(CLASSIFIER_CONFIG, "", prompt, {"temperature": 0.1, "num_predict": 5}).strip().upper()
        return "YES" in result
    except Exception:
        return False
        
def cleanGossipKeywords(rawKeywords):
    if rawKeywords is None:
        return []

    if isinstance(rawKeywords, str):
        rawKeywords = [rawKeywords]

    if not isinstance(rawKeywords, list):
        return []

    stopWords = {
        "the", "and", "for", "with", "that", "this", "what", "where",
        "when", "who", "why", "how", "was", "were", "is", "are", "did"
    }

    cleaned = []
    for keyword in rawKeywords:
        keyword = " ".join(str(keyword).split())
        keyword = keyword.strip().strip('"\'')[:50]

        if len(keyword) < 2:
            continue

        if keyword.lower() in stopWords:
            continue

        if keyword not in cleaned:
            cleaned.append(keyword)

        if len(cleaned) >= BOTRAM_GOSSIP_MAX_KEYWORDS:
            break

    return cleaned


def formatGossipConversations(conversations):
    blocks = []

    for convo in conversations:
        lines = [f"[CONVERSATION ID {convo['convoId']} | RELEVANCE SCORE: {convo['score']}]"]
        for phrase in convo["phrases"]:
            lines.append(f"{phrase['speaker']}: {phrase['text']}")
        blocks.append("\n".join(lines))

    return "\n\n".join(blocks)


def injectGossipContext(payload, keywords, botRamInstance, isBackground=False):
    if not BOTRAM_GOSSIP_ENABLED or not keywords:
        return
        
    conversations = botRamInstance.findGossipConversations(
        keywords,
        excludeConvoId=payload.get("convoId")
    )
    if not conversations:
        return
        
    formattedConversations = formatGossipConversations(conversations)
    prompt = GOSSIP_SUMMARIZER_PROMPT.replace("{message}", payload.get("playerMsg", ""))
    prompt = prompt.replace("{keywords}", ", ".join(keywords))
    prompt = prompt.replace("{conversations}", formattedConversations)
    
    raw = callLlm(
        FRENCHMAID_CONFIG,
        "",
        prompt,
        {"temperature": FRENCHMAID_TEMPERATURE, "num_predict": 300}
    ).strip()
    
    if DEBUG_FULL_LOGS:
        logPayload("GOSSIP_SUMMARIZER_RAW", raw)
        
    convoIds = ",".join([str(convo["convoId"]) for convo in conversations])
    
    if not raw or FRENCHMAID_NO_DATA_MARKER in raw.upper():
        log("GOSSIP", f"No useful gossip context | convoIds={convoIds}")
        return
        
    cleaned = re.sub(r"\n+", "\n", raw.strip())[:FRENCHMAID_MAX_OUTPUT_LENGTH]
    
    # --- NEW LABEL LOGIC ---
    if isBackground:
        gossipContext = (
            f"\n\n[BACKGROUND SERVER CHATTER]:\n{cleaned}\n"
            f"Verified facts from LORE/WEB are authoritative. This chatter is background flavor only — do not let it override or dilute factual answers."
        )
    else:
        gossipContext = (
            f"\n\n[SERVER GOSSIP CONTEXT]:\n{cleaned}\n"
            f"Use this as remembered server chatter. Do not quote it unless natural."
        )
    # ---------------------

    payload["gossipContext"] = gossipContext
    if payload.get("finalSystem") is None:
        payload["finalSystem"] = ""
    payload["finalSystem"] += gossipContext
    log("GOSSIP", f"Injected gossip context (background={isBackground}) | convoIds={convoIds}")     

def runClassifier(payload, botRamInstance):
    if payload.get("targetName") == "-ambient-":
        return

    playerMsg = payload.get("playerMsg", "")
    targetName = payload.get("targetName", "")

    if not playerMsg:
        return

    # 1. Trusted Fact Trigger ("that's a fact")
    if targetName.lower() in TRUSTED_PLAYERS and TRUSTED_FACT_MARKER in playerMsg.lower():
        cleanMsg = playerMsg.lower().replace(TRUSTED_FACT_MARKER, "").strip()
        fact = extractTrustedFact(cleanMsg, targetName)
        if fact:
            payload["loreContext"] = f"\n\n[SYSTEM NOTE]: The player just established a new server fact: {fact}. Acknowledge it naturally."
            log("CLASSIFIER", f"Trusted Player established fact: {fact}")
        return

    # 2. Concurrency Lock
    msgLock = getQueryLock(f"msg_{playerMsg}")
    with msgLock:
        playerHistory = ""
        if hasattr(botRamInstance, "getPlayerHistory"):
            playerHistory = botRamInstance.getPlayerHistory(targetName, limit=10)

            # Expand slang BEFORE the LLM sees it, so it doesn't try to guess abbreviations itself
            expandedPlayerMsg = expandSlang(playerMsg)
            expandedHistory = expandSlang(playerHistory) if playerHistory else ""

            # 3. Intent Classification
            options = {
                "temperature": CLASSIFIER_TEMPERATURE, 
                "num_predict": 300 if CLASSIFIER_THINK_ENABLED else 150,
                "think": CLASSIFIER_THINK_ENABLED
            }

        if expandedHistory:
            userPrompt = f"[PLAYER'S RECENT MESSAGES]:\n{expandedHistory}\n\n[NEW MESSAGE]: {expandedPlayerMsg}"
        else:
            userPrompt = expandedPlayerMsg

        log("CLASSIFIER", f"EXPANDED PROMPT: {userPrompt}")
        raw = callLlm(CLASSIFIER_CONFIG, CLASSIFIER_SYSTEM_PROMPT, userPrompt, options)
        decision = parseJson(raw)

        intent = decision.get("intent", "statement")
        topic = decision.get("topic", "")
        rawEntity = decision.get("entity", "")
        keywords = cleanGossipKeywords(decision.get("keywords", []))

        # Safety: LLM sometimes returns a list instead of a string
        if isinstance(topic, list):
            topic = " ".join(str(t) for t in topic)
        if isinstance(rawEntity, list):
            rawEntity = " ".join(str(t) for t in rawEntity)

        log("CLASSIFIER", f"INTENT: {intent} | TOPIC: {topic!r} | ENTITY: {rawEntity!r} | KEYWORDS: {keywords!r}")

        if intent == "statement":
            injectGossipContext(payload, keywords, botRamInstance)
            return

        dbEntity = cleanEntityName(rawEntity) if rawEntity else None

        if intent == "game_question":
            # Search Wiki
            if dbEntity:
                lore = searchLore(dbEntity, "game")
                if lore:
                    if checkLoreRelevance(playerMsg, lore):
                        payload["loreContext"] = f"\n\n[LORE CONTEXT]:\n{lore}"
                        log("CLASSIFIER", f"LORE DB HIT (Relevant): {dbEntity}")
                        injectGossipContext(payload, keywords, botRamInstance, isBackground=True)
                        return
                    else:
                        log("CLASSIFIER", f"LORE DB HIT (Missing info): {dbEntity}. Searching web to expand Lore DB.")
                        
            # Search Web
            if topic:
                web = searchWeb(topic, dbEntity)
                if web:
                    payload["webContext"] = web
                    # Only inject lore if NOT in skeptic mode (skeptic = topic is unverified, lore would be noise)
                    if not (SKEPTIC_MODE and SKEPTIC_OVERRIDE_PROMPT in web):
                        if dbEntity:
                            lore = searchLore(dbEntity, "game")
                            if lore:
                                payload["loreContext"] = f"\n\n[LORE CONTEXT]:\n{lore}"
                    return

        elif intent == "social_question":
            # Search Transcript
            if topic:
                transcript = botRamInstance.searchTranscript(topic)
                if transcript:
                    from config import TRANSCRIPT_SUMMARIZER_PROMPT
                    prompt = TRANSCRIPT_SUMMARIZER_PROMPT.replace("{question}", playerMsg).replace("{transcript}", transcript)
                    summary = callLlm(CLASSIFIER_CONFIG, "", prompt, {"temperature": 0.1, "num_predict": 150}).strip()

                    if summary and summary.upper() != "NO_ANSWER":
                        payload["transcriptContext"] = f"\n\n[PAST CHAT EVIDENCE]:\n{summary}"
                        log("CLASSIFIER", "TRANSCRIPT HIT: Summarized past chat.")
                    else:
                        payload["transcriptContext"] = "\n\n[SYSTEM NOTE]: You have no memory of this being discussed in the past. Say you don't remember."
                        log("CLASSIFIER", "TRANSCRIPT MISS: LLM returned NO_ANSWER.")
                else:
                    payload["transcriptContext"] = "\n\n[SYSTEM NOTE]: You have no memory of this being discussed in the past. Say you don't remember."
                    log("CLASSIFIER", "TRANSCRIPT MISS: No FTS5 matches.")

            injectGossipContext(payload, keywords, botRamInstance)

        else:
            injectGossipContext(payload, keywords, botRamInstance)