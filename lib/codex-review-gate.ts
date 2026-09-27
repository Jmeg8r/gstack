/**
 * Verdict for /codex review (Step 2A, check 4). Fails closed: PASS is only
 * returned from a verified clean or advisory-only result.
 *
 * Native `codex review` tags findings only when it has some, so a clean review
 * is untagged prose whose wording varies run to run. The authoritative verdict
 * is the structured ReviewOutputEvent (`findings[].priority`,
 * `overall_correctness`) that codex writes to the session rollout named by the
 * `session id:` line on stderr. Text tags are the fallback for runs with no
 * structured verdict (the custom-instructions `codex exec` path).
 */
import { readdirSync, readFileSync } from 'node:fs';
import { homedir } from 'node:os';
import { join } from 'node:path';

export type GateResult = {
  pass: boolean;
  reason: string;
  source: 'exit' | 'empty' | 'structured' | 'text';
  findings: number;
};

export type StructuredVerdict = {
  findings: Array<{ priority: number }>;
  overall_correctness: string;
};

const CRITICAL_MAX_PRIORITY = 1;
const MAX_PRIORITY = 3;
const CORRECT = 'patch is correct';
const INCORRECT = 'patch is incorrect';
const NO_FINDINGS_SENTINEL = 'NO_FINDINGS';
const SESSION_ID_LINE = /^session id:[\t ]*([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})[\t ]*$/gim;
const BRACKET_TAG = /\[P([0-9])\]/g;
const NATIVE_TAG = /^[\t ]*(?:[-*+][\t ]+)?P([0-9]):/gm;

export function sessionIdFromStderr(stderr: string): string | null {
  const ids = new Set([...stderr.matchAll(SESSION_ID_LINE)].map(m => m[1].toLowerCase()));
  return ids.size === 1 ? [...ids][0] : null;
}

export function findRollout(codexHome: string, sessionId: string): string | null {
  const sessions = join(codexHome, 'sessions');
  let entries: string[];
  try {
    entries = readdirSync(sessions, { recursive: true }) as string[];
  } catch {
    return null;
  }
  const suffix = `-${sessionId}.jsonl`;
  const hits = entries.filter(p => p.endsWith(suffix) && p.split(/[\\/]/).pop()!.startsWith('rollout-'));
  return hits.length === 1 ? join(sessions, hits[0]) : null;
}

/** Every distinct object carrying `overall_correctness` anywhere in the rollout. */
export function verdictsFromRollout(jsonl: string): unknown[] {
  const seen = new Map<string, unknown>();
  const walk = (node: unknown): void => {
    if (Array.isArray(node)) return node.forEach(walk);
    if (node && typeof node === 'object') {
      if ('overall_correctness' in node) seen.set(JSON.stringify(node), node);
      Object.values(node).forEach(walk);
    }
  };
  for (const line of jsonl.split('\n')) {
    if (!line.includes('"overall_correctness"')) continue;
    try {
      walk(JSON.parse(line));
    } catch {
      seen.set(`unparseable:${line}`, { overall_correctness: null });
    }
  }
  return [...seen.values()];
}

function isVerdict(v: unknown): v is StructuredVerdict {
  if (!v || typeof v !== 'object') return false;
  const o = v as Record<string, unknown>;
  if (o.overall_correctness !== CORRECT && o.overall_correctness !== INCORRECT) return false;
  if (!Array.isArray(o.findings)) return false;
  return o.findings.every(f => {
    const p = (f as Record<string, unknown> | null)?.priority;
    return Number.isInteger(p) && (p as number) >= 0 && (p as number) <= MAX_PRIORITY;
  });
}

function textTags(output: string): number[] {
  return [...output.matchAll(BRACKET_TAG), ...output.matchAll(NATIVE_TAG)].map(m => Number(m[1]));
}

export function decide(exitCode: number, output: string, verdicts: unknown[]): GateResult {
  if (exitCode !== 0) return { pass: false, source: 'exit', findings: 0, reason: `fail-closed: codex exited ${exitCode}` };
  if (!output.trim()) return { pass: false, source: 'empty', findings: 0, reason: 'fail-closed: empty output' };

  if (verdicts.length > 1) return { pass: false, source: 'structured', findings: 0, reason: 'fail-closed: ambiguous structured verdict' };
  if (verdicts.length === 1) {
    const v = verdicts[0];
    if (!isVerdict(v)) return { pass: false, source: 'structured', findings: 0, reason: 'fail-closed: malformed structured verdict' };
    const critical = v.findings.filter(f => f.priority <= CRITICAL_MAX_PRIORITY).length;
    if (critical > 0) return { pass: false, source: 'structured', findings: v.findings.length, reason: `${critical} critical findings` };
    if (v.findings.length === 0 && v.overall_correctness !== CORRECT) {
      return { pass: false, source: 'structured', findings: 0, reason: 'fail-closed: no findings but verdict is not "patch is correct"' };
    }
    return { pass: true, source: 'structured', findings: v.findings.length, reason: v.findings.length ? 'advisory findings only' : 'clean review' };
  }

  const tags = textTags(output);
  const textCritical = tags.filter(p => p <= CRITICAL_MAX_PRIORITY).length;
  if (textCritical > 0) return { pass: false, source: 'text', findings: tags.length, reason: `${textCritical} critical findings` };
  if (tags.some(p => p > MAX_PRIORITY)) return { pass: false, source: 'text', findings: tags.length, reason: 'fail-closed: unknown severity tag' };
  if (tags.length > 0) return { pass: true, source: 'text', findings: tags.length, reason: 'advisory findings only' };
  if (output.split(/\r?\n/).some(l => l.trim() === NO_FINDINGS_SENTINEL)) {
    return { pass: true, source: 'text', findings: 0, reason: `clean review (${NO_FINDINGS_SENTINEL} sentinel)` };
  }
  return { pass: false, source: 'text', findings: 0, reason: 'fail-closed: untagged output' };
}

export function evaluate(exitCode: number, output: string, stderr: string, codexHome: string): GateResult {
  const id = sessionIdFromStderr(stderr);
  const rollout = id ? findRollout(codexHome, id) : null;
  const verdicts = rollout ? verdictsFromRollout(readFileSync(rollout, 'utf8')) : [];
  return decide(exitCode, output, verdicts);
}

if (import.meta.main) {
  const [exitArg, outPath, errPath] = process.argv.slice(2);
  if (!/^-?[0-9]+$/.test(exitArg ?? '') || !outPath || !errPath) {
    console.error('Usage: codex-review-gate.ts <codex-exit-code> <stdout-file> <stderr-file>');
    process.exit(2);
  }
  try {
    const codexHome = process.env.CODEX_HOME || join(homedir(), '.codex');
    const r = evaluate(Number(exitArg), readFileSync(outPath, 'utf8'), readFileSync(errPath, 'utf8'), codexHome);
    const label = r.pass ? 'GATE: PASS' : r.reason.endsWith('critical findings') ? `GATE: FAIL (${r.reason})` : `GATE: FAIL (${r.reason} — needs human attention)`;
    console.log(r.pass ? `${label} (${r.reason})` : label);
    console.log(`GATE_SOURCE: ${r.source}  FINDINGS: ${r.findings}`);
    process.exit(r.pass ? 0 : 1);
  } catch (error) {
    console.log(`GATE: FAIL (fail-closed: gate error: ${error} — needs human attention)`);
    process.exit(1);
  }
}
