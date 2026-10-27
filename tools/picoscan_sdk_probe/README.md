# picoScan SDK diagnostic probe

This is an independent, read-only UDP receiver using SICK's native Compact parser
and point cloud converter. It does not replace the production receiver, configure
the scanner, integrate encoder data, or publish measurements.

Pinned SDK: `3213a0098a82e8e440a4887087d79e972584f168`.

## Windows

Build with Visual Studio 2022 C++ tools **and Windows SDK** installed:

```powershell
.\tools\picoscan_sdk_probe\build.ps1
```

Alternatively pass the `bin` directory of a portable LLVM-MinGW UCRT x64 compiler:

```powershell
.\tools\picoscan_sdk_probe\build.ps1 -ToolchainBin 'C:\path\llvm-mingw\bin'
```

Build dependencies are isolated in TEMP. Python, Git and network access are required.
The minimal SDK build omits drivers and REST configuration; UDP transport belongs
to this probe. CRC validation uses zlib. CPU timings include parser and conversion,
not queue waiting or network latency; they are not an end-to-end benchmark.

Stop the application's scanner listener before testing. The script refuses to run
if UDP 2115 is occupied. The scanner must already send Compact UDP to this PC.
No firewall or sensor configuration is changed automatically.

```powershell
.\tools\picoscan_sdk_probe\run.ps1 -Seconds 30
```

Defaults: local `192.168.0.250:2115`, source `192.168.0.1`.
CSV files, console logs and summary JSON are saved in ignored `output/`. Host time is sampled immediately after
recvfrom, not a hardware/network timestamp. Sensor beam times have their own clock.
All echoes are counted; the converter uses SDK default filters and meter units.

```powershell
python tools/picoscan_sdk_probe/analyze.py PATH_TO_CSV --segment-ids 0,1,2
```

Only supply segment IDs confirmed from the actual sensor configuration. The example
does not assert that this scanner uses three segments. First and last observed
frames are excluded from completeness checks. Reordered/reset frame counters may
require manual investigation; the CSV preserves arrival order. Repeated IDs are
reported, not silently treated as missing IDs. Entirely absent frames are not
included in observed-frame counts. No report is a measurement-validity certificate.

Exit codes: 0 reception/parsing succeeded; 1 runtime failure; 2 usage;
3 no packets from the selected source; 4 at least one parse/conversion failure.
CSV contains successfully parsed/converted segments only. The console separately
counts all accepted datagrams and parser errors, so keep both when diagnosing loss.

```powershell
python -m unittest discover -s tools/picoscan_sdk_probe -p 'test_*.py' -v
```

## Initial hardware test (2026-10-27)

Two 30-second runs on Windows, sensor `192.168.0.1`, destination
`192.168.0.250:2115`, actual SO_RCVBUF 4194304 bytes:

| Run | Datagrams | Parse/conversion errors | Interior frames | Frames with IDs 0-9 | Telegram discontinuities |
| --- | ---: | ---: | ---: | ---: | ---: |
| 1 | 4499 | 0 | 449 | 444 | 5 |
| 2 | 4496 | 0 | 449 | 441 | 8 |

Observed rate was approximately 15 frames/s. In each affected interior frame one
ID was absent relative to the dominant 0-9 pattern. No duplicate segments or
segments with zero positive distances were observed. These are observations for
this connected sensor, NOT proof of its intended configuration, nor identification
of where packets were lost. Parsing and CSV writes happen in the receive thread;
OS scheduling, socket buffering, network and sensor output remain possible causes.
No production acquisition, encoder alignment or two-scanner synchronization test
was performed. Four Python tests passed, including native loopback checks for no
data and malformed telegrams. The executable was built with LLVM-MinGW 20261020.

## Compare the production Python receiver

The backend probe imports the actual production receiver and runs its normal
manager worker (UDP, parser, frame assembly, conversion, queue), draining the
queue every 10 ms. It records segment IDs without adding extra point conversion.
It does not start HTTP, rendering, encoder integration or log measurement. Run
sequentially with the SDK probe, never simultaneously on the same UDP port:

```powershell
Push-Location backend
.\venv\Scripts\python.exe ..\tools\picoscan_sdk_probe\backend_probe.py --seconds 30 --segments 10
Pop-Location
```

`--segments 10` is a test assumption based on the observed 0-9 pattern, not a
persistent device configuration change. The default uses normal pattern learning.
`--explicit-pattern` bypasses learning using IDs 0..segments-1 for controlled tests.
Reports and CSV files are written to `output/`; process-local warning/error logging
and segment instrumentation add some overhead. SDK and backend runs use different
live data, so the counts do not establish which implementation is faster or better.

### Comparison on 2026-10-27 (30 seconds per run)

| Receiver | Parsed segments | Interior frames missing an ID relative to 0-9 | Delivered complete frames | Late complete frames discarded |
| --- | ---: | ---: | ---: | ---: |
| Python before learning-transition fix | 4500 | 4 | 437 | 4 |
| Native SDK (additional run) | 4489 | 14 | Not measured by this probe | Not applicable |
| Python after learning-transition fix | 4493 | 8 | 438 | 0 |

Both Python runs had zero empty delivered clouds, zero queue overflows and no
receiver warning/error logs. Four initial frames were intentionally excluded while
learning the pattern. The final partial frame is left pending at the stop boundary.
After the fix, 438 delivered + 4 learning + 8 incomplete + 1 pending = 451 observed
frames. The fix flushes already complete buffered frames when learning locks the
pattern, before newer frames can overtake them. A regression test reproduced the
old failure, then passed with the fix; all 65 backend tests passed.

These tests do not locate the remaining UDP losses and do not validate capture
storage, trigger delays, measurement length, two-scanner load or ARM performance.
