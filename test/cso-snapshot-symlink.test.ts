import { afterEach, describe, expect, spyOn, test } from 'bun:test';
import * as fs from 'node:fs';
import * as os from 'node:os';
import * as path from 'node:path';
import { spawnSync } from 'node:child_process';
import { OUTSIDE_AUDIT_PATHS, assertSnapshot, capture } from '../lib/cso/snapshot';
import { snapshotPathHandle, snapshotPathId } from '../lib/cso/contracts';

// Tracked symlinks (2026-11-08, change cso-symlink-alias): a symlink is recorded as a
// never-followed manifest entry. An in-repo alias of a regular source file is an
// `excluded:` entry (coverage stays assessed); every other symlink is a `withheld:` entry
// (coverage partial). Link text and target payloads never reach any snapshot output.

const posixOnly = test.skipIf(process.platform === 'win32');
const roots: string[] = [];
afterEach(() => {
  for (const root of roots.splice(0)) fs.rmSync(root, { recursive: true, force: true });
});

function fixture() {
  const root = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), 'cso-snapshot-symlink-')));
  roots.push(root);
  const repo = path.join(root, 'repo'),
    runDir = path.join(root, 'run');
  fs.mkdirSync(repo);
  fs.mkdirSync(runDir, { mode: 0o700 });
  const git = (...args: string[]) => {
    const result = spawnSync('/usr/bin/git', ['-C', repo, ...args], {
      encoding: 'utf8',
      env: { HOME: root, PATH: '/usr/bin:/bin', GIT_CONFIG_NOSYSTEM: '1' },
      timeout: 30_000,
    });
    if (result.status) throw new Error(result.stderr);
    return result.stdout.trim();
  };
  git('init', '-q');
  git('config', 'user.email', 'fixture@example.test');
  git('config', 'user.name', 'Fixture');
  fs.writeFileSync(path.join(repo, 'app.js'), 'export const secure = false\n');
  fs.writeFileSync(path.join(repo, 'CLAUDE.md'), 'project instructions\n');
  fs.writeFileSync(path.join(repo, 'README.md'), 'readme text\n');
  git('add', '.');
  git('commit', '-qm', 'base');
  const link = (name: string, target: string | Buffer) => {
    fs.mkdirSync(path.dirname(path.join(repo, name)), { recursive: true });
    fs.symlinkSync(target as string, path.join(repo, name));
  };
  return { root, repo, runDir, git, link };
}

const alias = (repo: string, target: string) => `excluded: symlink alias of ${snapshotPathHandle(snapshotPathId(repo, target))}`;
const unaudited = (reason: string) => `withheld: symlink not followed (${reason})`;
const entry = (manifest: Awaited<ReturnType<typeof capture>>, name: string) => manifest.entries.find((item) => item.path === name);

/** Every file under a directory, recursively, as [relative path, Stats from lstat]. */
function walk(dir: string, prefix = ''): Array<[string, fs.Stats]> {
  if (!fs.existsSync(dir)) return [];
  return fs.readdirSync(dir).flatMap((name) => {
    const full = path.join(dir, name),
      rel = prefix ? `${prefix}/${name}` : name,
      stat = fs.lstatSync(full);
    return stat.isDirectory() ? walk(full, rel) : [[rel, stat] as [string, fs.Stats]];
  });
}
function containsMarker(dir: string, marker: string): string[] {
  return walk(dir)
    .filter(([, stat]) => stat.isFile())
    .filter(([rel]) => fs.readFileSync(path.join(dir, rel)).includes(marker))
    .map(([rel]) => rel);
}
function noSymlinksInCopies(runDir: string) {
  for (const copy of ['snapshot', 'readable'])
    expect(walk(path.join(runDir, copy)).filter(([, stat]) => stat.isSymbolicLink())).toEqual([]);
}

describe('CSO snapshot tracked symlinks', () => {
  posixOnly('records an in-repo alias of a regular file as an excluded alias entry', async () => {
    const { repo, runDir, git, link } = fixture();
    link('AGENTS.md', 'CLAUDE.md');
    link('docs/guide.md', '../README.md');
    git('add', '.');
    git('commit', '-qm', 'aliases');
    const manifest = await capture(repo, runDir);
    expect(entry(manifest, 'AGENTS.md')).toMatchObject({ originalHash: 'not-read', transformation: alias(repo, 'CLAUDE.md') });
    expect(entry(manifest, 'docs/guide.md')).toMatchObject({ originalHash: 'not-read', transformation: alias(repo, 'README.md') });
    for (const name of ['AGENTS.md', 'docs/guide.md']) expect(entry(manifest, name)?.executionHash).toBeUndefined();
    // The target is still captured under its own path, exactly as without the alias.
    expect(entry(manifest, 'README.md')?.executionHash).toBeDefined();
    noSymlinksInCopies(runDir);
    expect(() => assertSnapshot(runDir, manifest)).not.toThrow();
  });

  // [description, symlink under test, arrange, expected reason]
  const cases: Array<[string, string, (f: ReturnType<typeof fixture>) => void, string]> = [
    ['absolute target', 'abs.md', ({ link }) => link('abs.md', '/etc/hosts'), 'absolute'],
    ['target escaping the repository', 'esc.md', ({ root, link }) => { fs.writeFileSync(path.join(root, 'outside.txt'), 'outside\n'); link('esc.md', '../outside.txt'); }, 'escapes the repository'],
    ['missing target', 'dangling.md', ({ link }) => link('dangling.md', 'nope.md'), 'missing target'],
    ['directory target', 'dir.md', ({ repo, link }) => { fs.mkdirSync(path.join(repo, 'lib')); fs.writeFileSync(path.join(repo, 'lib/x.js'), 'x\n'); link('dir.md', 'lib'); }, 'target is a directory'],
    ['chained symlink', 'chain.md', ({ link }) => { link('first.md', 'CLAUDE.md'); link('chain.md', 'first.md'); }, 'target is a symlink or crosses one'],
    ['target crossing a symlinked directory', 'cross.md', ({ repo, link }) => { fs.mkdirSync(path.join(repo, 'real')); fs.writeFileSync(path.join(repo, 'real/f.md'), 'f\n'); link('via', 'real'); link('cross.md', 'via/f.md'); }, 'target is a symlink or crosses one'],
    ['target outside the source inventory', 'hidden.md', ({ repo, link }) => { fs.writeFileSync(path.join(repo, '.gitignore'), 'ignored.md\n'); fs.writeFileSync(path.join(repo, 'ignored.md'), 'ignored\n'); link('hidden.md', 'ignored.md'); }, 'target not in the source inventory'],
    ['non-UTF-8 link text', 'binary.md', ({ link }) => link('binary.md', Buffer.from([0xff, 0xfe, 0x61])), 'link text is not UTF-8'],
    // `..` must not erase a component the kernel would have to traverse first.
    ['target through a missing directory', 'dotdot.md', ({ link }) => link('dotdot.md', 'missing/../README.md'), 'missing target'],
    ['target through a regular file', 'viafile.md', ({ link }) => link('viafile.md', 'README.md/../CLAUDE.md'), 'missing target'],
    ['target through a symlinked directory and back up', 'back.md', ({ repo, link }) => { fs.mkdirSync(path.join(repo, 'real/sub'), { recursive: true }); link('via', 'real/sub'); link('back.md', 'via/../CLAUDE.md'); }, 'target is a symlink or crosses one'],
  ];
  for (const [name, linkName, arrange, reason] of cases)
    posixOnly(`records a ${name} as an unaudited symlink`, async () => {
      const f = fixture();
      arrange(f);
      const manifest = await capture(f.repo, f.runDir);
      expect(entry(manifest, linkName)).toMatchObject({ originalHash: 'not-read', transformation: unaudited(reason) });
      expect(entry(manifest, linkName)?.executionHash).toBeUndefined();
      noSymlinksInCopies(f.runDir);
      expect(() => assertSnapshot(f.runDir, manifest)).not.toThrow();
    });

  posixOnly('never writes link text or a target payload into any snapshot output', async () => {
    const { root, repo, runDir, link } = fixture(),
      marker = `MARKER-${process.pid}-${Date.now()}`;
    fs.writeFileSync(path.join(root, `${marker}.txt`), `${marker} outside payload\n`);
    link('leak.md', `../${marker}.txt`);
    link('AGENTS.md', 'CLAUDE.md');
    await capture(repo, runDir);
    expect(containsMarker(runDir, marker)).toEqual([]);
    // Positive control: the same scan finds the marker when it is present.
    fs.writeFileSync(path.join(runDir, 'control.txt'), marker);
    expect(containsMarker(runDir, marker)).toEqual(['control.txt']);
  });

  posixOnly('still records an alias whose path climbs back through a real directory', async () => {
    const { repo, runDir, link } = fixture();
    link('docs/up.md', '../docs/../README.md');
    const manifest = await capture(repo, runDir);
    expect(entry(manifest, 'docs/up.md')).toMatchObject({ originalHash: 'not-read', transformation: alias(repo, 'README.md') });
  });

  posixOnly('keeps committed link text out of retained history', async () => {
    const { root, repo, runDir, git, link } = fixture(),
      marker = `HISTORY-MARKER-${process.pid}-${Date.now()}`;
    fs.writeFileSync(path.join(root, `${marker}.txt`), `${marker} outside payload\n`);
    link('leak.md', `../${marker}.txt`);
    link('AGENTS.md', 'CLAUDE.md');
    git('add', '.');
    git('commit', '-qm', 'links');
    await capture(repo, runDir);
    const history = fs.readFileSync(path.join(runDir, 'history.txt'), 'utf8');
    expect(history).toContain('diff withheld: leak.md (symlink)');
    // AGENTS.md is already withheld by its path (agent configuration); either reason keeps the text out.
    expect(history).toContain('diff withheld: AGENTS.md (');
    expect(containsMarker(runDir, marker)).toEqual([]);
  });

  posixOnly('binds each symlink target into the original source identity', async () => {
    const { root, repo, runDir, link } = fixture(),
      second = path.join(root, 'run-2'),
      third = path.join(root, 'run-3');
    fs.mkdirSync(second, { mode: 0o700 });
    fs.mkdirSync(third, { mode: 0o700 });
    link('docs/guide.md', '../CLAUDE.md');
    const first = await capture(repo, runDir),
      again = await capture(repo, second);
    expect(again.originalHash).toBe(first.originalHash);
    // Retarget the alias out of the repository: same path, same mode, different topology.
    fs.unlinkSync(path.join(repo, 'docs/guide.md'));
    link('docs/guide.md', '../../outside.md');
    const retargeted = await capture(repo, third);
    expect(retargeted.originalHash).not.toBe(first.originalHash);
    expect(() => assertSnapshot(third, retargeted)).not.toThrow();
    // The bound value is checked, not just carried: altering it breaks the identity.
    const tampered = structuredClone(first),
      bound = tampered.entries.find((item) => item.path === 'docs/guide.md') as { linkHash?: string };
    expect(bound.linkHash).toMatch(/^[a-f0-9]{64}$/);
    bound.linkHash = '0'.repeat(64);
    expect(() => assertSnapshot(runDir, tampered)).toThrow('Snapshot original identity is inconsistent');
  });

  posixOnly('gives coverage what it keys on: excluded for an alias, withheld for anything else', async () => {
    const { repo, runDir, link } = fixture();
    link('AGENTS.md', 'CLAUDE.md');
    link('esc.md', '../elsewhere.md');
    const manifest = await capture(repo, runDir),
      unread = manifest.entries.filter((item) => !item.executionHash && !item.transformation?.startsWith('excluded:'));
    expect(entry(manifest, 'AGENTS.md')?.transformation?.startsWith('excluded:')).toBe(true);
    expect(unread.map((item) => item.path)).toContain('esc.md');
    expect(unread.map((item) => item.path)).not.toContain('AGENTS.md');
  });

  posixOnly('records a symlink outside --paths or inside a dependency tree without refusing the run', async () => {
    const { repo, runDir, link } = fixture();
    link('AGENTS.md', 'CLAUDE.md');
    link('node_modules/pkg/index.js', '../../app.js');
    const manifest = await capture(repo, runDir, undefined, undefined, {}, ['app.js']);
    expect(entry(manifest, 'AGENTS.md')).toMatchObject({ originalHash: 'not-read', transformation: OUTSIDE_AUDIT_PATHS });
    expect(entry(manifest, 'node_modules/pkg/index.js')?.transformation?.startsWith('excluded:')).toBe(true);
  });

  posixOnly('hashes symlinks the way Git does, so diff mode sees only real symlink changes', async () => {
    const { repo, runDir, git, link } = fixture();
    link('AGENTS.md', 'CLAUDE.md');
    link('moved.md', 'CLAUDE.md');
    fs.writeFileSync(path.join(repo, 'tool.md'), 'regular file\n');
    git('add', '.');
    git('commit', '-qm', 'links');
    const base = git('rev-parse', 'HEAD');
    fs.unlinkSync(path.join(repo, 'moved.md'));
    link('moved.md', 'README.md');
    fs.unlinkSync(path.join(repo, 'tool.md'));
    link('tool.md', 'README.md');
    const manifest = await capture(repo, runDir, base);
    expect(manifest.changedPaths).not.toContain('AGENTS.md');
    expect(manifest.changedPaths).toContain('moved.md');
    expect(manifest.changedPaths).toContain('tool.md');
  });

  posixOnly('fails with SNAPSHOT_RACE when a symlink is retargeted during capture', async () => {
    const { repo, runDir, link } = fixture(),
      target = path.join(repo, 'docs', 'guide.md'),
      readlink = fs.readlinkSync;
    // Below the root on purpose: the repository-root identity guard would catch a root-level swap first.
    link('docs/guide.md', '../CLAUDE.md');
    let reads = 0, failure: unknown;
    const patched = spyOn(fs, 'readlinkSync').mockImplementation(((candidate: any, options?: any) => {
      if (String(candidate) === target && ++reads === 2) {
        fs.unlinkSync(target);
        fs.symlinkSync('../README.md', target);
      }
      return readlink(candidate, options);
    }) as typeof fs.readlinkSync);
    try { await capture(repo, runDir); } catch (error) { failure = error; } finally { patched.mockRestore(); }
    expect(reads).toBeGreaterThanOrEqual(2);
    expect(failure).toMatchObject({ code: 'SNAPSHOT_RACE', message: expect.stringContaining('docs/guide.md') });
  });

  posixOnly('fails with SNAPSHOT_RACE when a symlink is swapped for a regular file during capture', async () => {
    const { repo, runDir, link } = fixture(),
      target = path.join(repo, 'docs', 'guide.md'),
      readlink = fs.readlinkSync;
    // Below the root on purpose: the repository-root identity guard would catch a root-level swap first.
    link('docs/guide.md', '../CLAUDE.md');
    let reads = 0, failure: unknown;
    const patched = spyOn(fs, 'readlinkSync').mockImplementation(((candidate: any, options?: any) => {
      if (String(candidate) === target && ++reads === 2) {
        fs.unlinkSync(target);
        fs.writeFileSync(target, 'swapped in\n');
      }
      return readlink(candidate, options);
    }) as typeof fs.readlinkSync);
    try { await capture(repo, runDir); } catch (error) { failure = error; } finally { patched.mockRestore(); }
    expect(reads).toBeGreaterThanOrEqual(2);
    expect(failure).toMatchObject({ code: 'SNAPSHOT_RACE', message: expect.stringContaining('docs/guide.md') });
  });

  posixOnly('still refuses a FIFO next to a symlink', async () => {
    const { repo, runDir, link } = fixture();
    link('AGENTS.md', 'CLAUDE.md');
    expect(spawnSync('/usr/bin/mkfifo', [path.join(repo, 'request.pipe')], { timeout: 5_000 }).status).toBe(0);
    await expect(capture(repo, runDir)).rejects.toThrow('Symlink or special source file: request.pipe');
  });
});
