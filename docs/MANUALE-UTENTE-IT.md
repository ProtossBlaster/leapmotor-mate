# LeapMotor Mate — Manuale utente

> **Versione di Mate:** v1.22.5 · **Lingua:** Italiano (prima edizione)
> Questo manuale è pensato per chi *usa* Mate, non per chi lo sviluppa. Spiega come configurarlo
> dall'inizio e cosa fa ogni pagina. Per i dettagli tecnici interni c'è `ARCHITECTURE.md`.

---

## Indice

1. [Cos'è Mate (e cosa non è)](#1-cosè-mate-e-cosa-non-è)
2. [Prima di iniziare: i requisiti](#2-prima-di-iniziare-i-requisiti)
3. [Installazione](#3-installazione)
4. [Primo avvio: la configurazione guidata](#4-primo-avvio-la-configurazione-guidata)
5. [Conoscere l'interfaccia](#5-conoscere-linterfaccia)
6. [Le pagine, una per una](#6-le-pagine-una-per-una)
   - [Panoramica](#panoramica) · [Viaggi](#viaggi) · [Mappa](#mappa) · [Ricariche](#ricariche)
   - [Prezzi di ricarica](#prezzi-di-ricarica) · [Statistiche](#statistiche) · [Report mensile](#report-mensile)
   - [Salute batteria](#salute-batteria) · [Manutenzione](#manutenzione) · [Comandi](#comandi)
   - [Schedulazione](#schedulazione) · [Preparazione veicolo](#preparazione-veicolo)
   - [Navigazione](#navigazione) · [Veicolo](#veicolo) · [Wallbox](#wallbox)
7. [Impostazioni](#7-impostazioni)
8. [Le integrazioni in dettaglio (Wallbox, ABRP, MQTT)](#8-le-integrazioni-in-dettaglio)
9. [Modalità demo](#9-modalità-demo)
10. [Domande frequenti e risoluzione problemi](#10-domande-frequenti-e-risoluzione-problemi)
11. [Glossario](#11-glossario)

---

## 1. Cos'è Mate (e cosa non è)

**LeapMotor Mate** è un'applicazione che installi tu (self-hosted) e che fa da "compagno" per la tua
auto elettrica Leapmotor. Si collega al **cloud Leapmotor** (lo stesso a cui parla l'app ufficiale),
legge lo stato dell'auto e, a partire da quei dati, ricostruisce in autonomia:

- i tuoi **viaggi** (distanza, durata, consumo, recupero in frenata);
- le tue **ricariche** (energia, potenza, tipo, costo);
- i **costi** e l'**efficienza** nel tempo;
- la **salute della batteria** e le **scadenze di manutenzione**.

In più ti permette di **inviare comandi a distanza** (chiusura, clima, preparazione veicolo,
programmazioni…) e, se vuoi, di integrare i dati con **Home Assistant** (via MQTT), con
**A Better Routeplanner (ABRP)** e con la tua **wallbox**.

**Cosa NON fa / limiti importanti:**

- **Non parla direttamente con l'auto.** Tutto passa dal cloud Leapmotor. Quando Mate "interroga"
  il cloud (polling) legge l'**ultimo stato noto**: *non* sveglia l'auto e *non* scarica la
  batteria. È un'operazione sicura ed economica.
- **Solo auto 100% elettriche (BEV).** Sono supportate **T03, B05, B10, C10** nelle versioni
  elettriche. Le versioni **REEV** (con range extender a benzina) **non** sono supportate: i calcoli
  di energia/consumo/costo userebbero la capacità della batteria sbagliata e risulterebbero falsati.
- **Solo cloud europeo (Leapmotor International / Stellantis).** Account registrati su server di
  altre regioni (es. Cina) non riescono ad accedere. Fuori Europa, al momento, non è utilizzabile.
- **Non è uno strumento di contabilità.** Stima il costo *a partire dalla telemetria*; non tiene
  traccia di metodi di pagamento, fatture o abbonamenti delle colonnine.

---

## 2. Prima di iniziare: i requisiti

Per configurare Mate ti servono tre cose:

1. **Un account Leapmotor dedicato a Mate.** ⚠️ **Importantissimo.** Crea (o destina) un account
   Leapmotor che usi **solo** Mate. Leapmotor consente poche sessioni contemporanee per account: se
   lo stesso account è loggato anche nell'app ufficiale, in un'altra integrazione o in una seconda
   istanza di Mate, i client si "sfrattano" la sessione a vicenda. Il risultato è una raffica di
   *"Token non valido"* / ripetuti re-login, l'auto che va **offline** e **dati persi** (viaggi e
   ricariche non registrati). È la causa numero uno dei problemi segnalati. *Soluzione:* un account
   secondario con una **password usata solo in Mate**.

2. **Il certificato dell'app Leapmotor** (`app.crt` + `app.key`). È un certificato **uguale per
   tutti** (è quello dell'app, non del tuo account), necessario per dialogare col cloud. Si scarica
   da un repository pubblico — il wizard ti dà il link diretto
   ([github.com/markoceri/leapmotor-certs](https://github.com/markoceri/leapmotor-certs)).

3. **Email, password e PIN operativo dell'account.** Il **PIN a 4 cifre** è quello che usi anche
   nell'app ufficiale per autorizzare i comandi a distanza (chiusura, clima…).

> 💡 Vuoi solo dare un'occhiata senza configurare niente? Salta tutto e usa la **[modalità demo](#9-modalità-demo)**:
> Mate parte con un mese di dati finti realistici, senza auto e senza account.

---

## 3. Installazione

Mate gira allo stesso modo in due ambienti (l'interfaccia è identica):

- **Come add-on di Home Assistant** — il modo più semplice se hai già Home Assistant. Si aggiunge
  il repository dell'add-on, si installa "LeapMotor Mate" e si apre dalla barra laterale di HA
  (ingress). In questo caso Mate può anche leggere la tua **wallbox** direttamente da Home Assistant.
- **Come container Docker autonomo** (per esempio su un NAS) — tramite `docker-compose`. In questo
  caso l'app è raggiungibile dal browser sulla **porta 4000** (`http://INDIRIZZO-DEL-SERVER:4000`).

Le istruzioni passo-passo di installazione (repository, compose, ecc.) sono nel **README** del
progetto e nella pagina **Docker Hub**. Una volta avviato, il *primo accesso* è uguale per entrambi
ed è descritto qui sotto.

> 🔒 **Backup.** Tutti i dati di Mate stanno in una cartella persistente (`/data`): il database, la
> chiave di cifratura dei segreti (`secret.key`) e il certificato. Se fai un backup, **salva il
> database insieme alla sua `secret.key`** — senza la chiave, password e token salvati non sono più
> leggibili. Dalla pagina Impostazioni puoi scaricare un backup del database in qualsiasi momento.

---

## 4. Primo avvio: la configurazione guidata

Al primo accesso Mate mostra un **wizard** (procedura guidata). In alto puoi scegliere la lingua
(🇮🇹 Italiano). Poi:

### Passo 0 — Scegli come iniziare

Due pulsanti:

- **▶ Configura la mia auto** — la configurazione vera e propria (continua sotto).
- **🧪 Prova la demo** — entra in modalità dimostrativa con dati finti. Puoi uscire quando vuoi.

### Passo 1 — Certificato app

Mate ti chiede il certificato TLS dell'app Leapmotor. Hai due modi:

- **Carica i file** `app.crt` e `app.key` (modalità predefinita), oppure
- **Incolla il testo PEM** dei due file (pulsante *"Incolla il testo PEM invece"*).

Scaricali dal link mostrato, caricali e premi **Salva certificato**. Questo passo compare solo se il
certificato non è già presente nell'immagine.

### Passo 2 — Accesso all'account

Inserisci:

- **Email account Leapmotor**
- **Password**
- **PIN operativo** (4 cifre)

> ⚠️ Qui Mate ti ricorda di usare **un account dedicato solo a Mate** (vedi
> [requisiti](#2-prima-di-iniziare-i-requisiti)).

Premi **🔍 Rileva la mia auto**. Mate verifica le credenziali e legge dal cloud **modello e numero
di telaio (VIN)**. Se tutto va bene vedi una scheda "Auto rilevata" con `Leapmotor <modello> · VIN
···xxxxxx`.

### Passo 3 — Batteria

In base al modello:

- se la versione europea ha **una sola variante** di batteria, Mate la rileva da solo (es. T03 →
  37,3 kWh);
- se ci sono **più varianti** (es. B10 Pro 56,2 kWh / Pro Max 67,1 kWh; C10 RWD 69,9 / AWD 81,9),
  scegli la tua;
- se il rilevamento non riesce, puoi **inserire la capacità a mano** (in kWh).

> La capacità indicata è quella **utile/netta** (quella che conta davvero per consumi e costi) e si
> può sempre correggere dopo, da Impostazioni → Batteria.

### Passo 4 — Connetti

Premi **Connetti e avvia**. Mate salva la configurazione, si collega e ti porta alla **Panoramica**.
Da questo momento il "poller" inizia a raccogliere dati in sottofondo: i primi viaggi e ricariche
appariranno man mano che guidi e ricarichi.

---

## 5. Conoscere l'interfaccia

L'interfaccia è composta da:

- **Menu laterale (sidebar)** — l'elenco delle pagine (vedi sotto). Su schermo piccolo si apre con
  l'icona ☰.
- **Intestazione (header)** — titolo della pagina, eventuale **avviso di aggiornamento** disponibile
  (↑ vX.Y.Z) e il pulsante **🔄 Aggiorna ora**.
- **Pulsante Aggiorna ora** — forza una lettura immediata dallo stato dell'auto, senza aspettare il
  ciclo automatico. Utile dopo aver dato un comando.

In fondo al menu trovi **⚙️ Impostazioni** e **🚪 Esci** (logout).

Molte pagine si **aggiornano da sole** ogni 30 secondi circa, quindi i valori "vivi" (stato,
ricarica in corso…) restano freschi senza ricaricare la pagina.

**Lingua, valuta e unità** si cambiano da *Impostazioni → 🌍 Lingua e valuta*:

- **Lingua:** Italiano, English, Français, Deutsch.
- **Valuta:** per i costi (€, £, …).
- **Unità:** metriche (km, °C) o imperiali UK/US (miglia, °F). I dati restano sempre salvati in
  km/°C; cambia solo come vengono **mostrati**.

---

## 6. Le pagine, una per una

L'ordine qui sotto è lo stesso del menu laterale.

### Panoramica
**(menu: Panoramica)** — La home. In alto c'è una **scheda principale** con l'immagine dell'auto e
lo stato dal vivo:

- **percentuale di carica (SoC)** e autonomia stimata;
- **icone di stato** che cambiano colore: chiusura (verde = chiusa, ambra = aperta), bagagliaio
  (rosso se aperto), finestrini (viola se aperti), clima, ecc.;
- **comandi rapidi** (chiudi/apri, trova auto…), già "consapevoli" dello stato attuale;
- quando l'auto è **in ricarica**, un'**animazione** mostra il flusso di energia e una targhetta con
  la stima del tempo "fino a X%" (X = il limite di carica che hai impostato in auto);
- una targhetta **"Cavo collegato / Carica completa"** quando il cavo è inserito ma non si sta
  caricando attivamente.

Più in basso trovi mini-statistiche e un **indicatore di "reattività auto"** (un pallino
🟢/🟡/🔴, ⚪ se non ci sono dati): riassume quanto l'auto ha risposto agli ultimi comandi inviati.

### Viaggi
**(menu: Viaggi)** — L'elenco dei tuoi spostamenti, uno per guidata. Per ogni viaggio vedi
**distanza, durata, consumo (kWh/100 km), energia recuperata** in frenata e il **costo** stimato.

- Cliccando un viaggio apri il **dettaglio**, con il **tracciato GPS** su mappa e i dati di quel
  singolo viaggio.
- Puoi **unire** due viaggi spezzati per errore (Fusione 🔗) o **separarli** di nuovo, e
  **cancellare** un viaggio.
- Soste brevi (semafori, code) **non** spezzano un viaggio: una guidata resta una sola riga.

### Mappa
**(menu: Mappa)** — La posizione dell'auto su mappa. Mostra l'ultima posizione nota; se l'ultimo
dato dal cloud non ha un GPS valido, Mate **mantiene l'ultima posizione valida** invece di far
sparire la mappa.

### Ricariche
**(menu: Ricariche)** — L'elenco delle ricariche. Per ognuna: **energia aggiunta (kWh)**, **potenza
massima**, **tipo** e **costo**, con il **€/kWh effettivo** ben in vista. Il tipo è classificato con
un'etichetta:

- **Casa** (la tua wallbox), **AC** (corrente alternata pubblica), **Veloce/FAST** (DC),
  **HPC** (ricarica ultraveloce) e **✎ Manuale**.
- **✎ Manuale**: per le colonnine pubbliche con tariffe complicate (abbonamenti, costi di sessione…)
  puoi **scrivere a mano il totale realmente pagato**; questo valore scavalca la stima automatica.
- Anche le ricariche avvenute mentre l'auto era spenta/offline vengono **ricostruite** dal salto di
  percentuale di carica.

### Prezzi di ricarica
**(menu: Prezzi di ricarica)** — Qui imposti **quanto paghi l'energia**, così Mate può calcolare i
costi. Puoi definire un prezzo **per ciascun tipo** di ricarica (Casa, AC, Veloce, HPC) e scegliere
tra:

- **Tariffa fissa** (un solo €/kWh), oppure
- **Fasce orarie (TOU)** — prezzi diversi per giorno della settimana e fascia oraria (es. F1/F2/F3,
  notte più economica).

Il prezzo di **Casa** è quello che alimenta i costi delle ricariche domestiche e, a cascata, il
costo dei viaggi (calcolato sul prezzo "medio" dell'energia in batteria al momento del viaggio).

> Le modifiche ai prezzi valgono **solo per le ricariche future**: i costi già calcolati non
> cambiano. Con le fasce orarie puoi anche scegliere *come* ripartire una sessione tra le fasce —
> *Split accurato* (sulla curva di potenza reale) oppure *Ora di inizio* (tutta la sessione alla
> fascia in cui è partita).

### Statistiche
**(menu: Statistiche)** — Le tue medie e i totali nel tempo: **distanza totale** e numero di viaggi,
**distanza media per viaggio**, **tempo di guida**, **consumo medio** (pesato sulla distanza) e
**migliore**, **energia usata e ricaricata**, **recupero** totale e medio, numero di **sessioni di
ricarica**, con le relative **tendenze** (efficienza e recupero nel tempo).

### Report mensile
**(menu: Report mensile)** — Una sintesi **mese per mese**: quanto hai guidato, quanta energia hai
usato e ricaricato, quanto hai speso. Comodo per tenere d'occhio l'andamento.

### Salute batteria
**(menu: Salute batteria)** — Una **stima dello stato di salute (SoH)** della batteria, cioè quanta
capacità "vera" è rimasta rispetto al nuovo. Mate la calcola dai dati reali di ricarica (energia
realmente entrata rispetto alla percentuale guadagnata), **escludendo** le ricariche a freddo che
falserebbero la misura, e la mostra nel tempo e/o per chilometraggio. È una **stima**, non una
diagnosi ufficiale, ma migliora con l'accumularsi dei dati.

### Manutenzione
**(menu: Manutenzione)** — Le **scadenze di manutenzione** della tua auto, basate sul **programma
ufficiale del tuo modello** (T03, B05, B10, C10). Per ogni intervento (es. tagliando, liquido freni,
filtro abitacolo, pneumatici…) vedi due barre di avvicinamento: una per i **chilometri** e una per
il **tempo**, perché scade ciò che arriva prima.

- Puoi **registrare un intervento** ("fatto oggi a X km") direttamente dalla pagina: la scadenza
  successiva si ricalcola.
- Per un'**auto nuova** che non ha ancora storico, puoi impostare una **data/chilometraggio di
  riferimento** così le scadenze partono dalla consegna ("primo tagliando tra…") invece di risultare
  "mai eseguito".
- Le distanze rispettano l'unità scelta (km o miglia).

### Comandi
**(menu: Comandi)** — I **comandi a distanza**. Da qui puoi:

- **chiudere/aprire**, aprire il **bagagliaio**, **trovare l'auto** (clacson/luci);
- gestire il **clima**: raffrescamento, riscaldamento, sbrinamento, ventilazione, **spegnimento**;
- attivare **riscaldamento sedili**, **volante** e **specchietti** (dove supportato);
- gestire il **limite di carica**.

Quando dai un comando, Mate aggiorna subito l'interfaccia in modo "ottimistico" e poi conferma alla
lettura successiva. Se il cloud accetta ma l'auto non conferma entro pochi secondi, vedi un avviso
**ambra** ("inviato, può aver funzionato") — non è un errore: spesso il comando va comunque a buon
fine (dipende da copertura/standby dell'auto).

### Schedulazione
**(menu: Schedulazione)** — Le **programmazioni** dell'auto:

- **Ricarica programmata** (e il **limite di carica**);
- **Clima programmato** — 5 preset (raffresca / riscalda / ventila / sbrina / auto) con orario di
  avvio futuro; puoi crearli, modificarli e annullarli.

### Preparazione veicolo
**(menu: Preparazione veicolo)** — La funzione "**prepara l'auto con un tocco**": porta l'abitacolo
alla temperatura desiderata (e funzioni collegate) **subito** oppure a un **orario programmato**.
Puoi anche spegnere tutto.

### Navigazione
**(menu: Navigazione)** — *Invia una destinazione al navigatore dell'auto* e **trova le colonnine
nelle vicinanze**. La pagina ha tre parti:

- **Destinazione** — scrivi un **indirizzo** (e, se serve, la **città**), premi **Cerca**: la meta
  appare sulla mappa e con **🧭 Invia all'auto** la mandi al navigatore di bordo. *La ricerca per
  indirizzo richiede una chiave di geocoding* (vedi [Impostazioni → Geocoder](#7-impostazioni)).
- **⚡ Colonnine di ricarica — "Trova colonnine"** — cerca le **colonnine pubbliche intorno
  all'auto** (usa la sua posizione GPS attuale). Puoi impostare:
  - **Distanza massima** — 500 m, 1, 2, **5 km** (predefinito) o 10 km;
  - **Risultati per pagina** — 25, 50 o 100;
  - **Rete / operatore** (facoltativo) — per filtrare un gestore specifico (es. Electra, Ionity,
    Enel X Way, Be Charge, Plenitude, A2A, Atlante, Ewiva, Tesla…).

  I risultati compaiono sia come **segnalini ⚡ sulla mappa** sia in un **elenco** sotto, con
  **nome, distanza** e, dove disponibile, la **disponibilità in tempo reale** (🟢/🔴 "disponibili
  ora", p.es. sulla rete pubblica italiana). Tocca una colonnina nell'elenco per **vederla sulla
  mappa**, e con un clic puoi **usarla come destinazione** e poi inviarla all'auto. Se nel raggio
  scelto non c'è nulla, Mate allarga e mostra **le più vicine**.

  > La ricerca colonnine **non richiede chiavi** (usa mappe aperte + database colonnine pubbliche);
  > le chiavi facoltative in *Impostazioni → ⚡ Etichette colonnine* (OpenChargeMap, TomTom) la
  > arricchiscono. Serve però che l'auto abbia una **posizione GPS** nota.
- **Posizione attuale dell'auto** — l'indirizzo dell'auto e una mappa con il suo segnalino 🚗.

### Veicolo
**(menu: Veicolo)** — La scheda **stato completo** dell'auto: tutti i sensori disponibili sul tuo
modello (carica, autonomia, temperatura interna, marcia, porte, finestrini, pneumatici, blocchi,
stato di ricarica…). Mate mostra **solo ciò che la tua auto riporta davvero** (alcuni modelli non
espongono certi dati).

### Wallbox
**(menu: Wallbox)** — Se hai collegato una wallbox (vedi
[Integrazioni](#8-le-integrazioni-in-dettaglio)), qui vedi i suoi dati **dal vivo** (potenza,
energia), il **riepilogo** e l'elenco delle **sessioni**, ed eventualmente i **controlli** (es.
corrente massima) se la tua wallbox li espone tramite Home Assistant.

---

## 7. Impostazioni

**(menu: ⚙️ Impostazioni)** — La pagina è organizzata in **schede a fisarmonica**: ne apri una alla
volta. È divisa in tre colonne.

**Colonna 1 — Veicolo e guida**

- **🌍 Lingua e valuta** — lingua dell'interfaccia, valuta dei costi, **unità** (metriche/imperiali).
- **Veicolo** — modello e VIN della tua auto. Qui c'è anche il pulsante **🔓 Esci dall'account**
  (logout) per collegare un account diverso: cancella *solo* le credenziali salvate, **non** i tuoi
  viaggi/ricariche né il certificato.
- **Batteria** — la **capacità** in kWh usata per tutti i calcoli; correggibile. Se Mate ha una
  stima "misurata" dai tuoi dati, te la propone.
- **Cadenza di polling** — ogni quanto Mate legge lo stato dal cloud, con due cursori: **da fermo**
  (10 s–5 min, predefinito 30 s) e **in marcia** (10–60 s, predefinito 10 s). Leggere più spesso non
  scarica l'auto, ma genera più traffico verso il cloud.
- **Rilevamento ricarica** — la **soglia di corrente** (in ampere) sopra la quale Mate considera
  "ricarica in corso". Da abbassare solo se hai ricariche molto lente non rilevate.

**Colonna 2 — Integrazioni**

- **ABRP** — invio telemetria ad A Better Routeplanner (vedi [§8](#8-le-integrazioni-in-dettaglio)).
- **Geocoder** — il servizio per tradurre indirizzi ↔ coordinate nella pagina Navigazione
  (Geoapify *consigliato*, LocationIQ, TomTom). Richiede una **chiave** gratuita del servizio scelto.
- **⚡ Etichette colonnine** — abilita i **nomi delle colonnine** sulle ricariche (📍) e accetta
  chiavi opzionali (OpenChargeMap, TomTom) per arricchire la ricerca. È **disattivato** di default.
- **Wallbox** — collega la tua wallbox per i **costi reali** e gli eventuali controlli (vedi
  [§8](#8-le-integrazioni-in-dettaglio)).
- **MQTT → Home Assistant** — pubblica i dati dell'auto come entità in Home Assistant (vedi
  [§8](#8-le-integrazioni-in-dettaglio)).

**Colonna 3 — Dati e manutenzione**

- **Database** — dimensione del DB e **conservazione posizioni** (retention): puoi tenere i punti GPS
  "per sempre" (predefinito) o cancellare quelli più vecchi di 6/12/18/24 mesi per risparmiare
  spazio. *Vengono potate solo le posizioni*: viaggi, ricariche e curve di ricarica restano.
- **Esporta / backup** — scarica **viaggi (CSV)**, **ricariche (CSV)** e un **backup del database**.
- **🩺 Diagnostica** — una fotografia del sistema (versione, modello, conteggi, ultimo poll,
  integrazioni attive), la possibilità di **vedere i log** (poller/web) e soprattutto di **scaricare
  un pacchetto diagnostico** spuntando le parti volute (info, log poller, log web, **segnali grezzi**).
  Il pacchetto è **già ripulito** dai dati sensibili: **GPS rimosso** e VIN/segreti oscurati, quindi
  è sicuro da allegare quando chiedi assistenza. C'è anche una **scansione delle ricariche perse**
  mentre l'auto dormiva.
- **⚙️ Avanzate** — parametri fini per utenti esperti: soglia minima per **ricostruire** una ricarica
  saltata, soglia del **consumo da fermo (vampire drain)**, soglia kW per distinguere **DC**, e
  temperatura minima per il calcolo della **salute batteria**. C'è un pulsante per **ripristinare i
  valori predefiniti**.

> 🆕 Quando arriva una funzione nuova, la sua scheda può mostrare un badge **NEW** finché non la apri
> la prima volta.

---

## 8. Le integrazioni in dettaglio

Tutte le integrazioni sono **opzionali** e **disattivate** di default. Si configurano da
**Impostazioni**.

### Wallbox (per i costi reali di ricarica)
Collegando la tua wallbox, Mate usa l'**energia realmente erogata** (lato corrente alternata) per
calcolare il costo delle ricariche di casa, invece di stimarla dalla variazione di percentuale.

Mate legge la wallbox **attraverso Home Assistant**:

1. In *Impostazioni → Wallbox*, attiva **Abilita wallbox**.
2. **Se usi l'add-on di Home Assistant**, Mate può raggiungere HA da solo: non serve inserire
   indirizzo o token.
3. **Se usi Mate come Docker autonomo**, inserisci l'**URL di Home Assistant**
   (es. `http://192.168.1.10:8123`) e un **token di accesso a lungo termine** di HA, poi premi
   **Prova/Test**.
4. Con le **parole chiave** puoi aiutare Mate a riconoscere le entità giuste della tua wallbox
   (es. `wallbox, charger, evse, keba, pulsar`). Alcune wallbox note (es. V2C Trydan) vengono
   riconosciute in automatico; le entità "trappola" (solare/casa) sono escluse.
5. Apri l'elenco entità per verificare che Mate abbia agganciato i sensori di **energia/potenza**
   giusti.
6. Opzione **"casa automatica"**: assegna in automatico l'etichetta **Casa** alle ricariche fatte
   sulla tua wallbox.

### ABRP (A Better Routeplanner)
Invia la telemetria dell'auto ad ABRP per la pianificazione viaggi in tempo reale.

1. In *Impostazioni → ABRP*, attiva **Abilita ABRP**.
2. Incolla il tuo **token** ABRP (lo trovi nelle impostazioni "generic"/telemetria del tuo account
   ABRP).
3. Salva. Lo stato dell'integrazione compare nell'intestazione della scheda.

#### ABRP — Precondizionamento batteria prima di una ricarica DC rapida

Quando viaggi con un **piano ABRP attivo** e MQTT è configurato, Mate può inviare
automaticamente il comando di **preriscaldamento batteria** prima di una sosta a un fast charger.
Questo dà al BMS il tempo di portare la batteria alla temperatura ottimale, migliorando la
potenza di picco nelle giornate fredde.

**Come funziona:**

1. Ogni 60 s durante la guida, Mate interroga ABRP per il prossimo waypoint del piano attivo.
2. Se la prossima sosta è un **DC fast charger** a meno della distanza configurata *e* la
   temperatura della batteria è sotto la soglia impostata, Mate invia il comando
   `battery_preheat` all'auto.
3. Un debounce di 10 minuti evita comandi ripetuti sullo stesso stop.
4. Dopo l'invio, Mate monitora il segnale BMS 1186 (`batteryThermalRequest`) per 3 minuti:

| Risposta BMS | Evento pubblicato | Significato |
|---|---|---|
| Segnale = 2 (riscaldamento) | `battery_preheat_confirmed` | BMS ha avviato il riscaldamento attivo |
| Segnale = 1 (raffreddamento) | `battery_preheat_not_needed` (`reason: bms_cooling`) | Batteria già calda, BMS sta raffreddando |
| Nessun cambio dopo 3 min | `battery_preheat_not_needed` (`reason: no_bms_response`) | BMS ha deciso che non serve riscaldamento |

> **Nota**: Il comando `battery_preheat` corrisponde al toggle "Preriscaldamento batteria" nel
> Centro di ricarica dell'app Leapmotor. Il BMS usa il proprio sensore interno di temperatura e
> può decidere di non riscaldare anche se il comando viene inviato. Gli eventi qui sopra ti
> comunicano esattamente cosa è successo.

**Configurazione:**

1. In *Impostazioni → ABRP*, assicurati che ABRP sia abilitato con un token valido.
2. Attiva **Precondizionamento batteria** e configura:
   - **Distanza (km)**: attiva quando il prossimo DC charger è a meno di questa distanza (default 20 km).
   - **Temp. max batteria (°C)**: attiva solo se la temperatura batteria è sotto questo valore (default 20 °C).
3. MQTT deve essere configurato e connesso — gli eventi arrivano su `<prefisso>/<vin>/event`.

**Payload degli eventi MQTT:**

```json
// battery_preheat — comando inviato
{"type": "battery_preheat", "batt_temp_c": 3.5, "dist_km": 14.2, "charger": "Ionity A4 km 42"}

// battery_preheat_confirmed — BMS riscaldamento attivo
{"type": "battery_preheat_confirmed", "batt_temp_c": 5.1}

// battery_preheat_not_needed — BMS ha rifiutato
{"type": "battery_preheat_not_needed", "batt_temp_c": 18.3, "reason": "no_bms_response"}
```

**Esempio di automazione Home Assistant:**

```yaml
alias: "Leapmotor – Notifica precondizionamento batteria"
description: "Avvisa quando Mate avvia (o conferma) il preriscaldamento prima di un fast charger."
mode: single
trigger:
  - platform: mqtt
    topic: leapmotor/+/event          # sostituisci 'leapmotor' se hai cambiato il prefisso
condition:
  - condition: template
    value_template: >
      {{ trigger.payload_json.type in [
           'battery_preheat',
           'battery_preheat_confirmed',
           'battery_preheat_not_needed'
         ] }}
action:
  - choose:
      - conditions:
          - condition: template
            value_template: "{{ trigger.payload_json.type == 'battery_preheat' }}"
        sequence:
          - service: notify.mobile_app_IL_TUO_TELEFONO   # ← il tuo servizio notify di HA
            data:
              title: "Leapmotor B10 – Preriscaldamento batteria"
              message: >
                Batteria a {{ trigger.payload_json.batt_temp_c }}°C —
                comando inviato.
                {{ trigger.payload_json.dist_km }} km da
                {{ trigger.payload_json.charger }}.

      - conditions:
          - condition: template
            value_template: "{{ trigger.payload_json.type == 'battery_preheat_confirmed' }}"
        sequence:
          - service: notify.mobile_app_IL_TUO_TELEFONO
            data:
              title: "Leapmotor B10 – Riscaldamento confermato ✓"
              message: >
                Il BMS sta riscaldando attivamente la batteria
                ({{ trigger.payload_json.batt_temp_c }}°C).
                Pronto per la ricarica rapida.

      - conditions:
          - condition: template
            value_template: "{{ trigger.payload_json.type == 'battery_preheat_not_needed' }}"
        sequence:
          - service: notify.mobile_app_IL_TUO_TELEFONO
            data:
              title: "Leapmotor B10 – Batteria OK, nessun riscaldamento"
              message: >
                {% if trigger.payload_json.reason == 'bms_cooling' %}
                Il BMS sta già raffreddando — la batteria è abbastanza calda.
                {% else %}
                Il BMS non ha avviato il riscaldamento — la temperatura è probabilmente adeguata.
                {% endif %}
                ({{ trigger.payload_json.batt_temp_c }}°C)
```

Sostituisci `notify.mobile_app_IL_TUO_TELEFONO` con il servizio di notifica reale di HA
(es. `notify.mobile_app_iphone_di_marco`). Lo trovi in *Strumenti sviluppatore → Servizi* in
Home Assistant.

### MQTT → Home Assistant
Pubblica lo stato dell'auto (carica, autonomia, posizione, porte, stato ricarica…) come **entità in
Home Assistant**, con **auto-discovery**. Puoi anche **comandare** l'auto dalle entità di HA.

1. Prepara un **broker MQTT** (di solito l'add-on *Mosquitto* in Home Assistant).
2. In *Impostazioni → MQTT*, attiva **Abilita MQTT** e compila:
   - **Broker** (es. `192.168.1.10` o `core-mosquitto`) e **Porta** (default `1883`);
   - **Utente** e **Password** del broker;
   - **Prefisso** dei topic (default `leapmotor`);
   - opzioni: **Discovery** (consigliata), **TLS** e **TLS non sicuro** se usi certificati
     self-signed.
3. Premi **Prova** per verificare la connessione, poi **Salva**. Entro pochi secondi le entità
   compaiono in Home Assistant.

> Per i comandi via MQTT, l'auto richiede comunque il PIN: Mate lo usa in automatico con le
> credenziali salvate.

---

## 9. Modalità demo

La **demo** serve a provare Mate senza auto e senza account: parte con **un mese di dati finti ma
realistici**. Puoi attivarla in due modi:

- dal wizard di primo avvio, pulsante **🧪 Prova la demo**;
- oppure avviando il container con la variabile `MATE_DEMO=1`.

In demo: i dati sono dichiaratamente fittizi (badge **DEMO**), i comandi sono **simulati** (non
viene contattata nessuna auto) e un banner in alto resta sempre visibile con il pulsante per
**uscire**. Uscendo, Mate torna alla configurazione normale.

---

## 10. Domande frequenti e risoluzione problemi

**L'auto va spesso "offline" / vedo "Token non valido" di continuo.**
Quasi sempre è perché lo **stesso account Leapmotor è usato altrove** (app ufficiale, un'altra
integrazione, una seconda istanza di Mate). Usa un **account dedicato solo a Mate** e **cambia la
sua password** usandola solo qui (così l'altro client viene buttato fuori e non rientra). Vedi
[requisiti](#2-prima-di-iniziare-i-requisiti).

**Un comando dà "timeout" / avviso ambra.**
Non è (di solito) un problema di Mate. I comandi sono in *tempo reale* e dipendono dalla
**raggiungibilità dell'auto** (copertura, standby). Mate riprova e spesso il comando va comunque a
segno. L'indicatore **"Reattività auto"** in Panoramica ti dà un'idea della situazione.

**Mancano dei viaggi o dei km dopo un periodo offline.**
Quando l'auto era irraggiungibile, alcuni dati possono non essere stati registrati. Le ricariche
avvenute "a sonno" vengono in genere **ricostruite** dal salto di carica; per i km persi non sempre
è possibile recuperarli. La **scansione ricariche perse** (Impostazioni → Diagnostica) aiuta a
ritrovare ricariche non registrate.

**Vedo una ricarica strana / costo assurdo.**
Mate ha protezioni contro i valori impossibili (es. contatori wallbox che riportano il totale a
vita). Se una ricarica pubblica ha una tariffa complicata, usa il tipo **✎ Manuale** e scrivi il
totale pagato.

**Il grafico del consumo da fermo (vampire drain) è vuoto.**
Serve almeno una **sosta lunga** con un calo di carica misurabile negli ultimi giorni. Se l'auto è
sempre in carica o dorme da ferma, può non esserci abbastanza materiale. Mate cattura anche il calo
che si "rivela" solo al risveglio.
Un'altra causa frequente è la **soglia del consumo da fermo** in *Impostazioni → Avanzate*: se l'hai
alzata sopra i cali reali della tua auto, il grafico non disegna nulla. Riportala verso **0,2** (o
premi **Reset**) e le finestre ricompaiono. Dalla **v1.22.4** la pagina te lo dice esplicitamente —
mostra comunque il valore tipico e un avviso "sotto la tua soglia" invece di sembrare vuota.

**Ho una Leapmotor REEV (ibrida con range extender).**
Non è supportata: i calcoli di energia userebbero la capacità della batteria BEV e risulterebbero
sballati. Mate è **solo per le versioni 100% elettriche**.

**Non sono in Europa.**
Al momento Mate funziona solo con il cloud Leapmotor **europeo**. Account su server di altre regioni
non riescono ad accedere.

**Come faccio il backup?**
Da *Impostazioni → Esporta/backup* scarichi il database (e i CSV). Conserva il DB **insieme alla sua
`secret.key`**.

---

## 11. Glossario

- **SoC** (*State of Charge*) — percentuale di carica della batteria.
- **SoH** (*State of Health*) — stato di salute della batteria: capacità residua rispetto al nuovo.
- **AC / DC** — corrente alternata (ricarica lenta, da casa/colonnine AC) / continua (ricarica
  veloce e ultraveloce).
- **Casa / AC / Veloce (FAST) / HPC / Manuale** — i tipi di ricarica che Mate riconosce o che puoi
  assegnare; "HPC" è la ricarica ad altissima potenza.
- **TOU** (*Time-of-Use*) — tariffa a **fasce orarie** (prezzi diversi per giorno/ora).
- **Regen** — energia **recuperata** in frenata/rilascio e rimessa in batteria.
- **Vampire drain** — il piccolo **consumo da fermo** dell'auto (sistemi in standby), misurato da
  Mate sulle soste lunghe.
- **Polling** — la lettura periodica dello stato dell'auto dal cloud (non scarica l'auto).
- **Wallbox** — la tua stazione di ricarica domestica.
- **Poller / Web** — i due componenti interni di Mate: il *poller* raccoglie i dati, il *web* mostra
  l'interfaccia. Per te utente è un dettaglio: lavorano insieme.
- **VIN** — il numero di telaio dell'auto; identifica univocamente la tua vettura.
- **PIN operativo** — il PIN a 4 cifre dell'account, necessario per autorizzare i comandi a distanza.

---

> 📌 **Nota di manutenzione del manuale.** Questo documento descrive la versione **v1.22.5**. Quando
> cambia qualcosa di visibile all'utente (una pagina nuova, un'opzione, un flusso), aggiorna la
> sezione corrispondente e la riga di versione in alto. È pensato come base per le traduzioni
> (EN/FR/DE): la struttura è volutamente la stessa dell'interfaccia.
