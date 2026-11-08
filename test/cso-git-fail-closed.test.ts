import { afterEach, describe, expect, spyOn, test } from 'bun:test';
import * as fs from 'node:fs';
import * as os from 'node:os';
import * as path from 'node:path';
import { CsoError } from '../lib/cso/contracts';
import * as processModule from '../lib/cso/process';
import { childEnvironment, executable, runProcess } from '../lib/cso/process';

// hardenGit() used to return a Git-named child's arguments unchanged when the
// file was not the trusted Git, so a lookalike ran without core.fsmonitor,
// core.hooksPath or include.path pinned. These cases pin the fail-closed rule.
const roots: string[] = [];
afterEach(() => {
  for (const root of roots.splice(0)) fs.rmSync(root, { recursive: true, force: true });
});

function fixture() {
  const root = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), 'cso-git-fail-closed-'))),
    repo = path.join(root, 'repo'),
    marker = path.join(root, 'spawned');
  roots.push(root);
  fs.mkdirSync(repo);
  return { root, repo, marker };
}

function lookalike(root: string, name: string, marker: string): string {
  const dir = path.join(root, 'bin'),
    file = path.join(dir, name);
  fs.mkdirSync(dir, { recursive: true });
  fs.writeFileSync(file, `#!/bin/sh\n: > '${marker}'\n`, { mode: 0o755 });
  return file;
}

describe('CSO Git hardening fails closed', () => {
  for (const name of ['git', 'git.exe', 'GIT'])
    test.skipIf(process.platform === 'win32')(`refuses an untrusted executable named ${name} without spawning it`, async () => {
      const { root, repo, marker } = fixture(),
        file = lookalike(root, name, marker);
      await expect(
        runProcess(file, ['--no-optional-locks', '-C', repo, 'status'], { cwd: root, env: childEnvironment(root), raw: true }),
      ).rejects.toMatchObject({ code: 'UNSAFE_PATH' });
      expect(fs.existsSync(marker)).toBe(false);
    });

  // Hardening refuses a trusted Git call with no -C, so INVALID_ARGUMENT proves the
  // symlink took the hardened path rather than being spawned as-is or refused.
  test.skipIf(process.platform === 'win32')('still hardens a symlink named git that resolves to the trusted Git', async () => {
    const { root } = fixture(),
      link = path.join(root, 'git');
    fs.symlinkSync(executable('git'), link);
    await expect(
      runProcess(link, ['rev-parse', '--is-inside-work-tree'], { cwd: root, env: childEnvironment(root), raw: true }),
    ).rejects.toMatchObject({ code: 'INVALID_ARGUMENT' });
  });

  test.skipIf(process.platform === 'win32')('refuses a Git-named child when the trusted Git cannot be resolved', async () => {
    const { root, repo, marker } = fixture(),
      file = lookalike(root, 'git', marker),
      missing = spyOn(processModule, 'executable').mockImplementation(() => {
        throw new CsoError('TOOL_UNAVAILABLE', 'git is not installed');
      });
    try {
      await expect(
        runProcess(file, ['--no-optional-locks', '-C', repo, 'status'], { cwd: root, env: childEnvironment(root), raw: true }),
      ).rejects.toMatchObject({ code: 'TOOL_UNAVAILABLE' });
    } finally {
      missing.mockRestore();
    }
    expect(fs.existsSync(marker)).toBe(false);
  });
});
