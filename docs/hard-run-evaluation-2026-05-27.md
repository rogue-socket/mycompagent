# Hard Run Evaluation - 2026-05-27

All runs used `--llm-provider codex`, `--headed`, `--debug`, and autonomous mode.
After the evaluation, debug logging was fixed so new debug runs persist
`debug_artifacts.jsonl`, copied Playwright trace files, and `session.webm` under the
run directory. Verified by `runs/run_20260527T092715Z`.

## Results

| Run | Task | Result | Steps | Runtime |
| --- | --- | --- | ---: | ---: |
| `runs/run_20260527T091406Z` | Wikipedia link puzzle: Solar System to Anime | completed | 6 | 131.61s |
| `runs/run_20260527T091629Z` | MDN: find `Promise.allSettled()` syntax and return value | completed | 4 | 87.45s |
| `runs/run_20260527T091808Z` | Python docs: find `argparse.BooleanOptionalAction` | completed | 15 | 284.21s |
| `runs/run_20260527T092307Z` | GitHub: find latest Playwright release | completed | 2 | 43.01s |

## Routes And Answers

- Wikipedia route: Solar System -> Sun -> Helios -> Personification -> Anthropomorphism -> Anime.
- MDN answer: `Promise.allSettled(iterable)`; returns a promise fulfilled after all inputs settle with outcome objects.
- Python docs answer: `BooleanOptionalAction` creates positive and negative boolean flags, e.g. `--foo` and `--no-foo`.
- GitHub answer: latest Playwright release was shown as `v1.60.0`, dated May 11, 2026.

## Findings

- Core browser mechanics are now reliable across Wikipedia, MDN, Python docs, and GitHub.
- The agent can complete link-only Wikipedia tasks when a target link appears in the prompt.
- Docs tasks succeed when the relevant content is near the top or linked from visible navigation.
- Static long docs expose a major weakness: the raw snapshot already contained the Python answer by step 5, but the prompt-visible text did not, causing 10 extra steps of search, scroll, and same-page anchor churn.
- Debug logging was incomplete during the four hard runs; it is now run-scoped for subsequent runs.
- Planner latency remains the dominant runtime cost. New runs persist step-level latency and prompt size in JSON logs, and Codex history now omits previous full page states to reduce prompt growth.

## Follow-Ups

- Done: task-focused evidence snippets from raw snapshots or DOM text now surface exact-match content below the visible-text cutoff.
- Done: planner latency, prompt length, retry metadata, and debug artifact paths are persisted to JSONL.
- Done: repeated same-page anchor clicks are skipped when the URL already contains the target fragment.
- Done: successful click actions now persist target link metadata, and `finish` logs include a grounded route.
