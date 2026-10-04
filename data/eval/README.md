# Disruption extraction evaluation sets

- `disruptions_dev.json`: 30 hand-written reports (German and English) for choosing a model and
  improving prompts. Written by the dev team to cover edge cases: dates vs times, times without minutes,
  overnight windows, replacement stops, resolved notices, sharing and on-demand outages.
- `../sample_disruptions/disruptions.json`: the original 5 reports, kept **held out**: never used to
  tune anything, only to check that dev-set choices generalise.

Neither set is real operational data. Replace or extend with real labelled reports for evaluation.

Labelling conventions: `expected_affected_stops` lists stops that are closed, skipped, or bound a
blocked section, never replacement stops. `expected_effect` is one of suspended, stops_not_served,
station_closed, delayed, reduced_availability, other. Times are HH:MM or null.
