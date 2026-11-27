// The policy file the launcher writes (policy.mjs). Plain functions on temp
// files, so it runs on the host too: node --test browser/engine/tests/policy.test.mjs
import { test } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { MAX_DENY, parseDenyHosts, withDenyHosts, writePolicies } from '../policy.mjs';

const BASE = {
  PasswordManagerEnabled: false,
  URLBlocklist: ['file://*', 'devtools://*', 'view-source:*', 'javascript:*'],
};

test('a list of host names is taken', () => {
  assert.deepEqual(parseDenyHosts('alfred.example.com,alfred-lollo.example.com,team.cloudflareaccess.com'),
    ['alfred.example.com', 'alfred-lollo.example.com', 'team.cloudflareaccess.com']);
});

test('a missing or malformed list is refused', () => {
  for (const value of ['', undefined, 'alfred.example.com,', ',alfred.example.com', 'a.example.com,,b.example.com',
    'Alfred.example.com', 'localhost', 'alfred.example.com/vault', 'https://alfred.example.com', '*.example.com',
    'alfred.example.com:443', 'alfred .example.com']) {
    assert.throws(() => parseDenyHosts(value), /LOLLO_DENY_HOSTS/, String(value));
  }
});

test('more than the most hosts is refused', () => {
  const hosts = Array.from({ length: MAX_DENY + 1 }, (_, i) => `h${i}.example.com`);
  assert.throws(() => parseDenyHosts(hosts.join(',')), /more than/);
  assert.equal(parseDenyHosts(hosts.slice(1).join(',')).length, MAX_DENY);
});

test('each host is blocked alone, after the image policies', () => {
  const out = withDenyHosts(BASE, ['alfred-lollo.example.com']);
  assert.deepEqual(out.URLBlocklist, [...BASE.URLBlocklist, '.alfred-lollo.example.com']);
  assert.equal(out.PasswordManagerEnabled, false);
  assert.deepEqual(BASE.URLBlocklist.length, 4, 'the base is not changed');
});

test('the file is written with the base and the hosts, readable by Chromium', () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'policy-'));
  try {
    const base = path.join(dir, 'policies.json');
    const out = path.join(dir, 'policies', 'lollo.json');
    fs.writeFileSync(base, JSON.stringify(BASE));
    writePolicies({ LOLLO_DENY_HOSTS: 'alfred.example.com' }, { base, out });
    const written = JSON.parse(fs.readFileSync(out, 'utf8'));
    assert.deepEqual(written.URLBlocklist, [...BASE.URLBlocklist, '.alfred.example.com']);
    assert.equal(fs.statSync(out).mode & 0o777, 0o644);
    assert.throws(() => writePolicies({}, { base, out: path.join(dir, 'other', 'lollo.json') }), /not set/);
    assert.equal(fs.existsSync(path.join(dir, 'other', 'lollo.json')), false, 'nothing written for a bad list');
  } finally {
    fs.rmSync(dir, { recursive: true, force: true });
  }
});
