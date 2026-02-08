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
