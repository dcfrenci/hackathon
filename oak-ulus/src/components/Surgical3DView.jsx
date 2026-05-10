import React, { useRef, useState, useEffect, Suspense } from 'react';
import { Canvas, useFrame, useThree, useLoader } from '@react-three/fiber';
import { OrbitControls, Text, Center, Html } from '@react-three/drei';
import * as THREE from 'three';
import { STLLoader } from 'three/examples/jsm/loaders/STLLoader';
import { useGesture } from '../lib/useGesture.js';

const AnatomicalAxes = () => {
  return (
    <group>
      {/* Evident anatomical axes using cylinders */}
      <mesh rotation={[0, 0, -Math.PI / 2]} position={[5, 0, 0]}><cylinderGeometry args={[0.060, 0.060, 10]} /><meshBasicMaterial color="#ef4444" /></mesh>
      <mesh position={[0, 5, 0]}><cylinderGeometry args={[0.060, 0.060, 10]} /><meshBasicMaterial color="#22c55e" /></mesh>
      <mesh rotation={[Math.PI / 2, 0, 0]} position={[0, 0, 5]}><cylinderGeometry args={[0.060, 0.060, 10]} /><meshBasicMaterial color="#3b82f6" /></mesh>

      <Text position={[10.5, 0, 0]} color="#ef4444" fontSize={1} fontWeight="bold">L/R</Text>
      <Text position={[0, 10.5, 0]} color="#22c55e" fontSize={1} fontWeight="bold">S/I</Text>
      <Text position={[0, 0, 10.5]} color="#3b82f6" fontSize={1} fontWeight="bold">A/P</Text>
    </group>
  );
};

const STLModel = ({ url, onLoaded }) => {
  const geom = useLoader(STLLoader, url);
  const [scale, setScale] = useState(1);

  useEffect(() => {
    if (geom) {
      geom.computeVertexNormals();
      geom.center();

      geom.computeBoundingBox();
      const box = geom.boundingBox;
      const size = new THREE.Vector3();
      box.getSize(size);
      const maxDim = Math.max(size.x, size.y, size.z);

      // Safety check for scale
      if (maxDim > 0) {
        setScale(12 / maxDim); // Target dimension 12 for better visibility
      }
      if (onLoaded) onLoaded();
    }
  }, [geom, url]);

  return (
    <mesh geometry={geom} scale={scale} castShadow receiveShadow>
      <meshStandardMaterial
        color="#a3a3a3"
        metalness={0.1}
        roughness={0.7}
      />
    </mesh>
  );
};

// Per impulso, la rotazione totale integrata = IMPULSE / (1 - DAMPING_60).
// Con DAMPING_60 = 0.82 → impulso 1.8 dà ~10°, impulso 0.9 dà ~5° (parità con la
// versione pre-momentum). Cambiando DAMPING_60 ricalibrare di conseguenza.
const IMPULSE_YAW   = 1.8;
const IMPULSE_PITCH = 0.9;
const DAMPING_60    = 0.82; // riferito a 60 fps; normalizzato per delta sotto

const CameraController = ({ zoomValue, rotXRef, rotYRef, rotVelXRef, rotVelYRef, onAnglesChange }) => {
  const { camera } = useThree();
  const lastPublishRef = useRef(0);
  useFrame((_, delta) => {
    // Damping frame-rate independent: equivalente a DAMPING_60 per frame a 60Hz.
    const k = Math.pow(DAMPING_60, delta * 60);
    rotVelXRef.current *= k;
    rotVelYRef.current *= k;
    rotXRef.current = (rotXRef.current + rotVelXRef.current + 360) % 360;
    rotYRef.current = Math.max(-85, Math.min(85, rotYRef.current + rotVelYRef.current));

    // Throttle del re-render: aggiorna lo stato React ~10 Hz per slider/label.
    lastPublishRef.current += delta;
    if (onAnglesChange && lastPublishRef.current >= 0.1) {
      lastPublishRef.current = 0;
      onAnglesChange(Math.round(rotXRef.current), Math.round(rotYRef.current));
    }

    const zoomRatio = zoomValue / 1000;
    const distance = 50 * Math.pow(1 - zoomRatio, 4) + 0.001;
    camera.fov = 45 - (zoomRatio * 35);
    camera.updateProjectionMatrix();

    const theta = (rotXRef.current * Math.PI) / 180;
    const phi = ((90 - rotYRef.current) * Math.PI) / 180;

    camera.position.set(
      distance * Math.sin(phi) * Math.sin(theta),
      distance * Math.cos(phi),
      distance * Math.sin(phi) * Math.cos(theta),
    );
    camera.lookAt(0, 0, 0);
  });
  return null;
};

const Loader = () => (
  <Html center>
    <div style={{ color: 'var(--primary)', textAlign: 'center', background: 'rgba(0,0,0,0.8)', padding: '20px', borderRadius: '12px', border: '1px solid var(--primary)' }}>
      <div className="spinner" style={{ border: '4px solid rgba(255,255,255,0.1)', borderTop: '4px solid var(--primary)', borderRadius: '50%', width: '40px', height: '40px', animation: 'spin 1s linear infinite', margin: '0 auto 12px' }}></div>
      <div style={{ fontWeight: 'bold', fontSize: '14px' }}>LOADING 3D DATA...</div>
      <div style={{ fontSize: '10px', opacity: 0.7, marginTop: '4px' }}>PROCESSING VOLUMETRIC SCANS</div>
    </div>
    <style>{`
      @keyframes spin { 0% { transform: rotate(0deg); } 100% { transform: rotate(360deg); } }
    `}</style>
  </Html>
);

export default function Surgical3DView({ patientId = '8842-XJ' }) {
  const [activeTool, setActiveTool] = useState('ROTATE');
  const [zoomValue, setZoomValue] = useState(150);
  const [rotX, setRotX] = useState(45);
  const [rotY, setRotY] = useState(30);
  const [stlPath, setStlPath] = useState('');
  const [scanTitle, setScanTitle] = useState('ANATOMICAL MODEL');

  const rotXRef    = useRef(45);
  const rotYRef    = useRef(30);
  const rotVelXRef = useRef(0);
  const rotVelYRef = useRef(0);

  const handleSliderRotX = (v) => {
    rotXRef.current    = v;
    rotVelXRef.current = 0;
    setRotX(v);
  };
  const handleSliderRotY = (v) => {
    rotYRef.current    = v;
    rotVelYRef.current = 0;
    setRotY(v);
  };

  useGesture({
    click: () => setActiveTool((t) => (t === 'ROTATE' ? 'ZOOM' : 'ROTATE')),
    drag_left:  () => { rotVelXRef.current -= IMPULSE_YAW; },
    drag_right: () => { rotVelXRef.current += IMPULSE_YAW; },
    drag_up: () => {
      if (activeTool === 'ZOOM') setZoomValue((v) => Math.min(v + 50, 1000));
      else rotVelYRef.current += IMPULSE_PITCH;
    },
    drag_down: () => {
      if (activeTool === 'ZOOM') setZoomValue((v) => Math.max(v - 50, 0));
      else rotVelYRef.current -= IMPULSE_PITCH;
    },
  }, [activeTool]);

  useEffect(() => {
    // Robust path detection
    const path = window.location.pathname.toLowerCase();
    if (path.includes('cardiac') || path.includes('hearth')) {
      setStlPath('/renders/hearth.stl');
      setScanTitle('CARDIAC 3D');
    } else if (path.includes('lvot') || path.includes('thoracic') || path.includes('aneurysm')) {
      setStlPath('/renders/thoracic_cage.stl');
      setScanTitle('THORACIC CAGE 3D');
    } else if (path.includes('cranial') || path.includes('brain')) {
      setStlPath('/renders/brain.stl');
      setScanTitle('CRANIAL 3D');
    } else {
      setStlPath('/renders/brain.stl');
      setScanTitle('ANATOMICAL MODEL');
    }
  }, []);

  return (
    <div style={{ width: '100%', height: '100%', position: 'relative', display: 'grid', gridTemplateColumns: '200px 1fr', background: '#050507', padding: '24px', gap: '24px', boxSizing: 'border-box' }}>
      {/* Left Sidebar */}
      <div style={{ display: 'flex', flexDirection: 'column', gap: '32px', zIndex: 10 }}>

        {/* Back Button - Outside the container */}
        <div style={{ display: 'flex', justifyContent: 'center', width: '100%' }}>
          <a href={`/patient/${patientId}/`} style={{ display: 'flex', alignItems: 'center', gap: '8px', color: 'var(--primary)', textDecoration: 'none', fontWeight: 'bold', transition: 'opacity 0.2s' }} onMouseOver={(e) => e.currentTarget.style.opacity = '0.7'} onMouseOut={(e) => e.currentTarget.style.opacity = '1'}>
            <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M19 12H5M12 19l-7-7 7-7" /></svg>
            BACK
          </a>
        </div>

        {/* Tools HUD Container */}
        <div style={{ display: 'flex', flexDirection: 'column', justifyContent: 'center', alignItems: 'center', gap: '20px', padding: '24px', background: 'rgba(255,255,255,0.02)', borderRadius: '40px', border: '1px solid rgba(255,255,255,0.05)' }}>
          <button className="hud-btn" style={{ width: '140px', height: '110px', borderRadius: '16px', background: 'rgba(255,255,255,0.04)', border: '1px solid rgba(255,255,255,0.1)', marginBottom: '10px', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
            <svg width="140" height="100" viewBox="4 6 16 15" fill="none" stroke="rgba(255,255,255,0.8)" strokeWidth="4.5"><path d="M4 14l8-8 8 8" /><path d="M4 21l8-8 8 8" /></svg>
          </button>

          {[
            { label: 'ROTATE', icon: 'M21 12a9 9 0 1 1-9-9c2.52 0 4.93 1 6.74 2.74L21 8m0-5v5h-5' },
            { label: 'ZOOM', icon: 'M15 15l6 6m-11-4a7 7 0 1 1 0-14 7 7 0 0 1 0 14zM8 10h4m-2-2v4' }
          ].map((btn) => (
            <div key={btn.label} className={activeTool === btn.label ? 'active-selection-anim' : ''} style={{ borderRadius: '16px' }}>
              <button className={`hud-btn ${activeTool === btn.label ? 'active-primary' : ''}`} onClick={() => setActiveTool(btn.label)} style={{ width: '140px', height: '140px', flexDirection: 'column', padding: '0', justifyContent: 'center', position: 'relative', borderRadius: '16px' }}>
                <svg width="35" height="35" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" style={{ marginBottom: '12px' }}><path d={btn.icon} /></svg>
                <span style={{ fontSize: '13px', fontWeight: '800', letterSpacing: '0.1em' }}>{btn.label}</span>
              </button>
            </div>
          ))}

          <button className="hud-btn" style={{ width: '140px', height: '110px', borderRadius: '16px', background: 'rgba(255,255,255,0.04)', border: '1px solid rgba(255,255,255,0.1)', marginTop: '10px', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
            <svg width="140" height="100" viewBox="4 3 16 15" fill="none" stroke="rgba(255,255,255,0.8)" strokeWidth="4.5"><path d="M4 10l8 8 8-8" /><path d="M4 3l8 8 8-8" /></svg>
          </button>
        </div>
      </div>

      <div style={{ position: 'relative', height: '100%', overflow: 'hidden', borderRadius: '40px', background: '#d1d1d1', boxShadow: '0 20px 50px rgba(0,0,0,0.3)', border: '1px solid rgba(255,255,255,0.1)' }}>

        {/* Overlaid Patient Info Block */}
        <div className="glass-panel" style={{ position: 'absolute', top: '24px', left: '24px', background: 'rgba(15, 15, 17, 0.85)', borderRadius: '16px', border: '1px solid var(--primary)', padding: '16px', zIndex: 100, backdropFilter: 'blur(10px)', minWidth: '220px', pointerEvents: 'none' }}>
          <div className="label-small" style={{ fontSize: '9px', color: '#fff', marginBottom: '8px' }}>PATIENT INFORMATION</div>
          <div style={{ fontSize: '1.1rem', fontWeight: '500', marginBottom: '4px' }}>MARIO ROSSI</div>
          <div className="label-small" style={{ color: 'var(--primary)', marginBottom: '12px', fontSize: '9px' }}>{scanTitle}</div>
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '12px' }}>
            <div><div className="label-small" style={{ fontSize: '8px' }}>PATIENT ID</div><div style={{ fontSize: '0.8rem' }}>#9982-X</div></div>
            <div><div className="label-small" style={{ fontSize: '8px' }}>FILE DATE</div><div style={{ fontSize: '0.8rem' }}>24/05/2024</div></div>
          </div>
        </div>

        <Canvas camera={{ position: [10, 10, 10], fov: 45, near: 0.0001 }} shadows>
          <color attach="background" args={['#d1d1d1']} />

          {/* Medical White Lighting */}
          <ambientLight intensity={0.8} />
          <directionalLight position={[10, 20, 10]} intensity={1.5} castShadow />
          <directionalLight position={[-10, -20, -10]} intensity={0.4} />
          <pointLight position={[0, 0, 15]} intensity={0.6} />

          <gridHelper args={[200, 100, '#e5e5e5', '#f5f5f5']} />
          <AnatomicalAxes />

          <Suspense fallback={<Loader />}>
            {stlPath && <STLModel url={stlPath} />}
          </Suspense>

          <CameraController zoomValue={zoomValue} rotXRef={rotXRef} rotYRef={rotYRef} rotVelXRef={rotVelXRef} rotVelYRef={rotVelYRef} onAnglesChange={(x, y) => { setRotX(x); setRotY(y); }} />
        </Canvas>

        {activeTool === 'ROTATE' && (
          <>
            <div className="glass-panel" style={{ position: 'absolute', bottom: '40px', left: '50%', transform: 'translateX(-50%)', width: '40%', padding: '12px 24px', borderRadius: '20px', border: '1px solid var(--primary)', background: 'rgba(15, 15, 17, 0.95)', zIndex: 100 }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: '10px', color: 'var(--primary)', fontWeight: 'bold' }}><span>YAW</span><span>{rotX}°</span></div>
              <input type="range" min="0" max="360" value={rotX} onChange={(e) => handleSliderRotX(parseInt(e.target.value))} style={{ width: '100%', accentColor: 'var(--primary)' }} />
            </div>
            <div className="glass-panel" style={{ position: 'absolute', right: '40px', top: '50%', transform: 'translateY(-50%)', height: '60%', width: '50px', padding: '20px 10px', borderRadius: '24px', border: '1px solid var(--primary)', background: 'rgba(15, 15, 17, 0.95)', zIndex: 100, display: 'flex', flexDirection: 'column', alignItems: 'center' }}>
              <div style={{ writingMode: 'vertical-rl', transform: 'rotate(180deg)', fontSize: '10px', color: 'var(--primary)', fontWeight: 'bold' }}>PITCH</div>
              <input type="range" min="-85" max="85" value={rotY} onChange={(e) => handleSliderRotY(parseInt(e.target.value))} style={{ appearance: 'slider-vertical', width: '8px', height: '100%', accentColor: 'var(--primary)', cursor: 'pointer' }} />
              <div style={{ fontSize: '10px', fontWeight: 'bold', marginTop: '10px' }}>{rotY}°</div>
            </div>
          </>
        )}

        {activeTool === 'ZOOM' && (
          <div className="glass-panel" style={{ position: 'absolute', bottom: '40px', left: '50%', transform: 'translateX(-50%)', width: '40%', padding: '12px 24px', borderRadius: '20px', border: '1px solid var(--primary)', background: 'rgba(15, 15, 17, 0.95)', zIndex: 100 }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}><span className="label-small" style={{ color: '#fff' }}>EXPLORATION DEPTH</span><span className="data-value" style={{ color: 'var(--primary)', fontWeight: 'bold' }}>{((zoomValue / 1000) * 100).toFixed(1)}%</span></div>
            <input type="range" min="0" max="1000" value={zoomValue} onChange={(e) => setZoomValue(parseInt(e.target.value))} style={{ width: '100%', accentColor: 'var(--primary)' }} />
          </div>
        )}
      </div>
    </div>
  );
}
