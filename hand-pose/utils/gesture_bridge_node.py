"""
GestureBridgeNode — HostNode that reads gathered_data.out, classifies gestures
over time and broadcasts them via WebSocket to the web app.

Pipeline:
  gathered_data.out → GestureBridgeNode.process()
                         → remap landmarks to full frame
                         → recognize_gesture()        (per-frame static label)
                         → GestureTracker.update()    (temporal state machine)
                         → websocket_server.send_event()

Events sent to the web app:
  CLICK        →  {"gesture": "click"}
  SWIPE_LEFT   →  {"gesture": "swipe_left"}
  SWIPE_RIGHT  →  {"gesture": "swipe_right"}
  DRAG_LEFT    →  {"gesture": "drag_left"}
  DRAG_RIGHT   →  {"gesture": "drag_right"}
  DRAG_UP      →  {"gesture": "drag_up"}
  DRAG_DOWN    →  {"gesture": "drag_down"}
  BACK         →  {"gesture": "back"}

JSON event format:
  {"type": "gesture", "gesture": "<name>", "timestamp": <float>}
"""

import threading
import time
from collections import deque
from typing import List, Optional

import depthai as dai
import numpy as np
from depthai_nodes import GatheredData, Predictions
from depthai_nodes.message import Keypoints

from utils.gesture_recognition import GestureTracker, recognize_gesture
from utils import websocket_server


_VALID_HANDEDNESS = {"right", "left", "any"}
_DEFAULT_HANDEDNESS = "right"

# Side (px) of the square ROI centered on the palm for depth sampling.
# Median of non-zero values is more robust than a single pixel against
# stereo neural-depth holes and background noise.
_DEPTH_SAMPLE_HALF = 4  # → 9×9 window

# Rolling window for dominant-hand depth samples. Median over this window
# suppresses spikes from stereo neural-depth (background hit instead of palm)
# that would otherwise flip too_close/too_far frame-by-frame.
_DEPTH_HISTORY_LEN = 7
# Consecutive frames required before committing a new distance status,
# preventing brief UI flicker.
_DEPTH_STATUS_DEBOUNCE = 4


def _label_from_prediction(pred: float) -> str:
    """Map model output (Predictions["2"]) to a handedness label.
    MediaPipe Hand Landmarker convention: pred < 0.5 → 'left', else → 'right'."""
    return "left" if pred < 0.5 else "right"


class GestureBridgeNode(dai.node.HostNode):
    """
    Add to main.py:

        from utils.gesture_bridge_node import GestureBridgeNode
        gesture_bridge = pipeline.create(GestureBridgeNode).build(
            gathered_data=gather_data.out,
            padding=PADDING,
            confidence_threshold=CONFIDENCE_THRESHOLD,
        )

    Automatically starts the WebSocket server on port 8766 at construction time.
    """

    def __init__(self):
        super().__init__()
        self.gathered_data_input = self.createInput()
        # Depth input decoupled from gathered_data sync: NeuralDepth and
        # palm-detection have different latencies. Syncing via
        # link_args(gathered, depth) causes internal queue mismatch that stalls
        # the pipeline after tens of thousands of frames. Non-blocking queue
        # of size 1 lets the producer always overwrite the last frame.
        self.depth_input = self.createInput()
        self.depth_input.setBlocking(False)
        self.depth_input.setMaxSize(1)
        self._latest_depth: Optional[dai.ImgFrame] = None

        self._padding = 0.1
        self._confidence_threshold = 0.7
        self._tracker = GestureTracker()

        # Distance range filter (mm). Disabled by default; enabled only when
        # main.py passes a valid depth_frame (replay/single-cam path skips it).
        self._depth_min_mm = 0
        self._depth_max_mm = 0
        self._depth_filter_enabled = False

        # Dominant hand, configurable by the web app via WebSocket.
        self._dominant_hand = _DEFAULT_HANDEDNESS
        self._dominant_lock = threading.Lock()

        # Last depth status sent to the web app; emit only on change.
        self._last_depth_status: Optional[str] = None
        self._depth_history: deque = deque(maxlen=_DEPTH_HISTORY_LEN)
        self._pending_depth_status: Optional[str] = None
        self._pending_depth_count: int = 0

        # Port 8766 — 8765 is already used internally by DepthAI v3.
        websocket_server.start(host="0.0.0.0", port=8766)
        websocket_server.on_message(self._on_ws_message)

    def build(
        self,
        gathered_data: dai.Node.Output,
        depth_frame: Optional[dai.Node.Output] = None,
        padding: float = 0.1,
        confidence_threshold: float = 0.5,
        depth_min_mm: int = 0,
        depth_max_mm: int = 0,
    ) -> "GestureBridgeNode":
        self._padding = padding
        self._confidence_threshold = confidence_threshold
        if depth_frame is not None and depth_max_mm > depth_min_mm > 0:
            self._depth_min_mm = depth_min_mm
            self._depth_max_mm = depth_max_mm
            self._depth_filter_enabled = True
            depth_frame.link(self.depth_input)
        # Only gathered_data drives process(); depth is best-effort, read
        # opportunistically from the non-blocking input.
        self.link_args(gathered_data)
        return self

    def process(self, gathered_data: dai.Buffer) -> None:
        assert isinstance(gathered_data, GatheredData)

        detections = gathered_data.reference_data.detections

        with self._dominant_lock:
            dominant = self._dominant_hand

        # Non-blocking depth pull: reuse cached frame if nothing new arrived.
        # A 1-3 frame lag is imperceptible for the distance filter.
        depth_array = None
        if self._depth_filter_enabled:
            new_depth = self.depth_input.tryGet()
            if new_depth is not None:
                self._latest_depth = new_depth
            depth_array = self._depth_to_array(self._latest_depth)

        processed_any = False
        current_depth_status: Optional[str] = None
        current_depth_mm: Optional[int] = None

        for ix, detection in enumerate(detections):
            keypoints_msg: Keypoints = gathered_data.items[ix]["0"]
            confidence_msg: Predictions = gathered_data.items[ix]["1"]
            handedness_msg: Predictions = gathered_data.items[ix]["2"]

            if confidence_msg.prediction < self._confidence_threshold:
                continue

            hand_label = _label_from_prediction(handedness_msg.prediction)
            if dominant != "any" and hand_label != dominant:
                continue

            bbox = detection.getBoundingBox()

            depth_mm: Optional[int] = None
            if self._depth_filter_enabled:
                depth_mm = self._sample_depth_mm(
                    depth_array, bbox.center.x, bbox.center.y
                )
                if depth_mm is None:
                    # No valid measurement → discard; better miss a frame than
                    # react to a bystander outside the surgical zone.
                    continue
                if depth_mm < self._depth_min_mm:
                    if current_depth_status != "ok":
                        current_depth_status = "too_close"
                        current_depth_mm = depth_mm
                    continue
                if depth_mm > self._depth_max_mm:
                    if current_depth_status != "ok":
                        current_depth_status = "too_far"
                        current_depth_mm = depth_mm
                    continue
                # In-range: "ok" overwrites any prior alert for this frame.
                current_depth_status = "ok"
                current_depth_mm = depth_mm

            kpts = self._remap_landmarks(bbox, keypoints_msg, self._padding)
            if len(kpts) < 21:
                continue

            label = recognize_gesture(kpts)
            tracker_event = self._tracker.update(label, kpts)
            processed_any = True

            if tracker_event is not None:
                event = _to_ws_event(tracker_event)
                depth_str = f"{depth_mm}mm" if depth_mm is not None else "—"
                print(
                    f"[gesture] {event['gesture']:<12}  "
                    f"hand={hand_label}  depth={depth_str}  "
                    f"clients={websocket_server.client_count()}"
                )
                websocket_server.send_event(event)

            break  # process only the first dominant hand per frame

        # No dominant hand in frame: release any suspended PINCH/DRAG session.
        if not processed_any:
            self._tracker.reset()

        if self._depth_filter_enabled:
            self._update_depth_status(current_depth_mm)

    # ── Depth status: smoothing + debounce + WS notify ──────────────────────

    def _update_depth_status(self, sampled_depth_mm: Optional[int]) -> None:
        """Update depth history, compute smoothed status (median), and notify
        the web app only once the new status is stable for
        _DEPTH_STATUS_DEBOUNCE consecutive frames."""
        if sampled_depth_mm is not None:
            self._depth_history.append(int(sampled_depth_mm))
        elif self._depth_history:
            # Gradual decay: remove one old sample instead of clearing the
            # history on a single missed frame (fast motion, palm-detect miss)
            # so short gaps don't reset the estimate.
            self._depth_history.popleft()

        if not self._depth_history:
            candidate = "ok"
            smoothed: Optional[int] = None
        else:
            smoothed = int(np.median(self._depth_history))
            if smoothed < self._depth_min_mm:
                candidate = "too_close"
            elif smoothed > self._depth_max_mm:
                candidate = "too_far"
            else:
                candidate = "ok"

        if candidate == self._last_depth_status:
            self._pending_depth_status = None
            self._pending_depth_count = 0
            return

        if candidate == self._pending_depth_status:
            self._pending_depth_count += 1
        else:
            self._pending_depth_status = candidate
            self._pending_depth_count = 1

        if self._pending_depth_count < _DEPTH_STATUS_DEBOUNCE:
            return

        self._last_depth_status = candidate
        self._pending_depth_status = None
        self._pending_depth_count = 0
        websocket_server.send_event({
            "type": "depth_status",
            "status": candidate,
            "depth_mm": smoothed,
            "min_mm": self._depth_min_mm,
            "max_mm": self._depth_max_mm,
            "timestamp": time.time(),
        })

    # ── Depth helpers ───────────────────────────────────────────────────────

    @staticmethod
    def _depth_to_array(depth_frame: Optional[dai.ImgFrame]) -> Optional[np.ndarray]:
        """Return the depth frame as a (H, W) uint16 array in mm, or None."""
        if depth_frame is None:
            return None
        try:
            arr = depth_frame.getCvFrame()
        except Exception:
            return None
        if arr is None or arr.ndim < 2 or arr.size == 0:
            return None
        return arr

    @staticmethod
    def _sample_depth_mm(
        depth_array: Optional[np.ndarray],
        cx_norm: float,
        cy_norm: float,
    ) -> Optional[int]:
        """Sample depth around the bbox centre and return the median of non-zero
        pixels (0 = no measurement) in mm. Returns None if no valid pixels."""
        if depth_array is None:
            return None
        h, w = depth_array.shape[:2]
        cx = int(round(cx_norm * w))
        cy = int(round(cy_norm * h))
        if not (0 <= cx < w and 0 <= cy < h):
            return None
        x0 = max(0, cx - _DEPTH_SAMPLE_HALF)
        x1 = min(w, cx + _DEPTH_SAMPLE_HALF + 1)
        y0 = max(0, cy - _DEPTH_SAMPLE_HALF)
        y1 = min(h, cy + _DEPTH_SAMPLE_HALF + 1)
        valid = depth_array[y0:y1, x0:x1]
        valid = valid[valid > 0]
        return int(np.median(valid)) if valid.size > 0 else None

    @staticmethod
    def _remap_landmarks(
        bbox,
        kpts_msg: Keypoints,
        padding: float,
    ) -> List[List[float]]:
        """Remap 21 landmarks from crop-relative to full-frame normalised
        coordinates (same transform as AnnotationNode.process)."""
        cx, cy = bbox.center.x, bbox.center.y
        w, h = bbox.size.width, bbox.size.height
        xmin, ymin = cx - w / 2, cy - h / 2
        sx, sy = w + 2 * padding, h + 2 * padding
        return [
            [
                min(max(xmin - padding + sx * kp.imageCoordinates.x, 0.0), 1.0),
                min(max(ymin - padding + sy * kp.imageCoordinates.y, 0.0), 1.0),
            ]
            for kp in kpts_msg.getKeypoints()
        ]

    # ── Handedness control via WebSocket ────────────────────────────────────

    def _on_ws_message(self, msg: dict) -> None:
        """Handle control messages from the web app. Supports `set_handedness`."""
        if msg.get("type") != "set_handedness":
            return
        hand = str(msg.get("hand", "")).lower()
        if hand not in _VALID_HANDEDNESS:
            print(f"[gesture] unknown handedness ignored: {hand!r}")
            return
        with self._dominant_lock:
            if self._dominant_hand == hand:
                return
            self._dominant_hand = hand
        # Fresh session: clear any pending events from the previous hand.
        self._tracker.reset()
        print(f"[gesture] dominant hand = {hand}")


def _to_ws_event(tracker_event: dict) -> dict:
    """Convert a GestureTracker event to the WebSocket JSON format."""
    return {
        "type": "gesture",
        "gesture": tracker_event["type"].lower(),
        "timestamp": time.time(),
    }
