/* Driving a scan from the page.
 *
 * The controls here can choose a target from the scope file, a wordlist from the allowed
 * directory and a rate up to the server's ceiling. They cannot widen any of those: the
 * scope file and wordlist directory are named on the server's command line before the
 * browser exists, and the server re-checks every request against them. This file is a
 * convenience over that, never the thing enforcing it.
 *
 * Every POST carries a per-process token the server injected into this page. A page on
 * another origin cannot read it, which is what stops a site the operator happens to be
 * visiting from driving a scanner on their loopback interface.
 */

const $ = (id) => document.getElementById(id);
const TOKEN = window.FLYPAPER_TOKEN || '';

const POLL_MS = 900;

const ui = {
  toggle: $('scan-toggle'),
  body: $('scan-body'),
  target: $('scan-target'),
  wordlist: $('scan-wordlist'),
  rate: $('scan-rate'),
  start: $('scan-start'),
  stop: $('scan-stop'),
  status: $('scan-status'),
  scope: $('scan-scope'),
};

let polling = null;
let since = 0;

function say(text, bad = false) {
  ui.status.textContent = text;
  ui.status.classList.toggle('bad', Boolean(bad));
}

async function post(path, body) {
  const res = await fetch(path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'X-Flypaper-Token': TOKEN },
    body: JSON.stringify(body || {}),
  });
  if (res.status === 403) throw new Error('rejected: this page is not holding a valid token');
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
  return res.json();
}

async function loadCapabilities() {
  const info = await fetch('/api/scan/ready').then((r) => r.json());
  ui.wordlist.innerHTML = '';
  for (const name of info.wordlists) {
    const opt = document.createElement('option');
    opt.value = name;
    opt.textContent = name;
    ui.wordlist.append(opt);
  }
  ui.rate.max = String(info.max_rate);
  if (Number(ui.rate.value) > info.max_rate) ui.rate.value = String(info.max_rate);

  if (!info.ready) {
    ui.start.disabled = true;
    ui.scope.textContent = info.why;
    say('scanning not configured', true);
    return info;
  }
  ui.scope.textContent =
    `in scope: ${info.hosts.join(', ')} · rate ceiling ${info.max_rate}/s · ` +
    `the page cannot widen either`;
  // A first target the operator can edit rather than invent, built from the scope file.
  const first = info.hosts.find((h) => !h.startsWith('*')) || info.hosts[0] || '';
  if (first && !ui.target.value) {
    ui.target.value = `http://${first.replace(/^\*\./, '')}/FUZZ`;
  }
  say('ready');
  return info;
}

function summarise(st) {
  const bits = [
    st.status,
    `${st.requests.toLocaleString()} responses`,
    `${st.surfaced} surfaced`,
    `${st.elapsed}s`,
  ];
  if (st.malformed) bits.push(`${st.malformed} unparsed`);
  return bits.join(' · ');
}

async function poll() {
  let st;
  try {
    st = await fetch(`/api/scan/status?since=${since}`).then((r) => r.json());
  } catch (err) {
    say(`lost contact: ${err.message}`, true);
    return;
  }
  if (st.frames && st.frames.length && window.flypaperLive) {
    window.flypaperLive.push(st.frames, st.meta ? st.meta.saturation : 0);
    since = st.next;
  }
  say(st.error ? `${st.status}: ${st.error}` : summarise(st), Boolean(st.error));
  if (st.complete) {
    clearInterval(polling);
    polling = null;
    ui.start.disabled = false;
    ui.stop.disabled = true;
    if (window.flypaperLive) window.flypaperLive.end(summarise(st));
  }
}

ui.toggle.addEventListener('click', () => {
  const open = ui.body.hidden;
  ui.body.hidden = !open;
  ui.toggle.setAttribute('aria-expanded', String(open));
  ui.toggle.innerHTML = open ? '&#9660; scan' : '&#9658; scan';
});

ui.start.addEventListener('click', async () => {
  ui.start.disabled = true;
  say('starting…');
  try {
    const st = await post('/api/scan/start', {
      target: ui.target.value.trim(),
      wordlist: ui.wordlist.value,
      rate: Number(ui.rate.value) || 20,
    });
    if (st.refused) {
      say(st.refused, true);
      ui.start.disabled = false;
      return;
    }
    since = 0;
    if (window.flypaperLive) window.flypaperLive.begin({ label: st.target });
    ui.stop.disabled = false;
    say(summarise(st));
    polling = setInterval(poll, POLL_MS);
  } catch (err) {
    say(err.message, true);
    ui.start.disabled = false;
  }
});

ui.stop.addEventListener('click', async () => {
  ui.stop.disabled = true;
  try {
    await post('/api/scan/stop', {});
    say('stopping…');
  } catch (err) {
    say(err.message, true);
  }
});

loadCapabilities().catch((err) => say(`could not read scan config: ${err.message}`, true));
