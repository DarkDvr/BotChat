import time
import threading
import requests
import re
from config import (
    LLM_URL, LLM_TIMEOUT, ONLINE_API_TIMEOUT, 
    ONLINE_API_REQUESTS_PER_SECOND, CACHE_TTL,
    LOCAL_LLM_STAGGER_DELAY, DROP_DELAYED_RESPONSES, MAX_QUEUE_WAIT_SECONDS
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
_local_llm_lock = threading.Lock()

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

def callLLM(model, systemPrompt, userPrompt, options):
    thinkMode = options.get("think", False)
    
    messages = []
    if systemPrompt:
        messages.append({"role": "system", "content": systemPrompt})
    messages.append({"role": "user", "content": userPrompt})

    payload = {
        "model": model,
        "messages": messages,
        "stream": False,
        "temperature": options.get("temperature", 0.7),
        "max_tokens": options.get("num_predict", 256),
    }
    
    url = LLM_URL.rstrip("/")
    if not url.endswith("/chat/completions"):
        url += "/chat/completions"

    enqueue_time = time.time()

    # Enforce sequential local requests
    with _local_llm_lock:
        wait_time = time.time() - enqueue_time
        
        # Explicit drop check before we waste GPU cycles
        if DROP_DELAYED_RESPONSES and wait_time > MAX_QUEUE_WAIT_SECONDS:
            log("ENGINE", f"DROPPED: Request waited {wait_time:.1f}s in queue (DROP_DELAYED_RESPONSES=True).")
            return ""

        if LOCAL_LLM_STAGGER_DELAY > 0:
            time.sleep(LOCAL_LLM_STAGGER_DELAY)
        
        try:
            resp = requests.post(url, json=payload, timeout=max(LLM_TIMEOUT, 120))
            resp.raise_for_status()
            
            response_data = resp.json()
            response = response_data.get("choices", [{}])[0].get("message", {}).get("content", "")
            
            if thinkMode:
                response = stripThinkTags(response)
            return response
            
        except requests.exceptions.HTTPError as e:
            log("LLM", f"LLM HTTP error ({model}): {e}")
            if e.response is not None:
                log("LLM", f"API error details: {e.response.text}")
            return ""
        except Exception as e:
            log("LLM", f"LLM connection error ({model}): {e}")
            return ""

def callLlm(engine, systemPrompt, userPrompt, options):
    if engine["apiUrl"] and engine["apiKey"]:
        return callOnlineApi(engine["apiUrl"], engine["apiKey"], engine["model"], systemPrompt, userPrompt, options)
    return callLLM(engine["model"], systemPrompt, userPrompt, options)