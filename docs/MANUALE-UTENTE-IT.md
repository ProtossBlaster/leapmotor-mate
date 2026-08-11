# LeapMotor Mate — Manuale utente

> **Versione di Mate:** v3.11.0 · **Lingua:** Italiano
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

Mate gira allo stesso modo in tre ambienti (l'interfaccia è identica):

- **Come add-on di Home Assistant** — il modo più semplice se hai già Home Assistant. Si aggiunge
  il repository dell'add-on, si installa "LeapMotor Mate" e si apre dalla barra laterale di HA
  (ingress). In questo caso Mate può anche leggere la tua **wallbox** direttamente da Home Assistant.
- **Come container Docker autonomo** (per esempio su un NAS) — tramite `docker-compose`. In questo
  caso l'app è raggiungibile dal browser sulla **porta 4000** (`http://INDIRIZZO-DEL-SERVER:4000`).
- **Come applicazione da scrivania** — [**MateDesktop**](https://github.com/ProtossBlaster/MateDesktop)
  è lo stesso Mate impacchettato per **macOS e Windows**, per chi non usa né Home Assistant né
  Docker: scarichi, apri, e trovi lo stesso wizard di configurazione. Su Windows si distribuisce
  **dentro uno `.zip`**: prima lo scompatti, poi lanci l'installatore — un `.exe` preso da internet
  non ha ancora una reputazione per SmartScreen e viene fermato all'ingresso.

Le istruzioni passo-passo di installazione (repository, compose, ecc.) sono nel **README** del
progetto e nella pagina **Docker Hub**. Una volta avviato, il *primo accesso* è uguale per entrambi
ed è descritto qui sotto.

> 📱 **Sul telefono.** Mate non è un'app da telefono e non può esserlo: deve interrogare il cloud
> per anni, e un telefono sospende quello che gira in secondo piano. Però lo puoi mettere **sulla
> schermata Home**: apri Mate nel browser del telefono, poi *Condividi → Aggiungi a Home* su iPhone,
> oppure *⋮ → Aggiungi a schermata Home* su Android. Prende l'icona di Mate e si apre a tutto
> schermo, senza la barra dell'indirizzo e senza quella degli strumenti — circa 110 px di schermo in
> più. Resta una scorciatoia al server che hai acceso tu: se quello è spento, non apre niente.

> 🔒 **Backup.** Tutti i dati di Mate stanno in una cartella persistente (`/data`): il database, la
> chiave di cifratura dei segreti (`secret.key`) e il certificato. Se fai un backup, **salva il
> database insieme alla sua `secret.key`** — senza la chiave, password e token salvati non sono più
> leggibili. Dalla pagina Impostazioni puoi scaricare un backup del database in qualsiasi momento.
> Se ripristini un database **senza** la sua chiave, ora Mate lo scrive nel log per nome — quali
> segreti non riesce a leggere e cosa fare — invece di fallire più tardi come errore di accesso.
> Viaggi, ricariche e costi non sono cifrati e tornano sempre.

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

- se la versione europea ha **una sola variante** di batteria, Mate la imposta da solo — oggi solo la
  T03 (36,0 kWh);
- se ci sono **più varianti** — B10 e B05 (Pro 55,0 kWh / Pro Max 65,0), C10 (RWD 69,9 / AWD 81,9) —
  **la scegli tu**: il cloud non dice quale hai a bordo, quindi Mate non può saperlo;
- se il rilevamento non riesce, puoi **inserire la capacità a mano** (in kWh).

> La capacità indicata è quella **utile/netta** (quella che conta davvero per consumi e costi) e si
> può sempre correggere dopo, da Impostazioni → Batteria.
> Accanto c'è il **Riferimento SoH**: la capacità da nuova su cui si misura la salute batteria.
> Mate la fotografa la prima volta che salvi la capacità e poi non la tocca più, così adottare un
> valore misurato (e già invecchiato) non può riportare la salute a ~100 % nascondendo
> l'invecchiamento. Se è stata registrata sbagliata, la salute può superare il 100 %: si corregge lì.

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

In fondo al menu trovi **⚙️ Impostazioni**, e **🚪 Esci** *solo se hai impostato una password
d'accesso* — quello chiude la sessione della password, niente altro. Senza password non c'è, perché
non c'è niente da chiudere.

**Per cambiare il PIN dell'auto 🆕** — se lo cambi sull'auto, non serve sganciare niente: vai su
**Impostazioni → Veicolo**, sotto l'indirizzo dell'account trovi **PIN operativo**. Si digita due
volte, con l'occhio per rileggerlo, e vale subito — sia per i comandi dalla pagina sia per quelli
che arrivano da Home Assistant. Domanda di **@alextchao** (#225).

**Se due Leapmotor condividono il tuo account 🆕** — in testata compare un **selettore auto**,
accanto al badge del modello. C'è solo dalla seconda auto in poi: con una Leapmotor non cambia
assolutamente nulla. Scegli un'auto e tutto la segue — Panoramica, Statistiche, viaggi, ricariche,
report mensile, i comandi che quell'auto permette e le sue entità Home Assistant. La scelta resta.

Le impostazioni restano condivise, perché sotto lo stesso tetto raramente cambiano: prezzi, valuta,
fuso orario, posizione di casa. Ciò che è dell'auto resta all'auto — la capacità della batteria, il
suo **PIN operativo**, il suo **token A Better Route Planner**, se è una range-extender, cosa le si può comandare e quali sensori ha davvero.
Le due auto sono seguite da **un solo Mate**: un poller, un database, una sessione verso il cloud
Leapmotor, invece di due installazioni che si sganciano a vicenda.

**Per sganciare l'account Leapmotor** — che è un'altra cosa — vai su **Impostazioni → Veicolo →
🔓 Esci dall'account**. Cancella le credenziali salvate e riapre la configurazione guidata; il
certificato, i viaggi e le ricariche restano.

Molte pagine si **aggiornano da sole** ogni 30 secondi circa, quindi i valori "vivi" (stato,
ricarica in corso…) restano freschi senza ricaricare la pagina.

**Lingua, valuta e unità** si cambiano da *Impostazioni → 🌍 Lingua e valuta*:

- **Lingua:** Italiano, English, Français, Deutsch, Polski, Nederlands, Português, Español.
  *(Un manuale scritto come questo esiste in italiano, inglese, francese, tedesco e spagnolo.)*
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
  caricando attivamente. Accanto, se hai impostato una **ricarica programmata**, compare la fascia
  oraria dell'auto (per esempio **"Carica 01:50 – 12:00"**): è la risposta a «il cavo è dentro,
  perché non carica?».

Quando l'auto alimenta un dispositivo esterno tramite l'adattatore **V2L** (vehicle-to-load), la
Panoramica mostra un **blocco V2L** con lo **stato** (Attivo / Non attivo), la **potenza istantanea**
in watt — riportata **al netto dell'overhead dell'auto (~300 W)**, così da corrispondere a ciò che il
dispositivo consuma davvero — con una barra 0–3500 W, e l'**energia prelevata nella sessione**; si
aggiorna circa ogni **10 s** mentre una sessione è in corso. È **di sola lettura**: il V2L si attiva
dall'auto (cambio in P + un dispositivo collegato), non da Mate. È accurato da circa **42 W** in su
(la risoluzione del sensore di corrente dell'auto — un carico minuscolo da ~10 W resta invisibile).

Più in basso trovi mini-statistiche e un **indicatore di "reattività auto"** (un pallino
🟢/🟡/🔴, ⚪ se non ci sono dati): riassume quanto l'auto ha risposto agli ultimi comandi inviati.

#### Le tre temperature: abitacolo, target A/C, batteria
Non tutte le Leapmotor mandano tutte e tre. Mate distingue **tre situazioni diverse**, perché
confonderle porta a numeri assurdi:

- **il sensore c'è ma questo aggiornamento non l'ha portato** → la riga resta e mostra **«—»**;
- **lo zero è un dato vero** (un pacco batteria davvero a 0 °C, in inverno) → Mate stampa **0 °C**,
  perché è la lettura che conta di più;
- **l'auto non manda quel sensore, mai** → la riga **non viene mostrata affatto**, e la relativa
  entità in Home Assistant viene **rimossa**.

L'ultimo caso è **misurato, non dedotto dal modello**: Mate lo dichiara solo dopo circa mezz'ora di
aggiornamenti in cui quel valore non è mai arrivato — così un'installazione appena fatta mostra tutte
le righe, e se un sensore ricomincia a rispondere la riga (e l'entità) **torna da sola** in poche ore.

Se usi la condizione di temperatura in **Preparazione veicolo** ("pre-raffresca solo sopra i 25 °C"),
una temperatura **sconosciuta** non fa scattare la preparazione e lo scrive nel registro. Prima
valeva come 0 °C, quindi su un'auto senza sensore abitacolo la condizione "sotto i 5 °C" era
soddisfatta **a ogni aggiornamento, tutto l'anno**.

### Viaggi
**(menu: Viaggi)** — L'elenco dei tuoi spostamenti, uno per guidata. Per ogni viaggio vedi
**distanza, durata, consumo (kWh/100 km), energia recuperata** in frenata e il **costo** stimato.

- Cliccando un viaggio apri il **dettaglio**, con il **tracciato GPS** su mappa e i dati di quel
  singolo viaggio.
- **Un calendario, e una ricerca.** I viaggi si sfogliano per **mese**; clicchi un giorno e vedi solo
  le guidate di quel giorno, oppure usi la **ricerca** con un intervallo di date, di distanza o di
  efficienza per tirare fuori un insieme da tutta la cronologia.
- **L'unione parte dal giorno che stai guardando.** Una sosta abbastanza lunga da chiudere una guidata
  può spezzare un unico spostamento in due righe. Apri un giorno e il pulsante **🔗** accanto alla data
  ti propone le coppie unibili *di quel giorno*: un cursore allarga cosa conta come una sola sosta,
  vedi l'anteprima del percorso combinato prima di confermare, ed è **reversibile** quando vuoi
  (Separa). Puoi anche **cancellare** un viaggio.
- Soste brevi (semafori, code) **non** spezzano un viaggio: una guidata resta una sola riga.
- **Un viaggio abbandonato dal cloud finisce quando l'auto ha parlato l'ultima volta.** Se il
  collegamento cade mentre guidi, dopo mezz'ora Mate chiude il viaggio da solo — ma lo data
  all'**ultima notizia vera**, non al momento in cui se n'è accorto. Così la durata non contiene
  mezz'ora di silenzio e la velocità media resta quella giusta.
- **I chilometri fatti mentre l'auto non comunicava non finiscono in nessun viaggio.** Quando il
  collegamento col cloud si interrompe, l'auto continua a girare ma Mate non lo vede; al ritorno
  trova solo un contachilometri più avanti. Quel salto può contenere la fine di una guidata, una
  sosta e l'inizio di un'altra, e **non c'è modo di sapere come si divide** — quindi Mate non lo
  attribuisce a nessuno. Sopra il calendario compare una riga con i chilometri, la carica e il
  costo di quel mese, e sulla pagina **Statistiche** il totale di sempre: *misurati, ma non
  attribuibili a un viaggio preciso — perciò esclusi da distanze, consumi e costi.*
  ⚠️ Per questo il totale di Mate può restare sotto al contachilometri dell'auto: la differenza è
  esattamente quella riga.
- **Altimetria e temperatura esterna.** Il cloud Leapmotor non riporta né l'una né l'altra, quindi
  qualche minuto dopo la fine di un viaggio Mate cerca il suo tracciato GPS su
  [Open-Meteo](https://open-meteo.com) (gratuito, senza chiave e senza account). Nel dettaglio
  compaiono la **linea dell'altitudine sotto il grafico SoC e velocità**, i metri **saliti e scesi**, e
  la temperatura **alla partenza e all'arrivo** — non una media, così una salita da fondovalle a passo
  mostra il calo vero. Insieme spiegano buona parte del consumo di una guidata: la salita costa
  energia, il freddo costa autonomia. I viaggi registrati prima che esistesse hanno un pulsante
  **Calcola altimetria**, e tutto si può spegnere dalle Impostazioni.
- **Consumi ufficiali dal cloud 🆕** — quando disponibili, **consumo, efficienza e costo** del viaggio
  vengono dal **dato ufficiale Leapmotor** (la vera ripartizione **guida / A·C / altro**) invece della
  sola stima dal calo di batteria. Subito dopo il viaggio vedi la stima con l'avviso **⏳ provvisorio**;
  appena il cloud elabora il dato (di solito qualche decina di minuti) viene **sostituito da solo** con
  quello ufficiale, e nel dettaglio compare la **ripartizione**. Sui viaggi più vecchi c'è il bottone
  **"Converti con dati ufficiali"**. Se per un viaggio il cloud non ha il dato (capita, su qualsiasi
  auto connessa), resta la **stima** — non è un errore. È **sempre attivo**, nessuna configurazione.
  > L'energia ufficiale può differire un po' dalla stima da SoC (è il conteggio "di guida" del cloud):
  > il viaggio mantiene comunque sempre un valore: ufficiale se c'è, altrimenti la stima.
  - **Conteggiato da quando l'auto è ACCESA, non dall'inizio della guida 🆕** — il dato ufficiale copre
    l'intera **sessione di accensione** (dall'accensione allo spegnimento), quindi può includere il tempo
    ad auto accesa prima di partire. Se **non spegni mai l'auto tra due viaggi** (ti fermi, resti in P,
    riparti), il cloud li conta come **un'unica** sessione — Mate ti avvisa di **unire i due viaggi** per
    avere il consumo reale combinato.
- **La tua nota + tag di guida 🆕** (#107) — nel dettaglio di un viaggio puoi scrivere una **nota libera**
  (traffico, meteo, tipo di strada, qualsiasi appunto) e indicare la **modalità di guida** (Comfort /
  Normale / Sport) e il **One-Pedal** (attivo/disattivo) usati. Mate non può leggerli dall'auto —
  Leapmotor non li manda al cloud — quindi li imposti a mano; aiutano a spiegare perché due guidate
  simili hanno consumato in modo diverso.

### Mappa
**(menu: Mappa)** — Tutti i posti dove hai guidato, su una mappa sola. C'è la posizione attuale
dell'auto (se l'ultimo dato dal cloud non ha un GPS valido, Mate **mantiene l'ultima posizione
valida** invece di far sparire la mappa), e insieme:

- **Il percorso di ogni viaggio**, disegnato come linea continua invece che a puntini sparsi, e mai
  unito fra due viaggi diversi.
- **Un ponte magenta tratteggiato dove il segnale si è perso.** Un tunnel, una zona senza copertura,
  un intoppo del cloud: quando il buco fra due punti registrati è molto più grande della cadenza di
  campionamento *di quel viaggio*, Mate disegna il collegamento **tratteggiato** invece che pieno. Una
  linea piena vuol dire *l'auto ha percorso davvero questo*; una tratteggiata vuol dire *qui l'abbiamo
  persa*, e la retta fra i due capi non è una strada.
- **I luoghi frequenti**, come bolle grandi quanto spesso ti fermi lì, e le **colonnine** che hai
  usato.
- **«Viaggi mostrati»**, una casella nella riga della legenda. Una cronologia lunga riduce la mappa a
  una massa di linee sovrapposte, quindi puoi limitarla agli N viaggi più recenti; **0 vuol dire
  tutti**, ed è così che parte. Limitarla fa anche seguire meglio la strada vera a ogni percorso
  disegnato, perché il budget di punti si distribuisce su meno viaggi.

### Ricariche
**(menu: Ricariche)** — L'elenco delle ricariche. Per ognuna: **energia aggiunta (kWh)**, **potenza
massima**, **tipo** e **costo**, con il **€/kWh effettivo** ben in vista. Il tipo è classificato con
un'etichetta:


- **La banda «da confermare» ti ci porta 🆕** (#240) — quando una ricarica è finita senza un tipo,
  in cima alla pagina compare una striscia. **Cliccala**: apre la ricarica sul suo giorno del
  calendario e la evidenzia, invece di lasciarti indovinare su quale giorno sia.
- **Quando una parte della pagina non si carica 🆕** — diversi riquadri di Mate si riempiono un
  istante dopo l'apertura della pagina. Se uno non ci riesce, adesso **lo dice sotto di sé**, con
  l'errore e un **Riprova**, invece di lasciare uno spazio vuoto senza spiegazione.
- **Casa** (la tua wallbox **o una presa domestica**), **AC** (corrente alternata pubblica),
  **Veloce/FAST** (DC), **HPC** (ricarica ultraveloce) e **✎ Manuale**.
- **Casa non vuol dire wallbox.** *Casa* è **dove** hai caricato, non da cosa: anche una presa
  normale in garage è una ricarica di casa. La differenza conta per il conteggio: se hai collegato
  il contatore di una wallbox (vedi *Wallbox* più sotto), la ricarica si fattura sull'**energia
  erogata dal contatore**; se non l'hai collegato, si fattura sull'**energia arrivata in batteria**,
  esattamente come una ricarica pubblica. Fra le due c'è la perdita in calore del caricabatterie,
  tipicamente il 10-15%.
- **✎ Manuale**: per le colonnine pubbliche con tariffe complicate (abbonamenti, costi di sessione…)
  puoi **scrivere a mano il totale realmente pagato**; questo valore scavalca la stima automatica.
- **I kWh della colonnina 🆕** (#222) — su una colonnina pubblica Mate **non ha un contatore**: legge
  solo quanto è entrato in batteria, mentre la colonnina ti fattura quanto è uscito dal suo. Puoi
  scrivere tu quel numero: sulla scheda della ricarica, sotto le tre mattonelle, c'è una **✎**; il
  riquadro **si apre solo se lo apri tu** e la casella **è sempre vuota** — così un clic di troppo
  non cambia niente, e premere OK a vuoto lascia tutto com'era. *Rimuovi* toglie un valore
  sbagliato. Da lì in poi quel numero **prezza la ricarica**, esattamente come fa il contatore della
  wallbox a casa, e ti mostra l'**efficienza** (quanto ne ha trasformato in calore il caricabatterie
  di bordo). L'energia che Mate riporta resta quella **misurata in batteria**.
- **Cosa entra nel conto e cosa no 🆕** — una ricarica entra in questi confronti solo se ha
  **tutti e due** i numeri, quello del contatore e quello della batteria. Una con uno solo dei due
  spingerebbe il rapporto sopra il 100 %, cosa che nessuna colonnina può fare. **Le ricariche in
  corso restano fuori**: una sessione che sta ancora arrivando non ha un totale da confrontare, e
  entra nei conti quando finisce.
- **Il mese dice tutte e due le cose 🆕** — sopra il calendario: *«154,93 kWh erogati · 142,57 in
  batteria»*. Il primo è ciò che è uscito dai contatori (wallbox, o i kWh che hai scritto tu); il
  secondo è ciò che è arrivato nel pacco. Fra i due c'è la perdita di conversione, che paghi.
- Anche le ricariche avvenute mentre l'auto era spenta/offline vengono **ricostruite** dal salto di
  percentuale di carica.
- **La tua nota 🆕** (#107) — ogni ricarica ha una **nota libera** (subito sopra *Elimina ricarica*) per
  ciò che i numeri non catturano: dov'era la colonnina, ombra/riparo, quanto è affidabile, le condizioni
  del parcheggio, il meteo, qualsiasi appunto personale.
- **Il contachilometri della ricarica 🆕** (#237) — ogni ricarica si porta dietro **quanto segnava il
  contachilometri quando è cominciata**. Mate lo scrive da solo su tutto ciò che vede, e lo ha
  recuperato una volta sola anche dalle ricariche già in archivio. Sulle ricariche che **scrivi tu**
  c'è una casella *Contachilometri*: è l'unico modo per dare dei chilometri a una sessione di prima
  che Mate esistesse — di quei giorni non c'è nessun dato da cui ricavarli. Si scrive nella **tua**
  unità (km o miglia).
- **Quanti km fra una ricarica e l'altra 🆕** (#237) — sotto la ricarica compare *«🛣 122 km dalla
  ricarica precedente»*, preso dal contachilometri dell'auto. Appare solo quando **tutte e due** le
  ricariche hanno il loro numero e solo se l'auto si è mossa davvero: due sessioni lo stesso
  pomeriggio non scrivono niente invece di scrivere zero.
- **Importa le ricariche da un foglio (CSV)** — *Importa ricariche da CSV* scarica un **modello già
  commentato**, lo riempi con Excel o Numbers e lo ricarichi. Le colonne obbligatorie sono solo due,
  data ed energia; le altre — costo, AC/DC, percentuali di carica, ora di fine e **contachilometri
  🆕** — sono facoltative. Anche l'**esportazione** delle ricariche si può reimportare così com'è.
  **Reimportare lo stesso file non crea doppioni 🆕** (#237): una riga che corrisponde a una
  sessione già in archivio la **completa** (le scrive il contachilometri) invece di aggiungerne una
  seconda, e Mate ti dice quante ne ha aggiunte e quante ne ha completate. Prima raddoppiava tutto
  in silenzio. ⚠️ Di una ricarica già registrata viene toccato **solo** il contachilometri: un costo
  che Mate ha calcolato da una curva di ricarica vera non viene mai sovrascritto.

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
**(menu: Statistiche)** — Le tue medie e i totali nel tempo: **distanza dei viaggi registrati** 🆕
(si chiamava *distanza totale*, ma è sempre stata la somma dei viaggi conclusi — non il
contachilometri dell'auto) e numero di viaggi,
**distanza media per viaggio**, **tempo di guida**, **consumo medio** (pesato sulla distanza) e
**migliore**, **energia usata e ricaricata**, **recupero** totale e medio, numero di **sessioni di
ricarica**, con le relative **tendenze** (efficienza e recupero nel tempo). Tra i totali c'è anche una
scheda **Totale V2L** con l'energia cumulativa prelevata via V2L in tutto lo storico. C'è anche la
**ricerca consumo per intervallo** (date libere + preset) e la scheda **"Cumulativo Totale del
Veicolo"** 🆕 — energia totale, chilometraggio e media kWh/100 km **da consegna** (dal contatore
dell'auto, quindi non intaccata dai rari viaggi che il cloud non registra), con una barra
**Guida / A·C / Altro / Da-fermo**.

**Costo per 100 km 🆕** — quanto costa davvero percorrere 100 km: **gli euro spesi** diviso **i
chilometri percorsi**. Nessun prezzo al kWh e nessuna stima — la somma di ciò che hai pagato sopra
la somma di ciò che hai guidato, quindi ci sono dentro anche i kWh che non hanno mosso l'auto
(clima, precondizionamento, perdite del caricatore).

**Gli euro e i chilometri sono dello stesso periodo 🆕** (#237) — una ricarica finita **prima** del
primo viaggio registrato non ha chilometri suoi da farsi dividere, e quindi non entra nel conto. Chi
aveva inserito a mano un anno di ricariche vecchie vedeva mesi di spesa divisi per i chilometri di
un pomeriggio: il numero era decine di volte più alto del vero. Una ricarica fatta **dopo**
l'ultimo viaggio invece i suoi soldi se li tiene — quei chilometri arrivano domani.

**E può dividere per il contachilometri dell'auto 🆕** (#237) — se le tue ricariche hanno il
contachilometri (vedi *Ricariche*), Mate misura la distanza fra la prima e l'ultima con il contatore
dell'auto invece che con i viaggi ricostruiti: da pieno a pieno, come si è sempre misurata la
benzina. **Funziona anche se non c'è nemmeno un viaggio registrato**, che è il caso di chi si è
segnato tutto su un quaderno e installa Mate mesi dopo. Mate sceglie da solo la base che prezza **di
più di quello che hai davvero speso** e lo scrive sotto la cifra — *«sui 18422 km del
contachilometri»* invece di *«sui km registrati»*. Su una storia normale vincono i viaggi e non
cambia niente. Su una versione con range extender la
benzina si aggiunge accanto all'elettrico — la benzina **bruciata**, al prezzo che ti è costato il
serbatoio, non l'intero rifornimento: un pieno pagato è quasi tutto ancora nel serbatoio, e
addebitarlo ai chilometri che non ha ancora fatto rendeva il numero parecchie volte più alto 🆕. Se qualche ricarica non ha un prezzo la card lo dice,
perché in quel caso la spesa vera è più alta. Segue le tue unità: con le miglia diventa «per 100 mi».

Accanto ai soldi la card adesso dice anche **quanti kWh sono serviti per quei 100 km**, con
l'etichetta *«soste comprese» 🆕*. È un bilancio, non una somma di viaggi: l'energia caricata dentro
il periodo, meno quella che a fine periodo è rimasta in batteria e all'inizio non c'era. Quindi
comprende tutto quello che è uscito dal pacco — guida, clima, precondizionamento, perdite del
caricatore — ed è per questo che è **più alto del consumo che vedi in cima ai Viaggi**, e per questo
l'etichetta lo dice. Se una ricarica di quel periodo non ha il dato di energia la card lo scrive: in
quel caso il numero è un minimo, non il totale.

**Da quando sono questi numeri 🆕** — in cima alla pagina una riga ricorda che **tutti** i totali
delle Statistiche sono quelli registrati da Mate dalla sua installazione, con la data d'inizio, e
**non** il totale che segna il contachilometri dell'auto.

**Cosa copre ogni numero 🆕** — *Consumo medio* è la media sui chilometri che **hanno** un consumo, e
sotto compare *«su 452 km di 509 km»* quando quelli sono meno del totale — dice tutti e due i
numeri, così vedi subito se il valore copre quasi tutto il periodo o solo un angolo. *Energia consumata* somma solo i viaggi
di cui Mate conosce l'energia: un viaggio senza quel dato viene **escluso**, non contato come zero,
e la mattonella dice su quanti viaggi sta parlando. Su un'auto in cui tutti i viaggi hanno il loro
consumo — cioè quasi sempre — non compare niente di tutto questo.

### Report mensile
**(menu: Report mensile)** — Una sintesi **mese per mese**: quanto hai guidato, quanta energia hai
usato e ricaricato, quanto hai speso. Comodo per tenere d'occhio l'andamento. Include anche le schede
**consumo ufficiale** (Oggi / Questa settimana / Questo mese) dal cloud. 🆕

Si apre sempre sul **mese in cui sei**, anche il primo del mese con ancora zero chilometri: un mese
vuoto te lo dice, invece di mostrarti in silenzio quello prima. E su un mese senza niente dentro non
compare nessun confronto col mese precedente — ogni casella direbbe −100 %, che descrive il
calendario e non la tua guida.

**Da dove viene il consumo, e quando Mate lo scavalca.** *Consumo medio* ed *Energia consumata*
normalmente sono il totale ufficiale dell'auto per quel mese. Quel totale è completo quanto lo è
stato il collegamento della tua auto: se durante un viaggio l'auto non è riuscita a parlare col
cloud, quel viaggio lì dentro non c'è. Quando il totale torna molto sotto la somma dei viaggi che
Mate ha registrato nello stesso mese, Mate mostra **il proprio numero** — lo stesso della pagina
Viaggi — e lo scrive sotto la casella. La ripartizione Guida / Clima / Altro resta quella dell'auto,
con una riga che avverte che copre solo la parte arrivata al cloud.

### Salute batteria
**(menu: Salute batteria)** — Una **stima dello stato di salute (SoH)** della batteria: quanta capacità
utilizzabile è rimasta rispetto al nuovo. Per ogni ricarica Mate divide l'energia che ha **misurato**
entrare nel pacco (tensione × corrente, integrata sulla sessione) per la percentuale che quella
ricarica ha aggiunto. Quel rapporto è una stima della capacità dell'intero pacco, e il suo andamento
nel tempo — o sui chilometri, come preferisci — è l'invecchiamento.

Tre cose su come viene calcolata, perché cambiano il significato del numero.


- **Un silenzio dell'auto non invecchia più la batteria 🆕** (#241) — la capacità si misura come
  energia rispetto al SoC salito. Dove l'auto smette di riportare per più di un quarto d'ora quella
  energia non viene contata di proposito (nessuno sa cosa abbia fatto il caricatore nel frattempo),
  e adesso **non viene contato nemmeno il SoC di quello stesso pezzo**. Prima una ricarica con
  un'ora di silenzio dentro poteva leggere 81% con la batteria al 100%.
- **Su un collegamento normale non cambia niente.** Dove l'auto riporta come sempre i numeri sono
  identici al decimo; si spostano solo le ricariche che avevano buchi veri — verso l'alto, dove
  dovevano stare.
- **Si ferma al 95 %.** Su un pacco LFP la tensione cambia pochissimo in mezzo alla scala, quindi il
  BMS **conta** la carica invece di leggerla, e deriva; vicino al massimo la curva finalmente sale e il
  BMS **si riancora**, aggiungendo punti percentuali che nessuna energia ha pagato. Contarli farebbe
  sembrare il pacco più piccolo, e nel modo peggiore su un rabbocco breve fino al 100 %, dove sono
  quasi tutta la salita. Quindi l'aritmetica si ferma al 95 %: la ricarica conta lo stesso, resta fuori
  solo il suo ultimo tratto.
- **Le ricariche grandi contano di più, in proporzione.** Il numero in cima somma energia e percentuale
  delle ricariche recenti invece di fare una media una-per-una, quindi una che ha coperto 50 punti pesa
  circa quattro volte una da 13. E non si butta via niente per ottenerlo.
- **Le ricariche a freddo si vedono ma non contano** — una LFP legge basso quando è fredda — come quelle
  partite quasi a zero o quelle in cui il BMS fa un salto.

**Il numero porta con sé un ± , ed è la parte onesta.** È la **dispersione** delle ricariche che ci
stanno dietro, non un'accuratezza: l'energia è misurata, ma la percentuale per cui viene divisa è un
numero che il BMS ha contato, e quel numero deriva. Una banda stretta vuol dire che le tue ricariche
concordano fra loro, non che il pacco sia certamente di quella misura. Con una sola ricarica il ± non
compare affatto, perché una misura non ha dispersione da riportare.

È una **stima**, quindi — non una diagnosi di laboratorio — e si assesta man mano che le ricariche si
accumulano.

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
- La **data di immatricolazione/consegna è modificabile**: clicca la **✏️** accanto alla data
  impostata per correggere un errore (il nuovo valore sovrascrive il precedente).
- Le distanze rispettano l'unità scelta (km o miglia).

### Comandi
**(menu: Comandi)** — I **comandi a distanza**. Da qui puoi:

- **chiudere/aprire**, aprire il **bagagliaio**, **trovare l'auto** (clacson/luci);
- gestire il **clima**: raffrescamento, riscaldamento, sbrinamento, ventilazione, **spegnimento**;
- attivare **riscaldamento sedili**, **volante** e **specchietti** (dove supportato);
- gestire il **limite di carica**.

La scheda del **climatizzatore** ha uno **slider temperatura**, uno **slider ventola** e un
**interruttore ricircolo** (aria fresca / ricircolo). Ogni modo (**A/C AUTO · Raffredda · Riscalda ·
Ventila · Sbrina**) si accende in base al modo **reale** dell'auto — **una sola alla volta**, come
l'app ufficiale. Nei tre modi manuali (Raffredda / Riscalda / Ventila) imposti **temperatura target
e velocità ventola** e l'auto **resta in quel modo e mantiene il valore**. In **AUTO** è l'auto a
gestire ventola e ricircolo: quei due controlli mostrano il valore corrente ma sono in **sola
lettura** (la temperatura resta regolabile). La **Ventilazione** ingrana la sola ventilazione (solo
aria, né caldo né freddo) in modo affidabile da qualsiasi stato.

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

**🆕 Automazione all'accensione** — Invece di premere il pulsante ogni volta, puoi far eseguire a Mate
la preparazione **da sola nel momento in cui l'auto va in Ready** (accensione). Attiva **Automazione
all'accensione**, scegli una volta cosa deve fare — preset del clima e temperatura desiderata, quanto
aprire i finestrini, **ventilazione o riscaldamento** dei sedili guidatore/passeggero, riscaldamento di
volante e specchietti — e salva.

Puoi aggiungere una **condizione opzionale sulla temperatura interna**: esegui la preparazione **solo se
l'abitacolo è superiore a** un valore (es. pre-raffresca solo se supera i 25 °C) **oppure solo se è
inferiore a** uno (es. pre-riscalda solo se è sotto i 5 °C). **Lascia la condizione disattivata e parte a
ogni accensione**, qualunque sia la temperatura. Due cose da sapere sulla condizione: guarda la
temperatura **interna** (l'auto non fornisce quella esterna) ed è decisa **una sola volta, nell'istante
in cui accendi l'auto** — quindi se l'abitacolo cambia più tardi durante la guida, non riparte una
seconda volta.

Parte **una volta per accensione** (non si ripete finché resti acceso, né per un viaggio successivo nella
stessa sessione), ignora i brevi disturbi del segnale e non riparte solo perché Mate si è riavviato.

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
espongono certi dati). Tra questi ora ci sono anche i dati del clima letti dall'auto: **livello
ventola** (1–7), **ricircolo aria** (aria fresca / ricircolo) e **modalità clima** attiva (AUTO /
Raffreddamento / Riscaldamento / Ventilazione).

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
- **Veicolo** — modello e VIN della tua auto, e **con quale account Leapmotor questa istanza fa il
  login**. L'account conta se hai più di un'installazione di Mate — una seconda istanza, una di
  prova, una per auto: modello e VIN descrivono l'*auto*, quindi due istanze che guardano la stessa
  macchina prima erano indistinguibili dall'interno. Qui c'è anche il pulsante **🔓 Esci dall'account**
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

- **🔐 Accesso** *(solo Docker autonomo — sotto l'add-on di Home Assistant l'ingress autentica già
  ogni richiesta e la scheda non compare)* — una password per aprire Mate. Vale la pena metterla:
  senza, chiunque sia sulla tua rete può aprire Mate, e Mate può aprire la tua auto.

  La scrivi **due volte**, perché dopo non c'è modo di rileggerla — è salvata come impronta cifrata,
  mai in chiaro. **Se la perdi** non resti fuori per sempre: il campo *Nuova password* non chiede
  quella vecchia, quindi da un qualsiasi dispositivo ancora collegato ne imposti una nuova. Se non
  c'è più nessun dispositivo dentro, la variabile d'ambiente `MATE_AUTH_PASSWORD` scavalca quella
  salvata.

- **Database** — dimensione del DB e **conservazione posizioni** (retention): puoi tenere i punti GPS
  "per sempre" (predefinito) o cancellare quelli più vecchi di 6/12/18/24 mesi per risparmiare
  spazio. *Vengono potate solo le posizioni*: viaggi, ricariche e curve di ricarica restano.
- **Esporta / backup** — scarica **viaggi (CSV)**, **ricariche (CSV)** e un **backup del database**.
- **🩺 Diagnostica** — una fotografia del sistema (versione, modello, conteggi, ultimo poll,
  integrazioni attive), la possibilità di **vedere i log** (poller/web) e soprattutto di **scaricare
  un pacchetto diagnostico** spuntando le parti volute (info, log poller, log web, **segnali grezzi**).
  Il pacchetto è **già ripulito** dai dati sensibili: **GPS rimosso** e VIN/segreti oscurati, quindi
  è sicuro da allegare quando chiedi assistenza. La riga delle integrazioni riporta separatamente la
  **spunta della wallbox** e **Home Assistant**: la prima dice se hai la funzione attiva, la seconda
  solo se Mate riesce a raggiungere HA. C'è anche una **scansione delle ricariche perse** mentre
  l'auto dormiva.

  🆕 **I cursori che cambiano il comportamento di Mate adesso vogliono un Salva.** Cadenza dei
  poll, rilevamento della ricarica, le soglie avanzate: prima si salvavano appena lasciavi il
  pomello, quindi un dito che passava sopra uno scorrendo la pagina lo cambiava senza chiedere. Il
  cursore si muove ancora liberamente; finché non premi Salva non si scrive niente. **E ogni
  modifica di questo tipo viene registrata** — quando, da cosa a cosa — e finisce nel pacchetto,
  così «si è cambiato da solo» si può verificare.

  🆕 Adesso il pacchetto porta anche **le righe stesse** — le ricariche e i viaggi delle ultime due
  settimane, direttamente dal database — e una sezione che elenca **ogni volta che la batteria si è
  riempita ad auto ferma** insieme a quello che Mate vedeva in quel momento: se il cavo si era
  dichiarato, se Mate aveva concluso che stava caricando, la corrente, e se i dati arrivavano
  freschi o il cloud ripeteva una lettura vecchia. Non è nessuna informazione nuova su di te: è
  quello che Mate già registrava, finalmente scritto dove l'assistenza può leggerlo. Le posizioni
  restano fuori.
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

### MQTT → Home Assistant
Pubblica lo stato dell'auto (carica, autonomia, posizione, porte, stato ricarica…) come **entità in
Home Assistant**, con **auto-discovery**. Tra queste, tre nuove entità V2L **di sola lettura**:
**`V2L Active`** (binary sensor), **`V2L Power`** (W) e **`V2L Session Energy`** (Wh), un binary sensor **`Ready`** che si accende appena l'auto viene accesa — prima che si muova, cioè finché un'automazione fa ancora in tempo ad agire, più i dati del
clima letti dall'auto: il **Livello ventola** (`number` scrivibile, 1–7), il **Ricircolo**
(interruttore scrivibile) e la **Modalità clima** (sensore: AUTO / Raffreddamento / Riscaldamento /
Ventilazione). Puoi anche **comandare** l'auto dalle entità di HA — incluso un **limite di carica**
(`number` scrivibile) per impostare il SoC target e una **Programmazione ricarica** (`text`
scrivibile) che accetta un piano in JSON pensato per le automazioni (`{"start":"23:00","soc":90}` —
ogni campo è opzionale, e quello che ometti resta com'è).

Le entità che la **tua** auto non supporta non ti vengono lasciate addosso: quelle che il modello non
ha (sedili riscaldati, volante…) non vengono create, e un'**entità di temperatura** il cui sensore
l'auto non ha mai riportato viene **rimossa** — non lasciata su `unknown` per sempre. La rimozione
arriva quando arrivano le prove (circa mezz'ora di aggiornamenti), non serve riavviare, e se il
sensore ricomincia a rispondere l'entità **torna**.

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

**Se hai più di un Mate sullo stesso broker 🆕** — per esempio l'add-on normale e quello BetaTester —
dai a ciascuno un **Prefisso topic diverso** (*Impostazioni → MQTT*). Con lo stesso prefisso e la
stessa auto, per Home Assistant sono **un solo dispositivo**: il secondo sembra non funzionare, e
soprattutto **ogni comando parte due volte**. Mate ora se ne accorge e lo scrive; la versione
BetaTester si sposta da sé su un prefisso suo, quella normale non si muove mai.

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
vita). Vale anche il caso opposto: se il contatore della wallbox **si ferma** durante la ricarica
mentre l'auto continua a tirare corrente, Mate smette di fidarsi del suo totale per quella sessione
e fattura sull'energia arrivata in batteria — il totale del contatore sarebbe corto di tutto quello
che si è perso mentre era fermo.
Se una ricarica pubblica ha una tariffa complicata, usa il tipo **✎ Manuale** e scrivi il
totale pagato.

**Il grafico del consumo da fermo (vampire drain) è vuoto.**
Serve almeno una **sosta lunga** con un calo di carica misurabile negli ultimi giorni. Se l'auto è
sempre in carica o dorme da ferma, può non esserci abbastanza materiale. Mate cattura anche il calo
che si "rivela" solo al risveglio.
Un'altra causa frequente è la **soglia del consumo da fermo** in *Impostazioni → Avanzate*: se l'hai
alzata sopra i cali reali della tua auto, il grafico non disegna nulla. Riportala verso **0,2** (o
premi **Reset**) e le finestre ricompaiono. Dalla **v1.22.4** la pagina te lo dice esplicitamente —
mostra comunque il valore tipico e un avviso "sotto la tua soglia" invece di sembrare vuota.
Dalla **v3.10.5** sotto il grafico compare anche **l'ultima sosta scartata**, con la sua durata, il
suo calo e il motivo: così un grafico che non cresce da giorni non sembra più rotto. Spesso il
motivo è che l'auto ha perso **0,1%**, cioè un solo scalino del sensore — sotto quel valore un calo
non si distingue dal rumore, e Mate preferisce non disegnare niente piuttosto che un numero
inventato.

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
- **Vampire drain** — quello che l'auto consuma da **completamente spenta**, misurato dallo spegnimento
  alla successiva accensione. **Include il riscaldamento/raffrescamento ad auto spenta** (by design: auto
  spenta → conta come drain). L'idle ad auto *accesa* (ferma, motore/clima attivo) non rientra qui.
- **Polling** — la lettura periodica dello stato dell'auto dal cloud (non scarica l'auto).
- **Wallbox** — la tua stazione di ricarica domestica.
- **Poller / Web** — i due componenti interni di Mate: il *poller* raccoglie i dati, il *web* mostra
  l'interfaccia. Per te utente è un dettaglio: lavorano insieme.
- **VIN** — il numero di telaio dell'auto; identifica univocamente la tua vettura.
- **PIN operativo** — il PIN a 4 cifre dell'account, necessario per autorizzare i comandi a distanza.

---

> 📌 **Nota di manutenzione del manuale.** Questo documento descrive la versione **v3.11.0**. Quando
> cambia qualcosa di visibile all'utente (una pagina nuova, un'opzione, un flusso), aggiorna la
> sezione corrispondente e la riga di versione in alto. È pensato come base per le traduzioni
> (EN/FR/DE): la struttura è volutamente la stessa dell'interfaccia.
