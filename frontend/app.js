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
const statusBadge = document.getElementById('statusBadge');
const statusDot = document.getElementById('statusDot');
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
const elementCountBadge = document.getElementById('elementCountBadge');

// ── Session Management ──────────────────────────────────────

async function startSession() {
    const url = urlInput.value.trim() || 'https://www.google.com';
    urlInput.value = url;

    addLog('🚀', `Starting session — navigating to ${url}...`, 'agent');
    startBtn.disabled = true;
    updateStartBtn('⏳', 'STARTING...', true);

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

        // Update UI
        setStatus('online', 'LIVE');
        const startIcon = document.getElementById('startIcon');
        const startText = document.getElementById('startText');
        if (startIcon) startIcon.textContent = '⏹';
        if (startText) startText.textContent = 'STOP';
        startBtn.onclick = stopSession;
        startBtn.classList.remove('text-slate-600', 'bg-white', 'border-slate-200');
        startBtn.classList.add('text-red-600', 'bg-red-50', 'border-red-200');

        addLog('✅', `Session started — ${data.elements_found} elements detected`, 'system');
        addLog('👁️', `Page context: ${data.page_context}`, 'agent');

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
        addLog('❌', `Error: ${err.message}`, 'system');
        loadingOverlay.style.display = 'none';
        placeholder.style.display = 'flex';
        startBtn.disabled = false;
        updateStartBtn('▶', 'START', false);
    }
}

async function stopSession() {
    try {
        await fetch(`${API_BASE}/api/session/stop`, { method: 'POST' });
    } catch (e) { /* ignore */ }

    isSessionActive = false;
    setStatus('offline', 'OFFLINE');
    const startIcon = document.getElementById('startIcon');
    const startText = document.getElementById('startText');
    if (startIcon) startIcon.textContent = '▶';
    if (startText) startText.textContent = 'START';
    startBtn.onclick = startSession;
    startBtn.classList.remove('text-red-600', 'bg-red-50', 'border-red-200');
    startBtn.classList.add('text-slate-600', 'bg-white', 'border-slate-200');
    startBtn.disabled = false;

    // Clear screenshot
    screenshotImg.style.display = 'none';
    screenshotImg.classList.add('hidden');
    placeholder.style.display = 'flex';
    overlayContainer.innerHTML = '';
    loadingOverlay.style.display = 'none';
    if (elementCountBadge) elementCountBadge.classList.add('hidden');

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

        case 'context_update':
            addLog('👁️', `Page context: ${msg.context}`, 'narration');
            break;

        case 'result_summary':
            addLog('💬', msg.text, 'agent', msg.options);
            if (msg.audio) {
                try {
                    const audio = new Audio("data:audio/mp3;base64," + msg.audio);
                    audio.play();
                } catch (e) {
                    speak(msg.text);
                }
            } else {
                speak(msg.text);
            }
            break;

        case 'plan_generated':
            addLog('🧠', `Working on: "${msg.intent}"`, 'action');
            break;

        case 'execution_complete':
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

        case 'auto_navigate':
            const urlInput = document.getElementById('urlInput');
            if (urlInput) urlInput.value = msg.url;
            break;

        case 'session_auto_started':
            isSessionActive = true;
            loadingOverlay.style.display = 'none';
            const startIcon = document.getElementById('startIcon');
            const startText = document.getElementById('startText');
            if (startIcon) startIcon.textContent = '⏹';
            if (startText) startText.textContent = 'STOP';
            startBtn.onclick = stopSession;
            startBtn.classList.remove('text-slate-600', 'bg-white', 'border-slate-200');
            startBtn.classList.add('text-red-600', 'bg-red-50', 'border-red-200');
            startBtn.disabled = false;
            setStatus('online', 'LIVE');
            startScreenshotPolling();
            addLog('✅', `Connected to ${msg.url} — ${msg.elements} elements detected`, 'system');
            if (msg.options) {
                addLog('✨', 'Here are your options:', 'agent', msg.options);
            }
            break;
    }
}

// ── Commands ────────────────────────────────────────────────

async function sendCommand() {
    const intent = commandInput.value.trim();
    if (!intent) return;

    commandInput.value = '';
    addLog('👤', intent, 'user');

    // If no session, auto-connect WebSocket and let backend handle auto-navigation
    if (!isSessionActive) {
        // Show loading animation
        placeholder.style.display = 'none';
        loadingOverlay.style.display = 'flex';
        const loadingText = document.getElementById('loadingText');
        const substatus = document.getElementById('loadingSubstatus');
        if (loadingText) loadingText.textContent = 'Phantom is thinking...';
        if (substatus) substatus.textContent = 'Detecting the best website for your request';
        const progressBar = document.getElementById('loadingProgressBar');
        if (progressBar) {
            progressBar.style.animation = 'none';
            progressBar.offsetHeight;
            progressBar.style.animation = 'progress-sweep 12s ease-in-out forwards';
        }
        setStatus('offline', 'AUTO-NAVIGATING...');

        // Connect WebSocket if not connected
        if (!ws || ws.readyState !== WebSocket.OPEN) {
            connectWebSocket();
            // Wait for connection
            await new Promise(resolve => {
                const checkInterval = setInterval(() => {
                    if (ws && ws.readyState === WebSocket.OPEN) {
                        clearInterval(checkInterval);
                        resolve();
                    }
                }, 100);
                setTimeout(() => { clearInterval(checkInterval); resolve(); }, 5000);
            });
        }
    }

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
    screenshotImg.classList.remove('hidden');
    placeholder.style.display = 'none';
}

// ── Bounding Box Overlay ────────────────────────────────────

function updateElements(elements) {
    currentElements = elements;
    renderOverlay(elements);
    renderElementsList(elements);

    // Update element count badge
    const countSpan = document.getElementById('elementCount');
    if (countSpan) countSpan.textContent = elements.length;
    if (elementCountBadge) {
        if (elements.length > 0) {
            elementCountBadge.classList.remove('hidden');
            elementCountBadge.classList.add('flex');
        } else {
            elementCountBadge.classList.add('hidden');
            elementCountBadge.classList.remove('flex');
        }
    }
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
    const tabCount = document.getElementById('elementsTabCount');
    if (tabCount) {
        tabCount.textContent = elements.length;
        tabCount.classList.remove('hidden');
    }

    elementsList.innerHTML = '';
    elements.forEach((el, i) => {
        const item = document.createElement('div');
        item.className = 'flex items-center justify-between p-3 bg-white border border-slate-200 rounded-lg shadow-sm animate-slide-up hover:border-bahama-blue-300 transition-colors';
        item.style.animationDelay = `${i * 30}ms`;

        const leftCol = document.createElement('div');
        leftCol.className = 'flex flex-col gap-1.5 overflow-hidden pr-2';

        const badge = document.createElement('span');
        const typeClass = getElementClass(el.element_type);
        let badgeColor = 'bg-slate-100 text-slate-600 border-slate-200';
        if(typeClass === 'button') badgeColor = 'bg-purple-50 text-purple-700 border-purple-100';
        if(typeClass === 'input')  badgeColor = 'bg-green-50 text-green-700 border-green-100';
        if(typeClass === 'link')   badgeColor = 'bg-orange-50 text-orange-700 border-orange-100';
        
        badge.className = `text-[9px] font-mono shadow-sm font-bold uppercase tracking-wider px-1.5 py-0.5 rounded border w-max ${badgeColor}`;
        badge.textContent = el.element_type;

        const labelSpan = document.createElement('span');
        labelSpan.className = 'text-sm font-medium text-slate-700 truncate';
        labelSpan.textContent = el.label || '<no label>';

        leftCol.appendChild(badge);
        leftCol.appendChild(labelSpan);

        const confidence = document.createElement('div');
        confidence.className = 'text-xs font-mono font-bold text-bahama-blue-600 bg-bahama-blue-50 border border-bahama-blue-100 shadow-sm px-2 py-1 rounded-md ml-2 flex-shrink-0';
        
        const targetVal = Math.round((el.confidence || 0) * 100);
        confidence.textContent = '0%';
        animateCounter(confidence, 0, targetVal, 100 + i * 30);

        item.appendChild(leftCol);
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
        addLog('⚠️', 'Speech recognition not supported in this browser', 'system');
        return;
    }

    const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
    recognition = new SpeechRecognition();
    recognition.lang = 'fr-FR,en-US'; 
    recognition.continuous = true;    
    recognition.interimResults = true;

    recognition.onresult = (event) => {
        let transcript = '';
        for (let i = 0; i < event.results.length; i++) {
            transcript += event.results[i][0].transcript;
        }
        commandInput.value = transcript;

        if (event.results[0].isFinal) {
            stopRecording();
            sendCommand();
        }
    };

    recognition.onerror = (event) => {
        addLog('⚠️', `Mic error: ${event.error}`, 'system');
        stopRecording();
    };

    recognition.onend = () => {
        stopRecording();
    };

    recognition.start();
    isRecording = true;
    
    micBtn.classList.remove('bg-white', 'text-slate-400', 'border-slate-200');
    micBtn.classList.add('bg-bahama-blue-600', 'text-white', 'border-bahama-blue-600', 'shadow-md');
    micBtn.innerHTML = '<svg class="w-7 h-7" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M21 12a9 9 0 11-18 0 9 9 0 0118 0z" /><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 10a1 1 0 011-1h4a1 1 0 011 1v4a1 1 0 01-1 1h-4a1 1 0 01-1-1v-4z" /></svg>';
    
    document.getElementById('micPulse1').classList.remove('hidden');
    document.getElementById('micPulse1').classList.add('animate-radar');
    document.getElementById('micPulse2').classList.remove('hidden');
    document.getElementById('micPulse2').classList.add('animate-radar');
    
    addLog('🎙️', 'Voice input active - Listening...', 'system');
}

function stopRecording() {
    if (recognition) {
        recognition.stop();
        recognition = null;
    }
    isRecording = false;
    
    micBtn.classList.add('bg-white', 'text-slate-400', 'border-slate-200');
    micBtn.classList.remove('bg-bahama-blue-600', 'text-white', 'border-bahama-blue-600', 'shadow-md');
    micBtn.innerHTML = '<svg class="w-7 h-7" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M19 11a7 7 0 01-7 7m0 0a7 7 0 01-7-7m7 7v4m0 0H8m4 0h4m-4-8a3 3 0 01-3-3V5a3 3 0 116 0v6a3 3 0 01-3 3z" /></svg>';
    
    document.getElementById('micPulse1').classList.add('hidden');
    document.getElementById('micPulse1').classList.remove('animate-radar');
    document.getElementById('micPulse2').classList.add('hidden');
    document.getElementById('micPulse2').classList.remove('animate-radar');
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

function addLog(icon, text, type = 'narration', options = null) {
    const entry = document.createElement('div');
    entry.className = 'w-full animate-slide-up flex flex-col gap-1';

    const now = new Date().toLocaleTimeString('en-US', {
        hour12: false, hour: '2-digit', minute: '2-digit', second: '2-digit'
    });

    let headerClass = '';
    let textClass = '';
    let label = '';

    if (type === 'user' || type === 'command') {
        headerClass = 'bg-slate-100 text-slate-600 border-slate-200';
        textClass = 'text-slate-800 font-medium text-base';
        label = 'You';
    } else if (type === 'narration' || type === 'result' || type === 'agent') {
        headerClass = 'bg-bahama-blue-50 text-bahama-blue-700 border-bahama-blue-100';
        textClass = 'text-slate-700 text-sm';
        label = 'Phantom';
    } else if (type === 'action') {
        headerClass = 'bg-emerald-50 text-emerald-700 border-emerald-100';
        textClass = 'font-mono text-xs text-emerald-700 leading-relaxed';
        label = 'Action';
    } else {
        // system, error, success
        headerClass = 'bg-slate-50 text-slate-500 border-slate-100';
        textClass = 'font-mono text-[11px] text-slate-500';
        label = 'System';
    }

    entry.innerHTML = `
        <div class="flex items-center justify-between mb-1 mt-3">
            <span class="text-[10px] uppercase tracking-wider font-bold ${headerClass} border rounded px-1.5 py-0.5 font-mono flex items-center gap-1">${icon} ${label}</span>
            <span class="text-[10px] text-slate-400 font-mono">${now}</span>
        </div>
        <div class="${textClass}">${escapeHtml(text)}</div>
    `;

    // Add Interactive Options cards if provided
    if (options && Array.isArray(options) && options.length > 0) {
        const optionsContainer = document.createElement('div');
        optionsContainer.className = 'flex flex-col gap-2 mt-3';
        
        options.forEach(opt => {
            const btn = document.createElement('button');
            btn.className = 'group flex items-center justify-between w-full text-left bg-white border border-slate-200 rounded-xl p-3.5 hover:border-bahama-blue-400 hover:shadow-md transition-all hover:-translate-y-[1px]';
            btn.onclick = () => handleOptionClick(opt);
            
            btn.innerHTML = `
                <div class="flex flex-col">
                    <span class="font-bold text-slate-800 text-sm">${escapeHtml(opt.title || opt)}</span>
                    ${opt.subtitle ? `<span class="text-xs text-slate-500 mt-0.5">${escapeHtml(opt.subtitle)}</span>` : ''}
                </div>
                <div class="opacity-0 group-hover:opacity-100 group-hover:translate-x-1 transition-all text-bahama-blue-500 flex-shrink-0 ml-2">
                    <svg class="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M14 5l7 7m0 0l-7 7m7-7H3" /></svg>
                </div>
            `;
            optionsContainer.appendChild(btn);
        });
        
        entry.appendChild(optionsContainer);
    }

    activityLog.appendChild(entry);
    
    setTimeout(() => {
        document.getElementById('logsEndRef').scrollIntoView({ behavior: 'smooth' });
    }, 50);

    // Keep max 100 entries
    while (activityLog.children.length > 100) {
        activityLog.firstElementChild.remove();
    }
}

function handleOptionClick(opt) {
    const choiceText = opt.title || opt;
    addLog('👤', choiceText, 'user');
    
    if (ws && ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify({ type: 'command', intent: choiceText }));
    } else {
        fetch(`${API_BASE}/api/command`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ intent: choiceText, auto_execute: true }),
        }).catch(err => addLog('❌', `Error: ${err.message}`, 'system'));
    }
}

// ── Tab Switching ───────────────────────────────────────────

function switchTab(tab) {
    const tabActivity = document.getElementById('tabActivity');
    const tabElements = document.getElementById('tabElements');

    if (tab === 'activity') {
        tabActivity.className = 'flex-1 bg-white border border-slate-200 text-bahama-blue-700 shadow-sm rounded-md px-4 py-2 text-sm font-medium transition-all flex items-center justify-center gap-2';
        tabElements.className = 'flex-1 text-slate-500 hover:bg-slate-100 border border-transparent hover:border-slate-200 rounded-md px-4 py-2 text-sm font-medium transition-all flex items-center justify-center gap-2';
        activityLog.style.display = 'flex';
        elementsList.style.display = 'none';
        elementsList.classList.add('hidden');
        activityLog.classList.remove('hidden');
    } else {
        tabElements.className = 'flex-1 bg-white border border-slate-200 text-bahama-blue-700 shadow-sm rounded-md px-4 py-2 text-sm font-medium transition-all flex items-center justify-center gap-2';
        tabActivity.className = 'flex-1 text-slate-500 hover:bg-slate-100 border border-transparent hover:border-slate-200 rounded-md px-4 py-2 text-sm font-medium transition-all flex items-center justify-center gap-2';
        activityLog.style.display = 'none';
        activityLog.classList.add('hidden');
        elementsList.style.display = 'flex';
        elementsList.classList.remove('hidden');
    }
}

// ── Utilities ───────────────────────────────────────────────

/**
 * @description Update the status badge with Tailwind classes
 * @param {string} status - 'online' or 'offline'
 * @param {string} text - Status text to display
 */
function setStatus(status, text) {
    statusText.textContent = text;
    if (status === 'online') {
        statusBadge.className = 'flex items-center px-3 py-1 rounded-full border bg-green-50 border-green-200 text-green-600 shadow-sm';
        if (statusDot) { statusDot.className = 'w-2 h-2 rounded-full bg-green-500 mr-2 animate-pulse'; }
    } else {
        statusBadge.className = 'flex items-center px-3 py-1 rounded-full border bg-slate-50 border-slate-200 text-slate-500 shadow-sm';
        if (statusDot) { statusDot.className = 'w-2 h-2 rounded-full bg-slate-400 mr-2'; }
    }
}

/**
 * @description Helper to update the start/stop button consistently
 * @param {string} icon - Icon character
 * @param {string} label - Button label
 * @param {boolean} isActive - Whether the session is active (STOP state)
 */
function updateStartBtn(icon, label, isActive) {
    const startIcon = document.getElementById('startIcon');
    const startTextEl = document.getElementById('startText');
    if (startIcon) startIcon.textContent = icon;
    if (startTextEl) startTextEl.textContent = label;
    if (isActive) {
        startBtn.classList.remove('text-slate-600', 'bg-white', 'border-slate-200');
        startBtn.classList.add('text-red-600', 'bg-red-50', 'border-red-200');
    } else {
        startBtn.classList.remove('text-red-600', 'bg-red-50', 'border-red-200');
        startBtn.classList.add('text-slate-600', 'bg-white', 'border-slate-200');
    }
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
