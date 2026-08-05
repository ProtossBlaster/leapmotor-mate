# LeapMotor Mate — Manuel utilisateur

> **Version de Mate :** v3.6.0 · **Langue :** Français
> Ce manuel s'adresse à celles et ceux qui *utilisent* Mate, et non à ceux qui le développent. Il explique
> comment le configurer depuis le début et ce que fait chaque page. Pour les détails techniques internes, voir `ARCHITECTURE.md`.

---

## Sommaire

1. [Qu'est-ce que Mate (et ce qu'il n'est pas)](#1-quest-ce-que-mate-et-ce-quil-nest-pas)
2. [Avant de commencer : les prérequis](#2-avant-de-commencer--les-prérequis)
3. [Installation](#3-installation)
4. [Premier démarrage : la configuration guidée](#4-premier-démarrage--la-configuration-guidée)
5. [Découvrir l'interface](#5-découvrir-linterface)
6. [Les pages, une par une](#6-les-pages-une-par-une)
   - [Aperçu](#aperçu) · [Trajets](#trajets) · [Carte](#carte) · [Recharges](#recharges)
   - [Prix de recharge](#prix-de-recharge) · [Statistiques](#statistiques) · [Rapport mensuel](#rapport-mensuel)
   - [Santé de la batterie](#santé-de-la-batterie) · [Entretien](#entretien) · [Commandes](#commandes)
   - [Programmation](#programmation) · [Préparer le véhicule](#préparer-le-véhicule)
   - [Navigation](#navigation) · [Véhicule](#véhicule) · [Wallbox](#wallbox)
7. [Paramètres](#7-paramètres)
8. [Les intégrations en détail (Wallbox, ABRP, MQTT)](#8-les-intégrations-en-détail)
9. [Mode démo](#9-mode-démo)
10. [Questions fréquentes et résolution des problèmes](#10-questions-fréquentes-et-résolution-des-problèmes)
11. [Glossaire](#11-glossaire)

---

## 1. Qu'est-ce que Mate (et ce qu'il n'est pas)

**LeapMotor Mate** est une application que vous installez vous-même (auto-hébergée) et qui sert de
« compagnon » à votre voiture électrique Leapmotor. Elle se connecte au **cloud Leapmotor** (le même
que celui auquel parle l'application officielle), lit l'état de la voiture et, à partir de ces données,
reconstitue toute seule :

- vos **trajets** (distance, durée, consommation, récupération au freinage) ;
- vos **recharges** (énergie, puissance, type, coût) ;
- les **coûts** et l'**efficacité** dans le temps ;
- la **santé de la batterie** et les **échéances d'entretien**.

En plus, elle vous permet d'**envoyer des commandes à distance** (verrouillage, climatisation, préparation
du véhicule, programmations…) et, si vous le souhaitez, d'intégrer les données avec **Home Assistant**
(via MQTT), avec **A Better Routeplanner (ABRP)** et avec votre **wallbox**.

**Ce que Mate NE fait PAS / limites importantes :**

- **Il ne parle pas directement à la voiture.** Tout passe par le cloud Leapmotor. Quand Mate « interroge »
  le cloud (polling), il lit le **dernier état connu** : il *ne* réveille *pas* la voiture et *ne* décharge
  *pas* la batterie. C'est une opération sûre et économique.
- **Uniquement les voitures 100 % électriques (BEV).** Sont prises en charge les **T03, B05, B10, C10** dans
  leurs versions électriques. Les versions **REEV** (avec prolongateur d'autonomie à essence) **ne** sont
  **pas** prises en charge : les calculs d'énergie/consommation/coût utiliseraient la mauvaise capacité de
  batterie et seraient faussés.
- **Uniquement le cloud européen (Leapmotor International / Stellantis).** Les comptes enregistrés sur des
  serveurs d'autres régions (ex. Chine) ne parviennent pas à se connecter. Hors d'Europe, pour le moment,
  Mate n'est pas utilisable.
- **Ce n'est pas un outil de comptabilité.** Il estime le coût *à partir de la télémétrie* ; il ne suit pas
  les moyens de paiement, les factures ou les abonnements des bornes.

---

## 2. Avant de commencer : les prérequis

Pour configurer Mate, il vous faut trois choses :

1. **Un compte Leapmotor dédié à Mate.** ⚠️ **Très important.** Créez (ou réservez) un compte Leapmotor
   utilisé **uniquement** par Mate. Leapmotor n'autorise que peu de sessions simultanées par compte : si le
   même compte est aussi connecté dans l'application officielle, dans une autre intégration ou dans une
   seconde instance de Mate, les clients s'« expulsent » mutuellement la session. Le résultat : une rafale
   de *« Token non valide »* / des reconnexions répétées, la voiture qui passe **hors ligne** et des
   **données perdues** (trajets et recharges non enregistrés). C'est la cause numéro un des problèmes
   signalés. *Solution :* un compte secondaire avec un **mot de passe utilisé uniquement dans Mate**.

2. **Le certificat de l'application Leapmotor** (`app.crt` + `app.key`). C'est un certificat **identique pour
   tout le monde** (celui de l'application, pas de votre compte), nécessaire pour dialoguer avec le cloud.
   Il se télécharge depuis un dépôt public — l'assistant vous donne le lien direct
   ([github.com/markoceri/leapmotor-certs](https://github.com/markoceri/leapmotor-certs)).

3. **L'e-mail, le mot de passe et le code PIN d'opération du compte.** Le **PIN à 4 chiffres** est celui que
   vous utilisez aussi dans l'application officielle pour autoriser les commandes à distance (verrouillage,
   climatisation…).

> 💡 Vous voulez juste jeter un coup d'œil sans rien configurer ? Sautez tout et utilisez le
> **[mode démo](#9-mode-démo)** : Mate démarre avec un mois de données fictives réalistes, sans voiture et
> sans compte.

---

## 3. Installation

Mate fonctionne de la même manière dans trois environnements (l'interface est identique) :

- **Comme module complémentaire de Home Assistant** — la façon la plus simple si vous avez déjà Home
  Assistant. On ajoute le dépôt du module complémentaire, on installe « LeapMotor Mate » et on l'ouvre depuis
  la barre latérale de HA (ingress). Dans ce cas, Mate peut aussi lire votre **wallbox** directement depuis
  Home Assistant.
- **Comme conteneur Docker autonome** (par exemple sur un NAS) — via `docker-compose`. Dans ce cas,
  l'application est accessible depuis le navigateur sur le **port 4000**
  (`http://ADRESSE-DU-SERVEUR:4000`).
- **Comme application de bureau** — [**MateDesktop**](https://github.com/ProtossBlaster/MateDesktop) est le
  même Mate empaqueté pour **macOS et Windows**, pour ceux qui n'utilisent ni Home Assistant ni Docker :
  vous téléchargez, vous ouvrez, et vous retrouvez le même assistant de configuration. Sous Windows il est
  distribué **dans un `.zip`** — décompressez-le d'abord, puis lancez l'installateur : un `.exe` téléchargé
  depuis Internet n'a pas encore de réputation auprès de SmartScreen et se fait bloquer à l'entrée.

Les instructions d'installation pas à pas (dépôt, compose, etc.) se trouvent dans le **README** du projet et
sur la page **Docker Hub**. Une fois lancé, le *premier accès* est identique pour les deux et est décrit
ci-dessous.

> 📱 **Sur votre téléphone.** Mate n'est pas une application mobile et ne peut pas l'être : il doit
> interroger le cloud pendant des années, et un téléphone suspend ce qui tourne en arrière-plan.
> Vous pouvez toutefois le placer **sur votre écran d'accueil** : ouvrez Mate dans le navigateur du
> téléphone, puis *Partager → Sur l'écran d'accueil* sur iPhone, ou *⋮ → Ajouter à l'écran d'accueil*
> sur Android. Il prend l'icône de Mate et s'ouvre en plein écran, sans la barre d'adresse ni la
> barre d'outils — environ 110 px d'écran récupérés. Cela reste un raccourci vers le serveur que
> vous faites tourner : s'il est éteint, il n'ouvre rien.

> 🔒 **Sauvegarde.** Toutes les données de Mate se trouvent dans un dossier persistant (`/data`) : la base de
> données, la clé de chiffrement des secrets (`secret.key`) et le certificat. Si vous faites une sauvegarde,
> **conservez la base de données avec sa `secret.key`** — sans la clé, les mots de passe et jetons enregistrés
> ne sont plus lisibles. Depuis la page Paramètres, vous pouvez télécharger une sauvegarde de la base de
> données à tout moment.

---

## 4. Premier démarrage : la configuration guidée

Au premier accès, Mate affiche un **assistant** (procédure guidée). En haut, vous pouvez choisir la langue
(🇫🇷 Français). Ensuite :

### Étape 0 — Choisissez comment commencer

Deux boutons :

- **▶ Configurer ma voiture** — la configuration proprement dite (voir la suite).
- **🧪 Essayer la démo** — entre en mode démonstration avec des données fictives. Vous pouvez en sortir quand
  vous voulez.

### Étape 1 — Certificat de l'application

Mate vous demande le certificat TLS de l'application Leapmotor. Vous avez deux moyens :

- **Téléverser les fichiers** `app.crt` et `app.key` (mode par défaut), ou
- **Coller le texte PEM** des deux fichiers (bouton *« Coller le texte PEM à la place »*).

Téléchargez-les depuis le lien affiché, chargez-les et appuyez sur **Enregistrer le certificat**. Cette étape
n'apparaît que si le certificat n'est pas déjà présent dans l'image.

### Étape 2 — Connexion au compte

Saisissez :

- **E-mail du compte Leapmotor**
- **Mot de passe**
- **Code PIN d'opération** (4 chiffres)

> ⚠️ Ici, Mate vous rappelle d'utiliser **un compte dédié uniquement à Mate** (voir
> [prérequis](#2-avant-de-commencer--les-prérequis)).

Appuyez sur **🔍 Détecter ma voiture**. Mate vérifie les identifiants et lit dans le cloud le **modèle et le
numéro de châssis (VIN)**. Si tout se passe bien, vous voyez une fiche « Voiture détectée » avec
`Leapmotor <modèle> · VIN ···xxxxxx`.

### Étape 3 — Batterie

Selon le modèle :

- si la version européenne n'a **qu'une seule variante** de batterie, Mate la détecte tout seul (ex. T03 →
  37,3 kWh) ;
- s'il y a **plusieurs variantes** (ex. B10 Pro 56,2 kWh / Pro Max 67,1 kWh ; C10 RWD 69,9 / AWD 81,9),
  choisissez la vôtre ;
- si la détection échoue, vous pouvez **saisir la capacité à la main** (en kWh).

> La capacité indiquée est celle **utile/nette** (celle qui compte vraiment pour les consommations et les
> coûts) et peut toujours être corrigée par la suite, depuis Paramètres → Batterie.
> À côté se trouve la **Référence SoH** : la capacité à l'état neuf sur laquelle se mesure la santé
> batterie. Mate l'enregistre la première fois que tu sauvegardes la capacité puis n'y touche plus,
> afin qu'adopter une valeur mesurée (déjà vieillie) ne puisse pas ramener la santé à ~100 % et
> masquer le vieillissement. Si elle a été mal enregistrée, la santé peut dépasser 100 % : corrige-la là.

### Étape 4 — Connexion

Appuyez sur **Connecter et démarrer**. Mate enregistre la configuration, se connecte et vous amène à
l'**Aperçu**. À partir de ce moment, le « poller » commence à collecter des données en arrière-plan : les
premiers trajets et recharges apparaîtront au fur et à mesure que vous roulez et rechargez.

---

## 5. Découvrir l'interface

L'interface se compose de :

- **Menu latéral (barre latérale)** — la liste des pages (voir ci-dessous). Sur petit écran, il s'ouvre avec
  l'icône ☰.
- **En-tête** — titre de la page, éventuel **avis de mise à jour** disponible (↑ vX.Y.Z) et le bouton
  **🔄 Actualiser**.
- **Bouton Actualiser** — force une lecture immédiate de l'état de la voiture, sans attendre le cycle
  automatique. Utile après avoir envoyé une commande.

En bas du menu, vous trouvez **⚙️ Paramètres**, et **🚪 Déconnexion** *uniquement si vous avez
défini un mot de passe d'accès* — celle-ci ferme la session du mot de passe, rien d'autre. Sans mot
de passe, elle n'apparaît pas, car il n'y a rien à fermer.

**Pour délier le compte Leapmotor** — ce qui est autre chose — allez dans **Paramètres → Véhicule →
🔓 Déconnexion**. Cela efface les identifiants enregistrés et rouvre l'assistant ; le certificat, les
trajets et les recharges restent.

De nombreuses pages **se mettent à jour toutes seules** environ toutes les 30 secondes ; ainsi les valeurs
« en direct » (état, recharge en cours…) restent fraîches sans recharger la page.

**La langue, la devise et les unités** se changent depuis *Paramètres → 🌍 Langue et Devise* :

- **Langue :** Italiano, English, Français, Deutsch, Polski, Nederlands, Português.
  *(Un manuel écrit comme celui-ci existe en français, anglais, italien et allemand.)*
- **Devise :** pour les coûts (€, £, …).
- **Unités :** métriques (km, °C) ou impériales UK/US (miles, °F). Les données restent toujours enregistrées
  en km/°C ; seule change la façon dont elles sont **affichées**.

---

## 6. Les pages, une par une

L'ordre ci-dessous est le même que celui du menu latéral.

### Aperçu
**(menu : Aperçu)** — La page d'accueil. En haut, il y a une **carte principale** avec l'image de la voiture
et l'état en direct :

- **pourcentage de charge (SoC)** et autonomie estimée ;
- **icônes d'état** qui changent de couleur : verrouillage (vert = fermé, ambre = ouvert), coffre (rouge s'il
  est ouvert), vitres (violet si ouvertes), climatisation, etc. ;
- **commandes rapides** (verrouiller/déverrouiller, localiser la voiture…), déjà « conscientes » de l'état
  actuel ;
- quand la voiture est **en charge**, une **animation** montre le flux d'énergie ainsi qu'une étiquette avec
  l'estimation du temps « jusqu'à X % » (X = la limite de charge que vous avez définie dans la voiture) ;
- une étiquette **« Câble branché / Charge terminée »** quand le câble est inséré mais qu'aucune charge active
  n'est en cours.

Plus bas, vous trouvez des mini-statistiques et un **indicateur de « réactivité voiture »** (une pastille
🟢/🟡/🔴, ⚪ s'il n'y a pas de données) : il résume à quel point la voiture a répondu aux dernières commandes
envoyées.

Quand la voiture alimente un appareil externe via l'adaptateur **V2L** (vehicle-to-load), l'Aperçu affiche un
**bloc V2L** avec l'**état** (Actif / Inactif), la **puissance instantanée** en watts — indiquée **nette de la
consommation propre de la voiture (~300 W)** pour correspondre à ce que votre appareil consomme réellement,
avec une barre 0–3500 W — et l'**énergie soutirée durant la session** ; il se rafraîchit environ toutes les
**10 s** pendant une session. Le bloc est en **lecture seule** : le V2L se déclenche depuis la voiture (levier
sur P + un appareil branché), pas depuis Mate. Il est précis à partir d'environ **42 W** (la résolution du
capteur de courant de la voiture — une petite charge de ~10 W reste invisible).

### Trajets
**(menu : Trajets)** — La liste de vos déplacements, un par conduite. Pour chaque trajet, vous voyez
**distance, durée, consommation (kWh/100 km), énergie récupérée** au freinage et le **coût** estimé.

- En cliquant sur un trajet, vous ouvrez le **détail**, avec le **tracé GPS** sur la carte et les données de
  ce trajet précis.
- Vous pouvez **fusionner** deux trajets coupés par erreur (Fusionner 🔗) ou les **séparer** à nouveau, et
  **supprimer** un trajet.
- Les arrêts brefs (feux, embouteillages) **ne** coupent **pas** un trajet : une conduite reste une seule
  ligne.
- **Consommation officielle depuis le cloud 🆕** — lorsqu'elles sont disponibles, la **consommation,
  l'efficacité et le coût** d'un trajet proviennent de la **donnée officielle** Leapmotor (la vraie
  répartition **conduite / clim. / autre**) au lieu de la seule estimation par le % de batterie. Juste
  après un trajet, vous voyez l'estimation marquée **⏳ provisoire** ; dès que le cloud a traité la donnée
  (en général quelques dizaines de minutes), elle est **remplacée automatiquement** par l'officielle et la
  **répartition** apparaît dans le détail. Les trajets plus anciens ont un bouton **« Convertir avec les
  données officielles »**. Si le cloud n'a pas la donnée d'un trajet (cela arrive, sur toute voiture
  connectée), l'**estimation** reste — ce n'est pas une erreur. **Toujours actif**, sans configuration.
- **Votre note + tags de conduite 🆕** (#107) — dans le détail d'un trajet, vous pouvez écrire une **note
  libre** (trafic, météo, type de route, toute remarque) et indiquer le **mode de conduite** (Confort /
  Normal / Sport) et le **One-Pedal** (activé/désactivé) utilisés. Mate ne peut pas les lire depuis la
  voiture — Leapmotor ne les envoie pas au cloud — vous les renseignez donc à la main ; ils aident à
  expliquer pourquoi deux conduites semblables ont consommé différemment.

### Carte
**(menu : Carte)** — Tous les endroits où vous avez roulé, sur une seule carte. La position actuelle de la
voiture y figure (si la dernière donnée du cloud n'a pas de GPS valide, Mate **conserve la dernière position
valide** au lieu de faire disparaître la carte), et avec elle :

- **Le trajet de chaque déplacement**, tracé comme une ligne continue plutôt qu'en points épars, et jamais
  raccordé entre deux trajets différents.
- **Un pont magenta en pointillés là où le signal s'est perdu.** Un tunnel, une zone sans couverture, un
  hoquet du cloud : quand l'écart entre deux points enregistrés est bien plus grand que la cadence
  d'échantillonnage *de ce trajet-là*, Mate trace la liaison **en pointillés** au lieu d'un trait plein. Un
  trait plein signifie *la voiture a vraiment roulé là* ; des pointillés signifient *nous l'avons perdue
  ici*, et la droite entre les deux extrémités n'est pas une route.
- **Les lieux fréquents**, sous forme de bulles proportionnelles à la fréquence de vos arrêts, et les
  **bornes** que vous avez utilisées.
- **« Trajets affichés »**, une case sur la ligne de légende. Un long historique réduit la carte à une masse
  de lignes superposées : vous pouvez donc la limiter aux N trajets les plus récents ; **0 signifie tous**,
  et c'est ainsi qu'elle démarre. Limiter permet aussi à chaque trajet tracé de mieux épouser la vraie
  route, le budget de points étant réparti sur moins de trajets.

### Recharges
**(menu : Recharges)** — La liste des recharges. Pour chacune : **énergie ajoutée (kWh)**, **puissance
maximale**, **type** et **coût**, avec le **€/kWh effectif** bien en évidence. Le type est classé par une
étiquette :

- **Domicile** (votre wallbox **ou une prise domestique**), **AC** (courant alternatif public),
  **Rapide/FAST** (DC), **HPC** (recharge ultra-rapide) et **✎ Manuel**.
- **Domicile ne veut pas dire wallbox.** *Domicile* désigne **où** vous avez rechargé, pas à partir
  de quoi : une prise ordinaire dans le garage est aussi une recharge à domicile. La différence
  compte pour le calcul : si le compteur d'une wallbox est relié (voir *Wallbox* plus bas), la
  recharge est facturée sur l'**énergie délivrée par le compteur** ; sinon, elle l'est sur
  l'**énergie arrivée à la batterie**, exactement comme une recharge publique. Entre les deux se
  trouve la perte en chaleur du chargeur, typiquement 10 à 15 %.
- **✎ Manuel** : pour les bornes publiques aux tarifs compliqués (abonnements, frais de session…), vous pouvez
  **saisir à la main le total réellement payé** ; cette valeur remplace l'estimation automatique.
- **Les kWh de la borne 🆕** (#222) — sur une borne publique, Mate **n'a pas de compteur** : il ne lit
  que ce qui est entré dans la batterie, alors que la borne vous facture ce qui est sorti du sien.
  Vous pouvez saisir ce chiffre : sur la fiche de la recharge, sous les trois tuiles, il y a un
  **✎** ; le cadre **ne s'ouvre que si vous l'ouvrez** et la case est **toujours vide** — un clic de
  trop ne change donc rien, et valider à vide laisse tout en l'état. *Retirer* enlève une valeur
  erronée. Dès lors, ce nombre **tarife la recharge**, exactement comme le compteur de la wallbox à
  la maison, et affiche le **rendement** (ce que le chargeur embarqué a transformé en chaleur).
  L'énergie affichée par Mate reste celle **mesurée à la batterie**.
- **Le mois dit les deux 🆕** — au-dessus du calendrier : *« 154,93 kWh délivrés · 142,57 dans la
  batterie »*. Le premier, c'est ce qui est sorti des compteurs (la wallbox, ou les kWh que vous avez
  saisis) ; le second, ce qui est arrivé dans le pack. Entre les deux, la perte de conversion que
  vous payez.
- Même les recharges effectuées pendant que la voiture était éteinte/hors ligne sont **reconstruites** à
  partir du saut de pourcentage de charge.
- **Votre note 🆕** (#107) — chaque recharge a une **note libre** (juste au-dessus de *Supprimer la
  recharge*) pour ce que les chiffres ne capturent pas : l'emplacement de la borne, ombre/abri, sa
  fiabilité, les conditions de stationnement, la météo, toute remarque personnelle.

### Prix de recharge
**(menu : Prix de recharge)** — Ici, vous définissez **combien vous payez l'énergie**, afin que Mate puisse
calculer les coûts. Vous pouvez définir un prix **pour chaque type** de recharge (Domicile, AC, Rapide, HPC)
et choisir entre :

- **Tarif fixe** (un seul €/kWh), ou
- **Plages horaires (TOU)** — des prix différents selon le jour de la semaine et la plage horaire (ex. F1/F2/F3,
  nuit moins chère).

Le prix **Domicile** est celui qui alimente les coûts des recharges à domicile et, par ricochet, le coût des
trajets (calculé sur le prix « moyen » de l'énergie en batterie au moment du trajet).

> Les modifications de prix ne valent **que pour les recharges futures** : les coûts déjà calculés ne changent
> pas. Avec les plages horaires, vous pouvez aussi choisir *comment* répartir une session entre les plages —
> *Répartition précise* (sur la courbe de puissance réelle) ou *Heure de début* (toute la session à la plage
> où elle a démarré).

### Statistiques
**(menu : Statistiques)** — Vos moyennes et totaux dans le temps : **distance totale** et nombre de trajets,
**distance moyenne par trajet**, **temps de conduite**, **consommation moyenne** (pondérée sur la distance) et
**meilleure**, **énergie consommée et rechargée**, **récupération** totale et moyenne, nombre de **sessions de
recharge**, avec les **tendances** correspondantes (efficacité et récupération dans le temps). Les totaux
incluent désormais une carte **Total V2L** avec l'énergie cumulée soutirée via V2L sur tout l'historique.

**Coût aux 100 km 🆕** — ce que parcourir 100 km coûte réellement : **chaque euro dépensé**, divisé
par **chaque kilomètre parcouru**. Pas de prix au kWh ni d'estimation — la somme de ce que vous avez
payé sur la somme de ce que vous avez roulé, donc y compris les kWh qui n'ont fait avancer la
voiture nulle part (climatisation, préconditionnement, pertes du chargeur). Sur une version à
prolongateur d'autonomie, le carburant s'ajoute à côté de l'électricité. Si une recharge n'a pas de
prix, la carte le dit, car le vrai montant est alors plus élevé. Elle suit vos unités : en miles,
cela devient « aux 100 mi ».

**Depuis quand ces chiffres 🆕** — une ligne en haut de la page rappelle que **tous** les totaux des
Statistiques sont ceux enregistrés par Mate depuis son installation, avec la date de départ, et
**non** le total affiché par le compteur de la voiture.

**Ce que couvre chaque chiffre 🆕** — *Consommation moyenne* est la moyenne sur les kilomètres qui
**ont** une consommation, et « sur N km » apparaît en dessous quand ils sont moins nombreux que le
total. *Énergie consommée* n'additionne que les trajets dont Mate connaît l'énergie : un trajet sans
cette donnée est **exclu**, et non compté comme zéro, et la tuile indique de combien de trajets elle
parle. Sur une voiture où chaque trajet porte sa consommation — presque toujours — rien de tout cela
ne s'affiche.

### Rapport mensuel
**(menu : Rapport mensuel)** — Une synthèse **mois par mois** : combien vous avez roulé, combien d'énergie vous
avez consommée et rechargée, combien vous avez dépensé. Pratique pour suivre l'évolution. Il porte
aussi les cartes **consommation officielle** (Aujourd'hui / Cette semaine / Ce mois-ci) du cloud.

Il s'ouvre toujours sur le **mois en cours**, même le 1er sans un seul kilomètre : un mois vide le
dit, au lieu de vous montrer sans rien signaler celui d'avant. Et sur un mois encore vide, aucune
comparaison avec le précédent n'apparaît — chaque case afficherait −100 %, ce qui décrit le
calendrier et non votre conduite.

**D'où vient la consommation, et quand Mate la remplace.** *Consommation moyenne* et *Énergie
consommée* sont normalement le total officiel de la voiture pour ce mois. Ce total n'est complet que
dans la mesure où la liaison de votre voiture l'a été : si la voiture n'a pas pu joindre le cloud
pendant un trajet, ce trajet n'y figure pas. Quand le total revient très en dessous de la somme des
trajets que Mate a enregistrés pour le même mois, Mate affiche **son propre chiffre** — celui de la
page Trajets — et l'indique sous la case. La répartition Conduite / Clim / Autre reste celle de la
voiture, avec une ligne précisant qu'elle ne couvre que la partie arrivée au cloud.

### Santé de la batterie
**(menu : Santé batterie)** — Une **estimation de l'état de santé (SoH)** de la batterie : combien de capacité
utilisable il reste par rapport au neuf. Pour chaque recharge, Mate divise l'énergie qu'il a **mesurée**
entrant dans le pack (tension × courant, intégrée sur la session) par le pourcentage que cette recharge a
ajouté. Ce rapport est une estimation de la capacité du pack entier, et son évolution dans le temps — ou sur
les kilomètres, au choix — c'est le vieillissement.

Trois points sur la façon dont c'est calculé, car ils changent le sens du chiffre.

- **Le calcul s'arrête à 95 %.** Sur un pack LFP la tension varie très peu au milieu de la plage : le BMS
  **compte** la charge au lieu de la lire, et il dérive ; près du haut la courbe remonte enfin et le BMS
  **se recale**, ajoutant des points de pourcentage qu'aucune énergie n'a payés. Les compter ferait paraître
  le pack plus petit, et surtout sur un petit appoint jusqu'à 100 %, où ils représentent l'essentiel de la
  montée. Le calcul s'arrête donc à 95 % : la recharge compte quand même, seule sa dernière portion est
  laissée de côté.
- **Les grosses recharges pèsent davantage, proportionnellement.** Le chiffre principal additionne l'énergie
  et le pourcentage des recharges récentes au lieu d'en faire une moyenne une par une : une recharge ayant
  couvert 50 points pèse environ quatre fois plus qu'une de 13. Et rien n'est écarté pour y parvenir.
- **Les recharges à froid sont affichées mais exclues** — une LFP lit bas quand elle est froide — tout comme
  celles parties presque à vide ou celles où le BMS fait un saut.

**Le chiffre est accompagné d'un ± , et c'est la partie honnête.** C'est la **dispersion** des recharges qui
sont derrière, pas une exactitude : l'énergie est mesurée, mais le pourcentage qui la divise est un nombre
que le BMS a compté, et ce nombre dérive. Une fourchette étroite signifie que vos recharges s'accordent entre
elles, pas que le pack fait certainement cette taille. Avec une seule recharge, le ± ne s'affiche pas du
tout : une mesure n'a pas de dispersion à rapporter.

C'est donc une **estimation** — pas un diagnostic de laboratoire — et elle se stabilise à mesure que les
recharges s'accumulent.

### Entretien
**(menu : Entretien)** — Les **échéances d'entretien** de votre voiture, basées sur le **programme officiel de
votre modèle** (T03, B05, B10, C10). Pour chaque intervention (ex. révision, liquide de frein, filtre
d'habitacle, pneus…), vous voyez deux barres d'approche : une pour les **kilomètres** et une pour le **temps**,
car c'est la première échéance atteinte qui compte.

- Vous pouvez **enregistrer une intervention** (« fait aujourd'hui à X km ») directement depuis la page :
  l'échéance suivante se recalcule.
- Pour une **voiture neuve** qui n'a pas encore d'historique, vous pouvez définir une **date/un kilométrage de
  référence** afin que les échéances partent de la livraison (« première révision dans… ») au lieu d'apparaître
  comme « jamais effectué ».
- La **date d'immatriculation / de livraison est désormais modifiable** : cliquez sur le **✏️** à côté de la
  date enregistrée pour corriger une erreur (la nouvelle valeur écrase l'ancienne).
- Les distances respectent l'unité choisie (km ou miles).

### Commandes
**(menu : Commandes)** — Les **commandes à distance**. D'ici, vous pouvez :

- **verrouiller/déverrouiller**, ouvrir le **coffre**, **localiser la voiture** (klaxon/phares) ;
- gérer la **climatisation** : refroidissement, chauffage, dégivrage, ventilation, **extinction** ;
- activer le **chauffage des sièges**, du **volant** et des **rétroviseurs** (là où c'est pris en charge) ;
- gérer la **limite de charge**.

La carte climatisation comporte une rangée de **tuiles de mode** (A/C AUTO · Refroidir · Chauffer · Ventiler ·
Dégivrage) : celle qui correspond au **mode réel de la voiture s'allume**, une seule à la fois, comme dans
l'application officielle. En dessous, trois commandes : un **curseur de température**, un **curseur de
ventilation** (1–7) et un **interrupteur de recyclage** (air frais / recyclage).

- Dans les **trois modes manuels** (Refroidir, Chauffer, Ventiler), vous réglez à la fois la **température
  cible** et la **vitesse de ventilation** : la voiture **reste dans ce mode et conserve la valeur** choisie.
- En **AUTO**, c'est la voiture qui gère elle-même la ventilation et le recyclage : ces deux commandes
  **affichent la valeur actuelle mais restent en lecture seule** (la température, elle, reste réglable).
- La **Ventilation Rapide** enclenche de façon fiable la vraie ventilation (**air seulement, ni chaud ni
  froid**) depuis n'importe quel état.

Quand vous envoyez une commande, Mate met aussitôt à jour l'interface de façon « optimiste », puis confirme à
la lecture suivante. Si le cloud accepte mais que la voiture ne confirme pas en quelques secondes, vous voyez
un avis **ambre** (« envoyé, ça a peut-être marché ») — ce n'est pas une erreur : souvent la commande aboutit
quand même (cela dépend de la couverture/de la veille de la voiture).

### Programmation
**(menu : Programmation)** — Les **programmations** de la voiture :

- **Recharge programmée** (et la **limite de charge**) ;
- **Climatisation programmée** — 5 préréglages (refroidir / chauffer / ventiler / dégivrer / auto) avec une
  heure de démarrage future ; vous pouvez les créer, les modifier et les annuler.

### Préparer le véhicule
**(menu : Préparer le véhicule)** — La fonction « **préparer la voiture en un geste** » : amène l'habitacle à
la température souhaitée (et les fonctions associées) **tout de suite** ou à une **heure programmée**. Vous
pouvez aussi tout éteindre.

**🆕 Automatique au démarrage** — Au lieu d'appuyer sur le bouton à chaque fois, vous pouvez laisser Mate
exécuter la préparation **tout seul dès que la voiture passe en Ready** (mise sous tension). Activez
**Automatique au démarrage**, choisissez une fois ce qu'elle doit faire — préréglage de climatisation et
température souhaitée, ouverture des vitres, **ventilation ou chauffage** des sièges conducteur/passager,
chauffage du volant et des rétroviseurs — puis enregistrez.

Vous pouvez ajouter une **condition facultative sur la température intérieure** : exécuter la préparation
**seulement si l'habitacle est supérieur à** une valeur (p. ex. pré-refroidir uniquement au-dessus de
25 °C) **ou seulement s'il est inférieur à** une autre (p. ex. préchauffer uniquement en dessous de 5 °C).
**Laissez la condition désactivée et elle s'exécute à chaque démarrage**, quelle que soit la température.
Deux choses à savoir : elle regarde la température **intérieure** (la voiture ne fournit pas l'extérieure)
et elle est décidée **une seule fois, à l'instant où vous démarrez la voiture** — donc si l'habitacle
change plus tard pendant le trajet, elle ne se relance pas une deuxième fois.

Elle s'exécute **une fois par démarrage** (elle ne se répète pas tant que vous restez allumé, ni pour un
trajet ultérieur dans la même session), ignore les brefs parasites du signal et ne se relance jamais
simplement parce que Mate a redémarré.

### Navigation
**(menu : Navigation)** — *Envoyer une destination au GPS de la voiture* et **trouver les bornes à proximité**.
La page comporte trois parties :

- **Destination** — saisissez une **adresse** (et, si besoin, la **ville**), appuyez sur **Rechercher** : la
  destination apparaît sur la carte et avec **🧭 Envoyer à la voiture** vous l'envoyez au GPS de bord. *La
  recherche par adresse nécessite une clé de géocodage* (voir [Paramètres → Recherche d'adresses](#7-paramètres)).
- **⚡ Bornes de recharge — « Trouver des bornes »** — recherche les **bornes publiques autour de la voiture**
  (en utilisant sa position GPS actuelle). Vous pouvez régler :
  - **Distance max.** — 500 m, 1, 2, **5 km** (par défaut) ou 10 km ;
  - **Résultats par page** — 25, 50 ou 100 ;
  - **Réseau / opérateur** (facultatif) — pour filtrer un exploitant précis (ex. Electra, Ionity, Enel X Way,
    Be Charge, Plenitude, A2A, Atlante, Ewiva, Tesla…).

  Les résultats apparaissent à la fois sous forme de **repères ⚡ sur la carte** et dans une **liste** en
  dessous, avec **nom, distance** et, là où c'est disponible, la **disponibilité en temps réel** (🟢/🔴
  « disponibles maintenant », p. ex. sur le réseau public italien). Touchez une borne dans la liste pour la
  **voir sur la carte**, et d'un clic vous pouvez l'**utiliser comme destination** puis l'envoyer à la voiture.
  Si rien ne se trouve dans le rayon choisi, Mate élargit et affiche **les plus proches**.

  > La recherche de bornes **ne nécessite pas de clés** (elle utilise des cartes ouvertes + une base de bornes
  > publiques) ; les clés facultatives dans *Paramètres → ⚡ Bornes de recharge* (OpenChargeMap, TomTom)
  > l'enrichissent. Il faut toutefois que la voiture ait une **position GPS** connue.
- **Position actuelle de la voiture** — l'adresse de la voiture et une carte avec son repère 🚗.

### Véhicule
**(menu : Véhicule)** — La fiche **état complet** de la voiture : tous les capteurs disponibles sur votre
modèle (charge, autonomie, température intérieure, rapport, portes, vitres, pneus, verrouillages, état de
charge…). Mate lit désormais aussi, en direct, les réglages de **climatisation** : la **vitesse de ventilation**
(1–7), le **recyclage de l'air** (air frais / recyclage) et le **mode de climatisation actif** (AUTO /
Refroidissement / Chauffage / Ventilation). Mate n'affiche **que ce que votre voiture rapporte réellement**
(certains modèles n'exposent pas certaines données).

### Wallbox
**(menu : Wallbox)** — Si vous avez connecté une wallbox (voir [Intégrations](#8-les-intégrations-en-détail)),
vous y voyez ses données **en direct** (puissance, énergie), le **récapitulatif** et la liste des **sessions**,
et éventuellement les **contrôles** (ex. courant maximal) si votre wallbox les expose via Home Assistant.

---

## 7. Paramètres

**(menu : ⚙️ Paramètres)** — La page est organisée en **fiches en accordéon** : vous en ouvrez une à la fois.
Elle est divisée en trois colonnes.

**Colonne 1 — Véhicule et conduite**

- **🌍 Langue et Devise** — langue de l'interface, devise des coûts, **unités** (métriques/impériales).
- **Véhicule** — modèle et VIN de votre voiture, et **avec quel compte Leapmotor cette instance se
  connecte**. Le compte compte si vous faites tourner Mate plus d'une fois — une deuxième instance, une
  de test, une par voiture : le modèle et le VIN décrivent la *voiture*, donc deux instances qui
  surveillaient la même voiture étaient auparavant impossibles à distinguer de l'intérieur. C'est aussi
  ici que se trouve le bouton **🔓 Se déconnecter**
  (logout) pour relier un autre compte : il efface *seulement* les identifiants enregistrés, **pas** vos
  trajets/recharges ni le certificat.
- **Batterie** — la **capacité** en kWh utilisée pour tous les calculs ; modifiable. Si Mate dispose d'une
  estimation « mesurée » à partir de vos données, il vous la propose.
- **Fréquence de relevé** — à quelle fréquence Mate lit l'état du cloud, avec deux curseurs : **Stationné**
  (10 s–5 min, 30 s par défaut) et **En conduite** (10–60 s, 10 s par défaut). Lire plus souvent ne décharge
  pas la voiture, mais génère plus de trafic vers le cloud.
- **Détection de charge** — le **seuil de courant** (en ampères) au-dessus duquel Mate considère qu'une
  « recharge est en cours ». À n'abaisser que si vous avez des recharges très lentes non détectées.

**Colonne 2 — Intégrations**

- **ABRP** — envoi de la télémétrie à A Better Routeplanner (voir [§8](#8-les-intégrations-en-détail)).
- **Recherche d'adresses** — le service pour traduire les adresses ↔ coordonnées dans la page Navigation
  (Geoapify *recommandé*, LocationIQ, TomTom). Nécessite une **clé** gratuite du service choisi.
- **⚡ Bornes de recharge** — active les **noms des bornes** sur les recharges (📍) et accepte des clés
  optionnelles (OpenChargeMap, TomTom) pour enrichir la recherche. **Désactivé** par défaut.
- **Wallbox** — connectez votre wallbox pour les **coûts réels** et les éventuels contrôles (voir
  [§8](#8-les-intégrations-en-détail)).
- **MQTT → Home Assistant** — publie les données de la voiture comme entités dans Home Assistant (voir
  [§8](#8-les-intégrations-en-détail)).

**Colonne 3 — Données et maintenance**

- **🔐 Accès** *(Docker autonome uniquement — sous l'add-on Home Assistant, l'ingress authentifie
  déjà chaque requête et la carte n'apparaît pas)* — un mot de passe pour ouvrir Mate. Cela vaut la
  peine : sans lui, n'importe quoi sur votre réseau peut ouvrir Mate, et Mate peut ouvrir votre
  voiture.

  Vous le saisissez **deux fois**, car il n'y a aucun moyen de le relire ensuite — il est stocké
  sous forme d'empreinte salée, jamais en clair. **Si vous le perdez**, vous n'êtes pas enfermé
  dehors pour autant : le champ *Nouveau mot de passe* ne demande pas l'ancien, donc depuis
  n'importe quel appareil encore connecté vous en définissez un nouveau. S'il n'y a plus aucun
  appareil connecté, la variable d'environnement `MATE_AUTH_PASSWORD` prend le dessus sur celui qui
  est enregistré.

- **Base de données** — taille de la base et **conservation des positions** (rétention) : vous pouvez garder
  les points GPS « pour toujours » (par défaut) ou supprimer ceux de plus de 6/12/18/24 mois pour économiser
  de l'espace. *Seules les positions sont élaguées* : les trajets, recharges et courbes de charge restent.
- **Export / sauvegarde** — téléchargez les **trajets (CSV)**, les **recharges (CSV)** et une **sauvegarde de
  la base de données**.
- **🩺 Diagnostic** — une photographie du système (version, modèle, comptages, dernier relevé, intégrations
  actives), la possibilité de **voir les journaux** (poller/web) et surtout de **télécharger un paquet de
  diagnostic** en cochant les parties voulues (infos, journal poller, journal web, **signaux bruts**). Le
  paquet est **déjà nettoyé** des données sensibles : **GPS retiré** et VIN/secrets masqués, donc il est sûr à
  joindre quand vous demandez de l'aide. Il y a aussi une **analyse des recharges manquées** pendant que la
  voiture dormait.
- **⚙️ Avancé** — des paramètres fins pour utilisateurs expérimentés : seuil minimal pour **reconstruire** une
  recharge manquée, seuil de la **décharge à l'arrêt (vampire drain)**, seuil kW pour distinguer le **DC**, et
  température minimale pour le calcul de la **santé batterie**. Il y a un bouton pour **réinitialiser les
  valeurs par défaut**.

> 🆕 Quand une nouvelle fonction arrive, sa fiche peut afficher un badge **Nouveau** tant que vous ne l'avez
> pas ouverte la première fois.

---

## 8. Les intégrations en détail

Toutes les intégrations sont **facultatives** et **désactivées** par défaut. Elles se configurent depuis les
**Paramètres**.

### Wallbox (pour les coûts réels de recharge)
En connectant votre wallbox, Mate utilise l'**énergie réellement délivrée** (côté courant alternatif) pour
calculer le coût des recharges à domicile, au lieu de l'estimer à partir de la variation de pourcentage.

Mate lit la wallbox **à travers Home Assistant** :

1. Dans *Paramètres → Wallbox*, activez **Wallbox présente**.
2. **Si vous utilisez le module complémentaire de Home Assistant**, Mate peut atteindre HA tout seul : pas
   besoin de saisir d'adresse ni de jeton.
3. **Si vous utilisez Mate en Docker autonome**, saisissez l'**URL de Home Assistant** (ex.
   `http://192.168.1.10:8123`) et un **jeton d'accès longue durée** de HA, puis appuyez sur **Tester la
   connexion**.
4. Avec les **mots-clés**, vous pouvez aider Mate à reconnaître les bonnes entités de votre wallbox (ex.
   `wallbox, charger, evse, keba, pulsar`). Certaines wallbox connues (ex. V2C Trydan) sont reconnues
   automatiquement ; les entités « pièges » (solaire/maison) sont exclues.
5. Ouvrez la liste des entités pour vérifier que Mate a bien accroché les bons capteurs d'**énergie/puissance**.
6. Option **« domicile automatique »** : assigne automatiquement l'étiquette **Domicile** aux recharges
   effectuées sur votre wallbox.

### ABRP (A Better Routeplanner)
Envoie la télémétrie de la voiture à ABRP pour la planification d'itinéraires en temps réel.

1. Dans *Paramètres → ABRP*, activez **Activé**.
2. Collez votre **jeton** ABRP (vous le trouvez dans les réglages « generic »/télémétrie de votre compte
   ABRP).
3. Enregistrez. L'état de l'intégration apparaît dans l'en-tête de la fiche.

### MQTT → Home Assistant
Publie l'état de la voiture (charge, autonomie, position, portes, état de charge…) sous forme d'**entités dans
Home Assistant**, avec **auto-discovery**. Vous pouvez aussi **commander** la voiture depuis les entités de HA — y compris une **limite de charge** (`number` modifiable) pour régler le SoC cible et une entité **Programmation de charge** (`text` modifiable) qui accepte un plan JSON pensé pour les automatisations (`{"start":"23:00","soc":90}` — chaque champ est optionnel, et ce que vous omettez reste inchangé). Les réglages de climatisation sont également exposés : **Vitesse de ventilation** (`number` modifiable, 1–7), **Recyclage** (`switch` modifiable) et **Mode climatisation** (capteur : AUTO / Refroidissement / Chauffage / Ventilation). Trois entités V2L en lecture seule sont aussi publiées : **`V2L Active`** (binary sensor), **`V2L Power`** (W) et **`V2L Session Energy`** (Wh), ainsi qu'un binary sensor **`Ready`** qui s'allume dès que la voiture est sous tension — avant qu'elle ne roule, c'est-à-dire tant qu'une automatisation a encore le temps d'agir.

1. Préparez un **broker MQTT** (généralement le module complémentaire *Mosquitto* dans Home Assistant).
2. Dans *Paramètres → MQTT*, activez **Activé** et renseignez :
   - **Broker** (ex. `192.168.1.10` ou `core-mosquitto`) et **Port** (par défaut `1883`) ;
   - **Utilisateur** et **Mot de passe** du broker ;
   - **Préfixe** des topics (par défaut `leapmotor`) ;
   - options : **Discovery** (recommandée), **TLS** et **TLS non sécurisé** si vous utilisez des certificats
     auto-signés.
3. Appuyez sur **Tester la connexion** pour vérifier la connexion, puis **Enregistrer**. En quelques secondes,
   les entités apparaissent dans Home Assistant.

> Pour les commandes via MQTT, la voiture exige tout de même le PIN : Mate l'utilise automatiquement avec les
> identifiants enregistrés.

---

**Si vous avez plusieurs Mate sur le même broker 🆕** — l'add-on normal et celui BetaTester, par
exemple — donnez à chacun un **préfixe de topic différent** (*Paramètres → MQTT*). Avec le même
préfixe et la même voiture, Home Assistant ne voit qu'**un seul appareil** : le second semble ne pas
fonctionner, et surtout **chaque commande part deux fois**. Mate le détecte désormais et le signale ;
la version BetaTester se déplace d'elle-même, la version normale ne bouge jamais.

## 9. Mode démo

La **démo** sert à essayer Mate sans voiture et sans compte : elle démarre avec **un mois de données fictives
mais réalistes**. Vous pouvez l'activer de deux manières :

- depuis l'assistant de premier démarrage, bouton **🧪 Essayer la démo** ;
- ou en lançant le conteneur avec la variable `MATE_DEMO=1`.

En démo : les données sont ouvertement fictives (badge **DEMO**), les commandes sont **simulées** (aucune
voiture n'est contactée) et une bannière en haut reste toujours visible avec le bouton pour **quitter**. En
sortant, Mate revient à la configuration normale.

---

## 10. Questions fréquentes et résolution des problèmes

**La voiture passe souvent « hors ligne » / je vois « Token non valide » en continu.**
C'est presque toujours parce que le **même compte Leapmotor est utilisé ailleurs** (application officielle, une
autre intégration, une seconde instance de Mate). Utilisez un **compte dédié uniquement à Mate** et **changez
son mot de passe** en ne l'utilisant qu'ici (ainsi l'autre client est expulsé et ne revient pas). Voir
[prérequis](#2-avant-de-commencer--les-prérequis).

**Une commande donne « timeout » / un avis ambre.**
Ce n'est (généralement) pas un problème de Mate. Les commandes sont en *temps réel* et dépendent de la
**joignabilité de la voiture** (couverture, veille). Mate réessaie et souvent la commande aboutit quand même.
L'indicateur **« Réactivité voiture »** dans l'Aperçu vous donne une idée de la situation.

**Il manque des trajets ou des km après une période hors ligne.**
Quand la voiture était injoignable, certaines données peuvent ne pas avoir été enregistrées. Les recharges
survenues « pendant le sommeil » sont en général **reconstruites** à partir du saut de charge ; pour les km
perdus, il n'est pas toujours possible de les récupérer. L'**analyse des recharges manquées** (Paramètres →
Diagnostic) aide à retrouver les recharges non enregistrées.

**Je vois une recharge étrange / un coût absurde.**
Mate dispose de protections contre les valeurs impossibles (ex. compteurs de wallbox qui rapportent le total
cumulé à vie). Le cas inverse est couvert aussi : si le compteur de la wallbox **s'arrête** en cours de recharge
alors que la voiture continue de tirer du courant, Mate cesse de se fier à son total pour cette session et
facture sur l'énergie arrivée à la batterie — le total du compteur serait amputé de tout ce qu'il a manqué
pendant l'arrêt.
Si une recharge publique a un tarif compliqué, utilisez le type **✎ Manuel** et saisissez le
total payé.

**Le graphique de décharge à l'arrêt (vampire drain) est vide.**
Il faut au moins un **arrêt long** avec une baisse de charge mesurable au cours des derniers jours. Si la
voiture est toujours en charge ou dort à l'arrêt, il se peut qu'il n'y ait pas assez de matière. Mate capte
aussi la baisse qui ne se « révèle » qu'au réveil.
Une autre cause fréquente est le **seuil de la décharge à l'arrêt** dans *Paramètres → Avancé* : si vous l'avez
relevé au-dessus des baisses réelles de votre voiture, le graphique ne dessine rien. Ramenez-le vers **0,2**
(ou appuyez sur **Réinitialiser**) et les fenêtres réapparaissent. Depuis la **v1.22.4**, la page vous le dit
explicitement — elle affiche tout de même la valeur typique et un avis « sous votre seuil » au lieu de sembler
vide.

**J'ai une Leapmotor REEV (hybride avec prolongateur d'autonomie).**
Elle n'est pas prise en charge : les calculs d'énergie utiliseraient la capacité de batterie BEV et seraient
faussés. Mate est **uniquement pour les versions 100 % électriques**.

**Je ne suis pas en Europe.**
Pour le moment, Mate ne fonctionne qu'avec le cloud Leapmotor **européen**. Les comptes sur des serveurs
d'autres régions ne parviennent pas à se connecter.

**Comment faire une sauvegarde ?**
Depuis *Paramètres → Export/sauvegarde*, vous téléchargez la base de données (et les CSV). Conservez la base de
données **avec sa `secret.key`**.

---

## 11. Glossaire

- **SoC** (*State of Charge*) — pourcentage de charge de la batterie.
- **SoH** (*State of Health*) — état de santé de la batterie : capacité restante par rapport au neuf.
- **AC / DC** — courant alternatif (recharge lente, à domicile/bornes AC) / continu (recharge rapide et
  ultra-rapide).
- **Domicile / AC / Rapide (FAST) / HPC / Manuel** — les types de recharge que Mate reconnaît ou que vous
  pouvez assigner ; « HPC » est la recharge à très haute puissance.
- **TOU** (*Time-of-Use*) — tarif à **plages horaires** (prix différents selon le jour/l'heure).
- **Régén** — énergie **récupérée** au freinage/au lâcher de l'accélérateur et remise en batterie.
- **Vampire drain** — ce que la voiture consomme **complètement éteinte**, mesuré de l'extinction au
  prochain démarrage. **Inclut le chauffage/refroidissement voiture éteinte** (par conception : voiture
  éteinte → compté comme décharge). Le ralenti voiture *allumée* (à l'arrêt, moteur/clim. actif) n'y entre pas.
- **Polling** — la lecture périodique de l'état de la voiture depuis le cloud (ne décharge pas la voiture).
- **Wallbox** — votre station de recharge domestique.
- **Poller / Web** — les deux composants internes de Mate : le *poller* collecte les données, le *web* affiche
  l'interface. Pour vous, utilisateur, c'est un détail : ils travaillent ensemble.
- **VIN** — le numéro de châssis de la voiture ; il identifie de façon unique votre véhicule.
- **PIN d'opération** — le PIN à 4 chiffres du compte, nécessaire pour autoriser les commandes à distance.

---

> 📌 **Note de maintenance du manuel.** Ce document décrit la version **v1.28.0**. Quand quelque chose de
> visible par l'utilisateur change (une nouvelle page, une option, un flux), mettez à jour la section
> correspondante et la ligne de version en haut. Il est conçu comme base pour les traductions (EN/FR/DE) : la
> structure est volontairement la même que celle de l'interface.
