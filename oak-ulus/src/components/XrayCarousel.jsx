import React, { useState, useEffect } from 'react';
import { useGesture } from '../lib/useGesture.js';

const xrayImages = [
  "Screenshot 2026-05-09 at 19.33.51.png",
  "Screenshot 2026-05-09 at 19.34.10.png",
  "Screenshot 2026-05-09 at 19.34.26.png",
  "Screenshot 2026-05-09 at 19.34.38.png",
  "Screenshot 2026-05-09 at 19.34.59.png",
  "Screenshot 2026-05-09 at 19.35.13.png",
  "Screenshot 2026-05-09 at 19.35.25.png",
  "Screenshot 2026-05-09 at 19.35.45.png",
  "Screenshot 2026-05-09 at 19.35.56.png",
  "Screenshot 2026-05-09 at 19.36.09.png",
  "Screenshot 2026-05-09 at 19.36.19.png",
  "Screenshot 2026-05-09 at 19.36.31.png",
  "Screenshot 2026-05-09 at 19.36.41.png",
  "Screenshot 2026-05-09 at 19.36.52.png",
  "Screenshot 2026-05-09 at 19.37.04.png",
  "Screenshot 2026-05-09 at 19.37.22.png",
  "Screenshot 2026-05-09 at 19.37.32.png",
  "Screenshot 2026-05-09 at 19.37.42.png",
  "Screenshot 2026-05-09 at 19.37.53.png",
  "Screenshot 2026-05-09 at 19.38.05.png",
  "Screenshot 2026-05-09 at 19.38.15.png",
  "Screenshot 2026-05-09 at 19.38.26.png",
  "Screenshot 2026-05-09 at 19.38.37.png",
  "Screenshot 2026-05-09 at 19.38.54.png"
];

export default function XrayCarousel({ patientId = "patient_1" }) {
  const [currentIndex, setCurrentIndex] = useState(0);

  const handlePrev = () => {
    setCurrentIndex((prev) => (prev > 0 ? prev - 1 : xrayImages.length - 1));
  };

  const handleNext = () => {
    setCurrentIndex((prev) => (prev < xrayImages.length - 1 ? prev + 1 : 0));
  };

  useGesture({
    swipe_left: () => handlePrev(),
    swipe_right: () => handleNext(),
  });

  // Preload images for smooth scrubbing
  useEffect(() => {
    xrayImages.forEach((img) => {
      const image = new Image();
      image.src = `/Xrays/8842-XJ/${img}`;
    });
  }, []);

  return (
    <div style={{ position: 'relative', width: '100%', height: '100%', display: 'flex', flexDirection: 'column', backgroundColor: '#0f0f11' }}>
      <style>{`
        @keyframes fadeIn {
          from { opacity: 0; transform: scale(0.98); }
          to { opacity: 1; transform: scale(1); }
        }
        @keyframes rippleOut {
          0% { transform: scale(1); opacity: 1; border-width: 8px; box-shadow: 0 0 0px var(--primary); }
          100% { transform: scale(6); opacity: 0; border-width: 2px; box-shadow: 0 0 50px var(--primary); }
        }
        .xray-image-transition {
          animation: fadeIn 0.2s ease-out;
        }
        .nav-btn:active {
          transform: scale(0.9) !important;
          background: rgba(41, 98, 255, 0.3) !important;
        }
        .nav-btn:active::after {
          content: "";
          position: absolute;
          top: 0; left: 0; right: 0; bottom: 0;
          border-radius: 50%;
          border: 8px solid var(--primary);
          animation: rippleOut 0.6s cubic-bezier(0, 0, 0.2, 1) forwards;
        }
      `}</style>

      {/* Top Bar */}
      <div style={{ position: 'absolute', top: 0, left: 0, right: 0, padding: '24px', display: 'flex', justifyContent: 'space-between', zIndex: 10 }}>
        <a href={`/patient/${patientId}/`} style={{ display: 'flex', alignItems: 'center', gap: '8px', color: 'var(--primary)', textDecoration: 'none', fontWeight: 'bold' }}>
          <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M19 12H5M12 19l-7-7 7-7"/></svg>
          BACK
        </a>
        <div style={{ color: '#fff', fontSize: '14px', letterSpacing: '2px' }}>
          X-RAY SEQUENCE · {currentIndex + 1} / {xrayImages.length}
        </div>
      </div>

      {/* Main Image Viewer */}
      <div style={{ flex: 1, display: 'flex', justifyContent: 'center', alignItems: 'center', overflow: 'hidden', padding: '80px 40px 40px 40px', position: 'relative' }}>
        
        {/* Left Arrow */}
        <button 
          onClick={handlePrev}
          className="nav-btn"
          style={{ position: 'absolute', left: '40px', background: 'rgba(255,255,255,0.05)', border: '1px solid rgba(255,255,255,0.1)', borderRadius: '50%', width: '64px', height: '64px', color: 'var(--primary)', cursor: 'pointer', zIndex: 20, display: 'flex', justifyContent: 'center', alignItems: 'center', backdropFilter: 'blur(10px)', transition: 'all 0.1s ease-out' }}
          onMouseOver={(e) => { e.currentTarget.style.background = 'rgba(255,255,255,0.1)'; e.currentTarget.style.transform = 'scale(1.1)'; }}
          onMouseOut={(e) => { e.currentTarget.style.background = 'rgba(255,255,255,0.05)'; e.currentTarget.style.transform = 'scale(1)'; }}
        >
          <svg width="32" height="32" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M15 18l-6-6 6-6"/></svg>
        </button>

        {/* 3D Coverflow Carousel */}
        <div style={{ position: 'relative', width: '100%', height: '100%', display: 'flex', justifyContent: 'center', alignItems: 'center', perspective: '1500px' }}>
          {[-2, -1, 0, 1, 2].map((offset) => {
            const index = (currentIndex + offset + xrayImages.length) % xrayImages.length;
            
            let translateX = 0;
            let scale = 1;
            let zIndex = 10;
            let opacity = 1;
            let rotateY = 0;

            if (offset === 0) {
              translateX = 0; scale = 1; zIndex = 10; opacity = 1; rotateY = 0;
            } else if (offset === -1) {
              translateX = -55; scale = 0.85; zIndex = 5; opacity = 0.6; rotateY = 20;
            } else if (offset === 1) {
              translateX = 55; scale = 0.85; zIndex = 5; opacity = 0.6; rotateY = -20;
            } else if (offset === -2) {
              translateX = -95; scale = 0.7; zIndex = 2; opacity = 0.25; rotateY = 30;
            } else if (offset === 2) {
              translateX = 95; scale = 0.7; zIndex = 2; opacity = 0.25; rotateY = -30;
            }

            return (
              <img
                key={xrayImages[index]} // Critical: Key must be bound to the image so React animates it instead of re-mounting
                src={`/Xrays/8842-XJ/${xrayImages[index]}`}
                alt={`Xray slice ${index + 1}`}
                style={{
                  position: 'absolute',
                  height: '95%',
                  maxWidth: '75%',
                  objectFit: 'contain',
                  borderRadius: '24px',
                  boxShadow: offset === 0 ? '0 40px 80px rgba(0,0,0,0.9)' : '0 15px 35px rgba(0,0,0,0.6)',
                  transform: `translateX(${translateX}%) scale(${scale}) rotateY(${rotateY}deg)`,
                  zIndex,
                  opacity,
                  transition: 'transform 0.6s cubic-bezier(0.2, 0.8, 0.2, 1), opacity 0.6s ease, box-shadow 0.6s ease',
                  cursor: offset !== 0 ? 'pointer' : 'default',
                  border: offset === 0 ? '2px solid rgba(255,255,255,0.1)' : '2px solid transparent'
                }}
                onClick={() => {
                  if (offset < 0) handlePrev();
                  if (offset > 0) handleNext();
                }}
              />
            );
          })}
        </div>

        {/* Right Arrow */}
        <button 
          onClick={handleNext}
          className="nav-btn"
          style={{ position: 'absolute', right: '40px', background: 'rgba(255,255,255,0.05)', border: '1px solid rgba(255,255,255,0.1)', borderRadius: '50%', width: '64px', height: '64px', color: 'var(--primary)', cursor: 'pointer', zIndex: 20, display: 'flex', justifyContent: 'center', alignItems: 'center', backdropFilter: 'blur(10px)', transition: 'all 0.1s ease-out' }}
          onMouseOver={(e) => { e.currentTarget.style.background = 'rgba(255,255,255,0.1)'; e.currentTarget.style.transform = 'scale(1.1)'; }}
          onMouseOut={(e) => { e.currentTarget.style.background = 'rgba(255,255,255,0.05)'; e.currentTarget.style.transform = 'scale(1)'; }}
        >
          <svg width="32" height="32" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M9 18l6-6-6-6"/></svg>
        </button>

      </div>
    </div>

  );
}
