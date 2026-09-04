import sqlite3
import re
import threading
from datetime import datetime
from config import (
    BOTRAM_ENABLED, BOTRAM_DB_PATH, BOTRAM_MODEL, BOTRAM_PAST_CONVOS_LIMIT,
    BOTRAM_PHRASES_PER_CONVO, BOTRAM_AMBIENT_CONVOS_LIMIT, BOTRAM_AMBIENT_JOIN_MINUTES,
    BOTRAM_RETENTION_DAYS, BOTRAM_ACTIVE_WINDOW_MINUTES, BOTRAM_LOOP_CHECK_THRESHOLD,
    BOTRAM_LOOP_CHECK_INTERVAL, BOTRAM_LOOP_COOLDOWN_MINUTES,
    BOTRAM_MEMORY_WINDOW_MINUTES,
    BOTRAM_LOOP_PROMPT, BOTRAM_MEMORY_RECALL_PROMPT, DEBUG_FULL_LOGS
)

from logging_utils import log, logPayload
from engine import callOllama
from french_maid import cleanWowLinks

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
            self.conn.execute("DELETE FROM phrases WHERE timestamp < datetime('now', ?)", (f'-{BOTRAM_RETENTION_DAYS} days',))
            self.conn.execute("DELETE FROM conversations WHERE last_valid_timestamp < datetime('now', ?)", (f'-{BOTRAM_RETENTION_DAYS} days',))
            self.conn.commit()
            log("BOTRAM", f"Initialized DB. Cleaned records older than {BOTRAM_RETENTION_DAYS} days.")

    def extractIdentity(self, prompt):
        prompt = cleanWowLinks(prompt)
        prompt = prompt.replace('\\n', '\n')
        botMatch = re.search(r"^You are (\w+)", prompt)
        botName = botMatch.group(1).lower() if botMatch else "unknownbot"

        targetMatch = re.search(r"NEW MESSAGE from (\w+):", prompt)
        if not targetMatch:
            targetMatch = re.search(r"(\w+) says: '.*?'\.\s*Your Info:", prompt)
        if not targetMatch:
            targetMatch = re.search(r"Event:\s*(\w+)", prompt)

        targetName = targetMatch.group(1).lower() if targetMatch else "-ambient-"

        zoneMatch = re.search(r"Zone:\s*([\w\s]+),", prompt)
        zone = zoneMatch.group(1).strip() if zoneMatch else "Unknown"

        return botName, targetName, zone, prompt

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

        # v9: This block is OUTSIDE the if above. It always runs.
        ambientConvos = self.conn.execute("""
            SELECT c.id FROM conversations c
            WHERE c.participants = ? AND c.last_valid_timestamp > datetime('now', ?)
            ORDER BY c.last_valid_timestamp DESC LIMIT ?
        """, (botName, f'-{BOTRAM_ACTIVE_WINDOW_MINUTES} minutes', BOTRAM_AMBIENT_CONVOS_LIMIT)).fetchall()

        for convoRow in ambientConvos:
            cid = convoRow[0]
            if cid in seenIds:
                continue
            seenIds.add(cid)
            rows = self.conn.execute("SELECT speaker, text FROM phrases WHERE convo_id=? ORDER BY timestamp DESC LIMIT ?",
                                     (cid, BOTRAM_PHRASES_PER_CONVO)).fetchall()
            if rows:
                historyText += f"\n[Conversation ID {cid} - Ambient]:\n"
                historyText += "\n".join([f"{r[0]}: {r[1]}" for r in reversed(rows)]) + "\n"

        if historyText:
            if DEBUG_FULL_LOGS:
                logPayload("BOTRAM_CONTEXT", historyText)
            log("BOTRAM", f"Gathered {len(seenIds)} conversations for memory parsing.")
        return historyText.strip()

    def processIncoming(self, payload):
        if not BOTRAM_ENABLED:
            payload["cleanPrompt"] = payload["rawPrompt"].replace('\\n', '\n')
            payload["finalSystem"] = payload["rawSystem"]
            return

        botName, targetName, zone, cleanPrompt = self.extractIdentity(payload["rawPrompt"])
        cleanPrompt = self.stripCppHistory(cleanPrompt)

        payload["botName"] = botName
        payload["targetName"] = targetName
        payload["zone"] = zone
        payload["cleanPrompt"] = cleanPrompt

        playerMsg = ""
        isEvent = False

        msgMatch = re.search(r"NEW MESSAGE from \w+:\s*(.*?)\s*\w+ says:", cleanPrompt, re.DOTALL)
        if not msgMatch:
            msgMatch = re.search(r"\w+ says:\s*'(.*?)'", cleanPrompt)
        if not msgMatch:
            msgMatch = re.search(r"Event:\s*(.*?)(?=\.\s+React|\.\s+Avoid|\.\s+You are playing|$)", cleanPrompt, re.DOTALL)
            if msgMatch:
                isEvent = True

        if msgMatch:
            playerMsg = cleanWowLinks(msgMatch.group(1).strip())

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
                    recallPrompt = BOTRAM_MEMORY_RECALL_PROMPT.replace("{conversations}", historyText).replace("{new_message}", playerMsg)
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
        payload["finalSystem"] = payload["rawSystem"] + memoryContext if memoryContext else payload["rawSystem"]

    def processOutgoing(self, payload):
        if not BOTRAM_ENABLED: return
        convoId = payload.get("convoId")
        if not convoId: return

        botName = payload["botName"]
        targetName = payload["targetName"]
        botReply = payload.get("response", "")

        cleanReply = cleanWowLinks(str(botReply))

        if cleanReply is None or not cleanReply.strip():
            if targetName == "-ambient-":
                log("BOTRAM", f"{botName} stayed silent. No phrase stored.")
            else:
                log("BOTRAM", f"{botName} stayed silent toward {targetName}. No phrase stored.")
            return

        with self.lock:
            row = self.conn.execute("SELECT status FROM conversations WHERE id=?", (convoId,)).fetchone()
            if row and row[0] == 'over':
                self.conn.execute("INSERT INTO phrases (convo_id, speaker, text) VALUES (?, ?, ?)",
                                  (convoId, botName, cleanReply))
                if targetName == "-ambient-":
                    log("BOTRAM", f"{botName} posted an ignored ambient thought: \"{cleanReply}\"")
                else:
                    log("BOTRAM", f"{botName} said to {targetName} (ignored, convo over): \"{cleanReply}\"")
            else:
                self.conn.execute("INSERT INTO phrases (convo_id, speaker, text) VALUES (?, ?, ?)",
                                  (convoId, botName, cleanReply))
                self.conn.execute("UPDATE conversations SET last_valid_timestamp=CURRENT_TIMESTAMP WHERE id=?", (convoId,))
                if targetName == "-ambient-":
                    log("BOTRAM", f"{botName} posted an ambient thought: \"{cleanReply}\"")
                else:
                    log("BOTRAM", f"{botName} said to {targetName}: \"{cleanReply}\"")
            self.conn.commit()

botRamInstance = BotRAM()