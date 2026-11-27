// Chromium's managed policies for this stack: the image's own (policies.json)
// plus, in URLBlocklist, every host of LOLLO_DENY_HOSTS (every stack's Lettuce
// and lollo hosts and the Access team domain, from lolloctl's deny_hosts), so a
// task can never open the person's own pages and complete their Access sign-in
// (browser/README.md, "Managed policies"). Pipelock refuses the same hosts; this
// is the second layer, with Chromium's "blocked" page. The launcher writes the
// file before Chromium starts, and refuses to start when it can't.
import { chmodSync, mkdirSync, readFileSync, writeFileSync } from 'node:fs';
import { dirname } from 'node:path';

export const DENY_VAR = 'LOLLO_DENY_HOSTS';
// The image's policies, and where Chromium reads them: the image links
// /etc/opt/chrome_for_testing/policies/managed/lollo.json to this file.
export const BASE_FILE = '/opt/engine/policies.json';
export const POLICY_FILE = '/tmp/policies/lollo.json';
export const MAX_DENY = 500;
const HOST = /^[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+$/;

// parseDenyHosts VALUE -> the host names of a comma-separated list; throws on
// an empty value, an empty entry, anything but a lower-case host name with a
// dot, or more than MAX_DENY entries
export function parseDenyHosts(value) {
  if (!value) throw new Error(`${DENY_VAR} is not set; lolloctl passes every stack's hosts in it`);
  const hosts = value.split(',');
  for (const host of hosts) {
    if (!HOST.test(host)) {
      throw new Error(`${DENY_VAR} must be comma-separated lower-case host names; not '${host}'`);
    }
  }
  if (hosts.length > MAX_DENY) throw new Error(`${DENY_VAR} has more than ${MAX_DENY} hosts`);
  return hosts;
}

// withDenyHosts BASE HOSTS -> BASE with each host added to URLBlocklist as
// ".<host>", which blocks that host only, never its parent's other sites
export function withDenyHosts(base, hosts) {
  const blocklist = Array.isArray(base.URLBlocklist) ? base.URLBlocklist : [];
  return { ...base, URLBlocklist: [...blocklist, ...hosts.map((host) => `.${host}`)] };
}

// writePolicies ENV -> writes the stack's policies where Chromium reads them;
// throws when the list is bad or the file can't be written
export function writePolicies(env, { base = BASE_FILE, out = POLICY_FILE } = {}) {
  const hosts = parseDenyHosts(env[DENY_VAR] ?? '');
  const policies = withDenyHosts(JSON.parse(readFileSync(base, 'utf8')), hosts);
  mkdirSync(dirname(out), { recursive: true });
  chmodSync(dirname(out), 0o755);
  writeFileSync(out, `${JSON.stringify(policies, null, 2)}\n`);
  chmodSync(out, 0o644);
  return policies;
}
