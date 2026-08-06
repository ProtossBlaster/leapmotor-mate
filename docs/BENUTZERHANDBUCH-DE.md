# LeapMotor Mate — Benutzerhandbuch

> **Mate-Version:** v3.6.0 · **Sprache:** Deutsch
> Dieses Handbuch richtet sich an alle, die Mate *nutzen*, nicht an die, die es entwickeln. Es erklärt, wie
> Sie es von Grund auf einrichten und was jede Seite tut. Für die internen technischen Details gibt es `ARCHITECTURE.md`.

---

## Inhaltsverzeichnis

1. [Was Mate ist (und was nicht)](#1-was-mate-ist-und-was-nicht)
2. [Bevor Sie beginnen: die Voraussetzungen](#2-bevor-sie-beginnen-die-voraussetzungen)
3. [Installation](#3-installation)
4. [Erster Start: die geführte Einrichtung](#4-erster-start-die-geführte-einrichtung)
5. [Die Oberfläche kennenlernen](#5-die-oberfläche-kennenlernen)
6. [Die Seiten, eine nach der anderen](#6-die-seiten-eine-nach-der-anderen)
   - [Übersicht](#übersicht) · [Fahrten](#fahrten) · [Karte](#karte) · [Ladevorgänge](#ladevorgänge)
   - [Ladepreise](#ladepreise) · [Statistik](#statistik) · [Monatsbericht](#monatsbericht)
   - [Batteriezustand](#batteriezustand) · [Wartung](#wartung) · [Befehle](#befehle)
   - [Planung](#planung) · [Fahrzeug vorbereiten](#fahrzeug-vorbereiten)
   - [Navigation](#navigation) · [Fahrzeug](#fahrzeug) · [Wallbox](#wallbox)
7. [Einstellungen](#7-einstellungen)
8. [Die Integrationen im Detail (Wallbox, ABRP, MQTT)](#8-die-integrationen-im-detail)
9. [Demo-Modus](#9-demo-modus)
10. [Häufige Fragen und Fehlerbehebung](#10-häufige-fragen-und-fehlerbehebung)
11. [Glossar](#11-glossar)

---

## 1. Was Mate ist (und was nicht)

**LeapMotor Mate** ist eine Anwendung, die Sie selbst installieren (self-hosted) und die als „Begleiter" für Ihr
elektrisches Leapmotor-Auto dient. Sie verbindet sich mit der **Leapmotor-Cloud** (derselben, mit der auch die
offizielle App spricht), liest den Zustand des Autos aus und rekonstruiert daraus eigenständig:

- Ihre **Fahrten** (Strecke, Dauer, Verbrauch, Rekuperation beim Bremsen);
- Ihre **Ladevorgänge** (Energie, Leistung, Typ, Kosten);
- die **Kosten** und die **Effizienz** über die Zeit;
- den **Batteriezustand** und die **Wartungsfälligkeiten**.

Zusätzlich können Sie damit **Befehle aus der Ferne senden** (Verriegeln, Klima, Fahrzeug vorbereiten,
Planungen…) und, wenn Sie möchten, die Daten mit **Home Assistant** (über MQTT), mit
**A Better Routeplanner (ABRP)** und mit Ihrer **Wallbox** verbinden.

**Was Mate NICHT tut / wichtige Einschränkungen:**

- **Es spricht nicht direkt mit dem Auto.** Alles läuft über die Leapmotor-Cloud. Wenn Mate die Cloud
  „abfragt" (Polling), liest es den **zuletzt bekannten Zustand**: Es weckt das Auto *nicht* auf und entlädt die
  Batterie *nicht*. Es ist ein sicherer und günstiger Vorgang.
- **Nur 100 % elektrische Autos (BEV).** Unterstützt werden **T03, B05, B10, C10** in den elektrischen
  Versionen. Die **REEV**-Versionen (mit Range-Extender auf Benzin) werden **nicht** unterstützt: Die
  Berechnungen von Energie/Verbrauch/Kosten würden die falsche Batteriekapazität verwenden und wären verfälscht.
- **Nur europäische Cloud (Leapmotor International / Stellantis).** Konten, die auf Servern anderer Regionen
  (z. B. China) registriert sind, können sich nicht anmelden. Außerhalb Europas ist Mate derzeit nicht nutzbar.
- **Es ist kein Buchhaltungswerkzeug.** Es schätzt die Kosten *anhand der Telemetrie*; es verfolgt keine
  Zahlungsmethoden, Rechnungen oder Abonnements der Ladesäulen.

---

## 2. Bevor Sie beginnen: die Voraussetzungen

Um Mate einzurichten, benötigen Sie drei Dinge:

1. **Ein Leapmotor-Konto, das nur für Mate bestimmt ist.** ⚠️ **Sehr wichtig.** Erstellen (oder bestimmen) Sie
   ein Leapmotor-Konto, das Sie **ausschließlich** für Mate verwenden. Leapmotor erlaubt nur wenige gleichzeitige
   Sitzungen pro Konto: Ist dasselbe Konto auch in der offiziellen App, in einer anderen Integration oder in einer
   zweiten Mate-Instanz angemeldet, „verdrängen" sich die Clients gegenseitig die Sitzung. Das Ergebnis ist eine
   Flut von *„Token ungültig"* / wiederholten erneuten Anmeldungen, das Auto geht **offline** und es gehen
   **Daten verloren** (nicht erfasste Fahrten und Ladevorgänge). Das ist die häufigste Ursache der gemeldeten Probleme.
   *Lösung:* ein zweites Konto mit einem **nur in Mate verwendeten Passwort**.

2. **Das Zertifikat der Leapmotor-App** (`app.crt` + `app.key`). Es ist ein für **alle gleiches** Zertifikat (es ist
   das der App, nicht das Ihres Kontos), das für die Kommunikation mit der Cloud nötig ist. Es wird aus einem
   öffentlichen Repository heruntergeladen — der Assistent gibt Ihnen den direkten Link
   ([github.com/markoceri/leapmotor-certs](https://github.com/markoceri/leapmotor-certs)).

3. **E-Mail, Passwort und Bedien-PIN des Kontos.** Der **vierstellige PIN** ist derselbe, den Sie auch in der
   offiziellen App verwenden, um die Fernbefehle (Verriegeln, Klima…) zu autorisieren.

> 💡 Sie wollen nur einen Blick darauf werfen, ohne etwas einzurichten? Überspringen Sie alles und nutzen Sie den
> **[Demo-Modus](#9-demo-modus)**: Mate startet mit einem Monat realistischer Beispieldaten, ohne Auto und ohne Konto.

---

## 3. Installation

Mate läuft auf dieselbe Weise in drei Umgebungen (die Oberfläche ist identisch):

- **Als Add-on von Home Assistant** — der einfachste Weg, wenn Sie bereits Home Assistant haben. Man fügt das
  Add-on-Repository hinzu, installiert „LeapMotor Mate" und öffnet es aus der Seitenleiste von HA (Ingress). In
  diesem Fall kann Mate auch Ihre **Wallbox** direkt aus Home Assistant auslesen.
- **Als eigenständiger Docker-Container** (zum Beispiel auf einem NAS) — über `docker-compose`. In diesem Fall ist
  die App vom Browser aus über **Port 4000** erreichbar (`http://ADRESSE-DES-SERVERS:4000`).
- **Als Desktop-Anwendung** — [**MateDesktop**](https://github.com/ProtossBlaster/MateDesktop) ist dasselbe
  Mate, verpackt für **macOS und Windows**, für alle, die weder Home Assistant noch Docker betreiben:
  herunterladen, öffnen, und derselbe Einrichtungsassistent erscheint. Unter Windows wird es **in einer
  `.zip`** ausgeliefert — erst entpacken, dann den Installer starten: Eine blanke `.exe` aus dem Internet
  hat bei SmartScreen noch keinen Ruf und wird beim Eintreten gestoppt.

Die Schritt-für-Schritt-Anleitungen zur Installation (Repository, Compose usw.) finden Sie im **README** des
Projekts und auf der **Docker-Hub**-Seite. Nach dem Start ist der *erste Zugriff* für beide gleich und wird hier
unten beschrieben.

> 📱 **Auf dem Handy.** Mate ist keine Handy-App und kann keine sein: Es muss über Jahre hinweg die
> Cloud abfragen, und ein Handy hält an, was im Hintergrund läuft. Sie können es aber **auf den
> Startbildschirm legen**: Öffnen Sie Mate im Browser des Handys und wählen Sie *Teilen → Zum
> Home-Bildschirm* auf dem iPhone bzw. *⋮ → Zum Startbildschirm hinzufügen* auf Android. Es bekommt
> Mates eigenes Symbol und öffnet sich im Vollbild, ohne Adress- und Werkzeugleiste — rund 110 px
> Bildschirm zurück. Es bleibt eine Verknüpfung zu dem Server, den Sie betreiben: Ist der aus,
> öffnet sie nichts.

> 🔒 **Backup.** Alle Daten von Mate liegen in einem dauerhaften Ordner (`/data`): die Datenbank, der
> Verschlüsselungsschlüssel der Geheimnisse (`secret.key`) und das Zertifikat. Wenn Sie ein Backup erstellen,
> **sichern Sie die Datenbank zusammen mit ihrer `secret.key`** — ohne den Schlüssel sind gespeicherte Passwörter
> und Token nicht mehr lesbar. Über die Seite Einstellungen können Sie jederzeit ein Backup der Datenbank herunterladen.
> Wenn Sie eine Datenbank **ohne** ihren Schlüssel wiederherstellen, schreibt Mate das jetzt namentlich ins Log —
> welche Geheimnisse es nicht lesen kann und was zu tun ist — statt später als Anmeldefehler zu scheitern.
> Fahrten, Ladevorgänge und Kosten sind nicht verschlüsselt und kommen immer zurück.

---

## 4. Erster Start: die geführte Einrichtung

Beim ersten Zugriff zeigt Mate einen **Assistenten** (geführtes Verfahren). Oben können Sie die Sprache wählen
(🇩🇪 Deutsch). Dann:

### Schritt 0 — Wählen Sie, wie Sie beginnen

Zwei Schaltflächen:

- **▶ Mein Auto einrichten** — die eigentliche Einrichtung (weiter unten).
- **🧪 Demo ausprobieren** — wechselt in den Demo-Modus mit Beispieldaten. Sie können jederzeit aussteigen.

### Schritt 1 — App-Zertifikat

Mate fragt Sie nach dem TLS-Zertifikat der Leapmotor-App. Sie haben zwei Möglichkeiten:

- **Laden Sie die Dateien** `app.crt` und `app.key` hoch (Standardmodus), oder
- **Fügen Sie den PEM-Text** der beiden Dateien ein (Schaltfläche *„Stattdessen den PEM-Text einfügen"*).

Laden Sie sie über den angezeigten Link herunter, laden Sie sie hoch und drücken Sie **Zertifikat speichern**.
Dieser Schritt erscheint nur, wenn das Zertifikat nicht bereits im Image vorhanden ist.

### Schritt 2 — Anmeldung am Konto

Geben Sie ein:

- **E-Mail des Leapmotor-Kontos**
- **Passwort**
- **Bedien-PIN** (4 Stellen)

> ⚠️ Hier erinnert Sie Mate daran, ein **nur für Mate bestimmtes Konto** zu verwenden (siehe
> [Voraussetzungen](#2-bevor-sie-beginnen-die-voraussetzungen)).

Drücken Sie **🔍 Mein Auto erkennen**. Mate prüft die Zugangsdaten und liest aus der Cloud **Modell und
Fahrgestellnummer (VIN)**. Wenn alles gut geht, sehen Sie eine Karte „Auto erkannt" mit `Leapmotor <Modell> · VIN
···xxxxxx`.

### Schritt 3 — Batterie

Je nach Modell:

- Wenn die europäische Version **nur eine einzige Variante** der Batterie hat, erkennt Mate sie selbst (z. B. T03 →
  37,3 kWh);
- wenn es **mehrere Varianten** gibt (z. B. B10 Pro 56,2 kWh / Pro Max 67,1 kWh; C10 RWD 69,9 / AWD 81,9), wählen
  Sie Ihre;
- wenn die Erkennung nicht gelingt, können Sie die **Kapazität von Hand eingeben** (in kWh).

> Die angegebene Kapazität ist die **nutzbare/netto** (die, die für Verbrauch und Kosten wirklich zählt) und kann
> später jederzeit unter Einstellungen → Batterie korrigiert werden.
> Daneben steht die **SoH-Referenz**: die Neukapazität, an der die Batteriegesundheit gemessen wird.
> Mate erfasst sie beim ersten Speichern der Kapazität und rührt sie danach nicht mehr an — damit ein
> gemessener (bereits gealterter) Wert die Gesundheit nicht auf ~100 % zurücksetzt und die Alterung
> verbirgt. Wurde sie falsch erfasst, kann die Gesundheit über 100 % liegen: dort korrigierbar.

### Schritt 4 — Verbinden

Drücken Sie **Verbinden & starten**. Mate speichert die Konfiguration, verbindet sich und führt Sie zur
**Übersicht**. Ab diesem Moment beginnt der „Poller", im Hintergrund Daten zu sammeln: Die ersten Fahrten und
Ladevorgänge erscheinen nach und nach, während Sie fahren und laden.

---

## 5. Die Oberfläche kennenlernen

Die Oberfläche besteht aus:

- **Seitenmenü (Sidebar)** — die Liste der Seiten (siehe unten). Auf kleinem Bildschirm öffnet es sich mit dem
  Symbol ☰.
- **Kopfzeile (Header)** — Titel der Seite, ein eventueller **Hinweis auf ein verfügbares Update** (↑ vX.Y.Z) und
  die Schaltfläche **🔄 Jetzt aktualisieren**.
- **Schaltfläche „Jetzt aktualisieren"** — erzwingt ein sofortiges Auslesen des Fahrzeugzustands, ohne auf den
  automatischen Zyklus zu warten. Nützlich, nachdem Sie einen Befehl gegeben haben.

Am Ende des Menüs finden Sie **⚙️ Einstellungen** und **🚪 Abmelden** — Letzteres *nur, wenn Sie ein
Zugangspasswort gesetzt haben*; es beendet die Passwort-Sitzung und sonst nichts. Ohne Passwort ist
es nicht da, weil es nichts zu beenden gibt.

**Um die PIN des Autos zu ändern 🆕** — wenn Sie sie am Auto ändern, muss nichts getrennt werden:
unter **Einstellungen → Fahrzeug** finden Sie unter der Kontoadresse die **Bedien-PIN**. Sie wird
zweimal eingegeben, mit einem Auge zum Nachlesen, und gilt sofort — sowohl für Befehle von der Seite
als auch für die aus Home Assistant. Gewünscht von **@alextchao** (#225).

**Um das Leapmotor-Konto zu trennen** — etwas ganz anderes — gehen Sie zu **Einstellungen → Fahrzeug
→ 🔓 Abmelden**. Das löscht die gespeicherten Zugangsdaten und öffnet den Einrichtungsassistenten
erneut; Zertifikat, Fahrten und Ladevorgänge bleiben.

Viele Seiten **aktualisieren sich von selbst** etwa alle 30 Sekunden, sodass die „lebendigen" Werte (Status,
laufender Ladevorgang…) frisch bleiben, ohne die Seite neu zu laden.

**Sprache, Währung und Einheiten** ändern Sie unter *Einstellungen → 🌍 Sprache & Währung*:

- **Sprache:** Italiano, English, Français, Deutsch, Polski, Nederlands, Português.
  *(Ein geschriebenes Handbuch wie dieses gibt es auf Deutsch, Englisch, Italienisch und Französisch.)*
- **Währung:** für die Kosten (€, £, …).
- **Einheiten:** metrisch (km, °C) oder imperial UK/US (Meilen, °F). Die Daten bleiben immer in km/°C gespeichert;
  es ändert sich nur, wie sie **angezeigt** werden.

---

## 6. Die Seiten, eine nach der anderen

Die Reihenfolge hier unten entspricht der des Seitenmenüs.

### Übersicht
**(Menü: Übersicht)** — Die Startseite. Oben gibt es eine **Hauptkarte** mit dem Bild des Autos und dem
Live-Status:

- **Ladestand (SoC)** und geschätzte Reichweite;
- **Statussymbole**, die die Farbe wechseln: Verriegelung (grün = verriegelt, bernsteinfarben = offen),
  Kofferraum (rot, wenn offen), Fenster (violett, wenn offen), Klima usw.;
- **Schnellbefehle** (schließen/öffnen, Auto finden…), die bereits den aktuellen Zustand „kennen";
- wenn das Auto **lädt**, zeigt eine **Animation** den Energiefluss und ein Schild mit der Schätzung der Zeit
  „bis X %" (X = das Ladelimit, das Sie im Auto eingestellt haben);
- ein Schild **„Kabel angeschlossen / Laden abgeschlossen"**, wenn das Kabel eingesteckt ist, aber gerade nicht
  aktiv geladen wird.

Wenn das Auto über den **V2L-Adapter** (Vehicle-to-Load) ein externes Gerät versorgt, erscheint ein **V2L-Block**
mit dem **Status** (Aktiv / Inaktiv), der **Momentanleistung** in Watt — angegeben **abzüglich des Eigenverbrauchs
des Autos (~300 W)**, sodass sie dem entspricht, was Ihr Gerät tatsächlich zieht — mit einem **0–3500-W-Balken**
und der **in der Sitzung entnommenen Energie**. Er aktualisiert sich etwa alle **10 s**, solange eine Sitzung
läuft. Der Block ist **schreibgeschützt**: V2L wird am Auto gestartet (Gang auf **P** + ein angeschlossenes Gerät),
nicht aus Mate. Erkannt wird ab etwa **42 W** (der Auflösung des Stromsensors des Autos — eine winzige ~10-W-Last
bleibt unsichtbar).

Weiter unten finden Sie Ministatistiken und einen **Indikator für die „Fahrzeug-Reaktion"** (ein Punkt
🟢/🟡/🔴, ⚪ wenn keine Daten vorliegen): Er fasst zusammen, wie zuverlässig das Auto auf die zuletzt gesendeten
Befehle reagiert hat.

### Fahrten
**(Menü: Fahrten)** — Die Liste Ihrer Fahrten, eine pro Fahrt. Für jede Fahrt sehen Sie **Strecke, Dauer,
Verbrauch (kWh/100 km), zurückgewonnene Energie** beim Bremsen und die geschätzten **Kosten**.

- Wenn Sie auf eine Fahrt klicken, öffnen Sie das **Detail** mit dem **GPS-Verlauf** auf der Karte und den Daten
  dieser einzelnen Fahrt.
- Sie können zwei versehentlich getrennte Fahrten **zusammenführen** (Zusammenführen 🔗) oder sie wieder
  **trennen** und eine Fahrt **löschen**.
- Kurze Pausen (Ampeln, Staus) **trennen** eine Fahrt **nicht**: Eine Fahrt bleibt eine einzige Zeile.
- **Offizieller Verbrauch aus der Cloud 🆕** — sofern vorhanden, stammen **Verbrauch, Effizienz und
  Kosten** einer Fahrt aus der **offiziellen Leapmotor-Angabe** (der echten Aufteilung **Fahren / Klima /
  Sonstiges**) statt nur aus der Batterie-%-Schätzung. Direkt nach einer Fahrt sehen Sie die Schätzung mit
  dem Hinweis **⏳ vorläufig**; sobald die Cloud die Daten verarbeitet hat (meist einige Dutzend Minuten),
  wird sie **von selbst** durch den offiziellen Wert ersetzt und die **Aufschlüsselung** erscheint im
  Detail. Ältere Fahrten haben eine Schaltfläche **„Mit offiziellen Daten umwandeln“**. Wenn die Cloud die
  Daten einer Fahrt nicht hat (kommt vor, bei jedem vernetzten Auto), bleibt die **Schätzung** — kein
  Fehler. **Immer aktiv**, keine Einrichtung.
- **Ihre Notiz + Fahr-Tags 🆕** (#107) — im Detail einer Fahrt können Sie eine **freie Notiz** (Verkehr,
  Wetter, Streckentyp, jede Anmerkung) schreiben und den verwendeten **Fahrmodus** (Comfort / Normal /
  Sport) sowie **One-Pedal** (ein/aus) angeben. Mate kann sie nicht vom Auto lesen — Leapmotor sendet sie
  nicht an die Cloud — Sie tragen sie also von Hand ein; sie helfen zu erklären, warum zwei ähnliche
  Fahrten unterschiedlich verbraucht haben.

### Karte
**(Menü: Karte)** — Alle Orte, an denen Sie gefahren sind, auf einer einzigen Karte. Die aktuelle Position des
Autos ist dabei (hat das letzte Datum aus der Cloud kein gültiges GPS, **behält Mate die letzte gültige
Position** bei, anstatt die Karte verschwinden zu lassen), und dazu:

- **Die Strecke jeder Fahrt**, als zusammenhängende Linie gezeichnet statt als lose Punkte, und nie über zwei
  verschiedene Fahrten hinweg verbunden.
- **Eine gestrichelte magentafarbene Brücke dort, wo das Signal verloren ging.** Ein Tunnel, ein Funkloch, ein
  Aussetzer der Cloud: Ist die Lücke zwischen zwei aufgezeichneten Punkten deutlich größer als der Abtastrhythmus
  *dieser* Fahrt, zeichnet Mate die Verbindung **gestrichelt** statt durchgezogen. Eine durchgezogene Linie
  heißt *hier ist das Auto wirklich gefahren*; eine gestrichelte heißt *hier haben wir es verloren*, und die
  Gerade zwischen den Enden ist keine Straße.
- **Häufige Orte**, als Blasen in der Größe Ihrer Aufenthaltshäufigkeit, und die **Ladesäulen**, die Sie
  benutzt haben.
- **„Angezeigte Fahrten“**, ein Feld in der Legendenzeile. Eine lange Historie macht die Karte zu einem
  Gewirr überlagerter Linien; Sie können sie daher auf die N zuletzt gefahrenen Fahrten begrenzen. **0 heißt
  alle**, und so beginnt es. Die Begrenzung lässt jede gezeichnete Strecke außerdem näher an der echten
  Straße liegen, weil sich das Punktebudget auf weniger Fahrten verteilt.

### Ladevorgänge
**(Menü: Ladevorgänge)** — Die Liste der Ladevorgänge. Für jede: **hinzugefügte Energie (kWh)**, **Spitzenleistung**,
**Typ** und **Kosten**, mit dem **tatsächlichen €/kWh** gut sichtbar. Der Typ ist mit einem Etikett klassifiziert:

- **Zuhause** (Ihre Wallbox **oder eine Haushaltssteckdose**), **AC** (öffentlicher Wechselstrom),
  **Schnell/FAST** (DC), **HPC** (Ultraschnellladung) und **✎ Manuell**.
- **Zuhause bedeutet nicht Wallbox.** *Zuhause* sagt, **wo** Sie geladen haben, nicht woraus — auch
  eine gewöhnliche Steckdose in der Garage ist ein Ladevorgang zuhause. Für die Abrechnung macht das
  einen Unterschied: Ist der Zähler einer Wallbox eingebunden (siehe *Wallbox* weiter unten), wird
  der Ladevorgang über die **vom Zähler gelieferte Energie** abgerechnet; ohne ihn über die **in der
  Batterie angekommene Energie**, genau wie ein öffentlicher Ladevorgang. Dazwischen liegt der
  Wärmeverlust des Ladegeräts, typischerweise 10–15 %.
- **✎ Manuell**: Für öffentliche Ladesäulen mit komplizierten Tarifen (Abonnements, Sitzungskosten…) können Sie
  **den tatsächlich gezahlten Gesamtbetrag von Hand eintragen**; dieser Wert überschreibt die automatische Schätzung.
- **Die kWh der Ladesäule 🆕** (#222) — an einer öffentlichen Ladesäule hat Mate **keinen Zähler**: es
  liest nur, was in die Batterie ging, während die Säule abrechnet, was aus ihrem eigenen Zähler kam.
  Diesen Wert können Sie eintragen: auf der Ladekarte, unter den drei Kacheln, gibt es ein **✎**; das
  Feld **öffnet sich nur, wenn Sie es öffnen**, und ist **immer leer** — ein versehentlicher Klick
  ändert also nichts, und ein leeres OK lässt alles wie es war. *Entfernen* nimmt einen falschen Wert
  zurück. Von da an **bepreist** diese Zahl die Ladung, genau wie der Wallbox-Zähler zu Hause, und
  zeigt den **Wirkungsgrad** (wie viel das Bordladegerät in Wärme umgewandelt hat). Die Energie, die
  Mate ausweist, bleibt die **an der Batterie gemessene**.
- **Der Monat nennt beides 🆕** — über dem Kalender: *„154,93 kWh geliefert · 142,57 in der Batterie"*.
  Das Erste kam aus den Zählern (Wallbox oder die von Ihnen eingetragenen kWh), das Zweite kam im
  Akku an. Dazwischen liegt der Umwandlungsverlust, den Sie bezahlen.
- Auch Ladevorgänge, die stattgefunden haben, während das Auto ausgeschaltet/offline war, werden aus dem Sprung des
  Ladestands **rekonstruiert**.
- **Ihre Notiz 🆕** (#107) — jeder Ladevorgang hat eine **freie Notiz** (direkt über *Ladevorgang löschen*) für das,
  was die Zahlen nicht erfassen: wo die Ladesäule stand, Schatten/Unterstand, ihre Zuverlässigkeit, die
  Parkbedingungen, das Wetter, jede persönliche Anmerkung.

### Ladepreise
**(Menü: Ladepreise)** — Hier legen Sie fest, **was Sie für die Energie zahlen**, damit Mate die Kosten berechnen
kann. Sie können einen Preis **für jeden Ladetyp** (Zuhause, AC, Schnell, HPC) festlegen und wählen zwischen:

- **Festtarif** (ein einziger €/kWh), oder
- **Zeitfenster (TOU)** — unterschiedliche Preise je nach Wochentag und Tageszeit (z. B. F1/F2/F3, Nacht
  günstiger).

Der Preis für **Zuhause** speist die Kosten der Heimladungen und, in der Folge, die Kosten der Fahrten (berechnet
auf dem „durchschnittlichen" Energiepreis in der Batterie zum Zeitpunkt der Fahrt).

> Die Änderungen an den Preisen gelten **nur für zukünftige Ladevorgänge**: Bereits berechnete Kosten ändern sich
> nicht. Mit den Zeitfenstern können Sie auch wählen, *wie* eine Sitzung auf die Fenster aufgeteilt wird —
> *Genaue Aufteilung* (anhand der realen Leistungskurve) oder *Nach Startzeit* (die ganze Sitzung zu dem Fenster,
> in dem sie begonnen hat).

### Statistik
**(Menü: Statistik)** — Ihre Durchschnitte und Summen über die Zeit: **Gesamtstrecke** und Anzahl der Fahrten,
**durchschnittliche Strecke pro Fahrt**, **Fahrzeit**, **durchschnittlicher Verbrauch** (gewichtet nach der
Strecke) und **bester**, **verbrauchte und geladene Energie**, **Rekuperation** insgesamt und im Durchschnitt,
Anzahl der **Ladesitzungen**, mit den entsprechenden **Trends** (Effizienz und Rekuperation über die Zeit). Die
Summen enthalten jetzt auch eine Karte **V2L gesamt** mit der über die gesamte Historie via V2L entnommenen
kumulierten Energie.

**Kosten pro 100 km 🆕** — was 100 km wirklich kosten: **jeder ausgegebene Euro**, geteilt durch
**jeden gefahrenen Kilometer**. Kein Preis pro kWh und keine Schätzung — die Summe des Bezahlten
über der Summe des Gefahrenen, also einschließlich der kWh, die das Auto nirgendwohin bewegt haben
(Klima, Vorkonditionierung, Verluste des Ladegeräts). Bei einer Version mit Range Extender kommt der
Kraftstoff neben dem Strom dazu. Fehlt bei einer Ladung der Preis, sagt die Karte es, denn der echte
Wert liegt dann höher. Sie folgt Ihren Einheiten: in Meilen wird daraus „pro 100 mi".

**Seit wann diese Zahlen gelten 🆕** — eine Zeile am Seitenanfang erinnert daran, dass **alle**
Summen der Statistik das sind, was Mate seit der Installation erfasst hat, mit dem Startdatum — und
**nicht** der Gesamtstand des Fahrzeugtachos.

**Was jede Zahl abdeckt 🆕** — *Durchschnittsverbrauch* ist der Mittelwert über die Kilometer, die
einen Verbrauch **haben**; darunter erscheint „über N km", wenn das weniger als die Gesamtstrecke
ist. *Verbrauchte Energie* summiert nur die Fahrten, deren Energie Mate kennt: eine Fahrt ohne diesen
Wert wird **ausgelassen** statt als Null gezählt, und die Kachel sagt, über wie viele Fahrten sie
spricht. Bei einem Auto, bei dem jede Fahrt ihren Verbrauch trägt — also fast immer — erscheint
davon nichts.

### Monatsbericht
**(Menü: Monatsbericht)** — Eine Zusammenfassung **Monat für Monat**: wie viel Sie gefahren sind, wie viel Energie
Sie verbraucht und geladen haben, wie viel Sie ausgegeben haben. Praktisch, um die Entwicklung im Auge zu behalten.
Er enthält außerdem die Karten **offizieller Verbrauch** (Heute / Diese Woche / Dieser Monat) aus der Cloud.

Er öffnet immer den **laufenden Monat**, auch am Ersten ohne einen einzigen Kilometer: ein leerer
Monat sagt das, statt Ihnen stillschweigend den vorherigen zu zeigen. Und für einen noch leeren Monat
erscheint kein Vergleich mit dem vorherigen — jede Kachel läse −100 %, was den Kalender beschreibt
und nicht Ihr Fahren.

**Woher der Verbrauch kommt und wann Mate ihn übergeht.** *Durchschnittsverbrauch* und *Verbrauchte
Energie* sind normalerweise die offizielle Monatssumme des Autos. Diese Summe ist nur so vollständig
wie die Verbindung Ihres Autos war: Konnte das Auto während einer Fahrt die Cloud nicht erreichen,
fehlt diese Fahrt darin. Liegt die Summe weit unter dem, was Mates eigene Fahrten für denselben
Monat ergeben, zeigt Mate **die eigene Zahl** — dieselbe wie auf der Seite Fahrten — und schreibt es
unter die Kachel. Die Aufteilung Fahren / Klima / Sonstiges bleibt die des Autos, mit einer Zeile,
die sagt, dass sie nur den in der Cloud angekommenen Teil abdeckt.

### Batteriezustand
**(Menü: Batteriezustand)** — Eine **Schätzung des Gesundheitszustands (SoH)** der Batterie: wie viel nutzbare
Kapazität gegenüber dem Neuzustand verblieben ist. Für jeden Ladevorgang teilt Mate die Energie, die es als in den
Akku fließend **gemessen** hat (Spannung × Strom, über die Sitzung integriert), durch den Prozentsatz, den
dieser Ladevorgang hinzugefügt hat. Dieses Verhältnis ist eine Schätzung der Kapazität des gesamten Akkus, und ihr
Verlauf über die Zeit — oder über die Kilometer, ganz wie Sie wollen — ist die Alterung.

Drei Dinge zur Berechnung, denn sie ändern die Bedeutung der Zahl.

- **Die Rechnung endet bei 95 %.** Bei einem LFP-Akku ändert sich die Spannung in der Mitte des Bereichs kaum,
  daher **zählt** das BMS die Ladung, statt sie zu lesen, und driftet; nahe am oberen Ende steigt die Kurve
  endlich an und das BMS **richtet sich neu aus** — es fügt Prozentpunkte hinzu, für die keine Energie bezahlt
  hat. Sie mitzuzählen ließe den Akku kleiner erscheinen, und am schlimmsten bei einer kurzen Nachladung bis
  100 %, wo sie den größten Teil des Anstiegs ausmachen. Die Rechnung endet daher bei 95 %: Der Ladevorgang zählt
  weiterhin, nur sein letztes Stück bleibt außen vor.
- **Größere Ladevorgänge wiegen mehr, und zwar anteilig.** Die Kennzahl summiert Energie und Prozentsatz der
  jüngsten Ladevorgänge, statt einzeln zu mitteln: Ein Ladevorgang über 50 Punkte wiegt etwa viermal so viel wie einer
  über 13. Und dafür wird nichts verworfen.
- **Kalte Ladevorgänge werden angezeigt, aber ausgeschlossen** — ein LFP liest im Kalten zu niedrig — ebenso Ladevorgänge,
  die fast leer begonnen haben, oder solche, bei denen das BMS springt.

**Die Zahl trägt ein ± bei sich, und das ist der ehrliche Teil.** Es ist die **Streuung** der dahinterliegenden
Ladevorgänge, keine Genauigkeit: Die Energie ist gemessen, aber der Prozentsatz, durch den sie geteilt wird, ist
eine Zahl, die das BMS gezählt hat — und die driftet. Ein schmales Band heißt, dass Ihre Ladevorgänge untereinander
übereinstimmen, nicht dass der Akku wirklich diese Größe hat. Bei einem einzigen Ladevorgang erscheint gar kein ±:
Eine Messung hat keine Streuung zu berichten.

Es ist also eine **Schätzung** — keine Labordiagnose — und sie stabilisiert sich, je mehr Ladevorgänge sich
ansammeln.

### Wartung
**(Menü: Wartung)** — Die **Wartungsfälligkeiten** Ihres Autos, basierend auf dem **offiziellen Programm Ihres
Modells** (T03, B05, B10, C10). Für jeden Service (z. B. Inspektion, Bremsflüssigkeit, Innenraumfilter, Reifen…)
sehen Sie zwei Annäherungsbalken: einen für die **Kilometer** und einen für die **Zeit**, denn fällig wird, was
zuerst eintritt.

- Sie können einen **Service erfassen** („heute bei X km erledigt") direkt von der Seite aus: Die nächste
  Fälligkeit wird neu berechnet.
- Für ein **neues Auto** ohne Vorgeschichte können Sie ein **Referenzdatum/-kilometerstand** festlegen, damit die
  Fälligkeiten ab der Übergabe starten („erste Inspektion in…") statt als „nie durchgeführt" zu erscheinen.
- Das **Zulassungs-/Übergabedatum** ist jetzt editierbar: Klicken Sie auf das **✏️** neben dem gespeicherten
  Datum, um einen Fehler zu korrigieren (der neue Wert überschreibt den alten).
- Die Strecken berücksichtigen die gewählte Einheit (km oder Meilen).

### Befehle
**(Menü: Befehle)** — Die **Fernbefehle**. Von hier aus können Sie:

- **verriegeln/entriegeln**, den **Kofferraum** öffnen, das **Auto finden** (Hupe/Lichter);
- das **Klima** steuern: Kühlen, Heizen, Enteisen, Lüften, **Ausschalten**;
- **Sitzheizung**, **Lenkrad** und **Spiegel** aktivieren (wo unterstützt);
- das **Ladelimit** verwalten.

**Die Klimakarte** zeigt für jeden Modus eine eigene **Kachel** — **A/C AUTO · Kühlen · Heizen · Lüften ·
Enteisen** — und es leuchtet immer **nur eine gleichzeitig**, genau dem echten Modus des Autos entsprechend, wie in
der offiziellen App. Darunter gibt es drei Bedienelemente: einen **Temperatur-Schieberegler**, einen
**Lüfter-Schieberegler** (Stufe 1–7) und einen **Umluft-Schalter** (Frischluft ↔ Umluft):

- In den **drei manuellen Modi** (Kühlen / Heizen / Lüften) stellen Sie **Zieltemperatur** und **Lüfterstufe** ein;
  das Auto **bleibt in diesem Modus und behält den Wert**.
- Im **AUTO**-Modus regelt das Auto Lüfter und Umluft selbst: Diese beiden Bedienelemente zeigen den aktuellen Wert
  daher nur **lesend** an, während die **Temperatur weiterhin einstellbar** bleibt.
- **Lüften** schaltet zuverlässig echte Lüftung (**nur Luft**, weder Heizen noch Kühlen) aus jedem Zustand ein.

Wenn Sie einen Befehl geben, aktualisiert Mate die Oberfläche sofort „optimistisch" und bestätigt ihn dann bei der
nächsten Auslesung. Wenn die Cloud annimmt, das Auto aber nicht innerhalb weniger Sekunden bestätigt, sehen Sie
einen **bernsteinfarbenen** Hinweis („gesendet, könnte funktioniert haben") — das ist kein Fehler: Oft geht der
Befehl trotzdem durch (hängt vom Empfang/Standby des Autos ab).

### Planung
**(Menü: Planung)** — Die **Planungen** des Autos:

- **Geplantes Laden** (und das **Ladelimit**);
- **Geplantes Klima** — 5 Voreinstellungen (Kühlen / Heizen / Lüften / Enteisen / Auto) mit künftiger Startzeit;
  Sie können sie erstellen, ändern und abbrechen.

### Fahrzeug vorbereiten
**(Menü: Fahrzeug vorbereiten)** — Die Funktion „**das Auto mit einem Tipp vorbereiten**": Sie bringt den
Innenraum auf die gewünschte Temperatur (und verbundene Funktionen) **sofort** oder zu einer **geplanten Zeit**.
Sie können auch alles ausschalten.

**🆕 Automatisch beim Einschalten** — Statt jedes Mal die Taste zu drücken, können Sie Mate die
Vorbereitung **von selbst ausführen lassen, sobald das Auto in den Ready-Zustand geht** (Einschalten).
Aktivieren Sie **Automatisch beim Einschalten**, legen Sie einmal fest, was sie tun soll — Klima-Preset
und Wunschtemperatur, wie weit die Fenster geöffnet werden, **Belüftung oder Heizung** der Sitze
Fahrer/Beifahrer, Lenkrad- und Spiegelheizung — und speichern Sie.

Sie können eine **optionale Bedingung für die Innentemperatur** hinzufügen: die Vorbereitung **nur
ausführen, wenn der Innenraum über** einem Wert liegt (z. B. nur über 25 °C vorkühlen) **oder nur, wenn
er unter** einem liegt (z. B. nur unter 5 °C vorheizen). **Lassen Sie die Bedingung aus, läuft sie bei
jedem Einschalten**, unabhängig von der Temperatur. Zwei Dinge zur Bedingung: Sie betrachtet die
**Innen**temperatur (das Auto liefert keine Außentemperatur) und wird **einmalig entschieden, im Moment
des Einschaltens** — ändert sich der Innenraum später während der Fahrt, löst sie kein zweites Mal aus.

Sie läuft **einmal pro Einschalten** (sie wiederholt sich nicht, solange Sie eingeschaltet bleiben, und
auch nicht für eine spätere Fahrt in derselben Sitzung), ignoriert kurze Signalstörungen und löst nie
erneut aus, nur weil Mate neu gestartet ist.

### Navigation
**(Menü: Navigation)** — *Sendet ein Ziel an die Navigation des Autos* und **findet die Ladestationen in der
Nähe**. Die Seite hat drei Teile:

- **Ziel** — geben Sie eine **Adresse** ein (und, falls nötig, die **Stadt**), drücken Sie **Suchen**: Das Ziel
  erscheint auf der Karte und mit **🧭 Ans Auto senden** schicken Sie es an die Navigation an Bord. *Die Suche nach
  Adresse erfordert einen Geocoding-Schlüssel* (siehe [Einstellungen → Adresssuche](#7-einstellungen)).
- **⚡ Ladestationen — „Ladestationen finden"** — sucht die **öffentlichen Ladestationen rund um das Auto** (nutzt
  dessen aktuelle GPS-Position). Sie können einstellen:
  - **Max. Entfernung** — 500 m, 1, 2, **5 km** (Standard) oder 10 km;
  - **Ergebnisse pro Seite** — 25, 50 oder 100;
  - **Netz / Betreiber** (optional) — um einen bestimmten Anbieter zu filtern (z. B. Electra, Ionity, Enel X Way,
    Be Charge, Plenitude, A2A, Atlante, Ewiva, Tesla…).

  Die Ergebnisse erscheinen sowohl als **⚡-Markierungen auf der Karte** als auch in einer **Liste** darunter, mit
  **Name, Entfernung** und, wo verfügbar, der **Echtzeit-Verfügbarkeit** (🟢/🔴 „jetzt verfügbar", z. B. im
  öffentlichen italienischen Netz). Tippen Sie eine Station in der Liste an, um sie **auf der Karte zu sehen**, und
  mit einem Klick können Sie sie **als Ziel verwenden** und dann ans Auto senden. Wenn im gewählten Radius nichts
  liegt, erweitert Mate und zeigt **die nächstgelegenen**.

  > Die Stationssuche **erfordert keine Schlüssel** (sie nutzt offene Karten + öffentliche Stationsdatenbanken);
  > die optionalen Schlüssel unter *Einstellungen → ⚡ Ladestationen* (OpenChargeMap, TomTom) reichern sie an. Es
  > ist jedoch nötig, dass das Auto eine bekannte **GPS-Position** hat.
- **Aktuelle Position des Autos** — die Adresse des Autos und eine Karte mit seiner 🚗-Markierung.

### Fahrzeug
**(Menü: Fahrzeug)** — Die Karte mit dem **vollständigen Zustand** des Autos: alle auf Ihrem Modell verfügbaren
Sensoren (Ladung, Reichweite, Innentemperatur, Gang, Türen, Fenster, Reifen, Verriegelungen, Ladezustand…). Mate
liest jetzt auch die **Lüfterstufe** (1–7), die **Luftumwälzung** (Frischluft / Umluft) und den **aktiven
Klimamodus** (AUTO / Kühlen / Heizen / Lüften) aus. Mate zeigt **nur das, was Ihr Auto wirklich meldet** (manche
Modelle stellen bestimmte Daten nicht bereit).

### Wallbox
**(Menü: Wallbox)** — Wenn Sie eine Wallbox verbunden haben (siehe
[Integrationen](#8-die-integrationen-im-detail)), sehen Sie hier ihre Daten **live** (Leistung, Energie), die
**Zusammenfassung** und die Liste der **Sitzungen** sowie gegebenenfalls die **Steuerungen** (z. B. maximaler
Strom), wenn Ihre Wallbox sie über Home Assistant bereitstellt.

---

## 7. Einstellungen

**(Menü: ⚙️ Einstellungen)** — Die Seite ist in **Ziehharmonika-Karten** organisiert: Sie öffnen jeweils eine. Sie
ist in drei Spalten unterteilt.

**Spalte 1 — Fahrzeug und Fahren**

- **🌍 Sprache & Währung** — Sprache der Oberfläche, Währung der Kosten, **Einheiten** (metrisch/imperial).
- **Fahrzeug** — Modell und VIN Ihres Autos sowie **mit welchem Leapmotor-Konto sich diese Instanz
  anmeldet**. Das Konto ist wichtig, wenn Sie Mate mehrfach betreiben — eine zweite Instanz, eine zum
  Testen, eine pro Auto: Modell und VIN beschreiben das *Auto*, zwei Instanzen am selben Auto waren von
  innen also bisher nicht zu unterscheiden. Hier gibt es auch die Schaltfläche **🔓 Vom Konto abmelden**
  (Logout), um ein anderes Konto zu verbinden: Sie löscht *nur* die gespeicherten Zugangsdaten, **nicht** Ihre
  Fahrten/Ladevorgänge und auch nicht das Zertifikat.
- **Batterie** — die **Kapazität** in kWh, die für alle Berechnungen verwendet wird; korrigierbar. Wenn Mate eine
  aus Ihren Daten „gemessene" Schätzung hat, schlägt es sie Ihnen vor.
- **Abfrageintervall** — wie oft Mate den Zustand aus der Cloud liest, mit zwei Schiebereglern: **geparkt**
  (10 s–5 min, Standard 30 s) und **in Fahrt** (10–60 s, Standard 10 s). Häufigeres Auslesen entlädt das Auto
  nicht, erzeugt aber mehr Verkehr zur Cloud.
- **Ladeerkennung** — die **Stromschwelle** (in Ampere), oberhalb derer Mate „laufender Ladevorgang" annimmt. Nur
  herabsetzen, wenn Sie sehr langsame, nicht erkannte Ladevorgänge haben.

**Spalte 2 — Integrationen**

- **ABRP** — Senden von Telemetrie an A Better Routeplanner (siehe [§8](#8-die-integrationen-im-detail)).
- **Adresssuche** — der Dienst, um Adressen ↔ Koordinaten auf der Seite Navigation zu übersetzen (Geoapify
  *empfohlen*, LocationIQ, TomTom). Erfordert einen kostenlosen **Schlüssel** des gewählten Dienstes.
- **⚡ Ladestationen** — aktiviert die **Namen der Ladestationen** bei den Ladevorgängen (📍) und akzeptiert optionale
  Schlüssel (OpenChargeMap, TomTom), um die Suche anzureichern. Standardmäßig **deaktiviert**.
- **Wallbox** — verbinden Sie Ihre Wallbox für die **realen Kosten** und die eventuellen Steuerungen (siehe
  [§8](#8-die-integrationen-im-detail)).
- **MQTT → Home Assistant** — veröffentlicht die Daten des Autos als Entitäten in Home Assistant (siehe
  [§8](#8-die-integrationen-im-detail)).

**Spalte 3 — Daten und Wartung**

- **🔐 Zugang** *(nur eigenständiges Docker — unter dem Home-Assistant-Add-on authentifiziert der
  Ingress bereits jede Anfrage, und die Karte erscheint nicht)* — ein Passwort, um Mate zu öffnen.
  Es lohnt sich: ohne eines kann alles in Ihrem Netz Mate öffnen, und Mate kann Ihr Auto öffnen.

  Sie geben es **zweimal** ein, denn es lässt sich danach nirgends mehr nachlesen — gespeichert wird
  ein gesalzener Hash, nie der Klartext. **Wenn Sie es verlieren**, sind Sie nicht endgültig
  ausgesperrt: das Feld *Neues Passwort* fragt das alte nicht ab, Sie vergeben also von jedem noch
  angemeldeten Gerät aus einfach ein neues. Ist kein Gerät mehr angemeldet, überschreibt die
  Umgebungsvariable `MATE_AUTH_PASSWORD` das gespeicherte.

- **Datenbank** — Größe der DB und **Aufbewahrung der Positionen** (Retention): Sie können die GPS-Punkte „für
  immer" behalten (Standard) oder die älter als 6/12/18/24 Monate löschen, um Platz zu sparen. *Es werden nur die
  Positionen entfernt*: Fahrten, Ladevorgänge und Ladekurven bleiben erhalten.
- **Export / Backup** — laden Sie **Fahrten (CSV)**, **Ladevorgänge (CSV)** und ein **Backup der Datenbank** herunter.
- **🩺 Diagnose** — eine Momentaufnahme des Systems (Version, Modell, Zählwerte, letzte Abfrage, aktive
  Integrationen), die Möglichkeit, die **Logs anzusehen** (Poller/Web) und vor allem ein **Diagnosepaket
  herunterzuladen**, indem Sie die gewünschten Teile ankreuzen (Info, Poller-Log, Web-Log, **Rohsignale**). Das
  Paket ist **bereits von sensiblen Daten bereinigt**: **GPS entfernt** und VIN/Geheimnisse verschleiert, sodass es
  sicher anzuhängen ist, wenn Sie um Hilfe bitten. Die Integrationszeile führt den **Wallbox-Schalter** und
  **Home Assistant** getrennt auf: Ersteres sagt, ob die Funktion aktiviert ist, Letzteres nur, ob Mate HA
  erreichen kann. Es gibt auch eine **Suche nach verpassten Ladevorgängen**, während das Auto schlief.
- **⚙️ Erweitert** — Feineinstellungen für erfahrene Benutzer: Mindestschwelle, um einen übersprungenen Ladevorgang zu
  **rekonstruieren**, Schwelle des **Ruhestromverlusts (Vampire Drain)**, kW-Schwelle, um **DC** zu unterscheiden,
  und Mindesttemperatur für die Berechnung des **Batteriezustands**. Es gibt eine Schaltfläche, um die
  **Standardwerte wiederherzustellen**.

> 🆕 Wenn eine neue Funktion ankommt, kann ihre Karte ein **Neu**-Abzeichen anzeigen, bis Sie sie das erste Mal
> öffnen.

---

## 8. Die Integrationen im Detail

Alle Integrationen sind **optional** und standardmäßig **deaktiviert**. Sie werden über die **Einstellungen**
konfiguriert.

### Wallbox (für die realen Ladekosten)
Wenn Sie Ihre Wallbox verbinden, verwendet Mate die **tatsächlich gelieferte Energie** (auf der Wechselstromseite),
um die Kosten der Heimladungen zu berechnen, statt sie aus der Änderung des Prozentsatzes zu schätzen.

Mate liest die Wallbox **über Home Assistant**:

1. Aktivieren Sie unter *Einstellungen → Wallbox* die Option **Wallbox vorhanden**.
2. **Wenn Sie das Add-on von Home Assistant nutzen**, kann Mate HA von selbst erreichen: Es ist nicht nötig,
   Adresse oder Token einzugeben.
3. **Wenn Sie Mate als eigenständigen Docker nutzen**, geben Sie die **URL von Home Assistant** ein (z. B.
   `http://192.168.1.10:8123`) und ein **langlebiges Zugriffstoken** von HA und drücken dann **Verbindung testen**.
4. Mit den **Schlüsselwörtern** können Sie Mate helfen, die richtigen Entitäten Ihrer Wallbox zu erkennen (z. B.
   `wallbox, charger, evse, keba, pulsar`). Einige bekannte Wallboxen (z. B. V2C Trydan) werden automatisch erkannt;
   die „Fallen"-Entitäten (Solar/Haus) werden ausgeschlossen.
5. Öffnen Sie die Entitätsliste, um zu prüfen, ob Mate die richtigen **Energie-/Leistungssensoren** erfasst hat.
6. Option **„Zuhause automatisch"**: weist Ladevorgänge, die an Ihrer Wallbox erfolgt sind, automatisch das Etikett
   **Zuhause** zu.

### ABRP (A Better Routeplanner)
Sendet die Telemetrie des Autos an ABRP für die Routenplanung in Echtzeit.

1. Aktivieren Sie unter *Einstellungen → ABRP* die Option **ABRP aktivieren**.
2. Fügen Sie Ihr ABRP-**Token** ein (Sie finden es in den „Generic"-/Telemetrie-Einstellungen Ihres ABRP-Kontos).
3. Speichern. Der Status der Integration erscheint in der Kopfzeile der Karte.

### MQTT → Home Assistant
Veröffentlicht den Zustand des Autos (Ladung, Reichweite, Position, Türen, Ladezustand…) als **Entitäten in Home
Assistant**, mit **Auto-Discovery**. Sie können das Auto auch über die Entitäten von HA **steuern** — einschließlich eines beschreibbaren **Ladelimits** (`number`) zum Einstellen des Ziel-SoC und einer beschreibbaren **Ladeplan**-`text`-Entität, die einen JSON-Plan für Automationen entgegennimmt (`{"start":"23:00","soc":90}` — jedes Feld ist optional, und was Sie weglassen, bleibt unverändert). Zum Klima kommen die **beschreibbare Lüfterstufe** (`number`, 1–7), der **beschreibbare Umluft-Schalter** (Frischluft ↔ Umluft) und ein **Klimamodus**-Sensor (AUTO / Kühlen / Heizen / Lüften) hinzu. Außerdem gibt es drei **schreibgeschützte** V2L-Entitäten: **`V2L Active`** (Binärsensor), **`V2L Power`** (W) und **`V2L Session Energy`** (Wh) sowie einen Binärsensor **`Ready`**, der angeht, sobald das Auto eingeschaltet ist — noch bevor es losfährt, also solange eine Automatisierung überhaupt noch handeln kann.

1. Bereiten Sie einen **MQTT-Broker** vor (üblicherweise das *Mosquitto*-Add-on in Home Assistant).
2. Aktivieren Sie unter *Einstellungen → MQTT* die Option **MQTT aktivieren** und füllen Sie aus:
   - **Broker** (z. B. `192.168.1.10` oder `core-mosquitto`) und **Port** (Standard `1883`);
   - **Benutzername** und **Passwort** des Brokers;
   - **Präfix** der Topics (Standard `leapmotor`);
   - Optionen: **Discovery** (empfohlen), **TLS** und **TLS unsicher**, wenn Sie selbstsignierte Zertifikate
     verwenden.
3. Drücken Sie **Verbindung testen**, um die Verbindung zu prüfen, dann **Speichern**. Innerhalb weniger Sekunden
   erscheinen die Entitäten in Home Assistant.

> Für die Befehle über MQTT verlangt das Auto weiterhin den PIN: Mate verwendet ihn automatisch mit den
> gespeicherten Zugangsdaten.

---

**Wenn mehrere Mate denselben Broker nutzen 🆕** — etwa das normale Add-on und das BetaTester-Add-on
— geben Sie jedem ein **eigenes Topic-Präfix** (*Einstellungen → MQTT*). Bei gleichem Präfix und
gleichem Auto sind sie für Home Assistant **ein Gerät**: das zweite scheint nicht zu funktionieren,
und vor allem wird **jeder Befehl zweimal ausgeführt**. Mate erkennt das jetzt und sagt es; die
BetaTester-Version zieht von selbst um, die normale bleibt immer stehen.

## 9. Demo-Modus

Der **Demo-Modus** dient dazu, Mate ohne Auto und ohne Konto auszuprobieren: Er startet mit **einem Monat
fingierter, aber realistischer Daten**. Sie können ihn auf zwei Arten aktivieren:

- über den Assistenten beim ersten Start, Schaltfläche **🧪 Demo ausprobieren**;
- oder indem Sie den Container mit der Variablen `MATE_DEMO=1` starten.

In der Demo: Die Daten sind ausdrücklich fingiert (Abzeichen **DEMO**), die Befehle sind **simuliert** (es wird
kein Auto kontaktiert) und ein Banner oben bleibt immer sichtbar mit der Schaltfläche zum **Verlassen**. Beim
Verlassen kehrt Mate zur normalen Konfiguration zurück.

---

## 10. Häufige Fragen und Fehlerbehebung

**Das Auto geht oft „offline" / ich sehe ständig „Token ungültig".**
Fast immer liegt es daran, dass **dasselbe Leapmotor-Konto anderswo verwendet wird** (offizielle App, eine andere
Integration, eine zweite Mate-Instanz). Verwenden Sie ein **nur für Mate bestimmtes Konto** und **ändern Sie
dessen Passwort**, indem Sie es nur hier benutzen (so wird der andere Client hinausgeworfen und kehrt nicht
zurück). Siehe [Voraussetzungen](#2-bevor-sie-beginnen-die-voraussetzungen).

**Ein Befehl meldet „Timeout" / bernsteinfarbener Hinweis.**
Das ist (in der Regel) kein Problem von Mate. Die Befehle erfolgen in *Echtzeit* und hängen von der
**Erreichbarkeit des Autos** ab (Empfang, Standby). Mate versucht es erneut, und oft geht der Befehl trotzdem
durch. Der Indikator **„Fahrzeug-Reaktion"** in der Übersicht gibt Ihnen einen Eindruck von der Lage.

**Nach einer Offline-Phase fehlen Fahrten oder Kilometer.**
Wenn das Auto unerreichbar war, können einige Daten nicht erfasst worden sein. Die Ladevorgänge, die „im Schlaf"
erfolgten, werden in der Regel aus dem Sprung des Ladestands **rekonstruiert**; die verlorenen Kilometer lassen
sich nicht immer wiederherstellen. Die **Suche nach verpassten Ladevorgängen** (Einstellungen → Diagnose) hilft, nicht
erfasste Ladevorgänge wiederzufinden.

**Ich sehe einen seltsamen Ladevorgang / absurde Kosten.**
Mate hat Schutzmechanismen gegen unmögliche Werte (z. B. Wallbox-Zähler, die den Gesamtwert seit Inbetriebnahme
melden). Auch der umgekehrte Fall ist abgedeckt: Bleibt der Wallbox-Zähler mitten im Ladevorgang **stehen**,
während das Auto weiter Strom zieht, vertraut Mate seinem Gesamtwert für diesen Ladevorgang nicht mehr und
rechnet über die in der Batterie angekommene Energie ab — der Zählerwert wäre um alles zu niedrig, was er im
Stillstand versäumt hat.
Wenn ein öffentlicher Ladevorgang einen komplizierten Tarif hat, verwenden Sie den Typ **✎ Manuell** und
tragen Sie den gezahlten Gesamtbetrag ein.

**Das Diagramm des Ruhestromverlusts (Vampire Drain) ist leer.**
Es braucht in den letzten Tagen mindestens eine **lange Parkphase** mit einem messbaren Ladungsrückgang. Wenn das
Auto immer am Laden ist oder im geparkten Zustand schläft, kann es an Material fehlen. Mate erfasst auch den
Rückgang, der sich erst beim Aufwachen „offenbart".
Eine weitere häufige Ursache ist die **Schwelle des Ruhestromverlusts** unter *Einstellungen → Erweitert*: Wenn
Sie sie über die realen Rückgänge Ihres Autos angehoben haben, zeichnet das Diagramm nichts. Setzen Sie sie wieder
auf etwa **0,2** (oder drücken Sie **Reset**) und die Fenster erscheinen wieder. Seit **v1.22.4** sagt die Seite es
Ihnen ausdrücklich — sie zeigt trotzdem den typischen Wert und einen Hinweis „unter Ihrer Schwelle", statt leer zu
wirken.

**Ich habe eine Leapmotor REEV (Hybrid mit Range-Extender).**
Sie wird nicht unterstützt: Die Energieberechnungen würden die Kapazität der BEV-Batterie verwenden und wären
verfälscht. Mate ist **nur für die 100 % elektrischen Versionen**.

**Ich bin nicht in Europa.**
Derzeit funktioniert Mate nur mit der **europäischen** Leapmotor-Cloud. Konten auf Servern anderer Regionen können
sich nicht anmelden.

**Wie mache ich ein Backup?**
Unter *Einstellungen → Export/Backup* laden Sie die Datenbank (und die CSVs) herunter. Bewahren Sie die DB
**zusammen mit ihrer `secret.key`** auf.

---

## 11. Glossar

- **SoC** (*State of Charge*) — Ladestand der Batterie in Prozent.
- **SoH** (*State of Health*) — Gesundheitszustand der Batterie: verbleibende Kapazität gegenüber dem Neuzustand.
- **AC / DC** — Wechselstrom (langsames Laden, zu Hause/an AC-Säulen) / Gleichstrom (Schnell- und
  Ultraschnellladen).
- **Zuhause / AC / Schnell (FAST) / HPC / Manuell** — die Ladetypen, die Mate erkennt oder die Sie zuweisen können;
  „HPC" ist das Laden mit sehr hoher Leistung.
- **TOU** (*Time-of-Use*) — Tarif mit **Zeitfenstern** (unterschiedliche Preise je Tag/Stunde).
- **Regen** (Rekuperation) — Energie, die beim Bremsen/Vom-Gas-Gehen **zurückgewonnen** und wieder in die Batterie
  gespeist wird.
- **Vampire Drain** — was das Auto im **komplett ausgeschalteten** Zustand verbraucht, gemessen vom
  Ausschalten bis zum nächsten Einschalten. **Enthält Heizen/Kühlen bei ausgeschaltetem Auto** (so
  gewollt: Auto aus → zählt als Verlust). Leerlauf bei *eingeschaltetem* Auto (geparkt, Motor/Klima an)
  zählt hier nicht.
- **Polling** — das regelmäßige Auslesen des Fahrzeugzustands aus der Cloud (entlädt das Auto nicht).
- **Wallbox** — Ihre heimische Ladestation.
- **Poller / Web** — die beiden internen Komponenten von Mate: der *Poller* sammelt die Daten, das *Web* zeigt die
  Oberfläche. Für Sie als Benutzer ist das ein Detail: Sie arbeiten zusammen.
- **VIN** — die Fahrgestellnummer des Autos; sie identifiziert Ihr Fahrzeug eindeutig.
- **Bedien-PIN** — der vierstellige PIN des Kontos, nötig, um die Fernbefehle zu autorisieren.

---

> 📌 **Hinweis zur Pflege des Handbuchs.** Dieses Dokument beschreibt die Version **v1.28.0**. Wenn sich etwas für
> den Benutzer Sichtbares ändert (eine neue Seite, eine Option, ein Ablauf), aktualisieren Sie den entsprechenden
> Abschnitt und die Versionszeile oben. Es ist als Grundlage für die Übersetzungen (EN/FR/DE) gedacht: Die Struktur
> ist bewusst dieselbe wie die der Oberfläche.
