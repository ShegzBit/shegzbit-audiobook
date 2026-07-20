"use strict";

// ---------------------------------------------------------------------------
// Persistent player
// ---------------------------------------------------------------------------
const _player = (() => {
  const audio     = document.getElementById('main-audio');
  const bar       = document.getElementById('persistent-player');
  const ppTitle   = document.getElementById('pp-title');
  const ppProg    = document.getElementById('pp-progress');
  const ppPlay    = document.getElementById('pp-play');
  const ppSkipB   = document.getElementById('pp-skip-back');
  const ppSkipF   = document.getElementById('pp-skip-fwd');
  const ppSeek    = document.getElementById('pp-seek');
  const ppSpeed   = document.getElementById('pp-speed');

  let episodeId   = null;
  let novelId     = null;
  let saveTimer   = null;

  function fmt(s) {
    const m = Math.floor(s / 60), sec = Math.floor(s % 60);
    return `${m}:${sec.toString().padStart(2, '0')}`;
  }

  function updateProgress() {
    if (!isFinite(audio.duration)) return;
    ppProg.textContent = `${fmt(audio.currentTime)} / ${fmt(audio.duration)}`;
    ppSeek.value = (audio.currentTime / audio.duration) * 100;
  }

  function savePosition() {
    if (!episodeId) return;
    const body = JSON.stringify({ position_seconds: audio.currentTime });
    try {
      navigator.sendBeacon(`/api/episodes/${episodeId}/position`, new Blob([body], { type: 'application/json' }));
    } catch (_) {
      fetch(`/api/episodes/${episodeId}/position`, { method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body });
    }
  }

  audio.addEventListener('timeupdate', updateProgress);
  audio.addEventListener('play', () => { ppPlay.classList.add('is-playing'); });
  audio.addEventListener('pause', () => { ppPlay.classList.remove('is-playing'); savePosition(); });
  audio.addEventListener('ended', () => {
    ppPlay.classList.remove('is-playing');
    savePosition();
    if (typeof window._onAudioEnded === 'function') window._onAudioEnded();
  });

  ppPlay.addEventListener('click', () => {
    if (audio.paused) audio.play(); else audio.pause();
  });
  ppSkipB.addEventListener('click', () => { audio.currentTime = Math.max(0, audio.currentTime - 10); });
  ppSkipF.addEventListener('click', () => { audio.currentTime = Math.min(audio.duration, audio.currentTime + 10); });
  ppSeek.addEventListener('input', () => {
    audio.currentTime = (ppSeek.value / 100) * audio.duration;
  });
  ppSpeed.addEventListener('change', () => { audio.playbackRate = parseFloat(ppSpeed.value); });

  // Auto-save every 15s
  setInterval(() => { if (!audio.paused) savePosition(); }, 15000);

  function loadEpisode({ episodeId: eid, audioUrl, startPos, title, wordCount, novelId: nid, autoPlay = true }) {
    episodeId = eid;
    novelId   = nid;

    audio.src = audioUrl;
    audio.load();
    audio.currentTime = startPos || 0;
    audio.playbackRate = parseFloat(ppSpeed.value);

    ppTitle.textContent = title || 'Unknown';
    ppProg.textContent  = `${fmt(startPos || 0)} / …`;
    bar.style.display   = 'flex';

    if (autoPlay) {
      audio.play().catch(() => {});
    }
  }

  return { loadEpisode, getNovelId: () => novelId, getEpisodeId: () => episodeId };
})();

window._player = _player;

// ---------------------------------------------------------------------------
// Reader page
// ---------------------------------------------------------------------------
if (typeof PAGE !== 'undefined' && PAGE === 'reader') {
  const urlInput      = document.getElementById('url-input');
  const voiceSelect   = document.getElementById('voice-select');
  const rateRange     = document.getElementById('rate-range');
  const rateLabel     = document.getElementById('rate-label');
  const btnSubmit     = document.getElementById('btn-submit');
  const jobStatus     = document.getElementById('job-status');
  const statusSpinner = document.getElementById('status-spinner');
  const statusLabel   = document.getElementById('status-label');
  const captchaBlock  = document.getElementById('captcha-block');
  const captchaLink   = document.getElementById('captcha-open-link');
  const btnRetry      = document.getElementById('btn-retry');
  const errorBlock    = document.getElementById('error-block');
  const errorMsg      = document.getElementById('error-msg');
  const btnRetryErr   = document.getElementById('btn-retry-error');
  const playerCard    = document.getElementById('player-card');
  const playerTitle   = document.getElementById('player-title');
  const playerWords   = document.getElementById('player-words');
  const playerDur     = document.getElementById('player-dur');
  const playerDl      = document.getElementById('player-download');
  const nextRow       = document.getElementById('next-chapter-row');
  const btnQueueNext  = document.getElementById('btn-queue-next');
  const nextQueuedMsg = document.getElementById('next-queued-msg');
  const progressWrap  = document.getElementById('progress-bar-wrap');
  const progressFill  = document.getElementById('progress-bar-fill');
  const progressPct   = document.getElementById('progress-pct');
  const progressMsg   = document.getElementById('progress-msg');
  const playerSource  = document.getElementById('player-source');

  const toggleAutoQueue     = document.getElementById('toggle-auto-queue');
  const toggleAutoDownload  = document.getElementById('toggle-auto-download');
  const toggleAutoPlay      = document.getElementById('toggle-auto-play');

  // Paste HTML elements
  const pasteHtmlInput  = document.getElementById('paste-html-input');
  const btnSubmitPaste  = document.getElementById('btn-submit-paste');

  function initToggle(toggleEl, key, defaultVal) {
    const stored = localStorage.getItem(key);
    const val = stored !== null ? stored === 'true' : defaultVal;
    toggleEl.checked = val;
    toggleEl.addEventListener('change', () => localStorage.setItem(key, toggleEl.checked));
  }
  initToggle(toggleAutoQueue,    'auto-queue-enabled',    true);
  initToggle(toggleAutoDownload, 'auto-download-enabled', true);
  initToggle(toggleAutoPlay,     'auto-play-enabled',     true);

  let pollingInterval = null;
  let currentJobId    = null;
  let currentJobData  = null;
  let jobDone         = false;

  function saveActiveJob(id) {
    localStorage.setItem('activeJobId', id || currentJobId || '');
  }
  function clearActiveJob() {
    localStorage.removeItem('activeJobId');
  }

  rateRange.addEventListener('input', () => {
    const v = parseInt(rateRange.value);
    rateLabel.textContent = v >= 0 ? `+${v}%` : `${v}%`;
  });

  function rateValue() {
    const v = parseInt(rateRange.value);
    return v >= 0 ? `+${v}%` : `${v}%`;
  }

  function estDuration(words) {
    const mins = Math.round(words / 150);
    return mins < 1 ? '< 1 min' : `~${mins} min`;
  }

  function setSubmitBusy(busy) {
    btnSubmit.disabled = busy;
    btnSubmit.textContent = busy ? 'Working…' : 'Generate Audio';
  }

  function playDing() {
    try {
      const ctx = new (window.AudioContext || window.webkitAudioContext)();
      const osc = ctx.createOscillator();
      const gain = ctx.createGain();
      osc.type = 'sine';
      osc.frequency.value = 880;
      gain.gain.setValueAtTime(0.3, ctx.currentTime);
      gain.gain.exponentialRampToValueAtTime(0.01, ctx.currentTime + 0.3);
      osc.connect(gain);
      gain.connect(ctx.destination);
      osc.start();
      osc.stop(ctx.currentTime + 0.3);
    } catch (_) {}
  }

  const STATUS_LABELS = {
    queued:           'Queued…',
    fetching:         'Fetching page…',
    extracting:       'Extracting chapter text…',
    synthesizing:     'Synthesizing audio (this takes a minute)…',
    done:             'Ready',
    error:            'Error',
    captcha_blocked:  'Verification required',
  };

  function showStatus(status, data) {
    jobStatus.style.display = 'block';
    captchaBlock.style.display = 'none';
    errorBlock.style.display = 'none';

    const inProgress = ['queued', 'fetching', 'extracting', 'synthesizing'].includes(status);
    statusSpinner.style.display = inProgress ? 'block' : 'none';
    statusLabel.textContent = STATUS_LABELS[status] || status;
    statusLabel.style.color = status === 'done' ? 'var(--success)'
                            : status === 'error' ? 'var(--danger)'
                            : status === 'captcha_blocked' ? 'var(--warn)'
                            : 'var(--muted)';

    if (status === 'captcha_blocked') {
      captchaBlock.style.display = 'flex';
      captchaLink.href = data?.url || '#';
    }
    if (status === 'error') {
      errorBlock.style.display = 'flex';
      errorMsg.textContent = data?.error || 'An unexpected error occurred.';
    }

    if (status === 'synthesizing' && data?.progress_pct != null) {
      progressWrap.style.display = 'block';
      progressFill.style.width = data.progress_pct + '%';
      progressPct.textContent = data.progress_pct + '%';
      if (data.progress_msg) {
        progressMsg.textContent = data.progress_msg + ' \u00b7 ' + data.progress_pct + '%';
      } else {
        progressMsg.textContent = data.progress_pct + '%';
      }
    } else {
      progressWrap.style.display = 'none';
    }
  }

  function showPlayer(chapter, episode) {
    playerCard.style.display  = 'block';
    playerTitle.textContent   = chapter.title;
    playerWords.textContent   = `${chapter.word_count.toLocaleString()} words`;
    playerDur.textContent     = estDuration(chapter.word_count);
    playerDl.href             = chapter.audio_url;
    playerDl.download         = chapter.title.replace(/[^a-z0-9 ]/gi, '_') + '.mp3';

    if (chapter.source) {
      playerSource.textContent = chapter.source;
      playerSource.style.display = 'inline';
    } else {
      playerSource.style.display = 'none';
    }

    if (chapter.next_chapter_url) {
      if (toggleAutoQueue.checked) {
        nextRow.style.display = 'none';
        if (chapter.novel_id && episode?.id) {
          autoQueueNext(chapter.novel_id, episode.id);
        }
      } else {
        nextRow.style.display = 'flex';
        nextQueuedMsg.style.display = 'none';
        btnQueueNext.disabled = false;
      }
    } else {
      nextRow.style.display = 'none';
    }

    _player.loadEpisode({
      episodeId:  episode?.id || null,
      audioUrl:   chapter.audio_url,
      startPos:   episode?.listened_position_seconds || 0,
      title:      chapter.title,
      wordCount:  chapter.word_count,
      novelId:    chapter.novel_id,
      autoPlay:   toggleAutoPlay.checked,
    });

    if (!toggleAutoPlay.checked) {
      playDing();
    }

    // Auto-queue next when audio ends
    window._onAudioEnded = () => {
      if (toggleAutoQueue.checked && chapter.novel_id && episode?.id) {
        autoQueueNext(chapter.novel_id, episode.id);
      }
    };
  }

  async function pollJob(jobId) {
    try {
      const resp = await fetch(`/api/jobs/${jobId}`);
      if (!resp.ok) return;
      const data = await resp.json();
      currentJobData = data;

      showStatus(data.status, data);

      if (data.status === 'done') {
        if (jobDone) return;
        jobDone = true;
        clearInterval(pollingInterval);
        setSubmitBusy(false);
        showPlayer(data.chapter, data.episode);
        if (toggleAutoDownload.checked && data.chapter?.audio_url) {
          const a = document.createElement('a');
          a.href = data.chapter.audio_url;
          a.download = data.chapter.title?.replace(/[^a-z0-9 ]/gi, '_') + '.mp3' || 'audio.mp3';
          document.body.appendChild(a);
          a.click();
          a.remove();
        }
      } else if (['error', 'captcha_blocked'].includes(data.status)) {
        clearInterval(pollingInterval);
        setSubmitBusy(false);
      }
    } catch (e) {
      console.error('Poll error:', e);
    }
  }

  async function submitChapter() {
    const url = urlInput.value.trim();
    if (!url) { urlInput.focus(); return; }

    jobDone = false;
    clearActiveJob();
    setSubmitBusy(true);
    playerCard.style.display = 'none';
    jobStatus.style.display  = 'none';
    clearInterval(pollingInterval);

    const resp = await fetch('/api/chapters', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ url, voice: voiceSelect.value, rate: rateValue() }),
    });

    if (!resp.ok) {
      const d = await resp.json();
      showStatus('error', { error: d.detail || 'Submission failed.' });
      setSubmitBusy(false);
      return;
    }

    const { job_id } = await resp.json();
    currentJobId = job_id;
    saveActiveJob(job_id);
    showStatus('queued', {});
    pollingInterval = setInterval(() => pollJob(job_id), 1500);
  }

  async function retryJob(jobId) {
    if (!jobId) return;
    const resp = await fetch(`/api/jobs/${jobId}/retry`, { method: 'POST' });
    if (!resp.ok) return;
    const { job_id } = await resp.json();
    currentJobId = job_id;
    jobDone = false;
    saveActiveJob(job_id);
    setSubmitBusy(true);
    showStatus('queued', {});
    clearInterval(pollingInterval);
    pollingInterval = setInterval(() => pollJob(job_id), 1500);
  }

  async function autoQueueNext(novelId, episodeId) {
    try {
      const resp = await fetch(`/api/novels/${novelId}/queue-next`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ current_episode_id: episodeId }),
      });
      if (!resp.ok) return;
      const data = await resp.json();
      if (data.job_id) {
        currentJobId = data.job_id;
        jobDone = false;
        saveActiveJob(data.job_id);
        urlInput.value = data.next_url || '';
        setSubmitBusy(true);
        showStatus('queued', {});
        clearInterval(pollingInterval);
        pollingInterval = setInterval(() => pollJob(data.job_id), 1500);
      }
    } catch (e) { console.error('Auto-queue error:', e); }
  }

  async function restoreActiveJob() {
    const savedId = localStorage.getItem('activeJobId');
    if (!savedId) return;
    try {
      const resp = await fetch(`/api/jobs/${savedId}`);
      if (!resp.ok) { clearActiveJob(); return; }
      const data = await resp.json();
      currentJobId = savedId;
      currentJobData = data;

      if (data.status === 'done') {
        jobDone = true;
        setSubmitBusy(false);
        showStatus('done', data);
        showPlayer(data.chapter, data.episode);
        return;
      }

      if (['error', 'captcha_blocked'].includes(data.status)) {
        setSubmitBusy(false);
        showStatus(data.status, data);
        return;
      }

      showStatus(data.status, data);
      saveActiveJob(savedId);
      pollingInterval = setInterval(() => pollJob(savedId), 1500);
    } catch (_) { clearActiveJob(); }
  }

  btnSubmit.addEventListener('click', submitChapter);
  urlInput.addEventListener('keydown', (e) => { if (e.key === 'Enter') submitChapter(); });
  restoreActiveJob();

  btnRetry.addEventListener('click', () => retryJob(currentJobId));
  btnRetryErr.addEventListener('click', () => retryJob(currentJobId));

  // Paste HTML functionality
  pasteHtmlInput.addEventListener('input', () => {
    btnSubmitPaste.disabled = !pasteHtmlInput.value.trim();
  });

  btnSubmitPaste.addEventListener('click', async () => {
    const html = pasteHtmlInput.value.trim();
    if (!html || !currentJobData?.url) return;

    btnSubmitPaste.disabled = true;
    btnSubmitPaste.textContent = 'Submitting…';
    jobDone = false;

    const resp = await fetch('/api/chapters/manual', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ 
        url: currentJobData.url, 
        html, 
        voice: voiceSelect.value, 
        rate: rateValue() 
      }),
    });

    if (!resp.ok) {
      const d = await resp.json();
      showStatus('error', { error: d.detail || 'Submission failed.' });
      btnSubmitPaste.disabled = false;
      btnSubmitPaste.textContent = 'Submit HTML';
      return;
    }

    const { job_id } = await resp.json();
    currentJobId = job_id;
    saveActiveJob(job_id);
    showStatus('queued', {});
    pasteHtmlInput.value = '';
    btnSubmitPaste.textContent = 'Submit HTML';
    clearInterval(pollingInterval);
    pollingInterval = setInterval(() => pollJob(job_id), 1500);
  });

  btnQueueNext.addEventListener('click', async () => {
    if (!currentJobData?.chapter) return;
    btnQueueNext.disabled = true;
    nextQueuedMsg.style.display = 'inline';
    const ch = currentJobData.chapter;
    const ep = currentJobData.episode;
    if (ch.novel_id && ep?.id) {
      await autoQueueNext(ch.novel_id, ep.id);
    } else {
      // No episode yet — submit raw next URL
      urlInput.value = ch.next_chapter_url;
      await submitChapter();
    }
  });

  // -----------------------------------------------------------------------
  // Extension integration
  // -----------------------------------------------------------------------
  const EXT_ID_KEY        = 'audiobook-juice-ext-id';
  const DELAY_KEY         = 'ext-auto-queue-delay';
  const CAP_KEY           = 'ext-session-cap';

  const extConfigToggle   = document.getElementById('ext-config-toggle');
  const extConfigPanel    = document.getElementById('ext-config');
  const extIdInput        = document.getElementById('ext-id-input');
  const delayInput        = document.getElementById('delay-input');
  const capInput          = document.getElementById('cap-input');
  const btnScrape         = document.getElementById('btn-scrape');
  const extDot            = document.getElementById('ext-dot');
  const extStatusText     = document.getElementById('ext-status-text');
  const sessionCounter    = document.getElementById('session-counter');

  // Load saved config
  extIdInput.value = localStorage.getItem(EXT_ID_KEY) || '';
  const savedDelay = localStorage.getItem(DELAY_KEY);
  if (savedDelay) delayInput.value = savedDelay;
  const savedCap = localStorage.getItem(CAP_KEY);
  if (savedCap) capInput.value = savedCap;

  extIdInput.addEventListener('change', () => localStorage.setItem(EXT_ID_KEY, extIdInput.value.trim()));
  delayInput.addEventListener('change', () => localStorage.setItem(DELAY_KEY, delayInput.value));
  capInput.addEventListener('change', () => localStorage.setItem(CAP_KEY, capInput.value));

  extConfigToggle.addEventListener('click', () => {
    const visible = extConfigPanel.style.display !== 'none';
    extConfigPanel.style.display = visible ? 'none' : 'flex';
  });

  let sessionChapterCount = 0;

  function jitteredDelayMs(baseMinutes) {
    const baseMs = baseMinutes * 60 * 1000;
    const jitterFactor = 0.7 + Math.random() * 0.6;
    return Math.round(baseMs * jitterFactor);
  }

  function sleep(ms) {
    return new Promise(resolve => setTimeout(resolve, ms));
  }

  function getBaseDelay() {
    return Math.max(0.5, parseFloat(delayInput.value) || 2);
  }

  function getSessionCap() {
    return Math.max(1, parseInt(capInput.value, 10) || 20);
  }

  // Extension availability check
  function checkExtension() {
    const isChrome = typeof chrome !== 'undefined' && chrome.runtime && typeof chrome.runtime.connect === 'function';
    if (isChrome) {
      extDot.className = 'ext-dot ext-dot-on';
      extStatusText.textContent = extIdInput.value.trim()
        ? 'Extension ready' : 'Enter extension ID \u2699';
    } else {
      extDot.className = 'ext-dot ext-dot-off';
      extStatusText.textContent = 'Use Chrome for extension support';
    }
  }
  checkExtension();

  function setExtStatus(state, msg) {
    extStatusText.textContent = msg;
    if (state === 'ok')       extDot.className = 'ext-dot ext-dot-on';
    else if (state === 'off') extDot.className = 'ext-dot ext-dot-off';
    else if (state === 'busy') extDot.className = 'ext-dot ext-dot-scraping';
  }

  function updateSessionCounter(extra) {
    const cap = getSessionCap();
    let text = `${sessionChapterCount} / ${cap} chapters`;
    if (extra) text += ' — ' + extra;
    sessionCounter.textContent = text;
    sessionCounter.style.display = 'block';
  }

  function scrapeUrl(url, { closeAfterScrape = true } = {}) {
    return new Promise((resolve, reject) => {
      if (typeof chrome === 'undefined' || !chrome.runtime || !chrome.runtime.connect) {
        reject(new Error('Browser extension not detected. Install the Audiobook Juice extension and reload this page.'));
        return;
      }
      const extId = extIdInput.value.trim();
      if (!extId) {
        reject(new Error('Extension ID not set. Click ⚙ and enter the 32-character ID from chrome://extensions.'));
        return;
      }
      let port;
      try {
        port = chrome.runtime.connect(extId);
      } catch (e) {
        reject(new Error('Failed to connect to extension: ' + e.message));
        return;
      }
      const timeout = setTimeout(() => {
        try { port.disconnect(); } catch (_) {}
        reject(new Error('Extension timed out after 30s. Is the extension enabled and on a chapter page?'));
      }, 30000);

      port.onMessage.addListener((msg) => {
        if (msg.type === 'SCRAPE_RESULT') {
          clearTimeout(timeout);
          try { port.disconnect(); } catch (_) {}
          resolve(msg);
        } else if (msg.type === 'SCRAPE_ERROR') {
          clearTimeout(timeout);
          try { port.disconnect(); } catch (_) {}
          reject(new Error(msg.error || 'Extension scrape failed.'));
        }
      });

      port.onDisconnect.addListener(() => {
        clearTimeout(timeout);
        const err = chrome.runtime?.lastError;
        if (err) reject(new Error('Extension disconnected: ' + err.message));
      });

      port.postMessage({ type: 'SCRAPE_URL', url, closeAfterScrape });
    });
  }

  function pollUntilDone(jobId) {
    return new Promise((resolve, reject) => {
      const interval = setInterval(async () => {
        try {
          const resp = await fetch(`/api/jobs/${jobId}`);
          if (!resp.ok) return;
          const data = await resp.json();
          showStatus(data.status, data);
          if (data.status === 'done') {
            clearInterval(interval);
            resolve(data);
          } else if (data.status === 'error') {
            clearInterval(interval);
            reject(new Error(data.error || 'Job failed.'));
          }
        } catch (_) {}
      }, 2000);
    });
  }

  function showExtensionPlayer(chapter, episode) {
    playerCard.style.display  = 'block';
    playerTitle.textContent   = chapter.title;
    playerWords.textContent   = `${chapter.word_count.toLocaleString()} words`;
    playerDur.textContent     = estDuration(chapter.word_count);
    playerDl.href             = chapter.audio_url;
    playerDl.download         = chapter.title.replace(/[^a-z0-9 ]/gi, '_') + '.mp3';
    nextRow.style.display     = 'none';

    if (chapter.source) {
      playerSource.textContent = chapter.source;
      playerSource.style.display = 'inline';
    } else {
      playerSource.style.display = 'none';
    }

    _player.loadEpisode({
      episodeId:  episode?.id || null,
      audioUrl:   chapter.audio_url,
      startPos:   episode?.listened_position_seconds || 0,
      title:      chapter.title,
      wordCount:  chapter.word_count,
      novelId:    chapter.novel_id,
      autoPlay:   toggleAutoPlay.checked,
    });

    if (!toggleAutoPlay.checked) playDing();
  }

  function triggerDownload(chapter) {
    const a = document.createElement('a');
    a.href = chapter.audio_url;
    a.download = (chapter.title || 'chapter').replace(/[^a-z0-9 ]/gi, '_') + '.mp3';
    document.body.appendChild(a);
    a.click();
    a.remove();
  }

  async function processChapter(url, autoQueue) {
    sessionChapterCount++;
    const cap = getSessionCap();
    updateSessionCounter('Scraping…');
    setExtStatus('busy', 'Scraping…');
    btnScrape.disabled = true;
    jobDone = false;
    playerCard.style.display = 'none';
    jobStatus.style.display  = 'none';
    clearInterval(pollingInterval);

    // 1 — Scrape via extension
    let result;
    try {
      result = await scrapeUrl(url, { closeAfterScrape: true });
    } catch (e) {
      setExtStatus('off', e.message);
      updateSessionCounter('Error');
      btnScrape.disabled = false;
      showStatus('error', { error: e.message });
      return;
    }

    // 2 — POST to /api/jobs
    updateSessionCounter('Submitting…');
    setExtStatus('busy', 'Extracting…');
    let jobData;
    try {
      const resp = await fetch('/api/jobs', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ url: result.url, html: result.html, title: result.title }),
      });
      if (!resp.ok) {
        const err = await resp.json();
        const msg = err.detail || 'Submission failed.';
        setExtStatus('off', msg);
        updateSessionCounter('Error');
        btnScrape.disabled = false;
        showStatus('error', { error: msg });
        return;
      }
      jobData = await resp.json();
    } catch (e) {
      setExtStatus('off', 'Network error');
      updateSessionCounter('Error');
      btnScrape.disabled = false;
      showStatus('error', { error: 'Network error submitting job.' });
      return;
    }

    currentJobId = jobData.job_id;
    saveActiveJob(jobData.job_id);
    showStatus('queued', {});

    // 3 — Poll until done
    updateSessionCounter('Synthesizing…');
    setExtStatus('busy', 'Synthesizing…');
    let pollData;
    try {
      pollData = await pollUntilDone(jobData.job_id);
    } catch (e) {
      setExtStatus('off', e.message);
      updateSessionCounter('Error');
      btnScrape.disabled = false;
      showStatus('error', { error: e.message });
      return;
    }

    jobDone = true;
    setExtStatus('ok', 'Done');
    updateSessionCounter('Done');
    btnScrape.disabled = false;
    showExtensionPlayer(pollData.chapter, pollData.episode);

    if (toggleAutoDownload.checked && pollData.chapter?.audio_url) {
      triggerDownload(pollData.chapter);
    }

    // 4 — Auto-queue next chapter
    const nextUrl = jobData.next_chapter_url || pollData.chapter?.next_chapter_url;
    console.log('[ext] auto-queue check:', { autoQueue, nextUrl, sessionChapterCount, cap });
    if (autoQueue && nextUrl) {
      if (sessionChapterCount >= cap) {
        setExtStatus('ok', `Cap reached (${cap} chapters)`);
        updateSessionCounter('Cap reached — done');
        return;
      }
      const delayMs = jitteredDelayMs(getBaseDelay());
      const mins = Math.round(delayMs / 60000);
      updateSessionCounter(`Waiting ~${mins}m for next chapter…`);
      setExtStatus('busy', `Next chapter in ~${mins}m`);
      await sleep(delayMs);
      processChapter(nextUrl, true);
    }
  }

  btnScrape.addEventListener('click', async () => {
    const url = urlInput.value.trim();
    if (!url) { urlInput.focus(); return; }
    sessionChapterCount = 0;
    await processChapter(url, toggleAutoQueue.checked);
  });
}
