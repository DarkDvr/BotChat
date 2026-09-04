import time
import threading
import requests
import re
from config import (
    OLLAMA_URL, OLLAMA_TIMEOUT, ONLINE_API_TIMEOUT, 
    ONLINE_API_REQUESTS_PER_SECOND, CACHE_TTL
)
from logging_utils import log

class RateLimiter:
    def __init__(self, rps):
        self.minInterval = (1.0 / rps) if rps > 0 else 0
        self.lastRequest = 0.0
        self.lock = threading.Lock()
        
    def wait(self):
        if self.minInterval <= 0: return
        with self.lock:
            now = time.time()
            waitTime = self.minInterval - (now - self.lastRequest)
            if waitTime > 0:
                log("RATE", f"Waiting {waitTime:.1f}s to respect API rate limit...")
                time.sleep(waitTime)
            self.lastRequest = time.time()

rateLimiter = RateLimiter(ONLINE_API_REQUESTS_PER_SECOND)

_cache = {}
_cacheLock = threading.Lock()
_queryLocks = {}
_queryLocksLock = threading.Lock()

def cacheGet(key):
    with _cacheLock:
        if key in _cache:
            value, ts = _cache[key]
            if time.time() - ts < CACHE_TTL: return value
            del _cache[key]
    return None

def cacheSet(key, value):
    with _cacheLock: _cache[key] = (value, time.time())

def getQueryLock(query):
    with _queryLocksLock:
        if query not in _queryLocks: _queryLocks[query] = threading.Lock()
        return _queryLocks[query]

def callOnlineApi(apiUrl, apiKey, model, systemPrompt, userPrompt, options):
    rateLimiter.wait()
    url = apiUrl.rstrip("/")
    if not url.endswith("/chat/completions"): url += "/chat/completions"
    headers = {"Authorization": f"Bearer {apiKey}", "Content-Type": "application/json"}
    payload = {
        "model": model,
        "messages": [{"role": "system", "content": systemPrompt}, {"role": "user", "content": userPrompt}],
        "temperature": options.get("temperature", 0.7),
        "max_tokens": options.get("num_predict", 256),
        "stream": False,
    }
    for attempt in range(3):
        try:
            resp = requests.post(url, headers=headers, json=payload, timeout=ONLINE_API_TIMEOUT)
            if resp.status_code in (500, 502, 503, 504, 429) and attempt < 2:
                log("API", f"Online API error ({model}): {resp.status_code}. Retrying...")
                time.sleep(2)
                continue
            resp.raise_for_status()
            return resp.json()["choices"][0]["message"]["content"]
        except (requests.exceptions.SSLError, requests.exceptions.ConnectionError, requests.exceptions.Timeout) as e:
            if attempt < 2:
                log("API", f"Network/SSL drop ({model}). Retrying ({attempt+1}/3)...")
                time.sleep(3)
                continue
            log("API", f"Network/SSL fatal ({model}): {e}")
            return ""
        except Exception as e:
            log("API", f"Online API error ({model}): {e}")
            return ""

def stripThinkTags(text):
    """Removes  blocks from model output."""
    if not text:
        return text
    return re.sub(r'', '', text, flags=re.DOTALL).strip()

def callOllama(model, systemPrompt, userPrompt, options):
    thinkMode = options.get("think", False)
    payload = {
        "model": model,
        "prompt": userPrompt,
        "system": systemPrompt,
        "stream": False,
        "think": thinkMode,
        "options": {
            "temperature": options.get("temperature", 0.7),
            "num_predict": options.get("num_predict", 256)
        },
    }
    try:
        resp = requests.post(OLLAMA_URL, json=payload, timeout=OLLAMA_TIMEOUT)
        resp.raise_for_status()
        response = resp.json().get("response", "")
        if thinkMode:
            response = stripThinkTags(response)
        return response
    except Exception as e:
        log("OLLAMA", f"Ollama error ({model}): {e}")
        return ""

def callLlm(engine, systemPrompt, userPrompt, options):
    if engine["apiUrl"] and engine["apiKey"]:
        return callOnlineApi(engine["apiUrl"], engine["apiKey"], engine["model"], systemPrompt, userPrompt, options)
    return callOllama(engine["model"], systemPrompt, userPrompt, options)