# PicoScan Validation

Osobny katalog do szybkiej walidacji strumienia UDP `compact` z `picoScan100`.

## Co tu jest

- `requirements.txt` - minimalne zaleznosci do testu
- `receive_compact_frame.py` - probe odbierajacy segmenty UDP i skladajacy obserwacje ramek
- `continuous_capture.py` - dluzsza diagnostyka zero-loss z liczeniem brakujacych segmentow i ramek
- `picoscan_viewer.py` - viewer diagnostyczny chmury punktow na zywo albo z zapisanego JSON
- `build_3d_stack_viewer.py` - interaktywny builder 3D z kolejnych profiliskanow
- `analyze_probe_report.py` - szybka analiza kompletności ramek z `probe_report.json`
- `profile_sensor_config.py` - zapisuje profil aktualnej konfiguracji sensora na podstawie pomiaru streamu
- `output/` - raporty JSON z ostatniego testu

## Przygotowanie

PowerShell:

```powershell
py -3.10 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

## Uruchomienie

Domyslnie skrypt nasluchuje na `0.0.0.0:2116`, filtruje pakiety od `192.168.0.10` i zapisuje raport do `output/`.

```powershell
.\.venv\Scripts\python.exe .\receive_compact_frame.py
```

Do dluzszego testu stabilnosci i utraty segmentow:

```powershell
.\.venv\Scripts\python.exe .\continuous_capture.py --duration 30 --max-frames 300
```

Przyklad z jawnymi parametrami:

```powershell
.\.venv\Scripts\python.exe .\receive_compact_frame.py `
  --listen-ip 0.0.0.0 `
  --port 2116 `
  --expected-sender 192.168.0.10 `
  --capture-seconds 20 `
  --observe-frames 3
```

## Co raportuje skrypt

- czy w ogole przyszly pakiety UDP
- `FrameNumber` i `SegmentCounter`
- ile segmentow wyglada na jedna ramke
- czy liczniki segmentow sa ciagle i startuja od `0`
- zapis najlepszej odebranej ramki do `output/latest_complete_frame.json`

## Viewer

Live viewer odbiera segmenty UDP, sklada je do pelnej ramki i pokazuje dwa rzuty chmury punktow:

```powershell
.\.venv\Scripts\python.exe .\picoscan_viewer.py --mode live
```

Domyslnie viewer pokazuje tylko pelne skany, tj. ramki z kompletem segmentow `0..4`.
Jesli chcesz celowo zobaczyc takze ramki niepelne, uruchom:

```powershell
.\.venv\Scripts\python.exe .\picoscan_viewer.py --mode live --show-incomplete
```

W trybie live viewer domyslnie sam wykrywa stabilna zmiane wzorca segmentow
po zmianie konfiguracji sensora, np. przejscie z `[0,1,2,3,4]` na `[1,2,3]`.
Jesli chcesz to wylaczyc i trzymac wzorzec na sztywno:

```powershell
.\.venv\Scripts\python.exe .\picoscan_viewer.py --mode live --disable-auto-recalc
```

Mozesz tez otworzyc ostatnia zapisana ramke bez laczenia z sensorem:

```powershell
.\.venv\Scripts\python.exe .\picoscan_viewer.py --mode file
```

Viewer pokazuje:

- aktualny `FrameNumber`
- liste `SegmentCounter` zlozonych do ramki
- liczbe punktow
- liczbe beamow na segment i liczbe ech
- szacowany packet rate i frame rate

To jest celowo oddzielne od backendu, zeby latwo sprawdzic, czy problem gubienia segmentow jest w samym streamie UDP, czy dopiero w logice glownej aplikacji.

## Analiza raportu

Po dluzszym przechwycie mozesz zrobic szybkie podsumowanie wzorcow brakow:

```powershell
.\.venv\Scripts\python.exe .\analyze_probe_report.py
```

`continuous_capture.py` zapisuje osobny raport do `output/continuous_capture_report.json` i zwraca:

- `zero_loss_pass=true`, jesli wszystkie zamkniete ramki byly kompletne
- `missing_frame_numbers`, jesli zniknely cale numery ramek
- `missing_counters`, jesli w ramce zabraklo np. `2` albo `4`

## Profil konfiguracji

Jesli zmienisz czestotliwosc, filtr katowy albo inna konfiguracje sensora i chcesz szybko zapisac nowy profil:

```powershell
.\.venv\Scripts\python.exe .\profile_sensor_config.py --label freq25_filterOff --duration 20
```

Skrypt zapisze np.:

- `likely_segments_per_frame`
- `frame_rate_hz_estimated`
- `segment_rate_hz_estimated`
- `beams_per_segment`
- `zero_loss_pass`

do pliku `output/profile_<label>.json`.

## Budowanie 3D z profili

Jesli chcesz kliknac `Start`, zebrac np. 50 kolejnych pelnych profiliskanow,
przesunac kazdy o `1 mm` w osi `Y` i zbudowac z tego obraz 3D:

```powershell
.\.venv\Scripts\python.exe .\build_3d_stack_viewer.py --profiles 50 --y-step-mm 1
```

W oknie:

- `Start` czyści poprzedni stos i zaczyna zbierać nowe profile
- `Reset` czyści aktualny stos
- `Save` zapisuje wynik do `output/profile_stack_<timestamp>.json`

## Wazna uwaga o konfiguracji sensora

Z dokumentacji `ScanSegmentAPI` wynika, ze dla UDP adres podawany do odbiornika to adres karty sieciowej komputera klienckiego, nie adres sensora. Z dokumentacji `sick_scan_xd` wynika tez, ze sensor musi miec ustawiony docelowy adres i port wyjsciowy danych, np. przez SOPAS/CoLa (`ScanDataEthSettings`, `ScanDataFormat`, `ScanDataEnable`).

Jesli raport pokazuje same timeouty, to zwykle oznacza jedno z ponizszych:

- sensor nie wysyla jeszcze danych na adres tego komputera
- port docelowy w sensorze jest inny niz `2116`
- firewall blokuje UDP
- urzadzenie wysyla w innym formacie niz `compact`
