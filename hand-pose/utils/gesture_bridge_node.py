"""
GestureBridgeNode — HostNode che legge gather_data.out, classifica i gesti
nel tempo e li trasmette via WebSocket alla web app.

Pipeline:
  gather_data.out → GestureBridgeNode.process()
                       → remap landmark al frame intero
                       → recognize_gesture() (label statica per frame)
                       → _TemporalClassifier (pattern nel tempo)
                       → websocket_server.send_event()

Mapping → eventi inviati alla web app:
  PINCH + tolti altri 3 dita       → "pinch_start" (quando tocca) / "pinch_end" (quando rilascia)
  FIVE  + palma si muove orizzontale → "swipe_left" / "swipe_right"
  ONE   + indice si muove verticale  → "scroll" (continuo, value = delta_y)
  FIVE  + palma si muove verticale   → "scroll" (alternativa con palma aperta)
  FIST  (apparizione)                → "reset_view"
  PEACE (apparizione)                → "zoom_in"
  OK    (apparizione)                → "zoom_out"

Formato evento JSON:
  { "type": "gesture", "gesture": "<nome>", "value": <float>, "timestamp": <float> }
"""

import time
from collections import deque
from typing import Optional

import depthai as dai
from depthai_nodes import GatheredData, Predictions
from depthai_nodes.message import Keypoints

from utils.gesture_recognition import recognize_gesture
from utils import websocket_server


# ── Parametri del classificatore temporale ───────────────────────────────────

SWIPE_MIN_DIST   = 0.18   # spostamento orizzontale minimo (norm.) per swipe
SWIPE_MAX_DY     = 0.12   # deriva verticale massima durante uno swipe
SWIPE_WINDOW     = 0.5    # finestra temporale (s) per rilevare lo swipe
SCROLL_DEAD_ZONE = 0.008  # jitter ignorato per lo scroll
SCROLL_SCALE     = 5.0    # amplifica il delta verticale per il client
ONESHOT_COOLDOWN = 1.0    # cooldown tra eventi one-shot (reset/zoom)


class _TemporalClassifier:
    """Converte (label, palm_x, palm_y) per frame in eventi gesture."""

    def __init__(self):
        # (timestamp, label, palm_x, palm_y)
        self._history: deque[tuple[float, str, float, float]] = deque(maxlen=30)
        self._last_oneshot: float = 0.0
        self._last_scroll_y: Optional[float] = None
        self._pinch_active: bool = False  # state machine per il pinch

    def update(self, label: Optional[str], palm_x: float, palm_y: float) -> Optional[dict]:
        now = time.time()
        self._history.append((now, label or "NONE", palm_x, palm_y))

        # ── Pinch (pollice + indice insieme, altre dita chiuse) ───────────────
        if label == "PINCH":
            if not self._pinch_active:
                self._pinch_active = True
                return {"type": "gesture", "gesture": "pinch_start", "value": 1.0, "timestamp": now}
            return None
        else:
            if self._pinch_active:
                self._pinch_active = False
                self._last_scroll_y = None
                return {"type": "gesture", "gesture": "pinch_end", "value": 0.0, "timestamp": now}

        if label == "FIVE":
            return self._check_swipe(now) or self._scroll_delta(palm_y, now)

        if label == "ONE":
            return self._scroll_delta(palm_y, now)

        if label == "FIST":
            return self._oneshot("reset_view", now)

        if label == "PEACE":
            return self._oneshot("zoom_in", now)

        if label == "OK":
            return self._oneshot("zoom_out", now)

        # gesto sconosciuto / nessuna mano: resetta il tracker dello scroll
        self._last_scroll_y = None
        return None

    def _check_swipe(self, now: float) -> Optional[dict]:
        cutoff = now - SWIPE_WINDOW
        recent = [
            (t, lbl, x, y)
            for t, lbl, x, y in self._history
            if t >= cutoff and lbl == "FIVE"
        ]
        if len(recent) < 5:
            return None

        dx = recent[-1][2] - recent[0][2]
        dy_max = max(abs(e[3] - recent[0][3]) for e in recent)

        if abs(dx) < SWIPE_MIN_DIST or dy_max > SWIPE_MAX_DY:
            return None

        gesture = "swipe_right" if dx > 0 else "swipe_left"
        self._history.clear()  # evita doppio firing
        return {"type": "gesture", "gesture": gesture, "value": float(dx), "timestamp": now}

    def _scroll_delta(self, palm_y: float, now: float) -> Optional[dict]:
        if self._last_scroll_y is None:
            self._last_scroll_y = palm_y
            return None

        dy = self._last_scroll_y - palm_y  # positivo = mano sale = scroll up
        self._last_scroll_y = palm_y

        if abs(dy) < SCROLL_DEAD_ZONE:
            return None

        return {"type": "gesture", "gesture": "scroll",
                "value": float(dy * SCROLL_SCALE), "timestamp": now}

    def _oneshot(self, gesture: str, now: float) -> Optional[dict]:
        if now - self._last_oneshot < ONESHOT_COOLDOWN:
            return None
        self._last_oneshot = now
        self._last_scroll_y = None
        return {"type": "gesture", "gesture": gesture, "value": 0.0, "timestamp": now}


# ── HostNode ──────────────────────────────────────────────────────────────────

class GestureBridgeNode(dai.node.HostNode):
    """
    Aggiunta in 3 righe nel main.py:

        from utils.gesture_bridge_node import GestureBridgeNode
        gesture_bridge = pipeline.create(GestureBridgeNode).build(
            gathered_data=gather_data.out,
            padding=PADDING,
            confidence_threshold=CONFIDENCE_THRESHOLD,
        )

    Avvia automaticamente il server WebSocket sulla porta 8765 alla creazione.
    """

    def __init__(self):
        super().__init__()
        self.gathered_data_input = self.createInput()
        self._padding = 0.1
        self._confidence_threshold = 0.7
        self._classifier = _TemporalClassifier()

        # Avvia il server WebSocket (idempotente)
        websocket_server.start(host="0.0.0.0", port=8765)

    def build(
        self,
        gathered_data: dai.Node.Output,
        padding: float = 0.1,
        confidence_threshold: float = 0.5,
    ) -> "GestureBridgeNode":
        self._padding = padding
        self._confidence_threshold = confidence_threshold
        self.link_args(gathered_data)
        return self

    def process(self, gathered_data: dai.Buffer) -> None:
        assert isinstance(gathered_data, GatheredData)

        detections_msg: dai.ImgDetections = gathered_data.reference_data
        detections = detections_msg.detections

        # Processa solo la prima mano valida (la più sicura).
        # Per supportare due mani cambia il `break` con un dispatch per handedness.
        for ix, detection in enumerate(detections):
            keypoints_msg: Keypoints = gathered_data.items[ix]["0"]
            confidence_msg: Predictions = gathered_data.items[ix]["1"]

            if confidence_msg.prediction < self._confidence_threshold:
                continue

            # Centro palma in coordinate full-frame (dal bbox della detection)
            bbox = detection.getBoundingBox()
            palm_x = bbox.center.x
            palm_y = bbox.center.y

            # Remap dei 21 landmark dalle coords del crop a quelle del frame intero
            # (stesso calcolo di AnnotationNode.process)
            w = bbox.size.width
            h = bbox.size.height
            xmin = palm_x - w / 2
            ymin = palm_y - h / 2
            p = self._padding
            slope_x = w + 2 * p
            slope_y = h + 2 * p

            kpts = []
            for kp in keypoints_msg.getKeypoints():
                x = min(max(xmin - p + slope_x * kp.imageCoordinates.x, 0.0), 1.0)
                y = min(max(ymin - p + slope_y * kp.imageCoordinates.y, 0.0), 1.0)
                kpts.append([x, y])

            if len(kpts) < 21:
                continue

            # Classificazione statica (un singolo frame)
            label = recognize_gesture(kpts)

            # Classificazione temporale → eventuale evento
            event = self._classifier.update(label, palm_x, palm_y)

            if event is not None:
                print(f"[gesture] {event['gesture']:<12} "
                      f"value={event['value']:+.3f}  "
                      f"clients={websocket_server.client_count()}")
                websocket_server.send_event(event)

            break  # solo la prima mano per frame
