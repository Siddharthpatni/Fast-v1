# End-to-end tests

Drive the real UI in headless Chromium against the running stack (real timetables, live
data, database): search, plan, time bars, arrive-by, a disruption report breaking a trip and
the replan avoiding it, and the phone layout. Any browser console error fails a test.

```bash
make up    # stack must be running
make e2e   # runs in the official Playwright image, headless
```

Screenshots of each step are written to `e2e/artifacts/`. The disruption test files a real
report and resolves it again at the end. It uses Jev (and the LLM, if configured) once per run.
