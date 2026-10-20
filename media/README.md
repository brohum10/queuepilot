# QueuePilot — closer-look demo

`QueuePilot-Demo.mp4` is a 40-second, silent, captioned 1080 × 1080 H.264
recording. The matching PNG cover is its opening frame; the SRT is an optional
text alternative. No speech auto-captions or background music are needed.

| Time | Actual evidence shown | What it does not establish |
|---|---|---|
| 0–6s | 28 intent-label disagreements among 1684 automatic suggestions: 7 map to a different team, 21 to the same team | Not 28 observed wrong customer destinations; seven-team mapping is a project convention |
| 6–12s | `test-2446`, the highest-confidence mapped-team disagreement; cheque deposit vs delayed-balance intent | A confident model can escape the explicit hold rules; not all failures are detected |
| 12–18s | `test-2637`, the highest-confidence same-team intent disagreement; disposable-card labels are debatable | Supplied labels are not infallible ground truth; no operational harm is inferred |
| 18–24s | Actual capacity control: 30 slots → 0; 20 waiting → 50; 70 suggestions remain | Batch-capacity assumption, not measured staffing throughput or resolved tickets |
| 24–30s | Demo human override to Account & app, reopened after reload, with the original Security & access suggestion retained | A saved route is not an account action, intent correction, training update or proof of correct handling |
| 30–35.5s | Exact-intent baseline 88.87% vs candidate 88.67%; four team-slice checks inconclusive; BLOCK | Inconclusive is not proof of regression; passing software tests is not model approval |
| 35.5–40s | Evidence export, portable models and reproducibility; 105 software tests | Unsigned hashes check consistency, not authenticity or scientific validity |

Counts use the unchanged frozen policy on all 3072 retained BANKING77 official-test
queries, not the 120-row inbox. Example selection is deterministic: confidence
descending, then case ID. At most six per group are offered, not a representative
sample. The seven-team mapping is hand-defined. These benchmark disagreements
are not actual support outcomes. The authored two-issue scratchpad remains in
the app, but is not this video's headline evidence.

Editing only trims navigation gaps, holds settled frames for reading, crops/scales
actual recorded pixels, and adds explanatory text. Close-ups move from the
supplied label to the candidate prediction; no UI or model output is synthesized.
The review scene is recorded after a real page reload of the same SQLite inbox.

Screenshots are actual app captures. No private imported customer data, local
runtime database, API credential, or externally retrieved personal data is included.
