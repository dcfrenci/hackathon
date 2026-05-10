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
  BACK         →  {"gesture": "back"}         # navigazione history (history.back)

Formato evento JSON:
  {"type": "gesture", "gesture": "<nome>", "timestamp": <float>, ...campi extra...}
"""

import threading
import time
from collections import deque
from typing import Optional

import depthai as dai
import numpy as np
from depthai_nodes import GatheredData, Predictions
from depthai_nodes.message import Keypoints

from utils.gesture_recognition import GestureTracker, recognize_gesture
from utils import websocket_server


# Etichetta della mano dominante: la non-dominante viene scartata.
# "right" / "left" filtrano; "any" disattiva il filtro (entrambe le mani).
_VALID_HANDEDNESS = {"right", "left", "any"}
_DEFAULT_HANDEDNESS = "right"

# Lato (in pixel) del ROI quadrato centrato sul palmo per il campionamento
# della depth: la mediana dei valori non-zero in questa finestra è più
# robusta del singolo pixel centrale a fori/rumore della depth map.
_DEPTH_SAMPLE_HALF = 4  # → finestra 9x9

# Smoothing temporale della depth della mano dominante: teniamo gli ultimi
# N campioni e usiamo la mediana per decidere lo stato. Senza questo, la
# stereo neural-depth produce picchi (sfondo invece del palmo) che fanno
# alternare too_close/too_far frame per frame quando la mano è lontana.
_DEPTH_HISTORY_LEN = 7
# Frame consecutivi richiesti prima di committare un nuovo stato distanza
# (debounce): evita brevi flicker visibili nell'UI.
_DEPTH_STATUS_DEBOUNCE = 4


def _label_from_prediction(pred: float) -> str:
    """Mappa l'output del modello (Predictions["2"]) sulla label letterale.
    Convenzione MediaPipe Hand Landmarker (vedi annotation_node):
    pred < 0.5 → 'left', altrimenti → 'right'."""
    return "left" if pred < 0.5 else "right"


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
        # Input dedicato per la depth, decouplato dal sync di gathered_data:
        # NeuralDepth e palm-detection hanno latenze diverse e sincronizzarli
        # via link_args(gathered, depth) accumula mismatch nelle code interne
        # → dopo decine di migliaia di frame la pipeline si blocca. Tenendo
        # qui una coda non-bloccante di dimensione 1 il producer sovrascrive
        # sempre l'ultimo frame e niente si accumula.
        self.depth_input = self.createInput()
        self.depth_input.setBlocking(False)
        self.depth_input.setMaxSize(1)
        self._latest_depth: Optional[dai.ImgFrame] = None

        self._padding = 0.1
        self._confidence_threshold = 0.7
        self._tracker = GestureTracker()

        # Range distanza palm dalla camera (mm). Detections fuori range
        # vengono scartate; di default il filtro è attivo solo se main.py
        # passa una depth_frame valida (per il path replay/single-cam resta off).
        self._depth_min_mm = 0
        self._depth_max_mm = 0
        self._depth_filter_enabled = False

        # Mano dominante (configurata dalla web app via WebSocket).
        # Le detection con label diversa vengono scartate prima di entrare
        # nella state-machine.
        self._dominant_hand = _DEFAULT_HANDEDNESS
        self._dominant_lock = threading.Lock()

        # Ultimo stato distanza inviato alla web app (too_far/too_close/ok).
        # Emettiamo solo on-change per non spammare la WS ad ogni frame.
        self._last_depth_status: Optional[str] = None
        # Storia mobile dei sample di depth della mano dominante: la mediana
        # su questa finestra è il valore "vero" usato per la classificazione,
        # robusta ai picchi della stereo neural-depth (che altrimenti fanno
        # alternare too_close/too_far frame per frame).
        self._depth_history: deque = deque(maxlen=_DEPTH_HISTORY_LEN)
        # Stato candidato in attesa di conferma (debounce N frame consecutivi).
        self._pending_depth_status: Optional[str] = None
        self._pending_depth_count: int = 0

        # Avvia il server WebSocket (idempotente).
        # NB: porta 8766 — la 8765 è già usata internamente da DepthAI v3.
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
        # Solo gathered_data drive il process(): la depth è "best-effort",
        # letta opportunisticamente dall'input non-bloccante.
        self.link_args(gathered_data)
        return self

    def process(self, gathered_data: dai.Buffer) -> None:
        assert isinstance(gathered_data, GatheredData)

        detections_msg: dai.ImgDetections = gathered_data.reference_data
        detections = detections_msg.detections

        with self._dominant_lock:
            dominant = self._dominant_hand

        # Depth pull non-bloccante: prendi l'ultimo frame se è arrivato dal
        # giro precedente, altrimenti riusa il cached. Un piccolo lag (1–3
        # frame) non è osservabile per il filtro distanza.
        depth_array = None
        if self._depth_filter_enabled:
            new_depth = self.depth_input.tryGet()
            if new_depth is not None:
                self._latest_depth = new_depth
            depth_array = self._depth_to_array(self._latest_depth)

        # Filtro mano dominante: scarta la mano non-selezionata in settings.
        # Loop completo (no break al primo confidence ok) così la dominante
        # viene processata anche se non è la prima detection.
        processed_any = False
        # Stato distanza per *questo* frame: la prima dominante con depth
        # valida lo determina. "ok" vince su too_far/too_close (se almeno
        # una mano è nel range non vogliamo allarmare). Quando nessuna mano
        # dominante è in frame (current_depth_status resta None) lo
        # interpretiamo come "ok" → l'alert si nasconde.
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

            # Filtro distanza: campiono la depth attorno al centro del palmo
            # e tengo solo le mani nel range chirurgico [DEPTH_MIN, DEPTH_MAX].
            # Detection senza depth valida (fuori frame, finestra tutta-zero)
            # → scartata: meglio perdere un frame che reagire a una mano
            # spuria di un'altra persona dietro/davanti al medico.
            depth_mm: Optional[int] = None
            if self._depth_filter_enabled:
                depth_mm = self._sample_depth_mm(
                    depth_array, bbox.center.x, bbox.center.y
                )
                if depth_mm is None:
                    continue
                if depth_mm < self._depth_min_mm:
                    # Registra l'allarme solo se non abbiamo già visto una
                    # mano nel range (current_depth_status != "ok").
                    if current_depth_status != "ok":
                        current_depth_status = "too_close"
                        current_depth_mm = depth_mm
                    continue
                if depth_mm > self._depth_max_mm:
                    if current_depth_status != "ok":
                        current_depth_status = "too_far"
                        current_depth_mm = depth_mm
                    continue
                # In range → "ok" sovrascrive eventuale alert precedente.
                current_depth_status = "ok"
                current_depth_mm = depth_mm

            # Remap dei 21 landmark dalle coords del crop a quelle del frame intero
            # (stesso calcolo di AnnotationNode.process)
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
            processed_any = True
            if tracker_event is None:
                break

            event = _to_ws_event(tracker_event)
            depth_str = f"{depth_mm}mm" if depth_mm is not None else "—"
            print(f"[gesture] {event['gesture']:<12}  "
                  f"hand={hand_label}  depth={depth_str}  "
                  f"clients={websocket_server.client_count()}")
            websocket_server.send_event(event)

            break  # solo la prima mano dominante per frame

        # Nessuna mano dominante in questo frame: il tracker deve "rilasciare"
        # (altrimenti una sessione PINCH/DRAG resta sospesa quando la mano
        # dominante esce dal frame ma quella non-dominante è ancora visibile).
        if not processed_any:
            self._tracker.reset()

        # Notifica alla web app il cambio di stato distanza, con smoothing
        # temporale (mediana sugli ultimi N campioni) + debounce (N frame
        # consecutivi nel nuovo stato) per evitare flicker.
        if self._depth_filter_enabled:
            self._update_depth_status(current_depth_mm)

    # ── Depth status: smoothing + debounce + WS notify ──────────────────────

    def _update_depth_status(self, sampled_depth_mm: Optional[int]) -> None:
        """Aggiorna la storia depth, calcola lo stato smoothed (mediana) e
        notifica la web app solo se il nuovo stato è stabile per
        _DEPTH_STATUS_DEBOUNCE frame consecutivi."""
        if sampled_depth_mm is not None:
            self._depth_history.append(int(sampled_depth_mm))
        elif self._depth_history:
            # Decadimento graduale: invece di azzerare la storia quando la
            # mano sparisce per un frame (movimento veloce, palm detect miss),
            # rimuoviamo un solo campione vecchio. Così piccoli buchi non
            # resettano la stima.
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

        # Già nello stato corrente: niente da fare, azzera il pending.
        if candidate == self._last_depth_status:
            self._pending_depth_status = None
            self._pending_depth_count = 0
            return

        # Conta i frame consecutivi nel nuovo candidato.
        if candidate == self._pending_depth_status:
            self._pending_depth_count += 1
        else:
            self._pending_depth_status = candidate
            self._pending_depth_count = 1

        if self._pending_depth_count < _DEPTH_STATUS_DEBOUNCE:
            return

        # Commit: il nuovo stato è stabile.
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
        """Estrae il frame depth come array (H, W) di uint16 millimetri.
        Restituisce None se non c'è depth (path single-cam/replay) o se il
        frame è degenere."""
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
        depth_array: Optional[np.ndarray], cx_norm: float, cy_norm: float,
    ) -> Optional[int]:
        """Campiona la depth attorno al centro del bbox e ritorna la mediana
        in mm dei pixel non-zero (0 = "no measurement"). None se la finestra
        non contiene misurazioni valide."""
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
        roi = depth_array[y0:y1, x0:x1]
        valid = roi[roi > 0]
        if valid.size == 0:
            return None
        return int(np.median(valid))

    # ── Controllo handedness via WebSocket ──────────────────────────────────

    def _on_ws_message(self, msg: dict) -> None:
        """Riceve i messaggi di controllo dalla web app. L'unico supportato
        oggi è `set_handedness`."""
        if msg.get("type") != "set_handedness":
            return
        hand = str(msg.get("hand", "")).lower()
        if hand not in _VALID_HANDEDNESS:
            print(f"[gesture] handedness ignorata: {hand!r}")
            return
        with self._dominant_lock:
            if self._dominant_hand == hand:
                return
            self._dominant_hand = hand
        # Cambio della mano dominante = sessione fresca, niente eventi appesi.
        self._tracker.reset()
        print(f"[gesture] mano dominante = {hand}")


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
