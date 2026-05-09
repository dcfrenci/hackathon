import React, { useState } from 'react';

const scans = [
  { type: '3D RENDER', title: 'CARDIAC 3D', source: 'MRI-S4 · 12.04.2024', img: '/assets/cardiac.png', slug: 'cardiac-3d' },
  { type: '3D RENDER', title: 'LVOT Aneurysm 3D', source: 'DR-SCAN-2 · 11.04.2024', img: '/assets/thorax.png', slug: 'lvot-aneurysm-3d' },
  { type: 'X-RAY SEQUENCE', title: 'THORAX DYNAMIC', source: 'SEQ-1 · 12.04.2024', img: '/assets/xrays.png', slug: 'thorax-x-ray' },
  { type: '3D RENDER', title: 'CRANIAL 3D', source: 'SCAN-UNIT-B · 12.04.2024', img: '/assets/cranial.png', slug: 'cranial-3d' },
];

export default function ScanSelector() {
  const [selectedIndex, setSelectedIndex] = useState(0);
  const [feedback, setFeedback] = useState(null);

  const triggerFeedback = (type) => {
    setFeedback(type);
    setTimeout(() => setFeedback(null), 400);
  };

  const handleUp = () => {
    setSelectedIndex((prev) => (prev > 0 ? prev - 1 : scans.length - 1));
    triggerFeedback('up');
  };
  const handleDown = () => {
    setSelectedIndex((prev) => (prev < scans.length - 1 ? prev + 1 : 0));
    triggerFeedback('down');
  };
  const handleSelect = () => {
    triggerFeedback('select');
    setTimeout(() => {
      // Extract patientId from URL (e.g., /patient/8842-XJ/)
      const pathParts = window.location.pathname.split('/').filter(Boolean);
      const patientId = pathParts[pathParts.length - 1] || '8842-XJ';
      window.location.href = `/viewer/${patientId}/${scans[selectedIndex].slug}/`;
    }, 450);
  };

  return (
    <main className="container">
      <div style={{ marginBottom: '24px' }}>
        <a href="/" style={{ display: 'inline-flex', alignItems: 'center', gap: '8px', color: 'var(--primary)', textDecoration: 'none', fontWeight: 'bold', transition: 'opacity 0.2s' }} onMouseOver={(e) => e.currentTarget.style.opacity='0.7'} onMouseOut={(e) => e.currentTarget.style.opacity='1'}>
          <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M19 12H5M12 19l-7-7 7-7"/></svg>
          BACK TO PATIENCE LIST
        </a>
      </div>
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 350px', gap: '40px', alignItems: 'start', position: 'relative' }}>
        <div style={{ position: 'relative' }}>
        {/* Directional Feedback Aligned to List */}
        {feedback === 'up' && (
          <div className="feedback-indicator-local animate-float-up" style={{ left: '-120px', top: '10%' }}>
            <svg width="150" height="150" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" style={{ opacity: 0.8 }}><path d="m17 11-5-5-5 5M17 18l-5-5-5 5" /></svg>
          </div>
        )}
        {feedback === 'down' && (
          <div className="feedback-indicator-local animate-float-down" style={{ left: '-120px', bottom: '10%' }}>
            <svg width="150" height="150" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" style={{ opacity: 0.8 }}><path d="m7 13 5 5 5-5M7 6l5 5 5-5" /></svg>
          </div>
        )}

        <div style={{ display: 'flex', gap: '20px', alignItems: 'stretch' }}>
          {/* Vertical Progress Bar */}
          <div style={{ width: '6px', background: 'rgba(255,255,255,0.05)', borderRadius: '3px', position: 'relative', overflow: 'hidden', height: '460px' }}>
            <div style={{
              position: 'absolute',
              top: `${(Math.max(0, Math.min(selectedIndex - 1, scans.length - 3)) / scans.length) * 100}%`,
              height: `${(3 / scans.length) * 100}%`,
              width: '100%',
              background: 'var(--primary)',
              borderRadius: '3px',
              boxShadow: '0 0 15px var(--primary)',
              transition: 'all 0.3s cubic-bezier(0.4, 0, 0.2, 1)'
            }}></div>
          </div>

          <div className="card-list" style={{ flex: 1 }}>
            {(() => {
              const start = Math.max(0, Math.min(selectedIndex - 1, scans.length - 3));
              return scans.slice(start, start + 3).map((s, sliceIndex) => {
                const actualIndex = start + sliceIndex;
                const typeColor = s.type === '3D RENDER' ? '#FF9800' : (s.type === 'X-RAY SEQUENCE' ? '#4CAF50' : 'var(--primary)');

                return (
                  <div
                    key={s.title}
                    className={`scan-card ${actualIndex === selectedIndex ? 'active-selection-anim' : ''}`}
                    onClick={() => setSelectedIndex(actualIndex)}
                    style={{
                      display: 'flex',
                      gap: '0',
                      padding: 0,
                      overflow: 'hidden',
                      alignItems: 'stretch',
                      height: '140px',
                      background: '#121212',
                      borderRadius: '16px',
                      border: actualIndex === selectedIndex ? `2px solid ${typeColor}` : '1px solid #333',
                      cursor: 'pointer',
                      transition: 'all 0.3s ease',
                      marginBottom: sliceIndex < 2 ? '20px' : 0
                    }}
                  >
                    <div style={{ width: '160px', height: '100%', overflow: 'hidden', background: '#000' }}>
                      <img src={s.img} alt={s.title} style={{ width: '100%', height: '100%', objectFit: 'cover', opacity: actualIndex === selectedIndex ? 1 : 0.4, transition: 'opacity 0.3s' }} />
                    </div>
                    <div style={{ padding: '24px', flex: 1, display: 'flex', flexDirection: 'column', justifyContent: 'center' }}>
                      <div className="label-small" style={{ marginBottom: '4px', color: typeColor, fontWeight: 'bold', letterSpacing: '1px' }}>{s.type}</div>
                      <div style={{ fontSize: '1.4rem', fontWeight: '600', color: '#fff', marginBottom: '4px' }}>{s.title}</div>
                      <div className="data-value" style={{ fontSize: '0.8rem', color: 'rgba(255,255,255,0.3)' }}>{s.source}</div>
                    </div>
                  </div>
                );
              });
            })()}
          </div>
        </div>

        <div style={{ marginTop: '60px', display: 'flex', justifyContent: 'center', gap: '16px', width: 'fit-content', marginLeft: 'auto', marginRight: 'auto' }}>
          <button className="hud-btn" onClick={handleUp} style={{ borderRadius: '16px' }}>
            <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" style={{ opacity: 0.7 }}><path d="m17 11-5-5-5 5M17 18l-5-5-5 5" /></svg>
            UP
          </button>
          <button className="hud-btn" onClick={handleDown} style={{ borderRadius: '16px' }}>
            <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" style={{ opacity: 0.7 }}><path d="m7 13 5 5 5-5M7 6l5 5 5-5" /></svg>
            DOWN
          </button>
          <button className="hud-btn active-primary" onClick={handleSelect} style={{ borderRadius: '16px', position: 'relative' }}>
            {feedback === 'select' && (
              <>
                <div className="ripple-pond animate-pond" style={{ animationDelay: '0s' }}></div>
                <div className="ripple-pond animate-pond" style={{ animationDelay: '0.1s' }}></div>
              </>
            )}
            <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5">
              <path d="M12 20V10" />
              <path d="M18 11V6a2 2 0 0 0-2-2v0a2 2 0 0 0-2 2v0" />
              <path d="M14 10V4a2 2 0 0 0-2-2v0a2 2 0 0 0-2 2v0" />
              <path d="M10 10.5V6a2 2 0 0 0-2-2v0a2 2 0 0 0-2 2v0" />
              <path d="M18 8a2 2 0 1 1 4 0v6a8 8 0 0 1-8 8h-2c-2.8 0-4.5-.86-5.99-2.34l-3.6-3.6a2 2 0 0 1 2.83-2.82L7 15" />
              <circle cx="12" cy="2" r="1" fill="currentColor" />
            </svg>
            SELECT
          </button>
        </div>
      </div>

      <aside className="glass-panel" style={{ padding: '32px', borderRadius: '24px', border: '1px solid rgba(255,255,255,0.1)', height: 'fit-content' }}>
        <div className="label-small" style={{ marginBottom: '24px' }}>SELECTED SCAN METRICS</div>
        <div style={{ display: 'flex', flexDirection: 'column', gap: '32px' }}>
          <div><div className="label-small">VOLUMETRIC DATA</div><div className="data-value">2.4 GB</div></div>
          <div><div className="label-small">RESOLUTION</div><div className="data-value">0.4 mm px</div></div>
          <div><div className="label-small">SCAN TIME</div><div className="data-value">12.5 min</div></div>
          <div style={{ marginTop: '16px', padding: '16px', background: 'rgba(41, 98, 255, 0.1)', borderRadius: '12px', borderLeft: '4px solid var(--primary)' }}>
            <div className="label-small" style={{ color: 'var(--primary)', marginBottom: '4px' }}>AI DIAGNOSTIC READY</div>
            <div style={{ fontSize: '0.8rem', opacity: 0.7 }}>Segmentation complete. 3D renders available.</div>
          </div>
        </div>
      </aside>
      </div>
    </main>
  );
}
