import cv2
import mediapipe as mp
from mediapipe.tasks.python import vision
from mediapipe.tasks.python.core import base_options
import numpy as np
import pyautogui
import time
from flask import Flask, Response, render_template, jsonify, request, send_from_directory
import threading
import webbrowser
import math
import json
from pathlib import Path
from datetime import datetime

app = Flask(__name__, template_folder='ui/templates', static_folder='ui/static')

# ── Camera ────────────────────────────────────────────────────────────────────
CAMERA_FRAME_WIDTH = 1280
CAMERA_FRAME_HEIGHT = 720
CAMERA_DIGITAL_ZOOM = 1.0

camera = cv2.VideoCapture(0)
camera.set(cv2.CAP_PROP_FRAME_WIDTH, CAMERA_FRAME_WIDTH)
camera.set(cv2.CAP_PROP_FRAME_HEIGHT, CAMERA_FRAME_HEIGHT)
camera.set(cv2.CAP_PROP_FPS, 30)

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
CLICK_COOLDOWN     = 1.2
BLINK_FLAG_DUR     = 0.4
ear_open_avg       = 0.30
BLINK_RATIO        = 0.72
calibration_frames = 0
blink_closed_frames = 0
BLINK_MIN_CLOSED_FRAMES = 2
BLINK_EAR_THRESHOLD = 0.20
BLINK_SCORE_THRESHOLD = 0.45
DOUBLE_BLINK_INTERVAL = 0.75
SHORT_BLINK_MIN = 0.04
SHORT_BLINK_MAX = 0.45
eyes_closed_start_time = None
last_short_blink_time = 0.0

# ── Dwell state ───────────────────────────────────────────────────────────────
dwell_position   = None
dwell_start_time = 0.0
dwell_progress   = 0.0
dwell_fired      = False
last_dwell_click = 0.0
DWELL_RADIUS     = 60
DWELL_TIME       = 1.5
DWELL_COOLDOWN   = 1.5
DWELL_ENABLED    = True
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

state_lock = threading.Lock()
frame_lock = threading.Lock()
latest_frame_bytes = None
tracking_running = False
tracking_paused = False
cursor_control_enabled = True
camera_thread = None
cursor_thread = None

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
    global dwell_position, dwell_start_time, dwell_progress, dwell_fired
    global last_dwell_click, calibration_frames, _debug_frame
    global cur_x, cur_y, current_gaze_x, current_gaze_y, current_raw_gaze_x, current_raw_gaze_y
    global current_cursor_x, current_cursor_y, last_cursor_move_x, last_cursor_move_y
    global relative_calibrated, relative_center_offset, relative_face_center_offset

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

    result        = face_landmarker.detect_for_video(mp_image, ts)
    face_detected = bool(result.face_landmarks)

    if face_detected:
        lm = result.face_landmarks[0]

        # ── Blink / EAR: double blink logic from EyeTrackingMouse ─────────
        ear       = get_ear(lm, w, h)
        eye_openness = ear
        blink_score = get_blink_score(result)
        if blink_score is not None:
            threshold = BLINK_SCORE_THRESHOLD
            currently_closed = blink_score >= threshold
            if calibration_frames < 30:
                calibration_frames += 1
        else:
            adaptive_threshold = ear_open_avg * BLINK_RATIO
            threshold = max(BLINK_EAR_THRESHOLD, adaptive_threshold)
            currently_closed = ear < threshold

            if calibration_frames < 30 and not currently_closed:
                ear_open_avg = 0.85 * ear_open_avg + 0.15 * ear
                calibration_frames += 1
            elif not currently_closed:
                ear_open_avg = 0.98 * ear_open_avg + 0.02 * ear

        if currently_closed:
            blink_closed_frames += 1
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
                            pyautogui.click()
                            last_click_time = now
                            print(
                                f">>> DOUBLE BLINK CLICK  EAR={ear:.4f} "
                                f"blink={blink_score if blink_score is not None else -1:.3f} "
                                f"thr={threshold:.4f} dur={closed_duration:.3f}"
                            )
                    else:
                        last_short_blink_time = now
                eyes_closed_start_time = None
                blink_closed_frames = 0
            eyes_closed = False

        if blink_detected and (now - last_blink_time) > BLINK_FLAG_DUR:
            blink_detected = False

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
            try:
                pyautogui.moveTo(scr_x, scr_y, duration=0)
                last_cursor_move_x = scr_x
                last_cursor_move_y = scr_y
            except Exception as e:
                print(f"[WARN] cursor move err: {e}")

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
                            pyautogui.doubleClick(interval=0.08)
                        else:
                            pyautogui.click()
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
        cv2.putText(frame, f"EAR:{ear:.3f} {blink_label} thr:{threshold:.3f}", (10, 30),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.65, ear_col, 2)
        cv2.putText(frame, f"avg:{ear_open_avg:.3f}  cal:{min(calibration_frames,30)}/30",
                    (10, 55), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (180, 180, 180), 1)

        if dwell_progress > 0 and not dwell_fired:
            angle = int(360 * dwell_progress)
            cv2.ellipse(frame, (int(smooth_x * w), int(smooth_y * h)),
                        (18, 18), -90, 0, angle, (0, 255, 255), 3)
        if blink_detected:
            cv2.putText(frame, "BLINK!", (10, 90),
                        cv2.FONT_HERSHEY_SIMPLEX, 1.1, (0, 0, 255), 3)
        if dwell_fired and (now - last_dwell_click) < 0.5:
            cv2.putText(frame, "DWELL CLICK!", (10, 130),
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
        dwell_position = None
        dwell_progress = 0.0
        dwell_fired    = False
        cv2.putText(frame, "No face detected", (10, 30),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 0, 255), 2)

    return frame


# ─────────────────────────────────────────────────────────────────────────────
# FLASK ROUTES & VIDEO STREAMING
# ─────────────────────────────────────────────────────────────────────────────

def camera_processing_loop():
    """Single owner for camera reads and gaze processing."""
    global latest_frame_bytes, face_detected
    while tracking_running:
        try:
            if tracking_paused:
                time.sleep(0.05)
                continue

            ret, frame = camera.read()
            if not ret:
                face_detected = False
                time.sleep(0.02)
                continue

            processed_frame = process_frame(frame)
            ret, buffer = cv2.imencode('.jpg', processed_frame, [cv2.IMWRITE_JPEG_QUALITY, 80])
            if ret:
                with frame_lock:
                    latest_frame_bytes = buffer.tobytes()

            time.sleep(0.005)
        except Exception as e:
            face_detected = False
            print(f"[WARN] camera loop err: {e}")
            time.sleep(0.1)


def cursor_output_loop():
    """Single owner for OS cursor movement."""
    global last_cursor_move_x, last_cursor_move_y, manual_cursor_target
    interval = 1.0 / max(1.0, CURSOR_OUTPUT_HZ)
    while tracking_running:
        with state_lock:
            if manual_cursor_target is not None:
                target_x, target_y = manual_cursor_target
                manual_cursor_target = None
                should_move = True
            else:
                should_move = False
                target_x = current_cursor_x
                target_y = current_cursor_y

        if should_move:
            if cursor_move_needed(target_x, target_y):
                try:
                    pyautogui.moveTo(target_x, target_y, duration=0)
                    last_cursor_move_x = target_x
                    last_cursor_move_y = target_y
                except Exception as e:
                    print(f"[WARN] cursor move err: {e}")

        time.sleep(interval)


def start_tracking_threads():
    """Start the single camera and cursor loops if they are not already running."""
    global tracking_running, tracking_paused, camera_thread, cursor_thread
    tracking_paused = False
    if tracking_running:
        return

    tracking_running = True
    camera_thread = threading.Thread(target=camera_processing_loop, daemon=True)
    cursor_thread = threading.Thread(target=cursor_output_loop, daemon=True)
    camera_thread.start()
    cursor_thread.start()


def generate_frames():
    """Video stream generator for Flask. This drives gaze processing."""
    global tracking_running
    tracking_running = True
    while True:
        ret, frame = camera.read()
        if not ret:
            time.sleep(0.01)
            continue

        processed_frame = process_frame(frame)
        ret, buffer = cv2.imencode('.jpg', processed_frame, [cv2.IMWRITE_JPEG_QUALITY, 80])
        if not ret:
            continue

        frame_bytes = buffer.tobytes()

        yield (b'--frame\r\n'
               b'Content-Type: image/jpeg\r\n'
               b'Content-Length: ' + str(len(frame_bytes)).encode() + b'\r\n\r\n'
               + frame_bytes + b'\r\n')

        time.sleep(0.01)


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


@app.route('/api/status')
def api_status():
    """Get current tracking status."""
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
    })


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
    global tracking_paused, tracking_running
    tracking_running = True
    tracking_paused = False
    return jsonify({'status': 'success', 'running': True})


@app.route('/api/camera/stop', methods=['POST'])
def api_camera_stop():
    """Pause camera-driven cursor updates without releasing the camera."""
    global tracking_paused
    tracking_paused = True
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
    try:
        pyautogui.click()
        return jsonify({'status': 'success'})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500


@app.route('/api/cursor/move', methods=['POST'])
def api_cursor_move():
    """Queue a manual cursor move through the single cursor output loop."""
    global current_cursor_x, current_cursor_y
    try:
        data = request.get_json(silent=True) or {}
        x = max(0, min(int(data.get('x', current_cursor_x)), screen_w - 1))
        y = max(0, min(int(data.get('y', current_cursor_y)), screen_h - 1))
        pyautogui.moveTo(x, y, duration=0)
        with state_lock:
            current_cursor_x = x
            current_cursor_y = y
        return jsonify({'status': 'success', 'x': x, 'y': y, 'cursor_enabled': True})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e), 'cursor_enabled': False}), 500


@app.route('/api/cursor/enable', methods=['POST'])
def api_cursor_enable():
    """Enable automatic gaze-driven cursor movement."""
    global cursor_control_enabled
    cursor_control_enabled = True
    return jsonify({'status': 'success', 'cursor_enabled': True})


@app.route('/api/cursor/disable', methods=['POST'])
def api_cursor_disable():
    """Disable automatic gaze-driven cursor movement without stopping camera tracking."""
    global cursor_control_enabled
    cursor_control_enabled = False
    return jsonify({'status': 'success', 'cursor_enabled': False})


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
    try:
        pyautogui.click()
        return jsonify({'status': 'success', 'message': 'test click fired'})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500


@app.route('/api/reset', methods=['POST'])
def api_reset():
    """Reset tracking state."""
    global cur_x, cur_y, dwell_position, dwell_fired, eyes_closed
    global calibration_frames, ear_open_avg, last_blink_time, blink_closed_frames
    global eyes_closed_start_time, last_short_blink_time
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
