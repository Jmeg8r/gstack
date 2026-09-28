/** Review-specific completion evidence, separate from provider transport success. */
export type OutsideGate = 'review' | 'structured' | 'spec';
/** P0 and P1 block, matching /codex review. */
const BLOCKING_MAX_PRIORITY = 1;
const MAX_PRIORITY = 3;
export function validateOutsideReview(text: string, gate: OutsideGate): { completed: boolean; reason?: string; score?: number; gate?: 'pass' | 'fail' } {
  if (!text.trim()) return { completed: false, reason: 'empty response' };
  // "I cannot find any issues" is a legitimate clean conclusion. Match a
  // refused/unavailable review, not every use of a negative auxiliary verb.
  if (/\b(?:(?:I (?:cannot|can't|won't|will not|am unable to)|I'm unable to)\s+(?:review|analy[sz]e|evaluate|assess|inspect|access|complete|perform|provide|assist|help|proceed)|unable to (?:review|analy[sz]e)|I must (?:decline|refuse))\b/i.test(text)) return { completed: false, reason: 'review refused' };
  if (gate === 'spec') {
    const scores = [...text.matchAll(/^SCORE:[\t ]*(10|[0-9])[\t ]*\r?$/gm)];
    const ambiguities = [...text.matchAll(/^AMBIGUITIES:[\t ]*(\S[^\r\n]*)\r?$/gm)];
    if (scores.length !== 1 || ambiguities.length !== 1) return { completed: false, reason: 'missing or invalid SCORE/AMBIGUITIES markers' };
    const score = Number(scores[0][1]);
    return { completed: true, score, gate: score >= 7 ? 'pass' : 'fail' };
  }
  if (gate === 'structured') {
    const plain = plainReview(text);
    const tags = [...plain.matchAll(/\[P([0-9])\]|^P([0-9]):/gm)].map(m => Number(m[1] ?? m[2]));
    if (tags.some(p => p <= BLOCKING_MAX_PRIORITY)) return { completed: true, gate: 'fail' };
    // Any P-number the tag parser did not recognize (numbered list, table,
    // lowercase, P10) could be a dropped blocker, so the pass is unproven.
    const recognized = new Set(tags);
    const loose = [...plain.matchAll(/(?<![A-Za-z0-9])P([0-9]+)(?![A-Za-z0-9])/gi)].map(m => Number(m[1]));
    if (tags.some(p => p > MAX_PRIORITY) || loose.some(p => !recognized.has(p))) return { completed: false, reason: 'unknown or unrecognized severity tag' };
    if (tags.length) return { completed: true, gate: 'pass' };
    // Clean-review prose varies run to run and can sit beside an untagged
    // defect, so only the exact sentinel line counts as a no-findings conclusion.
    // Matched on raw text: plainReview strips the sentinel's own underscore.
    if (!/^[\t ]*(?:[-+*][\t ]+)?([*_`]*)NO_FINDINGS\1[\t ]*\r?$/m.test(text)) return { completed: false, reason: 'missing severity tag or NO_FINDINGS line' };
    return { completed: true, gate: 'pass' };
  }
  // Formatting the requested marker in bold, inline code, or a list does not
  // invalidate a completed review. Preserve the explicit action + reason gate.
  const plain = plainReview(text);
  if (!/^Recommendation:[\t ]*[^\r\n]+\bbecause\b[\t ]*\S[^\r\n]+$/im.test(plain)) return { completed: false, reason: 'missing review completion recommendation' };
  return { completed: true };
}
function plainReview(text: string): string {
  return text.split(/\r?\n/).map(line => line.replace(/^[\t ]*(?:#{1,6}[\t ]+|[-+*][\t ]+)?/, '').replace(/[*_`]/g, '')).join('\n');
}
if (import.meta.main) {
  const [gate, path] = process.argv.slice(2);
  if (!['review', 'structured', 'spec'].includes(gate) || !path) { console.error('Usage: outside-review-result.ts review|structured|spec <response-file>'); process.exit(2); }
  try {
    const result = validateOutsideReview(await Bun.file(path).text(), gate as OutsideGate);
    if (!result.completed) { console.error(`Outside review unavailable: ${result.reason}; missing coverage.`); process.exit(1); }
  } catch (error) { console.error(`Outside review unavailable: ${error}`); process.exit(1); }
}
