// assistive_hands/ui/static/js/communication.js

/* Communication JavaScript */

let displayText = '';
let dwellTime = 1.0;
let gazeUpdateInterval;
let gazeTrackingStopped = false;
let gazeRequestInFlight = false;
let latestGazeRequestId = 0;
let telemetryUnsubscribe = null;
let dwellEnabled = true;
let gazeInputPaused = false;
let activeDwellId = null;
let completedDwellId = null;
let voiceRecognition = null;
let voiceListening = false;
let voiceSupported = false;
const voiceAutoStart = true;
const systemOutputAlwaysEnabled = true;
const mapper = new GazeElementMapper();
const dwellTimers = new Map();

document.addEventListener('DOMContentLoaded', async () => {
    console.log('Communication page loaded');

    try {
        syncDisplayTextFromDom();

        // Start camera
        await api.post('/api/camera/start');
        showToast('Camera started', 'success');

        // Initialize keyboard
        initializeKeyboard();

        // Initialize quick phrases
        initializeQuickPhrases();

        // Setup controls
        setupControls();

        // Register gaze targets after controls exist
        requestAnimationFrame(refreshGazeTargets);
        window.addEventListener('resize', debounce(refreshGazeTargets, 150));
        window.addEventListener('scroll', debounce(refreshGazeTargets, 150), true);

        // Start gaze tracking
        startGazeTracking();

    } catch (error) {
        console.error('Communication initialization error:', error);
        showToast('Error initializing communication interface', 'danger');
    }
});

function initializeKeyboard() {
    const keyboardBtns = document.querySelectorAll('.keyboard-btn');

    keyboardBtns.forEach(btn => {
        btn.addEventListener('click', () => {
            handleKeyPress(btn.dataset.key, btn);
        });
    });
}

function initializeQuickPhrases() {
    const phraseBtns = document.querySelectorAll('.phrase-btn');

    phraseBtns.forEach(btn => {
        btn.addEventListener('click', () => {
            handlePhrasePress(btn);
        });
    });
}

function setupControls() {
    console.log('=== Setting up controls ===');
    
    const speakBtn = document.getElementById('speakBtn');
    const voiceBtn = document.getElementById('voiceBtn');
    const clearBtn = document.getElementById('clearBtn');
    const dwellTimeInput = document.getElementById('dwellTimeInput');
    const dwellEnabledToggle = document.getElementById('dwellEnabledToggle');
    const pauseGazeInputBtn = document.getElementById('pauseGazeInputBtn');
    const communicationBackBtn = document.getElementById('communicationBackBtn');
    const sendToSystemCheckbox = document.getElementById('sendToSystemCheckbox');

    console.log('Speak Button found:', !!speakBtn);
    console.log('Voice Button found:', !!voiceBtn);
    console.log('Clear Button found:', !!clearBtn);

    if (sendToSystemCheckbox) {
        sendToSystemCheckbox.checked = true;
        sendToSystemCheckbox.disabled = true;
    }

    // Speak button - DIRECT IMPLEMENTATION
    if (speakBtn) {
        speakBtn.onclick = function(e) {
            e.preventDefault();
            e.stopPropagation();
            
            console.log('SPEAK BUTTON CLICKED');
            console.log('Display text:', displayText);
            console.log('Display text length:', displayText.length);
            
            if (!displayText || displayText.trim().length === 0) {
                showToast('No text to speak', 'warning');
                return;
            }
            
            try {
                // Use Web Speech API directly
                const text = displayText;
                const utterance = new SpeechSynthesisUtterance(text);
                utterance.rate = 1;
                utterance.pitch = 1;
                utterance.volume = 1;
                utterance.lang = 'en-US';
                
                utterance.onstart = function() {
                    console.log('Speech started');
                    showToast('Speaking text', 'info');
                };
                
                utterance.onend = function() {
                    console.log('Speech ended');
                };
                
                utterance.onerror = function(e) {
                    console.error('Speech error:', e);
                    showToast('Speech error: ' + e.error, 'danger');
                };
                
                window.speechSynthesis.cancel();
                window.speechSynthesis.speak(utterance);
                console.log('Speech synthesis started');
                
            } catch (error) {
                console.error('Error:', error);
                showToast('Error: ' + error.message, 'danger');
            }
            
            return false;
        };
    }

    // Clear button
    if (clearBtn) {
        clearBtn.onclick = function(e) {
            e.preventDefault();
            clearText();
            return false;
        };
    }

    setupVoiceControls();

    // Dwell time input
    if (dwellTimeInput) {
        dwellTimeInput.addEventListener('input', (e) => {
            dwellTime = parseFloat(e.target.value) || 1.0;
        });
    }

    if (dwellEnabledToggle) {
        dwellEnabled = dwellEnabledToggle.checked;
        dwellEnabledToggle.addEventListener('change', (e) => {
            dwellEnabled = e.target.checked;
            cancelAllDwell();
            updateGazeInputState();
        });
    }

    if (pauseGazeInputBtn) {
        pauseGazeInputBtn.addEventListener('click', () => {
            setGazeInputPaused(!gazeInputPaused);
        });
    }

    if (communicationBackBtn) {
        communicationBackBtn.addEventListener('click', () => {
            if (window.history.length > 1) {
                window.history.back();
            } else {
                window.location.href = '/';
            }
        });
    }

    updateGazeInputState();
    
    console.log('Controls setup complete');
}

function refreshGazeTargets() {
    cancelAllDwell();
    if (mapper.currentElement && mapper.currentElement.onLeave) {
        mapper.currentElement.onLeave();
    }
    mapper.elements = [];
    mapper.currentElement = null;

    const targets = [
        ...document.querySelectorAll('.keyboard-btn'),
        ...document.querySelectorAll('.phrase-btn'),
        ...document.querySelectorAll('#speakBtn, #voiceBtn, #clearBtn, #pauseGazeInputBtn, #communicationBackBtn, .communication-toolbar a')
    ];

    targets.forEach((target, index) => {
        const targetId = target.dataset.gazeId || target.id || `gaze-target-${index}`;
        target.dataset.gazeId = targetId;
        const rect = target.getBoundingClientRect();

        mapper.registerElement(
            targetId,
            rect.left,
            rect.top,
            rect.width,
            rect.height,
            () => target.classList.add('hovered'),
            () => {
                target.classList.remove('hovered');
                target.classList.remove('dwell-active');
                target.style.removeProperty('--dwell-progress');
            }
        );
    });

    console.log(`Registered ${targets.length} gaze targets for dwell mapping`);
}

function startGazeTracking() {
    gazeTrackingStopped = false;

    function handleTelemetry(event) {
        if (gazeTrackingStopped) return;
        const telemetry = event.detail.telemetry || {};
        const gaze = telemetry.gaze || {};
        const gazeNormalized = telemetry.gaze_normalized || gaze.normalized;
        if (!gazeNormalized) return;

        const viewX = gazeNormalized.x * window.innerWidth;
        const viewY = gazeNormalized.y * window.innerHeight;
        requestAnimationFrame(() => {
            if (gazeTrackingStopped) return;
            mapper.updateGaze(viewX, viewY);
            updateDwellTimers();
        });
    }

    if (window.AssistiveHandsTelemetry) {
        telemetryUnsubscribe = window.AssistiveHandsTelemetry.subscribe((telemetry, telemetryState, event) => {
            handleTelemetry(event);
        });
        window.AssistiveHandsTelemetry.connect();
        return;
    }

    async function pollGaze() {
        if (gazeTrackingStopped) return;
        if (document.hidden) {
            gazeUpdateInterval = setTimeout(pollGaze, 250);
            return;
        }

        if (gazeRequestInFlight) {
            gazeUpdateInterval = setTimeout(pollGaze, 100);
            return;
        }

        gazeRequestInFlight = true;
        const requestId = ++latestGazeRequestId;
        try {
            const response = await api.get('/api/gaze/current');

            if (response.status === 'success' && requestId === latestGazeRequestId) {
                const { gaze_normalized } = response;

                // Convert normalized gaze (0-1) to VIEWPORT pixels for element hit-testing
                // getBoundingClientRect() returns viewport coords, so we must match that
                const viewX = gaze_normalized.x * window.innerWidth;
                const viewY = gaze_normalized.y * window.innerHeight;

                requestAnimationFrame(() => {
                    if (requestId !== latestGazeRequestId || gazeTrackingStopped) return;

                    // Update keyboard element mapping (viewport coords)
                    mapper.updateGaze(viewX, viewY);

                    // Trigger dwell timer for hovered key
                    updateDwellTimers();
                });

                // Backend owns physical cursor movement; this page only uses gaze for UI hit-testing.
            }
        } catch (error) {
            console.error('Gaze tracking error:', error);
        } finally {
            gazeRequestInFlight = false;
            if (!gazeTrackingStopped) {
                gazeUpdateInterval = setTimeout(pollGaze, 100);
            }
        }
    }

    pollGaze();
}

function updateGazeCursor(viewX, viewY) {
    let cursor = document.getElementById('gazeCursor');
    if (!cursor) {
        cursor = document.createElement('div');
        cursor.id = 'gazeCursor';
        cursor.style.cssText = `
            position: fixed;
            top: 0;
            left: 0;
            width: 22px; height: 22px;
            border: 3px solid #00ff88;
            border-radius: 50%;
            background: rgba(0,255,136,0.15);
            pointer-events: none;
            z-index: 9999;
            transform: translate3d(0, 0, 0) translate(-50%, -50%);
            box-shadow: 0 0 12px rgba(0,255,136,0.6);
            transition: opacity 0.1s ease, box-shadow 0.1s ease;
        `;
        document.body.appendChild(cursor);
    }
    cursor.style.transform = `translate3d(${viewX}px, ${viewY}px, 0) translate(-50%, -50%)`;
}

function handleKeyPress(key, btn) {
    console.log(`Key pressed: ${key}`);
    
    // Update local display
    if (key === 'Enter') {
        addText('\n');
    } else if (key === 'Space') {
        addText(' ');
    } else if (key === 'Backspace' || key === '⌫') {
        deleteLastCharacter(false);
    } else if (key !== 'Shift') {
        addText(key);
    }

    updateTextDisplay();

    // Send key press to system keyboard (optional - can be disabled)
    const sendToSystem = shouldSendToSystem();
    if (sendToSystem) {
        sendKeyToSystem(key);
    }

    // Visual feedback
    if (btn) {
        btn.classList.add('active');
        setTimeout(() => btn.classList.remove('active'), 100);
    }
}

function handlePhrasePress(btn) {
    const phrase = btn.dataset.phrase;
    if (!phrase) return;

    addText(phrase);
    tts.speak(phrase);
    btn.classList.add('active');
    setTimeout(() => btn.classList.remove('active'), 150);
}

async function sendKeyToSystem(key) {
    // Send key press to the system keyboard
    try {
        const keyMap = {
            'Enter': 'enter',
            'Space': 'space',
            'Backspace': 'backspace',
            '⌫': 'backspace',
            'Tab': 'tab',
        };
        
        if (!keyMap[key] && key.length === 1) {
            await sendTextToSystem(key);
            return;
        }

        const systemKey = keyMap[key] || key.toLowerCase();
        
        const response = await api.post('/api/keyboard/press', {
            key: systemKey
        });
        
        if (response.status !== 'success') {
            console.error('Failed to send key to system:', response.message);
        }
    } catch (error) {
        console.error('Error sending key to system:', error);
    }
}

async function sendTextToSystem(text) {
    if (!text) return;

    try {
        const response = await api.post('/api/keyboard/type', {
            text: text
        });

        if (response.status !== 'success') {
            console.error('Failed to send text to system:', response.message);
            showToast(response.message || 'Could not type into Windows', 'warning');
        }
    } catch (error) {
        console.error('Error sending text to system:', error);
        showToast('Could not type into Windows', 'warning');
    }
}

function shouldSendToSystem() {
    if (systemOutputAlwaysEnabled) {
        return true;
    }

    const sendToSystemCheckbox = document.getElementById('sendToSystemCheckbox');
    return sendToSystemCheckbox ? sendToSystemCheckbox.checked : false;
}

function addText(text) {
    displayText += text;
    updateTextDisplay();
}

function addVoiceText(text) {
    if (!text) return;

    const separator = displayText && !/\s$/.test(displayText) ? ' ' : '';
    const addition = separator + text;
    addText(addition);

    if (shouldSendToSystem()) {
        sendTextToSystem(addition);
    }
}

function deleteLastCharacter(sendToSystem = true) {
    if (displayText.length > 0) {
        displayText = displayText.slice(0, -1);
    }
    updateTextDisplay();

    if (sendToSystem && shouldSendToSystem()) {
        sendKeyToSystem('Backspace');
    }
}

function clearText() {
    displayText = '';
    updateTextDisplay();
    showToast('Text cleared', 'info');
}

function setupVoiceControls() {
    if (window.AssistiveHandsVoice || window.AssistiveHandsGlobalVoiceOwner) {
        voiceListening = Boolean(window.AssistiveHandsVoice && window.AssistiveHandsVoice.isListening());
        updateVoiceStatus(voiceListening ? 'Listening' : 'Click Voice to start', voiceListening ? 'listening' : 'idle');
        updateVoiceButton();
        return;
    }

    const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
    const voiceBtn = document.getElementById('voiceBtn');

    if (!voiceBtn) return;

    if (!SpeechRecognition) {
        voiceSupported = false;
        voiceBtn.disabled = true;
        updateVoiceStatus('Voice not supported in this browser', 'unsupported');
        voiceBtn.title = 'Try Chrome or Edge for voice commands';
        return;
    }

    voiceSupported = true;
    voiceRecognition = new SpeechRecognition();
    voiceRecognition.continuous = true;
    voiceRecognition.interimResults = false;
    voiceRecognition.lang = 'en-US';

    voiceRecognition.onstart = () => {
        voiceListening = true;
        updateVoiceButton();
        updateVoiceStatus('Listening for voice commands', 'listening');
    };

    voiceRecognition.onresult = (event) => {
        for (let i = event.resultIndex; i < event.results.length; i += 1) {
            if (!event.results[i].isFinal) continue;
            const transcript = event.results[i][0].transcript;
            handleVoiceCommand(transcript);
        }
    };

    voiceRecognition.onerror = (event) => {
        console.error('Voice recognition error:', event.error);
        if (event.error === 'not-allowed' || event.error === 'service-not-allowed') {
            voiceListening = false;
            updateVoiceStatus('Microphone permission blocked', 'error');
            updateVoiceButton();
            return;
        }
        updateVoiceStatus(`Voice error: ${event.error}`, 'error');
    };

    voiceRecognition.onend = () => {
        if (voiceListening) {
            try {
                voiceRecognition.start();
            } catch (error) {
                voiceListening = false;
                updateVoiceStatus('Voice stopped', 'idle');
                updateVoiceButton();
            }
            return;
        }

        updateVoiceStatus('Voice idle', 'idle');
        updateVoiceButton();
    };

    voiceBtn.addEventListener('click', (event) => {
        event.preventDefault();
        toggleVoiceRecognition();
    });

    updateVoiceStatus('Voice idle', 'idle');
    updateVoiceButton();

    if (voiceAutoStart) {
        setTimeout(() => {
            startVoiceRecognition();
        }, 500);
    }
}

function toggleVoiceRecognition() {
    if (!voiceSupported || !voiceRecognition) return;

    if (voiceListening) {
        if (voiceAutoStart) {
            updateVoiceStatus('Voice is always on', 'listening');
            return;
        }
        stopVoiceRecognition();
    } else {
        startVoiceRecognition();
    }
}

function startVoiceRecognition() {
    if (!voiceSupported || !voiceRecognition) return;

    try {
        voiceRecognition.start();
    } catch (error) {
        console.warn('Voice recognition already starting or active:', error);
        updateVoiceStatus('Click Voice once if microphone permission is needed', 'error');
    }
}

function stopVoiceRecognition() {
    if (!voiceRecognition) return;

    voiceListening = false;
    try {
        voiceRecognition.stop();
    } catch (error) {
        console.warn('Voice recognition stop failed:', error);
    }
    updateVoiceStatus('Voice idle', 'idle');
    updateVoiceButton();
}

function handleVoiceCommand(transcript) {
    const spoken = transcript.trim();
    const command = spoken.toLowerCase().replace(/[.?!]$/g, '');
    const explicitKeyCommand = /^(press|key|keyboard)\s+/.test(command);
    const keyCommand = command
        .replace(/^(press|key|keyboard)\s+/, '')
        .replace(/\s+/g, ' ')
        .trim();

    if (!spoken) return;

    console.log('Voice command:', spoken);

    const quickPhrases = {
        'yes': 'Yes',
        'no': 'No',
        'thank you': 'Thank you',
        'i need help': 'I need help',
        "i'm feeling good": "I'm feeling good",
        'please wait': 'Please wait',
        'hello': 'Hello',
        'hello how are you': 'Hello, how are you?'
    };

    const punctuationCommands = {
        'comma': ',',
        'period': '.',
        'full stop': '.',
        'dot': '.',
        'question mark': '?',
        'exclamation mark': '!',
        'exclamation point': '!',
        'colon': ':',
        'semicolon': ';',
        'dash': '-',
        'hyphen': '-',
        'apostrophe': "'",
        'quote': '"',
        'open bracket': '(',
        'close bracket': ')',
        'open parenthesis': '(',
        'close parenthesis': ')'
    };

    const systemKeyCommands = {
        'enter': 'Enter',
        'return': 'Enter',
        'new line': 'Enter',
        'next line': 'Enter',
        'line break': 'Enter',
        'space': 'Space',
        'tab': 'Tab',
        'escape': 'escape',
        'esc': 'escape',
        'left': 'left',
        'left arrow': 'left',
        'right': 'right',
        'right arrow': 'right',
        'up': 'up',
        'up arrow': 'up',
        'down': 'down',
        'down arrow': 'down',
        'home': 'home',
        'end': 'end',
        'page up': 'pageup',
        'page down': 'pagedown',
        'delete': 'delete',
        'forward delete': 'delete',
        'delete key': 'delete'
    };

    if (command === 'stop listening' || command === 'stop voice') {
        if (voiceAutoStart) {
            updateVoiceStatus('Voice is always on', 'listening');
            return;
        }
        stopVoiceRecognition();
        return;
    }

    if (command === 'clear' || command === 'clear text') {
        clearText();
        return;
    }

    if (command === 'speak' || command === 'speak text') {
        const speakBtn = document.getElementById('speakBtn');
        if (speakBtn) {
            speakBtn.click();
        }
        return;
    }

    if (
        keyCommand === 'backspace' ||
        (keyCommand === 'delete' && !explicitKeyCommand) ||
        keyCommand === 'delete last' ||
        keyCommand === 'remove last'
    ) {
        deleteLastCharacter();
        return;
    }

    if (punctuationCommands[keyCommand]) {
        addVoiceText(punctuationCommands[keyCommand]);
        return;
    }

    if (systemKeyCommands[keyCommand]) {
        const key = systemKeyCommands[keyCommand];

        if (key === 'Enter') {
            addText('\n');
        } else if (key === 'Space') {
            addText(' ');
        } else if (key === 'Tab') {
            addText('\t');
        }

        if (shouldSendToSystem()) {
            sendKeyToSystem(key);
        }
        return;
    }

    if (command.startsWith('type ')) {
        addVoiceText(spoken.slice(5).trim());
        return;
    }

    addVoiceText(quickPhrases[command] || spoken);
}

function updateVoiceStatus(message, state = 'idle') {
    const statusEl = document.getElementById('voiceStatus');
    if (!statusEl) return;

    statusEl.classList.remove('is-listening', 'is-error', 'is-unsupported');
    if (state === 'listening') {
        statusEl.classList.add('is-listening');
    } else if (state === 'error') {
        statusEl.classList.add('is-error');
    } else if (state === 'unsupported') {
        statusEl.classList.add('is-unsupported');
    }

    const iconClass = state === 'listening' ? 'fa-microphone' : 'fa-microphone-slash';
    statusEl.innerHTML = `<i class="fas ${iconClass}"></i><span>${message}</span>`;
}

function updateVoiceButton() {
    const voiceBtn = document.getElementById('voiceBtn');
    if (!voiceBtn) return;

    voiceBtn.setAttribute('aria-pressed', voiceListening ? 'true' : 'false');
    voiceBtn.classList.toggle('btn-primary', voiceListening);
    voiceBtn.classList.toggle('btn-outline-primary', !voiceListening);
    voiceBtn.innerHTML = voiceListening && voiceAutoStart
        ? '<i class="fas fa-microphone"></i> Voice On'
        : voiceListening
        ? '<i class="fas fa-microphone-slash"></i> Stop Voice'
        : '<i class="fas fa-microphone"></i> Voice';
}

function updateTextDisplay() {
    const displayEl = document.getElementById('displayText');
    const charCountEl = document.querySelector('[id="charCount"]');

    if (displayEl) {
        displayEl.textContent = displayText || 'Start typing...';
    }

    if (charCountEl) {
        charCountEl.textContent = `Character count: ${displayText.length}`;
    }
}

function syncDisplayTextFromDom() {
    const displayEl = document.getElementById('displayText');
    if (!displayEl) return;

    const currentText = displayEl.textContent.trim();
    if (currentText && currentText !== 'Start typing...') {
        displayText = currentText;
        updateTextDisplay();
    }
}

function updateDwellTimers() {
    if (!dwellEnabled || gazeInputPaused) {
        cancelAllDwell();
        return;
    }

    const currentElement = mapper.currentElement;

    if (!currentElement) {
        cancelAllDwell();
        completedDwellId = null;
        return;
    }

    const elementId = currentElement.id;

    if (activeDwellId && activeDwellId !== elementId) {
        cancelAllDwell();
    }

    if (completedDwellId && completedDwellId !== elementId) {
        completedDwellId = null;
    }

    if (elementId === completedDwellId || dwellTimers.has(elementId)) {
        return;
    }

    const target = document.querySelector(`[data-gaze-id="${elementId}"]`);
    if (!target) return;

    activeDwellId = elementId;
    target.classList.add('dwell-active');
    target.style.setProperty('--dwell-progress', '0%');

    const dwellTimer = document.getElementById('dwellTimer');
    const duration = dwellTime * 1000;
    const startedAt = performance.now();

    const timerObj = {
        _timeout: null,
        _animation: null,
        stop() {
            clearTimeout(this._timeout);
            if (this._animation) {
                cancelAnimationFrame(this._animation);
            }
            target.classList.remove('dwell-active');
            target.style.removeProperty('--dwell-progress');
            if (dwellTimer) {
                dwellTimer.style.width = '0%';
            }
        }
    };

    const animate = (now) => {
        const progress = Math.min(100, ((now - startedAt) / duration) * 100);
        target.style.setProperty('--dwell-progress', `${progress}%`);
        if (dwellTimer) {
            dwellTimer.style.width = `${progress}%`;
        }
        if (progress < 100 && dwellTimers.has(elementId)) {
            timerObj._animation = requestAnimationFrame(animate);
        }
    };

    timerObj._animation = requestAnimationFrame(animate);
    timerObj._timeout = setTimeout(() => {
        target.classList.remove('dwell-active');
        target.style.removeProperty('--dwell-progress');
        if (dwellTimer) {
            dwellTimer.style.width = '0%';
        }
        dwellTimers.delete(elementId);
        activeDwellId = null;
        completedDwellId = elementId;
        activateDwellTarget(target);
    }, duration);

    dwellTimers.set(elementId, timerObj);
}

function getCommunicationText() {
    return displayText;
}

function activateDwellTarget(target) {
    if (target.classList.contains('keyboard-btn')) {
        handleKeyPress(target.dataset.key, target);
        return;
    }

    if (target.classList.contains('phrase-btn')) {
        handlePhrasePress(target);
        return;
    }

    target.click();
}

function cancelAllDwell() {
    dwellTimers.forEach(timer => timer.stop());
    dwellTimers.clear();
    activeDwellId = null;
    document.querySelectorAll('.keyboard-btn, .phrase-btn, .gaze-target, .app-page-home').forEach(target => {
        target.classList.remove('dwell-active');
        target.style.removeProperty('--dwell-progress');
    });
}

function setGazeInputPaused(paused) {
    gazeInputPaused = paused;
    cancelAllDwell();
    api.post(paused ? '/api/cursor/disable' : '/api/cursor/enable').catch(() => {});
    updateGazeInputState();
}

function updateGazeInputState() {
    const stateEl = document.getElementById('gazeInputState');
    const pauseBtn = document.getElementById('pauseGazeInputBtn');
    const enabledToggle = document.getElementById('dwellEnabledToggle');

    if (enabledToggle) {
        dwellEnabled = enabledToggle.checked;
    }

    if (stateEl) {
        stateEl.classList.toggle('is-paused', gazeInputPaused || !dwellEnabled);
        if (gazeInputPaused) {
            stateEl.innerHTML = '<strong>Gaze input paused</strong><span>Dwell selections are stopped until you resume.</span>';
        } else if (!dwellEnabled) {
            stateEl.innerHTML = '<strong>Dwell selection off</strong><span>Turn it on in Dwell Time Settings to type with gaze.</span>';
        } else {
            stateEl.innerHTML = '<strong>Dwell input ready</strong><span>Look at a key or phrase until the progress completes.</span>';
        }
    }

    if (pauseBtn) {
        pauseBtn.setAttribute('aria-pressed', gazeInputPaused ? 'true' : 'false');
        pauseBtn.classList.toggle('btn-warning', !gazeInputPaused);
        pauseBtn.classList.toggle('btn-success', gazeInputPaused);
        pauseBtn.innerHTML = gazeInputPaused
            ? '<i class="fas fa-play"></i> Resume Gaze'
            : '<i class="fas fa-pause"></i> Pause Gaze';
    }
}

// Cleanup — do NOT stop camera on page unload
Object.assign(window, {
    addText,
    clearText,
    deleteLastCharacter,
    getCommunicationText,
    setGazeInputPaused
});

window.addEventListener('beforeunload', () => {
    gazeTrackingStopped = true;
    clearTimeout(gazeUpdateInterval);
    if (telemetryUnsubscribe) {
        telemetryUnsubscribe();
        telemetryUnsubscribe = null;
    }
    cancelAllDwell();
    stopVoiceRecognition();
});
