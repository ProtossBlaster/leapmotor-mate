# Migrazione automatica a Mate 4

MATE-API è la libreria indipendente; Mate è il prodotto Docker, add-on Home Assistant
e Desktop che la usa. V3 indica la versione dei comandi cloud, non della libreria.

## Aggiornamento normale, nessuna procedura aggiuntiva

La migrazione viene eseguita all'avvio dopo il normale aggiornamento di Mate. Non
richiede importazione ZIP, inserimento di credenziali/PIN, cambio di add-on o
reinstallazione del Desktop. Si riutilizzano database, chiave, certificati,
configurazione, identità MQTT e modalità Beta/REEV dell'installazione.

Il software crea un backup SQLite coerente, verifica il nuovo client su una copia
privata ed esegue solo autenticazione e letture cloud. La verifica dura al massimo
15 secondi. I parametri comuni vengono dalla versione pubblica verificata del SDK,
con hash, provenienza e licenza inclusi. Non vengono distribuiti certificati privati,
token, credenziali o dati degli utenti.

Se la verifica riesce, vengono trasferite soltanto le impostazioni della nuova
sessione; lo storico e le altre impostazioni non vengono sostituiti. Entrambi i
processi usano la stessa decisione. Se la verifica fallisce, il normale Mate
aggiornato mantiene automaticamente il client precedente, comprese le correzioni
all'interfaccia e alla raccolta dati. Un errore di comando non provoca mai un
secondo invio tramite l'altro client.

## Compatibilità dei modelli

Il nuovo percorso dei comandi è qualificato per B10. Account con altri modelli,
compresi C10 REEV/T03, o account misti mantengono il client precedente finché i loro
contratti non sono qualificati. Questa protezione è automatica e non chiede azioni
all'utente. Un account trattenuto sul client precedente viene nuovamente verificato
al successivo rilascio, non durante una sessione in corso.

I comandi B10 sono stati provati fisicamente dall'operatore. Le prove automatiche
non estendono questa qualifica fisica ad altri modelli e non recuperano dati GPS
che il cloud o il vecchio client non hanno mai registrato.

## Distribuzione e recupero

Docker e gli add-on usano la stessa immagine; l'add-on esistente conserva slug e
volume dati. Il canale Beta conserva la modalità di ricerca. Il Desktop scarica il
payload attraverso il suo aggiornamento normale; il contratto è verificato anche
sui binari Desktop 1.0 già pubblicati, senza richiedere una nuova shell.

Per l'add-on Beta, il percorso senza azioni aggiuntive riguarda le installazioni
con versione numerica, a partire dalla baseline `3.19.2`. Le installazioni storiche
che mostrano ancora la versione letterale `beta` hanno un caso distinto: Supervisor
consente l'aggiornamento sullo stesso slug, ma la finestra di aggiornamento di HA
può bloccarlo per l'ordinamento delle versioni. Il passaggio senza azioni aggiuntive
non è ancora verificato su quel percorso UI; non implica che servano reinstallazione
o esportazione/ripristino del database.

Il backup precedente resta in `migration-backups/mate-4.0.0`. Le generazioni dei
certificati della sessione sono private e restano disponibili per i processi che
le usano. Il recupero ordinario consiste nella selezione automatica del client
precedente, senza ripristinare un vecchio database e perdere i dati più recenti.

La versione effettivamente distribuita è indicata nelle release e nell'add-on.
I test nativi non sostituiscono una prova su ogni hardware o su un Supervisor HA reale.
