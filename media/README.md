# QueuePilot demo

`QueuePilot-Demo.mp4` is a 37.5-second, silent, captioned 1080 × 1350 H.264
recording of the local application. `QueuePilot-Cover.png` is the opening frame;
`QueuePilot-Captions.srt` is an optional text alternative to the burned-in captions.

## What is actually shown

| Time | Evidence shown | Important boundary |
|---|---|---|
| 0–7s | Authored duplicate-charge + login example; 99.4% single-intent confidence, held by the multiple-issue rule | Confidence is not accuracy; the rule is explicit, not an LLM or general multi-intent detector |
| 7–13s | Actual review-capacity control changes; 120-message benchmark replay | Slots are a batch assumption, not measured staffing throughput |
| 13–19s | With zero review slots, 50 flagged requests wait; 70 suggestions remain | Nothing is force-routed to clear a backlog; suggestions are not resolved tickets |
| 19–25s | A demo human route and reason after reloading the same SQLite session | Original predictions/test labels remain unchanged; no automatic retraining or customer action |
| 25–32s | 88.87% baseline vs 88.67% candidate exact-intent accuracy; final BLOCK | Four queue slices are inconclusive, not proof of a statistically significant regression |
| 32–37.5s | Downloadable models, inputs, predictions, reports and manifest | Unsigned hashes verify consistency, not authenticity or scientific validity |

The opening example is author-written. Benchmark metrics use all 3072 retained
BANKING77 official-test messages, not the 120-row inbox sample. BANKING77 is
crowdsourced English single-intent text, not live customer traffic. Reference
policy/calibration/test splits are separate and fixed before official-test scoring.

The edit crops/scales actual screencast pixels and adds explanatory captions.
Navigation pauses are cut; settled frames are held for readability. The capacity
change is captured in real time. The audit close-up is a separate capture after
reloading the persisted session. There are no synthesized predictions, animated
fake controls, staged performance claims, music or voiceover.

`inbox.png`, `review-audit.png` and `model-check.png` are actual app screenshots.
No private inbox or external customer data is included.
