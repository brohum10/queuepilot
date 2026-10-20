# Data, provenance and permitted interpretation

`train.csv`, `test.csv`, and `categories.json` are unmodified BANKING77 files
from PolyAI's [task-specific datasets](https://github.com/PolyAI-LDN/task-specific-datasets),
pinned to commit `57ec275d8078af65b7731c2a98be812d844a6d6b`.
Their exact SHA-256 values are in `../protocol.json`. The original dataset
license is retained as `LICENSE-BANKING77` (CC BY 4.0).

**Attribution:** Iñigo Casanueva, Tadas Temčinas, Daniela Gerz, Matthew Henderson,
and Ivan Vulić. *Efficient Intent Detection with Dual Sentence Encoders*, 2020.
[Paper](https://arxiv.org/abs/2003.04807).

The data contains 10,003 training and 3,080 test queries across 77 banking
intents. The authors describe the queries as crowdsourced. They are English,
single-intent examples—not a stream of operational customer support tickets.
The seven team queues are QueuePilot's explicit mapping, not original labels.

Before splitting, QueuePilot normalizes case and whitespace and retains the
first occurrence of each text. Duplicates within or across the original
train/test files are excluded and recorded with source row IDs. The original
CSV files themselves are not modified. The retained training rows are divided
into fitting/calibration/policy splits using stratified seed 17. Official test
rows never fit vocabulary, weights, temperature, or the confidence threshold.

`challenge.json` contains separately authored English engineering fixtures for
the explicit sensitive/multiple-issue rules and unsupported requests. These
fixtures were developed alongside the rules. They are NOT a representative,
blind or production OOD benchmark. Do not pool them with BANKING77 accuracy.

`sample-import.csv` is an author-written, unlabeled import example. Its absence
of labels is intentional: no accuracy can be inferred from that inbox.

Imports are local runtime data, excluded from Git and all public evidence.
The email/long-number mask is basic minimization, not a PII guarantee. Use
public or anonymized text. Do not import confidential bank/customer records.
