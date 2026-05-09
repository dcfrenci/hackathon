/**
 * gesture-client.js
 * Ponte WebSocket tra il backend OAK-D (gesture_bridge_node.py) e la tua webapp.
 *
 * UTILIZZO — importa e registra i listener per le gesture che ti servono:
 *
 *   import { GestureClient } from './gesture-client.js';
 *
 *   const gestures = new GestureClient();      // porta default 8765
 *   // oppure: new GestureClient({ port: 9000, debug: true })
 *
 *   gestures.on('swipe_left',  ()      => prevSlide());
 *   gestures.on('swipe_right', ()      => nextSlide());
 *   gestures.on('scroll',      (value) => zoom(value));
 *   gestures.on('pinch_start', ()      => highlight());
 *   gestures.on('pinch_end',   ()      => select());
 *   gestures.on('reset_view',  ()      => resetView());
 *   gestures.on('zoom_in',     ()      => zoomIn());
 *   gestures.on('zoom_out',    ()      => zoomOut());
 *
 *   // Per smontare (es. React useEffect cleanup):
 *   gestures.destroy();
 *
 * GESTURE DISPONIBILI (dal backend gesture_bridge_node.py):
 *   pinch_start   — pollice + indice si toccano
 *   pinch_end     — pinch rilasciato
 *   swipe_left    — palma aperta (FIVE) + movimento orizzontale sx
 *   swipe_right   — palma aperta (FIVE) + movimento orizzontale dx
 *   scroll        — FIVE o ONE + movimento verticale, value = delta (pos=su, neg=giù)
 *   reset_view    — pugno (FIST)
 *   zoom_in       — V con dita (PEACE)
 *   zoom_out      — cerchio pollice-indice (OK)
 *
 * FORMATO MESSAGGIO RAW dal server:
 *   { "type": "gesture", "gesture": "<nome>", "value": <float>, "timestamp": <float> }
 */

export class GestureClient {
  /**
   * @param {object} [opts]
   * @param {string}  [opts.host='localhost']   Host del server WebSocket.
   * @param {number}  [opts.port=8765]          Porta del server WebSocket.
   * @param {number}  [opts.reconnectDelay=3000] ms prima di ritentare la connessione.
   * @param {number}  [opts.maxRetries=Infinity] Tentativi massimi di riconnessione.
   * @param {boolean} [opts.debug=false]        Log dettagliato in console.
   */
  constructor(opts = {}) {
    this._host           = opts.host           ?? 'localhost';
    this._port           = opts.port           ?? 8765;
    this._reconnectDelay = opts.reconnectDelay ?? 3000;
    this._maxRetries     = opts.maxRetries     ?? Infinity;
    this._debug          = opts.debug          ?? false;

    /** @type {Map<string, Set<Function>>} */
    this._listeners = new Map();

    /** @type {Map<string, Function>} — listener per lo stato della connessione */
    this._statusListeners = new Map();

    this._ws           = null;
    this._retries      = 0;
    this._destroyed    = false;
    this._retryTimeout = null;

    this._connect();
  }

  // ── API pubblica ─────────────────────────────────────────────────────────

  /**
   * Registra un handler per una gesture specifica.
   * Il callback riceve (value, rawEvent) dove:
   *   - value    è il campo numerico (es. delta scroll, o 0 per eventi discreti)
   *   - rawEvent è l'intero oggetto JSON ricevuto dal server
   *
   * @param {string}   gesture  Nome della gesture (es. 'swipe_left').
   * @param {Function} callback fn(value: number, rawEvent: object) => void
   * @returns {this}   Per concatenare: gestures.on(...).on(...)
   */
  on(gesture, callback) {
    if (!this._listeners.has(gesture)) {
      this._listeners.set(gesture, new Set());
    }
    this._listeners.get(gesture).add(callback);
    return this;
  }

  /**
   * Rimuove un handler precedentemente registrato con .on().
   * Se callback è omesso, rimuove tutti i listener per quella gesture.
   *
   * @param {string}    gesture
   * @param {Function} [callback]
   * @returns {this}
   */
  off(gesture, callback) {
    if (!this._listeners.has(gesture)) return this;
    if (callback) {
      this._listeners.get(gesture).delete(callback);
    } else {
      this._listeners.delete(gesture);
    }
    return this;
  }

  /**
   * Ascolta eventi di stato della connessione.
   * @param {'connect'|'disconnect'|'error'} event
   * @param {Function} callback
   * @returns {this}
   */
  onStatus(event, callback) {
    this._statusListeners.set(event, callback);
    return this;
  }

  /**
   * Chiude la connessione e impedisce ulteriori riconnessioni.
   * Da chiamare nel cleanup del componente (es. React useEffect return).
   */
  destroy() {
    this._destroyed = true;
    if (this._retryTimeout) {
      clearTimeout(this._retryTimeout);
      this._retryTimeout = null;
    }
    if (this._ws) {
      this._ws.close();
      this._ws = null;
    }
    this._listeners.clear();
    this._statusListeners.clear();
    this._log('Client distrutto.');
  }

  /** Stato corrente della connessione WebSocket. */
  get connected() {
    return this._ws?.readyState === WebSocket.OPEN;
  }

  // ── Internals ─────────────────────────────────────────────────────────────

  _connect() {
    if (this._destroyed) return;

    const url = `ws://${this._host}:${this._port}`;
    this._log(`Connessione a ${url}…`);

    try {
      this._ws = new WebSocket(url);
    } catch (err) {
      this._log(`WebSocket() ha lanciato:`, err);
      this._scheduleReconnect();
      return;
    }

    this._ws.onopen = () => {
      this._log(`Connesso a ${url}`);
      this._retries = 0;
      this._emitStatus('connect');
    };

    this._ws.onmessage = (ev) => {
      this._handleMessage(ev.data);
    };

    this._ws.onerror = (ev) => {
      this._log('Errore WebSocket', ev);
      this._emitStatus('error', ev);
      // onclose viene sempre chiamato dopo onerror — lì gestiamo la riconnessione
    };

    this._ws.onclose = (ev) => {
      this._log(`Connessione chiusa (code=${ev.code}, reason="${ev.reason}")`);
      this._emitStatus('disconnect', ev);
      this._ws = null;
      this._scheduleReconnect();
    };
  }

  _handleMessage(raw) {
    let msg;
    try {
      msg = JSON.parse(raw);
    } catch {
      this._log('Messaggio non-JSON ignorato:', raw);
      return;
    }

    // Il backend invia sempre { type: "gesture", gesture: "...", value: ..., timestamp: ... }
    if (msg.type !== 'gesture' || typeof msg.gesture !== 'string') {
      this._log('Messaggio sconosciuto ignorato:', msg);
      return;
    }

    this._log(`← ${msg.gesture}  value=${msg.value}`);

    const handlers = this._listeners.get(msg.gesture);
    if (!handlers || handlers.size === 0) return;

    for (const fn of handlers) {
      try {
        fn(msg.value ?? 0, msg);
      } catch (err) {
        console.error(`[GestureClient] Errore nel handler di "${msg.gesture}":`, err);
      }
    }
  }

  _scheduleReconnect() {
    if (this._destroyed) return;
    if (this._retries >= this._maxRetries) {
      this._log(`Raggiunto il limite di ${this._maxRetries} tentativi. Nessuna ulteriore riconnessione.`);
      return;
    }
    this._retries++;
    this._log(`Riconnessione in ${this._reconnectDelay}ms (tentativo ${this._retries})…`);
    this._retryTimeout = setTimeout(() => this._connect(), this._reconnectDelay);
  }

  _emitStatus(event, detail) {
    const fn = this._statusListeners.get(event);
    if (fn) {
      try { fn(detail); } catch (err) {
        console.error(`[GestureClient] Errore nel listener di status "${event}":`, err);
      }
    }
  }

  _log(...args) {
    if (this._debug) console.log('[GestureClient]', ...args);
  }
}
