from config import ROLEPLAYER_CONFIG
from logging_utils import log, logPayload
from engine import callLlm
from french_maid import cleanFinalResponse

def runRoleplayer(payload):
    prompt = payload["cleanPrompt"]
    systemPrompt = payload["finalSystem"]
    options = payload["options"]

    # Log ONLY the dynamic injected contexts, ignoring the static system prompt rules
    injectedContexts = []
    if payload.get("memoryContext"):
        injectedContexts.append(f"[MEMORY]: {payload['memoryContext'].strip()}")
    if payload.get("loreContext"):
        injectedContexts.append(f"[LORE]: {payload['loreContext'].strip()}")
    if payload.get("webContext"):
        injectedContexts.append(f"[WEB]: {payload['webContext'].strip()}")
        
    if injectedContexts:
        contextSummary = "\n".join(injectedContexts)
        logPayload("ROLEPLAYER_CONTEXT", contextSummary)
    else:
        log("ROLEPLAYER", "No dynamic context injected (using base system prompt).")

    raw = callLlm(ROLEPLAYER_CONFIG, systemPrompt, prompt, options)
    logPayload("ROLEPLAYER_RAW", raw)
    payload["response"] = cleanFinalResponse(raw)