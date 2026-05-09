# Gesture Mapping — Gesture Bridge

Mappa completa dei gesti riconosciuti e degli eventi inviati via WebSocket.

## Gesti statici (riconosciuti in `gesture_recognition.py`)

| Gesto | Descrizione | Condizioni |
|-------|-------------|-----------|
| **FIVE** | Palma aperta | Tutte le 5 dita estese |
| **FIST** | Pugno chiuso | Tutte le 5 dita chiuse |
| **ONE** | Solo indice alzato | Indice esteso, altre 4 dita chiuse |
| **TWO** | Pollice + indice | Pollice + indice estesi, altre 3 chiuse |
| **THREE** | Pollice + indice + medio | 3 dita estese, altre 2 chiuse |
| **FOUR** | Indice + medio + anulare + mignolo | 4 dita estese, pollice chiuso |
| **PEACE** | V con indice e medio | Indice + medio estesi, altre 3 chiuse |
| **OK** | Pollice in su | Solo pollice esteso, indice toccato al pollice per fare il cerchio |
| **PINCH** | Pollice + indice insieme | Pollice esteso, indice chiuso, distanza < 0.12 hand_size, altre 3 chiuse |

## Gesti dinamici (riconosciuti in `gesture_bridge_node.py`)

### Pinch (State Machine)
```
PINCH rilevato per la prima volta
  ↓
invia: { "gesture": "pinch_start", "value": 1.0 }
  ↓
Rimane PINCH per N frame (nessun evento aggiuntivo)
  ↓
PINCH non più rilevato
  ↓
invia: { "gesture": "pinch_end", "value": 0.0 }
```

**Uso**: clickare su un elemento, confermare un'azione. La web app può reagire su `pinch_start` (es. evidenzia elemento) e `pinch_end` (es. esegui azione).

---

### Swipe (Movimento orizzontale rapido con palma aperta)
**Condizioni**:
- Label `FIVE` mantenuto per ≥ 0.5s
- Spostamento orizzontale ≥ 0.18 (18% della larghezza frame)
- Deriva verticale < 0.12 (12% dell'altezza frame)
- Cooldown 0.6s fra due swipe

**Eventi inviati**:
```json
{ "gesture": "swipe_left",  "value": -0.23 }  // movimento verso sx
{ "gesture": "swipe_right", "value": +0.25 }  // movimento verso dx
```

**Uso**: navigare fra immagini (prev/next).

---

### Scroll (Movimento verticale lento)
**Condizioni**:
- Label `FIVE` o `ONE`
- Spostamento verticale > 0.008 (dead zone)
- Movimento continuo (non istantaneo come swipe)

**Evento inviato**:
```json
{ "gesture": "scroll", "value": +2.5 }  // positivo = scroll up (zoom in), negativo = scroll down (zoom out)
```

**Uso**: zoommare dentro un'immagine, scorrere una lista.

---

### One-Shot (Gesti istantanei con cooldown 1s)

**FIST** (pugno) → `reset_view`
```json
{ "gesture": "reset_view", "value": 0.0 }
```

**PEACE** (V) → `zoom_in`
```json
{ "gesture": "zoom_in", "value": 0.0 }
```

**OK** (cerchio pollice-indice) → `zoom_out`
```json
{ "gesture": "zoom_out", "value": 0.0 }
```

---

## Implementazione lato client (JavaScript)

```javascript
const ws = new WebSocket("ws://localhost:8765");

ws.onmessage = (event) => {
  const msg = JSON.parse(event.data);
  
  switch (msg.gesture) {
    case "pinch_start":
      // es. evidenzia elemento sotto il cursore
      highlightElement(palmX, palmY);
      break;
    
    case "pinch_end":
      // es. seleziona elemento
      selectElement(palmX, palmY);
      break;
    
    case "swipe_left":
      previousImage();
      break;
    
    case "swipe_right":
      nextImage();
      break;
    
    case "scroll":
      // msg.value > 0 = scroll up (zoom in)
      zoomLevel *= (1 + msg.value * 0.05);
      render();
      break;
    
    case "reset_view":
      resetZoomAndPan();
      break;
    
    case "zoom_in":
      zoomLevel *= 1.2;
      break;
    
    case "zoom_out":
      zoomLevel /= 1.2;
      break;
  }
};
```

---

## Tuning degli hyperparameter

File: `utils/gesture_bridge_node.py` (linee 29-36)

| Parametro | Valore | Uso | Tuning |
|-----------|--------|-----|--------|
| `SWIPE_MIN_DIST` | 0.18 | Minimo spostamento orizzontale per swipe | ↑ se troppi false positive, ↓ se lento a triggerare |
| `SWIPE_MAX_DY` | 0.12 | Max deriva verticale durante uno swipe | ↑ per permettere più movimento naturale |
| `SWIPE_WINDOW` | 0.5s | Finestra temporale per rilevare lo swipe | ↑ per movimento lento, ↓ per reattività |
| `SCROLL_DEAD_ZONE` | 0.008 | Movimento ignorato (jitter) | ↑ per eliminare tremore della camera |
| `SCROLL_SCALE` | 5.0 | Amplificazione del delta verticale | ↑ per scroll più veloce, ↓ per più fine |
| `ONESHOT_COOLDOWN` | 1.0s | Cooldown fra due one-shot | ↑ per evitare multipli click accidentali |

---

## Note per la sala sterile

- **Latenza**: tutti i gesti sono calcolati on-device (no rete fra camera e host) — < 50ms di latenza
- **Touchless**: zero contatto con schermi/superfici
- **Robustezza**: usare con **illuminazione controllata** (IR LED o luce diffusa) per evitare glitch di MediaPipe
- **Comfort**: i parametri sono calibrati per movimenti naturali — non è necessario fare gesti esagerati

