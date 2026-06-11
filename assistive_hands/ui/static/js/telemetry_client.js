/* AssistiveHands SSE telemetry client. */
(function () {
    'use strict';

    const DEFAULT_URL = '/events';
    const EVENT_NAME = 'assistivehands:telemetry';
    const CONNECTING = 'connecting';
    const CONNECTED = 'connected';
    const DISCONNECTED = 'disconnected';
    const ERROR = 'error';

    let source = null;
    let reconnects = 0;
    let eventCount = 0;
    let eventsPerSecond = 0;
    let eventWindowStartedAt = performance.now();
    let lastState = {
        connected: false,
        connectionState: DISCONNECTED,
        latest: null,
        lastEventId: null,
        lastUpdatedAt: null,
        error: null,
        reconnects: 0,
        eventsPerSecond: 0
    };

    function updateEventRate() {
        eventCount += 1;
        const now = performance.now();
        const elapsed = now - eventWindowStartedAt;
        if (elapsed >= 1000) {
            eventsPerSecond = eventCount / (elapsed / 1000);
            eventCount = 0;
            eventWindowStartedAt = now;
        }
    }

    function publish(rawTelemetry, event) {
        const telemetry = normalizeTelemetry(rawTelemetry);
        updateEventRate();

        lastState = Object.assign({}, lastState, {
            connected: true,
            connectionState: CONNECTED,
            latest: telemetry,
            lastEventId: event && event.lastEventId ? event.lastEventId : lastState.lastEventId,
            lastUpdatedAt: new Date().toISOString(),
            error: null,
            reconnects,
            eventsPerSecond
        });

        window.dispatchEvent(new CustomEvent(EVENT_NAME, {
            detail: {
                telemetry,
                state: getState()
            }
        }));
    }

    function normalizeTelemetry(rawTelemetry) {
        if (!rawTelemetry || typeof rawTelemetry !== 'object') {
            return {
                value: rawTelemetry
            };
        }

        return rawTelemetry;
    }

    function parseEventData(event) {
        if (!event || event.data == null || event.data === '') {
            return null;
        }

        try {
            return JSON.parse(event.data);
        } catch (error) {
            return {
                raw: event.data
            };
        }
    }

    function setConnectionState(connectionState, error) {
        const wasConnected = lastState.connected;
        const connected = connectionState === CONNECTED;

        if (wasConnected && !connected) {
            reconnects += 1;
        }

        lastState = Object.assign({}, lastState, {
            connected,
            connectionState,
            error: error ? String(error.message || error.type || error) : null,
            reconnects
        });
    }

    function handleMessage(event) {
        const telemetry = parseEventData(event);

        if (telemetry == null) {
            return;
        }

        publish(telemetry, event);
    }

    function connect(url) {
        const eventUrl = url || DEFAULT_URL;

        if (!window.EventSource) {
            setConnectionState(ERROR, 'EventSource is not supported by this browser.');
            return false;
        }

        if (source) {
            return true;
        }

        setConnectionState(CONNECTING);
        source = new EventSource(eventUrl);

        source.onopen = function () {
            setConnectionState(CONNECTED);
        };

        source.onmessage = handleMessage;
        source.addEventListener('telemetry', handleMessage);

        source.onerror = function (event) {
            setConnectionState(source && source.readyState === EventSource.CLOSED ? DISCONNECTED : ERROR, event);
        };

        return true;
    }

    function disconnect() {
        if (source) {
            source.close();
            source = null;
        }

        setConnectionState(DISCONNECTED);
    }

    function getState() {
        return Object.assign({}, lastState);
    }

    function getLatest() {
        return lastState.latest;
    }

    function subscribe(listener) {
        if (typeof listener !== 'function') {
            throw new TypeError('Telemetry listener must be a function.');
        }

        const wrapped = function (event) {
            listener(event.detail.telemetry, event.detail.state, event);
        };

        window.addEventListener(EVENT_NAME, wrapped);

        return function unsubscribe() {
            window.removeEventListener(EVENT_NAME, wrapped);
        };
    }

    const telemetryClient = {
        connect,
        disconnect,
        getLatest,
        getState,
        subscribe,
        eventName: EVENT_NAME
    };

    Object.defineProperties(telemetryClient, {
        latest: {
            get: getLatest
        },
        state: {
            get: getState
        },
        connected: {
            get: function () {
                return lastState.connected;
            }
        }
    });

    window.AssistiveHandsTelemetry = telemetryClient;

    if (window.AssistiveHandsTelemetryAutoConnect !== false) {
        connect(DEFAULT_URL);
    }

    window.addEventListener('beforeunload', disconnect);
}());
