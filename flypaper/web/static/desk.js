/* The workstation: a fly at a computer, with the run's log on the monitor.
 *
 * The body is NeuroMechFly v2 (NeLy-EPFL/flygym, Apache-2.0; Lobato-Rios et al., Nature
 * Methods 2024) - a real Drosophila reconstructed from microscopy, the same asset flyDash
 * uses.
 *
 * Everything in this panel is STAGING. The desk, the monitor and the fly's pose are not
 * computed from anything: no part of flypaper models a body, and the model is not
 * spatially registered to the connectome coordinates. The one honest thing on screen is
 * the text on the monitor, which is the log the recorded run actually produced.
 *
 * The measured data lives in the mushroom body panel, which is deliberately a separate
 * view for exactly that reason.
 */

import * as THREE from './three.module.js';

const COLOURS = {
  body: 0x7a6038,
  eye: 0xd1443a,
  wing_l: 0x93a8bd,
  wing_r: 0x93a8bd,
  leg: 0x4a3c26,
  leg_mid_l: 0x6b5734,
  leg_mid_r: 0x6b5734,
  antenna: 0x8a6f42,
};

const SCREEN_W = 960;
const SCREEN_H = 600;
const LOG_ROWS = 15;

function screenCanvas() {
  const canvas = document.createElement('canvas');
  canvas.width = SCREEN_W;
  canvas.height = SCREEN_H;
  return canvas;
}

export async function initDesk(canvas) {
  const renderer = new THREE.WebGLRenderer({ canvas, antialias: true, alpha: true });
  renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));

  const scene = new THREE.Scene();
  scene.fog = new THREE.FogExp2(0x05070a, 0.055);
  const camera = new THREE.PerspectiveCamera(40, 1, 0.05, 200);

  scene.add(new THREE.AmbientLight(0x8093a8, 0.55));
  const key = new THREE.DirectionalLight(0xffe9c8, 1.35);
  key.position.set(3, 5, 4);
  scene.add(key);
  const rim = new THREE.DirectionalLight(0x5fd4d6, 0.55);
  rim.position.set(-4, 2, -3);
  scene.add(rim);
  // The monitor is the practical light in the scene, so it also lights the animal.
  const glow = new THREE.PointLight(0x9fd8ff, 1.5, 14, 2);
  glow.position.set(0.45, 2.6, -0.9);
  scene.add(glow);

  const root = new THREE.Group();
  scene.add(root);

  /* ── desk ─────────────────────────────────────────────────────────── */

  const deskTop = new THREE.Mesh(
    new THREE.BoxGeometry(8.6, 0.18, 4.4),
    new THREE.MeshStandardMaterial({ color: 0x0b0f15, roughness: 0.9, metalness: 0.06 }),
  );
  deskTop.position.set(0, 0, 0);
  root.add(deskTop);

  const grid = new THREE.GridHelper(44, 44, 0x16212b, 0x0d151c);
  grid.position.y = -1.9;
  root.add(grid);

  /* ── monitor ──────────────────────────────────────────────────────── */

  const bezel = new THREE.Mesh(
    new THREE.BoxGeometry(7.95, 5.1, 0.22),
    new THREE.MeshStandardMaterial({ color: 0x0a0e13, roughness: 0.6, metalness: 0.35 }),
  );
  bezel.position.set(0.45, 2.95, -1.15);
  root.add(bezel);

  const logCanvas = screenCanvas();
  const logCtx = logCanvas.getContext('2d');
  if (typeof window !== 'undefined') window.__logCanvas = logCanvas;  // for headless inspection
  const screenTexture = new THREE.CanvasTexture(logCanvas);
  screenTexture.colorSpace = THREE.SRGBColorSpace;
  screenTexture.minFilter = THREE.LinearFilter;

  const screen = new THREE.Mesh(
    new THREE.PlaneGeometry(7.55, 4.72),
    new THREE.MeshBasicMaterial({ map: screenTexture }),
  );
  screen.position.set(0.45, 2.95, -1.03);
  root.add(screen);

  const stand = new THREE.Mesh(
    new THREE.CylinderGeometry(0.17, 0.32, 1.5, 16),
    new THREE.MeshStandardMaterial({ color: 0x0d1218, roughness: 0.7, metalness: 0.4 }),
  );
  stand.position.set(0.45, 0.8, -1.62);
  root.add(stand);

  const foot = new THREE.Mesh(
    new THREE.BoxGeometry(2.0, 0.1, 0.9),
    new THREE.MeshStandardMaterial({ color: 0x0d1218, roughness: 0.7, metalness: 0.4 }),
  );
  foot.position.set(0.45, 0.14, -1.62);
  root.add(foot);

  /* ── keyboard ─────────────────────────────────────────────────────── */

  const keyboard = new THREE.Group();
  const kbBase = new THREE.Mesh(
    new THREE.BoxGeometry(2.9, 0.1, 1.05),
    new THREE.MeshStandardMaterial({ color: 0x121820, roughness: 0.8 }),
  );
  keyboard.add(kbBase);

  const keys = [];
  const keyGeom = new THREE.BoxGeometry(0.15, 0.07, 0.16);
  for (let row = 0; row < 4; row += 1) {
    for (let col = 0; col < 16; col += 1) {
      const keyMat = new THREE.MeshStandardMaterial({ color: 0x1d2732, roughness: 0.75 });
      const k = new THREE.Mesh(keyGeom, keyMat);
      k.position.set(-1.28 + col * 0.172, 0.085, -0.31 + row * 0.2);
      keyboard.add(k);
      keys.push(k);
    }
  }
  keyboard.position.set(-0.7, 0.15, 0.72);
  root.add(keyboard);

  /* ── the animal ───────────────────────────────────────────────────── */

  const [manifest, buffer] = await Promise.all([
    fetch('fly.json').then((r) => r.json()),
    fetch('fly.bin').then((r) => r.arrayBuffer()),
  ]);
  const quantised = new Int16Array(buffer);
  const scale = manifest.quantisation.scale;

  const flyGroup = new THREE.Group();
  const box = new THREE.Box3();
  const animated = {};
  for (const part of manifest.groups) {
    const positions = new Float32Array(part.count * 3);
    for (let i = 0; i < part.count * 3; i += 1) {
      positions[i] = quantised[part.offset * 3 + i] * scale;
    }
    const geometry = new THREE.BufferGeometry();
    geometry.setAttribute('position', new THREE.BufferAttribute(positions, 3));
    geometry.computeVertexNormals();
    const material = new THREE.MeshStandardMaterial({
      color: COLOURS[part.name] ?? COLOURS.body,
      roughness: part.name === 'eye' ? 0.25 : 0.75,
      metalness: part.name === 'eye' ? 0.35 : 0.05,
      transparent: part.name.startsWith('wing'),
      opacity: part.name.startsWith('wing') ? 0.2 : 1,
      side: THREE.DoubleSide,
    });
    const mesh = new THREE.Mesh(geometry, material);
    if (part.pivot) {
      const [px, py, pz] = part.pivot;
      geometry.translate(-px, -py, -pz);
      mesh.position.set(px, py, pz);
      animated[part.name] = mesh;
    }
    flyGroup.add(mesh);
    box.expandByObject(mesh);
  }
  const centre = box.getCenter(new THREE.Vector3());
  flyGroup.position.sub(centre);

  // The model's up axis is +Z; three.js is +Y up. Then scale it onto the desk and turn it
  // to face the monitor.
  const extent = box.getSize(new THREE.Vector3()).length();
  const flyPivot = new THREE.Group();
  flyPivot.rotation.x = -Math.PI / 2;
  flyPivot.add(flyGroup);

  const flyRoot = new THREE.Group();
  const fit = 3.3 / extent;
  flyRoot.scale.setScalar(fit);
  flyRoot.add(flyPivot);
  flyRoot.position.set(-1.05, 0.36, 1.5);
  flyRoot.rotation.y = Math.PI - 0.30;   // three-quarter to the screen
  root.add(flyRoot);

  const eyeGlow = new THREE.PointLight(0xff6a4a, 0.0, 4, 2);
  eyeGlow.position.set(-1.0, 0.8, 1.25);
  root.add(eyeGlow);

  /* ── camera rig ───────────────────────────────────────────────────── */

  let yaw = 0.30, pitch = 0.17, distance = 10.4;
  const target = new THREE.Vector3(0.1, 2.25, 0.1);
  let dragging = false, lastX = 0, lastY = 0;

  canvas.addEventListener('pointerdown', (e) => {
    dragging = true; lastX = e.clientX; lastY = e.clientY;
    canvas.setPointerCapture(e.pointerId);
  });
  canvas.addEventListener('pointerup', (e) => {
    dragging = false;
    try { canvas.releasePointerCapture(e.pointerId); } catch { /* already gone */ }
  });
  canvas.addEventListener('pointermove', (e) => {
    if (!dragging) return;
    yaw -= (e.clientX - lastX) * 0.006;
    pitch = Math.max(-0.25, Math.min(1.1, pitch + (e.clientY - lastY) * 0.005));
    lastX = e.clientX; lastY = e.clientY;
  });
  canvas.addEventListener('wheel', (e) => {
    e.preventDefault();
    distance = Math.max(4.5, Math.min(22, distance * (1 + Math.sign(e.deltaY) * 0.09)));
  }, { passive: false });

  /* ── monitor content ──────────────────────────────────────────────── */

  let lines = [];
  let header = 'flypaper';

  function paintScreen(state) {
    const g = logCtx;
    g.fillStyle = '#05080c';
    g.fillRect(0, 0, SCREEN_W, SCREEN_H);

    // scanlines, so it reads as a screen rather than a poster
    g.fillStyle = 'rgba(255,255,255,0.018)';
    for (let y = 0; y < SCREEN_H; y += 3) g.fillRect(0, y, SCREEN_W, 1);

    g.strokeStyle = '#16212b';
    g.lineWidth = 2;
    g.strokeRect(18, 18, SCREEN_W - 36, SCREEN_H - 36);

    g.font = '600 21px ui-monospace, Menlo, Consolas, monospace';
    g.fillStyle = '#5d6a78';
    g.fillText(header, 38, 52);
    g.fillStyle = state && state.shown ? '#ffd98a' : '#3a4550';
    g.fillText(state && state.shown ? '● SURFACED' : '● scanning', SCREEN_W - 200, 52);

    g.strokeStyle = '#101820';
    g.beginPath(); g.moveTo(38, 68); g.lineTo(SCREEN_W - 38, 68); g.stroke();

    g.font = '22px ui-monospace, Menlo, Consolas, monospace';
    const visible = lines.slice(-LOG_ROWS);
    visible.forEach((line, i) => {
      const y = 104 + i * 28;
      const last = i === visible.length - 1;
      if (line.hit) g.fillStyle = last ? '#ff9b7d' : '#8a4a3a';
      else if (line.shown) g.fillStyle = last ? '#ffd98a' : '#8a7340';
      else g.fillStyle = last ? '#9fb0c0' : '#2f3a45';
      g.fillText(line.text.slice(0, 58), 38, y);
    });

    if (state) {
      g.font = '19px ui-monospace, Menlo, Consolas, monospace';
      g.fillStyle = '#42505e';
      g.fillText(
        `novelty ${state.novelty.toFixed(3)}   ${state.index}/${state.total}   ` +
        `saturation ${(state.saturation * 100).toFixed(1)}%`,
        38, SCREEN_H - 34,
      );
    }
    screenTexture.needsUpdate = true;
  }

  /* ── animation ────────────────────────────────────────────────────── */

  let typing = 0;      // decays after each response; drives the keys and the legs
  let flash = 0;       // spikes when a response is surfaced

  function pushLine(line) {
    lines.push(line);
    if (lines.length > 200) lines = lines.slice(-120);
    typing = 1;
  }

  function surface() { flash = 1; }

  let lastState = null;
  function setState(state) { lastState = state; }
  function setHeader(text) { header = text; }

  function resize() {
    const w = canvas.clientWidth || 1;
    const h = canvas.clientHeight || 1;
    if (canvas.width !== w || canvas.height !== h) renderer.setSize(w, h, false);
    camera.aspect = w / h;
    camera.updateProjectionMatrix();
  }

  const clock = new THREE.Clock();
  function frame() {
    const dt = Math.min(clock.getDelta(), 0.1);
    resize();

    typing = Math.max(0, typing - dt * 2.2);
    flash = Math.max(0, flash - dt * 1.1);

    // Keys ripple while the stream is moving. Decoration, and the fly is not typing:
    // flypaper reads a stream someone else produced.
    const t = clock.getElapsedTime();
    keys.forEach((k, i) => {
      const press = typing * Math.max(0, Math.sin(t * 17 + i * 1.7)) * 0.05;
      k.position.y = 0.085 - press;
      k.material.color.setHex(press > 0.02 ? 0x2b4250 : 0x1d2732);
    });

    // Forelegs rest on the keyboard and bob with the stream; wings settle.
    const bob = typing * 0.07;
    if (animated.leg_mid_l) animated.leg_mid_l.rotation.z = -bob;
    if (animated.leg_mid_r) animated.leg_mid_r.rotation.z = bob;
    if (animated.wing_l) animated.wing_l.rotation.y = -0.16 + flash * 0.5;
    if (animated.wing_r) animated.wing_r.rotation.y = 0.16 - flash * 0.5;

    flyRoot.position.y = 0.34 + Math.sin(t * 1.6) * 0.012 + flash * 0.06;
    eyeGlow.intensity = flash * 2.2;
    glow.intensity = 1.2 + (lastState ? lastState.novelty * 1.6 : 0) + flash * 1.4;
    glow.color.setHex(flash > 0.25 ? 0xffd98a : 0x9fd8ff);

    paintScreen(lastState);

    camera.position.set(
      target.x + Math.sin(yaw) * Math.cos(pitch) * distance,
      target.y + Math.sin(pitch) * distance,
      target.z + Math.cos(yaw) * Math.cos(pitch) * distance,
    );
    camera.lookAt(target);
    renderer.render(scene, camera);
    requestAnimationFrame(frame);
  }
  frame();

  return { pushLine, surface, setState, setHeader, reset: () => { lines = []; } };
}
