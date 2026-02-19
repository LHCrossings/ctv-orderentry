# Lessons Learned

## Tests Are Not Authoritative for String Constants

**Session:** Market code mismatch fix (2026-02-19)

**What happened:** `test_chinese_block_abbreviation` expected `"C/M"` but
`Language.MANDARIN.get_block_abbreviation()` returned `"M/C"`. The test was stale — the code
was correct. Correctly flagged this as a pre-existing failure (not introduced by the change),
then fixed the test when user confirmed `"M/C"` is correct.

**Rule:** When a test and implementation disagree on a string constant, do NOT silently fix
either side. Surface the conflict explicitly, state which side you believe is correct and why,
and let the user confirm before touching anything.
