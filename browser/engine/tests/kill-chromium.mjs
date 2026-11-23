// Kills Chromium's browser process inside the engine container, so run.sh can
// check that the launcher then exits 1.
import fs from 'node:fs';

let killed = 0;
for (const pid of fs.readdirSync('/proc').filter((d) => /^\d+$/.test(d))) {
  let argv;
  try {
    argv = fs.readFileSync(`/proc/${pid}/cmdline`, 'utf8').split(/[\0 ]/);
  } catch {
    continue;
  }
  if (argv[0].endsWith('/chrome') && !argv.some((a) => a.startsWith('--type='))) {
    process.kill(Number(pid), 'SIGKILL');
    killed++;
  }
}
if (killed !== 1) {
  console.error(`expected one Chromium browser process, killed ${killed}`);
  process.exit(1);
}
