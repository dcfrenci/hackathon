import React, { useEffect, useState } from 'react';
import { getGestureClient } from '../lib/gestureClient.js';

export default function GestureHUD() {
  const [connected, setConnected] = useState(false);
  const [last, setLast] = useState(null);

  useEffect(() => {
    const client = getGestureClient();
    setConnected(client.connected);

    const onConnect = () => setConnected(true);
    const onDisconnect = () => setConnected(false);
    const onAny = (name) => setLast({ name, t: Date.now() });

    client.onStatus('connect', onConnect);
    client.onStatus('disconnect', onDisconnect);
    client.onStatus('error', onDisconnect);
    client.onAny(onAny);

    return () => {
      client.offStatus('connect', onConnect);
      client.offStatus('disconnect', onDisconnect);
      client.offStatus('error', onDisconnect);
      client.offAny(onAny);
    };
  }, []);

  const dotColor = connected ? '#22c55e' : '#ef4444';
  const ageMs = last ? Date.now() - last.t : null;
  const fresh = ageMs !== null && ageMs < 1500;

  return (
    <div
      style={{
        position: 'fixed',
        bottom: 16,
        right: 16,
        zIndex: 9999,
        display: 'flex',
        alignItems: 'center',
        gap: 10,
        padding: '6px 12px',
        borderRadius: 999,
        background: 'rgba(15, 15, 17, 0.85)',
        border: '1px solid rgba(255,255,255,0.08)',
        backdropFilter: 'blur(8px)',
        color: '#fff',
        fontSize: 11,
        fontFamily: 'monospace',
        letterSpacing: '0.05em',
        pointerEvents: 'none',
        userSelect: 'none',
      }}
    >
      <span
        style={{
          width: 8,
          height: 8,
          borderRadius: '50%',
          background: dotColor,
          boxShadow: `0 0 8px ${dotColor}`,
        }}
      />
      <span style={{ opacity: 0.75 }}>
        {connected ? 'GESTURE' : 'OFFLINE'}
      </span>
      {last && (
        <span
          style={{
            color: fresh ? 'var(--primary, #2962ff)' : 'rgba(255,255,255,0.4)',
            fontWeight: 700,
            transition: 'color 0.3s ease',
          }}
        >
          {last.name}
        </span>
      )}
    </div>
  );
}
