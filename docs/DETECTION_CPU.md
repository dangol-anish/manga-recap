# Stage 3 CPU use

Windows detection now defaults to three quarters of the logical CPU count, capped at 12
threads (and bounded by the available count). On a Ryzen 7 5825U with 16 logical
processors this gives 12 calculation threads instead of two. macOS retains
the previous two-thread setting.

This changes only PyTorch's CPU thread budget. The model revision, full CPU
precision, normal detection prompt, three beams, token limits, panel filtering,
reading order and sequential page processing remain unchanged. Saved detections
and downstream work keep their existing source identities and are reused.

An optional `MAGI_CPU_THREADS` process environment variable accepts a positive
integer, bounded by the available logical CPU count. Set it to `2` before starting
the app to restore the previous CPU budget. Restart the app to apply a change;
do not interrupt a running detection job.

For an explicit local synthetic comparison, with the app idle, run:

```powershell
.\.venv\Scripts\python.exe -X utf8 -m scripts.benchmark_detection_cpu
```

This loads the installed model once and compares 2, 4 and 8 threads in forward
and reverse order on the same synthetic three-panel image. It checks exact panel
box equality and writes only a new report under ignored `outputs/cpu-benchmarks/`.
It does not process manga or modify chapter jobs/readiness. Results describe
this fixture, not guaranteed chapter speeds or universal detection equivalence.
