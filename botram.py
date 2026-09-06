import sqlite3
import re
import threading
from datetime import datetime
from config import (
    BOTRAM_ENABLED, BOTRAM_DB_PATH, BOTRAM_MODEL, BOTRAM_PAST_CONVOS_LIMIT,
    BOTRAM_PHRASES_PER_CONVO, BOTRAM_AMBIENT_CONVOS_LIMIT, BOTRAM_AMBIENT_JOIN_MINUTES,
    BOTRAM_RETENTION_DAYS, BOTRAM_ACTIVE_WINDOW_MINUTES, BOTRAM_LOOP_CHECK_THRESHOLD,
    BOTRAM_LOOP_CHECK_INTERVAL, BOTRAM_LOOP_COOLDOWN_MINUTES,
    BOTRAM_MEMORY_WINDOW_MINUTES, BOTRAM_TRANSCRIPT_WINDOW, ROLEPLAYER_SYSTEM_PROMPT,
    BOTRAM_LOOP_PROMPT, BOTRAM_MEMORY_RECALL_PROMPT, DEBUG_FULL_LOGS,
    BOTRAM_GOSSIP_ENABLED, BOTRAM_GOSSIP_WINDOW_HOURS, BOTRAM_GOSSIP_MAX_CONVOS,
    BOTRAM_GOSSIP_MAX_PHRASES, BOTRAM_GOSSIP_MIN_SCORE, BOTRAM_GOSSIP_MAX_KEYWORDS,
)

from logging_utils import log, logPayload
from engine import callOllama
from french_maid import cleanWowLinks, expandSlang, should_break_on_message

class BotRAM:
    def __init__(self):
        self.conn = sqlite3.connect(BOTRAM_DB_PATH, check_same_thread=False)
        self.lock = threading.Lock()
        self._initDb()

    def _initDb(self):
        with self.lock:
            self.conn.execute("""CREATE TABLE IF NOT EXISTS conversations (
                id INTEGER PRIMARY KEY AUTOINCREMENT, status TEXT DEFAULT 'active',
                last_valid_timestamp DATETIME DEFAULT CURRENT_TIMESTAMP, participants TEXT)""")
            self.conn.execute("""CREATE TABLE IF NOT EXISTS phrases (
                id INTEGER PRIMARY KEY AUTOINCREMENT, convo_id INTEGER, speaker TEXT,
                text TEXT, timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(convo_id) REFERENCES conversations(id))""")
            
            # v10: FTS5 for Transcript Search
            self.conn.execute("""CREATE VIRTUAL TABLE IF NOT EXISTS phrasesFts
                USING fts5(text, content='phrases', content_rowid='id')""")
            
            # Triggers to keep FTS5 in sync
            self.conn.execute("""CREATE TRIGGER IF NOT EXISTS phrases_ai AFTER INSERT ON phrases BEGIN
                INSERT INTO phrasesFts(rowid, text) VALUES (new.id, new.text);
            END""")
            self.conn.execute("""CREATE TRIGGER IF NOT EXISTS phrases_ad AFTER DELETE ON phrases BEGIN
                INSERT INTO phrasesFts(phrasesFts, rowid, text) VALUES('delete', old.id, old.text);
            END""")
            self.conn.execute("""CREATE TRIGGER IF NOT EXISTS phrases_au AFTER UPDATE ON phrases BEGIN
                INSERT INTO phrasesFts(phrasesFts, rowid, text) VALUES('delete', old.id, old.text);
                INSERT INTO phrasesFts(rowid, text) VALUES (new.id, new.text);
            END""")

            # Cleanup old records
            self.conn.execute("DELETE FROM phrases WHERE timestamp < datetime('now', ?)", (f'-{BOTRAM_RETENTION_DAYS} days',))
            self.conn.execute("DELETE FROM conversations WHERE last_valid_timestamp < datetime('now', ?)", (f'-{BOTRAM_RETENTION_DAYS} days',))
            
            # Rebuild FTS5 index on startup to ensure sync
            try:
                self.conn.execute("INSERT INTO phrasesFts(phrasesFts) VALUES('rebuild')")
            except:
                pass
                
            self.conn.commit()
            log("BOTRAM", f"Initialized DB & FTS5. Cleaned records older than {BOTRAM_RETENTION_DAYS} days.")

    def searchTranscript(self, keywords):
        """Searches the FTS5 index and returns a context window of surrounding phrases."""
        safeWords = [w for w in keywords.split() if w.isalnum() and len(w) > 2]
        if not safeWords: return None
        
        matchStr = " OR ".join(safeWords[:4])
        with self.lock:
            try:
                rows = self.conn.execute(
                    "SELECT rowid FROM phrasesFts WHERE phrasesFts MATCH ? ORDER BY rank LIMIT 3", 
                    (matchStr,)
                ).fetchall()
                
                if not rows: return None
                
                context = []
                for row in rows:
                    rid = row[0]
                    window = self.conn.execute(
                        "SELECT speaker, text FROM phrases WHERE id BETWEEN ? AND ? ORDER BY id", 
                        (rid - BOTRAM_TRANSCRIPT_WINDOW, rid + BOTRAM_TRANSCRIPT_WINDOW)
                    ).fetchall()
                    context.append("\n".join([f"{s}: {t}" for s, t in window]))
                    
                return "\n---\n".join(context)
            except Exception as e:
                log("BOTRAM", f"FTS5 search error: {e}")
        return None
    
    def _escapeLike(self, value):
        value = str(value)
        value = value.replace("\\", "\\\\")
        value = value.replace("%", "\\%")
        value = value.replace("_", "\\_")
        return value

    def findGossipConversations(self, keywords, excludeConvoId=None):
        """Finds top-scoring server conversations matching gossip keywords."""
        if not BOTRAM_ENABLED or not BOTRAM_GOSSIP_ENABLED:
            return []

        if not keywords or BOTRAM_GOSSIP_MAX_CONVOS <= 0 or BOTRAM_GOSSIP_MAX_PHRASES <= 0:
            return []

        safeKeywords = []
        for keyword in keywords[:BOTRAM_GOSSIP_MAX_KEYWORDS]:
            keyword = " ".join(str(keyword).split())
            keyword = keyword.strip().strip('"\'')[:50]
            if len(keyword) >= 2:
                safeKeywords.append(keyword)

        if not safeKeywords:
            return []

        candidateScores = {}
        timeWindow = f"-{BOTRAM_GOSSIP_WINDOW_HOURS} hours"

        with self.lock:
            for keyword in safeKeywords:
                try:
                    pattern = f"%{self._escapeLike(keyword)}%"
                    rows = self.conn.execute("""
                        SELECT DISTINCT convo_id FROM phrases
                        WHERE timestamp > datetime('now', ?)
                          AND text LIKE ? ESCAPE '\\'
                    """, (timeWindow, pattern)).fetchall()

                    for row in rows:
                        convoId = row[0]
                        if excludeConvoId is not None and convoId == excludeConvoId:
                            continue
                        candidateScores[convoId] = candidateScores.get(convoId, 0) + 1
                except Exception as e:
                    log("BOTRAM", f"Gossip keyword search error | keyword={keyword!r} | error={e}")

            filteredCandidates = []
            for convoId, score in candidateScores.items():
                if score >= BOTRAM_GOSSIP_MIN_SCORE:
                    filteredCandidates.append((convoId, score))

            if not filteredCandidates:
                log("BOTRAM", f"GOSSIP no matching convoIds | keywords={safeKeywords}")
                return []

            convoIds = [convoId for convoId, _ in filteredCandidates]
            placeholders = ",".join(["?"] * len(convoIds))
            rows = self.conn.execute(
                f"SELECT id, last_valid_timestamp FROM conversations WHERE id IN ({placeholders})",
                convoIds
            ).fetchall()

            convoTimes = {}
            for row in rows:
                convoTimes[row[0]] = row[1] if row[1] else ""

            sortedCandidates = sorted(
                filteredCandidates,
                key=lambda item: (item[1], convoTimes.get(item[0], "")),
                reverse=True
            )

            gossipConversations = []
            totalPhrases = 0

            for convoId, score in sortedCandidates:
                if len(gossipConversations) >= BOTRAM_GOSSIP_MAX_CONVOS:
                    break

                try:
                    phrases = self.conn.execute("""
                        SELECT speaker, text FROM phrases
                        WHERE convo_id = ?
                        ORDER BY timestamp ASC, id ASC
                    """, (convoId,)).fetchall()
                except Exception as e:
                    log("BOTRAM", f"Gossip phrase fetch error | convoId={convoId} | error={e}")
                    continue

                if not phrases:
                    continue

                if totalPhrases + len(phrases) > BOTRAM_GOSSIP_MAX_PHRASES:
                    if gossipConversations:
                        continue
                    phrases = phrases[:BOTRAM_GOSSIP_MAX_PHRASES]

                phraseList = []
                for phraseRow in phrases:
                    phraseList.append({
                        "speaker": phraseRow[0],
                        "text": phraseRow[1]
                    })

                gossipConversations.append({
                    "convoId": convoId,
                    "score": score,
                    "phrases": phraseList
                })

                totalPhrases += len(phraseList)

            if not gossipConversations:
                log("BOTRAM", f"GOSSIP no usable convoIds | keywords={safeKeywords}")
                return []

            summaryParts = []
            for convo in gossipConversations:
                summaryParts.append(f"convoId={convo['convoId']} score={convo['score']}")

            log("BOTRAM", f"GOSSIP keywords={safeKeywords} | {' | '.join(summaryParts)}")

            if DEBUG_FULL_LOGS:
                for convo in gossipConversations:
                    lines = [f"convoId={convo['convoId']} score={convo['score']}"]
                    for phrase in convo["phrases"]:
                        lines.append(f"{phrase['speaker']}: {phrase['text']}")
                    logPayload("BOTRAM_GOSSIP_CONVO", "\n".join(lines))

            return gossipConversations

    def extractIdentity(self, prompt):
        prompt = cleanWowLinks(prompt)
        prompt = prompt.replace('\\n', '\n')
        
        # Extract full identity line (e.g., "Gizmoia, a level 43 warrior")
        botMatch = re.search(r"^You are ([^\.]+)", prompt)
        if botMatch:
            botIdentity = botMatch.group(1).strip()
            botName = botIdentity.split(",")[0].strip().lower()
        else:
            botIdentity = ""
            botName = "unknownbot"

        targetMatch = re.search(r"NEW MESSAGE from (\w+):", prompt)
        if not targetMatch:
            targetMatch = re.search(r"(\w+) says: '.*?'\.\s*Your Info:", prompt)
        if not targetMatch:
            targetMatch = re.search(r"Event:\s*(\w+)", prompt)

        targetName = targetMatch.group(1).lower() if targetMatch else "-ambient-"

        zoneMatch = re.search(r"Zone:\s*([\w\s]+),", prompt)
        zone = zoneMatch.group(1).strip() if zoneMatch else "Unknown"

        return botName, targetName, zone, prompt, botIdentity

    def stripCppHistory(self, prompt):
        return re.sub(r"Recent chats with .*?(?=NEW MESSAGE from|\w+ says: ')", "", prompt, flags=re.DOTALL)

    def gatherMemoryContext(self, botName, targetName):
        historyText = ""
        seenIds = set()

        if targetName != "-ambient-":
            convos = self.conn.execute("""
                SELECT c.id FROM conversations c
                WHERE (c.participants LIKE ? OR c.participants LIKE ?)
                AND c.last_valid_timestamp > datetime('now', ?)
                ORDER BY c.last_valid_timestamp DESC LIMIT ?
            """, (f'%{targetName}%', f'%{botName}%', f'-{BOTRAM_MEMORY_WINDOW_MINUTES} minutes', BOTRAM_PAST_CONVOS_LIMIT)).fetchall()
            for convoRow in convos:
                cid = convoRow[0]
                seenIds.add(cid)
                rows = self.conn.execute("SELECT speaker, text FROM phrases WHERE convo_id=? ORDER BY timestamp DESC LIMIT ?",
                                         (cid, BOTRAM_PHRASES_PER_CONVO)).fetchall()
                if rows:
                    historyText += f"\n[Conversation ID {cid}]:\n"
                    historyText += "\n".join([f"{r[0]}: {r[1]}" for r in reversed(rows)]) + "\n"

        ambientConvos = self.conn.execute("""
            SELECT c.id FROM conversations c
            WHERE c.participants = ? AND c.last_valid_timestamp > datetime('now', ?)
            ORDER BY c.last_valid_timestamp DESC LIMIT ?
        """, (botName, f'-{BOTRAM_ACTIVE_WINDOW_MINUTES} minutes', BOTRAM_AMBIENT_CONVOS_LIMIT)).fetchall()

        for convoRow in ambientConvos:
            cid = convoRow[0]
            if cid in seenIds: continue
            seenIds.add(cid)
            rows = self.conn.execute("SELECT speaker, text FROM phrases WHERE convo_id=? ORDER BY timestamp DESC LIMIT ?",
                                     (cid, BOTRAM_PHRASES_PER_CONVO)).fetchall()
            if rows:
                historyText += f"\n[Conversation ID {cid} - Ambient]:\n"
                historyText += "\n".join([f"{r[0]}: {r[1]}" for r in reversed(rows)]) + "\n"

        if historyText:
            if DEBUG_FULL_LOGS: logPayload("BOTRAM_CONTEXT", historyText)
            log("BOTRAM", f"Gathered {len(seenIds)} conversations for memory parsing.")
        return historyText.strip()

    def processIncoming(self, payload):
        if not BOTRAM_ENABLED:
            payload["cleanPrompt"] = payload["rawPrompt"].replace('\\n', '\n')
            payload["finalSystem"] = payload["rawSystem"]
            return

        botName, targetName, zone, cleanPrompt, botIdentity = self.extractIdentity(payload["rawPrompt"])
        cleanPrompt = self.stripCppHistory(cleanPrompt)

        payload["botName"] = botName
        payload["targetName"] = targetName
        payload["zone"] = zone
        payload["cleanPrompt"] = cleanPrompt

        playerMsg = ""
        isEvent = False

        msgMatch = re.search(r"NEW MESSAGE from \w+:\s*(.*?)\s*\w+ says:", cleanPrompt, re.DOTALL)
        if not msgMatch: msgMatch = re.search(r"\w+ says:\s*'(.*?)'", cleanPrompt)
        if not msgMatch:
            msgMatch = re.search(r"Event:\s*(.*?)(?=\.\s+React|\.\s+Avoid|\.\s+You are playing|$)", cleanPrompt, re.DOTALL)
            if msgMatch: isEvent = True

        if msgMatch: playerMsg = cleanWowLinks(msgMatch.group(1).strip())

        # Check for break strings BEFORE any processing
        if playerMsg and should_break_on_message(playerMsg):
            log("BOTRAM", f"BREAK_ON_STRING detected in message from {botName}. Discarding request.")
            payload["discard"] = True
            return

        payload["playerMsg"] = playerMsg
        payload["isEvent"] = isEvent

        with self.lock:
            now = datetime.utcnow()
            convoId = None
            isShortCircuit = False
            memoryContext = ""

            if targetName == "-ambient-":
                row = self.conn.execute("""SELECT id FROM conversations
                    WHERE participants LIKE ? AND status = 'active'
                    AND last_valid_timestamp > datetime('now', ?)
                    ORDER BY last_valid_timestamp DESC LIMIT 1""",
                    (f'%{botName}%', f'-{BOTRAM_AMBIENT_JOIN_MINUTES} minutes')).fetchone()
                if row:
                    convoId = row[0]
                    log("BOTRAM", f"Ambient chatter joining active convo {convoId}.")

            elif playerMsg:
                historyText = self.gatherMemoryContext(botName, targetName)
                if historyText:
                    # Expand slang so the Memory Parser doesn't hallucinate fake names for abbreviations
                    expandedPlayerMsg = expandSlang(playerMsg)
                    recallPrompt = BOTRAM_MEMORY_RECALL_PROMPT.replace("{conversations}", historyText).replace("{new_message}", expandedPlayerMsg)
                    recallResp = callOllama(BOTRAM_MODEL, "", recallPrompt, {"temperature": 0.1, "num_predict": 150}).strip()
                    logPayload("BOTRAM_MEMORY_RAW", recallResp)

                    targetConvoId = None
                    context = ""
                    for line in recallResp.split('\n'):
                        if line.upper().startswith("CONVO:"):
                            convoVal = line.split(':', 1)[1].strip().upper()
                            if convoVal != "NEW":
                                digits = re.sub(r'\D', '', convoVal)
                                if digits: targetConvoId = int(digits)
                        elif line.upper().startswith("CONTEXT:"):
                            context = line.split(':', 1)[1].strip()

                    if targetConvoId is not None:
                        row = self.conn.execute("SELECT id, status, last_valid_timestamp, participants FROM conversations WHERE id=?", (targetConvoId,)).fetchone()
                        if row:
                            cid, dbStatus, lastValidStr, participants = row
                            lastValid = datetime.strptime(lastValidStr, "%Y-%m-%d %H:%M:%S")
                            timeDiff = (now - lastValid).total_seconds() / 60.0

                            if dbStatus == 'over':
                                if timeDiff < BOTRAM_LOOP_COOLDOWN_MINUTES:
                                    isShortCircuit = True
                                    convoId = cid
                                    log("BOTRAM", f"Memory Parser matched convo {cid}, but it's OVER (cooldown). Short-circuiting.")
                                else:
                                    self.conn.execute("UPDATE conversations SET status='active' WHERE id=?", (cid,))
                                    convoId = cid
                                    log("BOTRAM", f"Resurrecting expired cooldown convo {cid}.")
                            else:
                                convoId = cid
                                log("BOTRAM", f"Memory Parser matched convo {cid}. Joining.")
                                newParts = set(participants.split(','))
                                newParts.add(botName)
                                if targetName != "-ambient-": newParts.add(targetName)
                                self.conn.execute("UPDATE conversations SET participants=? WHERE id=?", (','.join(newParts), cid))
                        else:
                            log("BOTRAM", f"Memory Parser returned convo {targetConvoId}, but it doesn't exist. Starting fresh.")

                    if not isShortCircuit and context and "NO_RELEVANT_CONTEXT" not in context.upper():
                        memoryContext = f"\n\n[BOTRAM MEMORY CONTEXT]:\n{context}\nUse this memory naturally. Do not reference it unless relevant."
                        log("BOTRAM", f"Memory Parser injected context for {botName}.")

            if not convoId and not isShortCircuit:
                parts = f"{botName},{targetName}" if targetName != "-ambient-" else botName
                cur = self.conn.execute("INSERT INTO conversations (status, participants) VALUES ('active', ?)", (parts,))
                convoId = cur.lastrowid
                log("BOTRAM", f"Started new conversation {convoId} ({botName} -> {targetName})")

            if playerMsg:
                speakerName = "[EVENT]" if isEvent else (targetName if targetName != "-ambient-" else botName)
                isBotEcho = self.conn.execute("""
                    SELECT 1 FROM phrases
                    WHERE speaker = ? AND text = ? AND timestamp > datetime('now', '-15 seconds')
                    LIMIT 1
                """, (speakerName, playerMsg)).fetchone()

                if not isBotEcho:
                    self.conn.execute("INSERT INTO phrases (convo_id, speaker, text) VALUES (?, ?, ?)",
                                      (convoId, speakerName, playerMsg))
                    if not isShortCircuit:
                        self.conn.execute("UPDATE conversations SET last_valid_timestamp=CURRENT_TIMESTAMP WHERE id=?", (convoId,))
                else:
                    log("BOTRAM", f"Skipped duplicate event/echo from {speakerName}.")

            if not isShortCircuit and convoId:
                count = self.conn.execute("SELECT COUNT(*) FROM phrases WHERE convo_id=?", (convoId,)).fetchone()[0]
                if count > BOTRAM_LOOP_CHECK_THRESHOLD and count % BOTRAM_LOOP_CHECK_INTERVAL == 0:
                    log("BOTRAM", f"Running loop detection for convo {convoId}...")
                    lastMsgs = self.conn.execute("SELECT speaker, text FROM phrases WHERE convo_id=? ORDER BY timestamp DESC LIMIT 8", (convoId,)).fetchall()
                    msgText = "\n".join([f"{m[0]}: {m[1]}" for m in reversed(lastMsgs)])
                    loopPrompt = BOTRAM_LOOP_PROMPT.replace("{messages}", msgText)
                    loopResp = callOllama(BOTRAM_MODEL, "", loopPrompt, {"temperature": 0.1, "num_predict": 20}).strip().upper()
                    if loopResp.startswith("LOOP"):
                        self.conn.execute("UPDATE conversations SET status='over' WHERE id=?", (convoId,))
                        isShortCircuit = True
                        log("BOTRAM", f"LOOP DETECTED in convo {convoId}. Marking OVER and short-circuiting.")

            self.conn.commit()

        payload["convoId"] = convoId
        payload["isShortCircuit"] = isShortCircuit
        payload["memoryContext"] = memoryContext
        # We intentionally drop payload["rawSystem"] (the C++ module's generic prompt) 
        # to avoid conflicting instructions. We rely entirely on ROLEPLAYER_SYSTEM_PROMPT.
        baseSystem = memoryContext if memoryContext else ""
        
        # --- META ROLEPLAYER PROMPT ---
        payload["finalSystem"] = ROLEPLAYER_SYSTEM_PROMPT
        if baseSystem:
            payload["finalSystem"] += "\n\n" + baseSystem

        # --- EXTRACT BOT IDENTITY & PERSONALITY ---
        # The bot's level/class are in the opening identity line:
        identityMatch = re.search(r"^You are ([^.]+)\.", cleanPrompt, re.IGNORECASE)
        botIdentity = identityMatch.group(1).strip() if identityMatch else botName.capitalize()

        # Extract the persona/lore text between the identity line and the stats blocks
        personaMatch = re.search(r"^You are [^.]+\.\s*(.*?)(?=\s*Your Info:|\s*Player Info:|\s*NEW MESSAGE|\s*\w+ says:)", cleanPrompt, re.DOTALL | re.IGNORECASE)
        botPersona = personaMatch.group(1).strip() if personaMatch else ""

        # Bot stats are after "Your Info:" but must stop before "Player Info:"
        infoMatch = re.search(
            r"Your Info:\s*(.*?)(?=\.\s*Player Info:|\s+Player Info:|$)",
            cleanPrompt,
            re.DOTALL | re.IGNORECASE
        )
        botStats = infoMatch.group(1).strip() if infoMatch else ""

        # Defensive cleanup: never pass player stats into the bot's identity block
        botStats = re.sub(r"\bPlayer Info:.*", "", botStats, flags=re.DOTALL | re.IGNORECASE)
        # Remove overly narrow location/map fields from the stats block
        botStats = re.sub(r"\bLocation:\s*[^,.]*(?:,|\.)?", "", botStats, flags=re.IGNORECASE)
        botStats = re.sub(r"\bMap:\s*[^,.]*(?:,|\.)?", "", botStats, flags=re.IGNORECASE)
        # Remove Zone from the raw string so we can append it cleanly at the end
        botStats = re.sub(r"\bZone:\s*[^,.]*(?:,|\.)?", "", botStats, flags=re.IGNORECASE)
        
        # Normalize whitespace/punctuation after removals
        botStats = re.sub(r"\s+", " ", botStats)
        botStats = re.sub(r"\s+,", ",", botStats)
        botStats = re.sub(r",\s*,", ",", botStats)
        botStats = botStats.strip(" ,.")

        # Append the broad zone (extracted earlier by extractIdentity)
        if zone and zone != "Unknown":
            botStats = f"{botStats}, Zone: {zone}" if botStats else f"Zone: {zone}"

        # Prepend the identity line (which contains the level!) to the stats
        fullStats = f"{botIdentity}. {botStats}" if botIdentity else botStats
        log("BOTRAM", f"EXTRACTED STATS: {fullStats!r}")
        
        # Inject Persona into System Prompt
        if botPersona:
            payload["finalSystem"] = (payload["finalSystem"] or "") + f"\n\n[YOUR CHARACTER PERSONALITY & LORE]:\n{botPersona}"
            log("BOTRAM", "Injected bot persona/lore into system prompt.")

        # Inject Stats into System Prompt
        if fullStats:
            payload["finalSystem"] = (payload["finalSystem"] or "") + f"\n\n[YOUR CHARACTER STATS]: {fullStats}\nCRITICAL: If asked about your level, class, or spec, use ONLY these exact stats. Do not hallucinate numbers."

        # --- BUILD CLEAN ROLEPLAYER PROMPT ---
        recentHistory = self.getConvoContext(convoId, limit=10)
        
        if isEvent:
            newMsg = f"[System Event]: {playerMsg}"
        else:
            newMsg = f"{targetName.capitalize()} says: {playerMsg}"
            
        if recentHistory:
            payload["cleanPrompt"] = f"Recent conversation:\n{recentHistory}\n\n{newMsg}"
        else:
            payload["cleanPrompt"] = newMsg
    
    def getConvoContext(self, convoId, limit=10):
        """Fetches the last N messages from a specific conversation for coreference resolution."""
        if not convoId:
            return ""
        with self.lock:
            try:
                rows = self.conn.execute(
                    "SELECT speaker, text FROM phrases WHERE convo_id = ? ORDER BY timestamp DESC LIMIT ?",
                    (convoId, limit)
                ).fetchall()
                if not rows:
                    return ""
                # Reverse to chronological order
                rows.reverse()
                contextLines = [f"{r[0]}: {r[1]}" for r in rows]
                return "\n".join(contextLines)
            except Exception as e:
                log("BOTRAM", f"Error fetching convo context: {e}")
                return ""

    def getPlayerHistory(self, targetName, limit=15):
        """Fetches the most recent full transcript of conversations the player participated in."""
        if not targetName or targetName == "-ambient-":
            return ""
            
        with self.lock:
            try:
                # 1. Find the most recent conversations the player was in
                convo_rows = self.conn.execute("""
                    SELECT id FROM conversations 
                    WHERE participants LIKE ? 
                    ORDER BY last_valid_timestamp DESC 
                    LIMIT 5
                """, (f"%{targetName}%",)).fetchall()
                
                if not convo_rows:
                    return ""
                    
                convo_ids = [row[0] for row in convo_rows]
                placeholders = ",".join(["?"] * len(convo_ids))
                
                # 2. Fetch the full transcript (all speakers) from those convos, up to the limit
                rows = self.conn.execute(f"""
                    SELECT speaker, text FROM phrases 
                    WHERE convo_id IN ({placeholders})
                    ORDER BY timestamp DESC, id DESC 
                    LIMIT ?
                """, (*convo_ids, limit)).fetchall()
                
                if not rows:
                    return ""
                    
                # 3. Reverse to chronological order and format
                rows.reverse()
                history_lines = [f"{r[0]}: {r[1]}" for r in rows]
                return "\n".join(history_lines)
                
            except Exception as e:
                log("BOTRAM", f"Error fetching player history: {e}")
                return ""

    def processOutgoing(self, payload):
        if not BOTRAM_ENABLED: return
        convoId = payload.get("convoId")
        if not convoId: return

        botName = payload["botName"]
        targetName = payload["targetName"]
        botReply = payload.get("response", "")

        cleanReply = cleanWowLinks(str(botReply))

        if cleanReply is None or not cleanReply.strip():
            if targetName == "-ambient-": log("BOTRAM", f"{botName} stayed silent. No phrase stored.")
            else: log("BOTRAM", f"{botName} stayed silent toward {targetName}. No phrase stored.")
            return

        with self.lock:
            row = self.conn.execute("SELECT status FROM conversations WHERE id=?", (convoId,)).fetchone()
            if row and row[0] == 'over':
                self.conn.execute("INSERT INTO phrases (convo_id, speaker, text) VALUES (?, ?, ?)", (convoId, botName, cleanReply))
                if targetName == "-ambient-": log("BOTRAM", f"{botName} posted an ignored ambient thought: \"{cleanReply}\"")
                else: log("BOTRAM", f"{botName} said to {targetName} (ignored, convo over): \"{cleanReply}\"")
            else:
                self.conn.execute("INSERT INTO phrases (convo_id, speaker, text) VALUES (?, ?, ?)", (convoId, botName, cleanReply))
                self.conn.execute("UPDATE conversations SET last_valid_timestamp=CURRENT_TIMESTAMP WHERE id=?", (convoId,))
                if targetName == "-ambient-": log("BOTRAM", f"{botName} posted an ambient thought: \"{cleanReply}\"")
                else: log("BOTRAM", f"{botName} said to {targetName}: \"{cleanReply}\"")
            self.conn.commit()

botRamInstance = BotRAM()