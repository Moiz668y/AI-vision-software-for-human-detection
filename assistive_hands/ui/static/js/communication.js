// assistive_hands/ui/static/js/communication.js

/* Communication JavaScript */

let displayText = '';
let dwellTime = 1.0;
let gazeUpdateInterval;
let dwellEnabled = true;
let gazeInputPaused = false;
let activeDwellId = null;
let completedDwellId = null;
const mapper = new GazeElementMapper();
const dwellTimers = new Map();

document.addEventListener('DOMContentLoaded', async () => {
    console.log('Communication page loaded');

    try {
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
    const clearBtn = document.getElementById('clearBtn');
    const dwellTimeInput = document.getElementById('dwellTimeInput');
    const dwellEnabledToggle = document.getElementById('dwellEnabledToggle');
    const pauseGazeInputBtn = document.getElementById('pauseGazeInputBtn');
    const communicationBackBtn = document.getElementById('communicationBackBtn');

    console.log('Speak Button found:', !!speakBtn);
    console.log('Clear Button found:', !!clearBtn);

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

    pauseGazeInputBtn?.addEventListener('click', () => {
        setGazeInputPaused(!gazeInputPaused);
    });

    communicationBackBtn?.addEventListener('click', () => {
        if (window.history.length > 1) {
            window.history.back();
        } else {
            window.location.href = '/';
        }
    });

    updateGazeInputState();
    
    console.log('Controls setup complete');
}

function refreshGazeTargets() {
    cancelAllDwell();
    if (mapper.currentElement?.onLeave) {
        mapper.currentElement.onLeave();
    }
    mapper.elements = [];
    mapper.currentElement = null;

    const targets = [
        ...document.querySelectorAll('.keyboard-btn'),
        ...document.querySelectorAll('.phrase-btn'),
        ...document.querySelectorAll('#speakBtn, #clearBtn, #pauseGazeInputBtn, #communicationBackBtn, .communication-toolbar a')
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
    gazeUpdateInterval = setInterval(async () => {
        try {
            const response = await api.get('/api/gaze/current');

            if (response.status === 'success') {
                const { gaze_normalized } = response;

                // Convert normalized gaze (0-1) to VIEWPORT pixels for element hit-testing
                // getBoundingClientRect() returns viewport coords, so we must match that
                const viewX = gaze_normalized.x * window.innerWidth;
                const viewY = gaze_normalized.y * window.innerHeight;

                // Update on-screen gaze cursor (viewport coords)
                updateGazeCursor(viewX, viewY);

                // Update keyboard element mapping (viewport coords)
                mapper.updateGaze(viewX, viewY);

                // Trigger dwell timer for hovered key
                updateDwellTimers();

                // Backend owns physical cursor movement; this page only uses gaze for UI hit-testing.
            }
        } catch (error) {
            console.error('Gaze tracking error:', error);
        }
    }, 50);
}

function updateGazeCursor(viewX, viewY) {
    let cursor = document.getElementById('gazeCursor');
    if (!cursor) {
        cursor = document.createElement('div');
        cursor.id = 'gazeCursor';
        cursor.style.cssText = `
            position: fixed;
            width: 22px; height: 22px;
            border: 3px solid #00ff88;
            border-radius: 50%;
            background: rgba(0,255,136,0.15);
            pointer-events: none;
            z-index: 9999;
            transform: translate(-50%, -50%);
            box-shadow: 0 0 12px rgba(0,255,136,0.6);
            transition: left 0.05s, top 0.05s;
        `;
        document.body.appendChild(cursor);
    }
    cursor.style.left = viewX + 'px';
    cursor.style.top  = viewY + 'px';
}

function handleKeyPress(key, btn) {
    console.log(`Key pressed: ${key}`);
    
    // Update local display
    if (key === 'Enter') {
        addText('\n');
    } else if (key === 'Space') {
        addText(' ');
    } else if (key === 'Backspace' || key === '⌫') {
        if (displayText.length > 0) {
            displayText = displayText.slice(0, -1);
        }
    } else if (key !== 'Shift') {
        addText(key);
    }

    updateTextDisplay();

    // Send key press to system keyboard (optional - can be disabled)
    const sendToSystem = document.getElementById('sendToSystemCheckbox')?.checked ?? false;
    if (sendToSystem) {
        sendKeyToSystem(key);
    }

    // Visual feedback
    btn.classList.add('active');
    setTimeout(() => btn.classList.remove('active'), 100);
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

function addText(text) {
    displayText += text;
    updateTextDisplay();
}

function clearText() {
    displayText = '';
    updateTextDisplay();
    showToast('Text cleared', 'info');
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
window.addEventListener('beforeunload', () => {
    clearInterval(gazeUpdateInterval);
    cancelAllDwell();
});
