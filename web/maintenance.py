"""Maintenance schedule — load per-model factory service packs and compute
due/overdue status from the live odometer + a delivery baseline + the user's log.

Packs are static JSON in maintenance_packs/, keyed STRICTLY by car_type (no merge,
no cross-model fallback — each car shows ONLY its own validated programme). The only
stateful part is the maintenance_logs table (when each service was last done) plus
two settings: maint_baseline_date / maint_baseline_km (the car's start of service).

Distances are stored/computed in km (the DB is always metric — see units.py) and
converted to the user's unit (mi for UK/US) only for display + form input.
"""
import json
import sqlite3
from datetime import date, datetime
from pathlib import Path
from typing import Optional

import db_reader
import units

PACKS_DIR = Path(__file__).parent / "maintenance_packs"
SOON_KM = 2000           # within this many km of due → "due soon"
SOON_DAYS = 30           # within this many days of due → "due soon"
_DAYS_PER_MONTH = 30.44

# ── Localisation (self-contained; chrome nav label lives in i18n.py) ──────────
# Item + category + phrase strings for it/en; fr/de/pt-PT fall back to en.
_LABELS = {
    "brake_fluid_replace":   {"en": "Brake fluid — replace",        "it": "Liquido freni — sostituzione",        "fr": "Liquide de frein — remplacer",            "de": "Bremsflüssigkeit — wechseln", "pt-PT": "Líquido dos travões — substituição"},
    "brake_fluid_inspect":   {"en": "Brake fluid level — check",    "it": "Livello liquido freni — controllo",    "fr": "Niveau de liquide de frein — vérifier",   "de": "Bremsflüssigkeitsstand — prüfen", "pt-PT": "Nível do líquido dos travões — verificação"},
    "brake_pads_inspect":    {"en": "Brake pads — check",           "it": "Pastiglie freni — controllo",          "fr": "Plaquettes de frein — vérifier",          "de": "Bremsbeläge — prüfen", "pt-PT": "Pastilhas dos travões — verificação"},
    "brake_discs_inspect":   {"en": "Brake discs — check",          "it": "Dischi freni — controllo",             "fr": "Disques de frein — vérifier",             "de": "Bremsscheiben — prüfen", "pt-PT": "Discos dos travões — verificação"},
    "brake_hoses_inspect":   {"en": "Brake hoses & lines — check",  "it": "Tubi freni — controllo",               "fr": "Durites & conduites de frein — vérifier", "de": "Bremsschläuche & -leitungen — prüfen", "pt-PT": "Tubos e linhas dos travões — verificação"},
    "cabin_filter_replace":  {"en": "Cabin (A/C) filter — replace", "it": "Filtro abitacolo (A/C) — sostituzione","fr": "Filtre d'habitacle (clim) — remplacer",   "de": "Innenraumfilter (Klima) — wechseln", "pt-PT": "Filtro do habitáculo (A/C) — substituição"},
    "coolant_replace":       {"en": "Coolant — replace",            "it": "Refrigerante — sostituzione",          "fr": "Liquide de refroidissement — remplacer",  "de": "Kühlmittel — wechseln", "pt-PT": "Líquido de refrigeração — substituição"},
    "coolant_inspect":       {"en": "Coolant level — check",        "it": "Livello refrigerante — controllo",     "fr": "Niveau de refroidissement — vérifier",    "de": "Kühlmittelstand — prüfen", "pt-PT": "Nível do líquido de refrigeração — verificação"},
    "reducer_oil_replace":   {"en": "Reducer (gearbox) oil — replace", "it": "Olio riduttore — sostituzione",     "fr": "Huile du réducteur — remplacer",          "de": "Reduktoröl — wechseln", "pt-PT": "Óleo do redutor (caixa) — substituição"},
    "reducer_filter_replace":{"en": "Reducer filter — replace",     "it": "Filtro riduttore — sostituzione",      "fr": "Filtre du réducteur — remplacer",         "de": "Reduktorfilter — wechseln", "pt-PT": "Filtro do redutor — substituição"},
    "tire_replace":          {"en": "Tires — replace",              "it": "Pneumatici — sostituzione",            "fr": "Pneus — remplacer",                       "de": "Reifen — wechseln", "pt-PT": "Pneus — substituição"},
    "tire_rotation":         {"en": "Tire rotation",                "it": "Rotazione pneumatici",                 "fr": "Permutation des pneus",                   "de": "Reifenrotation", "pt-PT": "Rotação dos pneus"},
    "tire_inspect":          {"en": "Tires — pressure & tread",     "it": "Pneumatici — pressione e battistrada", "fr": "Pneus — pression & usure",                "de": "Reifen — Druck & Profil", "pt-PT": "Pneus — pressão e piso"},
    "wiper_blades_replace":  {"en": "Wiper blades — replace",       "it": "Spazzole tergicristallo — sostituzione","fr": "Balais d'essuie-glace — remplacer",      "de": "Wischerblätter — wechseln", "pt-PT": "Escovas do limpa-para-brisas — substituição"},
    "wiper_blades_inspect":  {"en": "Wiper blades — check",         "it": "Spazzole tergicristallo — controllo",  "fr": "Balais d'essuie-glace — vérifier",        "de": "Wischerblätter — prüfen", "pt-PT": "Escovas do limpa-para-brisas — verificação"},
    "driveshaft_boot_inspect":{"en":"Steering & drive-shaft boots — check","it":"Parapolvere sterzo/trasmissione — controllo","fr":"Soufflets direction & transmission — vérifier","de":"Manschetten Lenkung & Antrieb — prüfen", "pt-PT":"Foles da direção e da transmissão — verificação"},
    "battery_pack_inspect":  {"en": "HV battery pack — check",      "it": "Pacco batteria HV — controllo",        "fr": "Batterie HT — vérifier",                  "de": "HV-Batterie — prüfen", "pt-PT": "Bateria de alta tensão — verificação"},
    "drive_motor_inspect":   {"en": "Drive motor & reducer — check","it": "Motore e riduttore — controllo",       "fr": "Moteur & réducteur — vérifier",           "de": "Antriebsmotor & Reduktor — prüfen", "pt-PT": "Motor de tração e redutor — verificação"},
    "hvac_system_check":     {"en": "A/C system — check",           "it": "Impianto A/C — controllo",             "fr": "Système de clim — vérifier",              "de": "Klimaanlage — prüfen", "pt-PT": "Sistema de A/C — verificação"},
    "charging_port_inspect": {"en": "Charging port — check",        "it": "Presa di ricarica — controllo",        "fr": "Prise de charge — vérifier",              "de": "Ladeanschluss — prüfen", "pt-PT": "Porta de carregamento — verificação"},
    "lights_horn_check":     {"en": "Lights, horn, wipers — check", "it": "Luci, clacson, tergi — controllo",     "fr": "Feux, klaxon, essuie-glaces — vérifier",  "de": "Licht, Hupe, Wischer — prüfen", "pt-PT": "Luzes, cláxon, limpa-para-brisas — verificação"},
    "software_update_check": {"en": "Control software — check",     "it": "Software centralina — controllo",      "fr": "Logiciel de commande — vérifier",         "de": "Steuergerät-Software — prüfen", "pt-PT": "Software de controlo — verificação"},
}
_CATS = {
    "brakes":     {"en": "Brakes",      "it": "Freni",        "fr": "Freins",          "de": "Bremsen",  "pt-PT": "Travões", "icon": "🛑"},
    "tires":      {"en": "Tires",       "it": "Pneumatici",   "fr": "Pneus",           "de": "Reifen",   "pt-PT": "Pneus", "icon": "🛞"},
    "drivetrain": {"en": "Drivetrain",  "it": "Trasmissione", "fr": "Transmission",    "de": "Antrieb",  "pt-PT": "Transmissão", "icon": "⚙️"},
    "climate":    {"en": "Climate",     "it": "Clima",        "fr": "Climatisation",   "de": "Klima",    "pt-PT": "Climatização", "icon": "❄️"},
    "cooling":    {"en": "Cooling",     "it": "Raffreddamento","fr": "Refroidissement", "de": "Kühlung", "pt-PT": "Arrefecimento", "icon": "💧"},
    "battery":    {"en": "Battery",     "it": "Batteria",     "fr": "Batterie",        "de": "Batterie", "pt-PT": "Bateria", "icon": "🔋"},
    "electrical": {"en": "Electrical",  "it": "Elettrico",    "fr": "Électrique",      "de": "Elektrik", "pt-PT": "Elétrico", "icon": "💡"},
    "exterior":   {"en": "Exterior",    "it": "Esterno",      "fr": "Extérieur",       "de": "Außen",    "pt-PT": "Exterior", "icon": "🪟"},
}
_CHROME = {
    "title":        {"en": "Maintenance",            "it": "Manutenzione", "fr": "Entretien", "de": "Wartung", "pt-PT": "Manutenção"},
    "subtitle":     {"en": "Factory service schedule from your car's official manual", "it": "Programma di manutenzione ufficiale dal manuale della tua auto", "fr": "Programme d'entretien officiel issu du manuel de votre voiture", "de": "Werks-Wartungsplan aus dem offiziellen Handbuch Ihres Autos", "pt-PT": "Plano de manutenção de fábrica com base no manual oficial do seu carro"},
    "odometer":     {"en": "Odometer",               "it": "Contachilometri", "fr": "Compteur", "de": "Kilometerstand", "pt-PT": "Odómetro"},
    "overdue":      {"en": "Overdue",                "it": "Scaduti", "fr": "En retard", "de": "Überfällig", "pt-PT": "Em atraso"},
    "soon":         {"en": "Due soon",               "it": "In scadenza", "fr": "Bientôt dû", "de": "Bald fällig", "pt-PT": "Em breve"},
    "ok":           {"en": "Up to date",             "it": "In regola", "fr": "À jour", "de": "Aktuell", "pt-PT": "Em dia"},
    "log_btn":      {"en": "Log",                    "it": "Registra", "fr": "Enregistrer", "de": "Eintragen", "pt-PT": "Registar"},
    "log_done":     {"en": "Mark this service as done", "it": "Registra questo intervento", "fr": "Marquer cet entretien comme fait", "de": "Diesen Service als erledigt markieren", "pt-PT": "Marcar este serviço como concluído"},
    "date":         {"en": "Date",                   "it": "Data", "fr": "Date", "de": "Datum", "pt-PT": "Data"},
    "note":         {"en": "Note (optional)",        "it": "Nota (opzionale)", "fr": "Note (facultatif)", "de": "Notiz (optional)", "pt-PT": "Nota (opcional)"},
    "save":         {"en": "Save",                   "it": "Salva", "fr": "Enregistrer", "de": "Speichern", "pt-PT": "Guardar"},
    "cancel":       {"en": "Cancel",                 "it": "Annulla", "fr": "Annuler", "de": "Abbrechen", "pt-PT": "Cancelar"},
    "last":         {"en": "Last",                   "it": "Ultimo", "fr": "Dernier", "de": "Zuletzt", "pt-PT": "Último"},
    "never":        {"en": "Never logged",           "it": "Mai registrato", "fr": "Jamais enregistré", "de": "Nie eingetragen", "pt-PT": "Nunca registado"},
    "from_delivery":{"en": "since delivery",         "it": "dalla consegna", "fr": "depuis la livraison", "de": "seit Übergabe", "pt-PT": "desde a entrega"},
    "interval":     {"en": "interval",               "it": "intervallo", "fr": "intervalle", "de": "Intervall", "pt-PT": "intervalo"},
    "no_pack_title":{"en": "Schedule not available for this model yet", "it": "Programma non ancora disponibile per questo modello", "fr": "Programme pas encore disponible pour ce modèle", "de": "Plan für dieses Modell noch nicht verfügbar", "pt-PT": "Plano ainda não disponível para este modelo"},
    "no_pack_body": {"en": "Mate ships validated BEV schedules for T03, B05, B10 and C10.", "it": "Mate include programmi BEV validati per T03, B05, B10 e C10.", "fr": "Mate fournit des programmes BEV validés pour T03, B05, B10 et C10.", "de": "Mate liefert validierte BEV-Pläne für T03, B05, B10 und C10.", "pt-PT": "O Mate disponibiliza planos BEV validados para T03, B05, B10 e C10."},
    "provisional":  {"en": "Provisional schedule — to confirm against this model's manual.", "it": "Programma provvisorio — da confermare sul manuale di questo modello.", "fr": "Programme provisoire — à confirmer avec le manuel de ce modèle.", "de": "Vorläufiger Plan — anhand des Handbuchs dieses Modells zu bestätigen.", "pt-PT": "Plano provisório — a confirmar no manual deste modelo."},
    "baseline_q":   {"en": "When did you take delivery of the car?", "it": "Quando hai ritirato l'auto?", "fr": "Quand avez-vous pris livraison de la voiture ?", "de": "Wann haben Sie das Auto übernommen?", "pt-PT": "Quando recebeu o carro?"},
    "baseline_hint":{"en": "Set the registration/delivery date so first-service due dates are accurate.", "it": "Imposta la data di immatricolazione/consegna per scadenze del primo tagliando accurate.", "fr": "Indiquez la date d'immatriculation/livraison pour des échéances de premier entretien exactes.", "de": "Geben Sie das Zulassungs-/Übergabedatum an, damit die ersten Service-Termine stimmen.", "pt-PT": "Defina a data de matrícula/entrega para que as datas da primeira revisão sejam precisas."},
    "baseline_save":{"en": "Set delivery date", "it": "Imposta data consegna", "fr": "Définir la date de livraison", "de": "Übergabedatum festlegen", "pt-PT": "Definir data de entrega"},
    "baseline_set": {"en": "Delivery", "it": "Consegna", "fr": "Livraison", "de": "Übergabe", "pt-PT": "Entrega"},
    "baseline_edit":{"en": "edit", "it": "modifica", "fr": "modifier", "de": "ändern", "pt-PT": "editar"},
    "baseline_km":  {"en": "Odometer at delivery (km)", "it": "Odometro alla consegna (km)", "fr": "Compteur à la livraison (km)", "de": "Kilometerstand bei Übergabe (km)", "pt-PT": "Odómetro na entrega (km)"},
    "baseline_km_hint": {"en": "0 if the car was new — so first-service bars start from the real odometer.", "it": "0 se l'auto era nuova — così le barre del primo tagliando partono dall'odometro reale.", "fr": "0 si la voiture était neuve — pour que les barres du premier entretien partent du vrai compteur.", "de": "0, wenn das Auto neu war — damit die Balken des ersten Service vom echten Kilometerstand ausgehen.", "pt-PT": "0 se o carro era novo — para que as barras da primeira revisão partam do odómetro real."},
    "all_good":     {"en": "Everything is up to date.", "it": "È tutto in regola.", "fr": "Tout est à jour.", "de": "Alles ist aktuell.", "pt-PT": "Está tudo em dia."},
    "next_first":   {"en": "First service", "it": "Primo tagliando", "fr": "Premier entretien", "de": "Erster Service", "pt-PT": "Primeira revisão"},
    "st_overdue":   {"en": "Overdue",   "it": "Scaduto", "fr": "En retard", "de": "Überfällig", "pt-PT": "Em atraso"},
    "st_soon":      {"en": "Due soon",  "it": "In scadenza", "fr": "Bientôt", "de": "Bald", "pt-PT": "Em breve"},
    "st_ok":        {"en": "OK",        "it": "In regola", "fr": "OK", "de": "OK", "pt-PT": "OK"},
    "bar_time":     {"en": "time",      "it": "tempo", "fr": "temps", "de": "Zeit", "pt-PT": "tempo"},
}


def _loc(d: dict, lang: str) -> str:
    return d.get(lang) or d.get("en") or ""


def chrome(lang: str) -> dict:
    return {k: _loc(v, lang) for k, v in _CHROME.items()}


# ── Pack loading (exact car_type match, no fallback) ──────────────────────────
_pack_cache: dict = {}


def load_pack(car_type: Optional[str]) -> Optional[dict]:
    if not car_type:
        return None
    key = car_type.strip().upper()
    if key in _pack_cache:
        return _pack_cache[key]
    found = None
    for f in sorted(PACKS_DIR.glob("*.json")):
        try:
            d = json.loads(f.read_text())
        except Exception:
            continue
        if key in [str(m).upper() for m in d.get("model_compat", [])]:
            found = d
            break
    _pack_cache[key] = found
    return found


# ── Logs + baseline ───────────────────────────────────────────────────────────
def _ensure_table(conn) -> None:
    conn.execute("""CREATE TABLE IF NOT EXISTS maintenance_logs (
        id INTEGER PRIMARY KEY, vehicle_id INTEGER, service_type TEXT NOT NULL,
        done_date TEXT NOT NULL, done_odometer_km REAL, note TEXT,
        created_at TEXT DEFAULT (datetime('now')))""")


def latest_logs(vehicle_id: int) -> dict:
    """Most-recent log per service_type → {service_type: {done_date, done_odometer_km, note}}."""
    try:
        rows = db_reader._get().execute(
            "SELECT service_type, done_date, done_odometer_km, note FROM maintenance_logs "
            "WHERE vehicle_id=? ORDER BY done_date DESC, id DESC", (vehicle_id,)).fetchall()
    except sqlite3.OperationalError:
        return {}
    out: dict = {}
    for r in rows:
        out.setdefault(r["service_type"], dict(r))   # first seen = most recent (sorted desc)
    return out


def add_log(vehicle_id: int, service_type: str, done_date: str,
            done_km: Optional[float], note: str = "") -> None:
    conn = db_reader._conn_rw()
    _ensure_table(conn)
    conn.execute(
        "INSERT INTO maintenance_logs (vehicle_id, service_type, done_date, done_odometer_km, note) "
        "VALUES (?,?,?,?,?)", (vehicle_id, service_type, done_date, done_km, note or None))
    conn.commit()


def delete_log(vehicle_id: int, service_type: str) -> None:
    """Remove the most recent log for a service_type (an 'undo' for a mis-entry)."""
    conn = db_reader._conn_rw()
    _ensure_table(conn)
    row = conn.execute("SELECT id FROM maintenance_logs WHERE vehicle_id=? AND service_type=? "
                       "ORDER BY done_date DESC, id DESC LIMIT 1", (vehicle_id, service_type)).fetchone()
    if row:
        conn.execute("DELETE FROM maintenance_logs WHERE id=?", (row["id"],))
        conn.commit()


def get_baseline():
    """The car's start-of-service anchor (date, km, explicit?). If the user hasn't set
    one, infer it from the earliest odometer/date Mate has seen (so a new car still gets
    sensible first-service dates) and flag explicit=False so the UI can prompt."""
    d = db_reader.get_setting("maint_baseline_date", "")
    k = db_reader.get_setting("maint_baseline_km", "")
    if d:
        try:
            return d, (float(k) if k != "" else 0.0), True
        except ValueError:
            pass
    row = db_reader._get().execute(
        "SELECT MIN(recorded_at) AS rd, MIN(odometer_km) AS ko FROM positions "
        "WHERE odometer_km IS NOT NULL AND odometer_km > 0").fetchone()
    bdate, bkm = None, 0.0
    if row and row["rd"]:
        dt = db_reader._local_dt(row["rd"])
        bdate = dt.date().isoformat() if dt else None
        bkm = float(row["ko"] or 0.0)
    return bdate, bkm, False


def set_baseline(date_iso: str, km: Optional[float]) -> None:
    db_reader.set_setting("maint_baseline_date", date_iso)
    if km is not None:
        db_reader.set_setting("maint_baseline_km", str(km))


# ── Formatting helpers (distance respects the user's unit system) ─────────────
def _grp(n, lang: str) -> str:
    s = f"{int(round(n)):,}"
    return s.replace(",", ".") if lang != "en" else s


def _dist(km, lang: str, system: str) -> str:
    """A km distance → '20.000 km' (metric) or '12.427 mi' (imperial), grouped by lang."""
    v = units.dist_val(km, 0, system)
    return f"{_grp(v, lang)} {units.dist_unit(system)}"


def _parse_date(s) -> Optional[date]:
    if not s:
        return None
    try:
        return date.fromisoformat(str(s)[:10])
    except ValueError:
        return None


def _add_months(d: date, months: int) -> date:
    m = d.month - 1 + months
    y = d.year + m // 12
    m = m % 12 + 1
    import calendar
    day = min(d.day, calendar.monthrange(y, m)[1])
    return date(y, m, day)


# Dynamic phrase fragments (5 languages; fr/de/pt-PT fall back to en if a key is missing).
_P = {
    "en": {"first": "First service: in ", "in_": "In ", "overdue": "Overdue by ", "every": "every ", "or": " or ",
           "yN": "{n} years", "y1": "1 year", "moN": "{n} months", "mo1": "1 month", "dN": "{n} days", "d1": "1 day"},
    "it": {"first": "Primo tagliando: tra ", "in_": "Tra ", "overdue": "Scaduto da ", "every": "ogni ", "or": " o ",
           "yN": "{n} anni", "y1": "1 anno", "moN": "{n} mesi", "mo1": "1 mese", "dN": "{n} giorni", "d1": "1 giorno"},
    "fr": {"first": "Premier entretien : dans ", "in_": "Dans ", "overdue": "En retard de ", "every": "tous les ", "or": " ou ",
           "yN": "{n} ans", "y1": "1 an", "moN": "{n} mois", "mo1": "1 mois", "dN": "{n} jours", "d1": "1 jour"},
    "de": {"first": "Erster Service: in ", "in_": "In ", "overdue": "Überfällig seit ", "every": "alle ", "or": " oder ",
           "yN": "{n} Jahre", "y1": "1 Jahr", "moN": "{n} Monate", "mo1": "1 Monat", "dN": "{n} Tage", "d1": "1 Tag"},
    "pt-PT": {"first": "Primeira revisão: em ", "in_": "Em ", "overdue": "Em atraso há ", "every": "a cada ", "or": " ou ",
        "yN": "{n} anos", "y1": "1 ano", "moN": "{n} meses", "mo1": "1 mês", "dN": "{n} dias", "d1": "1 dia"},
}


def _p(lang: str) -> dict:
    return _P.get(lang, _P["en"])


def _dur(days: int, lang: str) -> str:
    """Human duration for a positive number of days, in the UI language."""
    days = int(round(days))
    p = _p(lang)
    if days >= 365:
        y = round(days / 365)
        return p["yN"].format(n=y) if y > 1 else p["y1"]
    if days >= 55:
        return p["moN"].format(n=round(days / _DAYS_PER_MONTH))
    if days >= 25:
        return p["mo1"]
    if days <= 1:
        return p["d1"]
    return p["dN"].format(n=days)


def _months_text(m: int, lang: str) -> str:
    p = _p(lang)
    if m % 12 == 0:
        y = m // 12
        return p["yN"].format(n=y) if y > 1 else p["y1"]
    return p["moN"].format(n=m)


def _interval_text(item: dict, lang: str, system: str) -> str:
    p = _p(lang)
    parts = []
    if item.get("interval_km"):
        parts.append(_dist(item["interval_km"], lang, system))
    if item.get("interval_months"):
        parts.append(_months_text(item["interval_months"], lang))
    body = p["or"].join(parts) if parts else ""
    return (p["every"] + body) if body else ""


def compute(vehicle: Optional[dict], current_km: Optional[float], lang: str) -> dict:
    """Build the per-item due/overdue view for the maintenance page."""
    car_type = (vehicle or {}).get("car_type")
    pack = load_pack(car_type)
    system = units.get_unit_system()
    if not pack:
        return {"has_pack": False, "car_type": car_type}

    vehicle_id = (vehicle or {}).get("id")
    logs = latest_logs(vehicle_id) if vehicle_id is not None else {}
    bdate_s, bkm, b_explicit = get_baseline()
    bdate = _parse_date(bdate_s)
    today = datetime.now().date()

    items, n_over, n_soon, n_ok = [], 0, 0, 0
    for it in pack["items"]:
        st = it["service_type"]
        log = logs.get(st)
        from_log = log is not None
        anchor_km = (log["done_odometer_km"] if from_log and log["done_odometer_km"] is not None else bkm)
        anchor_date = (_parse_date(log["done_date"]) if from_log else bdate)

        ikm = it.get("interval_km")
        imo = it.get("interval_months")
        mode = it.get("trigger_mode", "or")

        rem_km = km_pct = None
        if ikm and anchor_km is not None and current_km is not None:
            rem_km = (anchor_km + ikm) - current_km
            km_pct = max(0, min(120, round((current_km - anchor_km) / ikm * 100)))
        rem_days = time_pct = None
        if imo and anchor_date is not None:
            rem_days = (_add_months(anchor_date, imo) - today).days
            time_pct = max(0, min(120, round((today - anchor_date).days / (imo * _DAYS_PER_MONTH) * 100)))

        axes = []
        if mode in ("or", "and", "km") and rem_km is not None:
            axes.append(("km", rem_km, km_pct))
        if mode in ("or", "and", "time") and rem_days is not None:
            axes.append(("time", rem_days, time_pct))

        if not axes:
            status = "unknown"
        else:
            over = [a for a in axes if a[1] is not None and a[1] <= 0]
            is_over = (len(over) == len(axes)) if mode == "and" else (len(over) > 0)
            if is_over:
                status = "overdue"
            elif any((a[0] == "km" and a[1] <= SOON_KM) or (a[0] == "time" and a[1] <= SOON_DAYS) for a in axes):
                status = "soon"
            else:
                status = "ok"

        head = None
        if axes:
            head = min(over, key=lambda a: a[1]) if status == "overdue" \
                else max(axes, key=lambda a: (a[2] if a[2] is not None else 0))

        next_text = ""
        if head:
            axis, rem, _pct = head
            p = _p(lang)
            if status == "overdue":
                mag = _dist(-rem, lang, system) if axis == "km" else _dur(-rem, lang)
                next_text = p["overdue"] + mag
            else:
                mag = _dist(rem, lang, system) if axis == "km" else _dur(rem, lang)
                next_text = (p["first"] if not from_log else p["in_"]) + mag

        if from_log:
            km_part = f" · {_dist(log['done_odometer_km'], lang, system)}" if log["done_odometer_km"] is not None else ""
            last_text = f"{str(log['done_date'])[:10]}{km_part}"
        else:
            last_text = _loc(_CHROME["from_delivery"], lang)

        cat = _CATS.get(it.get("category", ""), {"en": it.get("category", ""), "it": it.get("category", ""), "icon": "🔧"})
        items.append({
            "service_type": st,
            "label": _loc(_LABELS.get(st, {"en": it.get("label", st)}), lang),
            "category": _loc(cat, lang),
            "icon": cat.get("icon", "🔧"),
            "status": status,
            "priority": it.get("priority", "routine"),
            "next_text": next_text,
            "interval_text": _interval_text(it, lang, system),
            "last_text": last_text,
            "from_log": from_log,
            "km_pct": km_pct,
            "time_pct": time_pct,
        })
        n_over += status == "overdue"
        n_soon += status == "soon"
        n_ok += status in ("ok", "unknown")

    order = {"overdue": 0, "soon": 1, "ok": 2, "unknown": 3}
    items.sort(key=lambda x: (order.get(x["status"], 9), x["category"]))

    return {
        "has_pack": True,
        "verified": bool(pack.get("verified")),
        "car_type": car_type,
        "summary": {"overdue": n_over, "soon": n_soon, "ok": n_ok, "total": len(items)},
        "rows": items,   # NOT "items": Jinja's maint.items resolves to dict.items() (a method)
        "attention": [i for i in items if i["status"] in ("overdue", "soon")],
        "baseline_date": bdate_s,
        "baseline_explicit": b_explicit,
        "baseline_km_input": round(units.dist_val(bkm, 0, system)) if bkm is not None else 0,
        "current_km_disp": units.dist(current_km, 0) if current_km is not None else None,
        "current_km_input": units.dist_val(current_km, 0, system) if current_km is not None else None,
        "dist_unit": units.dist_unit(system),
    }
