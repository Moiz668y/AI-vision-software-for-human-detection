# Implementation Plan: Eye Coverage, Cursor Jitter, and Head Inversion

## Scope

This file is a planning artifact only. It does not change runtime behavior.

The reported problems are:

- Eye tracking does not reach the screen corners.
- Cursor movement is too jittery.
- Head movement inversion is wrong.

Investigation covered the active Flask app, the UI polling code, the modular camera/gaze/calibration stack, and the completed GPT-5.5 explorer report focused on cursor jitter and related mapping issues.

## Main Active Code Paths

- `assistive_hands/app.py`
  - Main active Flask app.
  - Opens the camera directly.
  - Runs MediaPipe in `process_frame`.
  - Computes head position, iris gaze, blended cursor target, filtering, dwell click, blink click.
  - Moves the OS cursor directly with `pyautogui.moveTo`.

- `assistive_hands/ui/static/js/dashboard.js`
  - Polls `/api/gaze/current`.
  - Contains duplicate `moveSystemCursor` functions.
  - Expects newer API fields that current `app.py` does not return.

- `assistive_hands/calibration/calibrator.py`
  - Contains a 9-point calibration implementation.
  - Uses a margin of `screen // 8`, so even calibration points avoid true edges/corners.
  - Not wired into the active `app.py` path.

- `assistive_hands/camera_stream.py`, `camera/gaze_estimator.py`, `camera/eye_tracker.py`
  - Modular architecture for gaze estimation and calibration.
  - Appears partially wired or older compared with the active self-contained `app.py`.

- `assistive_hands/utils/cursor_control.py`
  - Alternate cursor controller.
  - If used, it treats absolute targets like deltas and applies sensitivity, which can overshoot.

## Confirmed Root Causes

### 1. Corners Are Intentionally Blocked

In `app.py`, after gaze and head are blended, the result is clamped:

```python
raw_x = max(0.02, min(0.98, raw_x))
raw_y = max(0.02, min(0.98, raw_y))
```

This makes the outer 2 percent of each axis unreachable. On a 1920x1080 screen, that blocks roughly:

- Left/right: about 38 px on each side.
- Top/bottom: about 22 px on each side.

So true corners cannot be reached regardless of calibration or user movement.

### 2. Head and Gaze Vertical Mapping Conflict

Current `app.py` mirrors gaze Y:

```python
gaze_y = 1.0 - gaze_y
```

But head Y is not mirrored:

```python
head_y = (nose_y - HEAD_MARGIN) / (1.0 - 2 * HEAD_MARGIN)
```

Then the two are added:

```python
head_offset_y = (head_y - 0.5) * 0.6
raw_y = gaze_y + head_offset_y
```

That means vertical head movement can fight vertical eye movement. This likely explains the wrong head movement inversion.

### 3. Blend Constants Are Defined But Not Used

`HEAD_WEIGHT` and `GAZE_WEIGHT` exist:

```python
HEAD_WEIGHT = 0.20
GAZE_WEIGHT = 0.80
```

But the actual blend ignores them and uses a hard-coded additive offset:

```python
head_offset_x = (head_x - 0.5) * 0.6
head_offset_y = (head_y - 0.5) * 0.6
raw_x = gaze_x + head_offset_x
raw_y = gaze_y + head_offset_y
```

This can amplify jitter and makes settings misleading.

### 4. Jitter Is Amplified Before Filtering

The iris position is normalized inside a very small eye box, then multiplied by:

```python
GAZE_GAIN = 3.5
```

Small MediaPipe landmark noise becomes large cursor movement. The current dead zone then step-holds the cursor until the raw value crosses the threshold, which can create jumps.

### 5. Cursor Output Has Competing Designs

Active `app.py` moves the cursor inside `process_frame`:

```python
pyautogui.moveTo(scr_x, scr_y, duration=0)
```

But the frontend also contains logic to poll gaze and call `/api/cursor/move`. In active `app.py`, that endpoint does not exist, and `/api/gaze/current` does not return the schema the frontend expects.

This API drift means the project currently has two cursor-control designs:

- Backend frame loop directly moves the cursor.
- Browser polling loop tries to move the cursor through an API.

Only one should own OS cursor movement.

### 6. Calibration Is Not Applied In The Active Path

`calibrator.py` can generate points and compute a mapping matrix, but active `app.py` does not expose or use:

- `/api/calibration/start`
- `/api/calibration/point`
- `/api/calibration/calculate`
- saved calibration matrices

The current UI expects those routes, but `app.py` does not implement them.

## Implementation Plan

### Phase 1: Make Cursor Control Single-Owner

Recommended owner: backend `app.py`.

Actions:

- Keep OS cursor movement in Python.
- Disable or remove frontend calls to `/api/cursor/move` in `dashboard.js` and `communication.js`.
- Keep frontend polling only for display/status, not for physical cursor movement.
- Ensure only one camera processing loop controls the cursor.

Why:

- Avoids browser/API latency.
- Avoids duplicate cursor writers.
- Avoids drift between UI schema and backend schema.

### Phase 2: Fix Axis Conventions Explicitly

Add clear configuration flags near the cursor constants in `app.py`:

```python
INVERT_GAZE_X = True
INVERT_GAZE_Y = True
INVERT_HEAD_X = True
INVERT_HEAD_Y = True
```

Then normalize both gaze and head into the same screen-space convention before blending.

Recommended initial behavior:

- Looking left should move cursor left.
- Looking up should move cursor up.
- Moving head left should move cursor left, unless user expects head-as-pointer opposite behavior.
- Moving head up should move cursor up.

Likely first fix:

- Keep `gaze_x` mirrored if current left/right eye direction feels correct.
- Keep `gaze_y` mirrored if looking up currently moves up.
- Also mirror `head_y`, because current gaze Y and head Y are inconsistent.

Important:

- Add temporary debug logging for raw iris, gaze, nose, head, blended raw, and screen coordinates.
- Test each axis independently after change.

### Phase 3: Restore Full Edge And Corner Reach

Replace the final `0.02..0.98` clamp with full-range clamping:

```python
raw_x = max(0.0, min(1.0, raw_x))
raw_y = max(0.0, min(1.0, raw_y))
```

Then optionally add an edge assist function before final clamping:

```python
def apply_edge_gain(v, gain=1.08):
    return (v - 0.5) * gain + 0.5
```

This lets users reach corners without requiring extreme eye movement.

Plan:

- Remove hard 2 percent edge clamp first.
- If corners are still difficult, apply mild edge gain.
- Keep final clamp at `0..1`.

### Phase 4: Replace Additive Blend With Real Weights

Use the existing constants:

```python
raw_x = GAZE_WEIGHT * gaze_x + HEAD_WEIGHT * head_x
raw_y = GAZE_WEIGHT * gaze_y + HEAD_WEIGHT * head_y
```

Normalize weights defensively:

```python
total = max(1e-6, HEAD_WEIGHT + GAZE_WEIGHT)
head_w = HEAD_WEIGHT / total
gaze_w = GAZE_WEIGHT / total
```

Then:

```python
raw_x = gaze_w * gaze_x + head_w * head_x
raw_y = gaze_w * gaze_y + head_w * head_y
```

Recommended starting weights:

- `GAZE_WEIGHT = 0.85`
- `HEAD_WEIGHT = 0.15`

If head control is only meant to extend range instead of directly pointing, use a smaller additive offset:

```python
raw_x = gaze_x + (head_x - 0.5) * HEAD_RANGE_GAIN
raw_y = gaze_y + (head_y - 0.5) * HEAD_RANGE_GAIN
```

But then remove or rename `HEAD_WEIGHT` and `GAZE_WEIGHT` to avoid confusion.

### Phase 5: Reduce Jitter Before It Reaches Cursor Output

Apply stability in this order:

1. Reject sudden landmark spikes.
2. Smooth raw iris ratios and nose position separately.
3. Blend smoothed gaze/head.
4. Use a soft dead zone.
5. Use One Euro filtering on final target.
6. Move OS cursor only if pixel delta is meaningful.

Recommended code-level changes:

- Replace step-hold dead zone:

```python
if abs(raw_x - cur_x) > DEAD_ZONE:
    cur_x = raw_x
```

with a soft update:

```python
def soft_deadzone_update(current, target, dead_zone, alpha):
    if current is None:
        return target
    delta = target - current
    if abs(delta) <= dead_zone:
        return current
    return current + alpha * delta
```

- Start with:

```python
DEAD_ZONE = 0.0025
SOFT_DEADZONE_ALPHA = 0.35
MIN_CURSOR_PIXEL_DELTA = 2
```

- Tune One Euro:

```python
oef_x = OneEuroFilter(freq=30.0, mincutoff=0.45, beta=0.015)
oef_y = OneEuroFilter(freq=30.0, mincutoff=0.45, beta=0.015)
```

- Use `time.monotonic()` for filter timestamps instead of `time.time()`.
- Bound `dt` inside the filter to prevent weird jumps after stalls.

### Phase 6: Decouple Cursor Movement From MJPEG Streaming

Current risk:

- `generate_frames()` calls `process_frame()`.
- `process_frame()` moves the cursor.
- If multiple clients open `/video_feed` or `/camera_feed`, more than one stream generator can call cursor movement.

Recommended structure:

- A single camera processing thread updates:
  - latest processed frame
  - latest normalized gaze
  - latest screen target
  - face/blink/dwell status

- The MJPEG route only serves the latest frame.
- A single cursor output loop moves the OS cursor at a fixed rate.

Minimal version for current code:

- Add a global lock around frame processing and cursor movement.
- Prevent multiple stream clients from running independent cursor-driving loops.

Better version:

- Refactor toward the existing `CameraStream` design, but only after the active `app.py` bug fixes are stable.

### Phase 7: Wire Calibration Into The Active App

Short-term:

- Add the missing calibration routes to active `app.py`.
- Store calibration samples using current raw gaze before final smoothing.
- Apply calibration before cursor movement.

Better:

- Use `calibration/calibrator.py`, but adjust it to output normalized screen coordinates or clearly convert pixels to normalized values.

Fix the calibration point generation:

- Current margin is `screen // 8`, which avoids edges.
- For corner coverage, use points closer to edges:

```python
margin_x = int(screen_width * 0.04)
margin_y = int(screen_height * 0.04)
```

Or use an expanded set:

- 9 standard grid points.
- 4 true corner assist points.
- Optional 4 edge midpoint points.

Important:

- Calibration sampling must not use already-calibrated output.
- During calibration, disable calibration application and collect raw gaze.

### Phase 8: Fix API Contract Drift

Update active `/api/gaze/current` to include both old and new fields:

```json
{
  "status": "success",
  "gaze_x": 0.5,
  "gaze_y": 0.5,
  "cursor_x": 960,
  "cursor_y": 540,
  "screen_width": 1920,
  "screen_height": 1080,
  "face_detected": true,
  "blink_detected": false,
  "eye_openness": 0.3,
  "dwell_progress": 0.0,
  "fps": 30,
  "gaze_normalized": {"x": 0.5, "y": 0.5},
  "gaze_screen": {"x": 960, "y": 540}
}
```

This lets existing UI code and debug tools work while the backend remains the cursor owner.

### Phase 9: Clean Up Duplicate Frontend Cursor Functions

In `dashboard.js`:

- Remove one of the duplicate `moveSystemCursor` definitions.
- Keep one function only if a manual API cursor mode is still desired.
- If backend owns cursor, make it a no-op or debug-only.

Also inspect:

- `communication.js`
- `dwell_click.js`
- `debug.html`

Goal:

- No page should accidentally start a second cursor loop.

## Recommended Implementation Order

1. Patch `app.py` axis flags and full-range clamp.
2. Replace hard-coded additive blend with normalized weights.
3. Add soft dead zone and tune One Euro settings.
4. Add minimum pixel delta before `pyautogui.moveTo`.
5. Update `/api/gaze/current` to return the richer schema.
6. Disable frontend physical cursor posting.
7. Add calibration routes and apply calibration to active gaze mapping.
8. Move cursor output out of the MJPEG generator into a single loop.
9. Clean up old backup/patch scripts after behavior is stable.

## Validation Checklist

### Static Checks

- `python -m py_compile assistive_hands/app.py`
- Confirm `dashboard.js` has only one `moveSystemCursor` function or none used for physical cursor movement.
- Confirm `/api/gaze/current` schema matches dashboard/debug/calibration expectations.

### Manual Direction Test

With debug logs enabled:

- Look left: normalized X decreases, screen X decreases.
- Look right: normalized X increases, screen X increases.
- Look up: normalized Y decreases, screen Y decreases.
- Look down: normalized Y increases, screen Y increases.
- Move head left: cursor moves in intended left/right direction.
- Move head up: cursor moves in intended up/down direction.

### Corner Test

Ask the user to look at:

- Top-left corner.
- Top-right corner.
- Bottom-left corner.
- Bottom-right corner.

Expected:

- Cursor can reach within 5-10 px of every screen corner after edge gain/calibration.
- No forced stop at 2 percent or 98 percent.

### Jitter Test

User holds gaze at center for 10 seconds.

Measure:

- Cursor pixel standard deviation.
- Number of cursor moves per second.
- Max jump while face is steady.

Expected target:

- Less than 5-10 px visible jitter at rest.
- No stair-step jumps while holding still.
- Still responsive enough to cross screen in under about 1 second during intentional movement.

### Multi-Client Test

Open:

- Dashboard.
- Debug page.
- Calibration page.

Expected:

- Only one backend camera/cursor loop moves the OS cursor.
- No doubled movement speed.
- No increased jitter caused by extra browser tabs.

## Risks

- Axis inversion depends on camera orientation and whether the feed is mirrored visually. Add flags so this can be corrected without rewriting mapping code.
- Calibration currently mixes normalized and pixel coordinate assumptions. Be explicit about which stage uses which coordinate space.
- Over-smoothing can make the cursor feel calm but laggy. Tune with a real user in front of the camera.
- Moving cursor from both backend and frontend will make any smoothing fix look worse, so single-owner cursor control is the first priority.

## First Patch Target

Start with `assistive_hands/app.py`.

Minimum first patch:

- Add inversion constants.
- Make head/gaze screen-space directions consistent.
- Remove `0.02..0.98` clamp.
- Use real blend weights.
- Add minimum pixel delta before `pyautogui.moveTo`.
- Return richer `/api/gaze/current` response.

Then patch `dashboard.js` to stop sending physical cursor moves.

