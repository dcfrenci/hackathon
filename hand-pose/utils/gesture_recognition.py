import time
from collections import deque
from typing import List, Optional, Tuple

import numpy as np


def distance(a, b):
    return np.linalg.norm(a - b)


def angle(a, b, c):
    ba = a - b
    bc = c - b
    cosine_angle = np.dot(ba, bc) / (np.linalg.norm(ba) * np.linalg.norm(bc))
    angle = np.arccos(cosine_angle)

    return np.degrees(angle)


def recognize_gesture(kpts: List[Tuple[float, float]]) -> str:
    kpts = np.array(kpts)
    d_3_5 = distance(kpts[3], kpts[5])
    d_2_3 = distance(kpts[2], kpts[3])
    angle0 = angle(kpts[0], kpts[1], kpts[2])
    angle1 = angle(kpts[1], kpts[2], kpts[3])
    angle2 = angle(kpts[2], kpts[3], kpts[4])
    thumb_state = 0
    index_state = 0
    middle_state = 0
    ring_state = 0
    little_state = 0
    gesture = None
    if angle0 + angle1 + angle2 > 460 and d_3_5 / d_2_3 > 1.2:
        thumb_state = 1
    else:
        thumb_state = 0

    if kpts[8][1] < kpts[7][1] < kpts[6][1]:
        index_state = 1
    elif kpts[6][1] < kpts[8][1]:
        index_state = 0
    else:
        index_state = -1

    if kpts[12][1] < kpts[11][1] < kpts[10][1]:
        middle_state = 1
    elif kpts[10][1] < kpts[12][1]:
        middle_state = 0
    else:
        middle_state = -1

    if kpts[16][1] < kpts[15][1] < kpts[14][1]:
        ring_state = 1
    elif kpts[14][1] < kpts[16][1]:
        ring_state = 0
    else:
        ring_state = -1

    if kpts[20][1] < kpts[19][1] < kpts[18][1]:
        little_state = 1
    elif kpts[18][1] < kpts[20][1]:
        little_state = 0
    else:
        little_state = -1

    # PINCH: pollice e indice si toccano, le altre 3 dita chiuse.
    # Va controllato PRIMA delle gesture basate sugli state, perché durante
    # un pinch il pollice è piegato e l'indice curvato: i loro state sono
    # ambigui e non corrispondono a nessun pattern fisso.
    hand_size = distance(kpts[0], kpts[9])  # wrist → middle MCP, riferimento scala
    pinch_dist = distance(kpts[4], kpts[8]) / hand_size
    PINCH_THRESHOLD = 0.35
    if (
        pinch_dist < PINCH_THRESHOLD
        and middle_state == 0
        and ring_state == 0
        and little_state == 0
    ):
        return "PINCH"

    # Gesture
    if (
        thumb_state == 1
        and index_state == 1
        and middle_state == 1
        and ring_state == 1
        and little_state == 1
    ):
        gesture = "FIVE"
    else:
        gesture = None

    # Gesture disabilitate (non servono per il flusso medico):
    # PEACE / ONE / TWO / OK / THREE / FOUR / FIST.
    # Le condizioni originali sono conservate sotto come riferimento.
    '''
    elif (
        thumb_state == 0 and index_state == 1 and middle_state == 1
        and ring_state == 0 and little_state == 0
    ):
        gesture = "PEACE"
    elif (
        thumb_state == 0 and index_state == 1 and middle_state == 0
        and ring_state == 0 and little_state == 0
    ):
        gesture = "ONE"
    elif (
        thumb_state == 1 and index_state == 1 and middle_state == 0
        and ring_state == 0 and little_state == 0
    ):
        gesture = "TWO"
    '''

    return gesture


class GestureTracker:
    """Tracker stateful che combina la gesture statica corrente (output di
    `recognize_gesture`) con il movimento della mano per rilevare gli eventi
    dinamici del flusso medico:

      • CLICK         -> pinch breve senza spostamento significativo
      • SWIPE_LEFT    -> 5 dita aperte + spostamento orizzontale verso sx
      • SWIPE_RIGHT   -> 5 dita aperte + spostamento orizzontale verso dx
      • PINCH_DRAG    -> pinch trattenuto + spostamento, restituisce dx/dy
                        normalizzati sulla dimensione della mano
                        (per ruotare il modello 3D)

    Da chiamare a ogni frame:
        event = tracker.update(gesture, kpts)
    Restituisce un dict {"type": ...} oppure None.

    Convenzione segno per PINCH_DRAG (in coordinate immagine):
      dx > 0 = mano si sposta verso destra dello schermo
      dy > 0 = mano si sposta verso il basso
    Se la camera è speculare o i due assi vanno invertiti per la UI 3D,
    farlo nel layer bridge (non qui).
    """

    def __init__(
        self,
        swipe_threshold: float = 0.8,
        click_max_movement: float = 0.3,
        click_max_duration: float = 0.5,
        drag_deadzone: float = 0.005,
        release_confirmation_frames: int = 2,
        gesture_cooldown_seconds: float = 0.5,
    ):
        # `swipe_threshold`: spostamento orizzontale (in unità "hand_size")
        #   che fa scattare uno SWIPE. 0.8 ≈ una mano di ampiezza.
        # `click_max_movement`: spostamento totale massimo durante un pinch
        #   per essere considerato un CLICK invece di un drag.
        # `click_max_duration`: durata massima del pinch per essere CLICK.
        # `drag_deadzone`: frame-to-frame, sotto questa soglia il movimento
        #   è considerato jitter dei landmark e non emette PINCH_DRAG.
        # `release_confirmation_frames`: frame consecutivi di non-PINCH
        #   richiesti per confermare il rilascio. Evita falsi CLICK quando il
        #   classificatore "buca" un frame durante un drag. Tipicamente 2-3.
        # `gesture_cooldown_seconds`: dopo CLICK o SWIPE, ignora qualsiasi
        #   gesture per questo tempo. Evita che la mano che si chiude
        #   subito dopo uno swipe venga interpretata come pinch. Non si
        #   applica ai PINCH_DRAG (che sono per natura continui).
        self._swipe_threshold = swipe_threshold
        self._click_max_movement = click_max_movement
        self._click_max_duration = click_max_duration
        self._drag_deadzone = drag_deadzone
        self._release_confirmation_frames = release_confirmation_frames
        self._gesture_cooldown = gesture_cooldown_seconds

        self._prev_gesture: Optional[str] = None
        self._in_pinch = False
        self._non_pinch_count = 0
        self._pinch_start_time = 0.0
        self._pinch_total_movement = 0.0
        self._prev_pinch_pos: Optional[np.ndarray] = None
        self._five_start_pos: Optional[np.ndarray] = None
        self._cooldown_until = 0.0

    def reset(self) -> None:
        self._prev_gesture = None
        self._in_pinch = False
        self._non_pinch_count = 0
        self._prev_pinch_pos = None
        self._five_start_pos = None
        self._pinch_total_movement = 0.0
        self._cooldown_until = 0.0

    def update(self, gesture: Optional[str], kpts) -> Optional[dict]:
        kpts = np.array(kpts)
        # MCP del medio: punto stabile al centro del palmo.
        pos = kpts[9].astype(float).copy()
        hand_size = float(distance(kpts[0], kpts[9]))
        if hand_size < 1e-6:
            self._prev_gesture = gesture
            return None

        now = time.monotonic()

        # Cooldown post-evento: scarta tutto e tieni le state machine "fredde"
        # finché non scade. Reset _in_pinch e _five_start_pos così la prossima
        # gesture utile parte da capo, non a metà di una sequenza precedente.
        if now < self._cooldown_until:
            self._in_pinch = False
            self._non_pinch_count = 0
            self._prev_pinch_pos = None
            self._five_start_pos = None
            self._prev_gesture = gesture
            return None

        event: Optional[dict] = None

        # ---- PINCH: drag durante, click su rilascio (con isteresi) ----
        if gesture == "PINCH":
            # Frame valido di pinch: azzera il contatore di non-pinch.
            self._non_pinch_count = 0
            if not self._in_pinch:
                # inizio pinch
                self._in_pinch = True
                self._pinch_start_time = time.monotonic()
                self._pinch_total_movement = 0.0
                self._prev_pinch_pos = pos
            else:
                # pinch trattenuto: emette PINCH_DRAG con dx/dy del frame.
                # Funziona anche dopo una "buca": _prev_pinch_pos viene
                # mantenuto durante i frame in attesa di conferma.
                if self._prev_pinch_pos is not None:
                    dx = float((pos[0] - self._prev_pinch_pos[0]) / hand_size)
                    dy = float((pos[1] - self._prev_pinch_pos[1]) / hand_size)
                    mag = (dx * dx + dy * dy) ** 0.5
                    self._pinch_total_movement += mag
                    if mag > self._drag_deadzone:
                        event = {"type": "PINCH_DRAG", "dx": dx, "dy": dy}
                self._prev_pinch_pos = pos
        elif self._in_pinch:
            # gesture != PINCH ma siamo in stato pinch: aspetta conferma.
            self._non_pinch_count += 1
            if self._non_pinch_count >= self._release_confirmation_frames:
                # rilascio confermato: valuta se è stato un CLICK.
                duration = time.monotonic() - self._pinch_start_time
                if (
                    duration < self._click_max_duration
                    and self._pinch_total_movement < self._click_max_movement
                ):
                    event = {"type": "CLICK"}
                self._in_pinch = False
                self._non_pinch_count = 0
                self._prev_pinch_pos = None
            # else: pending release, nessun evento. _prev_pinch_pos resta
            # invariato così se il pinch riprende il drag continua liscio.

        # ---- FIVE: swipe orizzontale ----
        if gesture == "FIVE" and self._prev_gesture != "FIVE":
            self._five_start_pos = pos
        elif gesture == "FIVE" and self._prev_gesture == "FIVE":
            if self._five_start_pos is not None:
                dx_norm = float((pos[0] - self._five_start_pos[0]) / hand_size)
                if abs(dx_norm) > self._swipe_threshold:
                    event = {"type": "SWIPE_RIGHT" if dx_norm > 0 else "SWIPE_LEFT"}
                    # reset del punto di partenza per consentire swipe ripetuti
                    self._five_start_pos = pos
        elif gesture != "FIVE" and self._prev_gesture == "FIVE":
            self._five_start_pos = None

        # Arma il cooldown dopo eventi "discreti". PINCH_DRAG è continuo e
        # va escluso, altrimenti ogni frame si autoblocca.
        if event is not None and event["type"] in ("CLICK", "SWIPE_LEFT", "SWIPE_RIGHT"):
            self._cooldown_until = now + self._gesture_cooldown

        self._prev_gesture = gesture
        return event






"""
Tarature da fare dal vivo:
  - swipe_threshold=0.8: se gli swipe scattano troppo facilmente alza a 1.0, se non scattano abbassa a 0.6
  - click_max_duration=0.5: se il click sembra "lento" (l'evento arriva solo al rilascio) considera di emetterlo subito su PINCH
  start (cambiamento minore: sposta il check di CLICK nel branch "inizio pinch")
  - drag_deadzone=0.005: se il modello 3D vibra a mano ferma alza a 0.01
  - Segno di dx/dy: dipende da come la pipeline DepthAI restituisce le coordinate (mirrored o no). Se il modello ruota al
  contrario, basta negare nel bridge layer.

  Punto di attenzione: durante un PINCH_DRAG lungo, se i landmark momentaneamente sbagliano e classificano come non-PINCH per un
  frame, scatta un falso CLICK. Se vedi questo problema, aggiungi una piccola isteresi (es. richiedi 2 frame consecutivi di
  non-PINCH per confermare il rilascio). Te la aggiungo se serve

    Tarature:
  - release_confirmation_frames=2: a 30fps = ~66ms di latenza al rilascio. Se il classificatore è molto rumoroso aumenta a 3
  (100ms). Più alto = il CLICK arriva più lento.
  - A 60fps puoi salire a 3-4 frame senza che si senta.
"""
