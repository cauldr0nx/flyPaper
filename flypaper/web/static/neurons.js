/* The mushroom body, at measured coordinates.
 *
 * Unlike the workstation panel, everything here is data. Each point is a location in
 * MaleCNS v1.0 EM space, 8 nm voxels converted to microns and centred on the mushroom
 * body, exported by `flypaper.web.export`.
 *
 *   calyx    188,984 PN -> KC synapses (subsampled for rendering) - the input side
 *   alpha3     3,579 KC -> MBON-alpha'3 synapses - THE BLOOM FILTER ITSELF
 *   kenyon     2,045 cells, each at the centroid of its own calyx input
 *
 * A tag selects ~5% of the Kenyon cells. When it does, this lights that cell and every
 * alpha'3 synapse it owns, because those are the weights the readout is about to read.
 * Nothing is placed for looks and nothing is animated that is not firing.
 */

import * as THREE from './three.module.js';

const CALYX_COLOUR = new THREE.Color(0xd9a441);
const ALPHA3_COLOUR = new THREE.Color(0x5fd4d6);
const KC_COLOUR = new THREE.Color(0x6f7d8c);
const FIRE = new THREE.Color(0xffffff);

function pointsFrom(list, colour, size, opacity) {
  const positions = new Float32Array(list.length * 3);
  const colours = new Float32Array(list.length * 3);
  for (let i = 0; i < list.length; i += 1) {
    positions[i * 3] = list[i][0];
    positions[i * 3 + 1] = list[i][1];
    positions[i * 3 + 2] = list[i][2];
    colours[i * 3] = colour.r;
    colours[i * 3 + 1] = colour.g;
    colours[i * 3 + 2] = colour.b;
  }
  const geometry = new THREE.BufferGeometry();
  geometry.setAttribute('position', new THREE.BufferAttribute(positions, 3));
  geometry.setAttribute('color', new THREE.BufferAttribute(colours, 3));
  const material = new THREE.PointsMaterial({
    size,
    vertexColors: true,
    transparent: true,
    opacity,
    depthWrite: false,
    blending: THREE.AdditiveBlending,
    sizeAttenuation: true,
  });
  return new THREE.Points(geometry, material);
}

export async function initCloud(canvas, statsEl) {
  const anatomy = await fetch('anatomy.json').then((r) => r.json());

  const renderer = new THREE.WebGLRenderer({ canvas, antialias: true, alpha: true });
  renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
  const scene = new THREE.Scene();
  const camera = new THREE.PerspectiveCamera(42, 1, 0.1, 4000);

  const root = new THREE.Group();
  scene.add(root);

  const calyx = pointsFrom(anatomy.calyx, CALYX_COLOUR, 0.9, 0.30);
  const alpha3 = pointsFrom(anatomy.alpha3, ALPHA3_COLOUR, 1.5, 0.62);
  const kenyon = pointsFrom(anatomy.kenyon, KC_COLOUR, 1.3, 0.42);
  root.add(calyx, alpha3, kenyon);

  // Which alpha'3 synapses belong to which Kenyon cell, so a firing cell lights exactly
  // the weights it owns.
  const synapsesOfKc = new Map();
  anatomy.alpha3_kc.forEach((kcIndex, synapseIndex) => {
    let list = synapsesOfKc.get(kcIndex);
    if (!list) { list = []; synapsesOfKc.set(kcIndex, list); }
    list.push(synapseIndex);
  });

  const kcColours = kenyon.geometry.attributes.color;
  const a3Colours = alpha3.geometry.attributes.color;
  const kcHeat = new Float32Array(anatomy.kenyon.length);
  const a3Heat = new Float32Array(anatomy.alpha3.length);

  // Centre the camera on the whole structure.
  const bounds = new THREE.Box3();
  for (const cloud of [calyx, alpha3, kenyon]) {
    cloud.geometry.computeBoundingBox();
    bounds.union(cloud.geometry.boundingBox);
  }
  const target = bounds.getCenter(new THREE.Vector3());
  const radius = Math.max(bounds.getSize(new THREE.Vector3()).length() * 0.5, 1);

  // The calyx sits dorsal and the alpha'3 lobe ventral, so the pair is separated mostly in
  // Y. No choice of yaw makes that fit a wide short panel; the camera has to be fitted to
  // the bounding box for the panel's actual aspect, and refitted when it changes.
  const size = bounds.getSize(new THREE.Vector3());
  let yaw = 0.95;
  let pitch = 0.12;
  let distance = radius * 2.2;
  let userZoom = 1;

  function fitDistance(aspect) {
    const vfov = (camera.fov * Math.PI) / 180;
    const forHeight = (size.y * 0.5) / Math.tan(vfov / 2);
    const forWidth = (Math.max(size.x, size.z) * 0.5) / (Math.tan(vfov / 2) * Math.max(aspect, 0.1));
    return Math.max(forHeight, forWidth) * 1.22;
  }
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
    yaw -= (e.clientX - lastX) * 0.007;
    pitch = Math.max(-1.3, Math.min(1.3, pitch + (e.clientY - lastY) * 0.006));
    lastX = e.clientX; lastY = e.clientY;
  });
  canvas.addEventListener('wheel', (e) => {
    e.preventDefault();
    userZoom = Math.max(0.35, Math.min(3.2, userZoom * (1 + Math.sign(e.deltaY) * 0.09)));
  }, { passive: false });

  let spin = true;
  canvas.addEventListener('pointerdown', () => { spin = false; });

  if (statsEl) {
    const c = anatomy.counts;
    statsEl.innerHTML =
      `<div>${c.kenyon_cells.toLocaleString()} Kenyon cells</div>` +
      `<div>${c.alpha3_synapses.toLocaleString()} &alpha;&prime;3 synapses</div>` +
      `<div>${c.calyx_synapses.toLocaleString()} calyx synapses</div>` +
      `<div class="dim">${c.glomeruli} glomeruli &middot; ${c.projection_neurons} PNs</div>`;
  }

  function fire(activeKc) {
    for (const kcIndex of activeKc) {
      kcHeat[kcIndex] = 1;
      const owned = synapsesOfKc.get(kcIndex);
      if (owned) for (const s of owned) a3Heat[s] = 1;
    }
  }

  function setMode(mode) {
    calyx.visible = mode === 'both' || mode === 'calyx';
    alpha3.visible = mode === 'both' || mode === 'alpha3';
    kenyon.visible = mode === 'both' || mode === 'kc' || mode === 'calyx';
  }

  function resize() {
    const w = canvas.clientWidth || 1;
    const h = canvas.clientHeight || 1;
    if (canvas.width !== w || canvas.height !== h) renderer.setSize(w, h, false);
    camera.aspect = w / h;
    camera.updateProjectionMatrix();
    distance = fitDistance(camera.aspect) * userZoom;
  }

  const clock = new THREE.Clock();
  const tmp = new THREE.Color();

  function applyHeat(heat, colours, base, decay) {
    let dirty = false;
    for (let i = 0; i < heat.length; i += 1) {
      if (heat[i] <= 0) continue;
      heat[i] = Math.max(0, heat[i] - decay);
      tmp.copy(base).lerp(FIRE, heat[i]);
      colours.setXYZ(i, tmp.r, tmp.g, tmp.b);
      dirty = true;
    }
    if (dirty) colours.needsUpdate = true;
  }

  function frame() {
    const dt = Math.min(clock.getDelta(), 0.1);
    resize();
    if (spin) yaw += dt * 0.06;

    applyHeat(kcHeat, kcColours, KC_COLOUR, dt * 1.8);
    applyHeat(a3Heat, a3Colours, ALPHA3_COLOUR, dt * 1.5);

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

  return { fire, setMode, counts: anatomy.counts, stats: anatomy.stats };
}
