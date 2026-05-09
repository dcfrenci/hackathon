import React, { useState } from 'react';

const patients = [
  { id: '8842-XJ', name: 'Mario Rossi', dept: 'CARDIOLOGY', status: 'READY FOR PREP', time: '08:30 AM', image: '/profiles/mario.png' },
  { id: '9102-LK', name: 'Anna Bianchi', dept: 'ORTHOPEDICS', status: 'STAGING', time: '09:45 AM', image: '/profiles/anna.png' },
  { id: '7721-OP', name: 'Luca Moretti', dept: 'NEUROSURGERY', status: 'STAGING', time: '11:15 AM', image: '/profiles/luca.png' },
];

export default function PatientSelector() {
  const [selectedIndex, setSelectedIndex] = useState(0);
  const [feedback, setFeedback] = useState(null);

  const triggerFeedback = (type) => {
    setFeedback(type);
    setTimeout(() => setFeedback(null), 400);
  };

  const handlePrev = () => {
    setSelectedIndex((prev) => (prev > 0 ? prev - 1 : patients.length - 1));
    triggerFeedback('up');
  };
  const handleNext = () => {
    setSelectedIndex((prev) => (prev < patients.length - 1 ? prev + 1 : 0));
    triggerFeedback('down');
  };
  const handleSelect = () => {
    triggerFeedback('select');
    setTimeout(() => {
      window.location.href = `/patient/${patients[selectedIndex].id}/`;
    }, 450);
  };

  return (
    <div className="container" style={{ position: 'relative' }}>
      <div className="queue-header">
        <div className="label-small">ACTIVE OPERATIVE SCHEDULE</div>
        <h1 style={{ fontSize: '2.5rem', margin: '8px 0 32px 0', fontWeight: '400' }}>Daily Queue</h1>
      </div>

      <div style={{ position: 'relative' }}>
        {/* Directional Feedback Aligned to List */}
        {feedback === 'up' && (
          <div className="feedback-indicator-local animate-float-up" style={{ right: '-100px', top: '10%' }}>
            <svg width="150" height="150" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" style={{ opacity: 0.8 }}><path d="m17 11-5-5-5 5M17 18l-5-5-5 5"/></svg>
          </div>
        )}
        {feedback === 'down' && (
          <div className="feedback-indicator-local animate-float-down" style={{ right: '-100px', bottom: '10%' }}>
            <svg width="150" height="150" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" style={{ opacity: 0.8 }}><path d="m7 13 5 5 5-5M7 6l5 5 5-5"/></svg>
          </div>
        )}

        <div className="card-list" style={{ gap: '16px' }}>
        {patients.map((p, index) => (
          <div 
            key={p.id} 
            className={`patient-card ${index === selectedIndex ? 'active-selection-anim' : ''}`}
            onClick={() => setSelectedIndex(index)}
            style={{
              padding: '24px 32px',
              height: '100px',
              background: '#121212',
              borderRadius: '12px',
              border: index === selectedIndex ? '2px solid var(--primary)' : '1px solid #333',
              boxShadow: 'none',
              transition: 'all 0.3s ease',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'space-between'
            }}
          >
            <div style={{ display: 'flex', alignItems: 'center', gap: '24px' }}>
              <div style={{ 
                width: '64px', 
                height: '64px', 
                borderRadius: '50%', 
                overflow: 'hidden', 
                border: index === selectedIndex ? '2px solid var(--primary)' : '2px solid rgba(255,255,255,0.1)',
                boxShadow: index === selectedIndex ? '0 0 15px var(--primary)' : 'none',
                transition: 'all 0.3s ease'
              }}>
                <img 
                  src={p.image} 
                  alt={p.name} 
                  style={{ width: '100%', height: '100%', objectFit: 'cover' }} 
                />
              </div>
              <div>
                <div style={{ fontSize: '1.4rem', fontWeight: '600', color: '#fff' }}>{p.name}</div>
                <div className="label-small" style={{ fontSize: '11px', color: 'rgba(255,255,255,0.4)' }}>
                  ID: <span className="data-value" style={{ color: 'inherit' }}>{p.id}</span> • {p.dept}
                </div>
              </div>
            </div>
            <div style={{ textAlign: 'right' }}>
              <div className="label-small" style={{ marginBottom: '4px', color: index === selectedIndex ? 'var(--primary)' : 'var(--on-surface-variant)' }}>{p.status}</div>
              <div className="data-value" style={{ color: 'rgba(255,255,255,0.3)', fontSize: '0.8rem' }}>{p.time}</div>
            </div>
          </div>
        ))}
      </div>

      <div style={{ marginTop: '80px', display: 'flex', justifyContent: 'center', gap: '16px', width: 'fit-content', marginLeft: 'auto', marginRight: 'auto' }}>
        <button className="hud-btn" onClick={handlePrev} style={{ borderRadius: '16px' }}>
          <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" style={{ opacity: 0.7 }}><path d="m17 11-5-5-5 5M17 18l-5-5-5 5"/></svg>
          PREV
        </button>
        <button className="hud-btn" onClick={handleNext} style={{ borderRadius: '16px' }}>
          <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" style={{ opacity: 0.7 }}><path d="m7 13 5 5 5-5M7 6l5 5 5-5"/></svg>
          NEXT
        </button>
        <button className="hud-btn active-primary" onClick={handleSelect} style={{ borderRadius: '16px', position: 'relative' }}>
          {feedback === 'select' && (
            <>
              <div className="ripple-pond animate-pond" style={{ animationDelay: '0s' }}></div>
              <div className="ripple-pond animate-pond" style={{ animationDelay: '0.1s' }}></div>
            </>
          )}
          <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5">
            <path d="M12 20V10"/>
            <path d="M18 11V6a2 2 0 0 0-2-2v0a2 2 0 0 0-2 2v0"/>
            <path d="M14 10V4a2 2 0 0 0-2-2v0a2 2 0 0 0-2 2v0"/>
            <path d="M10 10.5V6a2 2 0 0 0-2-2v0a2 2 0 0 0-2 2v0"/>
            <path d="M18 8a2 2 0 1 1 4 0v6a8 8 0 0 1-8 8h-2c-2.8 0-4.5-.86-5.99-2.34l-3.6-3.6a2 2 0 0 1 2.83-2.82L7 15"/>
            <circle cx="12" cy="2" r="1" fill="currentColor"/>
          </svg>
          SELECT
        </button>
      </div>
    </div>
    </div>
  );
}
