import { useGesture } from '../lib/useGesture.js';

export default function GlobalNavGestures() {
  // oneShot: la posa "L" (PEACE_H) può ri-committarsi più volte se il
  // classificatore oscilla per qualche frame, e ogni commit emetterebbe un
  // back (= salto a /root). Disarmiamo lato client come per drag verticale:
  // un solo back per "burst", riarmato dopo la finestra di silenzio.
  useGesture({
    back: () => {
      if (typeof window !== 'undefined' && window.history.length > 1) {
        window.history.back();
      }
    },
  }, [], { oneShot: ['back'] });

  return null;
}
