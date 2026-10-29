/**
 * bin/gstack-codex-review-prompt builds the one prompt every structured Codex
 * review sends through `codex exec` (change 2026-10-28-codex-exec-review-gate,
 * R1-R5, R7). It fails closed: no stdout unless the whole prompt was built.
 */
import { afterAll, describe, expect, test } from 'bun:test';
import { spawnSync } from 'node:child_process';
import * as fs from 'node:fs';
import * as os from 'node:os';
import * as path from 'node:path';

const BIN = path.resolve(import.meta.dir, '..', 'bin', 'gstack-codex-review-prompt');
const TMP = fs.mkdtempSync(path.join(os.tmpdir(), 'codex-review-prompt-'));
afterAll(() => fs.rmSync(TMP, { recursive: true, force: true }));

function git(cwd: string, ...args: string[]) {
  const r = spawnSync('git', ['-c', 'user.name=t', '-c', 'user.email=t@t', ...args], { cwd, encoding: 'utf8', timeout: 30_000 });
  if (r.status !== 0) throw new Error(`git ${args.join(' ')}: ${r.stderr}`);
  return r.stdout.trim();
}
const repo = path.join(TMP, 'repo');
fs.mkdirSync(repo);
git(repo, 'init', '-q', '-b', 'main');
fs.writeFileSync(path.join(repo, 'a.ts'), 'export const a = 1;\n');
git(repo, 'add', '.');
git(repo, 'commit', '-q', '-m', 'base');
const baseSha = git(repo, 'rev-parse', 'HEAD');
git(repo, 'checkout', '-q', '-b', 'feature');
fs.writeFileSync(path.join(repo, 'a.ts'), 'export const a = 2;\n');
git(repo, 'commit', '-q', '-am', 'change');

function build(args: string[], env: Record<string, string> = {}) {
  const r = spawnSync(BIN, args, { cwd: repo, encoding: 'utf8', env: { ...process.env, ...env }, timeout: 30_000 });
  return { status: r.status, stdout: r.stdout, stderr: r.stderr };
}

describe('gstack-codex-review-prompt', () => {
  test('prints boundary, persona, contract, base and the delimited merge-base diff, in order', () => {
    const r = build(['main']);
    expect(r.status).toBe(0);
    const order = ['Do NOT read or execute any files under ~/.claude/', 'introduces', 'end your reply with a final line containing only NO_FINDINGS',
      `Base: ${baseSha}`, '\nDIFF_START\n', '+export const a = 2;', '\nDIFF_END\n'];
    const at = order.map(s => r.stdout.indexOf(s));
    expect(at.every(i => i >= 0)).toBe(true);
    expect([...at].sort((x, y) => x - y)).toEqual(at);
    expect(r.stdout).toContain('- [Pn] <title> — <path>:<line>');
    expect(r.stdout.trimEnd().endsWith('DIFF_END')).toBe(true);
  });

  test('a focus file goes before the diff, never inside it', () => {
    const focus = path.join(TMP, 'focus.txt');
    fs.writeFileSync(focus, 'check the counter overflow');
    const r = build(['main', '--focus-file', focus]);
    expect(r.status).toBe(0);
    const f = r.stdout.indexOf('check the counter overflow');
    expect(f).toBeGreaterThan(-1);
    expect(f).toBeLessThan(r.stdout.indexOf('\nDIFF_START\n'));
  });

  test('an unresolvable base fails closed with nothing on stdout', () => {
    const r = build(['no-such-branch']);
    expect(r.status).not.toBe(0);
    expect(r.stdout).toBe('');
    expect(r.stderr).toContain('no-such-branch');
  });

  test('a diff over the byte limit fails closed, naming the size and the limit', () => {
    const r = build(['main'], { GSTACK_CODEX_REVIEW_MAX_DIFF_BYTES: '10' });
    expect(r.status).not.toBe(0);
    expect(r.stdout).toBe('');
    expect(r.stderr).toMatch(/\d+ bytes.*limit 10/);
  });

  test('--commit reviews exactly that commit, not what came after it', () => {
    const changeSha = git(repo, 'rev-parse', 'HEAD');
    fs.writeFileSync(path.join(repo, 'later.ts'), 'export const later = true;\n');
    git(repo, 'add', 'later.ts');
    git(repo, 'commit', '-q', '-m', 'later');
    try {
      const r = build(['--commit', changeSha]);
      expect(r.status).toBe(0);
      expect(r.stdout).toContain(`Scope: commit ${changeSha}`);
      expect(r.stdout).toContain('+export const a = 2;');
      expect(r.stdout).not.toContain('later');
      expect(build(['--commit', 'deadbeef']).stdout).toBe('');
    } finally {
      git(repo, 'reset', '-q', '--hard', changeSha);
    }
  });

  test('--commit on a merge reviews everything it brought in against its first parent', () => {
    const head = git(repo, 'rev-parse', 'HEAD');
    git(repo, 'checkout', '-q', '-b', 'side', baseSha);
    fs.writeFileSync(path.join(repo, 'side.ts'), 'export const side = 1;\n');
    git(repo, 'add', 'side.ts');
    git(repo, 'commit', '-q', '-m', 'side');
    git(repo, 'checkout', '-q', 'feature');
    git(repo, 'merge', '-q', '--no-ff', '-m', 'merge side', 'side');
    try {
      const r = build(['--commit', 'HEAD']);
      expect(r.status).toBe(0);
      expect(r.stdout).toContain('+export const side = 1;');
      expect(r.stdout).not.toContain('+export const a = 2;');
    } finally {
      git(repo, 'reset', '-q', '--hard', head);
      git(repo, 'branch', '-q', '-D', 'side');
    }
  });

  test('--uncommitted reviews staged, unstaged and untracked changes', () => {
    fs.writeFileSync(path.join(repo, 'a.ts'), 'export const a = 3;\n');
    fs.writeFileSync(path.join(repo, 'staged.ts'), 'export const staged = 1;\n');
    git(repo, 'add', 'staged.ts');
    fs.writeFileSync(path.join(repo, 'untracked.ts'), 'export const untracked = 1;\n');
    try {
      const r = build(['--uncommitted']);
      expect(r.status).toBe(0);
      expect(r.stdout).toContain('Scope: uncommitted changes against HEAD');
      for (const s of ['+export const a = 3;', '+export const staged = 1;', '+export const untracked = 1;']) expect(r.stdout).toContain(s);
      expect(r.stdout).not.toContain('+export const a = 2;');
    } finally {
      git(repo, 'reset', '-q', '--hard', 'HEAD');
      fs.rmSync(path.join(repo, 'untracked.ts'), { force: true });
    }
  });

  test('an empty diff is never a review: base at HEAD and an empty commit fail closed', () => {
    for (const r of [build(['HEAD']), build(['--uncommitted'])]) {
      expect(r.status).not.toBe(0);
      expect(r.stdout).toBe('');
      expect(r.stderr).toContain('empty diff');
    }
    git(repo, 'commit', '-q', '--allow-empty', '-m', 'empty');
    try {
      const r = build(['--commit', 'HEAD']);
      expect(r.status).not.toBe(0);
      expect(r.stdout).toBe('');
    } finally {
      git(repo, 'reset', '-q', '--hard', 'HEAD~1');
    }
  });

  test('base mode includes untracked files, from any directory', () => {
    fs.mkdirSync(path.join(repo, 'sub'), { recursive: true });
    fs.writeFileSync(path.join(repo, 'new-root.ts'), 'export const fresh = 1;\n');
    try {
      for (const cwd of [repo, path.join(repo, 'sub')]) {
        const r = spawnSync(BIN, ['main'], { cwd, encoding: 'utf8', timeout: 30_000 });
        expect(r.status).toBe(0);
        expect(r.stdout).toContain('+export const fresh = 1;');
        expect(r.stdout).toContain('+export const a = 2;');
      }
    } finally {
      fs.rmSync(path.join(repo, 'new-root.ts'), { force: true });
      fs.rmSync(path.join(repo, 'sub'), { recursive: true, force: true });
    }
  });

  test('an untracked symlink is shown as its target, not dropped with an error', () => {
    fs.symlinkSync(TMP, path.join(repo, 'linked-dir'));
    try {
      const r = build(['main']);
      expect(r.status).toBe(0);
      expect(r.stderr).toBe('');
      expect(r.stdout).toContain('new file mode 120000');
      expect(r.stdout).toContain(`+${TMP}`);
    } finally {
      fs.rmSync(path.join(repo, 'linked-dir'), { force: true });
    }
  });

  test('user diff config (external diff, textconv) cannot replace the patch', () => {
    const r = build(['main'], { GIT_CONFIG_COUNT: '1', GIT_CONFIG_KEY_0: 'diff.external', GIT_CONFIG_VALUE_0: '/bin/echo' });
    expect(r.status).toBe(0);
    expect(r.stdout).toContain('+export const a = 2;');
  });

  test('a missing focus file or bad arguments fail with usage, nothing on stdout', () => {
    for (const args of [[], ['main', '--focus-file', path.join(TMP, 'absent')], ['main', '--bogus'],
      ['main', '--uncommitted'], ['--commit'], ['--commit', 'HEAD', 'main']]) {
      const r = build(args);
      expect(r.status).not.toBe(0);
      expect(r.stdout).toBe('');
    }
  });
});
