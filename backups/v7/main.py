import os
import sys
import json
import re
import traceback
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

if os.name == "nt":
    os.system("")

from config import VERSION, PROXY_HOST, PROXY_PORT
from logging_utils import log, logPayload, logRequestHeader
from french_maid import validateConfig
from botram import botRamInstance
from librarian import runLibrarianAndSearch, extractFactsFromChat
from roleplayer import runRoleplayer

def buildPayload(prompt, system, options):
    return {
        # Raw input (set by main.py)
        "rawPrompt":      prompt,
        "rawSystem":      system,
        "options":        options,

        # Identity (set by BotRAM)
        "botName":        None,
        "targetName":     None,
        "zone":           None,
        "playerMsg":      None,
        "isEvent":        False,
        "cleanPrompt":    None,
        "finalSystem":    None,

        # Conversation (set by BotRAM)
        "convoId":        None,
        "isShortCircuit": False,
        "memoryContext":  None,

        # Knowledge (set by Librarian)
        "webContext":     None,
        "loreContext":    None,

        # Response (set by Roleplayer)
        "response":       None,
    }

def processRequest(prompt, system, options):
    payload = buildPayload(prompt, system, options)

    # PASS 1: BotRAM
    botRamInstance.processIncoming(payload)
    logPayload("PROMPT_IN", payload["cleanPrompt"])

    if payload["isShortCircuit"]:
        log("ROLEPLAYER", "FINAL REPLY (SHORT-CIRCUIT): [Silent/Empty]")
        payload["response"] = ""
        botRamInstance.processOutgoing(payload)
        return payload["response"]

    # PASS 2+3: Librarian + DDGS + FrenchMaid
    runLibrarianAndSearch(payload)
    if payload.get("webContext"):
        payload["finalSystem"] += payload["webContext"]
    if payload.get("loreContext"): 
        payload["finalSystem"] += payload["loreContext"]

    # PASS 4: Roleplayer
    runRoleplayer(payload)
    log("ROLEPLAYER", f"FINAL REPLY: '{payload['response']}'")

    # POST: BotRAM stores the reply
    botRamInstance.processOutgoing(payload)

    # POST: Librarian evaluates the exchange for new social/server facts
    extractFactsFromChat(payload)

    return payload["response"]

class ProxyHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        if self.path != "/api/generate":
            self.send_error(404)
            return

        contentLength = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(contentLength)
        try:
            data = json.loads(body.decode("utf-8"))
        except Exception:
            self.send_error(400, "Invalid JSON")
            return

        prompt = data.get("prompt", "")
        system = data.get("system", "")
        options = data.get("options", {})

        chatMsg = ""
        cleanPrompt = prompt.replace('\\n', '\n')

        matchPlayer = re.search(r"(\w+) says: '(.*?)'\.\s*Your Info:", cleanPrompt, re.DOTALL)
        if matchPlayer:
            chatMsg = f"[CHAT -> {matchPlayer.group(1)}] {matchPlayer.group(2).strip()}"
        else:
            matchEvent = re.search(r"Event:\s*(.*?)(?=\.\s+React|\.\s+Avoid|\.\s+You are playing|$)", cleanPrompt, re.DOTALL)
            if matchEvent:
                chatMsg = f"[EVENT] {matchEvent.group(1).strip()}"
            else:
                if cleanPrompt.startswith("You are "):
                    instrMatch = re.search(r"(Talk about|Ask|Share|Comment on|Plan your|Evaluate|Discuss|Say|Rant about|Suggest|Look at|Mention|Complain about|Brag about|Debate|Tell|Describe) [^.]+\.?", cleanPrompt)
                    if instrMatch:
                        chatMsg = f"[BOT INITIATED] {instrMatch.group(0).strip()}"
                    else:
                        snippet = cleanPrompt[:100].replace(chr(10), ' ')
                        chatMsg = f"[BOT INITIATED] {snippet}..."
                else:
                    snippet = cleanPrompt[:100].replace(chr(10), ' ')
                    chatMsg = f"[UNKNOWN] {snippet}..."

        logRequestHeader(len(prompt), chatMsg)

        # HARD FAIL POLICY
        try:
            response = processRequest(prompt, system, options)
        except Exception as e:
            errorMsg = f"UNEXPECTED FATAL ERROR at {datetime.now()}:\n{traceback.format_exc()}\n---\n"
            with open("error.log", "a", encoding="utf-8") as f:
                f.write(errorMsg)
            log("ERROR", "FATAL: Unexpected error. Dumped to error.log. Terminating process.")
            os._exit(1)

        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps({"response": response}).encode("utf-8"))

    def log_message(self, fmt, *args):
        pass

def main():
    log("STARTUP", f"SmartProxyAgentic v{VERSION} starting...")
    validateConfig()
    server = ThreadingHTTPServer((PROXY_HOST, PROXY_PORT), ProxyHandler)
    log("STARTUP", f"Listening on http://{PROXY_HOST}:{PROXY_PORT}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        log("STARTUP", "Shutting down.")
        server.shutdown()

if __name__ == "__main__":
    main()