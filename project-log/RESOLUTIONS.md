# Resolutions

Decisions that were argued about, and how they were settled. Each entry keeps the reasoning so
the decision can be revisited on evidence rather than re-litigated on memory.

Last updated: 2026-09-11

## Z.ai and Routeway are substituted, not dropped

**Question.** The specification lists Z.ai and Routeway as free fallback providers.

**Finding.** Z.ai's own devpack FAQ confirms there is no free tier for GLM coding models; the
cheap Coding Plan is a paid subscription. Routeway advertises signup credits, and no
first-party page confirms an ongoing free tier.

**Resolution.** Their chain slots are carried by OpenRouter free coder models
(`z-ai/glm-4.5-air:free`, `qwen/qwen3-coder:free, deepseek/deepseek-chat-v3.1:free`). Both
providers remain configurable for anyone who buys a key, and the substitution is visible in
Settings, in the agent chain editor and in PROVIDER_VERIFICATION.md. Nothing was removed
silently.

## Gemini can be the Tester, with the trade-off stated

**Question.** Should the vision-capable free provider that trains on input be used for the
agent that looks at the user's game?

**Resolution.** Yes, because the Tester must be able to see. The Settings key card says plainly
that free-tier Gemini data may be used to improve Google's models, and the Tester's chain is
editable, so a user who objects can point it at another vision provider. The honest disclosure
is the mitigation, not silence.

## Demo mode appends a provider instead of being a separate code path

**Question.** How does `PULSEG_DEMO=1` stay trustworthy?

**Resolution.** Demo mode does not stub the pipeline; it appends the demo provider to the end
of every capable chain. The same code runs, the same Auditor gates, the same human approval
step exists - only the model is deterministic and offline. This is why the pipeline tests run
on the demo provider and still prove the gate.

## Human rejections do not count as Auditor declines

**Question.** Should a human rejection increment `decline_count`, which rotates the model after
three?

**Resolution.** No. They are different signals: three Auditor declines mean the *model* keeps
producing work that fails the rubric, while a human rejection is feedback about the work. They
are tracked as `decline_count` and `human_rejections` so rule B.2.5 cannot misfire because
someone was picky.

## The Auditor's determinism dominates the model's opinion

**Question.** When the model says "approve" and the deterministic skills say "high-severity
defect", who wins?

**Resolution.** The skills. A high-severity finding forces DECLINED whatever the model says; the
model can lower a score but never raise it above the deterministic ceiling. Conversely, an
Auditor model that is unreachable does not block the pipeline - the verdict is then made on the
deterministic evidence alone and the task still goes to the human. Taste is subjective;
evidence is not.

## The Documenter writes through its own path, not file blocks

**Question.** `run_and_write` treated "no file blocks in the response" as failure, which
rejected correct Documenter work.

**Resolution.** `run_and_write` gained `expect_files=False` for agents that write through a
purpose-built path, and `expected_outputs_present()` checks the filesystem as well as the
artifact list. The alternative - making every agent emit whole-file blocks for a section edit -
would have been more brittle and wasted tokens re-emitting a document to change one paragraph.

## Stylometry thresholds are documented heuristics, not paper constants

**Question.** What cutoff decides "this text is machine-uniform"?

**Resolution.** None that the paper publishes. The code reports the distances and the merge
height, marks the thresholds as heuristics in the result metrics, and offers the comparative
method (`compare_to_corpus`) as the primary evidence. A fabricated cutoff would have been the
easy option and the wrong one.

## Files win over the index

**Question.** Which is authoritative when SQLite and the project files disagree?

**Resolution.** The files, always. The index is rebuildable cache; `Index.rebuild()` regenerates
it from `memory/` and the project folder. No code path may write a fact only to SQLite. This is
what makes the project folder portable and the app recoverable after a crash.
