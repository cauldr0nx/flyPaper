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
 * Two things move, and both are read off the recorded run rather than animated on a timer:
 *
 *   BRIGHTER  a tag selects ~5% of the Kenyon cells. When it does, this flashes those cells
 *             and every alpha'3 synapse they own, because those are the weights the readout
 *             is about to read. How hard it flashes is the response's own novelty score, so
 *             routine traffic is a murmur and an outlier is a strike. And when the run
 *             surfaces a response - the same decision, on the same frame, that makes the fly
 *             startle - the cells that response lit hold an afterglow on the same envelope
 *             as the startle, so the two panels visibly react to one event.
 *
 *   DARKER    firing *depresses* a synapse, and a depressed synapse stays depressed. Each
 *             point's resting brightness is its own weight in the filter, straight out of
 *             the run (`frame.wq`). So the cloud goes dark exactly where the scan has
 *             learned a baseline, and the parts of the circuit nothing has landed on stay
 *             lit. That darkening *is* the saturation trace, drawn in place.
 *
 * Nothing is placed for looks and nothing is animated that is not firing.
 *
 * The calyx cloud is the exception and is deliberately inert: `anatomy.json` subsamples it
 * for rendering and does not carry which Kenyon cell each site contacts, so there is no
 * honest way to light it. It is context, not state.
 */

import * as THREE from './three.module.js';

const CALYX_COLOUR = new THREE.Color(0xd9a441);
// Deeper than the cyan the rest of the page uses for the readout. At the size these points
// draw, #5fd4d6 is already most of the way to white, and a cloud that starts at white has
// nowhere to go when a synapse fires.
const ALPHA3_COLOUR = new THREE.Color(0x2f9fa4);
const KC_COLOUR = new THREE.Color(0x62707e);
const FIRE = new THREE.Color(0xffffff);

/* How faint a routine response is allowed to be.
 *
 * The flash is scaled by novelty, and on a settled scan almost every response scores a
 * flat zero - which is the true answer and also an unwatchable one, because the stream
 * would stop moving. The floor keeps ordinary traffic visible as movement while leaving
 * three quarters of the range for responses that are actually unlike the rest.
 */
const FLASH_FLOOR = 0.26;

//: Afterglow decay for a surfaced response, per second. Deliberately the same number as the
//: startle decay in desk.js: one event, one envelope, two panels.
const SURGE_DECAY = 0.55;

// Where a fully depressed point sits. Not black: a synapse the run has learned is still
// there, and a cloud that deleted its own familiar half would misread as missing data.
const ALPHA3_DEPRESSED = new THREE.Color(0x0a2226);
const KC_DEPRESSED = new THREE.Color(0x171d23);

/* A soft round dot, drawn once into a canvas and reused by every cloud.
 *
 * A point sprite is a square by default, which at the zoom the alpha'3 view reaches turns
 * the lobe into a mosaic of tiles - and a tile is exactly the wrong shape for something
 * meant to be read as one synapse. Generated here rather than shipped as an image: the page
 * has to work with no network, and this is eight lines. */
function discTexture() {
  const canvas = document.createElement('canvas');
  canvas.width = canvas.height = 64;
  const g = canvas.getContext('2d');
  const gradient = g.createRadialGradient(32, 32, 0, 32, 32, 32);
  gradient.addColorStop(0, 'rgba(255,255,255,1)');
  gradient.addColorStop(0.45, 'rgba(255,255,255,0.92)');
  gradient.addColorStop(1, 'rgba(255,255,255,0)');
  g.fillStyle = gradient;
  g.fillRect(0, 0, 64, 64);
  const texture = new THREE.CanvasTexture(canvas);
  texture.colorSpace = THREE.SRGBColorSpace;
  return texture;
}

const DISC = discTexture();

function pointsFrom(list, colour, size, opacity, blending = THREE.AdditiveBlending) {
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
    map: DISC,
    vertexColors: true,
    transparent: true,
    opacity,
    depthWrite: false,
    blending,
    sizeAttenuation: true,
  });
  return new THREE.Points(geometry, material);
}

/* A second, larger, additive cloud over the same points, black everywhere except where
 * something is firing.
 *
 * The flash has to be bigger than the point, not just brighter than it: on this run a
 * response lands on about 6% of the alpha'3 lobe, and six percent of a dense cloud changing
 * shade is not something an eye finds. Black contributes nothing under additive blending,
 * so this layer is invisible until a synapse fires and then puts a halo exactly on it - one
 * cloud, no second set of coordinates, nothing drawn anywhere the run did not put it.
 */
function glowOver(points, size) {
  const geometry = new THREE.BufferGeometry();
  geometry.setAttribute('position', points.geometry.attributes.position);
  const count = points.geometry.attributes.position.count;
  geometry.setAttribute('color', new THREE.BufferAttribute(new Float32Array(count * 3), 3));
  const glow = new THREE.Points(geometry, new THREE.PointsMaterial({
    size,
    map: DISC,
    vertexColors: true,
    transparent: true,
    opacity: 0.75,
    depthWrite: false,
    depthTest: false,
    blending: THREE.AdditiveBlending,
    sizeAttenuation: true,
  }));
  glow.renderOrder = 2;   // after the cloud it sits on, or the cloud paints over it
  return glow;
}

export async function initCloud(canvas, statsEl) {
  const anatomy = await fetch('anatomy.json').then((r) => r.json());

  const renderer = new THREE.WebGLRenderer({ canvas, antialias: true, alpha: true });
  renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
  const scene = new THREE.Scene();
  const camera = new THREE.PerspectiveCamera(42, 1, 0.1, 4000);

  const root = new THREE.Group();
  scene.add(root);

  // The calyx is context and is drawn faintly on purpose: it is the only cloud here that
  // cannot respond, and at equal weight its 12,000 points bury the 3,579 that are the
  // filter. The alpha'3 cloud is the subject and is drawn as such.
  const calyx = pointsFrom(anatomy.calyx, CALYX_COLOUR, 0.9, 0.17);
  // The alpha'3 lobe and the cells are drawn with ordinary blending, not additive. Additive
  // is right for a faint cloud read as density, and wrong for these two: the lobe is dense
  // enough that the overlaps saturate to white, and a cloud already at white cannot get
  // brighter when a synapse fires or darker when one is depressed - which is the entire
  // thing this panel is for.
  const alpha3 = pointsFrom(anatomy.alpha3, ALPHA3_COLOUR, 2.0, 0.95, THREE.NormalBlending);
  const kenyon = pointsFrom(anatomy.kenyon, KC_COLOUR, 1.6, 0.9, THREE.NormalBlending);
  alpha3.renderOrder = 1;
  kenyon.renderOrder = 1;
  const A3_GLOW_SIZE = 3.4;
  const KC_GLOW_SIZE = 2.8;
  const alpha3Glow = glowOver(alpha3, A3_GLOW_SIZE);
  const kenyonGlow = glowOver(kenyon, KC_GLOW_SIZE);
  root.add(calyx, alpha3, kenyon, alpha3Glow, kenyonGlow);

  // Which alpha'3 synapses belong to which Kenyon cell, so a firing cell lights exactly
  // the weights it owns, and a depressed cell dims exactly those.
  const owner = anatomy.alpha3_kc;
  const synapsesOfKc = new Map();
  owner.forEach((kcIndex, synapseIndex) => {
    let list = synapsesOfKc.get(kcIndex);
    if (!list) { list = []; synapsesOfKc.set(kcIndex, list); }
    list.push(synapseIndex);
  });

  const kcColours = kenyon.geometry.attributes.color;
  const a3Colours = alpha3.geometry.attributes.color;
  const kcGlowColours = kenyonGlow.geometry.attributes.color;
  const a3GlowColours = alpha3Glow.geometry.attributes.color;
  const kcHeat = new Float32Array(anatomy.kenyon.length);
  const a3Heat = new Float32Array(anatomy.alpha3.length);
  // One synaptic weight per Kenyon cell, exactly as the filter holds it. Rest is 1.
  const weight = new Float32Array(anatomy.kenyon.length).fill(1);

  /* The surfaced response, held so it can be seen.
   *
   * `mark` is the set of points the surfaced response actually lit - its own tag, not a
   * global flare over synapses it never touched. `surge` is how much of the afterglow is
   * left. Kept as lists as well as flags so the decay only has to repaint what is glowing.
   */
  const kcMark = new Float32Array(anatomy.kenyon.length);
  const a3Mark = new Float32Array(anatomy.alpha3.length);
  let markedCells = [];
  let markedSynapses = [];
  let surge = 0;
  let surgeAge = 0;
  let heldSurge = null;
  //: Envelope of recent novelty, which the halo size follows. Decays faster than the surge:
  //: it is about the response just read, not about the last thing worth stopping for.
  let bloom = 0;

  /* Where the camera looks, and how much room it has to leave.
   *
   * The calyx and the alpha'3 lobe are 100 um apart, so a view holding both spends most of
   * the panel on the gap between them and leaves the lobe - the filter, the part that
   * lights - a thumbnail in the corner. The mode selector therefore chooses the framing as
   * well as what is drawn: pick the alpha'3 and the camera goes to the alpha'3. Nothing
   * moves, the camera does.
   */
  for (const cloud of [calyx, alpha3, kenyon]) cloud.geometry.computeBoundingBox();

  function boxOf(clouds) {
    const box = new THREE.Box3();
    for (const cloud of clouds) box.union(cloud.geometry.boundingBox);
    return box;
  }

  const FRAMING = {
    both: [calyx, alpha3, kenyon],
    calyx: [calyx, kenyon],
    alpha3: [alpha3],
    kc: [kenyon],
  };

  const target = new THREE.Vector3();
  //: The corners of the boxes being framed, as offsets from the target. Fitting to these
  //: rather than to one big box matters because the two structures are far apart: the union
  //: box's own corners are empty space, and framing them throws away a third of the panel.
  let corners = [];

  function frameOn(mode) {
    const clouds = FRAMING[mode] || FRAMING.both;
    const box = boxOf(clouds);
    box.getCenter(target);
    corners = [];
    for (const cloud of clouds) {
      const { min, max } = cloud.geometry.boundingBox;
      for (const x of [min.x, max.x]) {
        for (const y of [min.y, max.y]) {
          for (const z of [min.z, max.z]) {
            corners.push([x - target.x, y - target.y, z - target.z]);
          }
        }
      }
    }
  }
  frameOn('both');
  const radius = Math.max(boxOf(FRAMING.both).getSize(new THREE.Vector3()).length() * 0.5, 1);

  let yaw = 0.95;
  let pitch = 0.12;
  let distance = radius * 2.2;
  let userZoom = 1;

  /* How far back to stand, recomputed from the actual orientation every frame.
   *
   * Side on, the 100 um between the calyx and the lobe is the widest thing on screen; end
   * on the two overlap and it costs nothing. And because this is a perspective camera, how
   * much room a structure needs depends on how far away it is, not only on how big it is -
   * the lobe sits well behind the calyx from most angles and takes up correspondingly less.
   * So rather than guess a bounding radius, solve it: for a corner at offset `o` from the
   * target, the camera at distance `d` sees it `o·forward + d` away and `o·up` off the
   * axis, which fits when d >= |o·up| / tan(fov/2) - o·forward. Take the largest, and the
   * frame is exactly as tight as it can be without cutting anything off.
   */
  function fitDistance(aspect) {
    const sy = Math.sin(yaw), cy = Math.cos(yaw);
    const sp = Math.sin(pitch), cp = Math.cos(pitch);
    const rx = cy, rz = -sy;                               // camera right
    const ux = -sy * sp, uy = cp, uz = -cy * sp;           // camera up
    const fx = -sy * cp, fy = -sp, fz = -cy * cp;          // camera forward
    const tan = Math.tan((camera.fov * Math.PI) / 180 / 2);
    const tanWide = tan * Math.max(aspect, 0.1);
    let needed = 1;
    for (const [ox, oy, oz] of corners) {
      const along = ox * fx + oy * fy + oz * fz;
      const up = Math.abs(ox * ux + oy * uy + oz * uz);
      const right = Math.abs(ox * rx + oz * rz);
      needed = Math.max(needed, Math.max(up / tan, right / tanWide) - along);
    }
    // A point is drawn as a disc and a firing one carries a halo, so leave a little more
    // room than the geometry alone asks for.
    return needed * 1.1;
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

  /* ── colour ───────────────────────────────────────────────────────────
   *
   * A point's colour is two things multiplied into one: where its weight sits between
   * depressed and rested, and how recently it fired. Both are the run's, neither is a
   * timer.
   */

  const tmp = new THREE.Color();

  /* The afterglow's shape, not just its size.
   *
   * desk.js shapes the startle as a damped oscillator - the jolt, then the settle - because
   * a plain fade is much harder to read as one event. This is the same envelope at the same
   * frequency and damping, so the light in this panel throbs on the beat the body does.
   */
  function surgeGlow() {
    if (surge <= 0) return 0;
    const throb = 0.74 + 0.26 * Math.exp(-surgeAge * 4.5) * Math.cos(surgeAge * 32);
    return Math.max(0, Math.min(1, surge * throb));
  }

  let glow = 0;   // recomputed once a frame rather than per point

  /* The halo grows with the response as well as brightening, because at this zoom a few
   * hundred points changing shade is easy to miss and a few hundred points swelling is not.
   * It follows the same two numbers as the colour: recent novelty, and the surfaced
   * afterglow. Called from `fire` as well as from the render loop, so the swell lands on the
   * same frame as the flash rather than the one after it. */
  function sizeHalos() {
    const swell = Math.max(bloom, glow);
    alpha3Glow.material.size = A3_GLOW_SIZE * (1 + 0.85 * swell);
    kenyonGlow.material.size = KC_GLOW_SIZE * (1 + 0.75 * swell);
  }

  function paintKc(i) {
    const lit = Math.max(kcHeat[i], kcMark[i] * glow);
    tmp.copy(KC_DEPRESSED).lerp(KC_COLOUR, weight[i]);
    if (lit > 0) tmp.lerp(FIRE, lit);
    kcColours.setXYZ(i, tmp.r, tmp.g, tmp.b);
    // Squared, so the halo is the part of the signal that separates a strike from the
    // murmur: routine traffic barely raises one at all and an outlier carries a real one.
    // Linear here washed the whole lobe white, which loses the shape and the one thing the
    // halo is for - saying *which* synapses.
    const kcHalo = lit * lit * 0.62;
    kcGlowColours.setXYZ(i, kcHalo * 0.82, kcHalo * 0.9, kcHalo);
  }

  function paintA3(s) {
    const lit = Math.max(a3Heat[s], a3Mark[s] * glow);
    tmp.copy(ALPHA3_DEPRESSED).lerp(ALPHA3_COLOUR, weight[owner[s]]);
    if (lit > 0) tmp.lerp(FIRE, lit);
    a3Colours.setXYZ(s, tmp.r, tmp.g, tmp.b);
    const a3Halo = lit * lit * 0.7;
    a3GlowColours.setXYZ(s, a3Halo * 0.86, a3Halo * 0.96, a3Halo);
  }

  function touched() {
    kcColours.needsUpdate = true;
    a3Colours.needsUpdate = true;
    kcGlowColours.needsUpdate = true;
    a3GlowColours.needsUpdate = true;
  }

  function repaint() {
    for (let i = 0; i < weight.length; i += 1) paintKc(i);
    for (let s = 0; s < owner.length; s += 1) paintA3(s);
    touched();
  }

  /** Take the filter's new weights for these cells, without flashing them.
   *
   * Used to catch the filter up over responses the viewer is not watching - a scrub, or a
   * restart - where the learning happened but the flash did not. */
  function observe(activeKc, weights) {
    if (!weights) return;
    for (let i = 0; i < activeKc.length; i += 1) weight[activeKc[i]] = weights[i];
  }

  /** One response was read.
   *
   * Flash the cells its tag selected and take their new weights. `novelty` is that
   * response's score on the run's own scale (app.js divides by the most novel response the
   * run produced, because an absolute scale puts 99% of a settled scan at a flat zero), and
   * it sets how hard the flash lands. `surfaced` is the run's own decision that this one
   * cleared the cutoff - the same flag, on the same frame, that startles the fly.
   */
  function fire(activeKc, weights, novelty = 0, surfaced = false) {
    observe(activeKc, weights);
    const strength = Math.max(0, Math.min(1, novelty));
    const intensity = FLASH_FLOOR + (1 - FLASH_FLOOR) * strength;
    bloom = Math.max(bloom, strength);

    if (surfaced) {
      // Only one surfaced response is held at a time, so let the last one go first.
      for (const i of markedCells) kcMark[i] = 0;
      for (const s of markedSynapses) a3Mark[s] = 0;
      markedCells = activeKc.slice();
      markedSynapses = [];
      surge = 1;
      surgeAge = 0;
      for (const i of markedCells) kcMark[i] = 1;
    }

    glow = surgeGlow();
    for (const kcIndex of activeKc) {
      // Take the brighter of what is already there and what this response asks for, so a
      // murmur cannot dim a strike that is still fading.
      kcHeat[kcIndex] = Math.max(kcHeat[kcIndex], intensity);
      paintKc(kcIndex);
      const owned = synapsesOfKc.get(kcIndex);
      if (!owned) continue;
      for (const s of owned) {
        a3Heat[s] = Math.max(a3Heat[s], intensity);
        if (surfaced) { a3Mark[s] = 1; markedSynapses.push(s); }
        paintA3(s);
      }
    }
    sizeHalos();
    touched();
  }

  /** Back to a filter that has seen nothing: every weight at rest, nothing firing. */
  function reset() {
    weight.fill(1);
    kcHeat.fill(0);
    a3Heat.fill(0);
    kcMark.fill(0);
    a3Mark.fill(0);
    markedCells = [];
    markedSynapses = [];
    surge = 0;
    bloom = 0;
    glow = 0;
    repaint();
  }

  function setMode(mode) {
    calyx.visible = mode === 'both' || mode === 'calyx';
    alpha3.visible = mode === 'both' || mode === 'alpha3';
    kenyon.visible = mode === 'both' || mode === 'kc' || mode === 'calyx';
    alpha3Glow.visible = alpha3.visible;
    kenyonGlow.visible = kenyon.visible;
    frameOn(mode);
    userZoom = 1;   // the selection is the framing; a zoom from the last one is not wanted
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

  /* The flash is short on purpose. Responses arrive ~14 a second and each one selects a
   * twentieth of the cells, so a slow fade leaves half the cloud permanently half-lit and
   * the whole thing reads as noise. A quick decay back to the resting weight keeps each
   * response a separate, countable event. */
  function decay(heat, rate, dt, repaintOne) {
    let dirty = false;
    for (let i = 0; i < heat.length; i += 1) {
      if (heat[i] <= 0) continue;
      heat[i] = Math.max(0, heat[i] - rate * dt);
      repaintOne(i);
      dirty = true;
    }
    return dirty;
  }

  function frame() {
    const dt = Math.min(clock.getDelta(), 0.1);
    resize();
    if (spin) yaw += dt * 0.06;

    if (heldSurge !== null) {
      // Same hook, and the same reason, as desk.js: a software-rendered screenshot takes
      // seconds, by which time the afterglow this is meant to photograph is long gone.
      surge = heldSurge;
      surgeAge = 0.03;
    } else if (surge > 0) {
      surge = Math.max(0, surge - SURGE_DECAY * dt);
      surgeAge += dt;
    }
    bloom = Math.max(0, bloom - dt * 1.1);

    const wasGlowing = glow > 0;
    glow = surgeGlow();

    const cooled = decay(kcHeat, 4.2, dt, paintKc);
    const cooledSynapses = decay(a3Heat, 3.4, dt, paintA3);
    if (glow > 0 || wasGlowing) {
      // Only the points the surfaced response actually lit, so the afterglow never spreads
      // to a synapse that response never touched.
      for (const i of markedCells) paintKc(i);
      for (const s of markedSynapses) paintA3(s);
    }
    if (cooled || cooledSynapses || glow > 0 || wasGlowing) touched();

    sizeHalos();

    camera.position.set(
      target.x + Math.sin(yaw) * Math.cos(pitch) * distance,
      target.y + Math.sin(pitch) * distance,
      target.z + Math.cos(yaw) * Math.cos(pitch) * distance,
    );
    camera.lookAt(target);
    renderer.render(scene, camera);
    requestAnimationFrame(frame);
  }
  repaint();
  frame();

  const api = {
    fire,
    observe,
    reset,
    repaint,
    setMode,
    counts: anatomy.counts,
    stats: anatomy.stats,
    // Test hook, and the same one the desk exposes: a software-rendered screenshot takes
    // seconds, by which time a flash is long gone. This reports what is lit right now.
    debugState: () => {
      let litCells = 0;
      let litSynapses = 0;
      let depressed = 0;
      let peak = 0;
      for (let i = 0; i < kcHeat.length; i += 1) {
        if (kcHeat[i] > 0.05) litCells += 1;
        if (weight[i] < 0.5) depressed += 1;
      }
      for (let s = 0; s < a3Heat.length; s += 1) {
        if (a3Heat[s] > 0.05) litSynapses += 1;
        peak = Math.max(peak, Math.max(a3Heat[s], a3Mark[s] * glow));
      }
      return {
        litCells,
        litSynapses,
        depressed,
        meanWeight: mean(weight),
        peak,
        surge,
        glow,
        bloom,
        marked: markedSynapses.length,
        haloSize: alpha3Glow.material.size,
      };
    },
    // Pins the surfaced afterglow so it can be photographed; `debugHold(null)` releases it.
    debugHold: (v) => { heldSurge = v; },
  };
  if (typeof window !== 'undefined') window.__cloud = api;
  return api;
}

function mean(values) {
  let total = 0;
  for (let i = 0; i < values.length; i += 1) total += values[i];
  return values.length ? total / values.length : 0;
}
