# Known limitations and future work

This page states plainly what the current system does not do well, split
into research findings from the thesis evaluation and engineering debt from
the implementation. The goal is honesty about scope, not a task list to
clear before release.

## From the thesis evaluation

**RAGAS evaluation scores the retriever and the LLM together, not the full
system.** The knowledge grounding comparison (baseline direct vs naive RAG
vs the production KB router) used 84 ISO derived question/answer pairs and
RAGAS metrics judged by an LLM. This measures retrieval quality in
isolation. It does not measure the effect of retrieval inside a live
SOW/SRS/ADR generation run, where the retrieved context competes with a
longer prompt, prior artifact context, and Socratic clarifications for the
model's attention. A end to end ablation on real generation runs, not just
isolated QA pairs, would close this gap.

**Surface similarity metrics are not a reliable proxy for decision
agreement.** The external replay evaluation (JabRef, Semantic Kernel)
found that a character level fuzzy Jaccard over rejected alternative
options collapses toward zero even when a generated ADR reaches the same
architectural decision as the real one, in different words. The chosen
decision is often excluded from the compared option sets on one or both
sides, and paraphrased prose rarely clears a literal similarity threshold.
The evaluation therefore treats fuzzy Jaccard strictly as a surface
vocabulary indicator, and uses a separate LLM based decision agreement
judge (reading the chosen decision text, not the rejected options) as the
substantive signal. Anyone building on this replay approach should keep
that separation, and should not report surface similarity alone as a
validity score.

**Small local models degrade sharply, not gracefully.** The multi model
comparison found that mid size local models (around 3 to 4B parameters)
hold up reasonably on structural trace coverage, while sub 2B local models
drop to near zero structural coverage and Gherkin validity, with a rising
mean error count per run. There is no current fallback or graceful
degradation path when a configured local model is too small for the task,
the pipeline just produces low quality or broken output. A model
capability check before committing to a full generation run would be a
real improvement here.

**Project to source disambiguation is still an open design question.**
The primary validation scenario (a time tracking pilot linking researcher
hours to GitHub activity and Outlook events) explicitly left open how a
project should be linked to its repositories and calendar events when
several projects overlap or a contributor pushes infrequently. Keyword or
LLM based classification was proposed but not settled. This is a real gap
for anyone wiring Sdlicit's knowledge ingestion to noisy, multi project
external sources rather than a single clean document set.

## From the implementation

**The agentic ablation knob only reaches one agent.** `config.agentic`
switches between the deterministic pipeline and a ReAct tool calling
loop (`LLMGateway.predict_react`), but that path had zero call sites
anywhere until this pass wired it into `ADRAgent.full_review`. Every
other agent, including `suggest_directions` and the generation stage
agents (SRS, personas, stories, Gherkin), still always calls the
deterministic `predict()` regardless of this setting. Extending it to
those call sites is real, mechanical follow on work, not attempted
here since it was deliberately scoped to proving the wiring works on
one agent first.

**LLM provider abstraction is minimal.** The gateway supports the
providers already wired in (OpenRouter, Ollama, and whatever DSPy's
adapter layer covers out of the box). Adding a new provider means editing
the gateway directly, there is no plugin or registry mechanism. Left as
is deliberately for now, a community contribution once there is real
demand for a specific provider is a reasonable way to grow this rather
than speculatively generalizing it up front.

**Type checking and formatting are informational, not enforced.** `mypy
--strict` reports several hundred pre existing errors across the backend,
mostly missing generic type arguments and DSPy's dynamically built
Signature classes not being visible to the type checker. `ruff format`
would reformat a large share of files that predate its adoption. Both run
in CI but do not block merges (`continue-on-error: true`), so they will
drift further unless someone deliberately schedules the cleanup. This is
a real, bounded piece of work, not a design problem.

**The backend does not ship as a standalone binary.** The CLI packages
cleanly with PyInstaller for Linux and Windows. The backend server's
dependency surface (LightRAG, DSPy, native extensions) made a PyInstaller
build unreliable to verify safely, so it stays a `uv run` or Docker
deployed component. This is normal for a server process, but it does mean
there is no single file backend binary in releases the way there is for
the CLI.

**The extension's free form chat panel has no CLI equivalent.** The VS
Code extension exposes a standing chat surface with an automatic probing
loop (`observeChat`/`giveSuggestions`) that reacts to what the user is
doing. The CLI's guided flow is structured and menu driven instead. These
are genuinely different interaction models, not a simple parity gap, and
bringing them to feature parity is future work rather than something to
force into this pass.

**Documentation site deployment needs a one time manual step.** The
`docs.yml` workflow builds the MkDocs site and attempts to deploy it to
GitHub Pages on every push to `main`. The deploy step needs GitHub Pages
enabled on the repository first (`Settings > Pages`, source set to
`GitHub Actions`), which is a repository setting, not something a
workflow run can turn on for itself, and which has visibility
implications on a repository that is currently kept private.

## What this list is not

This is not a backlog to burn down before opening the repository. Most of
these are legitimate follow on research or engineering questions that
outlive a single open sourcing pass. The point of writing them down here
is that a contributor reading the code should not have to rediscover
these limits by hitting them, and a reviewer of the thesis should be able
to see the gap between what was measured and what remains open, in one
place.
