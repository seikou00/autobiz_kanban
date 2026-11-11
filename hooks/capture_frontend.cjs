// Uses an already-installed Playwright. No package download or browser install.
const fs = require('fs');
const path = require('path');
const crypto = require('crypto');
const http = require('http');
function referenceFile(base, publicRoot, pathname) {
  const mount = '/__reference__/';
  const root = pathname.startsWith(mount) ? base : (publicRoot || path.dirname(base));
  const relative = pathname.startsWith(mount) ? pathname.slice(mount.length) : '.' + pathname;
  const file = path.resolve(root, relative);
  const assetRoot = publicRoot || path.join(path.dirname(base), 'assets');
  if (!file.startsWith(base + path.sep) && !file.startsWith(assetRoot + path.sep)) throw new Error('reference outside bundle');
  return file;
}
module.exports = { referenceFile };
if (require.main === module) (async () => {
  const [root, url, output, width, height, config] = process.argv.slice(2);
  const { sources, publicRoot } = JSON.parse(config);
  let driver;
  try { driver = require.resolve('playwright', { paths: [root] }); }
  catch { driver = require.resolve('playwright-core', { paths: [root] }); }
  const { chromium } = require(driver);
  const browser = await chromium.launch({ headless: true });
  process.once('SIGTERM', () => { browser.close().finally(() => process.exit(143)); });
  const screenshots = [], errors = [];
  let server;
  try {
    const context = await browser.newContext({ viewport: { width: +width, height: +height }, timezoneId: 'Asia/Shanghai', reducedMotion: 'reduce' });
    const page = await context.newPage();
    page.on('pageerror', e => errors.push(String(e)));
    page.on('response', r => { if (r.status() >= 400) errors.push(`${r.status()} ${r.url()}`); });
    async function snap(address, label) {
      await page.goto(address, { waitUntil: 'load', timeout: 20000 });
      await page.evaluate(() => document.fonts.ready);
      await page.addStyleTag({ content: '*,*::before,*::after{animation:none!important;transition:none!important;caret-color:transparent!important}' });
      await page.screenshot({ path: path.join(output, `${label}.png`), fullPage: true });
      const file = path.join(output, `${label}.png`);
      screenshots.push({ path: file, sha256: crypto.createHash('sha256').update(fs.readFileSync(file)).digest('hex') });
    }
    await snap(url, 'actual');
    // References get their own context (no app cookies) and local server, not file://.
    await context.close();
    const referenceContext = await browser.newContext({ viewport: { width: +width, height: +height }, reducedMotion: 'reduce' });
    const reference = await referenceContext.newPage();
    reference.on('pageerror', e => errors.push(`reference: ${e}`));
    reference.on('response', r => { if (r.status() >= 400) errors.push(`reference: ${r.status()} ${r.url()}`); });
    for (let i = 0; i < sources.length; i++) {
      const base = path.dirname(sources[i]);
      server = http.createServer((req, res) => {
        let file;
        try { file = referenceFile(base, publicRoot, decodeURIComponent(new URL(req.url, 'http://localhost').pathname)); }
        catch { res.writeHead(404); res.end(); return; }
        if (!fs.existsSync(file) || !fs.statSync(file).isFile()) { res.writeHead(404); res.end(); return; }
        const types = { '.html': 'text/html; charset=utf-8', '.css': 'text/css', '.js': 'text/javascript', '.svg': 'image/svg+xml', '.png': 'image/png', '.woff2': 'font/woff2' };
        res.setHeader('Content-Type', types[path.extname(file)] || 'application/octet-stream');
        fs.createReadStream(file).pipe(res);
      });
      await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
      await reference.goto(`http://127.0.0.1:${server.address().port}/__reference__/${encodeURIComponent(path.basename(sources[i]))}`, { waitUntil: 'load', timeout: 20000 });
      await reference.evaluate(() => document.fonts.ready);
      await reference.addStyleTag({ content: '*,*::before,*::after{animation:none!important;transition:none!important;caret-color:transparent!important}' });
      const file = path.join(output, `reference-${i}.png`);
      await reference.screenshot({ path: file, fullPage: true });
      screenshots.push({ path: file, sha256: crypto.createHash('sha256').update(fs.readFileSync(file)).digest('hex') });
      await new Promise(resolve => server.close(resolve)); server = undefined;
    }
    console.log(JSON.stringify({ url, viewport: { width: +width, height: +height }, screenshots, errors }));
  } finally {
    if (server) server.close();
    await browser.close();
  }
})().catch(e => { console.error(String(e)); process.exitCode = 1; });
