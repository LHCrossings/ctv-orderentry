## Writing Style: ASD-STE100 (Simplified Technical English)

When writing documentation, comments, error messages, or any technical prose,
follow ASD-STE100 principles:

### Vocabulary
- Use one word per meaning. Do not use synonyms for the same concept
  (e.g., pick "start" OR "begin," not both across the document).
- Use each word in only one part of speech / one meaning. Do not use
  "close" as both a verb ("close the valve") and an adjective
  ("close to the wall") in the same doc — pick one sense and stick to it.
- Prefer short, common, concrete words over long or abstract ones
  (e.g., "use" not "utilize"; "start" not "commence"; "show" not "indicate").
- Avoid jargon and invented terms. If a technical term is unavoidable,
  define it on first use.

### Sentences
- One instruction or one fact per sentence. Do not chain steps with "and."
- Use active voice: "The system saves the file," not "The file is saved
  by the system."
- Keep sentences short — target ~20 words max, hard cap ~25.
- Use simple tenses only: simple present, simple past, simple future.
  Avoid perfect/continuous/conditional constructions
  ("has been configured" → "you configured" / "is configured").
- Avoid "-ing" words as adjectives or nouns where a simpler form exists
  ("the operating system" is fine as a fixed term, but avoid constructions
  like "the increasing latency issue" → "the latency is increasing").

### Noun clusters
- Limit strings of nouns used as modifiers to 3 words max
  ("database connection timeout error" → break it up:
  "an error caused by a database connection timeout").

### Instructions and procedures
- Write steps as imperative commands: "Run the script," not "The script
  should be run" or "You should run the script."
- Number sequential steps. One action per step.

### Clarity
- Avoid ambiguous pronouns ("it," "this," "that") when the referent
  isn't the immediately preceding noun — repeat the noun instead.
- Spell out logic explicitly rather than implying it
  ("If X, do Y" rather than relying on context).
- Avoid negative constructions where a positive one is clearer
  ("Do not use the old config" → prefer telling the reader what TO do:
  "Use the new config").

### Scope note
Apply this to prose (docs, comments, commit messages, error/UI text),
not to code syntax itself. Where a domain term or proper noun (API,
library, product name) conflicts with a "simple word" rule, use the
correct term rather than a euphemism — precision overrides simplicity
for named technical entities.
