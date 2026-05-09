"""
gesture_recognition.py — Riconoscimento di pose statiche e gesti dinamici della
mano per il controllo touchless di un'interfaccia medica.

Architettura a due livelli:

    recognize_gesture(kpts)       → HandPose | None    (un singolo frame)
    GestureTracker.update(label, kpts) → Event | None  (state-machine temporale)

Le pose statiche (PINCH, DRAG, FIVE, PEACE_H) sono fisicamente distinte:
l'utente sceglie l'azione con la posa delle dita, non con il movimento. La
state-machine combina la posa "committed" (dopo filtraggio di flicker) con il
movimento del palmo per emettere eventi discreti consumati dal bridge node.

Eventi emessi:
    CLICK         — rilascio di PINCH dopo durata/movimento accettabili
    DRAG_*        — DRAG con spostamento sopra soglia (asse dominante)
    SWIPE_*       — FIVE con movimento orizzontale sopra soglia
    BACK          — ingresso nella posa PEACE_H (one-shot)

Convenzione segno: dx > 0 = destra, dy > 0 = basso (coordinate immagine).
Inversioni vanno fatte nel layer bridge / web app, non qui.

Le note di taratura sono in coda al modulo.
"""

import time
from enum import Enum
from typing import Dict, List, Optional, Tuple

import numpy as np


# =============================================================================
# Tipi pubblici: pose statiche ed eventi della state-machine
# =============================================================================


class HandPose(str, Enum):
    """Posa statica riconosciuta in un singolo frame."""
    PINCH = "PINCH"      # pollice+indice toccati, 3 dita estese  → click
    DRAG = "DRAG"        # pollice+indice toccati, 3 dita ripiegate → drag
    FIVE = "FIVE"        # 5 dita estese → swipe
    PEACE_H = "PEACE_H"  # pollice+indice estesi, altre 3 ripiegate → back


class EventType(str, Enum):
    """Eventi emessi dalla state-machine temporale."""
    CLICK = "CLICK"
    DRAG_LEFT = "DRAG_LEFT"
    DRAG_RIGHT = "DRAG_RIGHT"
    DRAG_UP = "DRAG_UP"
    DRAG_DOWN = "DRAG_DOWN"
    SWIPE_LEFT = "SWIPE_LEFT"
    SWIPE_RIGHT = "SWIPE_RIGHT"
    BACK = "BACK"


# Inversi per il direction lock: dopo un evento direzionale, il suo opposto
# è bloccato per un breve intervallo per evitare che il movimento di ritorno
# della mano triggeri il gesto opposto.
_OPPOSITE_EVENT: Dict[str, str] = {
    EventType.DRAG_LEFT.value:   EventType.DRAG_RIGHT.value,
    EventType.DRAG_RIGHT.value:  EventType.DRAG_LEFT.value,
    EventType.DRAG_UP.value:     EventType.DRAG_DOWN.value,
    EventType.DRAG_DOWN.value:   EventType.DRAG_UP.value,
    EventType.SWIPE_LEFT.value:  EventType.SWIPE_RIGHT.value,
    EventType.SWIPE_RIGHT.value: EventType.SWIPE_LEFT.value,
}

# Eventi one-shot (non continui) soggetti a cooldown post-emissione.
_ONE_SHOT_EVENTS = frozenset({EventType.CLICK.value, EventType.BACK.value})


# =============================================================================
# Indici dei landmark MediaPipe Hand (0-20)
# =============================================================================

WRIST = 0
THUMB_CMC, THUMB_MCP, THUMB_IP, THUMB_TIP = 1, 2, 3, 4
INDEX_MCP, INDEX_PIP, INDEX_DIP, INDEX_TIP = 5, 6, 7, 8
MIDDLE_MCP, MIDDLE_PIP, MIDDLE_DIP, MIDDLE_TIP = 9, 10, 11, 12
RING_MCP, RING_PIP, RING_DIP, RING_TIP = 13, 14, 15, 16
LITTLE_MCP, LITTLE_PIP, LITTLE_DIP, LITTLE_TIP = 17, 18, 19, 20

# Triplette (tip, dip, pip) delle 4 dita lunghe.
_LONG_FINGERS_TDP: Tuple[Tuple[int, int, int], ...] = (
    (INDEX_TIP, INDEX_DIP, INDEX_PIP),
    (MIDDLE_TIP, MIDDLE_DIP, MIDDLE_PIP),
    (RING_TIP, RING_DIP, RING_PIP),
    (LITTLE_TIP, LITTLE_DIP, LITTLE_PIP),
)

# Coppie (tip, pip) delle dita non-indice (medio, anulare, mignolo): usate
# nei check PINCH/DRAG/PEACE_H per le "altre 3 dita".
_OTHER_FINGERS_TP: Tuple[Tuple[int, int], ...] = (
    (MIDDLE_TIP, MIDDLE_PIP),
    (RING_TIP, RING_PIP),
    (LITTLE_TIP, LITTLE_PIP),
)


# =============================================================================
# Soglie di classificazione (tarate empiricamente — vedi note in coda)
# =============================================================================

# PINCH/DRAG: distanza pollice-indice / hand_size sotto questa soglia indica
# le 2 dita "toccate".
_PINCH_THRESHOLD = 0.40

# Tolleranze rotation-invariant per dita ripiegate / estese. > 1 crea una
# zona morta che evita flicker ai bordi.
_FINGER_CURLED_TOL = 1.05
_FINGER_EXTENDED_TOL = 1.10

# Pollice esteso: somma dei 3 angoli alta + rapporto delle distanze alto.
_THUMB_ANGLE_SUM_THRESHOLD = 460.0
_THUMB_DIST_RATIO_THRESHOLD = 1.2

# PEACE_H ("L shape"): angolo tra direzione pollice e direzione indice deve
# essere vicino a 90°, e le due punte devono essere ben separate.
_PEACE_H_ANGLE_MIN = 60.0   # gradi
_PEACE_H_ANGLE_MAX = 120.0  # gradi
_PEACE_H_MIN_SPREAD = 0.50  # distanza pollice-indice / hand_size (> soglia pinch)


# =============================================================================
# Helper geometrici
# =============================================================================


def _distance(a: np.ndarray, b: np.ndarray) -> float:
    return float(np.linalg.norm(a - b))


def _angle_deg(a: np.ndarray, b: np.ndarray, c: np.ndarray) -> float:
    """Angolo (in gradi) ∠abc, con b come vertice."""
    ba = a - b
    bc = c - b
    cosine = np.dot(ba, bc) / (np.linalg.norm(ba) * np.linalg.norm(bc))
    return float(np.degrees(np.arccos(cosine)))


def _hand_size(kpts: np.ndarray) -> float:
    """Distanza polso → MCP del medio. Riferimento di scala invariante alla
    distanza dalla camera, usato per normalizzare tutte le altre distanze."""
    return _distance(kpts[WRIST], kpts[MIDDLE_MCP])


def _finger_curled(
    kpts: np.ndarray, tip: int, pip: int, tol: float = _FINGER_CURLED_TOL,
) -> bool:
    """Dito ripiegato se la punta è più vicina al polso del PIP.
    Rotation-invariant: sostituisce il check kpts[tip].y < kpts[pip].y, che
    fallisce quando la mano è ruotata (dita non più orientate verso l'alto)."""
    return _distance(kpts[tip], kpts[WRIST]) < _distance(kpts[pip], kpts[WRIST]) * tol


def _finger_extended(
    kpts: np.ndarray, tip: int, pip: int, tol: float = _FINGER_EXTENDED_TOL,
) -> bool:
    """Dito esteso se la punta è significativamente più lontana dal polso del
    PIP. tol > 1 crea una "zona morta" fra esteso e ripiegato → posa intermedia
    non viene classificata né come PINCH né come DRAG, evita flicker."""
    return _distance(kpts[tip], kpts[WRIST]) > _distance(kpts[pip], kpts[WRIST]) * tol


def _thumb_extended(kpts: np.ndarray) -> bool:
    """Pollice esteso: somma dei 3 angoli articolari alta E rapporto delle
    distanze (THUMB_IP→INDEX_MCP) / (THUMB_MCP→THUMB_IP) sopra soglia.
    Combinazione tarata sul check originale (legacy)."""
    angle_sum = (
        _angle_deg(kpts[WRIST], kpts[THUMB_CMC], kpts[THUMB_MCP])
        + _angle_deg(kpts[THUMB_CMC], kpts[THUMB_MCP], kpts[THUMB_IP])
        + _angle_deg(kpts[THUMB_MCP], kpts[THUMB_IP], kpts[THUMB_TIP])
    )
    if angle_sum <= _THUMB_ANGLE_SUM_THRESHOLD:
        return False
    distance_ratio = (
        _distance(kpts[THUMB_IP], kpts[INDEX_MCP])
        / _distance(kpts[THUMB_MCP], kpts[THUMB_IP])
    )
    return distance_ratio > _THUMB_DIST_RATIO_THRESHOLD


def _thumb_index_angle(kpts: np.ndarray) -> float:
    """Angolo (in gradi) tra la direzione del pollice (MCP→TIP) e quella
    dell'indice (MCP→TIP). Usato per richiedere ~90° nella posa PEACE_H."""
    thumb_dir = kpts[THUMB_TIP] - kpts[THUMB_MCP]
    index_dir = kpts[INDEX_TIP] - kpts[INDEX_MCP]
    norm = np.linalg.norm(thumb_dir) * np.linalg.norm(index_dir)
    if norm < 1e-6:
        return 0.0
    cosine = np.clip(np.dot(thumb_dir, index_dir) / norm, -1.0, 1.0)
    return float(np.degrees(np.arccos(cosine)))


def _finger_extended_y(kpts: np.ndarray, tip: int, dip: int, pip: int) -> bool:
    """Check di estensione y-based (legacy): tip più in alto del DIP, DIP più
    in alto del PIP. Strict: richiede mano orientata verso l'alto. Usato solo
    per FIVE, dove vogliamo essere severi per non confondere con altre pose
    a 3+ dita estese."""
    return kpts[tip][1] < kpts[dip][1] < kpts[pip][1]


# =============================================================================
# Riconoscimento pose statiche (singolo frame)
# =============================================================================


def _classify_pinch_or_drag(kpts: np.ndarray) -> Optional[HandPose]:
    """Distingue PINCH da DRAG quando pollice+indice si toccano: la differenza
    sta nelle altre 3 dita (estese vs ripiegate). Posa intermedia → None,
    evita flicker fra i due label durante la transizione."""
    pinch_dist = _distance(kpts[THUMB_TIP], kpts[INDEX_TIP]) / _hand_size(kpts)
    if pinch_dist >= _PINCH_THRESHOLD:
        return None

    if all(_finger_curled(kpts, tip, pip) for tip, pip in _OTHER_FINGERS_TP):
        return HandPose.DRAG
    if all(_finger_extended(kpts, tip, pip) for tip, pip in _OTHER_FINGERS_TP):
        return HandPose.PINCH
    return None


def _is_peace_h(kpts: np.ndarray) -> bool:
    """PEACE_H ("L shape"): pollice + indice estesi a ~90°, bene separati,
    altre 3 dita ripiegate. L'angolo e la distanza minima evitano confusione
    con DRAG (dita vicine) e con pose intermedie (angolo non a 90°)."""
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
    """FIVE: tutte le 5 dita estese. Usa il check y-based legacy (richiede
    mano orientata verso l'alto): più severo, riduce falsi positivi con altre
    pose a dita estese."""
    if not _thumb_extended(kpts):
        return False
    return all(
        _finger_extended_y(kpts, tip, dip, pip)
        for tip, dip, pip in _LONG_FINGERS_TDP
    )


def recognize_gesture(kpts: List[Tuple[float, float]]) -> Optional[str]:
    """Classifica la posa statica di un singolo frame.

    Ordine di precedenza (importante: pose ambigue si risolvono qui):
        1. PINCH / DRAG   (pollice+indice toccati, dominano se attive)
        2. PEACE_H        (posa "il 2")
        3. FIVE           (5 dita estese)
        4. None           (nessuna posa riconosciuta o intermedia)

    Restituisce il nome della posa come stringa per compatibilità con il bridge
    node, oppure None. Per i consumatori interni usare l'enum HandPose.
    """
    arr = np.asarray(kpts, dtype=float)

    pinch_or_drag = _classify_pinch_or_drag(arr)
    if pinch_or_drag is not None:
        return pinch_or_drag.value
    if _is_peace_h(arr):
        return HandPose.PEACE_H.value
    if _is_five(arr):
        return HandPose.FIVE.value
    return None


# =============================================================================
# Helper della state-machine: filtri di flicker e gate temporali
# =============================================================================


class _StickyPoseLock:
    """Filtra il flicker frame-by-frame del label raw di recognize_gesture.

    Il classificatore può oscillare fra label adiacenti per 1 frame ai bordi
    delle soglie (es. PINCH che diventa DRAG per un frame se un dito si rilassa).
    La posa "committed" cambia solo dopo K frame consecutivi dello stesso
    nuovo label, dove K dipende dalla transizione (vedi _TRANSITION_K)."""

    _DEFAULT_K = 2
    # Tarato per assorbire flicker da 1 frame senza aggiungere lag percepibile
    # (~67ms a 30fps per K=2).
    _TRANSITION_K: Dict[Tuple[Optional[str], Optional[str]], int] = {
        # Inizio sessione (mano appena entrata in frame): reattivo, K=1.
        (None, HandPose.PINCH.value):   1,
        (None, HandPose.DRAG.value):    1,
        (None, HandPose.FIVE.value):    1,
        (None, HandPose.PEACE_H.value): 1,
        # DRAG → FIVE: transizione fisica grossa (estendere tutte le 5 dita),
        # se vista in 1-2 frame è quasi sempre flicker.
        (HandPose.DRAG.value, HandPose.FIVE.value): 3,
        # PEACE_H ↔ FIVE: alziamo K per ridurre falsi positivi durante
        # transizioni FIVE↔altro che attraversano una "via di mezzo" simile
        # a PEACE_H.
        (HandPose.FIVE.value, HandPose.PEACE_H.value): 3,
        (HandPose.PEACE_H.value, HandPose.FIVE.value): 3,
        # DRAG/PINCH → PEACE_H: il rilascio di un pinch/drag può attraversare
        # una forma simile a PEACE_H (pollice+indice che si aprono). K=4
        # (~133ms a 30fps) evita BACK spurio immediatamente dopo click/drag.
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
        """Aggiorna la posa committed con il nuovo label raw.
        Ritorna True se la posa committed è cambiata in questo frame."""
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
    """EMA sulla posizione del palmo per filtrare il jitter dei landmark."""

    def __init__(self, alpha: float):
        self._alpha = alpha
        self._smoothed: Optional[np.ndarray] = None

    def reset(self) -> None:
        self._smoothed = None

    def update(self, raw_pos: np.ndarray, force_reset: bool = False) -> np.ndarray:
        """Restituisce la posizione filtrata. force_reset=True azzera lo stato:
        usato al cambio posa per evitare che l'EMA "rincorra" la posizione
        precedente generando movimento spurio nei primi frame."""
        if self._smoothed is None or force_reset:
            self._smoothed = raw_pos.copy()
        else:
            self._smoothed = self._alpha * raw_pos + (1.0 - self._alpha) * self._smoothed
        return self._smoothed.copy()


class _DirectionLock:
    """Dopo un evento direzionale, blocca l'evento opposto per N secondi.
    Evita che il movimento di ritorno della mano triggeri il gesto opposto.
    Stessa direzione e altri assi restano disponibili."""

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
# State-machine principale
# =============================================================================


class GestureTracker:
    """Tracker stateful che combina la posa committed corrente con il
    movimento del palmo per emettere eventi del flusso medico:

      • CLICK                    — rilascio di PINCH (durata + movimento ok)
      • DRAG_{LEFT,RIGHT,UP,DOWN} — DRAG con spostamento sopra soglia
      • SWIPE_{LEFT,RIGHT}        — FIVE con movimento orizzontale sopra soglia
      • BACK                      — ingresso in PEACE_H (one-shot)

    PINCH e DRAG sono pose fisicamente distinte: l'utente sceglie con la posa
    delle 3 dita se vuole cliccare o trascinare. Una transizione PINCH → DRAG
    NON emette CLICK; DRAG → PINCH non emette nulla.

    Uso:
        tracker = GestureTracker()
        for frame_kpts in stream:
            label = recognize_gesture(frame_kpts)
            event = tracker.update(label, frame_kpts)
            if event is not None:
                send(event)
    """

    # Default tunable. Vedi note in coda al modulo per la guida di taratura.
    DEFAULT_SWIPE_THRESHOLD = 0.65
    DEFAULT_DRAG_THRESHOLD = 0.35
    DEFAULT_CLICK_MAX_MOVEMENT = 1.0
    DEFAULT_CLICK_MIN_DURATION = 0.08
    DEFAULT_CLICK_MAX_DURATION = 1.5
    DEFAULT_RELEASE_CONFIRMATION_FRAMES = 2
    DEFAULT_GESTURE_COOLDOWN = 0.5
    DEFAULT_DIRECTION_LOCK = 2.0
    DEFAULT_SMOOTHING_ALPHA = 0.45

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
        # Soglie e durate per gli eventi.
        self._swipe_threshold = swipe_threshold
        self._drag_threshold = drag_threshold
        self._click_max_movement = click_max_movement
        self._click_min_duration = click_min_duration
        self._click_max_duration = click_max_duration
        self._release_confirmation_frames = release_confirmation_frames
        self._gesture_cooldown = gesture_cooldown_seconds

        # Componenti (filtri/gate) — composizione, non ereditarietà.
        self._pose_lock = _StickyPoseLock()
        self._smoother = _PositionSmoother(smoothing_alpha)
        self._dir_lock = _DirectionLock(direction_lock_seconds)

        # Cooldown dedicato BACK dopo CLICK: vive fuori dalla session-state
        # perché deve sopravvivere ai cicli di reset durante _gesture_cooldown,
        # altrimenti la finestra effettiva si riduce a _gesture_cooldown.
        self._back_cooldown_until = 0.0

        # Stato per-posa.
        self._reset_session_state()

    # ── Public API ──────────────────────────────────────────────────────────

    def reset(self) -> None:
        """Riazzera completamente il tracker (es. quando la mano esce dal frame)."""
        self._pose_lock.reset()
        self._smoother.reset()
        self._dir_lock.reset()
        self._back_cooldown_until = 0.0
        self._reset_session_state()

    def update(self, gesture: Optional[str], kpts) -> Optional[dict]:
        """Avanza la state-machine di un frame.
        `gesture` è il label raw da `recognize_gesture` (può essere None).
        Restituisce un dict {"type": <EventType>} o None."""
        kpts_arr = np.asarray(kpts, dtype=float)
        hand_size = _hand_size(kpts_arr)
        if hand_size < 1e-6:
            # Frame degenere (mano collassata): non aggiornare nulla.
            return None
        raw_pos = kpts_arr[MIDDLE_MCP].astype(float)

        # 1. Filtra il flicker del label e ottieni la posa committed.
        commit_changed = self._pose_lock.update(gesture)
        if commit_changed:
            # Cambio posa = rilascio implicito → libera tutti i lock di
            # direzione, così l'utente può ripetere un gesto senza attesa.
            self._dir_lock.reset()
        committed = self._pose_lock.committed

        # 2. Smoothing della posizione (reset al cambio posa per evitare drift).
        pos = self._smoother.update(raw_pos, force_reset=commit_changed)
        now = time.monotonic()

        # 3. Cooldown post-evento: scarta tutto, tieni le sessioni a freddo.
        if now < self._cooldown_until:
            self._reset_session_state()
            return None

        # 4. State-machine per posa (mutuamente esclusive).
        if committed == HandPose.PINCH.value:
            self._update_pinch_session(pos, hand_size, now)
            event: Optional[dict] = None
        elif committed == HandPose.DRAG.value:
            event = self._update_drag_session(pos, hand_size, now)
        else:
            event = self._update_release(now)

        # Dopo un CLICK, blocca BACK per 1.5s: il rilascio del pinch apre
        # naturalmente le dita e può attraversare la forma PEACE_H.
        if event is not None and event["type"] == EventType.CLICK.value:
            self._back_cooldown_until = now + 1.5

        # 5. PEACE_H one-shot: BACK al primo frame in cui la posa è committed.
        if (commit_changed
                and committed == HandPose.PEACE_H.value
                and event is None
                and now >= self._back_cooldown_until):
            event = {"type": EventType.BACK.value}

        # 6. FIVE: gestione swipe (può sovrascrivere event di sopra; in pratica
        #    accade solo in transizioni rare PINCH→FIVE+swipe nello stesso frame).
        five_event = self._update_five_session(committed, pos, hand_size, now)
        if five_event is not None:
            event = five_event

        # 7. Cooldown applicato solo per eventi one-shot.
        if event is not None and event["type"] in _ONE_SHOT_EVENTS:
            self._cooldown_until = now + self._gesture_cooldown

        self._prev_committed_pose = committed
        return event

    # ── Stato di sessione ───────────────────────────────────────────────────

    def _reset_session_state(self) -> None:
        """Azzera tutti gli stati di sessione (PINCH/DRAG/FIVE) e il
        riferimento alla posa precedente. Il cooldown e i filtri (smoother,
        pose lock, dir lock) NON vengono toccati: hanno il loro reset."""
        # Sessione PINCH
        self._in_pinch = False
        self._non_pinch_count = 0
        self._pinch_start_time = 0.0
        self._pinch_total_movement = 0.0
        self._prev_pinch_pos: Optional[np.ndarray] = None
        # Sessione DRAG
        self._in_drag = False
        self._drag_anchor: Optional[np.ndarray] = None
        # Sessione FIVE
        self._five_start_pos: Optional[np.ndarray] = None
        # Memoria della posa committed del frame precedente (per FIVE).
        self._prev_committed_pose: Optional[str] = None
        # Cooldown post-evento.
        self._cooldown_until = 0.0

    # ── Branch per posa (chiamati da update) ────────────────────────────────

    def _update_pinch_session(
        self, pos: np.ndarray, hand_size: float, now: float,
    ) -> None:
        """Stato durante un PINCH: accumula movimento per filtrare i click
        accidentali. Non emette eventi: il CLICK arriva sul rilascio."""
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
        # Transizione DRAG → PINCH: cancella drag senza emettere nulla.
        if self._in_drag:
            self._in_drag = False
            self._drag_anchor = None

    def _update_drag_session(
        self, pos: np.ndarray, hand_size: float, now: float,
    ) -> Optional[dict]:
        """Stato durante un DRAG: emette un evento DRAG_* quando lo spostamento
        dall'anchor supera la soglia sull'asse dominante. Anchor resettato
        dopo ogni evento per permettere step concatenati."""
        # Transizione PINCH → DRAG: NON emettere CLICK (era intenzione di drag).
        if self._in_pinch:
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

        # Anchor resettato anche se l'evento è bloccato dal direction lock:
        # il movimento è "consumato", così il ritorno della mano non rimbalza.
        self._drag_anchor = pos
        if self._dir_lock.is_locked(candidate, now):
            return None
        self._dir_lock.apply(candidate, now)
        return {"type": candidate}

    def _classify_drag_direction(self, dx: float, dy: float) -> Optional[str]:
        """Asse dominante + soglia → tipo di evento DRAG_* o None."""
        if abs(dx) >= abs(dy):
            if abs(dx) > self._drag_threshold:
                return (EventType.DRAG_RIGHT.value if dx > 0
                        else EventType.DRAG_LEFT.value)
            return None
        if abs(dy) > self._drag_threshold:
            return (EventType.DRAG_DOWN.value if dy > 0
                    else EventType.DRAG_UP.value)
        return None

    def _update_release(self, now: float) -> Optional[dict]:
        """Posa diversa da PINCH/DRAG: possibile rilascio di una sessione
        attiva. Emette CLICK se il PINCH precedente era nei limiti accettabili."""
        # Fine drag silenziosa.
        if self._in_drag:
            self._in_drag = False
            self._drag_anchor = None

        if not self._in_pinch:
            return None

        # Isteresi: serve N frame consecutivi di non-PINCH per confermare.
        # Filtra i falsi rilasci dovuti a 1 frame "bucato" dal classificatore
        # durante un click reale.
        self._non_pinch_count += 1
        if self._non_pinch_count < self._release_confirmation_frames:
            return None

        duration = now - self._pinch_start_time
        movement = self._pinch_total_movement
        # Reset stato PINCH ora che il rilascio è confermato.
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
        """Posa FIVE attiva: traccia il punto di partenza e emette SWIPE_*
        quando lo spostamento orizzontale dall'inizio supera la soglia."""
        five = HandPose.FIVE.value
        prev = self._prev_committed_pose

        # Ingresso in FIVE: ancora il punto di partenza.
        if committed == five and prev != five:
            self._five_start_pos = pos
            return None

        # Uscita da FIVE: rilascia il punto di partenza.
        if committed != five and prev == five:
            self._five_start_pos = None
            return None

        # Stabile in FIVE: controlla la soglia di swipe.
        if committed == five and self._five_start_pos is not None:
            dx_norm = float((pos[0] - self._five_start_pos[0]) / hand_size)
            if abs(dx_norm) <= self._swipe_threshold:
                return None
            swipe = (EventType.SWIPE_RIGHT.value if dx_norm > 0
                     else EventType.SWIPE_LEFT.value)
            # Reset start_pos in ogni caso: movimento "consumato".
            self._five_start_pos = pos
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
# Note di taratura
# =============================================================================
"""
Tarature da fare dal vivo (parametri di GestureTracker):

  swipe_threshold = 0.65
    Spostamento orizzontale (in unità "hand_size") che fa scattare uno SWIPE.
    0.65 ≈ una mano di ampiezza. Se gli swipe scattano troppo facilmente alza
    a 1.0; se non scattano abbassa a 0.6.

  drag_threshold = 0.35
    Spostamento (in hand_size) dall'anchor per emettere un DRAG_*. Più piccolo
    di swipe per permettere step fini quando si controlla yaw/roll del 3D.
    Dopo l'evento l'anchor viene resettato → eventi concatenabili.

  click_max_duration = 1.5
    Durata massima del PINCH per essere CLICK. Tienilo largo: con sticky lock
    + flicker, un click reale può durare 0.6-0.9s in committed time.

  release_confirmation_frames = 2
    A 30fps = ~66ms di latenza al rilascio. Se il classificatore è molto
    rumoroso aumenta a 3 (100ms). Più alto = il CLICK arriva più lento.
    A 60fps puoi salire a 3-4 frame senza che si senta.

  smoothing_alpha = 0.45
    EMA sulla posizione del MCP medio. 1.0 = nessuno smoothing (rumoroso →
    drag spurio a mano ferma); valori bassi filtrano il jitter ma introducono
    lag. 0.45 è un buon compromesso a 30fps.

  direction_lock_seconds = 2.0
    Dopo un drag/swipe, l'evento opposto è bloccato per questa durata. Si
    azzera al cambio di posa committed (rilascio della mano) → l'utente può
    "ricaricare" abbassando la mano e rialzandola.

Segno di dx/dy: dipende da come la pipeline DepthAI restituisce le coordinate
(mirrored o no). Se il modello 3D ruota al contrario, basta negare nel bridge
layer, NON in questo modulo.

Gesture disabilitate (non servono per il flusso medico, conservate per
riferimento storico): PEACE / ONE / TWO / OK / THREE / FOUR / FIST. Le
classificazioni originali si basavano sulla combinazione thumb_state /
index_state / middle_state / ring_state / little_state in {0, 1, -1}, dove 1
era esteso (y-based) e 0 era ripiegato. Se in futuro servono, si aggiungono
come funzioni `_is_<pose>(kpts) -> bool` accanto a `_is_peace_h` e `_is_five`.
"""
