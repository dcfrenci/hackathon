import { useEffect } from 'react';
import { getGestureClient } from './gestureClient.js';

/**
 * Registra handler React per le gesture. I listener vengono ri-registrati
 * quando cambiano le `deps` (utile quando un handler chiude su uno state, es.
 * `activeTool` nel viewer 3D).
 *
 * @param {Record<string, (value:number, raw:object) => void>} handlers
 * @param {React.DependencyList} [deps]
 */
export function useGesture(handlers, deps = []) {
  useEffect(() => {
    const client = getGestureClient();
    const entries = Object.entries(handlers);
    for (const [name, fn] of entries) client.on(name, fn);
    return () => {
      for (const [name, fn] of entries) client.off(name, fn);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);
}
