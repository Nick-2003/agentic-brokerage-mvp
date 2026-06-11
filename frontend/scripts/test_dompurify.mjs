// Offline test for proposal 032 — the DOMPurify SafeHtml sanitizer config.
// Run from the frontend dir (so `isomorphic-dompurify` resolves):
//     cd frontend && node scripts/test_dompurify.mjs
// Asserts the same strict allowlist SafeHtml uses: keep <strong>/<em>, drop all
// attributes + every other tag (the XSS vectors).
import DOMPurify from 'isomorphic-dompurify';

const CFG = { ALLOWED_TAGS: ['strong', 'em'], ALLOWED_ATTR: [] };
const clean = (s) => DOMPurify.sanitize(s, CFG);

let pass = 0, fail = 0;
const check = (name, got, want) => {
  if (got === want) { pass++; console.log(`  ✓ ${name}`); }
  else { fail++; console.log(`  ✗ ${name}\n      got:  ${JSON.stringify(got)}\n      want: ${JSON.stringify(want)}`); };
};

check('keep <strong>',        clean('<strong>hi</strong>'),                         '<strong>hi</strong>');
check('keep <em>',            clean('<em>x</em>'),                                  '<em>x</em>');
check('strip attr, keep tag', clean('<strong onclick="alert(1)">x</strong>'),      '<strong>x</strong>');
check('drop <script>',        clean('<script>alert(1)</script>'),                  '');
check('drop <img onerror>',   clean('<img src=x onerror=alert(1)>'),               '');
check('drop anchor, keep txt',clean('<a href="javascript:alert(1)">link</a>'),     'link');
check('drop unknown tag',     clean('<div class="x">y</div>'),                     'y');
check('bare & preserved (text, not executed)', clean('a & b'),                     'a & b');
check('empty/nullish safe',   clean(''),                                           '');

console.log(`\n${pass} passed, ${fail} failed`);
process.exit(fail ? 1 : 0);
