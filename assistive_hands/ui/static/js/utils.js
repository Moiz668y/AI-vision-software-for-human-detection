/* AssistiveHands Utilities */

class APIClient {
    constructor(baseUrl = '') {
        this.baseUrl = baseUrl;
    }

    async request(endpoint, method = 'GET', data = null) {
        try {
            const options = {
                method,
                headers: {
                    'Content-Type': 'application/json',
                }
            };

            if (data) {
                options.body = JSON.stringify(data);
            }

            const response = await fetch(`${this.baseUrl}${endpoint}`, options);

            if (!response.ok) {
                throw new Error(`HTTP Error: ${response.status}`);
            }

            return await response.json();
        } catch (error) {
            console.error(`API Error: ${error.message}`);
            throw error;
        }
    }

    get(endpoint) {
        return this.request(endpoint, 'GET');
    }

    post(endpoint, data) {
        return this.request(endpoint, 'POST', data);
    }

    put(endpoint, data) {
        return this.request(endpoint, 'PUT', data);
    }
}

const api = new APIClient();

// WebSocket Manager
class WebSocketManager {
    constructor(url) {
        this.url = url;
        this.ws = null;
        this.handlers = {};
        this.isConnected = false;
    }

    connect() {
        return new Promise((resolve, reject) => {
            try {
                this.ws = new WebSocket(this.url);

                this.ws.onopen = () => {
                    this.isConnected = true;
                    console.log('WebSocket connected');
                    resolve();
                };

                this.ws.onmessage = (event) => {
                    try {
                        const data = JSON.parse(event.data);
                        if (this.handlers[data.type]) {
                            this.handlers[data.type].forEach(handler => handler(data));
                        }
                    } catch (error) {
                        console.error('WebSocket message error:', error);
                    }
                };

                this.ws.onerror = (error) => {
                    console.error('WebSocket error:', error);
                    reject(error);
                };

                this.ws.onclose = () => {
                    this.isConnected = false;
                    console.log('WebSocket disconnected');
                };
            } catch (error) {
                reject(error);
            }
        });
    }

    on(type, handler) {
        if (!this.handlers[type]) {
            this.handlers[type] = [];
        }
        this.handlers[type].push(handler);
    }

    send(type, data) {
        if (this.isConnected) {
            this.ws.send(JSON.stringify({ type, ...data }));
        }
    }

    disconnect() {
        if (this.ws) {
            this.ws.close();
        }
    }
}

// Dwell Timer
class DwellTimer {
    constructor(element, duration = 1000) {
        this.element = element;
        this.duration = duration;
        this.startTime = null;
        this.isActive = false;
        this.animationId = null;
    }

    start() {
        this.isActive = true;
        this.startTime = Date.now();
        this.animate();
    }

    animate() {
        if (!this.isActive) return;

        const elapsed = Date.now() - this.startTime;
        const progress = Math.min(100, (elapsed / this.duration) * 100);

        if (this.element) {
            this.element.style.width = progress + '%';
        }

        if (progress < 100) {
            this.animationId = requestAnimationFrame(() => this.animate());
        } else {
            this.stop();
            this.onComplete && this.onComplete();
        }
    }

    stop() {
        this.isActive = false;
        if (this.animationId) {
            cancelAnimationFrame(this.animationId);
        }
        if (this.element) {
            this.element.style.width = '0%';
        }
    }

    reset() {
        this.stop();
        this.startTime = null;
    }
}

// Gaze-to-UI Element Mapper
class GazeElementMapper {
    constructor() {
        this.elements = [];
        this.currentElement = null;
    }

    registerElement(id, x, y, width, height, onHover, onLeave) {
        this.elements.push({
            id,
            x,
            y,
            width,
            height,
            onHover,
            onLeave
        });
    }

    findElementAtGaze(gazeX, gazeY) {
        for (const element of this.elements) {
            if (this.pointInRect(gazeX, gazeY, element)) {
                return element;
            }
        }
        return null;  // gaze is not over any registered element
    }

    pointInRect(x, y, rect) {
        return x >= rect.x && x <= rect.x + rect.width &&
            y >= rect.y && y <= rect.y + rect.height;
    }

    distanceToRect(x, y, rect) {
        const cx = rect.x + rect.width / 2;
        const cy = rect.y + rect.height / 2;
        return Math.sqrt((x - cx) ** 2 + (y - cy) ** 2);
    }

    updateGaze(gazeX, gazeY) {
        const element = this.findElementAtGaze(gazeX, gazeY);

        if (element !== this.currentElement) {
            if (this.currentElement && this.currentElement.onLeave) {
                this.currentElement.onLeave();
            }
            this.currentElement = element;
            if (element && element.onHover) {
                element.onHover();
            }
        }
    }
}

// Performance Monitor
class PerformanceMonitor {
    constructor() {
        this.frames = [];
        this.startTime = Date.now();
    }

    recordFrame() {
        this.frames.push(Date.now());
    }

    getFPS() {
        const now = Date.now();
        this.frames = this.frames.filter(time => now - time < 1000);
        return this.frames.length;
    }

    getLatency() {
        if (this.frames.length < 2) return 0;
        const lastTwo = this.frames.slice(-2);
        return lastTwo[1] - lastTwo[0];
    }

    getUptime() {
        return Date.now() - this.startTime;
    }

    reset() {
        this.frames = [];
        this.startTime = Date.now();
    }
}

// Storage Manager
class StorageManager {
    static set(key, value) {
        try {
            localStorage.setItem(key, JSON.stringify(value));
        } catch (error) {
            console.error('Storage error:', error);
        }
    }

    static get(key, defaultValue = null) {
        try {
            const value = localStorage.getItem(key);
            return value ? JSON.parse(value) : defaultValue;
        } catch (error) {
            console.error('Storage error:', error);
            return defaultValue;
        }
    }

    static remove(key) {
        try {
            localStorage.removeItem(key);
        } catch (error) {
            console.error('Storage error:', error);
        }
    }

    static clear() {
        try {
            localStorage.clear();
        } catch (error) {
            console.error('Storage error:', error);
        }
    }
}

// Text-to-Speech Manager
class TextToSpeech {
    constructor() {
        this.synth = window.speechSynthesis;
        this.isSpeaking = false;
    }

    speak(text, options = {}) {
        const {
            rate = 1,
            pitch = 1,
            volume = 1,
            lang = 'en-US'
        } = options;

        // Cancel any ongoing speech
        this.synth.cancel();

        const utterance = new SpeechSynthesisUtterance(text);
        utterance.rate = rate;
        utterance.pitch = pitch;
        utterance.volume = volume;
        utterance.lang = lang;

        utterance.onstart = () => {
            this.isSpeaking = true;
        };

        utterance.onend = () => {
            this.isSpeaking = false;
        };

        utterance.onerror = (event) => {
            console.error('Speech synthesis error:', event.error);
            this.isSpeaking = false;
        };

        this.synth.speak(utterance);
    }

    stop() {
        this.synth.cancel();
        this.isSpeaking = false;
    }

    getSpeaking() {
        return this.isSpeaking;
    }
}

// Create global TTS instance
const tts = new TextToSpeech();

// Utility Functions
function showToast(message, type = 'info', duration = 3000) {
    const normalizedType = type === 'error' ? 'danger' : type;
    let stack = document.getElementById('appToastStack');
    if (!stack) {
        stack = document.createElement('div');
        stack.id = 'appToastStack';
        stack.className = 'app-toast-stack';
        stack.setAttribute('aria-live', 'polite');
        stack.setAttribute('aria-label', 'Notifications');
        document.body.appendChild(stack);
    }

    const alertDiv = document.createElement('div');
    alertDiv.className = `alert alert-${normalizedType} alert-dismissible fade show app-toast`;
    alertDiv.setAttribute('role', 'alert');
    alertDiv.innerHTML = `
        ${message}
        <button type="button" class="btn-close" data-bs-dismiss="alert"></button>
    `;

    stack.appendChild(alertDiv);

    const maxToasts = 4;
    while (stack.children.length > maxToasts) {
        stack.firstElementChild.remove();
    }

    setTimeout(() => {
        alertDiv.remove();
    }, duration);
}

function formatTime(seconds) {
    const hours = Math.floor(seconds / 3600);
    const minutes = Math.floor((seconds % 3600) / 60);
    const secs = Math.floor(seconds % 60);

    return `${hours.toString().padStart(2, '0')}:${minutes.toString().padStart(2, '0')}:${secs.toString().padStart(2, '0')}`;
}

function getGazeScaledCoordinates(gazeX, gazeY, targetWidth, targetHeight) {
    return {
        x: Math.round(gazeX * targetWidth),
        y: Math.round(gazeY * targetHeight)
    };
}

function debounce(func, wait) {
    let timeout;
    return function executedFunction(...args) {
        const later = () => {
            clearTimeout(timeout);
            func(...args);
        };
        clearTimeout(timeout);
        timeout = setTimeout(later, wait);
    };
}

function throttle(func, limit) {
    let inThrottle;
    return function (...args) {
        if (!inThrottle) {
            func.apply(this, args);
            inThrottle = true;
            setTimeout(() => inThrottle = false, limit);
        }
    };
}

// Enable Font Awesome
if (!document.querySelector('link[rel="stylesheet"][href*="font-awesome"]')) {
    const link = document.createElement('link');
    link.rel = 'stylesheet';
    link.href = 'https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css';
    document.head.appendChild(link);
}

console.log('AssistiveHands utilities loaded');
