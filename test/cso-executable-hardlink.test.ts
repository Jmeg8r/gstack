import { afterAll, describe, expect, test } from 'bun:test';
import * as fs from 'node:fs';
import * as os from 'node:os';
import * as path from 'node:path';
import { spawnSync } from 'node:child_process';
import { canonicalExecutable, childEnvironment, executable, git } from '../lib/cso/process';

// macOS ships /usr/bin/git as one of many hard links to the xcode-select shim, which
// picks its tool from its own path. A kernel path lookup (Bun's realpathSync) can name
// any sibling link, such as /usr/bin/python3, so the helper ran the wrong tool.
const SHIM = '/usr/bin/git';
const shimIsHardLinked = process.platform === 'darwin' && fs.existsSync(SHIM) && fs.statSync(SHIM).nlink > 1;
const STRESS_CALLS = 20_000;
const ALTERNATING_CALLS = 5_000;
const HELPER_GIT_CALLS = 2_000;
const LOOP_TIMEOUT_MS = 60_000;

const roots: string[] = [];
afterAll(() => {
  for (const root of roots.splice(0)) fs.rmSync(root, { recursive: true, force: true });
});
function tmp(): string {
  const root = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), 'cso-exec-link-')));
  roots.push(root);
  return root;
}

describe('CSO executable resolution keeps the hard-link name it was given', () => {
  test.skipIf(!shimIsHardLinked)('executable("git") never names a sibling of the xcode-select shim', () => {
    const first = executable('git'),
      names = new Set<string>();
    for (let i = 0; i < STRESS_CALLS; i++) names.add(executable('git'));
    expect([...names]).toEqual([first]);
    expect(path.basename(first)).toBe('git');
    expect(canonicalExecutable(SHIM)).toBe(SHIM);
  }, LOOP_TIMEOUT_MS);

  test.skipIf(process.platform === 'win32')('each hard link of one inode resolves to its own path', () => {
    const root = tmp(),
      tool = path.join(root, 'tool'),
      alias = path.join(root, 'alias');
    fs.writeFileSync(tool, '#!/bin/sh\n', { mode: 0o755 });
    fs.linkSync(tool, alias);
    const wrong: string[] = [];
    for (let i = 0; i < ALTERNATING_CALLS; i++) {
      const name = i % 2 ? alias : tool,
        resolved = canonicalExecutable(name);
      if (resolved !== name) wrong.push(resolved);
    }
    expect(wrong).toEqual([]);
  }, LOOP_TIMEOUT_MS);

  test.skipIf(process.platform === 'win32')('symlinks still resolve to their targets', () => {
    const root = tmp(),
      cellar = path.join(root, 'Cellar', 'git', '1.0', 'bin');
    fs.mkdirSync(cellar, { recursive: true });
    const real = path.join(cellar, 'git');
    fs.writeFileSync(real, '#!/bin/sh\n', { mode: 0o755 });
    fs.mkdirSync(path.join(root, 'bin'));
    // Relative, Homebrew style.
    fs.symlinkSync('../Cellar/git/1.0/bin/git', path.join(root, 'bin', 'git'));
    expect(canonicalExecutable(path.join(root, 'bin', 'git'))).toBe(real);
    // Chain ending in an absolute target.
    fs.symlinkSync(real, path.join(root, 'bin', 'abs'));
    fs.symlinkSync('abs', path.join(root, 'bin', 'chain'));
    expect(canonicalExecutable(path.join(root, 'bin', 'chain'))).toBe(real);
    // Symlinked parent directory, with a relative target that climbs out of it.
    fs.symlinkSync(cellar, path.join(root, 'linked-bin'));
    fs.symlinkSync('../../../../bin/git', path.join(cellar, 'up'));
    expect(canonicalExecutable(path.join(root, 'linked-bin', 'up'))).toBe(real);
    // A loop fails instead of spinning.
    fs.symlinkSync('loop-b', path.join(root, 'loop-a'));
    fs.symlinkSync('loop-a', path.join(root, 'loop-b'));
    expect(() => canonicalExecutable(path.join(root, 'loop-a'))).toThrow();
  });

  test('helper Git calls are never answered by another tool', async () => {
    const root = tmp(),
      repo = path.join(root, 'repo'),
      home = path.join(root, 'home');
    fs.mkdirSync(repo);
    fs.mkdirSync(home);
    const init = spawnSync(executable('git'), ['init', '-q', repo], { env: childEnvironment(home), encoding: 'utf8' });
    expect(init.status).toBe(0);
    const failures = new Map<string, number>();
    for (let i = 0; i < HELPER_GIT_CALLS; i++) {
      try {
        const format = (await git(repo, ['rev-parse', '--show-object-format'], home)).trim();
        if (!/^sha(?:1|256)$/.test(format)) failures.set(format, (failures.get(format) ?? 0) + 1);
      } catch (error) {
        const message = String((error as Error).message);
        failures.set(message, (failures.get(message) ?? 0) + 1);
      }
    }
    expect(Object.fromEntries(failures)).toEqual({});
  }, 2 * LOOP_TIMEOUT_MS);
});
