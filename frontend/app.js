// phantom-ui-navigator/frontend/app.js
// PHANTOM UI Navigator — Frontend Logic
// WebSocket, bounding box overlay, voice input, screenshot polling

// ── Config ──────────────────────────────────────────────────
const API_BASE = window.location.origin;
const WS_PROTOCOL = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
const WS_URL = `${WS_PROTOCOL}//${window.location.host}/ws`;

// ── State ───────────────────────────────────────────────────
let ws = null;
let isSessionActive = false;
let isRecording = false;
let recognition = null;
let screenshotInterval = null;
let currentElements = [];

// ── DOM References ──────────────────────────────────────────
const urlInput = document.getElementById('urlInput');
const startBtn = document.getElementById('startBtn');
const stopBtn = document.getElementById('stopBtn');
const statusBadge = document.getElementById('statusBadge');
const statusText = document.getElementById('statusText');
const screenshotImg = document.getElementById('screenshotImg');
const placeholder = document.getElementById('placeholder');
const overlayContainer = document.getElementById('overlayContainer');
const activityLog = document.getElementById('activityLog');
const elementsList = document.getElementById('elementsList');
const commandInput = document.getElementById('commandInput');
const micBtn = document.getElementById('micBtn');
const loadingOverlay = document.getElementById('loadingOverlay');
const loadingText = document.getElementById('loadingText');
const scanLine = document.getElementById('scanLine');
const elementCountBadge = document.getElementById('elementCount');

// ── Session Management ──────────────────────────────────────

async function startSession() {
    const url = urlInput.value.trim() || 'https://www.google.com';
    urlInput.value = url;

    addLog('🚀', `Starting session — navigating to ${url}...`, 'narration');
    startBtn.disabled = true;
    startBtn.textContent = '⏳ STARTING...';

    // Show loading overlay with animated progress
    placeholder.style.display = 'none';
    loadingOverlay.style.display = 'flex';
    loadingText.textContent = 'Launching Phantom...';
    // Reset progress bar animation
    const progressBar = document.getElementById('loadingProgressBar');
    if (progressBar) {
        progressBar.style.animation = 'none';
        progressBar.offsetHeight; // Force reflow
        progressBar.style.animation = 'progress-sweep 12s ease-in-out forwards';
    }
    setStatus('offline', 'STARTING...');

    // Cycle substatus messages for visual feedback
    const substatus = document.getElementById('loadingSubstatus');
    const phases = [
        'Initializing headless Chromium',
        'Configuring viewport & network',
        'Navigating to target URL',
        'Waiting for page to render',
        'Scanning UI elements with Gemini Vision',
        'Building visual element map',
    ];
    let phaseIdx = 0;
    const phaseTimer = setInterval(() => {
        if (substatus && phaseIdx < phases.length) {
            substatus.textContent = phases[phaseIdx++];
            loadingText.textContent = phaseIdx <= 2 ? 'Launching Phantom...' :
                phaseIdx <= 4 ? 'Loading page...' : 'Analyzing UI...';
        }
    }, 2500);

    try {
        const res = await fetch(`${API_BASE}/api/session/start`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                url,
                headless: true,
                accessibility_mode: document.getElementById('accessibilityToggle')?.checked || false
            }),
        });

        if (!res.ok) {
            const err = await res.json();
            throw new Error(err.detail || 'Failed to start session');
        }

        const data = await res.json();
        isSessionActive = true;

        // Stop phase cycling and hide loading
        clearInterval(phaseTimer);
        loadingOverlay.style.display = 'none';
        scanLine.classList.add('active');

        // Update UI
        setStatus('online', 'LIVE');
        startBtn.style.display = 'none';
        stopBtn.style.display = 'flex';

        addLog('✅', `Session started — ${data.elements_found} elements detected`, 'success');
        addLog('👁️', `Page context: ${data.page_context}`, 'narration');

        // Update elements
        if (data.ui_state && data.ui_state.elements) {
            updateElements(data.ui_state.elements);
        }

        // Connect WebSocket
        connectWebSocket();

        // Start screenshot polling
        startScreenshotPolling();

    } catch (err) {
        clearInterval(phaseTimer);
        addLog('❌', `Error: ${err.message}`, 'error');
        loadingOverlay.style.display = 'none';
        placeholder.style.display = 'flex';
        startBtn.disabled = false;
        startBtn.textContent = '▶ START';
    }
}

async function stopSession() {
    try {
        await fetch(`${API_BASE}/api/session/stop`, { method: 'POST' });
    } catch (e) { /* ignore */ }

    isSessionActive = false;
    setStatus('offline', 'OFFLINE');
    startBtn.style.display = 'flex';
    startBtn.disabled = false;
    startBtn.textContent = '▶ START';
    stopBtn.style.display = 'none';

    // Clear screenshot
    screenshotImg.style.display = 'none';
    placeholder.style.display = 'flex';
    overlayContainer.innerHTML = '';
    scanLine.classList.remove('active');
    loadingOverlay.style.display = 'none';
    elementCountBadge.style.display = 'none';

    // Stop polling
    if (screenshotInterval) {
        clearInterval(screenshotInterval);
        screenshotInterval = null;
    }

    // Close WebSocket
    if (ws) {
        ws.close();
        ws = null;
    }

    addLog('⏹️', 'Session stopped', 'narration');
}

// ── WebSocket ───────────────────────────────────────────────

function connectWebSocket() {
    ws = new WebSocket(WS_URL);

    ws.onopen = () => {
        console.log('🔌 WebSocket connected');
    };

    ws.onmessage = (event) => {
        const msg = JSON.parse(event.data);
        handleWsMessage(msg);
    };

    ws.onclose = () => {
        console.log('🔌 WebSocket disconnected');
        // Reconnect after 3s if session still active
        if (isSessionActive) {
            setTimeout(connectWebSocket, 3000);
        }
    };

    ws.onerror = (err) => {
        console.error('WebSocket error:', err);
    };
}

function handleWsMessage(msg) {
    switch (msg.type) {
        case 'narration':
            addLog('🎙️', msg.text, 'narration');
            // Play high-quality GCP TTS audio if provided, fallback to standard TTS
            if (msg.audio) {
                try {
                    const audio = new Audio("data:audio/mp3;base64," + msg.audio);
                    audio.play();
                } catch (e) {
                    console.error("Failed to play audio:", e);
                    speak(msg.text);
                }
            } else {
                speak(msg.text);
            }
            break;

        case 'plan_generated':
            addLog('🧠', `Plan: ${msg.steps.length} steps for "${msg.intent}"`, 'action');
            msg.steps.forEach((s, i) => {
                addLog('📋', `${i + 1}. [${s.action}] ${s.target}`, 'narration');
            });
            break;

        case 'execution_complete':
            const emoji = msg.success ? '✅' : '⚠️';
            addLog(emoji, `Done — ${msg.steps_completed} steps completed`, msg.success ? 'success' : 'error');
            // Refresh screenshot
            refreshScreenshot();
            break;

        case 'action_click':
            // Show ripple at click coordinates
            if (msg.x !== undefined && msg.y !== undefined) {
                showClickRipple(msg.x, msg.y);
            }
            break;

        case 'screenshot':
            displayScreenshot(msg.image);
            break;

        case 'error':
            addLog('❌', msg.message, 'error');
            break;

        case 'session_started':
            if (msg.ui_state && msg.ui_state.elements) {
                updateElements(msg.ui_state.elements);
            }
            break;

        case 'paused':
            addLog('⏸️', 'Execution paused', 'narration');
            break;

        case 'resumed':
            addLog('▶️', 'Execution resumed', 'narration');
            break;
    }
}

// ── Commands ────────────────────────────────────────────────

async function sendCommand() {
    const intent = commandInput.value.trim();
    if (!intent) return;
    if (!isSessionActive) {
        addLog('⚠️', 'Start a session first!', 'error');
        return;
    }

    commandInput.value = '';
    addLog('💬', `You: "${intent}"`, 'narration');

    // Send via WebSocket for real-time updates
    if (ws && ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify({ type: 'command', intent }));
    } else {
        // Fallback to REST
        try {
            const res = await fetch(`${API_BASE}/api/command`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ intent, auto_execute: true }),
            });
            const data = await res.json();

            if (data.plan) {
                addLog('🧠', `Plan: ${data.plan.total_steps} steps`, 'action');
            }
            if (data.execution) {
                addLog('✅', `Completed: ${data.execution.steps_succeeded}/${data.execution.steps_total}`, 'success');
            }

            refreshScreenshot();
        } catch (err) {
            addLog('❌', `Error: ${err.message}`, 'error');
        }
    }
}

// ── Screenshot ──────────────────────────────────────────────

function startScreenshotPolling() {
    // Initial screenshot
    refreshScreenshot();
    // Poll every 5 seconds (screenshot only — no Gemini API call)
    screenshotInterval = setInterval(refreshScreenshot, 5000);
}

async function refreshScreenshot() {
    if (!isSessionActive) return;

    try {
        const res = await fetch(`${API_BASE}/api/screenshot`);
        if (!res.ok) return;
        const data = await res.json();
        displayScreenshot(data.image);
        // NOTE: Do NOT call /api/state here — it triggers a full Gemini Vision
        // analysis every poll cycle, causing 429 RESOURCE_EXHAUSTED errors.
        // Elements are updated by command execution flow instead.
    } catch (e) {
        // Silently fail — polling will retry
    }
}

function displayScreenshot(dataUrl) {
    screenshotImg.src = dataUrl;
    screenshotImg.style.display = 'block';
    placeholder.style.display = 'none';
}

// ── Bounding Box Overlay ────────────────────────────────────

function updateElements(elements) {
    currentElements = elements;
    renderOverlay(elements);
    renderElementsList(elements);

    // Update element count badge
    elementCountBadge.textContent = elements.length;
    elementCountBadge.style.display = elements.length > 0 ? 'inline-flex' : 'none';
}

function renderOverlay(elements) {
    overlayContainer.innerHTML = '';

    const img = screenshotImg;
    if (!img.naturalWidth) return;

    // Calculate the actual rendered image area inside object-fit:contain
    const imgRect = img.getBoundingClientRect();
    const viewportRect = overlayContainer.getBoundingClientRect();

    // object-fit: contain scales image to fit while preserving aspect ratio
    const imgAspect = img.naturalWidth / img.naturalHeight;
    const containerAspect = imgRect.width / imgRect.height;

    let renderedWidth, renderedHeight;
    if (imgAspect > containerAspect) {
        // Image is wider than container → width fills, height has padding
        renderedWidth = imgRect.width;
        renderedHeight = imgRect.width / imgAspect;
    } else {
        // Image is taller than container → height fills, width has padding
        renderedHeight = imgRect.height;
        renderedWidth = imgRect.height * imgAspect;
    }

    const scaleX = renderedWidth / img.naturalWidth;
    const scaleY = renderedHeight / img.naturalHeight;
    // Center offset within the img element
    const imgPadX = (imgRect.width - renderedWidth) / 2;
    const imgPadY = (imgRect.height - renderedHeight) / 2;
    const offsetX = (imgRect.left - viewportRect.left) + imgPadX;
    const offsetY = (imgRect.top - viewportRect.top) + imgPadY;

    elements.forEach((el, i) => {
        const box = document.createElement('div');
        box.className = `ui-element-box ${getElementClass(el.element_type)}`;
        box.style.left = `${offsetX + el.x * scaleX}px`;
        box.style.top = `${offsetY + el.y * scaleY}px`;
        box.style.width = `${el.width * scaleX}px`;
        box.style.height = `${el.height * scaleY}px`;
        // Sequential stagger delay
        box.style.animationDelay = `${i * 100}ms`;

        const label = document.createElement('div');
        label.className = 'ui-element-label';
        label.textContent = el.label.substring(0, 30);
        box.appendChild(label);

        overlayContainer.appendChild(box);
    });
}

function getElementClass(type) {
    if (['button', 'submit', 'btn'].some(t => type.toLowerCase().includes(t))) return 'button';
    if (['input', 'text_field', 'search', 'textarea'].some(t => type.toLowerCase().includes(t))) return 'input';
    if (['link', 'a', 'href'].some(t => type.toLowerCase().includes(t))) return 'link';
    return '';
}

function renderElementsList(elements) {
    elementsList.innerHTML = '';
    elements.forEach((el, i) => {
        const item = document.createElement('div');
        item.className = 'element-item';
        // Stagger slide-in
        item.style.animationDelay = `${i * 60}ms`;

        const badge = document.createElement('span');
        badge.className = `element-type-badge ${getElementClass(el.element_type) || 'other'}`;
        badge.textContent = el.element_type;

        const labelSpan = document.createElement('span');
        labelSpan.className = 'element-label';
        labelSpan.textContent = el.label;

        const confidence = document.createElement('span');
        confidence.className = 'element-confidence';
        // Animate counter from 0 to target
        const targetVal = Math.round((el.confidence || 0) * 100);
        confidence.textContent = '0%';
        animateCounter(confidence, 0, targetVal, 400 + i * 60);

        item.appendChild(badge);
        item.appendChild(labelSpan);
        item.appendChild(confidence);
        elementsList.appendChild(item);
    });
}

/**
 * @description Animate a number counter from start to end with eased timing
 * @param {HTMLElement} el - Element to update
 * @param {number} start - Start value
 * @param {number} end - End value
 * @param {number} delayMs - Delay before animation starts
 * @author ANTIGRAVITY
 * @created 2026-03-01
 */
function animateCounter(el, start, end, delayMs) {
    setTimeout(() => {
        const duration = 500;
        const startTime = performance.now();
        function step(now) {
            const elapsed = now - startTime;
            const progress = Math.min(elapsed / duration, 1);
            const eased = 1 - Math.pow(1 - progress, 3);
            const current = Math.round(start + (end - start) * eased);
            el.textContent = `${current}%`;
            if (progress < 1) requestAnimationFrame(step);
        }
        requestAnimationFrame(step);
    }, delayMs);
}

/**
 * @description Show a click ripple effect at given coordinates on the screenshot viewport
 * @param {number} x - X coordinate in original screenshot pixels
 * @param {number} y - Y coordinate in original screenshot pixels
 * @author ANTIGRAVITY
 * @created 2026-03-01
 */
function showClickRipple(x, y) {
    const img = screenshotImg;
    if (!img.naturalWidth) return;

    const imgRect = img.getBoundingClientRect();
    const viewportRect = overlayContainer.getBoundingClientRect();
    const imgAspect = img.naturalWidth / img.naturalHeight;
    const containerAspect = imgRect.width / imgRect.height;

    let renderedWidth, renderedHeight;
    if (imgAspect > containerAspect) {
        renderedWidth = imgRect.width;
        renderedHeight = imgRect.width / imgAspect;
    } else {
        renderedHeight = imgRect.height;
        renderedWidth = imgRect.height * imgAspect;
    }

    const scaleX = renderedWidth / img.naturalWidth;
    const scaleY = renderedHeight / img.naturalHeight;
    const imgPadX = (imgRect.width - renderedWidth) / 2;
    const imgPadY = (imgRect.height - renderedHeight) / 2;
    const offsetX = (imgRect.left - viewportRect.left) + imgPadX;
    const offsetY = (imgRect.top - viewportRect.top) + imgPadY;

    const ripple = document.createElement('div');
    ripple.className = 'click-ripple';
    ripple.style.left = `${offsetX + x * scaleX}px`;
    ripple.style.top = `${offsetY + y * scaleY}px`;

    const dot = document.createElement('div');
    dot.className = 'click-dot';
    ripple.appendChild(dot);

    overlayContainer.appendChild(ripple);

    // Remove after animation completes
    setTimeout(() => ripple.remove(), 900);
}

// ── Voice Input ─────────────────────────────────────────────

function toggleMic() {
    if (isRecording) {
        stopRecording();
    } else {
        startRecording();
    }
}

function startRecording() {
    if (!('webkitSpeechRecognition' in window || 'SpeechRecognition' in window)) {
        addLog('⚠️', 'Speech recognition not supported in this browser', 'error');
        return;
    }

    const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
    recognition = new SpeechRecognition();
    recognition.lang = 'en-US';
    recognition.continuous = false;
    recognition.interimResults = true;

    recognition.onresult = (event) => {
        const transcript = Array.from(event.results)
            .map(r => r[0].transcript)
            .join('');

        commandInput.value = transcript;

        if (event.results[0].isFinal) {
            stopRecording();
            sendCommand();
        }
    };

    recognition.onerror = (event) => {
        addLog('⚠️', `Mic error: ${event.error}`, 'error');
        stopRecording();
    };

    recognition.onend = () => {
        stopRecording();
    };

    recognition.start();
    isRecording = true;
    micBtn.classList.add('recording');
    micBtn.textContent = '⏹';
    addLog('🎙️', 'Listening...', 'narration');
}

function stopRecording() {
    if (recognition) {
        recognition.stop();
        recognition = null;
    }
    isRecording = false;
    micBtn.classList.remove('recording');
    micBtn.textContent = '🎙️';
}

// ── Accessibility ───────────────────────────────────────────

function toggleAccessibility(enabled) {
    if (ws && ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify({ type: 'set_accessibility', enabled }));
    }
}

// ── Text-to-Speech ──────────────────────────────────────────

function speak(text) {
    if ('speechSynthesis' in window) {
        const utterance = new SpeechSynthesisUtterance(text);
        utterance.rate = 1.0;
        utterance.pitch = 0.8; // Voix grave — Phantom persona
        utterance.volume = 0.8;

        // Try to use a deeper voice
        const voices = speechSynthesis.getVoices();
        const deepVoice = voices.find(v =>
            v.name.includes('Google') && v.lang.startsWith('en')
        ) || voices.find(v => v.lang.startsWith('en'));

        if (deepVoice) utterance.voice = deepVoice;

        speechSynthesis.speak(utterance);
    }
}

// ── Activity Log ────────────────────────────────────────────

function addLog(icon, text, type = 'narration') {
    const entry = document.createElement('div');
    entry.className = `log-entry ${type}`;

    const now = new Date().toLocaleTimeString('en-US', {
        hour12: false,
        hour: '2-digit',
        minute: '2-digit',
        second: '2-digit'
    });

    entry.innerHTML = `
        <div class="log-icon">${icon}</div>
        <div class="log-content">
            <div class="log-text">${escapeHtml(text)}</div>
            <div class="log-time">${now}</div>
        </div>
    `;

    activityLog.appendChild(entry);
    activityLog.scrollTop = activityLog.scrollHeight;

    // Keep max 100 entries
    while (activityLog.children.length > 100) {
        activityLog.removeChild(activityLog.firstChild);
    }
}

// ── Tab Switching ───────────────────────────────────────────

function switchTab(tab) {
    document.querySelectorAll('.panel-tab').forEach(t => t.classList.remove('active'));
    document.querySelector(`[data-tab="${tab}"]`).classList.add('active');

    activityLog.classList.toggle('active', tab === 'activity');
    activityLog.style.display = tab === 'activity' ? 'flex' : 'none';
    elementsList.classList.toggle('active', tab === 'elements');
}

// ── Utilities ───────────────────────────────────────────────

function setStatus(status, text) {
    statusBadge.className = `status-badge ${status}`;
    statusText.textContent = text;
}

function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

// Resize observer for overlay
const resizeObserver = new ResizeObserver(() => {
    if (currentElements.length > 0) {
        renderOverlay(currentElements);
    }
});
resizeObserver.observe(document.getElementById('screenViewport'));

// Load voices for TTS
if ('speechSynthesis' in window) {
    speechSynthesis.onvoiceschanged = () => speechSynthesis.getVoices();
}

// URL bar — Enter to navigate
urlInput.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && isSessionActive) {
        const url = urlInput.value.trim();
        if (url) {
            fetch(`${API_BASE}/api/navigate?url=${encodeURIComponent(url)}`, { method: 'POST' })
                .then(r => r.json())
                .then(data => {
                    addLog('🌐', `Navigated to ${url} — ${data.elements_found} elements`, 'success');
                    refreshScreenshot();
                })
                .catch(err => addLog('❌', err.message, 'error'));
        }
    } else if (e.key === 'Enter' && !isSessionActive) {
        startSession();
    }
});

console.log('🛸 PHANTOM UI Navigator — Frontend loaded');
