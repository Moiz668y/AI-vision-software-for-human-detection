/* Stable global voice commands for AssistiveHands pages. */

(function () {
    const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
    const AUTO_START_VOICE = true;
    const STALE_ACTIVE_TIMEOUT_MS = 15000;
    const PERIODIC_RECREATE_MS = 5 * 60 * 1000;

    const state = {
        recognition: null,
        supported: Boolean(SpeechRecognition),
        desired: false,
        active: false,
        starting: false,
        restartTimer: null,
        watchdogTimer: null,
        scrollTimer: null,
        scrollSystemTimer: null,
        statusResetTimer: null,
        lastHandledCommand: '',
        lastHandledAt: 0,
        lastInterimTranscript: '',
        lastVoiceActivityAt: 0,
        recognitionStartedAt: 0,
        ignoreAbortUntil: 0,
        recovering: false,
        abortRestartTimer: null,
        abortBackoffMs: 600,
        stats: {
            starts: 0,
            results: 0,
            errors: 0,
            ends: 0,
            recreates: 0,
            lastError: ''
        }
    };

    window.AssistiveHandsGlobalVoiceOwner = true;

    const routes = {
        dashboard: '/',
        home: '/',
        main: '/',
        'main menu': '/',
        'control dashboard': '/',
        communication: '/communication',
        communicator: '/communication',
        keyboard: '/communication',
        'text entry': '/communication',
        'text input': '/communication',
        'communication page': '/communication',
        'keyboard page': '/communication',
        calibration: '/calibration',
        calibrate: '/calibration',
        settings: '/settings',
        setting: '/settings',
        setup: '/setup',
        debug: '/debug'
    };

    const routeAliases = {
        dashboard: ['dashboard kholo', 'dashboard open karo', 'home kholo', 'home open karo', 'main kholo', 'main menu kholo'],
        communication: ['communication kholo', 'communication open karo', 'keyboard kholo', 'keyboard open karo', 'text entry kholo', 'text input kholo'],
        calibration: ['calibration kholo', 'calibrate karo', 'calibration open karo'],
        settings: ['settings kholo', 'setting kholo', 'settings open karo'],
        setup: ['setup kholo', 'setup open karo'],
        debug: ['debug kholo', 'debug open karo']
    };

    function post(endpoint, data) {
        return fetch(endpoint, {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify(data || {})
        }).then(function (response) {
            return response.json().catch(function () {
                return {};
            }).then(function (payload) {
                if (!response.ok) {
                    throw new Error(payload.message || ('Request failed: ' + response.status));
                }
                return payload;
            });
        });
    }

    function setStatus(message, mode) {
        if (state.statusResetTimer) {
            clearTimeout(state.statusResetTimer);
            state.statusResetTimer = null;
        }

        const statusMode = mode || 'idle';
        document.querySelectorAll('#voiceStatus, #globalVoiceStatus').forEach(function (statusEl) {
            statusEl.classList.remove('is-listening', 'is-error', 'is-unsupported');
            if (statusMode === 'listening') {
                statusEl.classList.add('is-listening');
            } else if (statusMode === 'error') {
                statusEl.classList.add('is-error');
            } else if (statusMode === 'unsupported') {
                statusEl.classList.add('is-unsupported');
            }

            const icon = document.createElement('i');
            icon.className = 'fas ' + (statusMode === 'listening' ? 'fa-microphone' : 'fa-microphone-slash');

            const text = document.createElement('span');
            text.textContent = message;

            statusEl.replaceChildren(icon, text);
        });
    }

    function resetStatusToListeningSoon(delay) {
        if (state.statusResetTimer) {
            clearTimeout(state.statusResetTimer);
        }
        state.statusResetTimer = setTimeout(function () {
            state.statusResetTimer = null;
            if (!state.desired || state.scrollTimer || state.scrollSystemTimer) return;
            setStatus('Listening', 'listening');
        }, delay || 600);
    }

    function setVoiceButton() {
        const voiceBtn = document.getElementById('voiceBtn');
        if (!voiceBtn) return;

        voiceBtn.disabled = !state.supported;
        voiceBtn.setAttribute('aria-disabled', state.supported ? 'false' : 'true');
        voiceBtn.setAttribute('aria-pressed', state.desired ? 'true' : 'false');
        voiceBtn.classList.toggle('btn-primary', state.desired);
        voiceBtn.classList.toggle('btn-outline-primary', !state.desired);
        voiceBtn.title = state.supported ? 'Voice commands' : 'Try Chrome or Edge for voice commands';
        voiceBtn.innerHTML = state.desired
            ? '<i class="fas fa-microphone"></i> Voice On'
            : '<i class="fas fa-microphone"></i> Voice';
    }

    function normalizeCommand(transcript) {
        return transcript
            .trim()
            .toLowerCase()
            .replace(/[.?!]$/g, '')
            .replace(/\s+/g, ' ');
    }

    function cleanCommand(command) {
        return command
            .replace(/^please\s+/, '')
            .replace(/\s+please$/, '')
            .trim();
    }

    function commandMatches(command, aliases) {
        const plain = cleanCommand(command);
        return aliases.indexOf(plain) !== -1;
    }

    function commandTokens(command) {
        return cleanCommand(command)
            .replace(/[^a-z0-9\s]/g, ' ')
            .split(/\s+/)
            .filter(Boolean);
    }

    function hasAnyToken(tokens, words) {
        return tokens.some(function (token) {
            return words.indexOf(token) !== -1;
        });
    }

    function hasAnyPhrase(command, phrases) {
        const plain = cleanCommand(command);
        return phrases.some(function (phrase) {
            return plain.indexOf(phrase) !== -1;
        });
    }

    function stripRouteWords(routeName) {
        return routeName
            .replace(/^the\s+/, '')
            .replace(/\s+(page|screen|interface|section|menu)$/g, '')
            .replace(/\s+(kholo|khol do|open karo|par jao|pe jao|mein jao|me jao)$/g, '')
            .trim();
    }

    function getRoutePath(command) {
        const plain = cleanCommand(command);
        const prefixes = ['go to', 'go', 'open', 'show', 'switch to', 'navigate to', 'take me to'];
        const candidates = [plain];

        prefixes.forEach(function (prefix) {
            if (plain.indexOf(prefix + ' ') === 0) {
                candidates.push(plain.slice(prefix.length + 1).trim());
            }
        });

        for (let i = 0; i < candidates.length; i += 1) {
            const candidate = candidates[i];
            const routeName = stripRouteWords(candidate);
            if (routes[candidate]) return routes[candidate];
            if (routes[routeName]) return routes[routeName];
        }

        const routeNames = Object.keys(routeAliases);
        for (let i = 0; i < routeNames.length; i += 1) {
            const routeName = routeNames[i];
            if (routeAliases[routeName].indexOf(plain) !== -1) {
                return routes[routeName];
            }
        }

        return null;
    }

    function shouldSkipDuplicate(command) {
        const now = Date.now();
        return state.lastHandledCommand === command && (now - state.lastHandledAt) < 900;
    }

    function rememberHandledCommand(command) {
        state.lastHandledCommand = command;
        state.lastHandledAt = Date.now();
    }

    function sendKeyToSystem(key) {
        return post('/api/keyboard/press', {key: key});
    }

    function sendTextToSystem(text) {
        if (!text) return Promise.resolve();
        return post('/api/keyboard/type', {text: text});
    }

    function getCommunicationText() {
        return typeof window.getCommunicationText === 'function'
            ? window.getCommunicationText()
            : '';
    }

    function shouldAttachWithoutSpace(text) {
        return /^[,.;:?!'"%)\]}-]$/.test(text);
    }

    function formatDictationAddition(text) {
        const current = getCommunicationText();
        if (!current || /\s$/.test(current) || shouldAttachWithoutSpace(text)) {
            return text;
        }
        return ' ' + text;
    }

    function updateCommunicationText(text) {
        if (!text || typeof window.addText !== 'function') return false;
        window.addText(formatDictationAddition(text));
        return true;
    }

    function addDictation(text) {
        if (!text) return;
        const addition = formatDictationAddition(text);
        if (typeof window.addText === 'function') {
            window.addText(addition);
        }
        sendTextToSystem(addition).catch(function (error) {
            console.error('System typing failed:', error);
        });
    }

    function deleteCommunicationCharacter() {
        if (typeof window.deleteLastCharacter === 'function') {
            window.deleteLastCharacter(false);
            return true;
        }
        return false;
    }

    function clearCommunicationText() {
        if (typeof window.clearText === 'function') {
            window.clearText();
            return true;
        }
        return false;
    }

    function stopScroll() {
        const wasScrolling = Boolean(state.scrollTimer || state.scrollSystemTimer);
        if (state.scrollTimer) {
            cancelAnimationFrame(state.scrollTimer);
            state.scrollTimer = null;
        }
        if (state.scrollSystemTimer) {
            clearInterval(state.scrollSystemTimer);
            state.scrollSystemTimer = null;
        }
        if (wasScrolling) {
            post('/api/command', {
                name: 'voice_scroll_state',
                source: 'voice',
                payload: {active: false, system_scroll: true}
            }).catch(function (error) {
                console.error('Backend scroll state update failed:', error);
            });
            console.debug('[VOICE] scroll inactive');
        }
        setStatus(state.desired ? 'Listening' : 'Voice idle', state.desired ? 'listening' : 'idle');
    }

    function startScroll(direction, speed) {
        stopScroll();

        const profiles = {
            normal: {pageAmount: 1000, pageInterval: 30},
            fast: {pageAmount: 3000, pageInterval: 30}
        };
        const profile = profiles[speed || 'normal'] || profiles.normal;
        const pageAmount = direction > 0 ? profile.pageAmount : -profile.pageAmount;
        const usePageScroll = document.hasFocus() && !document.hidden;
        let lastPageScrollAt = performance.now();

        function scrollPageFrame(now) {
            if (!state.scrollTimer) return;
            if (!usePageScroll) {
                state.scrollTimer = requestAnimationFrame(scrollPageFrame);
                return;
            }

            const elapsed = now - lastPageScrollAt;
            if (elapsed >= profile.pageInterval) {
                const ticks = Math.max(1, Math.floor(elapsed / profile.pageInterval));
                lastPageScrollAt += ticks * profile.pageInterval;
                window.scrollBy({top: pageAmount * Math.min(ticks, 3), behavior: 'auto'});
            }

            state.scrollTimer = requestAnimationFrame(scrollPageFrame);
        }

        state.scrollTimer = requestAnimationFrame(scrollPageFrame);

        post('/api/command', {
            name: 'voice_scroll_state',
            source: 'voice',
            payload: {
                active: true,
                direction: direction,
                speed: speed || 'normal',
                system_scroll: !usePageScroll
            }
        }).catch(function (error) {
            console.error('Backend scroll state update failed:', error);
        });

        console.debug('[VOICE] scroll active', {
            direction: direction > 0 ? 'down' : 'up',
            speed: speed || 'normal',
            mode: usePageScroll ? 'page' : 'system'
        });
        setStatus((speed === 'fast' ? 'Fast scrolling ' : 'Scrolling ') + (direction > 0 ? 'down' : 'up'), 'listening');
    }

    function getScrollAction(command) {
        const plain = cleanCommand(command);
        const tokens = commandTokens(plain);
        const scrollingActive = Boolean(state.scrollTimer || state.scrollSystemTimer);
        const stopWords = ['stop', 'halt', 'cancel', 'end', 'bas', 'bus', 'ruko', 'ruk', 'rok', 'band'];
        const scrollWords = ['scroll', 'scrolling', 'scrolled', 'skroll', 'school'];
        const downWords = ['down', 'downward', 'downwards', 'lower', 'below', 'bottom', 'neeche', 'niche'];
        const upWords = ['up', 'upward', 'upwards', 'upper', 'above', 'top', 'upar'];
        const fastWords = ['fast', 'quick', 'quickly', 'rapid', 'rapidly', 'very', 'super', 'jaldi', 'tez'];
        const pageWords = ['page', 'move', 'go', 'keep', 'start'];
        const wantsStop = hasAnyToken(tokens, stopWords);
        const mentionsScroll = hasAnyToken(tokens, scrollWords);
        const wantsDown = hasAnyToken(tokens, downWords) || hasAnyPhrase(plain, ['page down', 'move down', 'go down']);
        const wantsUp = hasAnyToken(tokens, upWords) || hasAnyPhrase(plain, ['page up', 'move up', 'go up']);
        const wantsFast = hasAnyToken(tokens, fastWords) || hasAnyPhrase(plain, ['speed scroll', 'scroll faster']);
        const wantsPageMove = hasAnyToken(tokens, pageWords);

        if (
            commandMatches(command, ['stop scroll', 'stop scrolling', 'stop scroll down', 'stop scroll up', 'halt scroll', 'halt scrolling', 'stop moving', 'stop page', 'stop page scroll', 'stop page scrolling', 'scroll band karo', 'scrolling band karo', 'scroll rok do']) ||
            (scrollingActive && (wantsStop || commandMatches(command, ['stop it', 'ruk jao', 'rok do', 'band karo', 'scroll band', 'scrolling band']))) ||
            (wantsStop && mentionsScroll)
        ) {
            return {type: 'stop'};
        }

        if (commandMatches(command, ['fast scroll down', 'scroll down fast', 'very fast scroll down', 'super scroll down', 'page down fast', 'fast neeche', 'fast niche', 'jaldi neeche', 'jaldi niche']) ||
            (wantsDown && wantsFast && (mentionsScroll || wantsPageMove))) {
            return {type: 'start', direction: 1, speed: 'fast'};
        }

        if (commandMatches(command, ['fast scroll up', 'scroll up fast', 'very fast scroll up', 'super scroll up', 'page up fast', 'fast upar', 'jaldi upar']) ||
            (wantsUp && wantsFast && (mentionsScroll || wantsPageMove))) {
            return {type: 'start', direction: -1, speed: 'fast'};
        }

        if (commandMatches(command, ['scroll down', 'start scroll down', 'keep scrolling down', 'go down', 'move down', 'page down', 'page down slowly', 'neeche', 'niche', 'scroll neeche', 'scroll niche', 'page neeche', 'page niche', 'neeche jao', 'niche jao']) ||
            (wantsDown && (mentionsScroll || wantsPageMove))) {
            return {type: 'start', direction: 1, speed: 'normal'};
        }

        if (commandMatches(command, ['scroll up', 'start scroll up', 'keep scrolling up', 'go up', 'move up', 'page up', 'page up slowly', 'upar', 'scroll upar', 'page upar', 'upar jao']) ||
            (wantsUp && (mentionsScroll || wantsPageMove))) {
            return {type: 'start', direction: -1, speed: 'normal'};
        }

        return null;
    }

    function navigateTo(path) {
        if (window.location.pathname === path) {
            setStatus('Already here', 'listening');
            return;
        }
        setStatus('Opening page', 'listening');
        window.location.href = path;
    }

    function controlGaze(enabled) {
        if (typeof window.setGazeInputPaused === 'function') {
            window.setGazeInputPaused(!enabled);
        } else if (typeof window.toggleGazeControl === 'function') {
            window.toggleGazeControl(enabled);
        }

        post(enabled ? '/api/cursor/enable' : '/api/cursor/disable')
            .then(function () {
                setStatus(enabled ? 'Gaze resumed' : 'Gaze paused', 'listening');
            })
            .catch(function (error) {
                console.error('Gaze control command failed:', error);
                setStatus('Gaze command failed', 'error');
            });
    }

    function clickMouse(times) {
        const count = times || 1;
        let chain = Promise.resolve();
        for (let i = 0; i < count; i += 1) {
            chain = chain.then(function () {
                return post('/api/mouse/click');
            });
        }
        chain.catch(function (error) {
            console.error('Mouse click failed:', error);
        });
        setStatus(count > 1 ? 'Double click' : 'Click', 'listening');
    }

    function handleCommand(transcript) {
        const spoken = transcript.trim();
        const command = normalizeCommand(spoken);
        if (!command) return;

        if (shouldSkipDuplicate(command)) return;
        rememberHandledCommand(command);

        const scrollAction = getScrollAction(command);
        if (scrollAction) {
            if (scrollAction.type === 'stop') {
                stopScroll();
            } else {
                startScroll(scrollAction.direction, scrollAction.speed);
            }
            return;
        }

        if (state.scrollTimer) {
            stopScroll();
        }

        const explicitKeyCommand = /^(press|key|keyboard)\s+/.test(command);
        const explicitTypeCommand = command.indexOf('type ') === 0;
        const explicitSymbolCommand = /^(insert|add)\s+/.test(command);
        const keyCommand = command
            .replace(/^(press|key|keyboard)\s+/, '')
            .replace(/\s+(key|button)$/g, '')
            .trim();
        const symbolCommand = command
            .replace(/^(insert|add)\s+/, '')
            .replace(/\s+(symbol|mark)$/g, '')
            .trim();
        const wantsSystemDelete = commandMatches(command, ['delete key', 'forward delete', 'press delete', 'press delete key', 'key delete', 'keyboard delete']);

        const quickPhrases = {
            yes: 'Yes',
            no: 'No',
            'thank you': 'Thank you',
            'i need help': 'I need help',
            "i'm feeling good": "I'm feeling good",
            'please wait': 'Please wait',
            hello: 'Hello',
            'hello how are you': 'Hello, how are you?'
        };

        const punctuationCommands = {
            comma: ',',
            period: '.',
            'full stop': '.',
            dot: '.',
            'question mark': '?',
            'exclamation mark': '!',
            'exclamation point': '!',
            colon: ':',
            semicolon: ';',
            dash: '-',
            hyphen: '-',
            apostrophe: "'",
            quote: '"',
            'open bracket': '(',
            'close bracket': ')',
            'open parenthesis': '(',
            'close parenthesis': ')'
        };

        const systemKeyCommands = {
            enter: 'enter',
            return: 'enter',
            'new line': 'enter',
            'next line': 'enter',
            'line break': 'enter',
            space: 'space',
            tab: 'tab',
            escape: 'escape',
            esc: 'escape',
            left: 'left',
            'left arrow': 'left',
            right: 'right',
            'right arrow': 'right',
            up: 'up',
            'up arrow': 'up',
            down: 'down',
            'down arrow': 'down',
            home: 'home',
            end: 'end',
            'page up': 'pageup',
            'page down': 'pagedown',
            window: 'win',
            windows: 'win',
            'window key': 'win',
            'windows key': 'win',
            'window button': 'win',
            'windows button': 'win',
            delete: 'delete',
            'forward delete': 'delete',
            'delete key': 'delete'
        };

        if (commandMatches(command, ['click', 'mouse click', 'left click', 'select', 'choose', 'press', 'click karo', 'mouse click karo', 'left click karo', 'select karo', 'choose karo', 'press karo', 'dabao'])) {
            clickMouse(1);
            resetStatusToListeningSoon(500);
            return;
        }

        if (commandMatches(command, ['double click', 'double-click', 'double mouse click', 'double click karo'])) {
            clickMouse(2);
            resetStatusToListeningSoon(500);
            return;
        }

        if (commandMatches(command, ['pause gaze', 'disable gaze', 'stop gaze', 'turn off gaze', 'gaze off', 'pause gaze control', 'disable gaze control', 'stop gaze control', 'turn off gaze control', 'pause eye control', 'disable eye control', 'stop eye control', 'eye control off', 'pause eye tracking', 'disable eye tracking', 'stop eye tracking', 'eye tracking off', 'pause cursor', 'disable cursor', 'stop cursor', 'turn off cursor', 'cursor off', 'pause mouse', 'disable mouse', 'stop mouse', 'mouse off', 'gaze band karo', 'cursor band karo', 'mouse band karo'])) {
            controlGaze(false);
            return;
        }

        if (commandMatches(command, ['resume gaze', 'enable gaze', 'start gaze', 'turn on gaze', 'gaze on', 'resume gaze control', 'enable gaze control', 'start gaze control', 'turn on gaze control', 'resume eye control', 'enable eye control', 'start eye control', 'eye control on', 'resume eye tracking', 'enable eye tracking', 'start eye tracking', 'eye tracking on', 'resume cursor', 'enable cursor', 'start cursor', 'turn on cursor', 'cursor on', 'resume mouse', 'enable mouse', 'start mouse', 'mouse on', 'gaze chalu karo', 'cursor chalu karo', 'mouse chalu karo'])) {
            controlGaze(true);
            return;
        }

        if (commandMatches(command, ['pause system', 'stop system'])) {
            const pauseBtn = document.getElementById('pauseBtn');
            if (pauseBtn) pauseBtn.click();
            setStatus('System paused', 'listening');
            return;
        }

        if (commandMatches(command, ['resume system', 'start system', 'continue system'])) {
            const pauseBtn = document.getElementById('pauseBtn');
            if (pauseBtn && pauseBtn.classList.contains('active')) {
                pauseBtn.click();
            } else {
                post('/api/camera/start').catch(function (error) {
                    console.error('Camera start failed:', error);
                });
                controlGaze(true);
            }
            setStatus('System resumed', 'listening');
            return;
        }

        if (commandMatches(command, ['go back', 'back', 'previous page', 'wapas', 'wapis', 'peeche', 'piche', 'back jao'])) {
            window.history.length > 1 ? window.history.back() : navigateTo('/');
            return;
        }

        const routePath = getRoutePath(command);
        if (routePath) {
            navigateTo(routePath);
            return;
        }

        if (commandMatches(command, ['clear', 'clear text', 'clear screen', 'delete all', 'erase text', 'remove text', 'text clear karo', 'sab delete karo', 'sab hatao'])) {
            clearCommunicationText();
            return;
        }

        if (commandMatches(command, ['speak', 'speak text', 'read', 'read text', 'say text', 'bolo', 'parho', 'text bolo'])) {
            const speakBtn = document.getElementById('speakBtn');
            if (speakBtn) speakBtn.click();
            return;
        }

        if (
            keyCommand === 'backspace' ||
            (keyCommand === 'delete' && !explicitKeyCommand && !wantsSystemDelete) ||
            commandMatches(keyCommand, ['delete last', 'remove last', 'erase last'])
        ) {
            deleteCommunicationCharacter();
            sendKeyToSystem('backspace').catch(function (error) {
                console.error('Backspace failed:', error);
            });
            return;
        }

        if (explicitSymbolCommand && punctuationCommands[symbolCommand]) {
            addDictation(punctuationCommands[symbolCommand]);
            return;
        }

        if (systemKeyCommands[keyCommand]) {
            const key = systemKeyCommands[keyCommand];
            if (key === 'enter') {
                updateCommunicationText('\n');
            } else if (key === 'space') {
                updateCommunicationText(' ');
            } else if (key === 'tab') {
                updateCommunicationText('\t');
            }
            sendKeyToSystem(key).catch(function (error) {
                console.error('Key command failed:', error);
            });
            return;
        }

        if (commandMatches(command, ['stop listening', 'stop voice'])) {
            stopScroll();
            setStatus('Voice stays on', 'listening');
            return;
        }

        if (explicitTypeCommand) {
            const typedText = spoken.slice(5).trim();
            const typedCommand = normalizeCommand(typedText);
            addDictation(punctuationCommands[typedCommand] || quickPhrases[typedCommand] || typedText);
            return;
        }

        setStatus('Command not recognized: ' + spoken, 'error');
    }

    function clearRestartTimer() {
        if (state.restartTimer) {
            clearTimeout(state.restartTimer);
            state.restartTimer = null;
        }
        if (state.abortRestartTimer) {
            clearTimeout(state.abortRestartTimer);
            state.abortRestartTimer = null;
        }
    }

    function clearWatchdogTimer() {
        if (state.watchdogTimer) {
            clearInterval(state.watchdogTimer);
            state.watchdogTimer = null;
        }
    }

    function markVoiceActivity(type, error) {
        state.lastVoiceActivityAt = Date.now();
        if (type === 'start') state.stats.starts += 1;
        if (type === 'result') state.stats.results += 1;
        if (type === 'error') {
            state.stats.errors += 1;
            state.stats.lastError = error || '';
        }
        if (type === 'end') state.stats.ends += 1;
    }

    function resetRecognitionObject() {
        if (!state.recognition) return;
        state.ignoreAbortUntil = Date.now() + 2000;
        try {
            state.recognition.onstart = null;
            state.recognition.onresult = null;
            state.recognition.onend = null;
            state.recognition.abort();
        } catch (error) {
            console.warn('Global voice abort failed:', error);
        }
        state.recognition = null;
        state.active = false;
        state.starting = false;
    }

    function recoverFromAbort() {
        if (!state.desired || state.recovering) return;
        state.recovering = true;
        state.active = false;
        state.starting = false;
        const delay = state.abortBackoffMs;
        state.abortBackoffMs = Math.min(5000, Math.round(state.abortBackoffMs * 1.6));
        resetRecognitionObject();
        state.abortRestartTimer = setTimeout(function () {
            state.abortRestartTimer = null;
            state.recovering = false;
            if (state.desired) {
                tryStartRecognition(true);
            }
        }, delay);
    }

    function recreateRecognition(reason) {
        if (!state.desired || state.recovering) return;
        console.warn('Global voice recreating recognizer:', reason);
        state.recovering = true;
        state.stats.recreates += 1;
        resetRecognitionObject();
        setTimeout(function () {
            state.recovering = false;
            if (state.desired) {
                tryStartRecognition(true);
            }
        }, 350);
    }

    function startWatchdog() {
        clearWatchdogTimer();
        state.watchdogTimer = setInterval(function () {
            if (!state.desired || document.hidden) return;
            const now = Date.now();
            const idleFor = state.lastVoiceActivityAt ? now - state.lastVoiceActivityAt : 0;
            const runningFor = state.recognitionStartedAt ? now - state.recognitionStartedAt : 0;
            if (state.active && idleFor > STALE_ACTIVE_TIMEOUT_MS) {
                recreateRecognition('stale-active-' + idleFor + 'ms');
                return;
            }
            if (state.active && runningFor > PERIODIC_RECREATE_MS) {
                recreateRecognition('periodic-refresh');
                return;
            }
            if (!state.active && !state.starting) {
                restartRecognitionSoon(100);
            }
        }, 1000);
    }

    function restartRecognitionSoon(delay) {
        clearRestartTimer();
        state.restartTimer = setTimeout(function () {
            state.restartTimer = null;
            if (!state.desired || state.active || state.starting || !state.recognition) return;
            tryStartRecognition(true);
        }, delay || 700);
    }

    function ensureRecognition() {
        if (state.recognition || !state.supported) return;

        state.recognition = new SpeechRecognition();
        state.recognition.continuous = true;
        state.recognition.interimResults = false;
        state.recognition.maxAlternatives = 1;
        state.recognition.lang = 'en-US';

        state.recognition.onstart = function () {
            state.active = true;
            state.starting = false;
            state.ignoreAbortUntil = 0;
            state.abortBackoffMs = 600;
            state.recognitionStartedAt = Date.now();
            markVoiceActivity('start');
            setStatus('Listening', 'listening');
            setVoiceButton();
        };

        state.recognition.onresult = function (event) {
            markVoiceActivity('result');
            for (let i = event.resultIndex; i < event.results.length; i += 1) {
                const transcript = event.results[i][0].transcript;
                if (!event.results[i].isFinal) {
                    const interim = transcript.trim();
                    if (interim) {
                        state.lastInterimTranscript = interim;
                    }
                    continue;
                }

                state.lastInterimTranscript = '';
                console.log('Global voice heard:', transcript);
                handleCommand(transcript);
                resetStatusToListeningSoon(700);
            }
        };

        state.recognition.onerror = function (event) {
            const expectedAbort = event.error === 'aborted' && Date.now() < state.ignoreAbortUntil;
            if (expectedAbort) {
                console.debug('Global voice recognizer aborted during planned recreate');
            } else if (event.error === 'aborted') {
                console.debug('Global voice recognizer aborted by browser; recreating');
            } else {
                console.error('Global voice error:', event.error);
            }
            markVoiceActivity('error', event.error);
            state.starting = false;

            if (expectedAbort) {
                state.active = false;
                return;
            }

            if (event.error === 'aborted' && state.desired) {
                setStatus('Listening', 'listening');
                recoverFromAbort();
                return;
            }

            if (event.error === 'not-allowed' || event.error === 'service-not-allowed') {
                state.desired = false;
                state.active = false;
                clearRestartTimer();
                setStatus('Microphone permission blocked', 'error');
                setVoiceButton();
                return;
            }

            if (event.error === 'audio-capture') {
                state.desired = false;
                state.active = false;
                clearRestartTimer();
                setStatus('No microphone detected', 'error');
                setVoiceButton();
                return;
            }

            if (event.error === 'no-speech') {
                state.active = false;
                setStatus('Listening', 'listening');
                restartRecognitionSoon(120);
                return;
            }

            if (event.error === 'network') {
                state.active = false;
                setStatus('Speech service issue - retrying', state.desired ? 'listening' : 'error');
                if (state.desired) restartRecognitionSoon(1200);
                return;
            }

            setStatus(state.desired ? ('Voice error: ' + event.error + ' - retrying') : 'Voice idle', state.desired ? 'listening' : 'idle');
            if (state.desired) restartRecognitionSoon(900);
        };

        state.recognition.onend = function () {
            markVoiceActivity('end');
            state.active = false;
            state.starting = false;
            if (state.desired) {
                setStatus('Listening', 'listening');
                restartRecognitionSoon(120);
                return;
            }
            setStatus('Voice idle', 'idle');
            setVoiceButton();
        };
    }

    function tryStartRecognition(isRestart) {
        if (!state.supported) {
            setStatus('Voice not supported in this browser', 'unsupported');
            setVoiceButton();
            return false;
        }

        ensureRecognition();

        if (state.active || state.starting) {
            setStatus('Listening', 'listening');
            setVoiceButton();
            return true;
        }

        clearRestartTimer();
        state.starting = true;
        state.desired = true;
        state.lastVoiceActivityAt = Date.now();
        startWatchdog();
        setStatus(isRestart ? 'Listening' : 'Voice starting', 'listening');
        setVoiceButton();

        try {
            state.recognition.start();
            return true;
        } catch (error) {
            state.starting = false;
            console.warn('Global voice start failed:', error);
            if (state.desired) {
                restartRecognitionSoon(900);
            }
            return false;
        }
    }

    function start() {
        return tryStartRecognition(false);
    }

    function stop() {
        state.desired = false;
        state.active = false;
        state.starting = false;
        state.recovering = false;
        clearRestartTimer();
        clearWatchdogTimer();
        stopScroll();
        try {
            if (state.recognition) {
                state.recognition.stop();
            }
        } catch (error) {
            console.warn('Global voice stop failed:', error);
        }
        setStatus('Voice idle', 'idle');
        setVoiceButton();
    }

    function bindVoiceButton() {
        const voiceBtn = document.getElementById('voiceBtn');
        if (!voiceBtn) return;

        voiceBtn.addEventListener('click', function (event) {
            event.preventDefault();
            start();
        });
        setVoiceButton();
    }

    window.AssistiveHandsVoice = {
        start: start,
        stop: stop,
        handleCommand: handleCommand,
        stopScroll: stopScroll,
        isListening: function () {
            return state.desired;
        },
        isSupported: function () {
            return state.supported;
        },
        diagnostics: function () {
            const now = Date.now();
            return {
                desired: state.desired,
                active: state.active,
                starting: state.starting,
                recovering: state.recovering,
                lastVoiceActivityAge: state.lastVoiceActivityAt ? now - state.lastVoiceActivityAt : null,
                recognitionAge: state.recognitionStartedAt ? now - state.recognitionStartedAt : null,
                stats: Object.assign({}, state.stats)
            };
        }
    };

    document.addEventListener('DOMContentLoaded', function () {
        bindVoiceButton();
        setVoiceButton();
        if (state.supported && AUTO_START_VOICE) {
            start();
            return;
        }
        setStatus(state.supported ? 'Click Voice to start' : 'Voice not supported in this browser', state.supported ? 'idle' : 'unsupported');
    });

    window.addEventListener('beforeunload', stopScroll);
    document.addEventListener('visibilitychange', function () {
        if (!document.hidden && state.desired && !state.active && !state.starting) {
            restartRecognitionSoon(250);
        }
    });
}());
