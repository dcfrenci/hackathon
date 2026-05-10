import React, { useEffect, useState } from 'react';
import { getGestureClient } from '../lib/gestureClient.js';

/**
 * DepthAlert — banner full-width che avvisa il medico quando la mano dominante
 * è fuori dal range chirurgico configurato in `hand-pose/main.py`
 * (DEPTH_MIN_MM / DEPTH_MAX_MM). Si nasconde appena la mano rientra nel
 * range o esce dal frame. Listener-only: il backend già throttla on-change.
 */
export default function DepthAlert() {
  const [info, setInfo] = useState(null); // { status, depth_mm, min_mm, max_mm }

  useEffect(() => {
    const client = getGestureClient();
    const onDepth = (msg) => setInfo(msg);
    client.onDepthStatus(onDepth);
    return () => client.offDepthStatus(onDepth);
  }, []);

  const status = info?.status;
  const visible = status === 'too_far' || status === 'too_close';
  const isFar = status === 'too_far';

  const title = isFar ? "You're too far" : "You're too close";
  const range = info ? `${info.min_mm}–${info.max_mm} mm` : '';
  const measured = info?.depth_mm != null ? `${info.depth_mm} mm` : '—';
  const subtitle = isFar
    ? `Move closer to the camera (range ${range}, measured ${measured})`
    : `Move back from the camera (range ${range}, measured ${measured})`;

  // Colori coerenti con le variabili globali; rosso/giallo per i due stati.
  const accent = isFar ? '#f59e0b' : '#ef4444';

  return (
    <div
      role="alert"
      aria-live="polite"
      style={{
        position: 'fixed',
        top: 76, // sotto l'header (60px) + 16
        left: '50%',
        transform: `translateX(-50%) translateY(${visible ? '0' : '-140%'})`,
        opacity: visible ? 1 : 0,
        zIndex: 9998,
        display: 'flex',
        alignItems: 'center',
        gap: 14,
        padding: '12px 20px',
        minWidth: 320,
        maxWidth: '90vw',
        borderRadius: 12,
        background: 'rgba(15, 15, 17, 0.92)',
        border: `1px solid ${accent}`,
        boxShadow: `0 8px 32px rgba(0, 0, 0, 0.5), 0 0 24px ${accent}33`,
        backdropFilter: 'blur(12px)',
        color: 'var(--on-surface, #fff)',
        fontFamily: 'var(--font-main, Inter, system-ui, sans-serif)',
        pointerEvents: 'none',
        userSelect: 'none',
        transition: 'transform 0.25s ease, opacity 0.25s ease',
      }}
    >
      <span
        style={{
          width: 12,
          height: 12,
          borderRadius: '50%',
          background: accent,
          boxShadow: `0 0 12px ${accent}`,
          flexShrink: 0,
        }}
      />
      <div style={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
        <span style={{ fontSize: 14, fontWeight: 700, letterSpacing: '0.02em' }}>
          {title}
        </span>
        <span
          style={{
            fontSize: 12,
            color: 'var(--on-surface-variant, #8e8e93)',
            fontFamily: 'var(--font-mono, SF Mono, monospace)',
          }}
        >
          {subtitle}
        </span>
      </div>
    </div>
  );
}
