---
name: ci-preflight
description: >-
  Keep shared CI out of the agent's inner loop: prepare the task environment once, run lint and
  affected tests locally before every push, push only green states, and handle a red pipeline
  locally within a CI budget. Use when starting a coding task in a repository with CI, before any
  git push, and when a pipeline goes red.
---

# CI preflight

Shared CI runners are the company's scarcest resource. Half of runner hours go to pipelines that end
**red**, and 96% of those failures are code errors an agent can reproduce on its own machine
(41% are lint). Your **inner loop** — change, check, fix — runs locally. CI receives only states that
are already **green** locally and serves as the final gate.

Measurements and the reasoning behind each step: `docs/experiments/E05-agent-ci-behavior.md` and
`E06-hypotheses-offline.md` in `architects/agentic-runners`.

## 1. Prepare the task environment once

At the start of the task, before the first change:

1. Read the project's CI configuration (`.gitlab-ci.yml` and its `include:`s) and find the commands of
   its lint and test jobs. These are your **preflight** commands; use them verbatim, with the same
   tools and versions.
2. Install the project's dependencies and start the services its tests need (database, cache) the
   way the CI jobs do. Keep this environment for the whole task; later steps reuse it.
3. Python projects with pytest: install the test runtime into the same environment:

   ```bash
   pip install "agentic-runtime @ git+https://gitlab.raftds.com/architects/agentic-runners.git#subdirectory=runtime"
   export PYTEST_ADDOPTS="-p agentic_runtime --rt-workers=auto --rt-cache=.rt-cache --rt-fail-fast"
   ```

   Options take values with `=` only. Add `.rt-cache/` to `.git/info/exclude`.

Done when the preflight commands run to completion on the unchanged branch, and you have recorded
which of them, if any, already fail before your change.

## 2. Inner loop: preflight every change locally

After each change:

1. Run lint first: it takes seconds and catches the most common failures.
2. Run the tests. With the runtime, the full test command runs only files whose inputs changed and
   puts tests that failed last time first; stop at the first failure and fix it.
3. Repeat until lint and tests are green.

Done when every preflight command is green, or fails only where it already failed in step 1.

## 3. Attest and push green states

1. Push only after step 2 is done. Commit as often as you like; push once per green state.
2. If the project has `ci/agentic-checks.yml`, attest every check it lists: run
   `python -m agentic_runtime.attest run -- <AGENTIC_CMD>` for each, on the committed tree, then push the
   notes with the branch: `git push origin HEAD refs/notes/agentic-attest`. CI accepts these checks from
   the attestation and skips rerunning them. Needs `AGENTIC_ATTEST_KEY` in the environment.
3. Open the MR as Draft while work continues. Before marking it ready, rebase onto the target branch,
   rerun and re-attest the checks on the rebased tree, so the merge train accepts the attestation too.
4. One push triggers one pipeline. Batch follow-up fixes into the next green state instead of pushing
   each one.

Done when the pushed commit is the one you preflighted and, where the project uses attestation, its notes are pushed.

## 4. A red pipeline

The CI **budget** for one task is two red pipelines in a row on the branch.

1. Open the first failed job by finish time and read its first error. Later failures in the same
   pipeline are often consequences.
2. Reproduce it locally with that job's command. If it reproduces, fix it and go back to step 2.
3. If it does not reproduce locally, the difference is in the environment: compare image, versions,
   services and variables of the job with your environment, and record what differs.
4. When the budget is spent, stop pushing. Report to the builder or the human: the failing job, its
   first error, whether it reproduces locally, and what you tried.

Done when the pipeline is green, or the budget is spent and the report is delivered.
