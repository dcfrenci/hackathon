"""
GestureBridgeNode — HostNode che legge gather_data.out, classifica i gesti
nel tempo e li trasmette via WebSocket alla web app.

Pipeline:
  gather_data.out → GestureBridgeNode.process()
                       → remap landmark al frame intero
                       → recognize_gesture() (label statica per frame)
                       → GestureTracker.update()  (pattern nel tempo)
                       → websocket_server.send_event()

Eventi inviati alla web app:
  CLICK        →  {"gesture": "click"}
  SWIPE_LEFT   →  {"gesture": "swipe_left"}
  SWIPE_RIGHT  →  {"gesture": "swipe_right"}
  DRAG_LEFT    →  {"gesture": "drag_left"}    # yaw step verso sx
  DRAG_RIGHT   →  {"gesture": "drag_right"}   # yaw step verso dx
  DRAG_UP      →  {"gesture": "drag_up"}      # roll step in alto
  DRAG_DOWN    →  {"gesture": "drag_down"}    # roll step in basso

Formato evento JSON:
  {"type": "gesture", "gesture": "<nome>", "timestamp": <float>, ...campi extra...}
"""

import time
from typing import Optional

import depthai as dai
from depthai_nodes import GatheredData, Predictions
from depthai_nodes.message import Keypoints

from utils.gesture_recognition import GestureTracker, recognize_gesture
from utils import websocket_server


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
        self._tracker = GestureTracker()

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

            # Remap dei 21 landmark dalle coords del crop a quelle del frame intero
            # (stesso calcolo di AnnotationNode.process)
            bbox = detection.getBoundingBox()
            palm_x = bbox.center.x
            palm_y = bbox.center.y
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

            # Classificazione statica (un singolo frame):
            # "PINCH" (3 dita estese, posa click), "DRAG" (3 dita ripiegate,
            # posa drag), "FIVE" (5 dita aperte, posa swipe) o None.
            label = recognize_gesture(kpts)

            # Classificazione temporale → eventuale evento
            tracker_event = self._tracker.update(label, kpts)
            if tracker_event is None:
                break

            event = _to_ws_event(tracker_event)
            print(f"[gesture] {event['gesture']:<12}  "
                  f"clients={websocket_server.client_count()}")
            websocket_server.send_event(event)

            break  # solo la prima mano per frame


def _to_ws_event(tracker_event: dict) -> dict:
    """Converte l'evento di GestureTracker nel formato JSON WebSocket.
    Tutti gli eventi sono direzionali (CLICK / SWIPE_* / DRAG_*) → niente
    payload extra: il nome basta a identificare l'azione.
    """
    return {
        "type": "gesture",
        "gesture": tracker_event["type"].lower(),
        "timestamp": time.time(),
    }
