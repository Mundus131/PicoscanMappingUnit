# Picoscan Mapping Unit - Backend

Backend API aplikacji do przetwarzania chmur punktów z urządzeń Picoscan LIDAR.

## Funkcje

- **Multi-device Management**: Obsługa wielu urządzeń Picoscan jednocześnie
- **Point Cloud Processing**: Transformacja (translacja, rotacja), łączenie chmur punktów
- **Automatic Calibration**: Automatyczna kalibracja i wyrównanie chmur z wielu czujników
- **Data Interpolation**: Interpolacja brakujących danych w chmurze punktów
- **Measurements**: Pomiary i wymiarowanie na chmurze punktów
- **REST API**: Pełny REST API do komunikacji z frontendem

## Struktura projektu

```
backend/
├── venv/                      # Wirtualne środowisko Python
├── app/
│   ├── __init__.py
│   ├── main.py               # Główny plik aplikacji FastAPI
│   ├── api/
│   │   ├── endpoints/
│   │   │   ├── devices.py    # Endpointy do zarządzania urządzeniami
│   │   │   └── point_cloud.py # Endpointy przetwarzania chmur punktów
│   ├── core/
│   │   └── device_manager.py # Zarządzanie urządzeniami
│   ├── models/               # SQLAlchemy models (przyszłość)
│   ├── schemas/              # Pydantic schemas
│   ├── services/
│   │   └── point_cloud_processor.py # Logika przetwarzania chmur
│   └── utils/                # Narzędzia pomocnicze
├── config/
│   ├── settings.py           # Konfiguracja aplikacji
│   └── picoscans_config.json # Konfiguracja urządzeń
├── tests/                    # Testy jednostkowe
├── logs/                     # Logi aplikacji
├── data/                     # Dane (chmury punktów, baza danych)
├── requirements.txt          # Zależności Python
├── .env.example              # Przykład zmiennych środowiskowych
└── README.md
```

## Instalacja

### 1. Aktywuj wirtualne środowisko

Na Windows (PowerShell):
```powershell
.\venv\Scripts\Activate.ps1
```

Na Linux/Mac:
```bash
source venv/bin/activate
```

### 2. Zainstaluj zależności

```bash
pip install -r requirements.txt
```

### 3. Skonfiguruj zmienne środowiskowe

```bash
cp .env.example .env
# Edytuj .env według potrzeb
```

### 4. Uruchom serwer

```bash
python app/main.py
```

Serwer będzie dostępny pod adresem: `http://localhost:8000`

## API Dokumentacja

Po uruchomieniu serwera, dokumentacja API dostępna jest pod:
- **Swagger UI**: `http://localhost:8000/docs`
- **ReDoc**: `http://localhost:8000/redoc`

## Główne endpointy

### Zarządzanie urządzeniami
- `GET /api/v1/devices` - Pobierz wszystkie urządzenia
- `GET /api/v1/devices/{device_id}` - Pobierz konkretne urządzenie
- `POST /api/v1/devices` - Dodaj nowe urządzenie
- `PUT /api/v1/devices/{device_id}` - Aktualizuj urządzenie
- `DELETE /api/v1/devices/{device_id}` - Usuń urządzenie
- `POST /api/v1/devices/{device_id}/connect` - Połącz się z urządzeniem
- `POST /api/v1/devices/{device_id}/disconnect` - Odłącz się od urządzenia

### Przetwarzanie chmur punktów
- `POST /api/v1/point-cloud/merge` - Połącz chmury z wielu urządzeń
- `POST /api/v1/point-cloud/interpolate-missing-data` - Interpoluj brakujące dane
- `POST /api/v1/point-cloud/statistics` - Pobierz statystyki
- `POST /api/v1/point-cloud/filter` - Filtruj punkty
- `GET /api/v1/point-cloud/measure/{device_id}` - Mierz odległość między punktami

## Konfiguracja urządzeń

Konfiguracja urządzeń znajduje się w pliku `config/picoscans_config.json`:

```json
{
  "devices": [
    {
      "device_id": "picoscan_1",
      "name": "Picoscan 45°",
      "ip_address": "192.168.1.100",
      "port": 2111,
      "enabled": true,
      "calibration": {
        "translation": [0.0, 0.0, 0.0],
        "rotation_deg": [45.0, 0.0, 0.0],
        "scale": 1.0
      }
    }
  ]
}
```

## Planowane funkcjonalności

- [ ] Rzeczywista komunikacja z urządzeniami Picoscan
- [ ] Obsługa enkodera do dynamicznych map 3D
- [ ] Różne profile prędkości akwizycji
- [ ] Zaawansowana interpolacja danych (kriging)
- [ ] Baza danych do przechowywania konfiguracji
- [ ] Eksport chmur punktów (PLY, LAS, XYZ)
- [ ] Rejestracja skanów w czasie rzeczywistym

## Technologie

- **FastAPI**: Nowoczesny framework API dla Python
- **Pydantic**: Walidacja danych
- **NumPy**: Przetwarzanie danych numerycznych
- **SciPy**: Zaawansowane obliczenia naukowe
- **Uvicorn**: ASGI server
- **Pytest**: Framework testowania

## Autor

Picoscan Mapping Unit Backend v0.1.0
