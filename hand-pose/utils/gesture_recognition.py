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


def _finger_curled(kpts, tip_idx: int, pip_idx: int, tol: float = 1.05) -> bool:
    # Rotation-invariant: dito ripiegato se la punta è più vicina al polso del PIP.
    # Sostituisce il check kpts[X].y < kpts[Y].y, che fallisce quando la mano
    # è ruotata (dita non più orientate verso l'alto/basso).
    return distance(kpts[tip_idx], kpts[0]) < distance(kpts[pip_idx], kpts[0]) * tol


def _finger_extended(kpts, tip_idx: int, pip_idx: int, tol: float = 1.10) -> bool:
    # Rotation-invariant: dito esteso se la punta è significativamente più
    # lontana dal polso del PIP. Tolleranza > 1 lascia una "zona morta" fra
    # esteso e ripiegato → posa intermedia non viene classificata né come
    # PINCH né come DRAG, evitando flicker fra i due label.
    return distance(kpts[tip_idx], kpts[0]) > distance(kpts[pip_idx], kpts[0]) * tol


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

    # PINCH vs DRAG: distinguiamo per posa delle altre 3 dita quando pollice e
    # indice si toccano. Permette di separare fisicamente "click" (3 dita
    # estese) da "drag" (3 dita ripiegate) senza dover indovinare l'intento
    # dal movimento. Va controllato PRIMA delle gesture basate sugli state.
    hand_size = distance(kpts[0], kpts[9])  # wrist → middle MCP, riferimento scala
    pinch_dist = distance(kpts[4], kpts[8]) / hand_size
    PINCH_THRESHOLD = 0.40
    if pinch_dist < PINCH_THRESHOLD:
        middle_curled = _finger_curled(kpts, 12, 10)
        ring_curled = _finger_curled(kpts, 16, 14)
        little_curled = _finger_curled(kpts, 20, 18)
        middle_extended = _finger_extended(kpts, 12, 10)
        ring_extended = _finger_extended(kpts, 16, 14)
        little_extended = _finger_extended(kpts, 20, 18)
        if middle_curled and ring_curled and little_curled:
            return "DRAG"
        if middle_extended and ring_extended and little_extended:
            return "PINCH"
        # Posa intermedia (almeno un dito né esteso né ripiegato): nessun
        # label, evita di flickare fra PINCH e DRAG durante la transizione.

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

      • CLICK         -> posa "PINCH" (pollice+indice toccati, 3 dita estese)
                        rilasciata in tempi brevi → emesso al rilascio
      • DRAG_LEFT/RIGHT/UP/DOWN
                     -> posa "DRAG" (pollice+indice toccati, 3 dita ripiegate)
                        + spostamento che supera drag_threshold sull'asse
                        dominante. Eventi discreti per controllare yaw (asse
                        orizzontale) e roll (asse verticale) del modello 3D.
                        Si possono concatenare senza rilasciare il drag:
                        ogni evento resetta l'anchor.
      • SWIPE_LEFT    -> 5 dita aperte + spostamento orizzontale verso sx
      • SWIPE_RIGHT   -> 5 dita aperte + spostamento orizzontale verso dx

    Le pose PINCH e DRAG sono fisicamente distinte: l'utente sceglie con la
    posa delle 3 dita se vuole cliccare o trascinare. Una transizione
    PINCH → DRAG NON emette click; DRAG → PINCH non emette nulla.

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
        drag_threshold: float = 0.35,
        click_max_movement: float = 0.3,
        click_min_duration: float = 0.08,
        click_max_duration: float = 0.5,
        release_confirmation_frames: int = 2,
        gesture_cooldown_seconds: float = 0.5,
        smoothing_alpha: float = 0.45,
    ):
        # `swipe_threshold`: spostamento orizzontale (in unità "hand_size")
        #   che fa scattare uno SWIPE. 0.8 ≈ una mano di ampiezza.
        # `drag_threshold`: spostamento (in hand_size) dall'anchor su asse
        #   dominante per emettere un evento DRAG_*. Più piccolo di swipe per
        #   permettere step più fini quando si controlla yaw/roll. Dopo
        #   l'evento l'anchor viene resettato → eventi concatenabili.
        # `click_max_movement`: spostamento totale massimo durante un pinch
        #   per essere considerato un CLICK (filtro residuo, ora che la posa
        #   distingue intent).
        # `click_min_duration`: durata minima del pinch per essere CLICK.
        #   Filtra transizioni rapide del tipo DRAG → PINCH → rilascio, in cui
        #   l'utente apre fugacemente le 3 dita mentre rilascia un drag.
        # `click_max_duration`: durata massima del pinch per essere CLICK.
        # `release_confirmation_frames`: frame consecutivi di non-PINCH
        #   richiesti per confermare il rilascio. Evita falsi CLICK quando il
        #   classificatore "buca" un frame durante un drag. Tipicamente 2-3.
        # `gesture_cooldown_seconds`: dopo CLICK o SWIPE, ignora qualsiasi
        #   gesture per questo tempo. Evita che la mano che si chiude
        #   subito dopo uno swipe venga interpretata come pinch. Non si
        #   applica ai PINCH_DRAG (che sono per natura continui).
        # `smoothing_alpha`: EMA sulla posizione del MCP medio. 1.0 = nessuno
        #   smoothing (rumoroso → drag spurio a mano ferma); valori bassi
        #   filtrano il jitter dei landmark ma introducono lag. 0.45 è un
        #   buon compromesso a 30fps.
        self._swipe_threshold = swipe_threshold
        self._drag_threshold = drag_threshold
        self._click_max_movement = click_max_movement
        self._click_min_duration = click_min_duration
        self._click_max_duration = click_max_duration
        self._release_confirmation_frames = release_confirmation_frames
        self._gesture_cooldown = gesture_cooldown_seconds
        self._smoothing_alpha = smoothing_alpha

        self._prev_gesture: Optional[str] = None
        self._in_pinch = False
        self._non_pinch_count = 0
        self._pinch_start_time = 0.0
        self._pinch_total_movement = 0.0
        self._prev_pinch_pos: Optional[np.ndarray] = None
        self._in_drag = False
        self._drag_anchor: Optional[np.ndarray] = None
        self._five_start_pos: Optional[np.ndarray] = None
        self._cooldown_until = 0.0
        self._smooth_pos: Optional[np.ndarray] = None

    def reset(self) -> None:
        self._prev_gesture = None
        self._in_pinch = False
        self._non_pinch_count = 0
        self._prev_pinch_pos = None
        self._in_drag = False
        self._drag_anchor = None
        self._five_start_pos = None
        self._pinch_total_movement = 0.0
        self._cooldown_until = 0.0
        self._smooth_pos = None

    def update(self, gesture: Optional[str], kpts) -> Optional[dict]:
        kpts = np.array(kpts)
        # MCP del medio: punto stabile al centro del palmo.
        raw_pos = kpts[9].astype(float)
        hand_size = float(distance(kpts[0], kpts[9]))
        if hand_size < 1e-6:
            self._prev_gesture = gesture
            return None

        # EMA per filtrare il jitter dei landmark MediaPipe. Senza questo,
        # ogni frame con mano "ferma" produce un dx/dy non-zero che supera
        # facilmente la deadzone, generando PINCH_DRAG spurio in continuo.
        if self._smooth_pos is None:
            self._smooth_pos = raw_pos.copy()
        else:
            a = self._smoothing_alpha
            self._smooth_pos = a * raw_pos + (1.0 - a) * self._smooth_pos
        pos = self._smooth_pos.copy()

        now = time.monotonic()

        # Cooldown post-evento: scarta tutto e tieni le state machine "fredde"
        # finché non scade. Reset _in_pinch e _five_start_pos così la prossima
        # gesture utile parte da capo, non a metà di una sequenza precedente.
        if now < self._cooldown_until:
            self._in_pinch = False
            self._non_pinch_count = 0
            self._prev_pinch_pos = None
            self._in_drag = False
            self._drag_anchor = None
            self._five_start_pos = None
            # Forza re-arm pulito allo scadere del cooldown: se restassimo con
            # _prev_gesture == "FIVE", il prossimo frame entrerebbe nel branch
            # "FIVE già attivo" con _five_start_pos=None e non scatterebbe mai
            # un nuovo swipe finché l'utente non abbassa la mano.
            self._prev_gesture = None
            return None

        event: Optional[dict] = None

        # ---- PINCH (3 dita estese): posa "click". Emette CLICK su rilascio. ----
        if gesture == "PINCH":
            self._non_pinch_count = 0
            if not self._in_pinch:
                self._in_pinch = True
                self._pinch_start_time = now
                self._pinch_total_movement = 0.0
                self._prev_pinch_pos = pos
            else:
                # Accumula movimento per filtro click vs trascinamento accidentale
                if self._prev_pinch_pos is not None:
                    dx = float((pos[0] - self._prev_pinch_pos[0]) / hand_size)
                    dy = float((pos[1] - self._prev_pinch_pos[1]) / hand_size)
                    self._pinch_total_movement += (dx * dx + dy * dy) ** 0.5
                self._prev_pinch_pos = pos
            # Eventuale transizione DRAG → PINCH: l'utente ha aperto le 3 dita
            # mentre teneva pollice+indice. Cancelliamo lo stato di drag senza
            # emettere nulla.
            if self._in_drag:
                self._in_drag = False
                self._drag_anchor = None

        # ---- DRAG (3 dita ripiegate): eventi discreti DRAG_* per soglia. ----
        elif gesture == "DRAG":
            # Transizione PINCH → DRAG: l'utente ha piegato le 3 dita per
            # iniziare un drag. NON emettere CLICK: era un'intenzione di drag,
            # non un click breve interrotto.
            if self._in_pinch:
                self._in_pinch = False
                self._non_pinch_count = 0
                self._prev_pinch_pos = None

            if not self._in_drag:
                self._in_drag = True
                self._drag_anchor = pos
            elif self._drag_anchor is not None:
                # Spostamento dall'anchor (non frame-to-frame): a mano ferma
                # resta vicino a 0, non accumula jitter. Emette un evento solo
                # quando supera la soglia sull'asse dominante; poi resetta
                # l'anchor per permettere step concatenati.
                dx = float((pos[0] - self._drag_anchor[0]) / hand_size)
                dy = float((pos[1] - self._drag_anchor[1]) / hand_size)
                if abs(dx) >= abs(dy):
                    if abs(dx) > self._drag_threshold:
                        event = {"type": "DRAG_RIGHT" if dx > 0 else "DRAG_LEFT"}
                        self._drag_anchor = pos
                else:
                    if abs(dy) > self._drag_threshold:
                        # dy > 0 = mano scende in coord immagine
                        event = {"type": "DRAG_DOWN" if dy > 0 else "DRAG_UP"}
                        self._drag_anchor = pos

        # ---- Altri label (FIVE, None, posa intermedia): possibile rilascio ----
        else:
            # Fine drag: nessun evento, basta resettare lo stato.
            if self._in_drag:
                self._in_drag = False
                self._drag_anchor = None

            # Possibile rilascio del pinch → CLICK con isteresi.
            if self._in_pinch:
                self._non_pinch_count += 1
                if self._non_pinch_count >= self._release_confirmation_frames:
                    duration = now - self._pinch_start_time
                    if (
                        self._click_min_duration <= duration < self._click_max_duration
                        and self._pinch_total_movement < self._click_max_movement
                    ):
                        event = {"type": "CLICK"}
                    self._in_pinch = False
                    self._non_pinch_count = 0
                    self._prev_pinch_pos = None
                # else: in attesa di conferma rilascio, nessun evento.

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
