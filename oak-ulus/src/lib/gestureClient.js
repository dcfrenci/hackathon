/**
 * gestureClient.js — wrapper Astro/React del client WebSocket per le gesture
 * provenienti dal backend OAK-D (`hand-pose/utils/gesture_bridge_node.py`).
 *
 * GESTURE EMESSE DAL BACKEND:
 *   click         — PINCH breve (pollice+indice) trattenuto 80ms–1.5s e rilasciato
 *   swipe_left    — palma aperta (FIVE) + movimento orizzontale a sinistra
 *   swipe_right   — palma aperta (FIVE) + movimento orizzontale a destra
 *   drag_left     — posa DRAG (pollice+indice + 3 dita chiuse) + movimento sx
 *   drag_right    — posa DRAG + movimento dx
 *   drag_up       — posa DRAG + movimento in alto
 *   drag_down     — posa DRAG + movimento in basso
 *
 * Tutti i drag sono CONTINUI sul wire (eventi ripetuti mantenendo la posa).
 * Il behaviour "uno per gesto" usato dai selettori si attiva passando
 * `options.oneShot: ['drag_up', 'drag_down']` a useGesture, vedi useGesture.js.
 *   back          — "il 2": pollice + indice estesi, medio/anulare/mignolo
 *                   ripiegati nel palmo (one-shot)
 *
 * Formato messaggio: { type: "gesture", gesture: "<name>", timestamp: <float> }
 *
 * Singleton modulo-scope: una sola connessione WebSocket per tab.
 */

// Chiave localStorage e default per la mano dominante. Sincronizzati con
// settings.astro: cambiare qui = cambiare lì.
export const HANDEDNESS_STORAGE_KEY = 'oak-ulus-handedness';
export const DEFAULT_HANDEDNESS = 'right';

function readStoredHandedness() {
  if (typeof localStorage === 'undefined') return DEFAULT_HANDEDNESS;
  const v = localStorage.getItem(HANDEDNESS_STORAGE_KEY);
  return v === 'left' || v === 'right' ? v : DEFAULT_HANDEDNESS;
}

class GestureClient {
  constructor(opts = {}) {
    this._host           = opts.host           ?? 'localhost';
    this._port           = opts.port           ?? 8766;
    this._reconnectDelay = opts.reconnectDelay ?? 3000;
    this._maxRetries     = opts.maxRetries     ?? Infinity;
    this._debug          = opts.debug          ?? false;

    this._listeners = new Map();
    this._statusListeners = new Map();

    this._ws           = null;
    this._retries      = 0;
    this._destroyed    = false;
    this._retryTimeout = null;

    this._connect();
  }

  on(gesture, callback) {
    if (!this._listeners.has(gesture)) this._listeners.set(gesture, new Set());
    this._listeners.get(gesture).add(callback);
    return this;
  }

  off(gesture, callback) {
    if (!this._listeners.has(gesture)) return this;
    if (callback) this._listeners.get(gesture).delete(callback);
    else this._listeners.delete(gesture);
    return this;
  }

  onStatus(event, callback) {
    if (!this._statusListeners.has(event)) this._statusListeners.set(event, new Set());
    this._statusListeners.get(event).add(callback);
    return this;
  }

  offStatus(event, callback) {
    if (!this._statusListeners.has(event)) return this;
    if (callback) this._statusListeners.get(event).delete(callback);
    else this._statusListeners.delete(event);
    return this;
  }

  onAny(callback) {
    if (!this._anyListeners) this._anyListeners = new Set();
    this._anyListeners.add(callback);
    return this;
  }

  offAny(callback) {
    if (!this._anyListeners) return this;
    this._anyListeners.delete(callback);
    return this;
  }

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
    if (this._anyListeners) this._anyListeners.clear();
  }

  get connected() {
    return this._ws?.readyState === WebSocket.OPEN;
  }

  send(payload) {
    if (this._ws?.readyState !== WebSocket.OPEN) return false;
    try {
      this._ws.send(typeof payload === 'string' ? payload : JSON.stringify(payload));
      return true;
    } catch (err) {
      this._log('send() fallito:', err);
      return false;
    }
  }

  /**
   * Imposta la mano dominante. Salva in localStorage (default) e notifica
   * il backend. Se la WebSocket non è aperta il valore resta in localStorage
   * e verrà pubblicato al prossimo onopen.
   */
  setHandedness(hand, { persist = true } = {}) {
    if (hand !== 'left' && hand !== 'right') return false;
    if (persist && typeof localStorage !== 'undefined') {
      localStorage.setItem(HANDEDNESS_STORAGE_KEY, hand);
    }
    return this.send({ type: 'set_handedness', hand });
  }

  _connect() {
    if (this._destroyed) return;
    const url = `ws://${this._host}:${this._port}`;
    this._log(`Connessione a ${url}…`);

    try {
      this._ws = new WebSocket(url);
    } catch (err) {
      this._log('WebSocket() ha lanciato:', err);
      this._scheduleReconnect();
      return;
    }

    this._ws.onopen = () => {
      this._log(`Connesso a ${url}`);
      this._retries = 0;
      // Pubblica la mano dominante salvata: il backend così sa subito quale
      // mano scartare, anche dopo un riavvio della pipeline o un reconnect.
      this.setHandedness(readStoredHandedness(), { persist: false });
      this._emitStatus('connect');
    };

    this._ws.onmessage = (ev) => this._handleMessage(ev.data);

    this._ws.onerror = (ev) => {
      this._log('Errore WebSocket', ev);
      this._emitStatus('error', ev);
    };

    this._ws.onclose = (ev) => {
      this._log(`Chiusa (code=${ev.code})`);
      this._emitStatus('disconnect', ev);
      this._ws = null;
      this._scheduleReconnect();
    };
  }

  _handleMessage(raw) {
    let msg;
    try { msg = JSON.parse(raw); }
    catch { this._log('Messaggio non-JSON:', raw); return; }

    if (msg.type !== 'gesture' || typeof msg.gesture !== 'string') {
      this._log('Messaggio sconosciuto:', msg);
      return;
    }

    this._log(`← ${msg.gesture}`);

    if (this._anyListeners) {
      for (const fn of this._anyListeners) {
        try { fn(msg.gesture, msg); }
        catch (err) { console.error('[GestureClient] onAny error:', err); }
      }
    }

    const handlers = this._listeners.get(msg.gesture);
    if (!handlers || handlers.size === 0) return;
    for (const fn of handlers) {
      try { fn(msg.value ?? 0, msg); }
      catch (err) { console.error(`[GestureClient] handler "${msg.gesture}":`, err); }
    }
  }

  _scheduleReconnect() {
    if (this._destroyed) return;
    if (this._retries >= this._maxRetries) return;
    this._retries++;
    this._log(`Riconnessione in ${this._reconnectDelay}ms (tent. ${this._retries})`);
    this._retryTimeout = setTimeout(() => this._connect(), this._reconnectDelay);
  }

  _emitStatus(event, detail) {
    const set = this._statusListeners.get(event);
    if (!set) return;
    for (const fn of set) {
      try { fn(detail); }
      catch (err) { console.error(`[GestureClient] status "${event}":`, err); }
    }
  }

  _log(...args) {
    if (this._debug) console.log('[GestureClient]', ...args);
  }
}

let _client = null;

export function getGestureClient(opts = {}) {
  if (!_client) {
    _client = new GestureClient({ port: 8766, debug: false, ...opts });
  }
  return _client;
}

export function destroyGestureClient() {
  if (_client) {
    _client.destroy();
    _client = null;
  }
}

export { GestureClient };
