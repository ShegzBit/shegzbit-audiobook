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
  audio.addEventListener('play', () => { ppPlay.textContent = '⏸'; });
  audio.addEventListener('pause', () => { ppPlay.textContent = '▶'; savePosition(); });
  audio.addEventListener('ended', () => {
    ppPlay.textContent = '▶';
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

  function loadEpisode({ episodeId: eid, audioUrl, startPos, title, wordCount, novelId: nid }) {
    episodeId = eid;
    novelId   = nid;

    audio.src = audioUrl;
    audio.load();
    audio.currentTime = startPos || 0;
    audio.playbackRate = parseFloat(ppSpeed.value);

    ppTitle.textContent = title || 'Unknown';
    ppProg.textContent  = `${fmt(startPos || 0)} / …`;
    bar.style.display   = 'flex';

    audio.play().catch(() => {});
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

  let pollingInterval = null;
  let currentJobId    = null;
  let currentJobData  = null;

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

  const STATUS_LABELS = {
    queued:           'Queued…',
    fetching:         'Fetching page…',
    extracting:       'Extracting chapter text…',
    synthesizing:     'Synthesizing audio (this takes a minute)…',
    done:             '✓ Ready!',
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
  }

  function showPlayer(chapter, episode) {
    playerCard.style.display  = 'block';
    playerTitle.textContent   = chapter.title;
    playerWords.textContent   = `${chapter.word_count.toLocaleString()} words`;
    playerDur.textContent     = estDuration(chapter.word_count);
    playerDl.href             = chapter.audio_url;
    playerDl.download         = chapter.title.replace(/[^a-z0-9 ]/gi, '_') + '.mp3';

    if (chapter.next_chapter_url) {
      nextRow.style.display = 'flex';
      nextQueuedMsg.style.display = 'none';
      btnQueueNext.disabled = false;
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
    });

    // Auto-queue next when audio ends
    window._onAudioEnded = () => {
      if (chapter.novel_id && episode?.id) {
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
        clearInterval(pollingInterval);
        setSubmitBusy(false);
        showPlayer(data.chapter, data.episode);
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
    showStatus('queued', {});
    pollingInterval = setInterval(() => pollJob(job_id), 1500);
  }

  async function retryJob(jobId) {
    if (!jobId) return;
    const resp = await fetch(`/api/jobs/${jobId}/retry`, { method: 'POST' });
    if (!resp.ok) return;
    const { job_id } = await resp.json();
    currentJobId = job_id;
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
        urlInput.value = data.next_url || '';
        setSubmitBusy(true);
        showStatus('queued', {});
        clearInterval(pollingInterval);
        pollingInterval = setInterval(() => pollJob(data.job_id), 1500);
      }
    } catch (e) { console.error('Auto-queue error:', e); }
  }

  btnSubmit.addEventListener('click', submitChapter);
  urlInput.addEventListener('keydown', (e) => { if (e.key === 'Enter') submitChapter(); });

  btnRetry.addEventListener('click', () => retryJob(currentJobId));
  btnRetryErr.addEventListener('click', () => retryJob(currentJobId));

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
}
