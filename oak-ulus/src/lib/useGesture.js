import { useEffect, useRef } from 'react';
import { getGestureClient } from './gestureClient.js';

/**
 * Finestra di silenzio (ms) usata da options.oneShot per riarmare un gesto:
 * dopo aver "sparato" una volta, il gesto resta disarmato finché non passa
 * questo intervallo senza riceverlo. Tarato sopra il cooldown della state-
 * machine (0.5s) per coprire un rilascio + ripresa naturali della posa DRAG.
 */
const ONE_SHOT_SILENCE_MS = 600;

/**
 * Registra handler React per le gesture. I listener vengono ri-registrati
 * quando cambiano le `deps` (utile quando un handler chiude su uno state, es.
 * `activeTool` nel viewer 3D).
 *
 * @param {Record<string, (value:number, raw:object) => void>} handlers
 * @param {React.DependencyList} [deps]
 * @param {{ oneShot?: string[] }} [options]
 *   oneShot: lista di nomi di gesture che devono "sparare una sola volta per
 *   gesto". Per il backend questi gesti sono continui (es. drag_up emesso più
 *   volte mentre tieni la posa DRAG). oneShot li disarma dopo il primo fire e
 *   li riarma quando passano ONE_SHOT_SILENCE_MS senza riceverli (= la posa è
 *   stata rilasciata). I gesti non in oneShot passano invariati.
 */
export function useGesture(handlers, deps = [], options = {}) {
  const oneShotSet = new Set(options.oneShot ?? []);
  // Stato per-gesto: { armed, timer }. In ref così sopravvive ai re-render
  // dell'effect (deps cambiano) senza perdere il "non-armed" corrente.
  const stateRef = useRef({});

  useEffect(() => {
    const client = getGestureClient();
    const wrapped = [];

    for (const [name, fn] of Object.entries(handlers)) {
      let cb = fn;

      if (oneShotSet.has(name)) {
        if (!stateRef.current[name]) {
          stateRef.current[name] = { armed: true, timer: null };
        }
        cb = (val, raw) => {
          const s = stateRef.current[name];
          // Ogni evento ricevuto rinvia il "riarmo": finché continuano ad
          // arrivare, la posa è ancora tenuta → restiamo disarmati.
          if (s.timer) clearTimeout(s.timer);
          s.timer = setTimeout(() => {
            s.armed = true;
            s.timer = null;
          }, ONE_SHOT_SILENCE_MS);
          if (!s.armed) return;
          s.armed = false;
          fn(val, raw);
        };
      }

      client.on(name, cb);
      wrapped.push([name, cb]);
    }

    return () => {
      for (const [name, cb] of wrapped) client.off(name, cb);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);
}
