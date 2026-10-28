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
  const r = spawnSync('git', ['-c', 'user.name=t', '-c', 'user.email=t@t', ...args], { cwd, encoding: 'utf8' });
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
  const r = spawnSync(BIN, args, { cwd: repo, encoding: 'utf8', env: { ...process.env, ...env } });
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

  test('a missing focus file or bad arguments fail with usage, nothing on stdout', () => {
    for (const args of [[], ['main', '--focus-file', path.join(TMP, 'absent')], ['main', '--bogus']]) {
      const r = build(args);
      expect(r.status).not.toBe(0);
      expect(r.stdout).toBe('');
    }
  });
});
