# Picoscan Mapping Unit

System for multi-device SICK PicoScan acquisition, live preview, and calibration (ICP).

## Structure
- `backend/` FastAPI backend (receiver, calibration, device management)
- `frontend/` Next.js UI (system configurator, live preview)

## Quick Start

### Backend
1. Create and activate a Python venv.
2. Install dependencies.
3. Run the API server.

Example:
```powershell
cd backend
python -m venv venv
.\venv\Scripts\Activate.ps1
pip install -r requirements.txt
uvicorn app.main:app --reload
```

### Frontend
```powershell
cd frontend
npm install
npm run dev
```

The UI runs on `http://localhost:3000` and the API on `http://localhost:8000`.

## Post-acquisition analysis (log measurement)

### Capture integrity and encoder prerequisites

Install the encoder's matching IODD on the TDC and select the correct IO-Link
port. The decoded `Velocity.value` is interpreted as RPM. A byteArray response
is not decoded as speed; it raises `encoder_velocity_missing`. Configure the
wheel circumference/diameter and confirm units against a reference movement.

Configure the actual transmitted `segments_per_scan` for each scanner, not an
estimate reduced to hide packet loss. The receiver locks segment IDs only after
five matching observations of that configured count; it never lowers the count.
Let this initialization finish before triggering a capture. A listener restart
is required after changing the scanner's segment layout.

Completed frames enter bounded 256-frame queues. Every recorded profile requires
all enabled scanners, paired within 20 ms of first-segment host receipt. Encoder
speed is integrated between bracketing samples, with a maximum 500 ms sample gap;
each scanner frame gets its own interpolated Y position. No speed extrapolation
or fallback fixed speed is used in encoder mode. At stop, frame timestamps are
cut off consistently; trailing unmatched frames within the 20 ms pairing boundary
are not saved. Finalization waits up to 600 ms for the following encoder sample.

These are host-receipt timestamps, **not hardware-synchronized scanner/encoder
measurement timestamps**. Network latency, polling and wheel slip still limit
accuracy. Validate length on the target device at constant and varying speeds.
Correct configuration is required: software cannot prove that a configured
segment count matches the scanner or recover lost UDP datagrams.

Preview filters never alter stored acquisition points. Log analysis uses explicit
paired-profile IDs rather than deriving profile counts from rounded Y positions.
`measurement_valid`, `quality_status` and `quality_issues` accompany results in
UI, history, MQTT and OPC UA. Old history entries without quality are `unknown`.
Invalid results remain available for diagnostics; TCP result transmission is
suppressed, MQTT `result_ready` is false, and OPC UA numeric result nodes carry a
bad/uncertain status. Consumers must check quality, not just numeric payloads.

Run regression checks with:

```powershell
python -m unittest discover -s backend/tests
python -m unittest discover -s integrations/opcua_server -p test_quality.py
node --test frontend/tests/measurement-quality.test.cjs
```

The OPC UA tests require `integrations/opcua_server/requirements.txt`; frontend
tests require installed frontend dependencies. ARM CI retains verbose pip/build
logs as the `backend-build-logs` artifact even when dependency installation fails.

### Standalone analysis

Standalone script (no changes to existing backend endpoints). After you stop
acquisition, run:

```powershell
python backend/scripts/analyze_after_acquisition.py --window-profiles 10 --min-points 50
```

The script fetches the latest accumulated cloud and computes:
- circle fit per 10 profiles
- diameter, circumference, area
- volume integration along the Y axis (motion)

Results are saved to `analysis_results.json`.

## Notes
- Configure devices in the System Configurator.
- Frame settings are saved in backend config.
- Auto-calibration uses ICP (Open3D).

## Changelog (latest)
- Added TDC digital trigger support (token auth + DigitalIO).
- System Configurator: TDC settings, trigger input, delays, encoder port.
- Motion: encoder wheel settings + derived speed.
- Acquisition: live status via SSE (recording, speed, trigger state/source).
- Added backend restart endpoint and UI action.
