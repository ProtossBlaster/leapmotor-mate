"""REEV — la scorta pagata a scalare (_paid_stock_replay).

Il modello, in una riga: nel pacco di un range-extender entra energia da due sorgenti ma spesa da
una sola. Il generatore aggiunge kWh e non euro, perché quei kWh sono già pagati in litri sul
viaggio che li ha bruciati. Quindi un prelievo può essere più grande della scorta pagata: il supero
costa zero, e quando la scorta è finita è finita.
"""
import db_reader


CAP = 28.0          # pacco B10/C10 REEV


def _charge(kwh, cost, start_soc=None):
    return {"kind": "charge", "kwh": kwh, "cost": cost, "start_soc": start_soc}


def _draw(kwh, tid=None):
    return {"kind": "draw", "kwh": kwh, "id": tid}


def test_paid_stock_runs_out_and_stays_out():
    """Il caso di Silvio: 28 kWh a 7,00 €, 10 kWh al giorno, mai piu' ricaricata.

    Al terzo giorno l'energia comprata e' finita. Dal quarto il costo elettrico e' ZERO ESATTO —
    non una coda che si assottiglia, che e' esattamente il difetto della media ponderata.
    """
    ev = [_charge(28.0, 7.00)] + [_draw(10.0, tid=d) for d in range(1, 8)]
    rows = db_reader._paid_stock_replay(ev, CAP)

    assert [r["cost"] for r in rows] == [2.50, 2.50, 2.00, 0.0, 0.0, 0.0, 0.0]
    assert [r["free_kwh"] for r in rows] == [0.0, 0.0, 2.0, 10.0, 10.0, 10.0, 10.0]


def test_total_billed_never_exceeds_what_was_spent():
    """La proprieta' che regge tutto: la somma degli addebiti non supera MAI la spesa reale.

    E' cio' che lo sconto proposto da gm27271 viola per difetto e la media ponderata per eccesso.
    """
    ev = [_charge(28.0, 7.00)] + [_draw(10.0) for _ in range(7)]
    assert sum(r["cost"] for r in db_reader._paid_stock_replay(ev, CAP)) == 7.00


def test_single_draw_larger_than_the_pack_is_capped():
    """0-100% = 28 kWh comprati, getEC dichiara 30 usciti: i 2 in piu' li ha fatti il generatore.

    Si pagano i 28 comprati, non 30 al prezzo di rete (che sarebbe 7,50 € su una ricarica da 7,00).
    """
    rows = db_reader._paid_stock_replay([_charge(28.0, 7.00), _draw(30.0)], CAP)
    assert rows[0]["paid_kwh"] == 28.0
    assert rows[0]["free_kwh"] == 2.0
    assert rows[0]["cost"] == 7.00


def test_never_plugged_in_costs_exactly_zero():
    """Mai attaccata la spina: ogni viaggio elettrico costa zero PRECISO, senza regole speciali.

    Non «gratis»: quell'energia e' gia' stata pagata in litri, sul viaggio che li ha bruciati.
    """
    rows = db_reader._paid_stock_replay([_draw(12.0), _draw(9.0)], CAP)
    assert [r["cost"] for r in rows] == [0.0, 0.0]
    assert [r["free_kwh"] for r in rows] == [12.0, 9.0]


def test_unpriced_charge_does_not_enter_the_stock():
    """Una ricarica non confermata non porta euro nel pacco — regola gia' viva in _wac_blend.

    Se entrasse coi suoi kWh ma senza costo, abbasserebbe il prezzo di cio' che e' stato pagato.
    """
    rows = db_reader._paid_stock_replay(
        [_charge(10.0, 2.50), _charge(10.0, None), _draw(20.0)], CAP)
    assert rows[0]["paid_kwh"] == 10.0
    assert rows[0]["free_kwh"] == 10.0
    assert rows[0]["cost"] == 2.50


def test_two_prices_blend_inside_the_stock():
    """Dentro la scorta i prezzi si mescolano (media ponderata), come fa gia' Mate oggi.

    20 kWh a 0,20 + 10 kWh a 0,50 = 30 kWh per 9,00 € → 0,30 €/kWh.
    """
    rows = db_reader._paid_stock_replay(
        [_charge(20.0, 4.00), _charge(10.0, 5.00), _draw(15.0)], CAP)
    assert rows[0]["rate"] == 0.30
    assert rows[0]["cost"] == 4.50


def test_vampire_drain_between_charges_does_not_leave_phantom_paid_kwh():
    """Il pacco perde anche fuori dai viaggi (vampire, preclima, clima a sosta: beta #18 michapr).

    Quel calo non e' un prelievo di viaggio: senza il ri-ancoraggio al SoC vero della ricarica
    successiva, la scorta si porterebbe dietro kWh pagati che nel pacco non ci sono piu'.

    28 kWh a 0,250, nessun viaggio, poi alla ricarica dopo il pacco e' al 50% (14 kWh) e si
    aggiungono 14 kWh a 0,100. I 14 evaporati devono sparire dalla scorta COL LORO VALORE:
    restano 14 vecchi (3,50 €) + 14 nuovi (1,40 €) = 28 kWh per 4,90 € → 0,175 €/kWh.

    ⚠️ I due prezzi devono essere DIVERSI e i prelievi DUE: con lo stesso prezzo, o con un solo
    prelievo, il conto torna identico anche senza ri-ancoraggio e il test non dimostra nulla —
    prima versione di questo test, beccata mutando il codice.
    """
    ev = [_charge(28.0, 7.00), _charge(14.0, 1.40, start_soc=50.0), _draw(28.0), _draw(10.0)]
    rows = db_reader._paid_stock_replay(ev, CAP)
    assert rows[0]["cost"] == 4.90              # senza ri-ancoraggio sarebbe 5,60 (42 kWh, 8,40 €)
    assert rows[0]["rate"] == 0.175             # …e la tariffa 0,200
    assert rows[1]["cost"] == 0.0               # la scorta e' finita: senza, resterebbero 14 kWh


def test_reanchor_never_inflates_the_stock():
    """Il ri-ancoraggio taglia soltanto: un SoC ALTO non puo' regalare kWh mai comprati.

    Pacco quasi vuoto di scorta (2 kWh pagati) ma SoC al 90% perche' l'ha riempito il generatore:
    la ricarica successiva parte da 2, non da 25.
    """
    ev = [_charge(2.0, 0.50), _charge(1.0, 0.25, start_soc=90.0), _draw(20.0)]
    rows = db_reader._paid_stock_replay(ev, CAP)
    assert rows[0]["paid_kwh"] == 3.0
    assert rows[0]["cost"] == 0.75
