// Record the page the verify-live harness is driving, as a stream of frames.
//
//   node scripts/screencast.mjs <dir> [maxWidth] [maxHeight] [quality]
//
// Attaches to the same headless Chrome `cdp.mjs` talks to (CDP_PORT, required —
// Chrome takes any number of clients on one target), starts
// `Page.startScreencast`, and writes every frame Chrome sends to
// `<dir>/frames/NNNNNN.jpg` with its timestamp appended to `<dir>/index.jsonl`.
// Runs until SIGTERM/SIGINT, then stops the screencast and exits 0.
//
// Chrome emits a frame only when something painted, so the stream is
// variable-rate by construction: the timestamps are the whole record, and
// `record_run.py` turns them into a constant-rate mp4 through ffmpeg's concat
// demuxer with per-frame durations. Nothing here decides what the video looks
// like — it is the page, as the browser painted it, and whether a `<video>`
// element composites into these frames is the same open question as for
// `Page.captureScreenshot` (wiki tooling.md § Headless browser), which is why
// the orchestrator measures rather than assumes.
import { mkdir, writeFile, appendFile } from 'node:fs/promises';
import { join } from 'node:path';

const PORT = process.env.CDP_PORT;
if (!PORT) { console.error('CDP_PORT unset — see .claude/skills/verify-live/launch.sh'); process.exit(2); }
const [dir, maxWidth = '1920', maxHeight = '1080', quality = '85'] = process.argv.slice(2);
if (!dir) { console.error('usage: screencast.mjs <dir> [maxWidth] [maxHeight] [quality]'); process.exit(2); }

async function target() {
  const res = await fetch(`http://127.0.0.1:${PORT}/json/list`);
  const page = (await res.json()).find((t) => t.type === 'page');
  if (!page) throw new Error('no page target');
  return page.webSocketDebuggerUrl;
}

const ws = new WebSocket(await target());
await new Promise((ok, no) => { ws.addEventListener('open', ok); ws.addEventListener('error', no); });
let nextId = 0;
const waiting = new Map();
const send = (method, params = {}) => new Promise((resolve) => {
  const id = ++nextId;
  waiting.set(id, resolve);
  ws.send(JSON.stringify({ id, method, params }));
});

const frames = join(dir, 'frames');
await mkdir(frames, { recursive: true });
const index = join(dir, 'index.jsonl');
await writeFile(index, '');

let count = 0;
let stopping = false;
let pending = Promise.resolve();
ws.addEventListener('message', (ev) => {
  const msg = JSON.parse(ev.data);
  if (msg.id && waiting.has(msg.id)) { waiting.get(msg.id)(msg); waiting.delete(msg.id); return; }
  if (msg.method !== 'Page.screencastFrame') return;
  const { data, metadata, sessionId } = msg.params;
  const n = ++count;
  // Ack first: Chrome holds the next frame until it hears this, and a slow
  // disk write must not throttle the page's own painting.
  send('Page.screencastFrameAck', { sessionId });
  pending = pending.then(async () => {
    const name = String(n).padStart(6, '0') + '.jpg';
    await writeFile(join(frames, name), Buffer.from(data, 'base64'));
    await appendFile(index, JSON.stringify({ n, file: 'frames/' + name, ts: metadata.timestamp,
      w: metadata.deviceWidth, h: metadata.deviceHeight }) + '\n');
  });
});

async function stop() {
  if (stopping) return;
  stopping = true;
  await send('Page.stopScreencast');
  await pending;
  await writeFile(join(dir, 'DONE'), JSON.stringify({ frames: count, stopped: Date.now() / 1000 }));
  console.log(JSON.stringify({ frames: count, dir }));
  ws.close();
  process.exit(0);
}
process.on('SIGTERM', stop);
process.on('SIGINT', stop);

await send('Page.enable');
const started = await send('Page.startScreencast', {
  format: 'jpeg', quality: Number(quality), maxWidth: Number(maxWidth), maxHeight: Number(maxHeight), everyNthFrame: 1,
});
if (started.error) { console.error(JSON.stringify(started.error)); process.exit(1); }
await writeFile(join(dir, 'STARTED'), JSON.stringify({ started: Date.now() / 1000 }));
console.log(JSON.stringify({ recording: dir }));
