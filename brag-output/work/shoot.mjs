import { chromium } from '/opt/node-tools/node_modules/playwright/index.mjs';
import fs from 'fs';
const [,, mode, ...ts] = process.argv;
const b = await chromium.launch({ executablePath: '/opt/pw-browsers/chromium' });
const p = await b.newPage({ viewport: { width: 1920, height: 1080 } });
await p.goto('file://' + process.cwd() + '/index.html');
await p.evaluate(() => document.fonts.ready);
await p.waitForTimeout(300);
if (mode === 'stills') {
  fs.mkdirSync('stills', { recursive: true });
  for (const t of ts) { await p.evaluate(t => render(+t), t); await p.screenshot({ path: `stills/t${t}.png` }); }
} else {
  fs.mkdirSync('frames', { recursive: true });
  const N = 600;
  for (let i = +(process.env.A||0); i < +(process.env.B||N); i++) { await p.evaluate(t => render(t), i / 30); await p.screenshot({ path: `frames/f${String(i).padStart(4, '0')}.jpg`, type: 'jpeg', quality: 92 }); }
}
await b.close();
