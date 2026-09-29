# From the BetaTester build to the official one

**Who this is for:** owners of a Leapmotor with a range extender, who have been running the
**MateBetaTesterOnly** add-on (or the beta Docker image) because it was the only build that could
read their car. From **4.7.0** the range-extender pages are on the ordinary build.

**You do not have to move.** The BetaTester build stays exactly as it is and keeps working. This
page is for when you *want* to move, and it is a backup and a restore — nothing is converted and
nothing is migrated in place.

---

## What you gain, and what stays behind

**On the official build you get** the REEV page, the petrol per trip and per period, the fuel totals
on the day / month / period cards, the Overview and Statistics summaries, the efficiency-against-
temperature chart, and the REEV battery packs in the setup wizard.

**These stay on BetaTester only:** the research diary, the encrypted research pack and the consent
that opens them, the `?demo` preview, and the ⚡ electric rate of a generator drive
(`reev_elec_kwh_100km`) — the one figure that has never been checked against a real dashboard.

If you use the research diary or send encrypted packs, stay where you are.

---

## The move, step by step

> ⚠️ **The order matters.** Install and log in **first**, restore **afterwards**. The reason is
> below.

1. **In the BetaTester install** — *Settings → Export / Backup* → download the database. You get a
   `leapmotor_mate.db` (or `.db.gz`). Keep it somewhere safe.
2. **Install the official build.** The ordinary add-on from the repository, or the ordinary Docker
   image. Give it **its own data folder**: do not point it at the beta's.
3. **Run the setup wizard on it** — the certificate, then your Leapmotor account, password and
   operation PIN. Finish it, and check the car appears.
4. **Restore** — *Settings → Backup / Restore* → upload the file from step 1. Mate restarts by
   itself and reopens the restored database.
5. **Check it landed**: the number of trips, the charges, your prices and your places should be the
   ones you had in the beta.
6. **Stop the beta install** once you are satisfied. Leave both polling the same car and you have
   two sessions on one Leapmotor account, which they spend evicting each other.

> 💡 You can take the backup at any time and keep it. The restore is what replaces things, and it is
> the last step on purpose.

---

## Why the order matters

Your password, your PIN and your tokens are stored **encrypted**, and the key that seals them —
`secret.key` — is *not* in the backup, deliberately: a database file that carried its own key would
hand over your account to anyone who got hold of it.

So the secrets inside the beta's backup cannot be read by the new install. The restore handles this
for you: it **drops the backup's encrypted secrets and keeps the ones the new install already has**
— the login you just did in step 3. Everything else in the file is taken byte for byte.

That is why you log in first. If you restore before logging in there is simply nothing to keep, and
you log in afterwards; it works, it is just one more step.

---

## What crosses over, and what does not

Measured on a real installation, restoring a 150-trip backup onto an install with its own fresh
login:

| | |
|---|---|
| ✅ trips, positions, charges, refuels, places, prices, the logbook, the research signal log | every row, byte for byte |
| ✅ your settings — prices, tariffs, retention, MQTT, ABRP, language, units | from the backup |
| ✅ the login of the **new** install | kept, not overwritten |
| ❌ the secrets stored in the backup | dropped — they were sealed with the other install's key |
| ❌ `secret.key`, `certs/`, `api-v2-private/`, `api-v2-account-material/` | never in the database; the new install makes its own |
| ❌ the car picture (`car_picture.png`, `car_picture_pkg.zip`) | a cache beside the database; it is fetched again |

> ⚠️ **Anything you configured on the new install before restoring is replaced by the backup's
> version** — every setting except the login. Configure it afterwards, not before.

The restore refuses a file that is not a Mate database — it checks the SQLite signature and that the
`settings`, `vehicles` and `positions` tables are there — and it refuses **without touching** what
you already have.

---

## Going back

The beta install is untouched by all of this: its database, its key and its certificates are still
in its own data folder. If you want to go back, start it again.

Keep the backup file you downloaded in step 1 until you are sure. It is the only copy of the beta's
history that lives outside the beta.

---

## Italiano

# Dalla build BetaTester a quella ufficiale

**Per chi è questa pagina:** i proprietari di una Leapmotor con range extender che hanno usato
l'add-on **MateBetaTesterOnly** (o l'immagine Docker beta) perché era l'unica build capace di leggere
la loro auto. Dalla **4.7.0** le pagine del range extender stanno sulla build normale.

**Non sei obbligato a spostarti.** La build BetaTester resta esattamente com'è e continua a
funzionare. Questa pagina serve per quando *vuoi* spostarti, ed è un backup e un ripristino — non si
converte niente e non si migra niente sul posto.

---

## Cosa guadagni e cosa resta indietro

**Sulla build ufficiale hai** la pagina REEV, la benzina per viaggio e per periodo, i totali del
carburante sulle schede del giorno, del mese e del periodo, i riepiloghi in Panoramica e in
Statistiche, il grafico del consumo contro la temperatura, e i pacchi batteria REEV nella procedura
guidata.

**Restano solo su BetaTester:** il diario di ricerca, il pacchetto cifrato e il consenso che li apre,
l'anteprima `?demo`, e la riga ⚡ del consumo elettrico di una guidata col generatore
(`reev_elec_kwh_100km`) — l'unica cifra che non è mai stata confrontata con un cruscotto vero.

Se usi il diario di ricerca o mandi pacchetti cifrati, resta dove sei.

---

## Lo spostamento, passo per passo

> ⚠️ **L'ordine conta.** Prima installi e fai il login, **poi** ripristini. Il motivo è più sotto.

1. **Nell'installazione BetaTester** — *Impostazioni → Esporta / Backup* → scarica il database.
   Ottieni un `leapmotor_mate.db` (o `.db.gz`). Tienilo al sicuro.
2. **Installa la build ufficiale.** L'add-on normale dal repository, oppure l'immagine Docker
   normale. Dagli **una cartella dati sua**: non puntarla su quella della beta.
3. **Fai la procedura guidata** — il certificato, poi account Leapmotor, password e PIN operativo.
   Finiscila e controlla che l'auto compaia.
4. **Ripristina** — *Impostazioni → Backup / Ripristino* → carica il file del passo 1. Mate si riavvia
   da solo e riapre il database ripristinato.
5. **Controlla che sia arrivato**: numero di viaggi, ricariche, i tuoi prezzi e i tuoi luoghi devono
   essere quelli che avevi nella beta.
6. **Ferma l'installazione beta** quando sei soddisfatto. Se le lasci tutte e due a interrogare la
   stessa auto hai due sessioni su un solo account Leapmotor, e passano il tempo a buttarsi fuori a
   vicenda.

> 💡 Il backup puoi farlo quando vuoi e tenertelo. È il ripristino che sostituisce le cose, ed è
> l'ultimo passo apposta.

---

## Perché l'ordine conta

La tua password, il tuo PIN e i tuoi token sono salvati **cifrati**, e la chiave che li sigilla —
`secret.key` — nel backup **non c'è**, di proposito: un file di database che si portasse dietro la
propria chiave consegnerebbe il tuo account a chiunque lo trovasse.

Quindi i segreti dentro il backup della beta la nuova installazione non riesce a leggerli. Il
ripristino se ne occupa da sé: **butta via i segreti cifrati del backup e tiene quelli che la nuova
installazione ha già** — il login che hai appena fatto al passo 3. Tutto il resto del file viene
preso byte per byte.

Ecco perché prima si fa il login. Se ripristini prima di averlo fatto non c'è semplicemente niente da
tenere, e il login lo fai dopo: funziona lo stesso, è solo un passaggio in più.

---

## Cosa passa e cosa no

Misurato su un'installazione vera, ripristinando un backup da 150 viaggi su un'installazione col suo
login appena fatto:

| | |
|---|---|
| ✅ viaggi, posizioni, ricariche, rifornimenti, luoghi, prezzi, diario, registro dei segnali | ogni riga, byte per byte |
| ✅ le tue impostazioni — prezzi, tariffe, conservazione, MQTT, ABRP, lingua, unità | dal backup |
| ✅ il login della **nuova** installazione | tenuto, non sovrascritto |
| ❌ i segreti salvati nel backup | scartati — erano sigillati con la chiave dell'altra installazione |
| ❌ `secret.key`, `certs/`, `api-v2-private/`, `api-v2-account-material/` | non stanno nel database; la nuova installazione si fa i suoi |
| ❌ la foto dell'auto (`car_picture.png`, `car_picture_pkg.zip`) | è una cache accanto al database, viene riscaricata |

> ⚠️ **Tutto quello che hai configurato sulla nuova installazione prima di ripristinare viene
> sostituito dalla versione del backup** — ogni impostazione tranne il login. Configura dopo, non
> prima.

Il ripristino rifiuta un file che non sia un database di Mate — controlla la firma SQLite e che ci
siano le tabelle `settings`, `vehicles` e `positions` — e lo rifiuta **senza toccare** quello che
hai già.

---

## Tornare indietro

L'installazione beta da tutto questo non viene toccata: il suo database, la sua chiave e i suoi
certificati stanno ancora nella sua cartella dati. Se vuoi tornare indietro, la riavvii.

Il file di backup scaricato al passo 1 tienilo finché non sei sicuro: è l'unica copia dello storico
della beta che vive fuori dalla beta.
