"""
gesture_recognition.py — Static pose recognition and temporal gesture classification
for touchless control of a medical imaging interface.

Two-level architecture:

    recognize_gesture(kpts)            → HandPose | None    (single frame)
    GestureTracker.update(label, kpts) → Event | None       (temporal state machine)

Static poses (PINCH, DRAG, FIVE, PEACE_H) are physically distinct: the user
selects the action via finger shape, not motion. The state machine combines the
committed pose (after flicker filtering) with palm movement to emit discrete
events consumed by the bridge node.

Events emitted:
    CLICK         — PINCH released within acceptable duration and movement
    DRAG_*        — DRAG with displacement above threshold (dominant axis,
                    continuous: chainable while holding the pose)
    SWIPE_*       — FIVE with horizontal displacement above threshold
    BACK          — entering PEACE_H pose (one-shot)

Sign convention: dx > 0 = right, dy > 0 = down (image coordinates).
Inversions belong in the bridge / web app layer, not here.
"""

import time
from enum import Enum
from typing import Dict, List, Optional, Tuple

import numpy as np


# =============================================================================
# Public types: static poses and state-machine events
# =============================================================================


class HandPose(str, Enum):
    """Static pose recognised in a single frame."""
    PINCH  = "PINCH"    # thumb+index touching, 3 fingers extended  → click
    DRAG   = "DRAG"     # thumb+index touching, 3 fingers curled    → drag
    FIVE   = "FIVE"     # all 5 fingers extended                    → swipe
    PEACE_H = "PEACE_H" # thumb+index extended at ~90°, others curled → back


class EventType(str, Enum):
    """Events emitted by the temporal state machine."""
    CLICK       = "CLICK"
    DRAG_LEFT   = "DRAG_LEFT"
    DRAG_RIGHT  = "DRAG_RIGHT"
    DRAG_UP     = "DRAG_UP"
    DRAG_DOWN   = "DRAG_DOWN"
    SWIPE_LEFT  = "SWIPE_LEFT"
    SWIPE_RIGHT = "SWIPE_RIGHT"
    BACK        = "BACK"


# After a directional event, block the opposite direction for a short interval
# to prevent the return motion from triggering the reverse gesture.
_OPPOSITE_EVENT: Dict[str, str] = {
    EventType.DRAG_LEFT.value:   EventType.DRAG_RIGHT.value,
    EventType.DRAG_RIGHT.value:  EventType.DRAG_LEFT.value,
    EventType.DRAG_UP.value:     EventType.DRAG_DOWN.value,
    EventType.DRAG_DOWN.value:   EventType.DRAG_UP.value,
    EventType.SWIPE_LEFT.value:  EventType.SWIPE_RIGHT.value,
    EventType.SWIPE_RIGHT.value: EventType.SWIPE_LEFT.value,
}

# One-shot events subject to a post-emission cooldown.
_ONE_SHOT_EVENTS = frozenset({EventType.CLICK.value, EventType.BACK.value})


# =============================================================================
# MediaPipe Hand landmark indices (0–20)
# =============================================================================

WRIST = 0
THUMB_CMC, THUMB_MCP, THUMB_IP, THUMB_TIP = 1, 2, 3, 4
INDEX_MCP, INDEX_PIP, INDEX_DIP, INDEX_TIP = 5, 6, 7, 8
MIDDLE_MCP, MIDDLE_PIP, MIDDLE_DIP, MIDDLE_TIP = 9, 10, 11, 12
RING_MCP, RING_PIP, RING_DIP, RING_TIP = 13, 14, 15, 16
LITTLE_MCP, LITTLE_PIP, LITTLE_DIP, LITTLE_TIP = 17, 18, 19, 20

# (tip, dip, pip) triples for the 4 long fingers.
_LONG_FINGERS_TDP: Tuple[Tuple[int, int, int], ...] = (
    (INDEX_TIP,  INDEX_DIP,  INDEX_PIP),
    (MIDDLE_TIP, MIDDLE_DIP, MIDDLE_PIP),
    (RING_TIP,   RING_DIP,   RING_PIP),
    (LITTLE_TIP, LITTLE_DIP, LITTLE_PIP),
)

# (tip, pip) pairs for the non-index fingers (middle, ring, little),
# used in PINCH/DRAG/PEACE_H checks for the "other 3 fingers".
_OTHER_FINGERS_TP: Tuple[Tuple[int, int], ...] = (
    (MIDDLE_TIP, MIDDLE_PIP),
    (RING_TIP,   RING_PIP),
    (LITTLE_TIP, LITTLE_PIP),
)


# =============================================================================
# Classification thresholds (empirically tuned)
# =============================================================================

# PINCH/DRAG: thumb-index distance / hand_size below this → fingers "touching".
_PINCH_THRESHOLD = 0.40

# Rotation-invariant tolerances for curled/extended fingers. Values > 1 create
# a dead zone that prevents flicker at the boundary between poses.
_FINGER_CURLED_TOL   = 1.05
_FINGER_EXTENDED_TOL = 1.10

# Thumb extended: high sum of 3 joint angles AND high distance ratio.
_THUMB_ANGLE_SUM_THRESHOLD  = 460.0
_THUMB_DIST_RATIO_THRESHOLD = 1.2

# PEACE_H ("L shape"): angle between thumb and index directions must be ~90°,
# and the two tips must be well separated.
_PEACE_H_ANGLE_MIN  = 60.0   # degrees
_PEACE_H_ANGLE_MAX  = 120.0  # degrees
_PEACE_H_MIN_SPREAD = 0.50   # thumb-index distance / hand_size (> pinch threshold)


# =============================================================================
# Geometric helpers
# =============================================================================


def _distance(a: np.ndarray, b: np.ndarray) -> float:
    return float(np.linalg.norm(a - b))


def _angle_deg(a: np.ndarray, b: np.ndarray, c: np.ndarray) -> float:
    """Angle ∠abc in degrees, with b as vertex."""
    ba = a - b
    bc = c - b
    cosine = np.dot(ba, bc) / (np.linalg.norm(ba) * np.linalg.norm(bc))
    return float(np.degrees(np.arccos(np.clip(cosine, -1.0, 1.0))))


def _hand_size(kpts: np.ndarray) -> float:
    """Wrist → middle-MCP distance: a scale reference invariant to camera distance."""
    return _distance(kpts[WRIST], kpts[MIDDLE_MCP])


def _finger_curled(
    kpts: np.ndarray, tip: int, pip: int, tol: float = _FINGER_CURLED_TOL,
) -> bool:
    """Rotation-invariant curl check: tip closer to wrist than PIP.
    Replaces the y-based check (tip.y < pip.y) that breaks on rotated hands."""
    return _distance(kpts[tip], kpts[WRIST]) < _distance(kpts[pip], kpts[WRIST]) * tol


def _finger_extended(
    kpts: np.ndarray, tip: int, pip: int, tol: float = _FINGER_EXTENDED_TOL,
) -> bool:
    """Rotation-invariant extension check. tol > 1 creates a dead zone between
    extended and curled, preventing flicker on intermediate poses."""
    return _distance(kpts[tip], kpts[WRIST]) > _distance(kpts[pip], kpts[WRIST]) * tol


def _thumb_extended(kpts: np.ndarray) -> bool:
    """Thumb extended: high sum of 3 joint angles AND high distance ratio
    (THUMB_IP→INDEX_MCP) / (THUMB_MCP→THUMB_IP)."""
    angle_sum = (
        _angle_deg(kpts[WRIST],     kpts[THUMB_CMC], kpts[THUMB_MCP])
        + _angle_deg(kpts[THUMB_CMC], kpts[THUMB_MCP], kpts[THUMB_IP])
        + _angle_deg(kpts[THUMB_MCP], kpts[THUMB_IP],  kpts[THUMB_TIP])
    )
    if angle_sum <= _THUMB_ANGLE_SUM_THRESHOLD:
        return False
    distance_ratio = (
        _distance(kpts[THUMB_IP], kpts[INDEX_MCP])
        / _distance(kpts[THUMB_MCP], kpts[THUMB_IP])
    )
    return distance_ratio > _THUMB_DIST_RATIO_THRESHOLD


def _thumb_index_angle(kpts: np.ndarray) -> float:
    """Angle between thumb direction (MCP→TIP) and index direction (MCP→TIP).
    Used to require ~90° for the PEACE_H pose."""
    thumb_dir = kpts[THUMB_TIP] - kpts[THUMB_MCP]
    index_dir = kpts[INDEX_TIP] - kpts[INDEX_MCP]
    norm = np.linalg.norm(thumb_dir) * np.linalg.norm(index_dir)
    if norm < 1e-6:
        return 0.0
    cosine = np.clip(np.dot(thumb_dir, index_dir) / norm, -1.0, 1.0)
    return float(np.degrees(np.arccos(cosine)))


# =============================================================================
# Static pose recognition (single frame)
# =============================================================================


def _classify_pinch_or_drag(kpts: np.ndarray) -> Optional[HandPose]:
    """Distinguish PINCH from DRAG when thumb+index are touching: the difference
    is the other 3 fingers (extended vs curled). Intermediate pose → None to
    prevent flicker during transitions."""
    pinch_dist = _distance(kpts[THUMB_TIP], kpts[INDEX_TIP]) / _hand_size(kpts)
    if pinch_dist >= _PINCH_THRESHOLD:
        return None
    if all(_finger_curled(kpts, tip, pip) for tip, pip in _OTHER_FINGERS_TP):
        return HandPose.DRAG
    if all(_finger_extended(kpts, tip, pip) for tip, pip in _OTHER_FINGERS_TP):
        return HandPose.PINCH
    return None


def _is_peace_h(kpts: np.ndarray) -> bool:
    """PEACE_H ("L shape"): thumb + index extended at ~90°, well separated,
    other 3 fingers curled. Angle and minimum spread prevent confusion with
    DRAG (fingers close) and intermediate poses (wrong angle)."""
    spread = _distance(kpts[THUMB_TIP], kpts[INDEX_TIP]) / _hand_size(kpts)
    if spread < _PEACE_H_MIN_SPREAD:
        return False
    angle = _thumb_index_angle(kpts)
    return (
        _PEACE_H_ANGLE_MIN <= angle <= _PEACE_H_ANGLE_MAX
        and _thumb_extended(kpts)
        and _finger_extended(kpts, INDEX_TIP, INDEX_PIP)
        and all(_finger_curled(kpts, tip, pip) for tip, pip in _OTHER_FINGERS_TP)
    )


def _is_five(kpts: np.ndarray) -> bool:
    """FIVE: all 5 fingers extended, rotation-invariant.

    The y-based check (tip.y < dip.y < pip.y) breaks when the hand tilts
    during a lateral swipe: the y-ordering fails for a few frames, committed
    drops out of FIVE, and the swipe anchor resets mid-motion — causing the
    next step to fire in the wrong direction.

    False positives do not increase: PINCH/DRAG and PEACE_H still fail
    _finger_extended on at least one finger and take higher precedence in
    recognize_gesture."""
    if not _thumb_extended(kpts):
        return False
    return all(
        _finger_extended(kpts, tip, pip)
        for tip, _dip, pip in _LONG_FINGERS_TDP
    )


def recognize_gesture(kpts: List[Tuple[float, float]]) -> Optional[str]:
    """Classify the static pose of a single frame.

    Precedence order (resolves ambiguous poses):
        1. PINCH / DRAG   (thumb+index touching — dominant when active)
        2. PEACE_H
        3. FIVE
        4. None           (no pose recognised or intermediate state)

    Returns the pose name as a string (compatible with the bridge node),
    or None. Internal consumers should use the HandPose enum.
    """
    arr = np.asarray(kpts, dtype=float)
    # Guard against degenerate frames (wrist == middle-MCP) that occur at
    # model warm-up or on borderline detections: hand_size = 0 → ZeroDivisionError.
    if _hand_size(arr) < 1e-6:
        return None

    pinch_or_drag = _classify_pinch_or_drag(arr)
    if pinch_or_drag is not None:
        return pinch_or_drag.value
    if _is_peace_h(arr):
        return HandPose.PEACE_H.value
    if _is_five(arr):
        return HandPose.FIVE.value
    return None


# =============================================================================
# State-machine helpers: flicker filters and temporal gates
# =============================================================================


class _StickyPoseLock:
    """Filters frame-by-frame flicker from the raw recognize_gesture label.

    The classifier can oscillate between adjacent labels for 1 frame at
    threshold boundaries. The committed pose changes only after K consecutive
    frames of the same new label, where K depends on the transition type
    (see _TRANSITION_K)."""

    _DEFAULT_K = 2
    # Tuned to absorb 1-frame flicker without adding perceptible lag
    # (~67 ms at 30 fps for K=2).
    _TRANSITION_K: Dict[Tuple[Optional[str], Optional[str]], int] = {
        # Hand just entered frame: be reactive, K=1.
        (None, HandPose.PINCH.value):   1,
        (None, HandPose.DRAG.value):    1,
        (None, HandPose.FIVE.value):    1,
        (None, HandPose.PEACE_H.value): 1,
        # DRAG→FIVE: large physical transition; 1-2 frame occurrence is flicker.
        (HandPose.DRAG.value, HandPose.FIVE.value): 3,
        # PEACE_H↔FIVE: higher K to reduce false positives during transitions
        # that pass through a PEACE_H-like intermediate shape.
        (HandPose.FIVE.value,   HandPose.PEACE_H.value): 3,
        (HandPose.PEACE_H.value, HandPose.FIVE.value):   3,
        # DRAG/PINCH→PEACE_H: releasing a pinch/drag can briefly resemble
        # PEACE_H (thumb+index opening). K=4 (~133 ms at 30 fps) prevents
        # a spurious BACK immediately after a click or drag.
        (HandPose.DRAG.value,  HandPose.PEACE_H.value): 4,
        (HandPose.PINCH.value, HandPose.PEACE_H.value): 4,
    }

    def __init__(self) -> None:
        self.committed: Optional[str] = None
        self._candidate: Optional[str] = None
        self._candidate_count: int = 0

    def reset(self) -> None:
        self.committed = None
        self._candidate = None
        self._candidate_count = 0

    def update(self, raw_label: Optional[str]) -> bool:
        """Advance with the new raw label. Returns True if committed changed."""
        if raw_label == self.committed:
            self._candidate = None
            self._candidate_count = 0
            return False

        if raw_label != self._candidate:
            self._candidate = raw_label
            self._candidate_count = 1
        else:
            self._candidate_count += 1

        k_required = self._TRANSITION_K.get(
            (self.committed, raw_label), self._DEFAULT_K
        )
        if self._candidate_count >= k_required:
            self.committed = raw_label
            self._candidate = None
            self._candidate_count = 0
            return True
        return False


class _PositionSmoother:
    """EMA on palm position to filter landmark jitter."""

    def __init__(self, alpha: float):
        self._alpha = alpha
        self._smoothed: Optional[np.ndarray] = None

    def reset(self) -> None:
        self._smoothed = None

    def update(self, raw_pos: np.ndarray, force_reset: bool = False) -> np.ndarray:
        """Return filtered position. force_reset=True clears state on pose change
        to prevent the EMA from "chasing" the previous position and producing
        spurious motion in the first frames of a new pose."""
        if self._smoothed is None or force_reset:
            self._smoothed = raw_pos.copy()
        else:
            self._smoothed = self._alpha * raw_pos + (1.0 - self._alpha) * self._smoothed
        return self._smoothed.copy()


class _DirectionLock:
    """After a directional event, block its opposite for N seconds.
    Same direction and other axes remain available."""

    def __init__(self, lock_seconds: float):
        self._lock_seconds = lock_seconds
        self._until: Dict[str, float] = {}

    def reset(self) -> None:
        self._until.clear()

    def is_locked(self, event_type: str, now: float) -> bool:
        expiry = self._until.get(event_type)
        return expiry is not None and now < expiry

    def apply(self, event_type: str, now: float) -> None:
        opposite = _OPPOSITE_EVENT.get(event_type)
        if opposite is not None:
            self._until[opposite] = now + self._lock_seconds


# =============================================================================
# Main state machine
# =============================================================================


class GestureTracker:
    """Stateful tracker that combines the current committed pose with palm
    movement to emit discrete events:

      • CLICK                      — PINCH released within acceptable duration/movement
      • DRAG_{LEFT,RIGHT,UP,DOWN}  — DRAG with displacement above threshold
      • SWIPE_{LEFT,RIGHT}         — FIVE with horizontal displacement above threshold
      • BACK                       — entering PEACE_H (one-shot)

    PINCH and DRAG are physically distinct poses: the user selects click vs drag
    via finger shape. PINCH→DRAG does NOT emit CLICK; DRAG→PINCH emits nothing.

    Usage:
        tracker = GestureTracker()
        for frame_kpts in stream:
            label = recognize_gesture(frame_kpts)
            event = tracker.update(label, frame_kpts)
            if event is not None:
                send(event)
    """

    DEFAULT_SWIPE_THRESHOLD            = 0.65
    DEFAULT_DRAG_THRESHOLD             = 0.35
    DEFAULT_CLICK_MAX_MOVEMENT         = 1.0
    DEFAULT_CLICK_MIN_DURATION         = 0.08
    DEFAULT_CLICK_MAX_DURATION         = 1.5
    DEFAULT_RELEASE_CONFIRMATION_FRAMES = 2
    DEFAULT_GESTURE_COOLDOWN           = 0.5
    DEFAULT_DIRECTION_LOCK             = 2.0
    DEFAULT_SMOOTHING_ALPHA            = 0.45

    def __init__(
        self,
        swipe_threshold: float = DEFAULT_SWIPE_THRESHOLD,
        drag_threshold: float = DEFAULT_DRAG_THRESHOLD,
        click_max_movement: float = DEFAULT_CLICK_MAX_MOVEMENT,
        click_min_duration: float = DEFAULT_CLICK_MIN_DURATION,
        click_max_duration: float = DEFAULT_CLICK_MAX_DURATION,
        release_confirmation_frames: int = DEFAULT_RELEASE_CONFIRMATION_FRAMES,
        gesture_cooldown_seconds: float = DEFAULT_GESTURE_COOLDOWN,
        direction_lock_seconds: float = DEFAULT_DIRECTION_LOCK,
        smoothing_alpha: float = DEFAULT_SMOOTHING_ALPHA,
    ):
        self._swipe_threshold  = swipe_threshold
        self._drag_threshold   = drag_threshold
        self._click_max_movement = click_max_movement
        self._click_min_duration = click_min_duration
        self._click_max_duration = click_max_duration
        self._release_confirmation_frames = release_confirmation_frames
        self._gesture_cooldown = gesture_cooldown_seconds

        self._pose_lock = _StickyPoseLock()
        self._smoother  = _PositionSmoother(smoothing_alpha)
        self._dir_lock  = _DirectionLock(direction_lock_seconds)

        # Back cooldown lives outside session state so it survives the resets
        # that happen during _gesture_cooldown; otherwise the effective window
        # collapses to _gesture_cooldown.
        self._back_cooldown_until = 0.0

        self._reset_session_state()

    # ── Public API ──────────────────────────────────────────────────────────

    def reset(self) -> None:
        """Full reset (e.g. when the hand leaves the frame)."""
        self._pose_lock.reset()
        self._smoother.reset()
        self._dir_lock.reset()
        self._back_cooldown_until = 0.0
        self._reset_session_state()

    def update(self, gesture: Optional[str], kpts) -> Optional[dict]:
        """Advance the state machine by one frame.
        `gesture` is the raw label from recognize_gesture (may be None).
        Returns {"type": <EventType>} or None."""
        kpts_arr = np.asarray(kpts, dtype=float)
        hand_size = _hand_size(kpts_arr)
        if hand_size < 1e-6:
            return None
        raw_pos = kpts_arr[MIDDLE_MCP].astype(float)

        commit_changed = self._pose_lock.update(gesture)
        if commit_changed:
            # Pose change = implicit release: reset direction locks so the user
            # can repeat a gesture without waiting for the lock to expire.
            self._dir_lock.reset()
        committed = self._pose_lock.committed

        pos = self._smoother.update(raw_pos, force_reset=commit_changed)
        now = time.monotonic()

        if now < self._cooldown_until:
            self._reset_session_state()
            return None

        if committed == HandPose.PINCH.value:
            self._update_pinch_session(pos, hand_size, now)
            event: Optional[dict] = None
        elif committed == HandPose.DRAG.value:
            event = self._update_drag_session(pos, hand_size, now)
        else:
            event = self._update_release(now)

        # After a CLICK, block BACK for 1.5 s: releasing a pinch naturally
        # opens the fingers through a PEACE_H-like shape.
        if event is not None and event["type"] == EventType.CLICK.value:
            self._back_cooldown_until = now + 1.5

        if (commit_changed
                and committed == HandPose.PEACE_H.value
                and event is None
                and now >= self._back_cooldown_until):
            event = {"type": EventType.BACK.value}

        five_event = self._update_five_session(committed, pos, hand_size, now)
        if five_event is not None:
            event = five_event

        if event is not None and event["type"] in _ONE_SHOT_EVENTS:
            self._cooldown_until = now + self._gesture_cooldown

        self._prev_committed_pose = committed
        return event

    # ── Session state ───────────────────────────────────────────────────────

    def _reset_session_state(self) -> None:
        """Reset all per-pose session state. Filters (smoother, pose lock,
        direction lock) have their own reset and are not touched here."""
        self._in_pinch = False
        self._non_pinch_count = 0
        self._pinch_start_time = 0.0
        self._pinch_total_movement = 0.0
        self._prev_pinch_pos: Optional[np.ndarray] = None

        self._in_drag = False
        self._drag_anchor: Optional[np.ndarray] = None

        self._five_start_pos: Optional[np.ndarray] = None
        self._prev_committed_pose: Optional[str] = None

        self._cooldown_until = 0.0

    # ── Per-pose branches (called from update) ───────────────────────────────

    def _update_pinch_session(
        self, pos: np.ndarray, hand_size: float, now: float,
    ) -> None:
        """Accumulate movement during PINCH. CLICK is emitted on release,
        not here."""
        self._non_pinch_count = 0
        if not self._in_pinch:
            self._in_pinch = True
            self._pinch_start_time = now
            self._pinch_total_movement = 0.0
            self._prev_pinch_pos = pos
        else:
            if self._prev_pinch_pos is not None:
                dx = float((pos[0] - self._prev_pinch_pos[0]) / hand_size)
                dy = float((pos[1] - self._prev_pinch_pos[1]) / hand_size)
                self._pinch_total_movement += (dx * dx + dy * dy) ** 0.5
            self._prev_pinch_pos = pos
        if self._in_drag:
            self._in_drag = False
            self._drag_anchor = None

    def _update_drag_session(
        self, pos: np.ndarray, hand_size: float, now: float,
    ) -> Optional[dict]:
        """Emit DRAG_* when displacement from anchor exceeds threshold on the
        dominant axis. Anchor resets after each event for chained steps."""
        if self._in_pinch:
            # PINCH→DRAG: user intended to drag, not click.
            self._in_pinch = False
            self._non_pinch_count = 0
            self._prev_pinch_pos = None

        if not self._in_drag:
            self._in_drag = True
            self._drag_anchor = pos
            return None
        if self._drag_anchor is None:
            return None

        dx = float((pos[0] - self._drag_anchor[0]) / hand_size)
        dy = float((pos[1] - self._drag_anchor[1]) / hand_size)
        candidate = self._classify_drag_direction(dx, dy)
        if candidate is None:
            return None

        # Reset anchor even when direction-locked: the motion is "consumed"
        # so the return movement doesn't bounce the opposite event.
        self._drag_anchor = pos
        if self._dir_lock.is_locked(candidate, now):
            return None
        self._dir_lock.apply(candidate, now)
        return {"type": candidate}

    def _classify_drag_direction(self, dx: float, dy: float) -> Optional[str]:
        """Dominant axis + threshold → DRAG_* event type or None."""
        if abs(dx) >= abs(dy):
            if abs(dx) > self._drag_threshold:
                return EventType.DRAG_RIGHT.value if dx > 0 else EventType.DRAG_LEFT.value
            return None
        if abs(dy) > self._drag_threshold:
            return EventType.DRAG_DOWN.value if dy > 0 else EventType.DRAG_UP.value
        return None

    def _update_release(self, now: float) -> Optional[dict]:
        """Non-PINCH/DRAG pose: handle release of an active session.
        Emits CLICK if the preceding PINCH was within acceptable limits."""
        if self._in_drag:
            self._in_drag = False
            self._drag_anchor = None

        if not self._in_pinch:
            return None

        # Hysteresis: require N consecutive non-PINCH frames to confirm release
        # and filter single-frame classifier misses during a real click.
        self._non_pinch_count += 1
        if self._non_pinch_count < self._release_confirmation_frames:
            return None

        duration = now - self._pinch_start_time
        movement = self._pinch_total_movement
        self._in_pinch = False
        self._non_pinch_count = 0
        self._prev_pinch_pos = None

        is_click = (
            self._click_min_duration <= duration < self._click_max_duration
            and movement < self._click_max_movement
        )
        return {"type": EventType.CLICK.value} if is_click else None

    def _update_five_session(
        self,
        committed: Optional[str],
        pos: np.ndarray,
        hand_size: float,
        now: float,
    ) -> Optional[dict]:
        """Track FIVE pose and emit SWIPE_* when horizontal displacement from
        the entry position exceeds the threshold."""
        five = HandPose.FIVE.value
        prev = self._prev_committed_pose

        if committed == five and prev != five:
            self._five_start_pos = pos
            return None

        if committed != five and prev == five:
            self._five_start_pos = None
            return None

        if committed == five and self._five_start_pos is not None:
            dx_norm = float((pos[0] - self._five_start_pos[0]) / hand_size)
            if abs(dx_norm) <= self._swipe_threshold:
                return None
            swipe = (EventType.SWIPE_RIGHT.value if dx_norm > 0
                     else EventType.SWIPE_LEFT.value)
            self._five_start_pos = pos  # consume the movement
            if self._dir_lock.is_locked(swipe, now):
                return None
            self._dir_lock.apply(swipe, now)
            return {"type": swipe}

        return None


__all__ = [
    "HandPose",
    "EventType",
    "recognize_gesture",
    "GestureTracker",
]


# =============================================================================
# Live tuning guide
# =============================================================================
#
# swipe_threshold = 0.65
#   Horizontal displacement (in hand_size units) to trigger SWIPE.
#   0.65 ≈ one hand-width. Raise to 1.0 if swipes fire too easily;
#   lower to 0.6 if they don't fire reliably.
#
# drag_threshold = 0.35
#   Displacement (in hand_size) from anchor to emit DRAG_*.
#   Smaller than swipe to allow fine-grained yaw/roll control in 3D view.
#   Anchor resets after each event → steps are chainable.
#
# click_max_duration = 1.5
#   Max PINCH duration for a CLICK. Keep it generous: with sticky lock +
#   flicker, a real click can last 0.6–0.9 s in committed time.
#
# release_confirmation_frames = 2
#   At 30 fps ≈ 66 ms of release latency. Increase to 3 (100 ms) if the
#   classifier is noisy. At 60 fps you can use 3–4 without noticeable lag.
#
# smoothing_alpha = 0.45
#   EMA on middle-MCP position. 1.0 = no smoothing (noisy → spurious drag
#   at rest); lower values filter jitter but add lag. 0.45 is a good
#   compromise at 30 fps.
#
# direction_lock_seconds = 2.0
#   After a drag/swipe, the opposite event is blocked for this duration.
#   Resets on pose change (hand lowered and raised) → user can "reload"
#   without waiting for the full lock to expire.
#
# Sign of dx/dy depends on whether the DepthAI pipeline mirrors the frame.
# If the 3D model rotates the wrong way, negate in the bridge layer,
# not in this module.
