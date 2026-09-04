# Änderungsprotokoll

## 0.1.0-beta.2

- SKI-Kopplungsrichtung für die Hager Witty Flow korrigiert
- Eingabefeld für den 40-stelligen Wallbox-SKI ergänzt
- Doppelpunkte, Bindestriche und Leerzeichen im eingegebenen SKI werden normalisiert
- eingegebener SKI wird gegen das TLS-Zertifikat der Wallbox geprüft
- verständliche Fehler für ungültige und abweichende SKIs ergänzt
- Kopplung läuft in einem nicht blockierenden Home-Assistant-Fortschrittsdialog

## 0.1.0-beta.1

- erste direkte Home-Assistant-Integration ohne Bridge oder Add-on
- SHIP/TLS, SPINE-Geräteerkennung sowie OPEV- und OSCEV-Steuerung
- Sensoren, Binärsensoren, Stromvorgaben, Schalter, Diagnose und Wiederverbindung
