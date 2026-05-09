import { useEffect } from 'react';
import { getGestureClient } from '../lib/gestureClient.js';

export default function GlobalNavGestures() {
  useEffect(() => {
    const client = getGestureClient();
    const onBack = () => {
      if (typeof window !== 'undefined' && window.history.length > 1) {
        window.history.back();
      }
    };
    client.on('back', onBack);
    return () => { client.off('back', onBack); };
  }, []);

  return null;
}
