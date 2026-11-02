'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const { seal, unseal, cookieValue, mediaPath, matchesFile, allowedByStrapi, COOKIE } = require('../src/middlewares/admin-media')._test;
const secret = 'local-test-only-secret-for-admin-media-123456789';
const token = (expiry) => `header.${Buffer.from(JSON.stringify({ exp: expiry })).toString('base64url')}.signature`;
test('sealed capability does not disclose its token and rejects forgery, wrong key and expiration', () => {
  const now = 1900000000000;
  const jwt = token(now / 1000 + 3600);
  const value = seal(jwt, secret, now);
  assert.ok(value && !value.includes(jwt));
  assert.equal(unseal(value, secret, now), jwt);
  assert.equal(unseal(value, secret, now + 1800_001), null);
  assert.equal(unseal(value, secret + 'x', now), null);
  const bytes = Buffer.from(value, 'base64url'); bytes[30] ^= 1;
  assert.equal(unseal(bytes.toString('base64url'), secret, now), null);
  assert.equal(seal(token(now / 1000 - 1), secret, now), null);
});
test('sealed capability rejects noncanonical encodings of identical bytes', () => {
  const now = 1900000000000;
  const alphabet = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_';
  // Vary token length to obtain a base64url encoding with unused trailing bits.
  for (let extra = 0; extra < 3; extra++) {
    const jwt = `${token(now / 1000 + 3600)}${'x'.repeat(extra)}`;
    const value = seal(jwt, secret, now);
    if (value.length % 4 === 0) continue;
    const last = alphabet.indexOf(value.at(-1));
    const alias = value.slice(0, -1) + alphabet[last + 1];
    assert.notEqual(alias, value);
    assert.deepEqual(Buffer.from(alias, 'base64url'), Buffer.from(value, 'base64url'));
    assert.equal(unseal(value, secret, now), jwt);
    assert.equal(unseal(alias, secret, now), null);
    return;
  }
  assert.fail('could not construct a noncanonical base64url alias');
});
test('cookie and media paths reject ambiguous inputs', () => {
  assert.equal(cookieValue(`a=b; ${COOKIE}=abc`), 'abc');
  assert.equal(cookieValue(`${COOKIE}=a; ${COOKIE}=b`), null);
  assert.equal(mediaPath('/uploads/a.png'), '/uploads/a.png');
  for (const value of ['/uploads/../a.png', '/uploads/%61.png', '/uploads/a.txt', '/uploads/a.png?x=1', ['/uploads/a.png']]) assert.equal(mediaPath(value), null);
  assert.equal(matchesFile({ formats: { small: { url: '/uploads/small_x.png' } } }, '/uploads/small_x.png'), true);
});
test('permission check delegates to Strapi and honors pagination and failures', async () => {
  const calls = [];
  const fetcher = async (url, init) => {
    calls.push([url, init]);
    const page = Number(url.searchParams.get('pagination[page]'));
    return { ok: true, json: async () => ({ results: page === 1 ? [] : [{ url: '/uploads/owned.png' }], pagination: { pageCount: 2 } }) };
  };
  assert.equal(await allowedByStrapi('token', '/uploads/owned.png', 1337, fetcher), true);
  assert.equal(calls.length, 2);
  assert.equal(calls[0][1].headers.authorization, 'Bearer token');
  assert.equal(await allowedByStrapi('revoked', '/uploads/owned.png', 1337, async () => ({ ok: false })), false);
  assert.equal(await allowedByStrapi('token', '/uploads/owned.png', 1337, async () => ({ ok: true, json: async () => ({}) })), false);
});
