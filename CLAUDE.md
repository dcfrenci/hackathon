# Hackathon 2026 — Gesture Control per Ambienti Medici Sterili

## Obiettivo del progetto

Sistema di controllo gestuale touchless per medici in sale sterili. Il medico muove le mani davanti a una camera OAK (Luxonis) per interagire con una web app che mostra lastre radiografiche e rappresentazioni 3D. Zero contatto fisico richiesto.

**Flusso principale:**
```
OAK Camera → DepthAI v3 pipeline → gesture detection → eventi → web app
```

## Architettura

### 1. Layer camera (DepthAI v3)
- Camera OAK rileva le mani in tempo reale
- Pipeline depthai v3 con MediaPipe Hand Landmarker (Model Zoo Luxonis)
- Classificazione gesture (scroll, zoom, swipe, pinch, ecc.)
- Output: eventi gesture strutturati

### 2. Layer bridge (Python host)
- Riceve gli output dalla pipeline depthai
- Traduce gesture in comandi (es. "swipe sinistra" → "immagine precedente")
- Invia eventi alla web app via WebSocket o REST

### 3. Layer web app
- Riceve gli eventi gesture
- Esegue le azioni corrispondenti sull'UI (navigare tra lastre, zoom 3D, rotate, ecc.)

## Stack tecnico

| Componente | Tecnologia |
|------------|-----------|
| Camera | Luxonis OAK (RVC2 o RVC4) |
| SDK camera | DepthAI v3 (`pip install depthai`) |
| Gesture model | MediaPipe Hand Landmarker (Model Zoo Luxonis) |
| Post-processing | `depthai-nodes` (`pip install depthai-nodes`) |
| Bridge host | Python |
| Comunicazione app | WebSocket (da definire) |
| Web app | Da definire |

## Setup ambiente

```bash
# Virtual environment (già creato)
source .hackathon/bin/activate

# Pacchetti installati
# depthai==3.6.1
# numpy==2.4.4

# Da installare
pip install depthai-nodes opencv-python
```

Il venv è in `.hackathon/` nella root del progetto.

## Modello gesture

Luxonis fornisce il **MediaPipe Hand Landmarker** nel loro Model Zoo, nativo per depthai v3:
- URL modello: https://models.luxonis.com/luxonis/mediapipe-hand-landmarker/42815cca-deab-4860-b4a9-d44ebbe2988a
- Rileva 21 landmark 3D per mano
- Pipeline 2-stage: palm detection → hand landmark

```python
import depthai as dai
from depthai_nodes import ParsingNeuralNetwork

with dai.Pipeline() as pipeline:
    cam = pipeline.create(dai.node.Camera).build()
    model_description = dai.NNModelDescription(
        "luxonis/mediapipe-hand-landmarker:224x224"
    )
    nn = pipeline.create(ParsingNeuralNetwork).build(cam, model_description)
    pipeline.run()
```

## Documentazione locale

Tutta la documentazione Luxonis è nella cartella `docs/`. **Leggere sempre prima di cercare online.**

| File | Contenuto |
|------|-----------|
| `docs/INDEX.md` | Mappa completa di tutti i file + glossario |
| `docs/06-depthai-v3.md` | SDK v3: Device, Pipeline, Nodes, Messages, API Python |
| `docs/07-depthai-v3-examples.md` | Catalogo esempi v3 per categoria |
| `docs/08-ai-inference.md` | Model Zoo, conversione modelli, DepthAI Nodes, NN Archive |
| `docs/03-hardware-products.md` | Modelli OAK disponibili (OAK-D, OAK4, ecc.) |
| `docs/05-oak-apps.md` | OAK Apps containerizzate, oakctl |
| `docs/11-troubleshooting.md` | Troubleshooting device, NN, conversione |
| `docs/10-depthai-v2-legacy.md` | DepthAI v2 — solo per riferimento, NON usare per nuove feature |

## Riferimenti esterni chiave

- DepthAI v3 docs: https://docs.luxonis.com/software-v3/depthai.md
- Model Zoo Luxonis: https://models.luxonis.com
- DepthAI Nodes API: https://docs.luxonis.com/software-v3/ai-inference/inference/depthai-nodes/api-reference.md
- Esempi v3 online: https://docs.luxonis.com/software-v3/depthai/examples.md

## Note importanti

- Usare **sempre depthai v3** — la v2 è legacy, API completamente diversa
- Il repo `geaxgx/depthai_hand_tracker` usa la v2: **non usarlo**
- Ambiente sterile = nessun tocco, latenza bassa è critica
- Considerare illuminazione IR per stanze con luce controllata

