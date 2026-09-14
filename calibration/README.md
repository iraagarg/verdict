# calibration/

`labels.jsonl` holds Iraa's hand labels — one JSON object per comparison, appended
as each decision is made.

**These are committed.** They are real measurement data: the human half of every
Cohen's κ this project reports. Losing them means re-labelling from scratch,
which is hours of work, and re-labelling against a revised rubric would not
reproduce them anyway.

Each label records the candidate-relative verdict (`win`/`tie`/`loss`), which
slot the candidate was shown in, and how long the decision took. The slot is
recorded so the labeller's own position bias is measurable rather than invisible;
the timing is recorded because a label made in two seconds is worth re-checking
before blaming the judge for disagreeing with it.

Labels are tied to a `rubric_version`. Changing the rubric invalidates them —
that is the point of the version, and why the calibration refuses to mix.
