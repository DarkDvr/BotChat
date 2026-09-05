import os
import sys
import json
import re
import traceback
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from french_maid import validateConfig, cleanWowLinks

if os.name == "nt":
    os.system("")

from config import VERSION, PROXY_HOST, PROXY_PORT
from logging_utils import log, logPayload, logRequestHeader
from french_maid import validateConfig
from botram import botRamInstance
from classifier import runClassifier
from roleplayer import runRoleplayer

def buildPayload(prompt, system, options):
    return {
        "rawPrompt":      prompt,
        "rawSystem":      system,
        "options":        options,
        "botName":        None,
        "targetName":     None,
        "zone":           None,
        "playerMsg":      None,
        "isEvent":        False,
        "cleanPrompt":    None,
        "finalSystem":    None,
        "convoId":        None,
        "isShortCircuit": False,
        "memoryContext":  None,
        "webContext":     None,
        "loreContext":    None,
        "transcriptContext": None,
        "response":       None,
    }

def processRequest(prompt, system, options):
    payload = buildPayload(prompt, system, options)

    botRamInstance.processIncoming(payload)

    # Check if message was flagged for discard
    if payload.get("discard"):
        log("MAIN", "Request discarded due to BREAK_ON_STRING filter.")
        return ""

    if payload["isShortCircuit"]:
        log("ROLEPLAYER", "FINAL REPLY (SHORT-CIRCUIT): [Silent/Empty]")
        payload["response"] = ""
        botRamInstance.processOutgoing(payload)
        return payload["response"]

    # v10: Pass botRamInstance so Classifier can search transcript
    runClassifier(payload, botRamInstance)
    
    if payload.get("webContext"):
        payload["finalSystem"] += payload["webContext"]
    if payload.get("loreContext"):
        payload["finalSystem"] += payload["loreContext"]
    if payload.get("transcriptContext"):
        payload["finalSystem"] += payload["transcriptContext"]

    runRoleplayer(payload)
    log("ROLEPLAYER", f"FINAL REPLY: '{payload['response']}'")

    botRamInstance.processOutgoing(payload)
    
    # v10: NO automatic fact extraction from bot replies. 
    # Only "that's a fact" and Web Search populate the Lore DB now.

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
            chatMsg = f"[{matchPlayer.group(1)}] {cleanWowLinks(matchPlayer.group(2).strip())}"
        else:
            matchEvent = re.search(r"Event:\s*(.*?)(?=\.\s+React|\.\s+Avoid|\.\s+You are playing|$)", cleanPrompt, re.DOTALL)
            if matchEvent:
                chatMsg = f"[EVENT] {cleanWowLinks(matchEvent.group(1).strip())}"
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