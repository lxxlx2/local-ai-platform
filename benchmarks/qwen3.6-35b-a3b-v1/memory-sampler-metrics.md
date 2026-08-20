# Memory sampler metric definitions

- **PHYSICAL_MEMORY_USED / SYSTEM_MEMORY_USED**: `hw.memsize - free - inactive - speculative`, calculated from `vm_stat` pages (16 KiB per page). This is a system-level working-memory estimate, not per-model unified-memory allocation.
- **WIRED / ACTIVE / INACTIVE / COMPRESSED**: the corresponding `vm_stat` page categories, converted to MiB. `COMPRESSED` uses “Pages occupied by compressor”.
- **SWAP_USED**: macOS `vm.swapusage` “used” value, converted to MiB.
- **MEMORY_PRESSURE**: `memory_pressure -Q` system-wide free percentage; higher is healthier.
- **MODEL_PROCESS_RSS**: `ps` RSS for the oMLX server process, converted from KiB to MiB. On Apple Silicon it does not represent the model's complete unified-memory allocation and must not be treated as such.
- **CPU**: `ps` CPU percentage for the oMLX server process.
- **THERMAL_STATE**: read-only `pmset -g therm` heuristic. macOS's explicit “No thermal/performance warning level has been recorded” response is `NORMAL`; warning, critical, or throttle indicators are `WARNING`; unavailable output is `UNAVAILABLE`.
