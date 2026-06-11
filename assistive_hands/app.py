import cv2
import mediapipe as mp
from mediapipe.tasks.python import vision
from mediapipe.tasks.python.core import base_options
import numpy as np
import pyautogui
import time
from flask import Flask, Response, render_template, jsonify, request, send_from_directory, stream_with_context
import threading
import webbrowser
import math
import json
import os
from pathlib import Path
from datetime import datetime

from realtime import CommandBus, ScrollWorker, StateStore

app = Flask(__name__, template_folder='ui/templates', static_folder='ui/static')

# ── Camera ────────────────────────────────────────────────────────────────────
CAMERA_FRAME_WIDTH = 1280
CAMERA_FRAME_HEIGHT = 720
CAMERA_DIGITAL_ZOOM = 1.0

camera = cv2.VideoCapture(0)
camera.set(cv2.CAP_PROP_FRAME_WIDTH, CAMERA_FRAME_WIDTH)
camera.set(cv2.CAP_PROP_FRAME_HEIGHT, CAMERA_FRAME_HEIGHT)
camera.set(cv2.CAP_PROP_FPS, 30)
camera.set(cv2.CAP_PROP_BUFFERSIZE, 1)

# ── MediaPipe ─────────────────────────────────────────────────────────────────
# output_face_blendshapes=True populates the full 478-pt list including iris
# landmarks 468 (left iris centre) and 473 (right iris centre).
base_opts = base_options.BaseOptions(model_asset_path='models/face_landmarker.task')
options = vision.FaceLandmarkerOptions(
    base_options=base_opts,
    running_mode=vision.RunningMode.VIDEO,
    num_faces=1,
    output_face_blendshapes=True,
    output_facial_transformation_matrixes=True,
)
face_landmarker = vision.FaceLandmarker.create_from_options(options)
last_ts = 0

def now_ms():
    return int(time.time() * 1000)

# ── PyAutoGUI ─────────────────────────────────────────────────────────────────
pyautogui.FAILSAFE = False
pyautogui.MINIMUM_DURATION = 0
pyautogui.PAUSE = 0
screen_w, screen_h = pyautogui.size()

# ── Blink state ───────────────────────────────────────────────────────────────
face_detected      = False
blink_detected     = False
eye_openness       = 1.0
eyes_closed        = False
last_blink_time    = 0.0
last_click_time    = 0.0
CLICK_COOLDOWN     = 0.75
BLINK_FLAG_DUR     = 0.65
ear_open_avg       = 0.30
BLINK_RATIO        = 0.72
calibration_frames = 0
blink_closed_frames = 0
BLINK_MIN_CLOSED_FRAMES = 1
BLINK_EAR_THRESHOLD = 0.20
BLINK_SCORE_THRESHOLD = 0.35
DOUBLE_BLINK_INTERVAL = 0.90
SHORT_BLINK_MIN = 0.04
SHORT_BLINK_MAX = 0.65
eyes_closed_start_time = None
last_short_blink_time = 0.0
last_eye_closed_time = 0.0
blink_event_label = ""
blink_event_until = 0.0

# ── Dwell state ───────────────────────────────────────────────────────────────
dwell_position   = None
dwell_start_time = 0.0
dwell_progress   = 0.0
dwell_fired      = False
last_dwell_click = 0.0
DWELL_RADIUS     = 60
DWELL_TIME       = 1.5
DWELL_COOLDOWN   = 1.5
DWELL_ENABLED    = False  # UI pages handle dwell selection; avoid global 1.5s auto-clicks.
DWELL_DOUBLE_CLICK = True

# ── Cursor positioning constants ──────────────────────────────────────────────
# How much of the frame edge is "already at screen edge".
# Shrink HEAD_MARGIN if cursor doesn't reach screen corners when you look there.
HEAD_MARGIN  = 0.01

# Iris-position gain: iris sits in ~[0.3,0.7] of the eye box.
# GAZE_GAIN stretches that to ±[0,1].  3.5 = 1/0.28 stretch for 80% iris weight.
GAZE_GAIN    = 1.0
IRIS_X_MIN = 0.30
IRIS_X_MAX = 0.70
IRIS_Y_MIN = 0.30
IRIS_Y_MAX = 0.70
HEAD_RANGE_GAIN = 0.18

# Axis conventions. These make camera orientation fixes explicit instead of
# burying inversions inside the math.
INVERT_GAZE_X = True
INVERT_GAZE_Y = True
INVERT_HEAD_X = True
INVERT_HEAD_Y = True

# Keep eyes as the primary pointer. Head movement adds range instead of
# averaging the cursor back toward the center.
HEAD_WEIGHT  = 0.00
GAZE_WEIGHT  = 1.00

# Mild overscan so calibrated/extreme gaze can actually hit screen edges.
EDGE_GAIN = 1.08

# Dead zone: cursor won't move unless raw position changes by this fraction.
# Kills micro-jitter from camera noise.
DEAD_ZONE    = 0.0025
SOFT_DEADZONE_ALPHA = 0.35
MIN_CURSOR_PIXEL_DELTA = 4
CURSOR_OUTPUT_HZ = 45.0

# Relative eye-mouse controller, ported from D:\temp\EyeTrackingMouse.
RELATIVE_MOUSE_SENSITIVITY_X = 2.7
RELATIVE_MOUSE_SENSITIVITY_Y = 1.6
RELATIVE_FACE_SENSITIVITY = 0.55
RELATIVE_SMOOTHING = 0.94
RELATIVE_BUFFER_SIZE = 12
RELATIVE_DEADZONE = 0.006
MAX_CURSOR_STEP_X = 28.0
MAX_CURSOR_STEP_Y = 22.0

relative_calibrated = False
relative_center_offset = (0.0, 0.0)
relative_face_center_offset = (0.0, 0.0)
relative_prev_x, relative_prev_y = pyautogui.position()
relative_x_buffer = []
relative_y_buffer = []

# ── Debug frame counter ───────────────────────────────────────────────────────
_debug_frame = 0

# ── One-Euro Filter ───────────────────────────────────────────────────────────
# Much better than EMA for cursor control:
#   - at low speed (nearly still) → heavy smoothing, kills jitter
#   - at high speed (intentional movement) → light smoothing, stays responsive
# Reference: Casiez et al. 2012, "1€ Filter"

class OneEuroFilter:
    """Scalar 1€ filter.  Call .filter(value, timestamp_sec) each frame."""

    def __init__(self, freq=30.0, mincutoff=1.0, beta=0.007, dcutoff=1.0):
        # freq      – nominal sample rate (Hz); updated dynamically each call
        # mincutoff – lower = smoother when still, but adds lag
        # beta      – higher = faster response when moving, but more jitter
        # dcutoff   – cutoff for the derivative (leave at 1.0)
        self.freq      = freq
        self.mincutoff = mincutoff
        self.beta      = beta
        self.dcutoff   = dcutoff
        self._x        = None   # previous filtered value
        self._dx       = 0.0    # previous filtered derivative
        self._last_t   = None

    @staticmethod
    def _alpha(cutoff, freq):
        te  = 1.0 / freq
        tau = 1.0 / (2 * math.pi * cutoff)
        return 1.0 / (1.0 + tau / te)

    def filter(self, x, t):
        if self._last_t is not None:
            dt = t - self._last_t
            if dt > 0:
                dt = max(1.0 / 120.0, min(dt, 0.25))
                self.freq = 1.0 / dt
        self._last_t = t

        if self._x is None:   # first sample — no history yet
            self._x  = x
            self._dx = 0.0
            return x

        # Derivative with low-pass filter
        dx_raw       = (x - self._x) * self.freq
        a_d          = self._alpha(self.dcutoff, self.freq)
        self._dx     = a_d * dx_raw + (1.0 - a_d) * self._dx

        # Adaptive cutoff based on speed
        cutoff       = self.mincutoff + self.beta * abs(self._dx)
        a            = self._alpha(cutoff, self.freq)
        self._x      = a * x + (1.0 - a) * self._x
        return self._x

    def reset(self):
        self._x      = None
        self._dx     = 0.0
        self._last_t = None


# One filter per axis
oef_x = OneEuroFilter(freq=30.0, mincutoff=0.45, beta=0.015)
oef_y = OneEuroFilter(freq=30.0, mincutoff=0.45, beta=0.015)

# Smoothed cursor position (screen fraction [0,1])
cur_x = None
cur_y = None

# Current gaze position for API response
current_gaze_x = 0.5
current_gaze_y = 0.5
current_raw_gaze_x = 0.5
current_raw_gaze_y = 0.5
current_cursor_x = 0
current_cursor_y = 0
last_cursor_move_x = None
last_cursor_move_y = None
manual_cursor_target = None
latest_cursor_target_x = None
latest_cursor_target_y = None
latest_cursor_target_version = 0
last_cursor_target_version = -1

state_lock = threading.Lock()
frame_lock = threading.Lock()
pyautogui_lock = threading.Lock()
latest_frame_bytes = None
tracking_running = False
tracking_paused = False
cursor_control_enabled = True
camera_thread = None
cursor_thread = None
telemetry_thread = None
DEBUG_TIMING_EVERY = 120
TELEMETRY_HZ = 15.0
latest_timing = {
    'capture_ms': 0.0,
    'process_ms': 0.0,
    'mediapipe_ms': 0.0,
    'cursor_target_ms': 0.0,
    'cursor_move_ms': 0.0,
    'encode_ms': 0.0,
}
latest_telemetry_payload = None
telemetry_lock = threading.Lock()
runtime_started_at = time.time()
runtime_lock = threading.Lock()
runtime_metrics = {
    'camera_frames': 0,
    'camera_fps': 0.0,
    'cursor_moves': 0,
    'cursor_fps': 0.0,
    'sse_clients': 0,
    'sse_events': 0,
    'sse_events_per_second': 0.0,
    'commands_enqueued': 0,
    'commands_completed': 0,
    'commands_failed': 0,
    'last_command': '',
    'last_command_source': '',
    'last_command_age': None,
    'last_command_at': 0.0,
    'last_command_latency_ms': 0.0,
    'telemetry_hz': TELEMETRY_HZ,
    'last_telemetry_at': 0.0,
}
state_store = StateStore()
command_bus = CommandBus(state_store)
scroll_worker = ScrollWorker(pyautogui.scroll, state_store)
state_store.update({
    'engine': {'running': False, 'paused': False},
    'camera': {'running': False, 'fps': 0.0, 'dropped_frames': 0},
    'gaze': {
        'normalized': {'x': 0.5, 'y': 0.5},
        'raw': {'x': 0.5, 'y': 0.5},
        'screen': {'x': 0, 'y': 0},
        'face_detected': False,
        'blink_detected': False,
        'eye_openness': 1.0,
    },
    'cursor': {'enabled': True, 'x': 0, 'y': 0, 'fps': 0.0},
    'voice': {'state': 'browser-fallback', 'last_command': '', 'last_command_at': 0.0},
    'scroll': {'active': False, 'direction': 0, 'speed': 'normal'},
    'command': {'queue_size': 0, 'last': '', 'last_source': '', 'last_at': 0.0},
    'timing_ms': {},
})


def update_runtime_metrics(**updates):
    with runtime_lock:
        runtime_metrics.update(updates)


def increment_runtime_metric(name, amount=1):
    with runtime_lock:
        runtime_metrics[name] = runtime_metrics.get(name, 0) + amount


def runtime_snapshot():
    with runtime_lock:
        snapshot = dict(runtime_metrics)
    now = time.time()
    snapshot['uptime_seconds'] = now - runtime_started_at
    snapshot['process_id'] = os.getpid()
    snapshot['command_queue_size'] = command_bus.size()
    snapshot['threads'] = {
        'camera': bool(camera_thread and camera_thread.is_alive()),
        'cursor': bool(cursor_thread and cursor_thread.is_alive()),
        'telemetry': bool(telemetry_thread and telemetry_thread.is_alive()),
    }
    last_command_at = snapshot.get('last_command_at') or 0.0
    snapshot['last_command_age'] = (now - last_command_at) if last_command_at else None
    snapshot['state_sequence'] = state_store.revision
    return snapshot

BASE_DIR = Path(__file__).resolve().parent
CALIBRATION_DIR = BASE_DIR / "data" / "calibration"
CALIBRATION_FILE = CALIBRATION_DIR / "default_calibration.npz"
CALIBRATION_META_FILE = CALIBRATION_DIR / "default_metadata.json"
CALIBRATION_SCHEMA = "normalized_v2"
calibration_active = False
calibration_enabled = True
calibration_points = []
calibration_samples = {}
calibration_matrix = None
calibration_valid = False
calibration_last_validation = None


def clamp01(value):
    return max(0.0, min(1.0, float(value)))


def maybe_invert(value, enabled):
    value = clamp01(value)
    return 1.0 - value if enabled else value


def apply_edge_gain(value):
    return clamp01((value - 0.5) * EDGE_GAIN + 0.5)


def normalize_range(value, min_value, max_value):
    span = max(1e-6, max_value - min_value)
    return clamp01((value - min_value) / span)


def screen_fraction_to_pixel(x, y):
    return (
        max(0, min(int(round(clamp01(x) * (screen_w - 1))), screen_w - 1)),
        max(0, min(int(round(clamp01(y) * (screen_h - 1))), screen_h - 1)),
    )


def apply_camera_zoom(frame):
    """Center crop then resize so face/eyes occupy more tracking pixels."""
    if CAMERA_DIGITAL_ZOOM <= 1.0:
        return frame

    h, w = frame.shape[:2]
    crop_w = max(1, min(w, int(round(w / CAMERA_DIGITAL_ZOOM))))
    crop_h = max(1, min(h, int(round(h / CAMERA_DIGITAL_ZOOM))))
    x1 = max(0, (w - crop_w) // 2)
    y1 = max(0, (h - crop_h) // 2)
    cropped = frame[y1:y1 + crop_h, x1:x1 + crop_w]
    return cv2.resize(cropped, (w, h), interpolation=cv2.INTER_LINEAR)


def soft_deadzone_update(current, target):
    if current is None:
        return clamp01(target)
    delta = target - current
    magnitude = abs(delta)
    if magnitude <= DEAD_ZONE:
        return current
    adjusted_delta = math.copysign(magnitude - DEAD_ZONE, delta)
    return clamp01(current + SOFT_DEADZONE_ALPHA * adjusted_delta)


def cursor_move_needed(target_x, target_y):
    if last_cursor_move_x is None or last_cursor_move_y is None:
        return True
    dx = target_x - last_cursor_move_x
    dy = target_y - last_cursor_move_y
    return (dx * dx + dy * dy) >= (MIN_CURSOR_PIXEL_DELTA * MIN_CURSOR_PIXEL_DELTA)


def publish_cursor_target(x, y):
    """Publish latest cursor target for the fixed cursor output loop."""
    global latest_cursor_target_x, latest_cursor_target_y, latest_cursor_target_version
    with state_lock:
        latest_cursor_target_x = max(0, min(int(x), screen_w - 1))
        latest_cursor_target_y = max(0, min(int(y), screen_h - 1))
        latest_cursor_target_version += 1


def command_priority(name):
    priorities = {
        'stop_scroll': 1,
        'voice_scroll_state': 1,
        'cursor_disable': 2,
        'cursor_enable': 3,
        'click': 10,
        'double_click': 10,
        'key_press': 20,
        'type_text': 20,
        'cursor_move': 20,
        'start_scroll': 30,
    }
    return priorities.get(name, 50)


def enqueue_command(name, payload=None, source='api'):
    queued = command_bus.enqueue(name, payload or {}, priority=command_priority(name), source=source)
    if queued:
        update_runtime_metrics(
            commands_enqueued=runtime_snapshot().get('commands_enqueued', 0) + 1,
            last_command=name,
            last_command_source=source,
            last_command_at=time.time(),
        )
    return queued


def record_command_result(result):
    latency_ms = max(0.0, (result.completed_at - runtime_snapshot().get('last_command_at', result.completed_at)) * 1000.0)
    with runtime_lock:
        runtime_metrics['commands_completed'] = runtime_metrics.get('commands_completed', 0) + 1
        if not result.ok:
            runtime_metrics['commands_failed'] = runtime_metrics.get('commands_failed', 0) + 1
        runtime_metrics['last_command_latency_ms'] = latency_ms


command_bus.subscribe_results(record_command_result)


def safe_mouse_click(count=1, interval=0.08):
    """Serialize PyAutoGUI clicks with cursor movement to avoid click/move races."""
    with pyautogui_lock:
        for index in range(max(1, min(int(count), 2))):
            pyautogui.click()
            if index == 0 and count > 1:
                time.sleep(interval)


def safe_cursor_move(x, y):
    """Serialize OS cursor movement with clicks."""
    with pyautogui_lock:
        pyautogui.moveTo(x, y, duration=0)


def limit_cursor_step(previous, target, max_step):
    delta = target - previous
    if abs(delta) <= max_step:
        return target
    return previous + math.copysign(max_step, delta)


def generate_calibration_points():
    margin_x = int(screen_w * 0.04)
    margin_y = int(screen_h * 0.04)
    x_positions = np.linspace(margin_x, screen_w - 1 - margin_x, 3)
    y_positions = np.linspace(margin_y, screen_h - 1 - margin_y, 3)
    return [[int(x), int(y)] for y in y_positions for x in x_positions]


def apply_calibration_point(x, y):
    if calibration_matrix is None or calibration_active or not calibration_enabled:
        return clamp01(x), clamp01(y)
    try:
        mapped = np.array([x, y, 1.0]) @ calibration_matrix
        return clamp01(mapped[0]), clamp01(mapped[1])
    except Exception as e:
        print(f"[WARN] calibration apply err: {e}")
        return clamp01(x), clamp01(y)


def load_calibration():
    global calibration_matrix, calibration_valid, calibration_last_validation, calibration_enabled
    try:
        if not CALIBRATION_FILE.exists():
            return False
        if not CALIBRATION_META_FILE.exists():
            print("[CAL] Ignoring calibration without current metadata; please recalibrate.")
            calibration_enabled = False
            return False
        try:
            metadata = json.loads(CALIBRATION_META_FILE.read_text(encoding='utf-8'))
        except Exception:
            metadata = {}
        if metadata.get("schema") != CALIBRATION_SCHEMA:
            print("[CAL] Ignoring old calibration schema; please recalibrate.")
            calibration_enabled = False
            return False
        data = np.load(CALIBRATION_FILE, allow_pickle=True)
        loaded_matrix = data["matrix"]
        if loaded_matrix.shape != (3, 2) or np.max(np.abs(loaded_matrix)) > 10:
            print("[CAL] Ignoring incompatible pixel-space calibration; please recalibrate.")
            calibration_matrix = None
            calibration_valid = False
            calibration_enabled = False
            calibration_last_validation = None
            return False
        calibration_matrix = loaded_matrix
        if "validation" in data.files:
            validation = data["validation"]
            calibration_last_validation = validation.item() if validation.shape == () else validation.tolist()
        calibration_valid = True
        print(f"[CAL] Loaded calibration from {CALIBRATION_FILE}")
        return True
    except Exception as e:
        print(f"[WARN] calibration load err: {e}")
        calibration_matrix = None
        calibration_valid = False
        calibration_last_validation = None
        return False


load_calibration()


# ── EAR (unchanged) ───────────────────────────────────────────────────────────
def get_ear(lm, w, h):
    def d(a, b):
        return np.hypot(a[0] - b[0], a[1] - b[1])
    try:
        lp = [[lm[i].x * w, lm[i].y * h] for i in [33, 160, 158, 133, 153, 144]]
        rp = [[lm[i].x * w, lm[i].y * h] for i in [362, 385, 387, 263, 373, 380]]
        l = d(lp[1], lp[4]) / (d(lp[0], lp[3]) + 1e-6)
        r = d(rp[1], rp[4]) / (d(rp[0], rp[3]) + 1e-6)
        return float(max(0.0, (l + r) / 2.0))
    except Exception as e:
        print(f"EAR err: {e}")
        return 1.0


def get_blink_score(result):
    """Return MediaPipe blendshape blink score, or None if unavailable."""
    try:
        if not result.face_blendshapes:
            return None

        scores = {}
        for category in result.face_blendshapes[0]:
            scores[category.category_name] = category.score

        left = scores.get("eyeBlinkLeft")
        right = scores.get("eyeBlinkRight")
        if left is None or right is None:
            return None

        return float((left + right) / 2.0)
    except Exception as e:
        print(f"[WARN] blink score err: {e}")
        return None


def landmark_points(lm, indices, w, h):
    return np.array([[lm[i].x * w, lm[i].y * h] for i in indices], dtype=float)


def iris_center(lm, indices, w, h):
    valid = [i for i in indices if i < len(lm)]
    if not valid:
        return np.array([w / 2.0, h / 2.0], dtype=float)
    return landmark_points(lm, valid, w, h).mean(axis=0)


def eye_height(lm, pairs, w, h):
    distances = []
    for upper_idx, lower_idx in pairs:
        upper = np.array([lm[upper_idx].x * w, lm[upper_idx].y * h], dtype=float)
        lower = np.array([lm[lower_idx].x * w, lm[lower_idx].y * h], dtype=float)
        distances.append(np.linalg.norm(upper - lower))
    return float(np.mean(distances)) if distances else 1.0


def reset_relative_controller():
    global relative_calibrated, relative_center_offset, relative_face_center_offset
    global relative_prev_x, relative_prev_y, relative_x_buffer, relative_y_buffer
    relative_calibrated = False
    relative_center_offset = (0.0, 0.0)
    relative_face_center_offset = (0.0, 0.0)
    relative_prev_x, relative_prev_y = pyautogui.position()
    relative_x_buffer = []
    relative_y_buffer = []


def relative_mouse_target(iris_offset, face_offset, eye_width, eye_height):
    global relative_prev_x, relative_prev_y, relative_x_buffer, relative_y_buffer

    safe_eye_width = max(1.0, float(eye_width))
    safe_eye_height = max(1.0, float(eye_height))
    norm_dx = (iris_offset[0] / safe_eye_width) * RELATIVE_MOUSE_SENSITIVITY_X
    norm_dy = (iris_offset[1] / safe_eye_height) * RELATIVE_MOUSE_SENSITIVITY_Y
    norm_dx += (face_offset[0] / safe_eye_width) * RELATIVE_FACE_SENSITIVITY
    norm_dy += (face_offset[1] / safe_eye_width) * RELATIVE_FACE_SENSITIVITY

    if abs(norm_dx) < RELATIVE_DEADZONE:
        norm_dx = 0.0
    if abs(norm_dy) < RELATIVE_DEADZONE:
        norm_dy = 0.0

    target_x = (screen_w / 2.0) + (norm_dx * screen_w)
    target_y = (screen_h / 2.0) + (norm_dy * screen_h)

    relative_x_buffer.append(target_x)
    relative_y_buffer.append(target_y)
    if len(relative_x_buffer) > RELATIVE_BUFFER_SIZE:
        relative_x_buffer.pop(0)
        relative_y_buffer.pop(0)

    avg_x = sum(relative_x_buffer) / len(relative_x_buffer)
    avg_y = sum(relative_y_buffer) / len(relative_y_buffer)

    curr_x = relative_prev_x + (avg_x - relative_prev_x) * (1.0 - RELATIVE_SMOOTHING)
    curr_y = relative_prev_y + (avg_y - relative_prev_y) * (1.0 - RELATIVE_SMOOTHING)
    curr_x = limit_cursor_step(relative_prev_x, curr_x, MAX_CURSOR_STEP_X)
    curr_y = limit_cursor_step(relative_prev_y, curr_y, MAX_CURSOR_STEP_Y)

    curr_x = max(0, min(screen_w - 1, curr_x))
    curr_y = max(0, min(screen_h - 1, curr_y))

    relative_prev_x, relative_prev_y = curr_x, curr_y
    return (
        int(round(curr_x)),
        int(round(curr_y)),
        clamp01(curr_x / max(1, screen_w - 1)),
        clamp01(curr_y / max(1, screen_h - 1)),
    )


# ── process_frame ─────────────────────────────────────────────────────────────
def process_frame(frame):
    global last_ts
    global blink_detected, face_detected, eye_openness
    global last_blink_time, last_click_time, eyes_closed, ear_open_avg
    global blink_closed_frames, eyes_closed_start_time, last_short_blink_time
    global last_eye_closed_time, blink_event_label, blink_event_until
    global dwell_position, dwell_start_time, dwell_progress, dwell_fired
    global last_dwell_click, calibration_frames, _debug_frame
    global cur_x, cur_y, current_gaze_x, current_gaze_y, current_raw_gaze_x, current_raw_gaze_y
    global current_cursor_x, current_cursor_y, last_cursor_move_x, last_cursor_move_y
    global relative_calibrated, relative_center_offset, relative_face_center_offset
    global latest_timing

    process_started = time.perf_counter()
    mediapipe_ms = 0.0
    cursor_target_ms = 0.0
    cursor_move_ms = 0.0
    frame = apply_camera_zoom(frame)
    h, w = frame.shape[:2]
    now  = time.time()
    filter_now = time.monotonic()
    _debug_frame += 1

    rgb      = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
    mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)

    ts = now_ms()
    if ts <= last_ts:
        ts = last_ts + 1
    last_ts = ts

    mediapipe_started = time.perf_counter()
    result        = face_landmarker.detect_for_video(mp_image, ts)
    mediapipe_ms = (time.perf_counter() - mediapipe_started) * 1000.0
    face_detected = bool(result.face_landmarks)

    if face_detected:
        lm = result.face_landmarks[0]

        # ── Blink / EAR: double blink logic from EyeTrackingMouse ─────────
        ear       = get_ear(lm, w, h)
        eye_openness = ear
        blink_score = get_blink_score(result)
        adaptive_threshold = max(BLINK_EAR_THRESHOLD, ear_open_avg * BLINK_RATIO)
        score_closed = blink_score is not None and blink_score >= BLINK_SCORE_THRESHOLD
        ear_closed = ear < adaptive_threshold
        currently_closed = bool(score_closed or ear_closed)
        threshold = adaptive_threshold

        if calibration_frames < 30 and not currently_closed:
            ear_open_avg = 0.85 * ear_open_avg + 0.15 * ear
            calibration_frames += 1
        elif not currently_closed:
            ear_open_avg = 0.98 * ear_open_avg + 0.02 * ear

        if currently_closed:
            blink_closed_frames += 1
            last_eye_closed_time = now
            if eyes_closed_start_time is None:
                eyes_closed_start_time = now
            eyes_closed = True
        else:
            if eyes_closed and eyes_closed_start_time is not None:
                closed_duration = now - eyes_closed_start_time
                if (
                    blink_closed_frames >= BLINK_MIN_CLOSED_FRAMES
                    and SHORT_BLINK_MIN <= closed_duration <= SHORT_BLINK_MAX
                ):
                    if 0 < (now - last_short_blink_time) < DOUBLE_BLINK_INTERVAL:
                        blink_detected = True
                        last_blink_time = now
                        last_short_blink_time = 0.0
                        if (now - last_click_time) >= CLICK_COOLDOWN:
                            safe_mouse_click()
                            last_click_time = now
                            blink_event_label = "DOUBLE BLINK CLICK"
                            blink_event_until = now + 0.90
                            print(
                                f">>> DOUBLE BLINK CLICK  EAR={ear:.4f} "
                                f"blink={blink_score if blink_score is not None else -1:.3f} "
                                f"thr={threshold:.4f} dur={closed_duration:.3f} "
                                f"frames={blink_closed_frames}"
                            )
                    else:
                        last_short_blink_time = now
                        blink_event_label = "BLINK 1/2"
                        blink_event_until = now + 0.90
                eyes_closed_start_time = None
                blink_closed_frames = 0
            eyes_closed = False

        if blink_detected and (now - last_blink_time) > BLINK_FLAG_DUR:
            blink_detected = False
            if now >= blink_event_until:
                blink_event_label = ""

        freeze_cursor_for_blink = currently_closed

        # ── Relative eye/head mouse movement from EyeTrackingMouse ───────
        try:
            left_eye = landmark_points(lm, [33, 160, 158, 133, 153, 144], w, h)
            right_eye = landmark_points(lm, [362, 385, 387, 263, 373, 380], w, h)
            left_center = left_eye.mean(axis=0)
            right_center = right_eye.mean(axis=0)
            left_iris_center = iris_center(lm, [468, 469, 470, 471, 472], w, h)
            right_iris_center = iris_center(lm, [473, 474, 475, 476, 477], w, h)
            nose = np.array([lm[1].x * w, lm[1].y * h], dtype=float)

            curr_dx = ((left_iris_center[0] - left_center[0]) + (right_iris_center[0] - right_center[0])) / 2.0
            curr_dy = ((left_iris_center[1] - left_center[1]) + (right_iris_center[1] - right_center[1])) / 2.0
            curr_face_dx = nose[0]
            curr_face_dy = nose[1]

            if not relative_calibrated:
                relative_center_offset = (curr_dx, curr_dy)
                relative_face_center_offset = (curr_face_dx, curr_face_dy)
                relative_calibrated = True
                print("[REL] Center calibrated. Look at screen center when starting for best range.")

            final_dx = -(curr_dx - relative_center_offset[0])
            final_dy = curr_dy - relative_center_offset[1]
            final_face_dx = -(curr_face_dx - relative_face_center_offset[0])
            final_face_dy = curr_face_dy - relative_face_center_offset[1]

            left_width = np.linalg.norm(np.array([lm[33].x * w, lm[33].y * h]) - np.array([lm[133].x * w, lm[133].y * h]))
            right_width = np.linalg.norm(np.array([lm[362].x * w, lm[362].y * h]) - np.array([lm[263].x * w, lm[263].y * h]))
            avg_eye_width = (left_width + right_width) / 2.0
            left_height = eye_height(lm, [(160, 144), (158, 153)], w, h)
            right_height = eye_height(lm, [(385, 380), (387, 373)], w, h)
            avg_eye_height = (left_height + right_height) / 2.0

            cursor_target_started = time.perf_counter()
            if freeze_cursor_for_blink:
                scr_x, scr_y = current_cursor_x, current_cursor_y
                smooth_x, smooth_y = current_gaze_x, current_gaze_y
            else:
                scr_x, scr_y, smooth_x, smooth_y = relative_mouse_target(
                    (final_dx, final_dy),
                    (final_face_dx, final_face_dy),
                    avg_eye_width,
                    avg_eye_height,
                )
            cursor_target_ms = (time.perf_counter() - cursor_target_started) * 1000.0
            current_raw_gaze_x = smooth_x
            current_raw_gaze_y = smooth_y

            if _debug_frame % 30 == 0:
                print(
                    f"[REL] eye=({final_dx:.2f},{final_dy:.2f}) "
                    f"face=({final_face_dx:.2f},{final_face_dy:.2f}) "
                    f"eye=({avg_eye_width:.2f}w,{avg_eye_height:.2f}h) px=({scr_x},{scr_y})"
                )
        except (IndexError, ZeroDivisionError) as e:
            print(f"[WARN] relative cursor err: {e}")
            scr_x, scr_y = current_cursor_x, current_cursor_y
            smooth_x = current_gaze_x
            smooth_y = current_gaze_y

        # Update global cursor state for API and the single cursor output loop.
        with state_lock:
            current_gaze_x = smooth_x
            current_gaze_y = smooth_y
            current_cursor_x = scr_x
            current_cursor_y = scr_y

        if _debug_frame % 30 == 0:
            print(f"[CURSOR] smooth=({smooth_x:.3f},{smooth_y:.3f})  px=({scr_x},{scr_y})")

        if (
            cursor_control_enabled
            and not tracking_paused
            and not freeze_cursor_for_blink
            and cursor_move_needed(scr_x, scr_y)
        ):
            cursor_move_started = time.perf_counter()
            publish_cursor_target(scr_x, scr_y)
            cursor_move_ms = (time.perf_counter() - cursor_move_started) * 1000.0

        if DWELL_ENABLED and not freeze_cursor_for_blink:
            if dwell_position is None:
                dwell_position   = (scr_x, scr_y)
                dwell_start_time = now
                dwell_fired      = False

            dist = np.hypot(scr_x - dwell_position[0], scr_y - dwell_position[1])

            if dist > DWELL_RADIUS:
                dwell_position   = (scr_x, scr_y)
                dwell_start_time = now
                dwell_progress   = 0.0
                dwell_fired      = False
            else:
                elapsed        = now - dwell_start_time
                dwell_progress = min(1.0, elapsed / DWELL_TIME)
                if dwell_progress >= 1.0 and not dwell_fired:
                    if (now - last_dwell_click) >= DWELL_COOLDOWN:
                        if DWELL_DOUBLE_CLICK:
                            enqueue_command('double_click', {'count': 2}, source='dwell')
                        else:
                            enqueue_command('click', source='dwell')
                        last_dwell_click = now
                        dwell_fired      = True
                        print(f"DWELL {'DOUBLE ' if DWELL_DOUBLE_CLICK else ''}CLICK at ({scr_x},{scr_y})")
        else:
            if freeze_cursor_for_blink:
                dwell_position = None
                dwell_fired = False
            dwell_progress = 0.0

        # ── Overlay ───────────────────────────────────────────────────────
        # Nose-tip dot (green) — shows what head-pose is tracking
        nose_px = int(lm[1].x * w)
        nose_py = int(lm[1].y * h)
        cv2.circle(frame, (nose_px, nose_py), 5, (0, 255, 0), -1)
        # Cursor position dot (red) on frame
        cv2.circle(frame, (int(smooth_x * w), int(smooth_y * h)), 8, (0, 0, 255), -1)

        ear_col = (0, 0, 255) if currently_closed else (255, 255, 255)
        blink_label = f"B:{blink_score:.2f}" if blink_score is not None else "B:n/a"
        closed_src = []
        if score_closed:
            closed_src.append("score")
        if ear_closed:
            closed_src.append("ear")
        closed_text = "+".join(closed_src) if closed_src else "open"
        cv2.putText(frame, f"EAR:{ear:.3f} {blink_label} thr:{threshold:.3f} {closed_text}", (10, 30),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.65, ear_col, 2)
        cv2.putText(frame, f"avg:{ear_open_avg:.3f}  cal:{min(calibration_frames,30)}/30",
                    (10, 55), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (180, 180, 180), 1)
        if blink_event_label and now < blink_event_until:
            cv2.putText(frame, blink_event_label, (10, 90),
                        cv2.FONT_HERSHEY_SIMPLEX, 1.0, (0, 255, 255), 3)
        if currently_closed or (now - last_eye_closed_time) < 0.18:
            closed_row = 125 if blink_event_label and now < blink_event_until else 90
            cv2.putText(frame, "EYES CLOSED", (10, closed_row),
                        cv2.FONT_HERSHEY_SIMPLEX, 1.0, (0, 0, 255), 3)

        if dwell_progress > 0 and not dwell_fired:
            angle = int(360 * dwell_progress)
            cv2.ellipse(frame, (int(smooth_x * w), int(smooth_y * h)),
                        (18, 18), -90, 0, angle, (0, 255, 255), 3)
        if blink_detected:
            cv2.putText(frame, "CLICK", (10, 150),
                        cv2.FONT_HERSHEY_SIMPLEX, 1.1, (0, 255, 255), 3)
        if dwell_fired and (now - last_dwell_click) < 0.5:
            cv2.putText(frame, "DWELL CLICK!", (10, 190),
                        cv2.FONT_HERSHEY_SIMPLEX, 1.0, (0, 255, 255), 3)

    else:
        # Face lost — reset all filters and state
        oef_x.reset()
        oef_y.reset()
        reset_relative_controller()
        cur_x          = None
        cur_y          = None
        eyes_closed    = False
        eyes_closed_start_time = None
        blink_closed_frames = 0
        blink_detected = False
        last_short_blink_time = 0.0
        blink_event_label = ""
        blink_event_until = 0.0
        dwell_position = None
        dwell_progress = 0.0
        dwell_fired    = False
        cv2.putText(frame, "No face detected", (10, 30),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 0, 255), 2)

    latest_timing.update({
        'process_ms': (time.perf_counter() - process_started) * 1000.0,
        'mediapipe_ms': mediapipe_ms,
        'cursor_target_ms': cursor_target_ms,
        'cursor_move_ms': cursor_move_ms,
    })

    with telemetry_lock:
        global latest_telemetry_payload
        latest_telemetry_payload = {
            'camera': {'running': tracking_running and not tracking_paused},
            'gaze': {
                'normalized': {'x': current_gaze_x, 'y': current_gaze_y},
                'raw': {'x': current_raw_gaze_x, 'y': current_raw_gaze_y},
                'screen': {'x': current_cursor_x, 'y': current_cursor_y},
                'face_detected': face_detected,
                'blink_detected': blink_detected,
                'blink_event': blink_event_label if now < blink_event_until else "",
                'blink_event_active': now < blink_event_until,
                'eye_openness': eye_openness,
            },
            'cursor': {'enabled': cursor_control_enabled, 'x': current_cursor_x, 'y': current_cursor_y},
            'timing_ms': latest_timing.copy(),
        }

    return frame


# ─────────────────────────────────────────────────────────────────────────────
# FLASK ROUTES & VIDEO STREAMING
# ─────────────────────────────────────────────────────────────────────────────

def camera_processing_loop():
    """Single owner for camera reads and gaze processing."""
    global latest_frame_bytes, face_detected, latest_timing
    frame_count = 0
    fps_window = time.perf_counter()
    while tracking_running:
        try:
            if tracking_paused:
                time.sleep(0.05)
                continue

            capture_started = time.perf_counter()
            ret, frame = camera.read()
            capture_ms = (time.perf_counter() - capture_started) * 1000.0
            if not ret:
                face_detected = False
                time.sleep(0.02)
                continue

            processed_frame = process_frame(frame)
            encode_started = time.perf_counter()
            ret, buffer = cv2.imencode('.jpg', processed_frame, [cv2.IMWRITE_JPEG_QUALITY, 80])
            encode_ms = (time.perf_counter() - encode_started) * 1000.0
            if ret:
                with frame_lock:
                    latest_frame_bytes = buffer.tobytes()
                latest_timing.update({
                    'capture_ms': capture_ms,
                    'encode_ms': encode_ms,
                })
                frame_count += 1
                increment_runtime_metric('camera_frames')

            if _debug_frame and _debug_frame % DEBUG_TIMING_EVERY == 0:
                timing = latest_timing.copy()
                print(
                    "[TIMING] "
                    f"capture={timing['capture_ms']:.1f}ms "
                    f"mediapipe={timing['mediapipe_ms']:.1f}ms "
                    f"target={timing['cursor_target_ms']:.1f}ms "
                    f"move={timing['cursor_move_ms']:.1f}ms "
                    f"process={timing['process_ms']:.1f}ms "
                    f"encode={timing['encode_ms']:.1f}ms"
                )

            now = time.perf_counter()
            if now - fps_window >= 1.0:
                fps = frame_count / max(0.001, now - fps_window)
                update_runtime_metrics(camera_fps=fps)
                frame_count = 0
                fps_window = now

            time.sleep(0.005)
        except Exception as e:
            face_detected = False
            print(f"[WARN] camera loop err: {e}")
            time.sleep(0.1)


def telemetry_publisher_loop():
    """Publish compact UI telemetry at a fixed rate instead of every camera frame."""
    interval = 1.0 / max(1.0, TELEMETRY_HZ)
    while tracking_running:
        with telemetry_lock:
            payload = dict(latest_telemetry_payload or {})

        if payload:
            camera_payload = dict(payload.get('camera') or {})
            camera_payload.update({
                'running': tracking_running and not tracking_paused,
                'fps': runtime_snapshot().get('camera_fps', 0.0),
            })
            payload['camera'] = camera_payload
            payload['runtime'] = {
                'camera_fps': camera_payload.get('fps', 0.0),
                'cursor_fps': runtime_snapshot().get('cursor_fps', 0.0),
                'sse_events_per_second': runtime_snapshot().get('sse_events_per_second', 0.0),
                'command_queue_size': command_bus.size(),
            }
            state_store.update(**payload)
            update_runtime_metrics(last_telemetry_at=time.time())

        time.sleep(interval)


def cursor_output_loop():
    """Single OS cursor output loop consuming latest target only."""
    global last_cursor_move_x, last_cursor_move_y, manual_cursor_target, last_cursor_target_version
    global current_cursor_x, current_cursor_y
    interval = 1.0 / max(1.0, CURSOR_OUTPUT_HZ)
    moved_count = 0
    fps_window = time.perf_counter()
    while tracking_running:
        with state_lock:
            if manual_cursor_target is not None:
                target_x, target_y = manual_cursor_target
                manual_cursor_target = None
                should_move = True
            elif latest_cursor_target_version != last_cursor_target_version:
                target_x = latest_cursor_target_x
                target_y = latest_cursor_target_y
                last_cursor_target_version = latest_cursor_target_version
                should_move = target_x is not None and target_y is not None and cursor_control_enabled and not tracking_paused
            else:
                should_move = False
                target_x = current_cursor_x
                target_y = current_cursor_y

        if should_move:
            if cursor_move_needed(target_x, target_y):
                try:
                    safe_cursor_move(target_x, target_y)
                    last_cursor_move_x = target_x
                    last_cursor_move_y = target_y
                    with state_lock:
                        current_cursor_x = target_x
                        current_cursor_y = target_y
                    moved_count += 1
                    increment_runtime_metric('cursor_moves')
                except Exception as e:
                    print(f"[WARN] cursor move err: {e}")

        now = time.perf_counter()
        if now - fps_window >= 1.0:
            state_store.update(cursor={
                'enabled': cursor_control_enabled,
                'x': current_cursor_x,
                'y': current_cursor_y,
                'fps': moved_count / max(0.001, now - fps_window),
            })
            update_runtime_metrics(cursor_fps=moved_count / max(0.001, now - fps_window))
            moved_count = 0
            fps_window = now

        time.sleep(interval)


def start_tracking_threads():
    """Start the single camera and cursor loops if they are not already running."""
    global tracking_running, tracking_paused, camera_thread, cursor_thread, telemetry_thread
    tracking_paused = False
    tracking_running = True
    command_bus.start()
    scroll_worker.start()
    state_store.update(engine={'running': True, 'paused': False})
    if camera_thread is None or not camera_thread.is_alive():
        camera_thread = threading.Thread(target=camera_processing_loop, daemon=True)
        camera_thread.start()
    if cursor_thread is None or not cursor_thread.is_alive():
        cursor_thread = threading.Thread(target=cursor_output_loop, daemon=True)
        cursor_thread.start()
    if telemetry_thread is None or not telemetry_thread.is_alive():
        telemetry_thread = threading.Thread(target=telemetry_publisher_loop, daemon=True)
        telemetry_thread.start()


def generate_frames():
    """Video stream generator for Flask. Tracking is owned by camera_processing_loop."""
    start_tracking_threads()
    while True:
        with frame_lock:
            frame_bytes = latest_frame_bytes
        if frame_bytes is None:
            time.sleep(0.03)
            continue

        yield (b'--frame\r\n'
               b'Content-Type: image/jpeg\r\n'
               b'Content-Length: ' + str(len(frame_bytes)).encode() + b'\r\n\r\n'
               + frame_bytes + b'\r\n')

        time.sleep(0.03)


def handle_click_command(command):
    count = 2 if command.name == 'double_click' else int(command.payload.get('count', 1))
    safe_mouse_click(count)


def handle_scroll_start_command(command):
    scroll_worker.start_scroll(
        int(command.payload.get('direction', 1)),
        str(command.payload.get('speed', 'normal')),
    )


def handle_scroll_stop_command(command):
    scroll_worker.stop_scroll()


def handle_voice_scroll_state_command(command):
    active = bool(command.payload.get('active', False))
    direction = int(command.payload.get('direction', 0)) if active else 0
    speed = str(command.payload.get('speed', 'normal')) if active else 'normal'
    system_scroll = bool(command.payload.get('system_scroll', False))
    if active and system_scroll:
        scroll_worker.start_scroll(direction, speed)
    else:
        scroll_worker.stop_scroll()
    state_store.merge_dict('voice', {
        'scroll_active': active,
        'scroll_direction': direction,
        'scroll_speed': speed,
        'scroll_source': command.source,
        'scroll_system': system_scroll,
        'scroll_changed_at': time.time(),
    })
    state_store.merge_dict('scroll', {
        'active': active,
        'direction': direction,
        'speed': speed,
        'source': 'voice-system' if system_scroll else 'voice-page',
    })


def handle_scroll_once_command(command):
    amount = int(command.payload.get('amount', 0))
    if amount:
        pyautogui.scroll(max(-400, min(400, amount)))


def handle_cursor_move_command(command):
    global manual_cursor_target
    x = max(0, min(int(command.payload.get('x', current_cursor_x)), screen_w - 1))
    y = max(0, min(int(command.payload.get('y', current_cursor_y)), screen_h - 1))
    with state_lock:
        manual_cursor_target = (x, y)


def handle_cursor_enable_command(command):
    global cursor_control_enabled
    cursor_control_enabled = True
    state_store.update(cursor={'enabled': True})


def handle_cursor_disable_command(command):
    global cursor_control_enabled
    cursor_control_enabled = False
    scroll_worker.stop_scroll()
    state_store.update(cursor={'enabled': False})


def handle_key_press_command(command):
    key = str(command.payload.get('key', '')).strip().lower()
    if not key:
        return
    key_map = {
        'enter': 'enter',
        'return': 'enter',
        'space': 'space',
        'backspace': 'backspace',
        'delete': 'delete',
        'tab': 'tab',
        'escape': 'esc',
        'esc': 'esc',
        'win': 'win',
        'window': 'win',
        'windows': 'win',
    }
    pyautogui.press(key_map.get(key, key))


def handle_type_text_command(command):
    text = str(command.payload.get('text', ''))
    if not text:
        return
    text = text[:500]
    for part in text.splitlines(keepends=True):
        if part.endswith('\n') or part.endswith('\r'):
            chunk = part.rstrip('\r\n')
            if chunk:
                pyautogui.write(chunk, interval=0.005)
            pyautogui.press('enter')
        elif part:
            pyautogui.write(part, interval=0.005)


def register_command_handlers():
    command_bus.register('click', handle_click_command)
    command_bus.register('double_click', handle_click_command)
    command_bus.register('start_scroll', handle_scroll_start_command)
    command_bus.register('stop_scroll', handle_scroll_stop_command)
    command_bus.register('voice_scroll_state', handle_voice_scroll_state_command)
    command_bus.register('scroll_once', handle_scroll_once_command)
    command_bus.register('cursor_move', handle_cursor_move_command)
    command_bus.register('cursor_enable', handle_cursor_enable_command)
    command_bus.register('cursor_disable', handle_cursor_disable_command)
    command_bus.register('key_press', handle_key_press_command)
    command_bus.register('type_text', handle_type_text_command)


register_command_handlers()


@app.route('/')
def dashboard():
    """Serve the main dashboard."""
    return render_template('dashboard.html')


@app.route('/dashboard')
def dashboard_page():
    """Serve the main dashboard."""
    return render_template('dashboard.html')


@app.route('/communication')
def communication():
    """Serve the communication interface."""
    return render_template('communication.html')


@app.route('/debug')
def debug():
    """Serve the debug page."""
    return render_template('debug.html')


@app.route('/setup')
def setup():
    """Serve the setup page."""
    return render_template('setup.html')


@app.route('/favicon.ico')
def favicon():
    """Avoid noisy browser favicon 404s during local development."""
    return ('', 204)


@app.route('/video_feed')
def video_feed():
    """Stream video with eye-tracking overlay."""
    return Response(
        generate_frames(),
        mimetype='multipart/x-mixed-replace; boundary=frame'
    )


@app.route('/camera_feed')
def camera_feed():
    """Stream video with eye-tracking overlay (desktop camera endpoint)."""
    return Response(
        generate_frames(),
        mimetype='multipart/x-mixed-replace; boundary=frame'
    )


@app.route('/events')
def events():
    """Server-sent telemetry stream for UI status without gaze polling."""
    def stream():
        sequence = 0
        event_count = 0
        fps_window = time.perf_counter()
        increment_runtime_metric('sse_clients')
        try:
            while True:
                snapshot = state_store.wait_for_update(sequence, timeout=1.0)
                sequence = snapshot.get('sequence', sequence)
                event_count += 1
                increment_runtime_metric('sse_events')
                now = time.perf_counter()
                if now - fps_window >= 1.0:
                    update_runtime_metrics(sse_events_per_second=event_count / max(0.001, now - fps_window))
                    event_count = 0
                    fps_window = now
                yield 'event: telemetry\n'
                yield 'data: ' + json.dumps(snapshot, separators=(',', ':')) + '\n\n'
        except GeneratorExit:
            raise
        finally:
            with runtime_lock:
                runtime_metrics['sse_clients'] = max(0, runtime_metrics.get('sse_clients', 0) - 1)

    return Response(stream_with_context(stream()), mimetype='text/event-stream')


@app.route('/api/debug/runtime')
def api_debug_runtime():
    """Return lightweight runtime counters for long-run stability debugging."""
    return jsonify({
        'status': 'success',
        'runtime': runtime_snapshot(),
        'telemetry': state_store.snapshot(),
    })


@app.route('/api/status')
def api_status():
    """Get current tracking status."""
    snapshot = state_store.snapshot()
    return jsonify({
        'status': 'success',
        'face_detected': face_detected,
        'blink_detected': blink_detected,
        'eye_openness': eye_openness,
        'calibration_frames': min(calibration_frames, 30),
        'dwell_progress': dwell_progress,
        'system': {
            'camera_running': tracking_running and not tracking_paused,
            'face_detected': face_detected,
            'calibrated': calibration_valid,
            'calibration_enabled': calibration_enabled,
            'cursor_control_enabled': cursor_control_enabled,
        },
        'gaze': {
            'normalized': {'x': current_gaze_x, 'y': current_gaze_y},
            'raw': {'x': current_raw_gaze_x, 'y': current_raw_gaze_y},
        },
        'telemetry': snapshot,
    })


@app.route('/api/command', methods=['POST'])
def api_command():
    """Queue one backend command through the realtime command bus."""
    data = request.get_json(silent=True) or {}
    name = str(data.get('name', '')).strip()
    if not name:
        return jsonify({'status': 'error', 'message': 'name is required'}), 400
    payload = data.get('payload') or {}
    source = str(data.get('source', 'api'))
    queued = enqueue_command(name, payload, source=source)
    return jsonify({'status': 'success', 'queued': queued, 'queue_size': command_bus.size()})


@app.route('/api/gaze/current')
def api_gaze_current():
    """Get current gaze position."""
    with state_lock:
        raw_x = current_raw_gaze_x
        raw_y = current_raw_gaze_y
        smoothed_x = current_gaze_x
        smoothed_y = current_gaze_y
        gaze_x = raw_x if calibration_active else smoothed_x
        gaze_y = raw_y if calibration_active else smoothed_y
        cursor_x = current_cursor_x
        cursor_y = current_cursor_y

    calibrated_x, calibrated_y = apply_calibration_point(raw_x, raw_y)
    active_sample = {'x': gaze_x, 'y': gaze_y}
    raw_sample = {'x': raw_x, 'y': raw_y}
    calibrated_sample = {'x': calibrated_x, 'y': calibrated_y}
    smoothed_sample = {'x': smoothed_x, 'y': smoothed_y}
    screen_sample = {'x': cursor_x, 'y': cursor_y}

    return jsonify({
        'status': 'success',
        'gaze_x': gaze_x,
        'gaze_y': gaze_y,
        'cursor_x': cursor_x,
        'cursor_y': cursor_y,
        'screen_width': screen_w,
        'screen_height': screen_h,
        'face_detected': face_detected,
        'blink_detected': blink_detected,
        'eye_openness': eye_openness,
        'dwell_progress': dwell_progress,
        'fps': CURSOR_OUTPUT_HZ,
        'calibration_active': calibration_active,
        'calibrated': calibration_valid,
        'calibration_enabled': calibration_enabled,
        'gaze_normalized': active_sample,
        'gaze_raw': raw_sample,
        'gaze_calibrated': calibrated_sample,
        'gaze_smoothed': smoothed_sample,
        'gaze_screen': screen_sample,
        'screen': {
            'width': screen_w,
            'height': screen_h,
            'cursor': screen_sample,
        },
        'gaze': {
            'normalized': active_sample,
            'raw': raw_sample,
            'calibrated': calibrated_sample,
            'smoothed': smoothed_sample,
            'screen': screen_sample,
            'source': 'raw' if calibration_active else 'smoothed',
        },
        'calibration': {
            'active': calibration_active,
            'enabled': calibration_enabled,
            'calibrated': calibration_valid,
            'points_collected': len(calibration_samples),
            'points_expected': len(calibration_points) or 9,
            'validation': calibration_last_validation,
        },
        'timestamp': time.time(),
    })


@app.route('/api/android/status')
def api_android_status():
    """Get Android camera status (stub for dashboard compatibility)."""
    return jsonify({
        'connected': False,
        'status': 'Using desktop camera',
        'ip': 'N/A',
        'port': 'N/A',
    })


@app.route('/api/camera/start', methods=['POST'])
def api_camera_start():
    """Start or resume camera processing."""
    start_tracking_threads()
    return jsonify({'status': 'success', 'running': True})


@app.route('/api/camera/stop', methods=['POST'])
def api_camera_stop():
    """Pause camera-driven cursor updates without releasing the camera."""
    global tracking_paused
    tracking_paused = True
    state_store.update(engine={'running': tracking_running, 'paused': True}, camera={'running': False})
    return jsonify({'status': 'success', 'running': False})


@app.route('/api/camera/status')
def api_camera_status():
    """Get camera processing status."""
    return jsonify({
        'status': 'success',
        'running': tracking_running and not tracking_paused,
        'face_detected': face_detected,
        'cursor_control_enabled': cursor_control_enabled,
    })


@app.route('/api/mouse/click', methods=['POST'])
def api_mouse_click():
    """Manual mouse click endpoint used by UI/debug tools."""
    enqueue_command('click', source='api')
    return jsonify({'status': 'success', 'queued': True})


@app.route('/api/mouse/scroll', methods=['POST'])
def api_mouse_scroll():
    """Scroll the currently focused system surface."""
    try:
        data = request.get_json(silent=True) or {}
        amount = int(data.get('amount', 0))
        amount = max(-400, min(400, amount))

        if amount == 0:
            return jsonify({'status': 'error', 'message': 'amount is required'}), 400

        enqueue_command('scroll_once', {'amount': amount}, source='api')
        return jsonify({'status': 'success', 'amount': amount, 'queued': True})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500


@app.route('/api/cursor/move', methods=['POST'])
def api_cursor_move():
    """Queue a manual cursor move through the single cursor output loop."""
    try:
        data = request.get_json(silent=True) or {}
        x = max(0, min(int(data.get('x', current_cursor_x)), screen_w - 1))
        y = max(0, min(int(data.get('y', current_cursor_y)), screen_h - 1))
        enqueue_command('cursor_move', {'x': x, 'y': y}, source='api')
        return jsonify({'status': 'success', 'x': x, 'y': y, 'cursor_enabled': True, 'queued': True})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e), 'cursor_enabled': False}), 500


@app.route('/api/cursor/enable', methods=['POST'])
def api_cursor_enable():
    """Enable automatic gaze-driven cursor movement."""
    enqueue_command('cursor_enable', source='api')
    return jsonify({'status': 'success', 'cursor_enabled': True})


@app.route('/api/cursor/disable', methods=['POST'])
def api_cursor_disable():
    """Disable automatic gaze-driven cursor movement without stopping camera tracking."""
    enqueue_command('cursor_disable', source='api')
    return jsonify({'status': 'success', 'cursor_enabled': False})


@app.route('/api/keyboard/press', methods=['POST'])
def api_keyboard_press():
    """Press one system keyboard key."""
    try:
        data = request.get_json(silent=True) or {}
        key = str(data.get('key', '')).strip().lower()

        if not key:
            return jsonify({'status': 'error', 'message': 'key is required'}), 400

        enqueue_command('key_press', {'key': key}, source='api')
        return jsonify({'status': 'success', 'key': key, 'queued': True})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500


@app.route('/api/keyboard/type', methods=['POST'])
def api_keyboard_type():
    """Type text into the currently focused system application."""
    try:
        data = request.get_json(silent=True) or {}
        text = str(data.get('text', ''))

        if not text:
            return jsonify({'status': 'error', 'message': 'text is required'}), 400

        if len(text) > 500:
            return jsonify({'status': 'error', 'message': 'text is too long'}), 400

        enqueue_command('type_text', {'text': text}, source='api')
        return jsonify({'status': 'success', 'characters': len(text), 'queued': True})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500


@app.route('/api/calibration/start', methods=['POST'])
def api_calibration_start():
    """Start collecting raw gaze samples for calibration."""
    global calibration_active, calibration_points, calibration_samples, calibration_last_validation
    calibration_active = True
    calibration_points = generate_calibration_points()
    calibration_samples = {}
    calibration_last_validation = None
    oef_x.reset()
    oef_y.reset()
    return jsonify({
        'status': 'success',
        'points': calibration_points,
        'point_count': len(calibration_points),
        'screen': {'width': screen_w, 'height': screen_h},
        'calibration': {
            'active': calibration_active,
            'enabled': calibration_enabled,
            'calibrated': calibration_valid,
        },
    })


@app.route('/api/calibration/point', methods=['POST'])
def api_calibration_point():
    """Store samples for one calibration target."""
    global calibration_samples
    data = request.get_json(silent=True) or {}
    target = data.get('target_point')
    samples = data.get('gaze_samples') or []

    if not isinstance(target, (list, tuple)) or len(target) < 2:
        return jsonify({'status': 'error', 'message': 'target_point is required'}), 400

    valid_samples = []
    for sample in samples:
        if isinstance(sample, (list, tuple)) and len(sample) >= 2:
            try:
                valid_samples.append([clamp01(sample[0]), clamp01(sample[1])])
            except (TypeError, ValueError):
                continue

    if not valid_samples:
        return jsonify({'status': 'error', 'message': 'no valid gaze samples'}), 400

    start_idx = max(0, len(valid_samples) // 4)
    end_idx = max(start_idx + 1, 3 * len(valid_samples) // 4)
    stable_samples = valid_samples[start_idx:end_idx]
    target_key = (int(target[0]), int(target[1]))
    sample_mean = np.mean(stable_samples, axis=0).tolist()
    sample_std = np.std(stable_samples, axis=0).tolist()
    calibration_samples[target_key] = {
        'samples': stable_samples,
        'mean': sample_mean,
        'std': sample_std,
        'num_samples': len(stable_samples),
    }

    return jsonify({
        'status': 'success',
        'target_point': list(target_key),
        'mean': {'x': sample_mean[0], 'y': sample_mean[1]},
        'std': {'x': sample_std[0], 'y': sample_std[1]},
        'num_samples': len(stable_samples),
        'points_collected': len(calibration_samples),
        'points_expected': len(calibration_points) or 9,
    })


@app.route('/api/calibration/cancel', methods=['POST'])
def api_calibration_cancel():
    """Cancel calibration and clear partially collected samples."""
    global calibration_active, calibration_points, calibration_samples, calibration_last_validation
    calibration_active = False
    calibration_points = []
    calibration_samples = {}
    calibration_last_validation = None
    return jsonify({
        'status': 'success',
        'calibration': {
            'active': calibration_active,
            'enabled': calibration_enabled,
            'calibrated': calibration_valid,
        },
    })


@app.route('/api/calibration/calculate', methods=['POST'])
def api_calibration_calculate():
    """Calculate and save a raw-gaze-to-normalized-screen mapping."""
    global calibration_active, calibration_matrix, calibration_valid, calibration_last_validation, calibration_enabled

    if len(calibration_samples) < 4:
        calibration_active = False
        calibration_valid = False
        calibration_last_validation = {
            'valid': False,
            'points_tested': len(calibration_samples),
            'error': 'Need at least 4 calibration points',
        }
        return jsonify({
            'status': 'error',
            'message': 'Need at least 4 calibration points',
            'calibrated': False,
            'validation': calibration_last_validation,
        }), 400

    try:
        gaze_points = []
        screen_points = []
        for target, data in calibration_samples.items():
            gaze_points.append(data['mean'])
            screen_points.append([
                clamp01(target[0] / max(1, screen_w - 1)),
                clamp01(target[1] / max(1, screen_h - 1)),
            ])

        gaze_points = np.array(gaze_points, dtype=float)
        screen_points = np.array(screen_points, dtype=float)
        X = np.hstack([gaze_points, np.ones((gaze_points.shape[0], 1))])
        calibration_matrix, _, _, _ = np.linalg.lstsq(X, screen_points, rcond=None)

        predicted = X @ calibration_matrix
        errors = np.linalg.norm((predicted - screen_points) * np.array([screen_w, screen_h]), axis=1)
        validation = {
            'points_tested': int(len(errors)),
            'mean_error': float(np.mean(errors)),
            'max_error': float(np.max(errors)),
            'valid': bool(np.mean(errors) < 120.0),
        }
        calibration_last_validation = validation

        sample_payload = {
            f'{target[0]},{target[1]}': {
                'samples': data['samples'],
                'mean': data['mean'],
                'std': data.get('std', [0.0, 0.0]),
                'num_samples': data['num_samples'],
            }
            for target, data in calibration_samples.items()
        }

        CALIBRATION_DIR.mkdir(parents=True, exist_ok=True)
        np.savez(
            CALIBRATION_FILE,
            matrix=calibration_matrix,
            points=np.array(calibration_points),
            samples=np.array(sample_payload, dtype=object),
            validation=np.array(validation, dtype=object),
        )
        metadata = {
            'schema': CALIBRATION_SCHEMA,
            'user_profile': 'default',
            'timestamp': datetime.now().isoformat(),
            'screen': {'width': screen_w, 'height': screen_h},
            'grid_size': 3,
            'points': len(calibration_samples),
            'valid': validation['valid'],
            'mean_error': validation['mean_error'],
            'max_error': validation['max_error'],
        }
        CALIBRATION_META_FILE.write_text(json.dumps(metadata, indent=2) + '\n', encoding='utf-8')

        calibration_active = False
        calibration_valid = True
        calibration_enabled = True
        oef_x.reset()
        oef_y.reset()
        return jsonify({
            'status': 'success',
            'calibrated': True,
            'validation': validation,
            'points_collected': len(calibration_samples),
            'points_expected': len(calibration_points) or 9,
            'calibration': {
                'active': calibration_active,
                'enabled': calibration_enabled,
                'calibrated': calibration_valid,
                'valid': validation['valid'],
                'file': str(CALIBRATION_FILE),
            },
        })
    except Exception as e:
        calibration_active = False
        calibration_valid = False
        calibration_last_validation = {
            'valid': False,
            'points_tested': len(calibration_samples),
            'error': str(e),
        }
        return jsonify({'status': 'error', 'message': str(e), 'calibrated': False}), 500


@app.route('/api/test/calibration')
def api_test_calibration():
    """Debug endpoint for checking calibration status."""
    return jsonify({
        'status': 'success',
        'calibration_enabled': calibration_enabled,
        'calibration_active': calibration_active,
        'calibration_matrix_loaded': calibration_matrix is not None,
        'calibration_valid': calibration_valid,
        'points_collected': len(calibration_samples),
        'points_expected': len(calibration_points) or 9,
        'validation': calibration_last_validation,
        'calibration_file': str(CALIBRATION_FILE),
    })


@app.route('/api/cursor/test')
def api_cursor_test():
    """Debug endpoint that reports cursor state without moving through a pattern."""
    return jsonify({
        'status': 'success',
        'cursor_enabled': True,
        'position': {'x': current_cursor_x, 'y': current_cursor_y},
        'moves': [],
    })


@app.route('/api/blink/test-click', methods=['POST'])
def api_blink_test_click():
    """Debug endpoint for verifying the same click path used by double blink."""
    queued = enqueue_command('click', source='debug')
    return jsonify({'status': 'success', 'queued': queued, 'message': 'test click queued'})


@app.route('/api/reset', methods=['POST'])
def api_reset():
    """Reset tracking state."""
    global cur_x, cur_y, dwell_position, dwell_fired, eyes_closed
    global calibration_frames, ear_open_avg, last_blink_time, blink_closed_frames
    global eyes_closed_start_time, last_short_blink_time, last_eye_closed_time, blink_event_label
    global current_raw_gaze_x, current_raw_gaze_y, current_gaze_x, current_gaze_y
    global current_cursor_x, current_cursor_y, last_cursor_move_x, last_cursor_move_y
    global manual_cursor_target
    
    cur_x = None
    cur_y = None
    dwell_position = None
    dwell_fired = False
    eyes_closed = False
    blink_closed_frames = 0
    eyes_closed_start_time = None
    last_short_blink_time = 0.0
    last_eye_closed_time = 0.0
    blink_event_label = ""
    calibration_frames = 0
    ear_open_avg = 0.30
    last_blink_time = 0.0
    current_raw_gaze_x = 0.5
    current_raw_gaze_y = 0.5
    current_gaze_x = 0.5
    current_gaze_y = 0.5
    current_cursor_x = 0
    current_cursor_y = 0
    last_cursor_move_x = None
    last_cursor_move_y = None
    manual_cursor_target = None
    
    oef_x.reset()
    oef_y.reset()
    reset_relative_controller()
    
    return jsonify({'status': 'reset'})


@app.route('/settings', methods=['GET', 'POST'])
def settings():
    """Settings page for calibration and configuration."""
    if request.method == 'POST':
        global HEAD_WEIGHT, GAZE_WEIGHT, BLINK_RATIO, DWELL_TIME
        
        data = request.get_json()
        if 'head_weight' in data:
            HEAD_WEIGHT = float(data['head_weight'])
        if 'gaze_weight' in data:
            GAZE_WEIGHT = float(data['gaze_weight'])
        if 'blink_ratio' in data:
            BLINK_RATIO = float(data['blink_ratio'])
        if 'dwell_time' in data:
            DWELL_TIME = float(data['dwell_time'])
        
        return jsonify({'status': 'updated'})
    
    return render_template('settings.html', 
                          head_weight=HEAD_WEIGHT,
                          gaze_weight=GAZE_WEIGHT,
                          blink_ratio=BLINK_RATIO,
                          dwell_time=DWELL_TIME)


@app.route('/calibration')
def calibration():
    """Calibration interface."""
    return render_template('calibration.html')


if __name__ == '__main__':
    print("=" * 60)
    print("[EYE GAZE TRACKING] - FLASK DASHBOARD")
    print("=" * 60)
    print("[*] Opening browser at http://127.0.0.1:5000")
    print("=" * 60)
    
    # Open browser after small delay
    def open_browser():
        time.sleep(2)
        webbrowser.open('http://127.0.0.1:5000')
    
    browser_thread = threading.Thread(target=open_browser, daemon=True)
    browser_thread.start()
    
    # Run Flask app
    app.run(debug=False, host='127.0.0.1', port=5000, threaded=True, use_reloader=False)
