from config import ROLEPLAYER_CONFIG
from logging_utils import logPayload
from engine import callLlm
from french_maid import cleanFinalResponse

def runRoleplayer(payload):
    prompt = payload["cleanPrompt"]
    systemPrompt = payload["finalSystem"]
    options = payload["options"]

    logPayload("ROLEPLAYER_SYS_PROMPT", systemPrompt)
    raw = callLlm(ROLEPLAYER_CONFIG, systemPrompt, prompt, options)
    logPayload("ROLEPLAYER_RAW", raw)
    payload["response"] = cleanFinalResponse(raw)