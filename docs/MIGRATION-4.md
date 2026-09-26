# Migrazione a Mate 4.0.0-rc.1

Questa è una release candidata volontaria. MATE-API ha una versione indipendente;
V3 identifica i comandi cloud. I comandi sono qualificati per B10: altre vetture
non devono sostituire automaticamente una precedente installazione funzionante.

## Docker e Home Assistant

Usano la stessa immagine e lo stesso codice. Per Docker scegliere esplicitamente
il tag `4.0.0-rc.1` e un volume dati nominato. Per HA installare il candidato
separato solo dopo aver fermato l'istanza che usa lo stesso account. Le release
candidate non aggiornano `latest`, l'add-on stabile o quello beta esistente.

1. Fermare il vecchio Mate. Salvare l'intera directory dati e annotare immagine/tag.
2. Conservare database, `secret.key`, certificati applicativi e account. Le copie
   sono private: non caricarle su GitHub.
3. Al primo avvio il candidato crea un backup SQLite coerente e copia chiave e
   materiale locale in `migration-backups/mate-4.0.0`. Un errore interrompe la
   migrazione; la seconda esecuzione riusa il backup completo. Conservare anche
   il backup esterno del punto 1 per i dati dell'istanza precedente.
4. Materiale applicativo già completo: mantenuto e validato. Per un'installazione
   vuota fornire `MATE_APPLICATION_BUNDLE` (directory montata readonly) oppure
   caricare dalla configurazione un ZIP contenente esattamente `certs/app.crt`,
   `certs/app.key`, `api-v2-private/p12-parameters.json`. Nessun account, token o
   storico può essere incluso nel pacchetto. Non è scaricato automaticamente da
   repository terzi. Le release del sorgente non includono materiale privato.
5. Completare login, selezione veicolo e PIN. Verificare letture, un viaggio e una
   ricarica prima di considerare conclusa la migrazione operativa. La chiusura
   accettata dal cloud non equivale a conferma fisica.
6. Impostazioni → Storico viaggi cloud abilita l'importazione. Disattivarla mantiene
   i dati precedenti e la raccolta cloud utilizzata per selezionare i consumi.

## Desktop

Il codice `web/` e `poller/` comprende l'esatta copia della libreria indicata in
`poller/vendor/mate-api.json`, con hash e licenza. Non richiede un pip install
all'avvio. Utilizzare una shell compatibile e `--payload-tag v4.0.0-rc.1` per la
prova volontaria. Gli aggiornamenti automatici continuano a scegliere release stabili.
Il launcher mantiene il payload precedente per il ritorno; la cartella dati resta
quella dell'utente. Windows richiede la shell con moduli di lock e protezione ACL.

## Rollback

Fermare tutti i processi della versione candidata. Conservare una copia della
cartella attuale per non perdere quanto raccolto durante la prova. Ripristinare
insieme database, chiave e materiale dal backup precedente in una cartella separata;
avviare l'immagine/payload precedente su quella cartella. Non sovrascrivere solo
il database mantenendo una chiave diversa. Non avviare due istanze sullo stesso
account o volume. Verificare integrità SQLite, login e storico prima del ritorno.
I dati raccolti dopo il backup rimangono nella copia candidata e non sono fusi
automaticamente nel database precedente.

## Qualifica ancora esterna

Prove fisiche e sonno del veicolo, viaggio/ricarica reali, revoca lato emittente e
supporto ad altri modelli richiedono evidenze specifiche. Il build/test nativo non
certifica un'installazione su ogni computer o un HA Supervisor reale.
