'use strict';

// The browser cannot add Strapi's Authorization header to <img> requests.
// Seal the short-lived admin access token into a host-only cookie that grants
// only media-preview checks. The actual administrator token never leaves CMS
// in plaintext through this cookie, and Strapi remains the authority for both
// session revocation and per-file upload permissions.
const crypto = require('node:crypto');
const COOKIE = '__Host-shirone-admin-media';
const HOST = 'strapi.hyperbola.cc';
const CHECK_PATH = '/admin/shirone-media/check';
const PURPOSE = 'shirone-admin-media-preview-v1';
const MAX_AGE_SECONDS = 30 * 60;

function key(secret) {
  if (typeof secret !== 'string' || secret.length < 32) throw new Error('ADMIN_JWT_SECRET must be at least 32 characters');
  return crypto.createHmac('sha256', secret).update(PURPOSE).digest();
}
function seal(token, secret, now = Date.now()) {
  if (typeof token !== 'string' || token.length > 8192) return null;
  let payload;
  try { payload = JSON.parse(Buffer.from(token.split('.')[1], 'base64url').toString('utf8')); } catch { return null; }
  const expiry = Math.min(Number(payload.exp) * 1000, now + MAX_AGE_SECONDS * 1000);
  if (!Number.isFinite(expiry) || expiry <= now) return null;
  const iv = crypto.randomBytes(12);
  const cipher = crypto.createCipheriv('aes-256-gcm', key(secret), iv);
  cipher.setAAD(Buffer.from(PURPOSE));
  const data = Buffer.concat([cipher.update(JSON.stringify({ token, expiry }), 'utf8'), cipher.final()]);
  return Buffer.concat([iv, cipher.getAuthTag(), data]).toString('base64url');
}
function unseal(value, secret, now = Date.now()) {
  if (typeof value !== 'string' || !/^[A-Za-z0-9_-]{1,12000}$/.test(value)) return null;
  try {
    const bytes = Buffer.from(value, 'base64url');
    if (bytes.length < 29 || bytes.toString('base64url') !== value) return null;
    const decipher = crypto.createDecipheriv('aes-256-gcm', key(secret), bytes.subarray(0, 12));
    decipher.setAAD(Buffer.from(PURPOSE));
    decipher.setAuthTag(bytes.subarray(12, 28));
    const data = JSON.parse(Buffer.concat([decipher.update(bytes.subarray(28)), decipher.final()]).toString('utf8'));
    return typeof data.token === 'string' && Number.isFinite(data.expiry) && data.expiry > now ? data.token : null;
  } catch { return null; }
}
function cookieValue(header) {
  if (typeof header !== 'string' || header.length > 16384) return null;
  const found = header.split(';').map((v) => v.trim()).filter((v) => v.startsWith(`${COOKIE}=`));
  return found.length === 1 ? found[0].slice(COOKIE.length + 1) : null;
}
function mediaPath(value) {
  return typeof value === 'string' && /^\/uploads\/[A-Za-z0-9][A-Za-z0-9._-]*\.(?:png|jpe?g|webp|avif|gif|svg|mp4|webm|mp3|ogg|wav|pdf)$/i.test(value) ? value : null;
}
function matchesFile(file, path) {
  return file && (file.url === path || (file.formats && typeof file.formats === 'object' && Object.values(file.formats).some((format) => format && format.url === path)));
}
async function allowedByStrapi(token, path, port, fetcher = fetch) {
  // This admin API performs the normal active-session check, upload.read RBAC,
  // and conditional permission filtering. Never use a privileged service token.
  for (let page = 1; page <= 1000; page++) {
    const url = new URL(`http://127.0.0.1:${port}/upload/files`);
    url.searchParams.set('pagination[page]', String(page));
    url.searchParams.set('pagination[pageSize]', '100');
    const response = await fetcher(url, { headers: { authorization: `Bearer ${token}` }, redirect: 'error', signal: AbortSignal.timeout(5000) });
    if (!response.ok) return false;
    const data = await response.json();
    if (!Array.isArray(data?.results) || !Number.isInteger(data?.pagination?.pageCount)) return false;
    if (data.results.some((file) => matchesFile(file, path))) return true;
    if (page >= data.pagination.pageCount) return false;
  }
  return false;
}
module.exports = (_config, { strapi }) => async (ctx, next) => {
  const secret = strapi.config.get('admin.auth.secret');
  if (ctx.path === CHECK_PATH) {
    ctx.set('Cache-Control', 'no-store, private');
    ctx.set('Cross-Origin-Resource-Policy', 'same-origin');
    ctx.status = 404;
    if (ctx.method !== 'GET') return;
    const path = mediaPath(ctx.query.path);
    const token = unseal(cookieValue(ctx.get('cookie')), secret);
    if (!path || !token) return;
    try { if (await allowedByStrapi(token, path, strapi.config.get('server.port', 1337))) ctx.status = 204; } catch { /* fail closed */ }
    return;
  }
  await next();
  if (ctx.hostname !== HOST || !ctx.secure || ctx.method !== 'POST') return;
  const options = { httpOnly: true, secure: true, sameSite: 'strict', path: '/', signed: false, overwrite: true };
  if (ctx.path === '/admin/logout') {
    ctx.cookies.set(COOKIE, '', { ...options, expires: new Date(0) });
  } else if (['/admin/login', '/admin/access-token', '/admin/reset-password', '/admin/register', '/admin/register-admin'].includes(ctx.path) && ctx.status >= 200 && ctx.status < 300) {
    const value = seal(ctx.body?.data?.token, secret);
    if (value) ctx.cookies.set(COOKIE, value, { ...options, maxAge: MAX_AGE_SECONDS * 1000 });
  }
};
module.exports._test = { seal, unseal, cookieValue, mediaPath, matchesFile, allowedByStrapi, COOKIE };
