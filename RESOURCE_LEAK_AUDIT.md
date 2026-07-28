# Resource Leak Audit

## HIGH Priority

### 1. No timeout on edge-tts synthesis — worker thread can hang forever

**File:** `synthesizer.py:33-35`

```python
async def _synthesize_chunk(text: str, voice: str, rate: str, out_path: str):
    communicate = edge_tts.Communicate(text, voice=voice, rate=rate)
    await communicate.save(out_path)
```

`edge_tts.Communicate.save()` opens a WebSocket to Microsoft's TTS service with no timeout. If the service hangs mid-stream, the coroutine blocks forever, permanently consuming a worker thread. With 4 workers and 3 concurrent chunks (semaphore), 2 hung calls = all workers dead.

**Fix:**
```python
SYNTHESIS_TIMEOUT = 120  # seconds per chunk

async def _synthesize_chunk(text: str, voice: str, rate: str, out_path: str):
    communicate = edge_tts.Communicate(text, voice=voice, rate=rate)
    await asyncio.wait_for(communicate.save(out_path), timeout=SYNTHESIS_TIMEOUT)
```

---

### 2. Event loop never closed — file descriptor leak on every restart

**File:** `browser.py:126-139`

`shutdown()` calls `loop.stop()` and drops the reference, but never calls `loop.close()`. Leaks file descriptors, selectors, and callback handles. The `call_soon_threadsafe(loop.stop)` is also outside the try/except, so it can crash if the loop is degraded.

**Fix:**
```python
def shutdown(self):
    if self._loop is None or self._loop.is_closed():
        return
    future = asyncio.run_coroutine_threadsafe(self._shutdown(), self._loop)
    try:
        future.result(timeout=10)
    except Exception:
        logger.exception("Error shutting down browser")
    try:
        self._loop.call_soon_threadsafe(self._loop.stop)
    except RuntimeError:
        pass  # loop already stopped
    if self._thread:
        self._thread.join(timeout=5)
    if self._loop and not self._loop.is_closed():
        self._loop.close()
    self._loop = None
    self._thread = None
```

---

## MEDIUM Priority

### 3. DB session held idle during multi-minute synthesis

**Files:** `worker.py:160,262,347`

In all three `process_*` functions, the first `db` session reads job fields then sits idle (holding a SQLite connection) while `synthesize()` runs for minutes. With 4 workers, up to 8 simultaneous SQLite connections. Wasted resource and contention.

**Fix:** Close `db` immediately after reading job fields. Reopen `db2` only for the save step.

```python
def process_job(job_id: str):
    db = SessionLocal()
    try:
        job = db.query(Job).filter(Job.id == job_id).first()
        if not job:
            return
        url, voice, rate = job.url, job.voice, job.rate
    finally:
        db.close()

    # ... fetch, extract, synthesize using local vars ...

    db = SessionLocal()
    try:
        # ... save chapter and episode ...
    finally:
        db.close()
```

---

### 4. Rate-limit sleep inside global lock — serializes all workers

**File:** `worker.py:43-50`

```python
def _rate_limit_domain(url: str):
    domain = _domain_of(url)
    with _domain_lock:
        last = _domain_last_request[domain]
        wait = DOMAIN_MIN_DELAY - (time.time() - last)
        if wait > 0:
            time.sleep(wait)  # <-- sleeps while holding the lock
        _domain_last_request[domain] = time.time()
```

Sleeps up to 2s while holding `_domain_lock`, blocking ALL workers across ALL domains. A job fetching `domain-a.com` blocks `domain-b.com`.

**Fix:** Release the lock before sleeping:
```python
def _rate_limit_domain(url: str):
    domain = _domain_of(url)
    with _domain_lock:
        last = _domain_last_request[domain]
        wait = DOMAIN_MIN_DELAY - (time.time() - last)
    if wait > 0:
        time.sleep(wait)
    with _domain_lock:
        _domain_last_request[domain] = time.time()
```

---

### 5. Context leak if `page.close()` raises

**File:** `browser.py:101-103`

```python
finally:
    await page.close()
    await context.close()
```

If `page.close()` throws, `context.close()` is never reached, leaking a Playwright `BrowserContext`.

**Fix:**
```python
finally:
    try:
        await page.close()
    finally:
        await context.close()
```

---

### 6. `executor.shutdown(wait=False)` doesn't stop hung threads

**File:** `worker.py:441-442`

```python
def shutdown():
    executor.shutdown(wait=False)
    browser_manager.shutdown()
```

`wait=False` stops accepting new work but doesn't wait for running tasks. Non-daemon threads keep the process alive if synthesis is stuck. The process hangs on exit.

**Fix:**
```python
def shutdown():
    executor.shutdown(wait=True, cancel_futures=True)
    browser_manager.shutdown()
```

---

## LOW Priority

### 7. `_lock` declared but never used — `_ensure_loop()` not thread-safe

**File:** `browser.py:48,51-56`

`_lock` is created but never acquired. `_ensure_loop()` can be called concurrently from worker threads, potentially creating duplicate event loops.

**Fix:**
```python
def _ensure_loop(self):
    with self._lock:
        if self._loop is not None and self._loop.is_running():
            return
        if self._loop is not None and not self._loop.is_closed():
            self._loop.close()
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._loop.run_forever, daemon=True)
        self._thread.start()
```

---

### 8. Dead code — `_save_chapter_record()` never called

**File:** `worker.py:106-155`

`_save_chapter_record()` is defined but never called anywhere in the codebase. Should be removed.

---

### 9. HTML string held in scope during synthesis

**File:** `worker.py:171,262`

Large HTML strings stay referenced in function scope during multi-minute synthesis. Should be dereferenced after extraction.

**Fix:** Set `html = None` after extraction completes.
