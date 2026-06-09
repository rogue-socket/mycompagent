# Wikipedia Hard Run Evaluation - 2026-05-27

All runs used `--llm-provider codex`, `--headed`, `--debug`, autonomous mode,
and article-link-only task wording. Each run directory includes `actions.jsonl`,
`llm_responses.jsonl`, `browser_state.jsonl`, `interpreter_state.jsonl`,
snapshots, copied traces, and `session.webm`.

## Results

| Run | Task | Result | Steps | Runtime |
| --- | --- | --- | ---: | ---: |
| `runs/run_20260527T101051Z` | Quantum mechanics -> Hip hop music | completed | 11 | 269.18s |
| `runs/run_20260527T101537Z` | Antarctica -> Sushi | max_steps | 22 | 642.35s |
| `runs/run_20260527T102636Z` | Pythagorean theorem -> Heavy metal music | completed | 5 | 92.82s |
| `runs/run_20260527T102822Z` | Black hole -> Pokemon | max_steps | 22 | 740.83s |

## Routes

- Quantum mechanics -> Standing wave -> Vibration of a circular membrane -> Drum -> Popular music -> Hip-hop -> Rapping -> Hip-hop music.
- Antarctica -> Southern Ocean -> Notothenioidei -> Actinopterygii -> Three-spined stickleback -> Northern pike -> Northern snakehead -> Snakehead (fish) -> Actinopterygii -> Three-spined stickleback -> Northern Hemisphere -> Eurasia -> Southeast Asia.
- Pythagorean theorem -> Geometry -> Mathematics and art -> Music -> Heavy metal music.
- Black hole -> Milky Way -> Earth -> Human impact on the environment -> Fukushima nuclear accident -> 2011 Tohoku earthquake and tsunami -> Sendai -> Miyagi Prefecture -> Japanese language -> Japan -> Science and technology in Japan -> Video game -> Video game console -> Handheld game console -> List of handheld game consoles -> Game Boy -> Pokemon (video game series) -> The Pokemon Company.

## Findings

- The agent performs well when a high-level bridge exposes the target family quickly. The math-to-music run finished in 5 steps, and the physics-to-hip-hop run recovered from a redirect in 11 steps.
- Hard failures are mostly route-quality failures, not click mechanics. The Antarctica run drifted through local classification and geography links; the current direction is generic route-state tracking and better evidence comparison rather than topic-specific labels.
- Long-article anchors remain weak. The agent reached `Snakehead (fish)#Culinary_use`, but prompt-visible content still looked like the top of the page, causing scroll/snapshot churn and a retreat to local classification links.
- Step budget matters. The Black hole -> Pokemon run found a plausible route and reached Pokemon-adjacent pages, but spent too many steps on astronomy/geography/Japan before reaching video games.
- Planner latency can spike severely on open-ended route planning. The Black hole run had a 249s planning call on step 3 before recovering.
- Redirect/canonical prompt notes now tell the planner when the current page is the canonical article for a redirected task target.

## Follow-Ups

- Current direction: avoid hardcoded route-helper hints and improve generic route planning over visible/current-page links. A full multi-page graph search remains a possible future improvement.
- Surface target-adjacent raw snapshot matches even when they are below visible-text truncation.
- Done: prompt construction surfaces canonical redirect targets when the raw snapshot says the page was redirected from the task target.
- Same-page anchor clicks are now skipped when the URL already contains the target fragment; repeated scroll churn still needs broader route-quality handling.
- Planner latency now persists in JSONL for new runs.
