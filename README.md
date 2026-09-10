# EEBUS Direct für Home Assistant

Direkte, lokale EEBUS-Anbindung für Wallboxen wie die **Hager Witty Flow**. Die Integration läuft vollständig im Home-Assistant-Prozess. Sie benötigt weder `eebus_bridge` noch ein Add-on, einen REST-Dienst oder einen separaten Container.

> **Hardware-Beta:** Protokoll, Datenmodell, UI und Schreibbestätigungen sind automatisiert getestet. Die konkrete Kombination aus Home-Assistant-Version und Firmware einer echten Hager Witty Flow muss noch vor Ort geprüft werden. Teste Steuerbefehle zunächst mit beaufsichtigtem Fahrzeug und kleiner Leistung.

## Funktionen

Die Integration zeigt nur Funktionen an, welche die Wallbox in ihrer SPINE-Gerätebeschreibung tatsächlich ankündigt.

- Einrichtung und Kopplung vollständig über die Home-Assistant-Oberfläche
- mDNS-Erkennung von `_ship._tcp.local`
- eigenes, dauerhaftes P-256-Zertifikat und Prüfung des Wallbox-Zertifikats gegen den in Home Assistant eingegebenen SKI
- dynamische SPINE-Erkennung ohne fest codierte Feature- oder Measurement-IDs
- Ladestatus, Verbindung, Fahrzeugerkennung und Wallbox-Fehler
- Ladeleistung, Ladevorgangsenergie, Gesamtenergie, Strom und Spannung je Phase sowie Netzfrequenz
- maximaler Ladestrom über OPEV, auf Wunsch je Phase, sofern angekündigt
- Laden freigeben/pausieren über die OPEV-Stromvorgabe
- PV-Stromempfehlung über OSCEV und Freigabe aller gesetzten Grenzwerte
- Fahrzeug-SoC, SoH und Reichweite, falls Fahrzeug und ISO-15118-Verbindung diese Werte bereitstellen
- Diagnose-Download mit Use Cases, Entitäten, Limits, Rohmesswerten und einer kompakten SPINE-Telegrammübersicht; der private Schlüssel wird nie ausgegeben
- automatische Wiederverbindung und ein manueller „EEBUS neu verbinden“-Knopf

Nicht über Standard-EEBUS bereitgestellte Herstellerfunktionen – etwa RFID-Verwaltung, Firmware-Updates oder die Installateurkonfiguration – sind nicht Teil dieser Integration. Eine Hardware-Phasenumschaltung wird ebenfalls nicht ausgelöst.

## Installation

### HACS

1. Öffne **HACS → Integrationen → Drei-Punkte-Menü → Benutzerdefinierte Repositories**.
2. Trage `https://github.com/chris8601/homeassistant-eebus-direct` ein und wähle die Kategorie **Integration**.
3. Öffne anschließend **EEBUS Direct** in HACS und wähle **Herunterladen**.
4. Starte Home Assistant vollständig neu.
5. Öffne **Einstellungen → Geräte & Dienste → Integration hinzufügen** und wähle **EEBUS Direct**.

### Manuell

1. Falls die bisherige CoreTex-Integration mit dem Domainnamen `eebus` installiert ist, entferne oder benenne deren Ordner zuerst um. Zwei Integrationen mit demselben Domainnamen können nicht parallel laufen.
2. Kopiere den Ordner `custom_components/eebus` dieses Projekts nach `/config/custom_components/eebus` auf deinem Home-Assistant-System.
3. Starte Home Assistant vollständig neu.
4. Öffne **Einstellungen → Geräte & Dienste → Integration hinzufügen** und wähle **EEBUS Direct**.

## Kopplung mit der Hager Witty Flow

1. Home Assistant und Wallbox müssen sich im selben multicast-fähigen LAN befinden. Bei Docker oder einer VM ist Host-Netzwerk beziehungsweise funktionierendes mDNS-Forwarding erforderlich.
2. Wähle im Einrichtungsdialog die **LAN-IP des Home-Assistant-Hosts** – nicht die IP der Wallbox.
3. Starte die Suche und wähle die gefundene Hager-Wallbox.
4. Öffne die EEBUS-Einstellungen der Hager Witty Flow und lies dort den vollständigen **SKI der Wallbox** ab.
5. Trage diesen Wallbox-SKI in Home Assistant ein. Doppelpunkte, Bindestriche und Leerzeichen dürfen mitkopiert werden; intern wird auf 40 Hexadezimalzeichen normalisiert.
6. Aktiviere an der Wallbox den EEBUS-Kopplungsmodus und starte die Verbindung in Home Assistant. Der eingegebene SKI wird gegen das tatsächlich präsentierte TLS-Zertifikat geprüft.
7. Die erste Verbindung kann bis zu drei Minuten dauern. Nach erfolgreicher SPINE-Erkennung erscheint die Wallbox samt unterstützten Entitäten unter **Geräte & Dienste**.

Das lokale Home-Assistant-Zertifikat liegt ausschließlich in `.storage/eebus_direct`. Lösche diesen Ordner nicht, solange die Kopplung bestehen bleiben soll; andernfalls ist eine erneute Kopplung erforderlich.

## Bedienung und Sicherheit

„Maximaler Ladestrom“ setzt ein EEBUS-OPEV-Limit. Der erlaubte Bereich wird aus der Wallbox gelesen; bei der Witty Flow ist typischerweise mindestens 6 A zu erwarten. Der Schalter „Laden freigegeben“ verwendet zum Pausieren eine aktive 0-A-Vorgabe. Ob die jeweilige Firmware diese Vorgabe akzeptiert, erkennt die Integration an der EEBUS-Antwort und meldet eine Ablehnung in Home Assistant.

Das ist eine energiewirtschaftliche Steuerung und **keine Sicherheitsabschaltung**. Für Arbeiten an Anlage oder Fahrzeug muss weiterhin normgerecht freigeschaltet und gegen Wiedereinschalten gesichert werden.

## Fehlersuche

Wenn kein Gerät gefunden wird:

- EEBUS und Kopplungsmodus der Wallbox aktivieren
- prüfen, ob UDP 5353/mDNS zwischen Home Assistant und Wallbox funktioniert
- bei Docker/VM Host-Netzwerk oder einen mDNS-Reflector verwenden
- VLAN-, WLAN-Client- und Multicast-Isolation prüfen
- kontrollieren, ob als Schnittstelle wirklich die LAN-IP des Home-Assistant-Hosts gewählt wurde

Für Debug-Protokolle in `configuration.yaml`:

```yaml
logger:
  logs:
    custom_components.eebus: debug
```

Nach dem Neustart findest du unter der Integration über das Drei-Punkte-Menü **Diagnosedaten herunterladen**. Für einen Hardware-Fix sind besonders hilfreich: Home-Assistant-Version, Witty-Flow-Firmware, Diagnose-JSON und das Log vom Beginn der Kopplung bis zum ersten Datenabruf. Private Schlüssel sind darin nicht enthalten; prüfe Logs dennoch vor dem Weitergeben auf Netzwerkdaten.

## Entwicklung und Prüfung

```bash
python3 -m unittest discover -s tests -v
python3 -m ruff check custom_components/eebus tests --exclude custom_components/eebus/_vendor
python3 -m ruff format --check custom_components/eebus tests --exclude custom_components/eebus/_vendor
python3 -m compileall -q custom_components tests
```

## Lizenz und Herkunft

Apache License 2.0. Der eingebettete Python-EEBUS-Unterbau basiert auf `ULudo/eebus-sdk` und wurde für die direkte Home-Assistant-Nutzung, Standard-CEM-Profile sowie SPINE-Schreib- und Antwortsemantik angepasst. Details stehen in `NOTICE`.
