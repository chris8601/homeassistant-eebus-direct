# Änderungsprotokoll

## 0.1.0-beta.9

- Reihenfolge des SPINE-`CmdType` für Partial-Reads korrigiert: `function`, `filter`, Funktionsdaten
- Regressionstest prüft jetzt die tatsächlich auf dem SHIP-Kanal übertragene EEBUS-JSON-Reihenfolge

## 0.1.0-beta.8

- von der Hager Witty Flow angekündigte SPINE-Partial-Reads werden jetzt mit `cmdControl.partial` und funktionsspezifischen Selektoren angefordert
- Messwerte, elektrische Parameter, Ladegrenzen, Konfiguration und Identifikation können dadurch von Partial-Read-only-Features gelesen werden
- abgelehnte Leseanforderungen werden über `msgCounterReference` erkannt und mit EEBUS-Fehlernummer in den Diagnosedaten ausgegeben

## 0.1.0-beta.7

- Endlosschleife nach einem sofortigen Fehler im Kopplungsdialog verhindert
- gespeichertes EEBUS-Zertifikat und privaten Schlüssel vor jeder Verwendung auf Übereinstimmung geprüft
- beschädigte Identitätsdateien werden automatisch als atomar geschriebener Zertifikat-/Schlüsselsatz erneuert
- parallele Identitätserzeugung durch Zeroconf-Erkennungen abgesichert
- doppelte Zeroconf-Konfigurationsflüsse desselben SHIP-Geräts verhindert

## 0.1.0-beta.6

- SPINE-Werte werden jetzt von allen relevanten EVSE- und EV-Features gelesen statt nur vom tiefsten EV-Feature
- Binding und Subscriptions werden vor dem ersten Datenabruf eingerichtet; statische Daten werden bei anfänglichen Timeouts erneut gelesen
- unbekannter Ladezustand wird nicht mehr fälschlich als „lädt nicht“ oder „ladebereit“ ausgegeben
- Diagnose-Download enthält die rohe SPINE-Gerätebeschreibung sowie statische und dynamische Antworten für die Hardware-Analyse

## 0.1.0-beta.5

- standardkonforme SHIP-Antwort auf `accessMethodsRequest` umgesetzt
- nicht standardisierte `accessMethodsResponse`-Nachricht im normalen Verbindungsaufbau entfernt
- Phasenprotokollierung für CMI, Hello/Vertrauen, Protokollauswahl, PIN und Zugriffsmethoden ergänzt
- Wartezeiten für die erste SPINE-Gerätebeschreibung der Wallbox erhöht

## 0.1.0-beta.4

- Schema-Serialisierungsfehler der Home-Assistant-Oberfläche unter Python 3.14 behoben
- eigene IPv4-Prüffunktion aus dem UI-Schema entfernt und in die Verarbeitung der Benutzereingabe verschoben
- verständliche Feldmeldung für ungültige IPv4-Adressen ergänzt

## 0.1.0-beta.3

- 500-Fehler beim Laden des Konfigurationsflusses auf Home Assistant 2024.6 bis 2025.6 behoben
- Abhängigkeit von der erst später eingeführten Klasse `OptionsFlowWithReload` entfernt
- Änderungen an Schnittstelle und Aktualisierungsintervall laden die Integration nun über einen kompatiblen Update-Listener neu

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
