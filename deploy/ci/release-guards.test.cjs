'use strict';

// Offline fail-closed checks: all GitHub, Git and Docker calls are fakes.
const { test } = require('node:test');
const assert = require('node:assert/strict');
const { mkdtempSync, writeFileSync, existsSync, rmSync } = require('node:fs');
const { tmpdir } = require('node:os');
const path = require('node:path');
const { spawnSync } = require('node:child_process');

const root = path.resolve(__dirname, '../..');
const sha = 'a'.repeat(40);
const digest = `sha256:${'b'.repeat(64)}`;

function withMocks(run) {
  const dir = mkdtempSync(path.join(tmpdir(), 'shirone-ci-guards-'));
  try {
    for (const [name, body] of Object.entries({
      git: '#!/bin/sh\nprintf "%s\\n" "$MOCK_GIT_SHA"\n',
      gh: '#!/bin/sh\ncase "$2" in repos/*) printf "%s\\n" "$MOCK_REMOTE_SHA";; user/packages/container/shirone-ssr) [ "$MOCK_VISIBILITY" != inaccessible ] || exit 1; printf "%s\\n" "$MOCK_VISIBILITY";; *) printf "private\\n";; esac\n',
      docker: '#!/bin/sh\nprintf "called\\n" > "$MOCK_DOCKER_MARKER"\nexit 0\n',
    })) writeFileSync(path.join(dir, name), body, { mode: 0o700 });
    const env = {
      PATH: `${dir}:/usr/bin:/bin`, HOME: dir, GH_TOKEN: 'synthetic',
      GITHUB_SHA: sha, GITHUB_REPOSITORY: 'Hyperbola-QAQ/Shirone-Strapi',
      GITHUB_REF: 'refs/heads/main', GITHUB_ACTOR: 'synthetic',
      GITHUB_OUTPUT: path.join(dir, 'output'), RUNNER_TEMP: dir,
      DOCKER_CONFIG: path.join(dir, 'docker-config'), MOCK_GIT_SHA: sha,
      MOCK_REMOTE_SHA: sha, MOCK_VISIBILITY: 'private',
      MOCK_DOCKER_MARKER: path.join(dir, 'docker-called'),
      CMS_REPO: 'ghcr.io/hyperbola-qaq/shirone-cms',
      SSR_REPO: 'ghcr.io/hyperbola-qaq/shirone-ssr',
      RECONCILER_REPO: 'ghcr.io/hyperbola-qaq/shirone-reconciler',
      CMS_DIGEST: digest, SSR_DIGEST: digest, RECONCILER_DIGEST: digest,
      POSTGRES_TEST_IMAGE: 'synthetic', MEILI_TEST_IMAGE: 'synthetic',
      NODE_IMAGE: 'synthetic', GITHUB_RUN_ID: '1', GITHUB_RUN_ATTEMPT: '1',
    };
    const call = (file, overrides = {}) => spawnSync('bash', [path.join(root, 'deploy/ci', file)], {
      cwd: root, env: { ...env, ...overrides }, encoding: 'utf8', timeout: 5000,
    });
    run({ dir, env, call });
  } finally { rmSync(dir, { recursive: true, force: true }); }
}

test('preflight rejects wrong branch and either stale SHA before registry operations', () => withMocks(({ env, call }) => {
  for (const overrides of [
    { GITHUB_REF: 'refs/heads/archive/mizuki' },
    { GITHUB_SHA: 'not-a-sha' },
    { MOCK_GIT_SHA: 'c'.repeat(40) },
    { MOCK_REMOTE_SHA: 'd'.repeat(40) },
  ]) assert.notEqual(call('release-preflight.sh', overrides).status, 0);
  assert.equal(existsSync(env.MOCK_DOCKER_MARKER), false);
}));

test('preflight rejects public or inaccessible packages and accepts only verified private packages', () => withMocks(({ call }) => {
  assert.notEqual(call('release-preflight.sh', { MOCK_VISIBILITY: 'public' }).status, 0);
  assert.notEqual(call('release-preflight.sh', { MOCK_VISIBILITY: 'inaccessible' }).status, 0);
  assert.equal(call('release-preflight.sh').status, 0);
}));

test('test stage rejects an invalid digest before registry login or pull', () => withMocks(({ env, call }) => {
  assert.notEqual(call('release-test-candidate.sh', { SSR_DIGEST: 'sha256:invalid' }).status, 0);
  assert.equal(existsSync(env.MOCK_DOCKER_MARKER), false);
}));

test('build stage rejects arbitrary registry destinations before registry login', () => withMocks(({ env, call }) => {
  assert.notEqual(call('release-build-candidate.sh', { CMS_REPO: 'example.invalid/injected' }).status, 0);
  assert.equal(existsSync(env.MOCK_DOCKER_MARKER), false);
}));
