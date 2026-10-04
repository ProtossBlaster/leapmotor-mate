# LeapMotor Mate — Manuale utente

> **Versione di Mate:** v4.10.0 · **Lingua:** Italiano

## Novità della 4.10.0

**Batteria e tempo di guida del giorno nei Viaggi.** Apri un giorno nel calendario dei Viaggi:
l'intestazione dice adesso quanta batteria hanno usato i viaggi del giorno, *84,4% → 51,6% (−32,8%)*
dal primo all'ultimo — oppure, se l'auto ha caricato in mezzo, quanto hanno usato i viaggi e quanto
hanno aggiunto le ricariche, *−45,3%* e *⚡ +40,2%* — e quanto si è guidato quel giorno, senza i viaggi
ricostruiti, come fa Statistiche. Se manca una lettura, la batteria non compare invece di essere
indovinata. Ogni cifra dell'intestazione del giorno e della striscia del mese dice cos'è quando ci
passi sopra o la tocchi. La riga di ogni viaggio mostra la sua variazione di batteria,
*84,4→51,6% (−32,8%)*, anche sul telefono, dove gli orari del viaggio non vengono più schiacciati. Di
@arekm (#392).

**«Ripristina la stima» solo dove c'è una stima.** Nella pagina di un viaggio il bottone compariva anche
sui viaggi senza una stima messa da parte, dove chiedeva conferma e poi non cambiava niente. Adesso
compare solo dove può rimettere una stima. Di @arekm (#396).

**La programmazione di ricarica aspetta la parola dell'auto.** Salvare una programmazione di ricarica,
o mandare una destinazione al navigatore, adesso aspetta che l'auto dica di averla eseguita, per il
tempo che il cloud le concede — su una B10, 30 s se l'auto dorme e 5 s se è sveglia. «Programma
salvato» vuol dire che l'auto l'ha presa; se l'auto resta muta compare l'avviso **ambra** che non ha
confermato in tempo, e Mate tiene gli orari che aveva. Ogni comando adesso scrive una riga nel
registro: quello che ha mandato, senza indirizzo e coordinate di una destinazione, e quello che ha
risposto il cloud (#395).

### Novità della 4.9.2

**Una fine dice che il suo tempo è venuto dopo lo stato precedente.** Nella pagina Eventi,
«Bloccata · 4 min» si leggeva come un'auto bloccata da quattro minuti, mentre quei minuti erano il
tempo in cui era rimasta sbloccata. Adesso si legge «Bloccata · dopo 4 min». La fine di un viaggio e
di una ricarica tiene la sua cifra, che è la durata della guida o della ricarica.

**La riga a cui torni finisce sullo schermo, anche su un telefono lento.** Tornando da un viaggio con
la mappa aperta, un telefono che disegna lentamente poteva lasciare quella riga sotto lo schermo.

**La pagina Eventi legge solo i giorni che mostra**, e scorrendo un elenco lungo non lo ricompone più
per ogni pezzo: ogni pezzo successivo arriva subito. Su un Raspberry Pi anche la lettura dello storico
al primo avvio è più leggera, e con una conservazione del GPS impostata un viaggio ancora aperto
conserva i suoi eventi come conserva le sue posizioni.

### Novità della 4.9.1

**L'elenco Eventi non slitta più sotto di te.** Scorrendo un intervallo lungo, le
intestazioni delle ore e dei giorni sopra di te cambiavano altezza mentre il browser le raggiungeva,
e quello che stavi leggendo si spostava di qualche pixel — fino a 25 per volta. Adesso le
intestazioni delle ore e dei giorni dichiarano l'altezza che avranno davvero, quindi non spostano più
quello che stai leggendo.

### Novità della 4.9.0

**Un attacco alla spina è una ricarica.** Attacchi la sera e stacchi la mattina, e la pagina
Ricariche mostrava quattro sessioni. È l'auto che le spezza: dichiara il cavo staccato nell'istante
in cui la corrente si ferma, che è esattamente l'aspetto che hanno, da dentro l'auto, una wallbox che
bilancia il carico, un caricatore che segue il sole o una rete che distribuisce la potenza. I pezzi
adesso si rimettono insieme da soli, quando la pausa è sotto i sei minuti e nient'altro è cambiato —
la stessa unione che fa il pulsante **Unisci alla precedente**, con tutti i suoi controlli. Niente
viene riscritto: **Separa** restituisce i pezzi esattamente come li ha raccontati l'auto, e una
ricarica che hai separato non viene mai riunita. Al primo avvio tornano insieme anche le notti già
nel tuo database.

**L'energia di una guidata non può più essere il doppio di quella che la batteria ha perso.** Una
guidata di 7 km segnava 41,4 kWh/100km sul grafico dei consumi, perché il dato del cloud era 2,90
kWh dove la batteria aveva perso 1,07. Mate già rifiutava un dato oltre il doppio della batteria, ma
solo se segnava anche più di 60 kWh/100km. Quando il livello di carica è sceso di un punto intero o
più — una misura vera, non due o tre decimi di arrotondamento — il doppio della batteria adesso
basta da solo. I viaggi già convertiti tornano sulla stima dalla batteria al primo avvio.

**I chilometri misurati senza contatto dicono quanti di loro sono tornati.** I viaggi che lo storico
Leapmotor restituisce per un tratto di silenzio compaiono adesso accanto a quel numero, nelle
Statistiche e sul mese nel calendario dei Viaggi. Non vengono sottratti: quei chilometri sono stati
percorsi senza contatto comunque, e il silenzio non si può ancora dividere fra la fine di una
guidata, una sosta e l'inizio di un'altra.

**L'impostazione della capacità dice perché i kilowattora dell'app ufficiale sono più alti.** L'app
ufficiale conta tutto il pacco, tampone compreso; Mate conta la parte utilizzabile. La stessa
energia, misurata su un 100 % diverso.

### Novità della 4.8.0

**Una pagina Eventi racconta momento per momento cosa ha fatto l'auto.** Chiusa, aperta, portiere,
finestrini, cavo, clima, READY, viaggi, ricariche e i comandi che hai mandato — un inizio e una fine
sono due righe unite da una linea del colore del gruppo, così quello che è successo insieme, e per
quanto, si vede a colpo d'occhio. Una mappa accanto all'elenco dice dove; i filtri per parola,
gruppo e tipo stanno nell'indirizzo, quindi un link o un ricaricamento se li tiene. Gli eventi sono
ricavati dalle posizioni che Mate già salva: su un'installazione esistente il primo avvio rilegge
tutta la storia, una fetta per lettura, e la pagina dice a che punto è. Grazie a **@arekm**, che
l'ha scritta.

**Un viaggio finisce quando finisce l'auto.** Andare a prendere una persona — senza spegnere, solo
P e un'attesa — chiudeva il viaggio dopo un minuto e ne apriva un secondo quando ripartivi. Adesso
la guidata finisce sulla lettura che mostra l'auto **spenta**: aspettare in P con l'auto accesa è
una sosta dentro il viaggio, come un semaforo. Una commissione è un viaggio solo, e il consumo
ufficiale che il cloud misura da accensione a spegnimento gli appartiene tutto.

**Un guasto che Mate ha rimandato non nasconde più quello vero.** Mate tiene un minuto fra due
accessi, e la lettura che cadeva dentro quel minuto diceva «Login temporarily deferred after a
recent attempt» — parole sul nostro timer, che prendevano il posto del guasto che davvero tiene
lontano il cloud.

### Novità della 4.7.18

**Il ripristino di un backup funziona su Windows.** Su MateDesktop per Windows il ripristino di un
backup del database rispondeva un errore, perché il file non si poteva sostituire mentre Mate lo
teneva aperto. Adesso il backup entra nel database vivo; il resto non cambia.

**Un guasto verso il cloud dice perché.** Dove il log, la pagina di configurazione e il pacchetto di
diagnostica dicevano solo «Cloud transport failed» o «stage=transport», adesso aggiungono una parola:
«dns_failure», «timeout», «connection_refused», «certificate_rejected» e così via.

**Aggiornare un'installazione Docker:** il manuale non suggerisce più Watchtower, che è stato
archiviato. Scarica l'immagine e ricrea il container.

### Novità della 4.7.17

**La Panoramica mostra il cavo mentre la wallbox lo tiene.** Una wallbox programmata prende il cavo e non
dà corrente finché non si apre la sua finestra; la Panoramica allora non mostrava il cavo. Adesso lo legge
anche dalla presa AC dell'auto: la targhetta sopra l'auto dice «Cavo connesso (Non in ricarica)», o
«(Carica completa)», e la parola sotto l'auto dice «Park». Di @arekm.

### Novità della 4.7.16

**Una scrittura che trova il database occupato non blocca più tutte quelle dopo.** Una scrittura che
aveva aspettato troppo il database lasciava aperta la sua transazione, e da lì in poi ogni scrittura
falliva con «database is locked» fino al riavvio del container: 17 ore perse su un'installazione. Ogni
giro di lettura adesso chiude prima quella transazione, e lo dice nel log.

**Una T03 rilegge la sua programmazione di ricarica.** Dopo il salvataggio la pagina Ricariche mostrava
«Nessuna programmazione», perché la configurazione della T03 non la porta. Mate adesso la legge dove la
leggeva la libreria precedente, solo per un'auto la cui configurazione non dice se la programmazione è
accesa o quando inizia.

**Report è al plurale nelle altre lingue** (Reports, Berichte, Informes, Rapports…), come tutte le altre
voci del menu; in italiano resta «Report».

### Novità della 4.7.15

**Una T03 salva di nuovo la programmazione di ricarica.** Dalla 4.7.7 la pagina Ricariche rifiutava di
salvare la programmazione di ricarica di una T03 o di impostarne il limite di carica («complete current
charging configuration is required»). Adesso Mate completa i dati che la T03 non manda, come faceva la
libreria precedente.

### Novità della 4.7.14

**Il grafico della ricarica in corso, dal vivo.** Mentre l'auto carica, la pagina Ricariche mostra tra le
schede in cima il grafico *📈 Dati della ricarica* di quella ricarica, che cresce a ogni interrogazione. A
casa disegna la linea della wallbox accanto a quella dell'auto; una pausa col cavo inserito vale 0 kW.
Quando le letture si fermano, al posto di LIVE compare l'età dell'ultima. Di @arekm.

**Il Report mensile ora si chiama Report**, nel menu e sulla pagina. Nient'altro cambia.

### Novità della 4.7.13

**Una T03 riceve di nuovo i comandi.** Dalla 4.7.7 nessun comando arrivava a una T03; con la 4.7.12
ognuno finiva in «signal». Prima di un comando Mate controlla l'ultima lettura dell'auto, e quella di
una T03 la cercava nel posto sbagliato.

**Una carica che manca non è 0%.** *Aggiorna* salvava a 0% una lettura senza carica. Adesso non salva
niente, come il poller, e le posizioni già salvate così vengono tolte una volta sola, al primo avvio.

### Novità della 4.7.12

**Una T03 mostra le sue letture.** Con la 4.7.11 una T03 veniva letta di nuovo, ma Mate mostrava 0%, 0 km
e 0 °C: il cloud risponde a una T03 con campi con nome, e la 4.7.11 li leggeva come numerati. Adesso li
legge per nome, e le posizioni salvate a 0% vengono tolte una volta sola, al primo avvio.

**Il pacchetto di diagnostica tiene fuori le coordinate con nome.** Se hai pubblicato un pacchetto preso
su una T03 con la 4.7.11, contiene la posizione della tua auto: cancellalo.

### Novità della 4.7.11

**Una T03 viene letta di nuovo.** Dalla 4.7.7 una T03 riceveva dal cloud «No data found» a ogni
lettura, e Mate non registrava niente. Adesso Mate chiede un'auto che non ha mai letto come il modello
con cui la elenca il cloud, e all'indirizzo usato dalla libreria di prima; la strada che risponde viene
tenuta per quell'auto. Le auto che venivano già lette lo sono esattamente come prima.

**Dati della ricarica.** Sotto ogni ricarica, *📈 Dati della ricarica* apre un grafico a fasce come
quello di un viaggio: la potenza con i minuti che secondo l'auto mancavano, la percentuale di carica e
le temperature — della cella più fredda e, se attiva, dell'aria esterna.

### Novità della 4.7.10

**Una C10 con range extender mostra di nuovo corrente e potenza di ricarica.** Dalla 4.0.0, durante
una ricarica AC di una C10 con range extender, Mate scartava la corrente del pacco e la potenza
calcolata da essa: Home Assistant mostrava *Charge Current* e *Charge Power* come «sconosciuto» e la
potenza massima di ogni ricarica era 0,0 kW. Il sensore dell'auto invece misura, e adesso tornano
tutte e due. Le ricariche registrate prima di questo aggiornamento restano a 0,0 kW: la loro corrente
non era stata salvata.

### Novità della 4.7.9

**I mesi precedenti dei viaggi, dal cloud Leapmotor.** In **Impostazioni → Storico viaggi cloud**, con
**Importa viaggi dal cloud Leapmotor** attivo, il menu nuovo **Importa anche i mesi precedenti, a
partire da** elenca i mesi da settembre 2026 al mese scorso. Si sceglie una volta: Mate scarica quel
mese e tutti quelli dopo, una volta sola ciascuno, e i mesi che finiscono si aggiungono da soli. La
riga sotto il menu dice cosa aggiunge la scelta. Il cloud Leapmotor non ha viaggi singoli prima di
settembre 2026.

**La salute della batteria si apre più in fretta**, e così il grafico della potenza di una ricarica
([#363](https://github.com/ProtossBlaster/leapmotor-mate/pull/363), di @hubcasale): misurato su
quattro mesi di storico, da circa 1,4 s a 0,05 s.

### Novità della 4.7.8

**Un'installazione nuova parte pulita.** Al primo avvio, con la cartella dei dati vuota, i due
processi di Mate potevano aprire il database nuovo nello stesso istante e quello che registra si
fermava con «database is locked». Adesso aspetta i pochi millisecondi che servono all'altro. Le
installazioni esistenti non l'hanno mai incontrato.

### Novità della 4.7.7

**Niente da scaricare per configurare Mate.** Il certificato dell'app Leapmotor che serve a Mate per
il login — uguale per tutti, identifica l'app e non te — adesso è incluso in Mate e si installa da
solo al primo avvio. La procedura guidata va dritta al tuo account: il passo del certificato e il suo
link non ci sono più. Un'installazione che ha già il certificato lo tiene; uno caricato nel vecchio
formato di esportazione, con righe di attributi in testa, viene riscritto come la copia che Mate
porta con sé — è lo stesso certificato — e i file precedenti restano in una cartella di backup
accanto ai dati.

**Mate gira solo sul proprio client del cloud.** Il client è di Mate dalla 4.0, ma un'installazione
il cui primo controllo non era mai finito veniva rimessa sulla libreria di terze parti che Mate usava
prima. Quella libreria e il ripiego su di essa non ci sono più: ogni installazione usa il client di
Mate. Una che era ancora sulla libreria vecchia fa un login col client nuovo al primo avvio. Nel
pacchetto di diagnostica la riga `Cloud client` dice `independent (mate-api)` per tutti.

**Una C10 non legge più 0 km accanto alla batteria piena.** Quando va a dormire la C10 manda
l'autonomia a 0 con la batteria ancora carica, e la Panoramica mostrava «100% · 0 km». Mate non prende
più quello zero per una lettura: la Panoramica tiene l'ultima autonomia riportata dall'auto, e Home
Assistant tiene il suo ultimo valore. Sotto il 5% di carica uno zero conta ancora, perché una batteria
vuota può volerlo dire.

**Il riquadro dei costi conta una volta sola una ricarica unita.** Nelle Statistiche il riquadro del
costo per 100 km diceva che a una ricarica mancava il prezzo quando era una delle due righe che avevi
unito. Adesso conta le ricariche come le mostra la pagina Ricariche; gli euro e i kWh non cambiano.

### Novità della 4.7.6

**Un viaggio finisce quando spegni l'auto.** Mate dichiara finita una guida quando l'auto è stata in P
per circa un minuto, e quel minuto lo scriveva *tutto* dentro il viaggio. Adesso la fine — ora, livello
di carica, contachilometri, posizione e carburante — viene dalla prima lettura di quella sosta che
mostra l'auto **spenta**. Su quattro mesi di storico di un proprietario questo ha spostato la fine di
183 viaggi, da 7 a 54 secondi (42 in media). Si vede sulle guide corte: un salto di 1 km che leggeva
2,9 minuti a 21 km/h adesso legge 2,2 minuti a 27 km/h. Chilometri, kWh e consumi non cambiano.

Se scendi ad aprire un cancello e risali per fare la retromarcia nel posto, la guida resta una sola.
Un'auto lasciata accesa in P, o una che non dice se è accesa, mantiene la fine che Mate scriveva prima.
⚠️ **Le guide già registrate non cambiano** — vale da questa versione in avanti.

ℹ️ Su una guida di un chilometro o due il cloud a volte dà la sua energia come 0,0 kWh, e Mate
preferisce la cifra dell'auto alla propria stima. Qualche viaggio molto corto può quindi leggere
`0,00 kWh/100 km` dove prima mostrava una stima di qualche centesimo.

**La pagina Ricariche si apre subito.** Aprire le Ricariche, o un giorno del loro calendario, leggeva
tutto il registro delle posizioni per ogni ricarica della pagina. Su un database con 381.076 righe di
posizione è passata da **880 ms a 1 ms**, con le stesse cifre a schermo.

**Una ricarica che hai unito può ricevere il suo luogo direttamente.** Prima bisognava separarla,
assegnare il luogo e riunirla; adesso il luogo vale per tutto il gruppo. Unire e separare non ricaricano
più la pagina intera e non perdono il giorno che stavi guardando.

### Novità della 4.7.5

Due modifiche, trovate entrambe in uno stesso pacchetto di diagnostica mandato da un utente.

**Mate adesso conosce la B03X.** Il cloud Leapmotor manda il nome di progetto *cinese* di un'auto,
quindi il crossover venduto in Europa come B03X arriva come `A10` — e Mate non aveva una voce per lui.
La batteria ripiegava così sulla cifra usata per un'auto che Mate non ha mai sentito, 65,0 kWh, che la
B03X non ha mai avuto; e la procedura guidata non offriva nessuna variante, quindi il primo
proprietario arrivato ha scritto un numero a mano. Ha scritto 53,0, che è la cifra della scheda
tecnica — la capacità **di targa** — mentre quel campo vuole quella **utilizzabile**, l'energia che
l'auto ti lascia davvero prelevare. Adesso la procedura guidata offre entrambi i pacchi della B03X:
**39,0 kWh** (targa 39,8, 292 km WLTP) e **52,0 kWh** (targa 53,0, 382 km).

⚠️ **Se hai già scritto una capacità, Mate non la tocca** — non sovrascrive mai un numero scelto da te.
Una B03X impostata su 53,0 legge circa il 2% in meno su ogni cifra di energia; portala a 52,0 da
**Impostazioni → Batteria** e da lì in avanti è giusta.

**La B03 non è la B03X.** Sono a un carattere di distanza e sono due auto diverse: la B03 è la
hatchback, circa 10 cm più corta. In Mate non c'è ancora, di proposito — non è in vendita e le cifre
della sua batteria non sono pubblicate da nessuna parte. Due cose che la B03X non ha nemmeno lei: un
programma di manutenzione validato, e una scala di apertura dei finestrini misurata, quindi la
percentuale dei finestrini potrebbe essere sbagliata.

**Se mandi un pacchetto di diagnostica, adesso dice perché la tua installazione è ancora sul client
del cloud più vecchio** — lo stato, il motivo e quando ha tentato il passaggio l'ultima volta.
L'identità del tuo account continua a non finire mai in un pacchetto.

### Novità della 4.7.4

Tre modifiche di chi contribuisce. Due riguardano Mate che chiede di meno; la terza un segnale che,
quando l'auto non lo mandava, veniva scritto come se l'auto avesse risposto.

**Mate smette di richiedere la temperatura esterna quando il servizio meteo rifiuta.** La lettura
arriva da un servizio gratuito con una quota giornaliera. Una richiesta fallita non lasciava traccia,
quindi il poll successivo richiedeva, e così ogni poll dopo — in una giornata in cui la quota è
finita, 432 rifiuti in meno di quattro ore. Una richiesta fallita adesso aspetta venti minuti, che è
esattamente per quanto Mate già si fida di una lettura buona; misurato su quattro ore di richieste
rifiutate, 480 prima e 12 adesso. Se fallisce la primissima richiesta dopo un avvio resti senza
temperatura esterna per venti minuti; una lettura che c'è già viene tenuta, come prima.

**Un READY che l'auto non ha mandato non viene più scritto come «spenta».** READY dice se l'auto è
accesa, e Mate lo usa per capire se due guidate appartengono alla stessa accensione — che è quello
che decide quando ti propone di fondere due viaggi. Un frame può arrivare senza, e quell'assenza
veniva salvata come uno zero. Una sosta in P in cui l'auto non ha detto se era accesa teneva due
guidate nella stessa accensione per quanto durasse; adesso si comporta esattamente come uno
spegnimento che si vede, e due secondi in P per cambiare modalità di guida tengono comunque insieme
la guidata. La scheda di stato mostra un trattino per un valore che l'auto non ha mai mandato. Per
un'auto che dichiara READY non cambia niente: tutto lo storico vero si ricostruisce identico.

C'è anche, e dalla tua installazione non si vede: la suite dei test di Mate non chiama più l'esterno.
Risolveva 187 indirizzi esterni a ogni giro, abbastanza che due giri in un'ora esaurissero la quota
oraria che GitHub concede a un indirizzo — e un'installazione che condivideva quell'indirizzo si
vedeva poi rifiutare il proprio controllo aggiornamenti.

### Novità della 4.7.3

Due modifiche, entrambe di chi usa Mate, e una delle due corregge una cosa che questo progetto aveva
sbagliato in pubblico.

**Mate smette di chiedere al cloud di accedere ogni due ore.** La 4.4.0 gli aveva insegnato a
rinnovare una sessione invece di ricomprarla con un login, e diceva che voleva dire circa un login a
settimana. Non era così: il rinnovo veniva usato solo quando il cloud rifiutava un token a metà
richiesta, quindi una sessione che scadeva normalmente costava comunque un login — misurato su
un'installazione vera, uno ogni 119 minuti, dodici al giorno, mentre il biglietto di rinnovo salvato
accanto era buono per un'altra settimana. Quel cloud raziona i login, e un login rifiutato è un
tratto in cui Mate non riceve niente: né mappa, né durata, né velocità. Dopo la correzione, sulla
stessa installazione: sei rinnovi di fila in una notte e nessun login. Non c'è niente da fare da
parte tua.

**Un luogo di ricarica può dire che tipo di colonnina è.** I luoghi erano nati per una seconda casa,
quindi ogni luogo salvato tipizzava le sue ricariche come Casa — anche una colonnina al lavoro o una
comunale gratuita. Un luogo adesso porta il proprio tipo (Casa, AC, DC, HPC o Gratis), e il tipo
decide il prezzo oltre al badge: un luogo tipizzato AC a 0,45 fa costare 10 kWh 4,50, e uno Gratis
non costa niente qualunque tariffa gli fosse rimasta addosso. I luoghi che hai già leggono Casa,
esattamente come prima. Assegnare un luogo a una ricarica non ricarica più tutta la pagina, così il
giorno che avevi aperto in Ricariche resta aperto.

### Novità della 4.7.2

Nove cose che Mate già sapeva e non usava.

**I chilometri fatti mentre Mate non vedeva l'auto vengono tenuti.** Se il contachilometri si è
mosso mentre Mate non era in contatto, quel salto è l'unica traccia della guidata — e si perdeva in
due casi: quando la lettura di ritorno non portava affatto il contachilometri, e quando attaccavi la
spina appena arrivato a casa. Adesso vengono ricostruiti entrambi. Se fra le due letture c'è una
ricarica, i chilometri restano senza una cifra di energia, perché la differenza di batteria
attraverso una ricarica non è quello che ha speso la guidata.

**Una lettura che l'auto non ha mandato non viene più salvata come zero.** Una velocità mancante e
una sosta misurata erano identiche nello storico; e così un contachilometri mancante e uno fermo.

**Una guidata interrotta da un cambio d'ora finisce dove è finita davvero.** Se l'orologio della tua
macchina torna indietro durante la guidata — una correzione NTP, un Raspberry Pi che si risveglia —
il viaggio veniva chiuso sulla lettura sbagliata, prendendo da lì anche contachilometri e SoC finali.

**Una ricarica che l'auto smette di dichiarare viene comunque disegnata.** Se abbassi la wallbox a
metà sessione e la tua auto smette di dichiarare la ricarica sotto la sua corrente di rilevamento,
il grafico della potenza finiva in quel minuto mentre la ricarica andava avanti per ore. Adesso il
grafico, il confronto con la wallbox, la divisione a fasce e il costo a tariffa dinamica leggono
tutta la sessione. I tuoi kilowattora e i tuoi totali non sono mai stati toccati.

**Le etichette non finiscono più sopra i loro valori** nelle lingue con parole lunghe — lo spagnolo
soprattutto, sulla scheda di riepilogo.

**Un'installazione rimasta sul vecchio client del cloud riprova.** Quella scelta era stata fatta una
volta sola, parecchie versioni fa, e un controllo andato semplicemente in timeout o finito su un
database occupato veniva tenuto come se fosse una risposta.

**Il €/kWh di una ricarica adesso dice per quali kilowattora divide** — quelli erogati dalla
colonnina, o quelli arrivati in batteria. Le due cifre erano giuste tutte e due; mancava la parola.

**Una programmazione di ricarica che la tua auto non accettava adesso passa.** Se l'auto pubblica
con un valore inatteso una delle impostazioni che Mate legge e riscrive tali e quali, salvare la
programmazione — o cambiare il limite di SoC — falliva del tutto. Quelle impostazioni sono
dell'auto, non di Mate: qualunque cosa dica le torna indietro uguale.

### Novità della 4.7.1

Niente di nuovo a schermo: cinque punti in cui Mate si fermava prima della fine di quello che stava
facendo.

**Una caduta di linea non lascia più una guidata registrata a metà.** Se Mate perde il cloud durante
una guidata e l'auto è ferma o in ricarica quando il collegamento torna entro mezz'ora, il viaggio
adesso finisce lì e tiene i chilometri fatti nel buco. Dopo un silenzio più lungo finisce sull'ultima
cosa che ha detto l'auto, e i chilometri dopo vengono trattati come tutti gli altri fatti fuori
contatto. Prima il viaggio restava semplicemente aperto fino al riavvio del poller, e la guidata dopo
ne apriva un secondo accanto. I viaggi che una versione precedente ha lasciato aperti vengono
sistemati al poll successivo. ⚠️ Se hai impostato una **conservazione del GPS**, i punti di una
guidata ancora in corso adesso restano finché non finisce, perché la sua fine si legge da lì.

**Il grafico della potenza di una ricarica che hai unito adesso disegna tutta la sessione.**
Abbassare la wallbox in mezzo alla notte chiudeva lì il grafico, mentre la sessione andava avanti per
ore. I chilowattora e il costo sono sempre stati giusti: si fermava solo il disegno.

**Due messaggi dicono di più.** Una programmazione di ricarica che Mate si rifiuta di mandare adesso
nomina l'impostazione sbagliata e il valore che la tua auto ha pubblicato, invece di una frase sola
buona per tre impostazioni diverse. E su un'installazione che si sta aggiornando, uno scontro
innocuo fra le due metà di Mate non tronca più il resto dell'aggiornamento del database.

**Toccare il logo in cima alla pagina ti porta a casa**, sul telefono come sul computer.

### Novità della 4.7.0

**Se guidi una Leapmotor con range extender, adesso Mate è anche per te.** Quei modelli si potevano
leggere solo con la build BetaTester; le loro pagine — la pagina REEV, la benzina per viaggio e per
periodo, e i **pacchi batteria REEV nella procedura guidata** — stanno adesso sull'add-on normale e
sull'immagine Docker normale.

**La cifra della benzina è quella dell'auto.** Lo storico di Leapmotor tiene, per ogni guidata, quanta
benzina l'auto dice di aver bruciato: è il numero che vedi nell'app ufficiale. Mate se lo calcolava da
sé, dal livello del serbatoio ai due estremi della guidata, e sull'unica guidata in cui si sono potuti
confrontare tutti e tre veniva fuori il **20,7% in meno** — 3,886 L contro 4,9. Adesso vince la cifra
dell'auto; il serbatoio resta come riserva per una guidata di cui Leapmotor non ha record, e ogni
cifra dice quale delle due stai guardando. ⚠️ **Alcuni viaggi vecchi leggeranno diverso dopo
l'aggiornamento**: la finestra di Leapmotor è di circa 28 giorni, quindi le guidate più vecchie
tengono la risposta del serbatoio, circa un quinto più bassa.

**Un viaggio che non ha bruciato niente lo dice.** Una REEV va quasi sempre in elettrico, e quelle
guidate non mostravano niente — uguale a una guidata di cui Mate non è riuscito a leggere il
serbatoio. Quando il contatore dell'auto legge lo stesso valore ai due estremi, quella è una misura, e
adesso legge `0 L` con accanto *tutto elettrico*. Il bianco è tornato a voler dire una cosa sola: non
lo sappiamo.

**Il recupero in frenata è di nuovo frenata.** Su una REEV il generatore ricarica la batteria mentre
guidi, e Mate lo contava come energia recuperata dalla frenata — sull'unica guidata misurabile ne era
l'89%. Adesso non lo conta più. La cifra resta nascosta su una REEV, come prima, ma quello che viene
salvato adesso è onesto.

**Un riavvio non rovina più un viaggio.** Quando Mate si riavvia in mezzo a una guidata, quel viaggio
viene chiuso dopo, da quello che era già stato registrato. Prima perdeva il contachilometri d'arrivo,
il livello del serbatoio all'arrivo e tutto il recupero in frenata, che leggeva 0,00 kWh — **e
quest'ultimo anche sulle auto completamente elettriche**. Tutti e tre adesso si ricostruiscono dalle
letture della guidata stessa.

Inoltre: se il tuo database rifiuta le scritture — certe condivisioni di rete lo fanno — la pulizia
quotidiana non riprova più a ogni singola interrogazione, che sull'installazione che l'ha segnalato
erano 266 tentativi in quattro ore.

### Novità della 4.6.0

A ogni interrogazione mentre guidi, Mate legge la potenza che esce dalla batteria, la temperatura
della sua cella più fredda, la stima di autonomia e l'aria esterna. Le conservava tutte e non te ne
mostrava quasi nessuna. Quelle quattro letture restano adesso **insieme al viaggio** e stanno sulla
sua pagina: **Potenza max** e **Recupero max**, la temperatura della batteria e quella esterna come
intervallo dalla più bassa alla più alta della guidata invece che come media, e — sotto la durata —
quanto ne hai passato **in movimento, da fermo e senza dati**, in minuti interi che sommano alla
durata scritta sopra. Accanto alla velocità media c'è ora la **mediana** delle stesse letture, che su
una guidata mezza in autostrada e mezza in coda dice più di quanto dica la media.

Il grafico sotto la mappa si chiama adesso **Dati del viaggio**: un grafico in tre bande su un solo
asse dei tempi — velocità e potenza, SoC e autonomia, altitudine e temperatura della batteria — con un
solo riquadro al passaggio del mouse per tutte. La sua legenda accende e spegne ogni linea, e la tua
scelta resta memorizzata in questo browser.

La **velocità di punta** è corretta: dove il record che Leapmotor tiene di quella guidata è agganciato
al viaggio, la cifra è quella dell'auto, non il campione più veloce di Mate. Le letture di Mate sono a
circa undici secondi l'una dall'altra, quindi una punta più breve non c'è mai stata dentro — su 38
guidate il campione stava sotto la cifra dell'auto in 37.

⚠️ **I viaggi che hai guidato prima di questa versione ricevono quelle letture una volta sola,
all'avvio di Mate**, e solo dalle interrogazioni la cui riga di posizione è ancora nel database. Se
hai impostato una conservazione del GPS, sui viaggi più vecchi vedrai un trattino: a 7 giorni si
riempie circa il 3% dei loro punti, a 30 giorni un quinto, a 90 giorni sette decimi. Col valore di
serie — tieni tutto — si riempiono tutti. Ogni viaggio da qui in avanti ha le letture qualunque cosa
dica quell'impostazione.

### Novità della 4.5.5

Questa versione toglie due cose e non ne aggiunge nessuna, ed erano entrambe sugli aggiornamenti
software dell'auto. La riga «Aggiornamenti OTA» della panoramica diceva **Nessuno** ogni volta che
nella casella dei messaggi del tuo account non c'era un avviso — e della tua auto non ha mai
saputo niente: Leapmotor le versioni le dice solo all'account che la possiede, e Mate deve girare
su un account con cui l'auto è condivisa, che di avvisi del veicolo non ne riceve nessuno. Quindi
diceva «Nessuno» per sempre, e sotto quell'etichetta «Nessuno» si legge come «sei aggiornato».
⚠️ Con lei se ne va l'entità **OTA Update Notice** in Home Assistant — su tre diagnostiche di
proprietari ha trovato zero avvisi in 44 scansioni riuscite — quindi se ci avevi costruito
un'automazione, quell'automazione resterà senza entità. Non cambia nient'altro, e nei tuoi dati
non viene scritto niente.

### Novità della 4.5.4

Di quello che vedi in questa versione non cambia niente: serve a noi. Dalla 4.5.3 Mate conserva lo
storico dei singoli viaggi che tiene il cloud Leapmotor — gli stessi dati che l'app ufficiale mostra
nel suo riquadro per viaggio — e ogni record dice quanta benzina quel viaggio ha consumato. Su
un'ibrida ad autonomia estesa è un secondo dato, indipendente, accanto a quello che Mate già legge
dal contatore del serbatoio dell'auto, e stava nel database senza che si potesse tirare fuori. Ora
viaggia nel pacchetto di diagnostica, e il testo della diagnostica dice se quello storico è arrivato
e se il campo del carburante è pieno o piatto a zero. Su un'auto solo elettrica è zero su ogni
viaggio, che è la risposta giusta e non un silenzio. Nei tuoi dati non viene scritto niente e al
cloud non viene chiesto niente di nuovo.

### Novità della 4.5.3

Mate è ancora più veloce, e stavolta il motivo non sono le domande ma il chiedere. Ogni lettura apriva
una connessione nuova al database, e su una lettura piccola era quasi tutto il costo; adesso ce n'è una
per thread. E la stima di salute della batteria leggeva ogni frame di ogni ricarica solo per scoprire
che non c'era nessuno in auto col riscaldamento acceso — ora è una verifica su indice. Misurato su un
add-on vero con novanta giorni di storia: panoramica 0,213 s → 0,048, batteria 0,129 → 0,012,
ricariche 0,278 → 0,035, statistiche 0,489 → 0,163, la salute della batteria 1,150 → 0,114. Di quello
che vedi non è cambiato niente.

### Novità della 4.5.2

Mate è più veloce, su ogni pagina. Le pagine più lente chiedevano al database la stessa cosa continuamente — in quale fuso orario mostrare un'ora, una volta per riga; quale auto stai guardando, ottantasette volte per disegnare un riquadro; se l'auto è stata usata come presa di corrente, leggendo una settimana di dati — e ognuna di quelle domande apriva una connessione sua al database. Adesso si chiedono una volta sola. La pagina della batteria non aspetta più i suoi due conti lunghi: appare, e la salute e il consumo da fermo si riempiono dopo. Misurato su un add-on vero con novanta giorni di storia: batteria 3,526 s → 0,121 s, statistiche 2,679 → 0,448, viaggi 1,696 → 0,406, impostazioni 1,613 → 0,413. Di quello che vedi non è cambiato niente.

### Novità della 4.5.1

Mate carica più in fretta. Decidere quali bottoni può mostrare la tua auto leggeva il database 156 volte per ogni pagina — una per comando, tre impostazioni ciascuno, e ognuna apriva una connessione sua. Ora le legge una volta sola. Su un add-on che gira da una scheda SD era la maggior parte dell'attesa. Il riquadro «Collegamento al cloud» nelle impostazioni non si costruisce più a ogni apertura della pagina: si prende i suoi numeri quando lo apri tu. E il menu resta dov'era — scegliere una voce in basso lo riportava in cima, e la voce appena usata finiva di nuovo fuori schermo.

### Novità della 4.5.0

La panoramica dice se quello che mostra è affidabile. Accanto al titolo c'è un riquadrino: **Mate → cloud → auto**, due pallini, e passando il mouse (o toccando) su ogni parola trovi i fatti che ci stanno sotto — da quanto tempo il poller gira, se il cloud lo fa entrare e quando ha risposto l'ultima volta, quando l'auto ha mandato l'ultimo dato e cosa stava facendo. Finché tutto funziona, non scrive altro. Quando è Mate che non riesce a prendere i dati, il riquadro diventa rosso e dice cosa comporta: quando è arrivato l'ultimo dato, quando riprova, l'errore che ha dato il cloud e — solo se il cloud ha incolpato la password — che è la password da controllare. Fino a ora un cloud che rifiutava i login di un'installazione da nove giorni era identico a un'auto che dorme in garage: «visto 9 h fa», e nient'altro.

Home Assistant sente la stessa cosa. Ogni auto ha un sensore **Data Link** (`sensor.<auto>_data_link`) che vale `fresh`, `no_new_data`, `age_unknown`, `login_refused` o `fetch_failed`, con da quando, l'errore e il prossimo tentativo come attributi. Viene pubblicato anche mentre Mate sta aspettando dopo un login rifiutato, e scade da solo dopo 21 minuti — così `unavailable` vuol dire che il poller si è fermato, non che l'auto è ferma. Basta un'automazione per essere avvisati: notificami quando non è né `fresh` né `no_new_data` da un'ora.

Nelle impostazioni c'è un riquadro nuovo, **📡 Collegamento al cloud**: le ultime 24 ore come striscia di finestre da cinque minuti, e sette giorni di conteggi — quante letture, quante portavano un dato attuale, quante sono fallite, quante il cloud ha rifiutato e quanti login ha speso ciascuna parte di Mate. Ogni cella e ogni etichetta si spiega da sé al passaggio del mouse. La stessa tabella ora entra nel pacchetto diagnostico.

Due cose più piccole. Un'età oltre il giorno si scrive in giorni: nove giorni senza contatto si leggevano «216h fa». E il controllo di salute di Mate non dichiara più un processo morto mentre il cloud non lo fa entrare: sta aspettando, e adesso lo dice.

Sotto l'energia di un viaggio, l'etichetta che diceva **getEC** ora dice **Misurata dall'auto**: era il nome di un endpoint del cloud, non una parola per le persone. **Cloud Leapmotor** diventa **Storico Leapmotor** per lo stesso motivo — vengono dal cloud entrambe, e cambia quale delle due: il viaggio come lo registra lo storico del cloud, oppure l'energia che l'auto stessa ha misurato in quella finestra. **Stima Mate** resta com'era.

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
   - [Prezzi di ricarica](#prezzi-di-ricarica) · [Statistiche](#statistiche) · [Eventi](#eventi) · [Report](#report)
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
- **Elettriche e con range extender.** Sono supportate **T03, B03X, B05, B10, C10**. Le loro versioni
  **REEV**, con range extender a benzina, sono supportate dalla **4.7.0**: la pagina REEV, i litri per
  viaggio e per periodo e i pacchi batteria REEV nella procedura guidata stanno tutti sulla build
  normale. Su una REEV **non** viene mostrato il recupero in frenata — con un generatore che ricarica
  il pacco mentre guidi, la ricarica non si distingue dalla frenata — e il consumo elettrico di un
  viaggio col generatore resta sulla build BetaTester, dove lo si può tenere d'occhio.
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

2. **Niente da scaricare per il certificato dell'app.** Mate ha bisogno del certificato TLS dell'app
   Leapmotor (`app.crt` + `app.key`) per il login — è **uguale per tutti** (è quello dell'app, non del
   tuo account). È incluso in Mate e si installa da solo al primo avvio: non ti viene mai chiesto.

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
  non ha ancora una reputazione per SmartScreen e viene fermato all'ingresso. Il server web ascolta
  soltanto su questo computer; per accedere da un altro dispositivo usa Docker o l'add-on.

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


**Come si aggiorna Mate.** Il distintivo **↑ vX.Y.Z** accanto alla versione, in alto a sinistra, dice
che su GitHub c'è una release più nuova (controllato ogni 6 ore). È un avviso, non un pulsante: cosa
premere dipende da come fai girare Mate.

- **Add-on di Home Assistant** — non devi fare niente a mano. Home Assistant propone l'aggiornamento
  sull'add-on stesso, e premerlo è tutta la procedura. Se il distintivo non è ancora comparso:
  *Add-on Store → ⋮ → Controlla gli aggiornamenti*. I tuoi dati (`/data`) restano dove sono.
- **Docker** — scarica l'immagine nuova e ricrea il container:

  ```
  docker pull ghcr.io/protossblaster/leapmotor-mate:latest
  docker compose up -d          # oppure: docker rm -f <container> && docker run … come prima
  ```

  Il database sta nel volume, non nell'immagine, quindi non si perde niente.
- **MateDesktop** — non c'è niente da scaricare: l'app prende Mate dal repository **a ogni avvio**,
  quindi chiuderla e riaprirla *è* l'aggiornamento.

**Cosa cambia nella v3.14.2 🆕**

- **I viaggi unibili si disegnano una volta sola.** La vista proponeva coppie, quindi un viaggio in
  mezzo ad altri due compariva due volte. Adesso una serie di viaggi è un blocco unico con un gancio
  fra ogni coppia vicina — la fusione in sé non cambia.
- **La sosta dentro un viaggio unito è segnata sul grafico**, ombreggiata e con la sua durata: non
  sembra più che l'auto abbia perso il segnale.
- **La nota di una ricarica unita descrive tutta la sessione**, non solo il primo pezzo.
- **Il tempo che manca e il «traguardo ricarica programmata»** dicono cosa sono davvero: il traguardo
  della PROGRAMMAZIONE di ricarica, usato solo mentre quella programmazione è accesa. Il limite
  massimo che sposti nell'app dell'auto il cloud non lo manda.
- **Le Impostazioni avvisano se la soglia di rilevamento è più alta della corrente che l'auto
  assorbe.** Una soglia troppo alta non registra «nessuna ricarica»: ne registra metà.
- **Il pacchetto diagnostico si scarica anche dal telefono.** Era una navigazione di pagina, che il
  webview di Home Assistant scarta in silenzio; adesso è un collegamento normale.

**Nella v3.14.3–3.14.4 🆕** — con due auto, un **comando arriva all'auto che hai scelto, nella forma che quel modello capisce**. Fino a queste versioni la sessione che parla col cloud restava sulla prima auto elencata dall'account, quindi chiusura, bagagliaio, finestrini, clima e i comandi di ricarica andavano a quella qualunque cosa dicesse il selettore — e così la foto dell'auto e i consumi presi dal cloud. Anche il modello veniva letto da quella stessa auto: su un account con due modelli **diversi**, la posizione dei finestrini e i comandi di clima e spegnimento A/C erano costruiti con le regole dell'auto sbagliata. Con una macchina sola, o due dello stesso modello, non cambia niente.

**Nella v3.14.5 🆕** — altri due punti rispondevano ancora per l'installazione invece che per l'auto scelta: i **consumi tenuti in cache** (guardavi le Statistiche di un'auto, cambiavi entro mezz'ora e ti mostrava i kWh della prima) e l'**inizio del servizio** della manutenzione, la cui data di consegna e i cui chilometri erano condivisi fra le auto — e da quelli si contano tutte le scadenze. Con una macchina sola non cambia niente.

**Nella v3.14.6 🆕** — la riga **Sicurezza** non compare più sulle auto che non la comunicano. La C10 quel segnale non lo manda affatto (misurato su due C10, una per diciassette giorni di fila), e leggere l'assenza come uno zero faceva stampare *«Inattivo»* — che su una riga di sicurezza si legge come *la tua auto non è protetta*. Un'auto che invece lo manda, come la B10, non cambia.

---

## 4. Primo avvio: la configurazione guidata

Al primo accesso Mate mostra un **wizard** (procedura guidata). In alto puoi scegliere la lingua
(🇮🇹 Italiano). Poi:

### Passo 0 — Scegli come iniziare

Due pulsanti:

- **▶ Configura la mia auto** — la configurazione vera e propria (continua sotto).
- **🧪 Prova la demo** — entra in modalità dimostrativa con dati finti. Puoi uscire quando vuoi.

### Passo 1 — Accesso all'account

Inserisci:

- **Email account Leapmotor**
- **Password**
- **PIN operativo** (4 cifre)

> ⚠️ Qui Mate ti ricorda di usare **un account dedicato solo a Mate** (vedi
> [requisiti](#2-prima-di-iniziare-i-requisiti)).

Premi **🔍 Rileva la mia auto**. Mate verifica le credenziali e legge dal cloud **modello e numero
di telaio (VIN)**. Se tutto va bene vedi una scheda "Auto rilevata" con `Leapmotor <modello> · VIN
···xxxxxx`.

### Passo 2 — Batteria

In base al modello:

- se la versione europea ha **una sola variante** di batteria, Mate la imposta da solo — oggi solo la
  T03 (36,0 kWh);
- se ci sono **più varianti** — B10 e B05 (Pro 55,0 kWh / Pro Max 65,0), C10 (RWD 67,0 / AWD 81,9) —
  **la scegli tu**: il cloud non dice quale hai a bordo, quindi Mate non può saperlo;
- se il rilevamento non riesce, puoi **inserire la capacità a mano** (in kWh).

> La capacità indicata è quella **utile/netta** (quella che conta davvero per consumi e costi) e si
> può sempre correggere dopo, da Impostazioni → Batteria.
> Accanto c'è il **Riferimento SoH**: la capacità da nuova su cui si misura la salute batteria.
> Mate la fotografa la prima volta che salvi la capacità e poi non la tocca più, così adottare un
> valore misurato (e già invecchiato) non può riportare la salute a ~100 % nascondendo
> l'invecchiamento. Se è stata registrata sbagliata, la salute può superare il 100 %: si corregge lì.

> **Se un valore predefinito di Mate è stato smentito 🆕**, Impostazioni → Batteria lo dice sul
> posto e offre la cifra corretta con un pulsante — non riscrive mai il numero alle tue spalle.
> Oggi riguarda la **C10 RWD**: 69,9 kWh è il valore di targa, e le ricariche vere danno un pacco
> utile di 67,0.

### Passo 3 — Connetti

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
- **Striscia «mai configurata» 🆕** — una fascia arancione in cima a ogni pagina quando un'auto è
  arrivata a Mate **da sola**, senza passare dalla procedura guidata: succede alla **seconda auto**
  aggiunta a un'installazione dove l'accesso era già fatto. Finché nessuno risponde per lei, quell'auto
  usa il **pacco batteria predefinito del suo modello**, e questo storce i suoi kWh, il suo prezzo al
  kWh e i suoi consumi. Il pulsante apre la procedura guidata, dove si scelgono pacco e PIN.

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
report, i comandi che quell'auto permette e le sue entità Home Assistant. La scelta resta. Sul telefono il selettore è dentro il menu ☰, sotto l'intestazione.

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
- una targhetta **"Cavo collegato (Non in ricarica / Carica completa)"** quando il cavo è inserito ma non si sta
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

**L'ultima ricarica dice tutte e due le cose 🆕** — il riquadro **Ultima ricarica** mostra in primo
piano la stessa cifra della card: a casa, con un contatore wallbox, i kWh **🔌 wallbox (da pagare)**,
e sotto quel che è arrivato nel pacco — *🔋 12,0 kWh in batteria (DC) · rendimento 81%*; altrove la
cifra della batteria, con i kWh della colonnina su una riga a parte dove li hai scritti. Il costo
sotto è il costo del numero sopra. Prima mostrava la sola cifra della batteria, accanto a un costo
calcolato sull'altra.

**L'autonomia al tuo limite di ricarica, e al 100% 🆕** — sotto l'autonomia stimata Mate mostra quanto
farebbe l'auto **al limite a cui la carichi davvero** (per dire, l'80%), con accanto il valore al
100%. Se l'auto non dichiara un limite sotto il 100 la riga è una sola, così lo stesso numero non
viene mai stampato due volte.

**La temperatura esterna, dal meteo 🆕** — il cloud Leapmotor manda la temperatura dell'abitacolo ma
mai quella dell'aria fuori, e nemmeno l'app ufficiale ce l'ha. Con l'interruttore acceso, mentre
l'auto è sveglia Mate interroga [Open-Meteo](https://open-meteo.com) sulla sua posizione — al massimo
una volta ogni 20 minuti o 10 km, quello che arriva prima — e mostra il valore accanto a quello
dell'abitacolo. È **spento di default**, perché la richiesta manda la posizione dell'auto a
Open-Meteo: l'unico interruttore sta in *Impostazioni → valori predefiniti dei viaggi*. Lo stesso
dato diventa un'entità **Temperatura esterna** in Home Assistant e dà a ogni viaggio le letture
lungo il percorso, da cui vengono la sua temperatura più alta e più bassa.

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
  efficienza per tirare fuori un insieme da tutta la cronologia. L'intestazione di un giorno aperto dice
  quanta batteria hanno usato i suoi viaggi: *84,4% → 51,6% (−32,8%)* oppure, se l'auto ha caricato tra un viaggio e
  l'altro, *−45,3%* usato e *⚡ +40,2%* caricato,
  e quanto si è guidato quel giorno. Sul telefono anche la riga di ogni viaggio mostra la batteria,
  *84,4→51,6% (−32,8%)*, sotto la durata.
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
- **Un'interruzione non lascia mai aperto un viaggio.** Se Mate perde il cloud durante la guida e
  l'auto è ancora in marcia quando il collegamento torna entro mezz'ora, il viaggio semplicemente
  continua. Se nel frattempo l'auto è parcheggiata o in carica, il viaggio finisce lì, e i
  chilometri fatti durante l'interruzione ne fanno parte. Dopo un silenzio più lungo, il viaggio
  finisce all'ultima notizia dell'auto prima di esso, e i chilometri successivi sono trattati come
  tutti quelli fatti senza collegamento.
- **Un viaggio finisce quando finisce l'auto 🆕.** Un viaggio si chiude sulla lettura che mostra
  l'auto **spenta**, e da quella lettura prende la sua fine: ora, livello di carica,
  contachilometri, posizione e carburante. Quindi **aspettare in P con l'auto accesa è una sosta
  dentro il viaggio**, non la sua fine — andare a prendere una persona e tornare è un viaggio solo,
  non due, e il consumo ufficiale che il cloud misura da accensione a spegnimento appartiene tutto a
  quel viaggio, senza metà da unire. È la prima lettura che ha *visto* l'auto spenta: dopo
  un'interruzione del collegamento arriva quindi più tardi dello spegnimento stesso. Un'auto che non
  dice se è accesa si chiude dopo circa un minuto in P, come prima, e lo stesso vale quando la
  lettura in P è un frame che il cloud ripete da mezz'ora. La fine resta all'ultima lettura quando
  alla lettura dello spegnimento manca un dato o una successiva mostra un altro chilometraggio. Un
  viaggio che Mate trova ancora aperto al riavvio, con l'auto già parcheggiata, viene chiuso sul suo
  ultimo punto registrato; uno fermo in P per mezza giornata su un'auto che continua a dirsi accesa
  viene chiuso dove si è fermata.
- **I chilometri che Mate non ha visto non vengono aggiunti ai viaggi vicini.** Quando il
  collegamento col cloud si interrompe più a lungo di una breve interruzione dentro lo stesso
  viaggio (vedi sopra), l'auto continua a girare ma Mate non lo vede; al ritorno trova solo un
  contachilometri più avanti. Quel salto può contenere la fine di una guidata, una sosta e l'inizio
  di un'altra, e **non c'è modo di sapere come si divide**. Se l'auto risulta parcheggiata, il
  livello di carica non è salito e in quell'intervallo non è stata rilevata alcuna ricarica, Mate
  ricostruisce un viaggio dal solo salto, senza percorso. Altrimenti (un nuovo viaggio già in
  corso, una ricarica o un livello di carica salito) Mate non attribuisce quei chilometri a
  nessuno. Sopra il calendario compare una riga con i chilometri, la carica e il costo di quel
  mese, e sulla pagina **Statistiche** il totale di sempre: *misurati, ma non attribuibili a un
  viaggio preciso — perciò esclusi da distanze, consumi e costi.*
  ⚠️ Per questo il totale di Mate può restare sotto al contachilometri dell'auto: la differenza è
  esattamente quella riga.
- **Altimetria e temperatura esterna.** Il cloud Leapmotor non riporta né l'una né l'altra, quindi
  qualche minuto dopo la fine di un viaggio Mate cerca il suo tracciato GPS su
  [Open-Meteo](https://open-meteo.com) (gratuito, senza chiave e senza account). Nel dettaglio
  compaiono la **linea dell'altitudine nel grafico Dati del viaggio**, i metri **saliti e scesi**
  (riga *Dislivello + / −*; la sua ⓘ dice come sono calcolati), e la temperatura **più alta e più
  bassa** del viaggio — non una media, così una salita da fondovalle a passo mostra il calo vero.
  Insieme spiegano buona parte del consumo di una guidata: la salita costa energia, il freddo costa
  autonomia. I viaggi registrati prima che esistesse hanno un pulsante **Calcola altimetria**, e
  tutto si può spegnere dalle Impostazioni.
- **In movimento e fermo 🆕.** Sotto la durata, il dettaglio la divide nel tempo in movimento e nel
  tempo da fermi durante il viaggio (semafori, code), dalle letture di Mate prese a pochi secondi
  l'una dall'altra. Una sosta tra viaggi uniti non conta per nessuno dei due, e un buco nelle
  letture compare come *senza dati* invece di finire in uno dei due.
- **Velocità mediana 🆕.** Sotto la velocità media, il dettaglio dà la mediana delle stesse letture
  in movimento, cioè la velocità sotto la quale è rimasta metà di esse. Un breve tratto veloce alza
  la media di un giro in città, mentre la mediana ne conserva l'andatura abituale.
- **Velocità massima dall'auto 🆕.** Quando il record del viaggio nel cloud dell'auto viene abbinato
  al viaggio (lo stesso record che dà i consumi ufficiali), il dettaglio mostra la velocità massima
  misurata dall'auto stessa. Le letture di Mate sono a qualche secondo l'una dall'altra e perdono i
  picchi brevi — su una B10 fino a 21 km/h — quindi un viaggio senza quel record mantiene il valore
  campionato, segnato con una ⓘ.
- **Potenza max e regen max 🆕.** Il dettaglio indica la potenza più alta erogata dalla batteria e
  quella più alta rientrata in frenata, dalla tensione e dalla corrente della batteria che Mate
  legge a ogni aggiornamento. Le letture sono a qualche secondo l'una dall'altra, quindi un picco
  breve tra due letture sfugge: i valori sono un minimo, e la ⓘ accanto lo dice. Non compaiono su
  un'auto con range extender, come il regen.
- **Temperatura della batteria 🆕.** L'auto riporta una sola temperatura della batteria — quella
  della cella più fredda, in gradi interi — e il dettaglio ne indica i valori durante il viaggio
  come un unico intervallo, dal più basso al più alto, per esempio 19 – 22 °C; la ⓘ accanto alla riga
  dice che è la cella più fredda. D'inverno l'intervallo mostra quanto era fredda la batteria e
  quanto il viaggio l'ha scaldata.
- **Grafico Dati del viaggio 🆕.** Il grafico sotto la mappa si chiama *Dati del viaggio* ed è diviso
  in fasce con un unico asse del tempo, un'unica linea del cursore e un unico riquadro al passaggio
  del mouse, con le linee raggruppate per fascia: **guida** (velocità e potenza della batteria —
  sopra lo zero erogata, sotto lo zero rientrata), **batteria** (SoC e autonomia stimata dall'auto)
  e **altitudine con la temperatura della batteria** (quella della cella più fredda). Una fascia ha
  al massimo due scale, una per lato, ognuna con l'unità in alto e i numeri nel colore della sua
  linea. Ogni voce della legenda accende e spegne la sua linea — un quadratino vuoto indica una
  linea spenta — e una fascia con tutte le linee spente si chiude. Tutte le linee partono accese; la
  scelta viene ricordata nel browser per tutti i viaggi. Il riquadro si apre con l'ora del giorno,
  al secondo, e il minuto del viaggio.
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
    avere il consumo reale combinato. Mate lo dice solo quando è l'auto a segnalarlo: una sosta in P
    di oltre un minuto durante la quale l'auto non ha detto se era accesa non conta come un'unica
    sessione.
- **La tua nota + tag di guida 🆕** (#107) — nel dettaglio di un viaggio puoi scrivere una **nota libera**
  (traffico, meteo, tipo di strada, qualsiasi appunto) e indicare la **modalità di guida** (Comfort /
  Normale / Sport) e il **One-Pedal** (attivo/disattivo) usati. Mate non può leggerli dall'auto —
  Leapmotor non li manda al cloud — quindi li imposti a mano; aiutano a spiegare perché due guidate
  simili hanno consumato in modo diverso.

- **Un periodo cercato si somma da solo 🆕** — i filtri per data hanno sempre saputo selezionare
  qualunque finestra, ma i risultati elencavano le schede senza totalizzare niente: un periodo di
  fatturazione che non è un mese solare andava sommato a mano. Sopra i risultati ora compaiono
  **viaggi, km e costo** di quel periodo — gli stessi numeri, dalla stessa fonte, della striscia del
  mese sul calendario.

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
  **Veloce/FAST** (DC), **HPC** (ricarica ultraveloce) e **Gratis**; in fondo al menu, **✎ Manuale**
  per il totale pagato (vedi sotto). Una ricarica che nessuno ha ancora confermato resta
  **❓ Da confermare** finché non scegli tu.
- **Casa non vuol dire wallbox.** *Casa* è **dove** hai caricato, non da cosa: anche una presa
  normale in garage è una ricarica di casa. La differenza conta per il conteggio: se hai collegato
  il contatore di una wallbox (vedi *Wallbox* più sotto), la ricarica si fattura sull'**energia
  erogata dal contatore**; se non l'hai collegato, si fattura sull'**energia arrivata in batteria**,
  esattamente come una ricarica pubblica. Fra le due c'è la perdita in calore del caricabatterie,
  tipicamente il 10-15%.
- **✎ Manuale — il totale pagato** — per le colonnine pubbliche con tariffe complicate
  (abbonamenti, costi di sessione…) **scrivi a mano il totale realmente pagato**: apri il menu del
  tipo, scrivilo nella riga **✎ Manuale** in fondo e premi **OK** (la **✎** accanto al tipo è lo
  stesso campo). Scavalca la stima automatica e **non tocca il tipo della ricarica**: una ricarica
  senza tipo diventa **✎ Manuale** e non è più da confermare, una con il suo tipo lo tiene. Il
  costo sulla scheda porta la scritta **fatturato** al posto di **stima**, e *Ripristina*, nella ✎,
  rimette il valore calcolato. Le ricariche che avevi scritto così prima della v3.16.0 si leggono di
  nuovo **✎ Manuale**, con il loro prezzo: non c'è niente da fare.
- **Casa vs Pubblica 🆕** — accanto alla card *Distribuzione AC vs DC* ce n'è una seconda:
  **Casa**, **Pubblica**, **✎ Manuale** e **Da confermare**, con una ciambella e una mattonella
  ciascuna (le ultime due solo se ce ne sono). Fanno sempre il numero di ricariche scritto sopra,
  così una ricarica col prezzo scritto a mano non finisce fra le pubbliche, e una che aspetta il suo
  tipo si vede come tale.
- **Una ricarica abbandonata dal cloud finisce quando passava corrente l'ultima volta 🆕** (#289) —
  se l'auto si addormenta col cavo attaccato, il cloud non lo dice: continua a ripetere l'ultima
  notizia che ha, e lì dentro il cavo risulta ancora collegato. Prima la ricarica restava aperta
  fino al risveglio dell'auto — quattro ore contate come diciannove, e nell'elenco non si vedeva
  niente finché non si chiudeva. Adesso, dopo mezz'ora senza una notizia nuova, Mate la chiude da
  sola e la data all'**ultima lettura con corrente vera**: la durata non contiene più la notte di
  silenzio. La pausa di una wallbox non è toccata — lì l'auto è sveglia e le notizie arrivano.
- **I kWh della colonnina 🆕** (#222) — su una colonnina pubblica Mate **non ha un contatore**: legge
  solo quanto è entrato in batteria, mentre la colonnina ti fattura quanto è uscito dal suo. Puoi
  scrivere tu quel numero: sulla scheda della ricarica, sotto le tre mattonelle, c'è una **✎**; il
  riquadro **si apre solo se lo apri tu** e la casella **è sempre vuota** — così un clic di troppo
  non cambia niente, e premere OK a vuoto lascia tutto com'era. *Rimuovi* toglie un valore
  sbagliato. Da lì in poi quel numero **prezza la ricarica**, esattamente come fa il contatore della
  wallbox a casa, e ti mostra l'**efficienza** (quanto ne ha trasformato in calore il caricabatterie
  di bordo). L'energia che Mate riporta resta quella **misurata in batteria**. Su una **ricarica
  unita** la cifra che scrivi copre i pezzi per cui l'hai scritta — una sessione unita dopo conta per
  conto suo — e quando i pezzi si fatturano su cifre diverse (il contatore ha preso un pezzo e non
  l'altro, oppure hai scritto la cifra su un pezzo prima di unire) la card e la Panoramica mostrano
  la somma, sotto la parola *erogati*, e il €/kWh divide per quella 🆕. Rendimento e perdita accanto
  alle cifre scritte a mano compaiono solo se quelle cifre coprono ogni pezzo della ricarica unita.
  Un contatore che ha preso solo una parte ti lascia comunque vedere e correggere il solare inserito.
- **Cosa entra nel conto e cosa no 🆕** — una ricarica entra in questi confronti solo se ha
  **tutti e due** i numeri, quello del contatore e quello della batteria. Una con uno solo dei due
  spingerebbe il rapporto sopra il 100 %, cosa che nessuna colonnina può fare. **Le ricariche in
  corso restano fuori**: una sessione che sta ancora arrivando non ha un totale da confrontare, e
  entra nei conti quando finisce.
- **Il mese dice tutte e due le cose 🆕** — sopra il calendario: *«154,93 kWh erogati · 142,57 in
  batteria»*. Il primo è ciò che è uscito dai contatori (wallbox, o i kWh che hai scritto tu); il
  secondo è ciò che è arrivato nel pacco. Fra i due c'è la perdita di conversione, che paghi.
  **Energia totale** sulla pagina Ricariche ed **Energia caricata** su Statistiche sono la stessa
  cifra *erogata*, con *in batteria* sotto quando le due differiscono. Una regola sola per tutti i
  totali: il contatore della wallbox dove c'è, i kWh della colonnina dove li hai scritti, altrimenti
  la cifra della batteria.
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

- **Un periodo cercato si somma da solo 🆕** — sopra i risultati compaiono **sessioni, kWh erogati
  (col dato in batteria accanto) e costo** di quella finestra. L'energia fatturata dal 22 al 21, o
  qualunque altro periodo che non sia un mese solare, non va più sommata a mano.

- **Grafico Dati della ricarica 🆕** — sotto ogni ricarica, *📈 Dati della ricarica* apre un
  grafico a fasce sull'asse del tempo della sessione, come quello di un viaggio: **ricarica** (la
  potenza DC dell'auto, accanto la potenza AC della wallbox in una ricarica a casa con wallbox
  associata, e quanti minuti mancavano secondo l'auto), **batteria** (SoC) e
  **temperature** (quella della cella più fredda e, se la temperatura esterna è attiva
  nelle Impostazioni, l'aria esterna dove si trova l'auto). Ogni voce della legenda accende e spegne
  la sua linea, una fascia con tutte le linee spente si ripiega, il browser ricorda la scelta e il
  riquadro al passaggio del mouse si apre con l'ora e il tempo dalla prima lettura. Il confronto
  AC-DC della pagina Wallbox è questo stesso grafico.
  Mentre una ricarica è in corso, lo stesso grafico compare in diretta tra le schede in cima alla
  pagina, con la linea della wallbox accanto a quella dell'auto in una ricarica a casa: dice quando
  è iniziata e da quale percentuale, e cresce a ogni interrogazione.

### Prezzi di ricarica
**(menu: Prezzi di ricarica)** — Qui imposti **quanto paghi l'energia**, così Mate può calcolare i
costi. Puoi definire un prezzo **per ciascun tipo** di ricarica (Casa, AC, Veloce, HPC) e scegliere
tra:

- **Tariffa fissa** (un solo €/kWh);
- **Fasce orarie (TOU)** — prezzi diversi per giorno della settimana e fascia oraria (es. F1/F2/F3,
  notte più economica).
- **Dinamico (sensore Home Assistant) 🆕** — Mate legge il prezzo da un'entità che **cambia nel
  tempo** (Nordpool, Tibber, l'integrazione del tuo fornitore) e lo pesa sulla curva di potenza
  della sessione: una ricarica a cavallo di un cambio di prezzo viene addebitata per quello che
  ogni sua parte è costata davvero.
- **kWh personalizzati (Home Assistant) 🆕** — serve quando il prezzo è fisso ma **quanta parte della
  ricarica hai pagato** non lo è. Col fotovoltaico solo una parte della sessione viene dalla rete, e
  quella divisione non la conosce né l'auto né il cloud: la conosce un helper di Home Assistant.
  Scegli l'entità che contiene i kWh da addebitare; a fine ricarica Mate la legge e la moltiplica
  per il tuo prezzo fisso. **L'energia che Mate dichiara per la ricarica non cambia** — resta quella
  arrivata in batteria; dal tuo numero si ricava solo il costo. Se l'entità manca o non risponde, la
  ricarica torna al prezzo fisso sui kWh misurati.

- **kWh solari (manuali) 🆕** — lo stesso caso di sopra, senza Home Assistant. Scegli questa se hai
  il fotovoltaico e preferisci scrivere tu, ricarica per ricarica, quanti kWh sono venuti dal tuo
  impianto: Mate li sottrae da quelli misurati dalla wallbox e ti addebita solo il resto. Sulla
  ricarica compare un campo **☀️ Solare**, sotto i tre riquadri, e la riga accanto scrive il conto
  per esteso — «20,0 erogati − 8,0 solari = 12,0 pagati» — così un numero scritto al contrario si
  vede subito. Un valore più alto di quanto la wallbox ha misurato viene rifiutato. Il campo compare
  solo sulle ricariche di casa che la wallbox ha davvero misurato: senza quella misura non c'è
  niente da cui sottrarre, e una riga te lo dice. **L'energia che Mate dichiara non cambia** — resta
  quella misurata; dal tuo numero si ricava solo il costo.

> Le ultime tre valgono solo per le ricariche di **Casa**: una sessione pubblica la fattura il suo
> gestore, e un helper tuo non ha titolo per darle un prezzo.

Il prezzo di **Casa** è quello che alimenta i costi delle ricariche domestiche e, a cascata, il
costo dei viaggi (calcolato sul prezzo "medio" dell'energia in batteria al momento del viaggio).

> Le modifiche ai prezzi valgono **solo per le ricariche future**: i costi già calcolati non
> cambiano. Con le fasce orarie puoi anche scegliere *come* ripartire una sessione tra le fasce —
> *Split accurato* (sulla curva di potenza reale) oppure *Ora di inizio* (tutta la sessione alla
> fascia in cui è partita).

> **Niente tetto sul prezzo 🆕** — i campi rifiutavano qualunque valore sopra `9,99`, un limite che
> andava bene solo per tariffe in euro o dollari. Islanda, Giappone, Corea e Ungheria prezzano
> l'energia in decine o centinaia di unità per kWh: scrivi il numero com'è. La **corona islandese**
> è nell'elenco delle valute, e ogni importo mostra ora **almeno due decimali**, così niente viene
> arrotondato a schermo.

### Statistiche
**(menu: Statistiche)** — Le tue medie e i totali nel tempo: **distanza dei viaggi registrati** 🆕
(si chiamava *distanza totale*, ma è sempre stata la somma dei viaggi conclusi — non il
contachilometri dell'auto) e numero di viaggi,
**distanza media per viaggio**, **tempo di guida**, **consumo medio** (pesato sulla distanza) e
**migliore**, **energia usata e ricaricata** (l'energia caricata è quella che le colonnine hanno
**erogato**, con sotto la cifra **in batteria** — la stessa coppia della pagina Ricariche 🆕), **recupero** totale e medio, numero di **sessioni di
ricarica**, con le relative **tendenze** (efficienza e recupero nel tempo). Tra i totali c'è anche una
scheda **Totale V2L** con l'energia cumulativa prelevata via V2L in tutto lo storico. C'è anche la
**ricerca consumo per intervallo** (date libere + preset) e la scheda **"Cumulativo Totale del
Veicolo"** 🆕 — energia totale, chilometraggio e media kWh/100 km **da consegna** (dal contatore
dell'auto, quindi non intaccata dai rari viaggi che il cloud non registra), con una barra
**Guida / A·C / Altro / Da-fermo**.

**Consumo contro temperatura esterna 🆕** — un punto per ogni viaggio concluso: il suo consumo contro
la temperatura dell'aria in cui è stato guidato, con una linea di tendenza tratteggiata a
attraversarli. Risponde alla domanda che si fa ogni proprietario quando arriva il freddo — *quanto
beve davvero la MIA auto a 5 °C?* — partendo dalla tua guida invece che da una tabella. Serve che i
viaggi portino una temperatura esterna (vedi *Panoramica*); su dati realistici il disegno si legge
dopo circa un mese di guida.

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

### Eventi
**(menu: Eventi)** — Cosa ha fatto l'auto, momento per momento: sbloccata e di nuovo bloccata, una porta o
il portellone aperti e chiusi, il cavo dentro e fuori, il clima acceso e spento, READY acceso e spento, dove si è fermata la tendina, ogni
viaggio e ricarica dall'inizio alla fine, e ogni comando inviato da Mate. L'elenco si apre sugli ultimi tre
giorni, dal più recente, in un'unica scheda con un'intestazione per giorno e una linea sottile per ora; il
punto di una riga ha il colore del bottone del suo gruppo. I pulsanti sopra i gruppi vanno più indietro — 3,
7 o 30 giorni, 3, 6 o 12 mesi, o Tutto — contando da oggi; le date inserite sotto ⚙ prevalgono su di loro.
Un intervallo lungo arriva a blocchi di mille righe, e il successivo si carica quando la fine dell'elenco
entra nello schermo.

- **Un inizio e una fine sono due righe, unite da una linea.** A sinistra degli orari, una linea del
  colore del gruppo unisce il punto di una fine a quello del suo inizio, come in un grafo della cronologia
  di git, così ciò che è durato insieme, e per quanto, si vede a colpo d'occhio. La fine dice quanto è
  durato lo stato — «Portellone chiuso · dopo 35s» — e un clic sulla linea o su un punto accende la coppia e
  le sue due righe senza scorrere l'elenco. Se l'inizio è prima dei giorni mostrati, la linea esce
  sbiadita dal fondo dell'elenco e la fine lo indica: «dal 02 ott 2026 alle 14:20:05». Uno stato ancora in
  corso porta la sua linea fino in cima e dice **(in corso)** solo se l'ultimo dato dell'auto è
  fresco; mentre il cloud ripete un dato vecchio (auto in sospensione, o senza copertura) la riga indica
  invece l'ora di quel dato.
- **La tendina è una riga dove si è fermata** — «Tendina aperta al 50%», «Tendina chiusa» — senza linea e
  senza «dopo»: resta aperta per giorni, e una linea attraverserebbe soltanto tutta la pagina. Un valore
  visto in un solo dato non compare: la tendina di passaggio, o una sosta più breve dell'intervallo tra due dati.
- **Gli orari sono quelli dell'auto, al secondo**: l'ora del primo dato che ha mostrato il nuovo stato,
  confermato dal successivo. Col puntatore su un orario lo si vede accanto all'ora in cui Mate ha
  registrato la riga. Viaggi, ricariche e comandi hanno solo l'orologio di Mate, quindi accanto a un
  segnale degli stessi secondi il loro ordine può differire di quei secondi.
- **Una fine risponde alla sua domanda.** READY spento: quanta strada ha fatto l'auto e la carica prima e
  dopo. Clima acceso: in sosta o durante un viaggio, la temperatura impostata e quella esterna; Clima
  spento: l'abitacolo prima e dopo. Cavo scollegato: l'energia caricata e, se la prima ricarica è partita
  più di cinque minuti dopo l'inserimento del cavo (una wallbox che aspetta la sua programmazione), quanto
  ha atteso. La fine di un viaggio o di una ricarica porta i numeri di Viaggi e Ricariche.
  Questi numeri risaltano sul resto della riga; il costo è verde e il tempo prima che partisse la ricarica
  è ambra.
- **Luoghi**: una riga in uno dei tuoi luoghi di ricarica (*Prezzi di ricarica → Luoghi di ricarica*) lo
  nomina.
- **La mappa** resta nascosta finché **🗺 Mostra mappa**, sopra l'elenco, non la apre (accanto all'elenco
  su uno schermo largo, sopra sul telefono o su uno più stretto), e la volta dopo è come l'hai lasciata.
  Ogni riga con una posizione ha un 🌍: apre la mappa se serve, accende il punto della riga, lo porta in
  vista e accende la riga e l'altra metà della sua coppia. Un clic su un punto accende lui e le sue righe
  e scorre l'elenco fino alla sua riga più recente; la riga sotto il puntatore accende il suo punto finché
  il puntatore resta lì. Un punto rappresenta un posto di circa 110 m, o un intero luogo di ricarica. La
  riga di un viaggio o di una ricarica lo apre, e **← Eventi** lì riporta all'elenco com'era.
- **Due dati fanno un evento.** Il cloud manda lampeggi di un solo dato — una portiera «aperta» per una
  sola lettura — quindi un cambiamento conta solo quando due letture consecutive lo confermano. Un
  cambiamento che l'auto ha annullato fra due sue trasmissioni non si vede mai, e il cloud può perdere il
  segnale della chiusura per una o due letture, che allora appaiono come un breve sblocco.
- **Filtri**: una parola (il nome dell'evento, l'esito di un comando, un luogo, la nota di un
  viaggio o di una ricarica — la nota automatica di un viaggio contiene i suoi indirizzi), i bottoni dei gruppi
  (Sicurezza, Porte, Finestrini, Ricarica, Clima, Guida, Comandi) e, sotto ⚙, un intervallo di date e i
  singoli tipi. I filtri vivono nell'indirizzo: un link o un ricaricamento li conserva.
- **Storico**: gli eventi derivano dalle posizioni che Mate già salva, quindi su un'installazione esistente
  il primo avvio rilegge tutto lo storico, una fetta per lettura, e finché non ha finito la pagina dice a che
  punto è. Gli eventi restano quanto le posizioni (*Impostazioni → Database*).

### Report
**(menu: Report)** — Una sintesi **mese per mese**: quanto hai guidato, quanta energia hai
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
- aprire o chiudere la **tendina** del tetto panoramico: il riquadro dice quanto è aperta e, se si è
  fermata a metà, offre **Apri** (del tutto) e **Chiudi**, le sole due che l'auto esegue;
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
Raffreddamento / Riscaldamento / Ventilazione). Il riquadro del tetto panoramico dice quanto è aperta la
tendina — «40%» sopra «Aperto» —, come fanno quelli dei finestrini.

### Wallbox
**(menu: Wallbox)** — Se hai collegato una wallbox (vedi
[Integrazioni](#8-le-integrazioni-in-dettaglio)), qui vedi i suoi dati **dal vivo** (potenza,
energia), il **riepilogo** e l'elenco delle **sessioni**, ed eventualmente i **controlli** (es.
corrente massima) se la tua wallbox li espone tramite Home Assistant.

Quando la tua auto **non è collegata**, la scheda lo dice per nome — *«C10 non collegata»* — perché
alla wallbox può essere attaccata l'auto di un altro, e quei valori dal vivo non sarebbero i tuoi. Il
riquadro del costo si chiama **Ultima ricarica di casa**: una ricarica riceve un prezzo solo quando
finisce, quindi quel numero non è mai la sessione in corso.

> In Mate «casa» vuol dire **wallbox o presa domestica**: una ricarica può portare quel contrassegno
> senza che la tua wallbox c'entri niente.

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

- **Ricarico sempre a casa 🆕** — senza wallbox e senza Home Assistant non c'è niente che dica a Mate
  dove è avvenuta una ricarica, quindi ogni sessione nasce senza tipo e va etichettata a mano: tanti
  clic identici per chi ricarica solo a casa, magari con più rabbocchi brevi al giorno. Con questo
  acceso una ricarica nuova nasce **Casa**, e resta modificabile per la rara volta in pubblico. Vale
  **solo in avanti** per il **tipo** — le ricariche di prima che tu lo accendessi restano senza tipo,
  esattamente come sono — e accenderlo chiede una conferma esplicita, così non può succedere per
  sbaglio.
- **E prezzata, non solo etichettata 🆕** — una ricarica nata **Casa** arrivava col distintivo verde e
  senza costo, perché il motore dei prezzi girava solo su una *conferma*, a mano o dalla wallbox.
  Nascendo già confermata non passava da nessuna delle due. Adesso è prezzata esattamente come se ne
  premessi tu il distintivo — le fasce orarie leggono l'ora della ricarica, non quella di adesso — e
  anche quelle che stanno lì da prima senza prezzo vengono riempite. Un costo che hai scritto tu non
  viene mai riscritto, e una ricarica che hai segnato gratis resta gratis.

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
  salvata. ⚠️ La *scavalca*, non la sostituisce: l'impronta dimenticata resta nel database sotto,
  quindi una volta rientrato imposta una password nuova (o toglila) da **Impostazioni → Accesso** e
  solo dopo rimuovi la variabile — se la togli prima, torna a comandare quella che avevi perso.

- **Database** — dimensione del DB e **conservazione posizioni** (retention): puoi tenere i punti
  GPS "per sempre" (predefinito) o cancellare quelli più vecchi di 6/12/18/24 mesi per risparmiare
  spazio. *Vengono potate solo le posizioni*: viaggi (con il percorso e le letture lungo la strada),
  ricariche e curve di ricarica restano.
  I punti di un viaggio ancora in corso restano finché non finisce, perché la sua fine si ricava da
  essi.
- **Esporta / backup** — scarica **viaggi (CSV)**, **ricariche (CSV)** e un **backup del database**.
  Il backup arriva **compresso in gzip** (`leapmotor_mate.db.gz`) 🆕, mandato a pezzi così nemmeno un
  database grande deve stare tutto in memoria. Il ripristino accetta **sia** il file compresso **sia**
  un `.db` salvato prima di questa modifica, quindi niente di quello che hai già smette di
  funzionare — e un file più piccolo è più comodo da tenere o da sincronizzare dove fai i backup.
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

Di recente sono arrivate altre due entità 🆕: **Potenza clima**, i watt che il climatizzatore sta
assorbendo (così un'automazione vede l'abitacolo che viene riscaldato o raffreddato), e **Temperatura
esterna**, quella dell'aria dal meteo — la seconda solo con quell'interruttore acceso (vedi
*Panoramica*).

E un'altra ancora 🆕: **Avviso aggiornamento OTA**, accesa quando nella casella dei messaggi del tuo
account Leapmotor c'è un avviso di aggiornamento software, con titolo e data del messaggio come
attributi — quanto basta a un'automazione per avvisarti. Va letta per quello che è: la casella è
dell'**account**, quindi con due auto lo stesso avviso compare su entrambe, e dice che è arrivato un
messaggio, non che la tua auto ha un aggiornamento in attesa. Leapmotor non pubblica uno stato
dell'aggiornamento, quindi non c'è nessun numero di versione da mostrare.

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
che si è perso mentre era fermo. A questi due se ne aggiunge un terzo: se una ricarica resta aperta più di dieci minuti **senza che il contatore venga letto** — Home Assistant spento, Mate riavviato a metà ricarica — il totale non è una misura di quella ricarica e succede la stessa cosa. (Il contatore continua a essere letto mentre il cloud **dell'auto** non risponde: sta in casa tua, non dietro di lui.) E un rendimento sopra il 100 % è impossibile, quindi non viene mai mostrato.
Se una ricarica pubblica ha una tariffa complicata, scrivi il totale pagato in **✎ Manuale**, in
fondo al menu del suo tipo.

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
È supportata dalla **4.7.0**, sulla build normale: la pagina REEV, la benzina per viaggio e per
periodo, e i pacchi batteria REEV nella procedura guidata. Per queste cose la build BetaTester non
serve più.
I litri sono quelli dell'auto, presi dallo storico per viaggio di Leapmotor — lo stesso numero che
vedi nell'app ufficiale. Dove il cloud non ha il record di una guidata, Mate li ricava dal serbatoio,
e ogni cifra dice quale delle due è a schermo. La finestra del cloud è di circa 28 giorni, quindi su
uno storico lungo i viaggi più vecchi leggono la risposta del serbatoio, che misura circa il 20% in
meno.
Un viaggio che non ha bruciato niente legge `0 L` con accanto *tutto elettrico*, che non è la stessa
cosa di un viaggio di cui non si è potuto leggere il serbatoio: quello resta bianco.
Su una REEV non viene mostrato il **recupero in frenata**, perché un generatore che ricarica il pacco
mentre guidi non si distingue da una frenata.
Stai già usando la build BetaTester? Non sei obbligato a spostarti, continua a funzionare. Se vuoi
farlo è un backup e un ripristino, in quest'ordine: vedi
[Dalla build BetaTester a quella ufficiale](BETA-TO-OFFICIAL.md#italiano).

**Mate non riceve dati, e ho un firewall (Synology o altro).**
Mate non ha mai bisogno di essere raggiungibile da fuori. L'unica regola **in entrata** che gli serve
è la porta TCP **4001** dalla tua rete, per poter aprire la pagina. Tutto il resto è **in uscita**:
DNS sulla porta 53, poi HTTPS sulla 443 verso `app-gw-global-master.leapmotor-international.de` e
`appgateway.leapmotor-international.de` — lo stesso bilanciatore in AWS Francoforte, i cui indirizzi
ruotano: permetti i nomi o la regione, e non fissare mai gli indirizzi di oggi.
È per questo che aggiungere porte o paesi al profilo del firewall non cambia niente: quelle regole
descrivono le connessioni **in entrata**, e le risposte del cloud tornano su una connessione che
Mate ha aperto. A fermarlo è il «nega tutto» finale del profilo, applicato al traffico che esce dal
bridge di Docker.
Su un Synology, quello che ha funzionato
([#384](https://github.com/ProtossBlaster/leapmotor-mate/issues/384)): leggi la rete del container —
il suo gateway e il suo indirizzo, per esempio `172.28.0.1` e `172.28.0.2` — poi crea una regola che
permette **tutte le porte** a quell'intero intervallo (da `172.28.0.1` a `172.28.255.254`) e spostala
**in cima**, sopra le altre. Il resto del profilo, «nega tutto» compreso, può restare com'è.
Dalla **4.7.18** una connessione fallita finisce con una parola, nel log e nella pagina di
configurazione — `dns_failure`, `connection_refused`, `timeout`, `network_unreachable`… — che dice a
colpo d'occhio se la macchina non risolve il nome o non raggiunge l'indirizzo. Dalla **4.8.0** quella
parola non viene più nascosta dal messaggio di Mate «login temporarily deferred».

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
- **Casa / AC / Veloce (FAST) / HPC / Gratis** — i tipi di ricarica che Mate riconosce o che puoi
  assegnare; una ricarica senza tipo si legge **✎ Manuale** se ci hai scritto il prezzo,
  **❓ Da confermare** se no; "HPC" è la ricarica ad altissima potenza.
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
