// A small CDP driver for the Studio walk. Keeps nothing alive itself: the
// browser runs separately with --remote-debugging-port and every invocation
// attaches to the same page, so state survives between shell calls.
//
// Clicks are real Input.dispatchMouseEvent pairs and assert
// elementFromPoint at the point they press, because el.click() skips hit
// testing and a clamped popover reads green under it (wiki tooling.md
// § Headless browser).
// No default port: a fixed one drove another session's leftover browser
// (launch.sh). Start the browser with launch.sh and pass what it prints.
const PORT = process.env.CDP_PORT;
if (!PORT) { console.error('CDP_PORT unset — start a browser with `bash .claude/skills/verify-live/launch.sh` and prefix this call with the CDP_PORT=… it prints'); process.exit(2); }

async function target() {
  const res = await fetch(`http://127.0.0.1:${PORT}/json/list`);
  const list = await res.json();
  const page = list.find((t) => t.type === 'page');
  if (!page) throw new Error('no page target');
  return page.webSocketDebuggerUrl;
}

class Session {
  constructor(ws) { this.ws = ws; this.id = 0; this.waiting = new Map(); }
  static async open() {
    const ws = new WebSocket(await target());
    const s = new Session(ws);
    ws.addEventListener('message', (ev) => {
      const msg = JSON.parse(ev.data);
      if (msg.id && s.waiting.has(msg.id)) { s.waiting.get(msg.id)(msg); s.waiting.delete(msg.id); }
    });
    await new Promise((ok, no) => { ws.addEventListener('open', ok); ws.addEventListener('error', no); });
    return s;
  }
  send(method, params = {}) {
    const id = ++this.id;
    return new Promise((resolve) => {
      this.waiting.set(id, (msg) => resolve(msg));
      this.ws.send(JSON.stringify({ id, method, params }));
    });
  }
  async eval(expression) {
    const msg = await this.send('Runtime.evaluate', {
      expression, returnByValue: true, awaitPromise: true, userGesture: true,
    });
    const r = msg.result;
    if (r.exceptionDetails) throw new Error(JSON.stringify(r.exceptionDetails.exception?.description ?? r.exceptionDetails));
    return r.result.value;
  }
  close() { this.ws.close(); }
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function main() {
  const [cmd, ...rest] = process.argv.slice(2);
  const s = await Session.open();
  try {
    if (cmd === 'goto') {
      await s.send('Page.enable');
      await s.send('Page.navigate', { url: rest[0] });
      await sleep(Number(rest[1] ?? 1500));
      console.log(JSON.stringify({ url: await s.eval('location.href'), title: await s.eval('document.title') }));
    } else if (cmd === 'eval') {
      console.log(JSON.stringify(await s.eval(rest.join(' ')), null, 2));
    } else if (cmd === 'evalfile') {
      const src = await (await import('node:fs/promises')).readFile(rest[0], 'utf8');
      console.log(JSON.stringify(await s.eval(src), null, 2));
    } else if (cmd === 'click') {
      const selector = rest[0];
      const dwell = Number(rest[1] ?? 120);
      const hit = await s.eval(`(() => {
        const el = document.querySelector(${JSON.stringify(selector)});
        if (!el) return {ok: false, why: 'no such element'};
        const r = el.getBoundingClientRect();
        if (!r.width || !r.height) return {ok: false, why: 'zero-sized', rect: {x: r.x, y: r.y, w: r.width, h: r.height}};
        const cx = Math.round(r.x + r.width / 2), cy = Math.round(r.y + r.height / 2);
        const at = document.elementFromPoint(cx, cy);
        const reachable = at && (at === el || el.contains(at) || at.contains(el));
        return {ok: !!reachable, cx, cy, why: reachable ? null : 'elementFromPoint is ' + (at ? at.tagName + '.' + at.className : 'null'),
                rect: {x: r.x, y: r.y, w: r.width, h: r.height},
                vis: getComputedStyle(el).visibility, disabled: el.disabled ?? null};
      })()`);
      if (!hit.ok) { console.log(JSON.stringify({ clicked: false, ...hit })); process.exitCode = 2; }
      else {
        const p = { x: hit.cx, y: hit.cy, button: 'left', clickCount: 1, buttons: 1 };
        await s.send('Input.dispatchMouseEvent', { type: 'mouseMoved', x: hit.cx, y: hit.cy, buttons: 0 });
        await s.send('Input.dispatchMouseEvent', { type: 'mousePressed', ...p });
        await sleep(dwell);
        await s.send('Input.dispatchMouseEvent', { type: 'mouseReleased', ...p, buttons: 0 });
        await sleep(120);
        console.log(JSON.stringify({ clicked: true, dwell, ...hit }));
      }
    } else if (cmd === 'key') {
      // key <key> [selector] [dwell] — a real key press through the browser,
      // not a synthesised KeyboardEvent. The difference matters for anything
      // reading `event.target`: `?` typed into the composer must NOT open the
      // shortcut sheet, and only a genuine press with focus in the textarea
      // proves it. `selector` focuses that element first; omit it for a
      // window-level binding.
      const key = rest[0];
      const selector = rest[1] && rest[1] !== '-' ? rest[1] : null;
      const dwell = Number(rest[2] ?? 120);
      if (selector) {
        const focused = await s.eval(`(() => { const el = document.querySelector(${JSON.stringify(selector)});
          if (!el) return null; el.focus(); return document.activeElement === el ? (el.id || el.tagName) : 'not-focused'; })()`);
        if (!focused || focused === 'not-focused') {
          console.log(JSON.stringify({ pressed: false, why: 'could not focus ' + selector }));
          process.exitCode = 2;
          return;
        }
      }
      // `text` is what makes a printable key actually type; the modifier bit
      // is what makes `?` reach a handler reading `event.key` on a US layout.
      //
      // **A named key needs its virtual key code or the browser's own
      // machinery ignores it.** Escape without `windowsVirtualKeyCode: 27`
      // reaches a JS keydown listener exactly as a real press does, so
      // anything hand-written looks fine — but Chrome's close watcher, which
      // is what dismisses a native <dialog>, reads the virtual code and not
      // `.key`. Measured 2026-08-24: the same dialog stayed open under a
      // code-less Escape and closed under this one, which would have been
      // reported as a bug in the page.
      const CODES = { Escape: 27, Enter: 13, Tab: 9, Backspace: 8, Delete: 46,
                      ArrowLeft: 37, ArrowUp: 38, ArrowRight: 39, ArrowDown: 40,
                      Home: 36, End: 35, PageUp: 33, PageDown: 34, ' ': 32 };
      const printable = key.length === 1;
      const shifted = (printable && key !== key.toLowerCase()) || '?!@#$%^&*()_+{}|:"<>~'.includes(key);
      const vk = CODES[key] ?? (printable ? key.toUpperCase().charCodeAt(0) : 0);
      const base = {
        key,
        code: printable ? undefined : key,
        modifiers: shifted ? 8 : 0,
        windowsVirtualKeyCode: vk,
        nativeVirtualKeyCode: vk,
      };
      await s.send('Input.dispatchKeyEvent', { type: 'keyDown', ...base, text: printable ? key : undefined });
      await sleep(dwell);
      await s.send('Input.dispatchKeyEvent', { type: 'keyUp', ...base });
      await sleep(120);
      console.log(JSON.stringify({ pressed: key, on: selector, dwell }));
    } else if (cmd === 'drag') {
      // drag <selector> <fromFrac> <toFrac> [dwell] — horizontal, inside the element
      const [selector, from, to] = rest;
      const dwell = Number(rest[3] ?? 120);
      const box = await s.eval(`(() => { const el = document.querySelector(${JSON.stringify(selector)}); if (!el) return null;
        const r = el.getBoundingClientRect(); return {x: r.x, y: r.y, w: r.width, h: r.height}; })()`);
      if (!box) { console.log(JSON.stringify({ dragged: false, why: 'no element' })); process.exitCode = 2; return; }
      const y = Math.round(box.y + box.h / 2);
      const x1 = Math.round(box.x + box.w * Number(from));
      const x2 = Math.round(box.x + box.w * Number(to));
      await s.send('Input.dispatchMouseEvent', { type: 'mouseMoved', x: x1, y, buttons: 0 });
      await s.send('Input.dispatchMouseEvent', { type: 'mousePressed', x: x1, y, button: 'left', clickCount: 1, buttons: 1 });
      const steps = 12;
      for (let i = 1; i <= steps; i++) {
        await s.send('Input.dispatchMouseEvent', { type: 'mouseMoved', x: Math.round(x1 + ((x2 - x1) * i) / steps), y, button: 'left', buttons: 1 });
        await sleep(dwell / steps);
      }
      await s.send('Input.dispatchMouseEvent', { type: 'mouseReleased', x: x2, y, button: 'left', clickCount: 1, buttons: 0 });
      await sleep(150);
      console.log(JSON.stringify({ dragged: true, from: x1, to: x2, y }));
    } else if (cmd === 'dragxy') {
      const [x1, y1, x2, y2] = rest.slice(0, 4).map(Number);
      const dwell = Number(rest[4] ?? 120);
      await s.send('Input.dispatchMouseEvent', { type: 'mouseMoved', x: x1, y: y1, buttons: 0 });
      await s.send('Input.dispatchMouseEvent', { type: 'mousePressed', x: x1, y: y1, button: 'left', clickCount: 1, buttons: 1 });
      const steps = 10;
      for (let i = 1; i <= steps; i++) {
        await s.send('Input.dispatchMouseEvent', { type: 'mouseMoved', x: Math.round(x1 + ((x2 - x1) * i) / steps), y: Math.round(y1 + ((y2 - y1) * i) / steps), button: 'left', buttons: 1 });
        await sleep(dwell / steps);
      }
      await s.send('Input.dispatchMouseEvent', { type: 'mouseReleased', x: x2, y: y2, button: 'left', clickCount: 1, buttons: 0 });
      await sleep(200);
      console.log(JSON.stringify({ dragged: [x1, y1, x2, y2], dwell }));
    } else if (cmd === 'viewport') {
      const [w, h] = rest.slice(0, 2).map(Number);
      // A third argument is the device scale factor — the recorder captures a
      // 1280x720 page at 1.5 so the window's type is legible in a 1080p frame.
      const dsf = Number(rest[2] ?? 1);
      // A fourth is CDP's own `scale` — a scale applied to the resulting view
      // image, which is what makes the screencast hand back more than the
      // page's CSS pixels (a device scale factor alone does not).
      const scale = Number(rest[3] ?? 1);
      await s.send('Emulation.setDeviceMetricsOverride', { width: w, height: h, deviceScaleFactor: dsf, mobile: false, ...(scale !== 1 ? { scale } : {}) });
      await sleep(400);
      // Two sweeps, because one is blind where the other is noisy
      // (CLAUDE.md § The README screenshots, and the five defects they found).
      //
      // `overflowing` walks the page but **skips anything inside a scroll
      // container** — without that skip every timeline lane is a finding:
      // the flat version reported 240 nodes on a normal Edit view, all of
      // them ruler ticks and clip blocks doing exactly what they are supposed
      // to, which is a probe that has to be ignored and therefore is.
      //
      // `scrollers` is the second sweep the first one cannot make: each
      // scroll container against its **own** clientWidth. `overflow-y: auto`
      // computes `overflow-x` to `auto` too, so a pane is one of these without
      // saying so, and that is how `#properties-body` clipped a value
      // mid-word while every page-level probe called it clean. In Edit mode
      // this should find exactly one, `#track-lanes`.
      const probe = await s.eval(`(() => {
        const scrollParent = (el) => {
          for (let p = el.parentElement; p; p = p.parentElement) {
            const cs = getComputedStyle(p);
            if (/(auto|scroll)/.test(cs.overflowX + cs.overflowY)) return p;
          }
          return null;
        };
        const name = (e) => (e.id || e.className || e.tagName);
        const over = [...document.querySelectorAll('body *')]
          .filter((e) => !scrollParent(e) && e.getBoundingClientRect().right > innerWidth + 1)
          .map((e) => name(e) + '@' + Math.round(e.getBoundingClientRect().right));
        const scrollers = [...document.querySelectorAll('body *')]
          .filter((e) => {
            const cs = getComputedStyle(e);
            return /(auto|scroll)/.test(cs.overflowX + cs.overflowY) && e.scrollWidth > e.clientWidth + 1;
          })
          .map((e) => name(e) + ' ' + e.scrollWidth + '>' + e.clientWidth);
        return {innerWidth, scrollWidth: document.body.scrollWidth,
                overflowing: over.slice(0, 8), scrollers: scrollers.slice(0, 8)};
      })()`);
      console.log(JSON.stringify(probe));
    } else if (cmd === 'shot') {
      const msg = await s.send('Page.captureScreenshot', { format: 'png' });
      const fs = await import('node:fs/promises');
      await fs.writeFile(rest[0], Buffer.from(msg.result.data, 'base64'));
      console.log(JSON.stringify({ saved: rest[0] }));
    } else if (cmd === 'console') {
      // attach, collect console + page errors for N ms
      await s.send('Runtime.enable');
      await s.send('Log.enable');
      const lines = [];
      s.ws.addEventListener('message', (ev) => {
        const m = JSON.parse(ev.data);
        if (m.method === 'Runtime.consoleAPICalled' && ['error', 'warning'].includes(m.params.type))
          lines.push({ type: m.params.type, text: m.params.args.map((a) => a.value ?? a.description).join(' ') });
        if (m.method === 'Runtime.exceptionThrown')
          lines.push({ type: 'exception', text: m.params.exceptionDetails.exception?.description });
        // `url` is the whole value of this branch: a failed request logs
        // "Failed to load resource: ... 400" and names no route, and
        // guessing which one from a plausible-looking endpoint has already
        // cost a wrong diagnosis here (proofcut HISTORY.md § The two console
        // 400s). CDP hands it over; only dropping it made it a mystery.
        if (m.method === 'Log.entryAdded' && m.params.entry.level === 'error')
          lines.push({ type: 'log', text: m.params.entry.text, url: m.params.entry.url });
      });
      await sleep(Number(rest[0] ?? 3000));
      console.log(JSON.stringify(lines, null, 2));
    } else throw new Error(`unknown command: ${cmd}`);
  } finally { s.close(); }
}

main().catch((e) => { console.error(String(e)); process.exit(1); });
