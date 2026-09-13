/* Dashboard orchestration.
 *
 * Everything on screen is driven by one recorded run (`run.json`), produced by
 * `flypaper.web.replay` from a real corpus. Nothing is generated in the browser: the
 * novelty scores, the active Kenyon cells and the log lines are what the run produced, in
 * the order it produced them.
 */

import { initDesk } from './desk.js';
import { initCloud } from './neurons.js';

const $ = (id) => document.getElementById(id);

/* The filter's synaptic weights for the cells this response fired, straight out of the
 * recording. `replay.py` quantises each weight to a byte and base64s the lot, because a
 * hundred of them per response for two thousand responses is otherwise the biggest thing
 * on the page and this loads over a tailnet. A byte is finer than the screen can show. */
function weightsOf(frame) {
  if (!frame.wq) return null;   // a run recorded before the weight track existed
  const raw = atob(frame.wq);
  const out = new Float32Array(raw.length);
  for (let i = 0; i < raw.length; i += 1) out[i] = raw.charCodeAt(i) / 255;
  return out;
}

const state = {
  run: null,
  frame: 0,
  playing: true,
  speed: 1,
  accum: 0,
  shown: 0,
};

/* ── charts ─────────────────────────────────────────────────────────────── */

function chart(canvas, values, options = {}) {
  const {
    colour = '#5fd4d6', fill = null, marks = [], lo = null, hi = null, cutoff = null,
  } = options;
  const ratio = Math.min(window.devicePixelRatio || 1, 2);
  const w = canvas.clientWidth || 1;
  const h = canvas.clientHeight || 1;
  if (canvas.width !== w * ratio || canvas.height !== h * ratio) {
    canvas.width = w * ratio;
    canvas.height = h * ratio;
  }
  const g = canvas.getContext('2d');
  g.setTransform(ratio, 0, 0, ratio, 0, 0);
  g.clearRect(0, 0, w, h);

  if (!values.length) return;
  const min = lo === null ? Math.min(...values) : lo;
  const max = hi === null ? Math.max(...values) : hi;
  const span = max - min || 1;
  const x = (i) => (i / Math.max(values.length - 1, 1)) * w;
  const y = (v) => h - ((v - min) / span) * (h - 4) - 2;

  // baseline grid
  g.strokeStyle = '#101820';
  g.lineWidth = 1;
  for (let i = 1; i < 4; i += 1) {
    const gy = (h / 4) * i;
    g.beginPath(); g.moveTo(0, gy); g.lineTo(w, gy); g.stroke();
  }

  if (fill) {
    g.beginPath();
    g.moveTo(0, h);
    values.forEach((v, i) => g.lineTo(x(i), y(v)));
    g.lineTo(w, h);
    g.closePath();
    g.fillStyle = fill;
    g.fill();
  }

  g.beginPath();
  values.forEach((v, i) => (i ? g.lineTo(x(i), y(v)) : g.moveTo(x(i), y(v))));
  g.strokeStyle = colour;
  g.lineWidth = 1.2;
  g.stroke();

  if (cutoff !== null && cutoff > min) {
    const cy = y(cutoff);
    g.setLineDash([3, 4]);
    g.beginPath(); g.moveTo(0, cy); g.lineTo(w, cy); g.stroke();
    g.strokeStyle = 'rgba(255,217,138,0.55)';
    g.stroke();
    g.setLineDash([]);
  }

  for (const mark of marks) {
    if (mark.i >= values.length) continue;
    g.beginPath();
    g.arc(x(mark.i), y(values[mark.i]), 2.6, 0, Math.PI * 2);
    g.fillStyle = mark.colour;
    g.fill();
  }

  // playhead
  g.beginPath();
  g.moveTo(x(values.length - 1), 0);
  g.lineTo(x(values.length - 1), h);
  g.strokeStyle = 'rgba(255,255,255,0.14)';
  g.stroke();
}

/* ── log ────────────────────────────────────────────────────────────────── */

const logEl = $('log');
const logRows = [];

function appendLog(frame) {
  const row = document.createElement('div');
  row.className = 'row' + (frame.shown ? ' surfaced' : '') + (frame.hit ? ' hit' : '');
  row.textContent = frame.log;
  row.dataset.shown = frame.shown ? '1' : '0';
  logRows.push(row);
  logEl.appendChild(row);
  while (logRows.length > 400) logEl.removeChild(logRows.shift());
  applyLogFilter();
  logEl.scrollTop = logEl.scrollHeight;
}

function applyLogFilter() {
  const only = $('only-surfaced').checked;
  for (const row of logRows) {
    row.style.display = only && row.dataset.shown !== '1' ? 'none' : '';
  }
}

/* ── main ───────────────────────────────────────────────────────────────── */

async function main() {
  const [run, desk, cloud] = await Promise.all([
    fetch('run.json').then((r) => r.json()),
    initDesk($('desk')),
    initCloud($('cloud'), $('cloud-stats')),
  ]);
  state.run = run;

  const totals = `${run.total_responses.toLocaleString()} responses`;
  $('subtitle').textContent =
    `${totals} · ${run.n_kc.toLocaleString()} Kenyon cells · ` +
    `${run.n_active} active per tag (${(run.sparsity * 100).toFixed(0)}% sparse) · ` +
    `channel set ${run.channel_set} · ${run.projection} projection`;
  $('run-label').textContent = run.label || 'recorded run';
  $('foot-prov').textContent =
    `replay of a recorded run · commit ${(run.provenance.git_commit || '').slice(0, 10)} · ` +
    `${run.provenance.generated_utc}`;
  $('ax-r').textContent = `${run.total_responses}`;
  $('note-shown').textContent = `${run.shown_count}`;
  desk.setHeader(`flypaper — ${run.label || 'run'}`);

  const scrubEl = $('scrub');
  scrubEl.max = String(run.frames.length - 1);

  /* The scale the neuron view's brightness is read against: the most novel response this
   * run produced.
   *
   * Novelty is not used raw there, and the reason is in the numbers. On a settled scan the
   * filter is doing its job, so 99.6% of this run's responses score a flat 0.000 and the
   * most novel thing in two thousand responses still only reaches 0.312. Scaled absolutely,
   * every flash would be the same invisible sliver above nothing. Against the run's own
   * ceiling, routine traffic sits at the floor and an outlier fills the range - which is
   * the comparison a reader is actually making.
   */
  const noveltyScale = Math.max(1e-6, ...run.frames.map((f) => f.n));

  const novelty = [];
  const saturation = [];
  const sizes = [];
  const marks = [];

  function renderFrame(index, { silent = false } = {}) {
    const frame = run.frames[index];
    if (!frame) return;

    novelty.push(frame.n);
    saturation.push(run.saturation[index] ?? 0);
    sizes.push(frame.len);
    if (frame.hit) marks.push({ i: novelty.length - 1, colour: '#ff7a59' });
    else if (frame.shown) marks.push({ i: novelty.length - 1, colour: '#ffd98a' });

    const WINDOW = 420;
    if (novelty.length > WINDOW) {
      const drop = novelty.length - WINDOW;
      novelty.splice(0, drop);
      saturation.splice(0, drop);
      sizes.splice(0, drop);
      for (const mark of marks) mark.i -= drop;
      while (marks.length && marks[0].i < 0) marks.shift();
    }

    /* One response, handed to both panels on the same tick out of the same frame.
     *
     * `frame.shown` is the run's own decision that this response cleared the cutoff, and it
     * is the only thing either panel reacts to specially: the fly startles and the cells
     * that response lit surge, together, because it is one event. They were already on the
     * same tick before, but the neuron flash decayed in a quarter of a second while the
     * startle ran for two, so by the time a viewer looked across there was nothing left to
     * see.
     *
     * Silent frames are the catch-up after a scrub: the filter still learned from them, so
     * its weights have to move, but the viewer did not watch them arrive and nothing should
     * react to responses already past.
     */
    const weights = weightsOf(frame);
    if (silent) {
      cloud.observe(frame.kc, weights);
    } else {
      cloud.fire(frame.kc, weights, frame.n / noveltyScale, frame.shown);
      if (frame.shown) desk.surface();
      appendLog(frame);
    }
    // The count is of the run, so it includes frames replayed to catch up after a scrub.
    if (frame.shown) state.shown += 1;
    desk.pushLine({ text: frame.log, shown: frame.shown, hit: frame.hit });
    desk.setState({
      novelty: frame.n,
      index: frame.i,
      total: run.total_responses,
      saturation: run.saturation[index] ?? 0,
      shown: frame.shown,
    });

    $('d-word').textContent = frame.w || '—';
    $('d-novelty').textContent = frame.n.toFixed(3);
    $('d-shown').textContent = String(state.shown);
    $('t-novelty').textContent = `${frame.n.toFixed(3)} now · ${state.shown} surfaced`;
    $('t-sat').textContent = `${((run.saturation[index] ?? 0) * 100).toFixed(1)}%`;
    $('t-size').textContent = `${frame.len.toLocaleString()} b · ${frame.wd} w · ${frame.ln} l`;
    $('trace-meta').textContent =
      `${frame.i + 1} / ${run.total_responses} · ${run.shown_count} surfaced in this run`;
    $('scrub-label').textContent = `${frame.i + 1} / ${run.total_responses}`;
    $('ax-l').textContent = `${Math.max(0, frame.i - novelty.length + 1)}`;
    scrubEl.value = String(index);

    // The cutoff the run actually used: the top (100 - percentile)% of recent scores.
    const recent = novelty.slice(-Math.min(novelty.length, 1000)).slice().sort((a, b) => a - b);
    const cutoff = recent.length > 40 ? recent[Math.floor(recent.length * 0.995)] : null;
    chart($('chart-novelty'), novelty, {
      colour: '#ffd98a', fill: 'rgba(217,164,65,0.10)', marks, lo: 0, hi: 1, cutoff,
    });
    $('t-novelty').textContent =
      `${frame.n.toFixed(3)} now · cutoff ${cutoff === null ? '—' : cutoff.toFixed(3)} · ` +
      `${state.shown} surfaced`;
    chart($('chart-sat'), saturation, { colour: '#5fd4d6', fill: 'rgba(95,212,214,0.08)', lo: 0 });
    chart($('chart-size'), sizes, { colour: '#8aa0b4' });
  }

  function reset(to = 0) {
    novelty.length = 0; saturation.length = 0; sizes.length = 0; marks.length = 0;
    state.shown = 0;
    while (logRows.length) logEl.removeChild(logRows.pop());
    desk.reset();
    cloud.reset();
    state.frame = to;
    // Replay quietly up to the scrub point so the traces and the filter state are right.
    // The traces only need the window they display, but a synaptic weight is the whole run
    // so far - a cell depressed at response 20 is still depressed at response 1,900 - so the
    // filter is caught up from the start. That is only arithmetic; the one repaint at the
    // end is what costs.
    const from = Math.max(0, to - 420);
    for (let i = 0; i < from; i += 1) {
      const frame = run.frames[i];
      if (frame) cloud.observe(frame.kc, weightsOf(frame));
    }
    for (let i = from; i < to; i += 1) renderFrame(i, { silent: true });
    cloud.repaint();
  }

  /* controls */
  $('btn-play').addEventListener('click', () => {
    state.playing = !state.playing;
    $('btn-play').innerHTML = state.playing ? '&#9646;&#9646; pause' : '&#9654; play';
    $('live-dot').classList.toggle('playing', state.playing);
  });
  $('btn-restart').addEventListener('click', () => reset(0));

  // Surfacing is what the tool is for and it is rare by design, so make it reachable
  // instead of leaving the reader to wait for one.
  $('btn-next').addEventListener('click', () => {
    const LEAD = 10;   // start a little before it, so the approach is visible
    let next = run.frames.findIndex((f, i) => i > state.frame && f.shown);
    if (next < 0) next = run.frames.findIndex((f) => f.shown);
    if (next < 0) return;
    reset(Math.max(0, next - LEAD));
    state.playing = true;
    $('btn-play').innerHTML = '&#9646;&#9646; pause';
    $('live-dot').classList.add('playing');
  });
  $('speed').addEventListener('change', (e) => { state.speed = Number(e.target.value); });
  $('cloud-mode').addEventListener('change', (e) => cloud.setMode(e.target.value));
  $('only-surfaced').addEventListener('change', applyLogFilter);
  scrubEl.addEventListener('input', (e) => {
    state.playing = false;
    $('btn-play').innerHTML = '&#9654; play';
    $('live-dot').classList.remove('playing');
    reset(Number(e.target.value));
    renderFrame(state.frame);
  });

  $('live-dot').classList.add('playing');

  /* Live mode.
   *
   * A scan produces exactly the frames a replay does - the recorder is the same object on
   * the server - so live is not a second renderer. It is the same one, with frames arriving
   * instead of being read from a file, and playback pinned to the end of what has arrived.
   */
  window.flypaperLive = {
    begin(meta) {
      state.playing = false;
      run.frames.length = 0;
      run.saturation.length = 0;
      run.label = meta.label || 'live';
      reset(0);
      $('live-dot').innerHTML = '&#9679; LIVE';
      $('live-dot').classList.add('playing');
      $('run-label').textContent = meta.label || 'live scan';
      desk.setHeader(`flypaper — ${meta.label || 'live'}`);
      $('foot-prov').textContent = 'live scan · ranked as it arrives';
    },
    push(frames, saturation) {
      for (const frame of frames) {
        run.frames.push(frame);
        run.saturation.push(saturation);
        renderFrame(run.frames.length - 1);
      }
      state.frame = run.frames.length;
      scrubEl.max = String(Math.max(0, run.frames.length - 1));
      scrubEl.value = scrubEl.max;
      $('ax-r').textContent = `${run.frames.length}`;
      $('scrub-label').textContent = `${run.frames.length} / ${run.frames.length}`;
    },
    end(summary) {
      $('live-dot').innerHTML = '&#9679; DONE';
      $('live-dot').classList.remove('playing');
      $('foot-prov').textContent = summary || 'scan finished';
    },
  };
  window.dispatchEvent(new Event('flypaper-ready'));

  let last = performance.now();
  function tick(now) {
    const dt = (now - last) / 1000;
    last = now;
    if (state.playing) {
      state.accum += dt * state.speed * 14;   // ~14 responses/second at 1x
      while (state.accum >= 1) {
        state.accum -= 1;
        if (state.frame >= run.frames.length) { state.frame = 0; reset(0); }
        renderFrame(state.frame);
        state.frame += 1;
      }
    }
    requestAnimationFrame(tick);
  }
  requestAnimationFrame(tick);
}

main().catch((err) => {
  $('subtitle').textContent = `failed to load: ${err}`;
  console.error(err);
});
