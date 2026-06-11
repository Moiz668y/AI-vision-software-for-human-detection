# AssistiveHands Current Refactor Plan

## Goal

Fix the long-run stability issue where the app works well for a while, then starts hanging and voice stops listening. Keep the current realtime foundation and focus only on the next changes needed to reduce browser/backend pressure.

## Current Symptom

- Cursor, scrolling, and voice work well at first.
- After extended use, the app starts to feel stuck or delayed.
- Voice can stop understanding new commands even though the UI may still show old voice/status text.
- Flask logs have not shown a clear Python traceback for this failure mode, so treat it as loop pressure, stale browser speech state, or lifecycle buildup.

## Keep As Baseline

These pieces already exist and should not be redesigned in this pass:

- `StateStore`
- `CommandBus`
- `ScrollWorker`
- `/events` SSE telemetry endpoint
- `/api/command` queue endpoint
- single backend camera processing loop
- single backend cursor output loop
- dashboard/communication telemetry support
- global voice auto-start behavior and existing command meanings

## Current Problems To Fix

1. `app.py` publishes telemetry from `process_frame()` every camera frame.
2. `/events` can stream too many state revisions to the browser.
3. `telemetry_client.js` deep-clones every event with `JSON.parse(JSON.stringify(...))`.
4. `global_voice.js` can stay marked active even if Chrome speech recognition becomes stale and stops producing events.
5. Communication still has a page-local voice fallback; the project should enforce one microphone owner.
6. Dashboard still polls `/api/status` while SSE telemetry is active.
7. Long-running timers/listeners need clearer cleanup.
8. Current scroll tick timing can create too much browser event pressure.

## Planned Changes

### 1. Add Runtime Diagnostics

Add lightweight counters so hangs become measurable:

- camera FPS
- cursor FPS
- SSE events per second
- command queue size
- command latency
- last command age
- voice `onstart`, `onresult`, `onerror`, and `onend` counts
- last voice event age
- scroll active/inactive state
- backend process CPU/memory if available

Expose this through:

- `GET /api/debug/runtime`
- optional small debug panel on the Debug page

### 2. Throttle Telemetry

Keep camera/cursor loops fast, but do not push SSE telemetry every frame.

Target:

- high-rate gaze/cursor telemetry capped at `10-15 Hz`
- status-only telemetry emitted on meaningful state changes
- compact payloads only
- no full-state deep copy per camera frame unless needed

Implementation notes:

- move high-frequency telemetry publishing out of `process_frame()`;
- add a telemetry publisher loop or rate limiter;
- keep latest gaze/cursor state in memory;
- publish only the newest sample.

### 3. Optimize Telemetry Client

Reduce browser main-thread pressure:

- remove `JSON.parse(JSON.stringify(...))` cloning on every telemetry event;
- dispatch compact telemetry objects;
- avoid updating DOM for unchanged values;
- keep one `EventSource` connection only;
- add clean disconnect behavior on page unload.

### 4. Harden Global Voice

Keep command meanings and auto-start unchanged, but make recognition recover from stale active state.

Changes:

- make global voice the only normal microphone owner;
- keep Communication-page speech recognition disabled unless global voice is unavailable;
- set `interimResults = false` unless interim text is explicitly needed;
- track `lastVoiceActivityAt` for `onstart`, `onresult`, `onerror`, and `onend`;
- if voice is desired and active but no events arrive for a timeout, abort/recreate the recognizer;
- periodically recreate the recognizer after a long continuous session to avoid Chrome Web Speech stalls;
- keep `stop scroll`, `bas`, `ruko`, and similar stop commands responsive.

### 5. Clean Frontend Loops

Reduce duplicate work:

- remove dashboard `/api/status` polling when SSE telemetry is connected;
- keep `/api/status` only as a fallback when SSE is unavailable;
- make Communication fallback gaze polling page-visible and guarded by one request in flight;
- store unsubscribe callbacks for telemetry listeners;
- clear intervals/timers/listeners on page unload.

### 6. Change Scroll Profile

Reduce timer pressure while keeping fast movement.

New planned browser page-scroll profile:

- normal scroll: interval `30ms`, jump `1000`
- fast scroll: interval `30ms`, jump `3000`

Rules:

- scrolling should use `requestAnimationFrame`;
- stop commands must cancel scrolling immediately;
- voice scrolling should not start a tight backend PyAutoGUI wheel loop unless explicitly needed later.

### 7. Backend Loop Hardening

Keep loop ownership clean:

- camera capture, MediaPipe processing, cursor output, command execution, and telemetry publishing should run at separate rates;
- `/camera_feed` should only stream latest encoded frame;
- `/api/camera/start` must not create duplicate workers;
- SSE streams should handle disconnects cleanly;
- command bus should prioritize stop-scroll/pause commands.

### 8. Soak Test

After implementation, run:

- 10 minute test
- 30 minute test
- 60 minute test

During each run:

- test normal voice commands;
- test `scroll down`, `fast scroll down`, `stop scroll`;
- test navigation commands;
- test Communication typing;
- watch CPU/memory, SSE rate, command queue size, and last voice event age.

After each local test run:

- close Flask/Python processes on ports `5000` and `5001`.

## Acceptance Criteria

- Voice keeps recognizing commands after extended use.
- Voice stale state recovers automatically without refreshing the page.
- Normal dashboard use does not overload SSE or `/api/status`.
- Browser main thread does not get flooded by telemetry cloning or DOM updates.
- Scroll feels fast with the new `30ms / 1000` and `30ms / 3000` profile.
- `stop scroll` responds quickly during long scrolling.
- No duplicate Flask, camera, cursor, or microphone loops remain after testing.
