import os
import json
import logging
import base64
import httpx
from datetime import datetime, date
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, BotCommand
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ContextTypes
from apscheduler.schedulers.asyncio import AsyncIOScheduler

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

TOKEN = os.environ.get("BOT_TOKEN", "YOUR_BOT_TOKEN_HERE")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "YOUR_GEMINI_KEY_HERE")

async def read_sheet_with_gemini(image_bytes: bytes) -> list:
    image_b64 = base64.standard_b64encode(image_bytes).decode("utf-8")
    prompt = """Tu es un assistant médical. Lis cette feuille de suivi hospitalier manuscrite.
Extrais uniquement les patients qui ont des données valides.
Retourne UNIQUEMENT un JSON valide, sans texte avant ou après, sans markdown.
Format exact:
[
  {
    "nomPrenom": "Nom complet",
    "date": "JJ/MM/AAAA ou vide",
    "nOrdonnance": "numéro ou vide",
    "nbreGTT": "nombre ou vide",
    "prochainFlacon": "JJ/MM/AAAA ou vide"
  }
]
Si une valeur n'est pas lisible, mets une chaîne vide "".
Ne retourne rien d'autre que le JSON."""

    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key={GEMINI_API_KEY}"
    async with httpx.AsyncClient(timeout=60) as client:
        response = await client.post(url, json={
            "contents": [{
                "parts": [
                    {"inline_data": {"mime_type": "image/jpeg", "data": image_b64}},
                    {"text": prompt}
                ]
            }]
        })
        result = response.json()
        text = result["candidates"][0]["content"]["parts"][0]["text"].strip()
        text = text.replace("```json", "").replace("```", "").strip()
        patients = json.loads(text)
        converted = []
        for p in patients:
            for field in ["date", "prochainFlacon"]:
                val = p.get(field, "")
                if val:
                    try:
                        d = datetime.strptime(val, "%d/%m/%Y")
                        p[field] = d.strftime("%Y-%m-%d")
                    except:
                        p[field] = ""
            converted.append(p)
        return converted

def filter_patients(patients):
    seen = {}
    for p in patients:
        name = p.get("nomPrenom", "").strip().lower()
        pf = p.get("prochainFlacon", "")
        if name not in seen:
            seen[name] = p
        else:
            existing_pf = seen[name].get("prochainFlacon", "")
            if pf and (not existing_pf or pf > existing_pf):
                seen[name] = p
    result = []
    for p in seen.values():
        pf = p.get("prochainFlacon", "")
        if pf:
            d = days_until_simple(pf)
            if d is not None and d < -3:
                continue
        result.append(p)
    return result

def days_until_simple(date_str):
    try:
        return (datetime.strptime(date_str, "%Y-%m-%d").date() - date.today()).days
    except:
        return None

UNITS = ["PUA", "PUB", "PUC", "PUF", "PBF", "AHA", "AHB", "AHC"]
UNIT_PINS = {
    "PUA": "0101", "PUB": "0202", "PUC": "0202",
    "PUF": "0001", "PBF": "0002",
    "AHA": "0103", "AHB": "0104", "AHC": "0105"
}
ADMIN_PIN = "2510"
PRODUCTS = ["Largactil GTT", "Nozinan GTT", "Risperdal GTT"]
PRODUCT_ICONS = {"Largactil GTT": "💊", "Nozinan GTT": "💉", "Risperdal GTT": "🔬"}
DATA_FILE = "data.json"
USERS_FILE = "users.json"

# ─── Data ─────────────────────────────────────────────────────────────────────

def load_data():
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            d = json.load(f)
            for u in UNITS:
                if u not in d:
                    d[u] = {p: [] for p in PRODUCTS}
                for p in PRODUCTS:
                    if p not in d[u]:
                        d[u][p] = []
            return d
    return {u: {p: [] for p in PRODUCTS} for u in UNITS}

def save_data(data):
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def load_users():
    if os.path.exists(USERS_FILE):
        with open(USERS_FILE, "r") as f:
            return json.load(f)
    return {}

def save_users(users):
    with open(USERS_FILE, "w") as f:
        json.dump(users, f)

def register_user(chat_id):
    users = load_users()
    users[str(chat_id)] = True
    save_users(users)

def days_until(date_str):
    if not date_str:
        return None
    try:
        return (datetime.strptime(date_str, "%Y-%m-%d").date() - date.today()).days
    except:
        return None

def flacon_badge(date_str):
    d = days_until(date_str)
    if d is None: return ""
    if d < 0: return f"🔴 En retard de {abs(d)}j"
    if d == 0: return "🟡 Aujourd'hui !"
    if d <= 3: return f"🟠 Dans {d}j"
    return f"🟢 Dans {d}j"

def ordonnance_status(start_date_str, prochain_date_str):
    if not start_date_str or not prochain_date_str:
        return ""
    try:
        start = datetime.strptime(start_date_str, "%Y-%m-%d").date()
        prochain = datetime.strptime(prochain_date_str, "%Y-%m-%d").date()
        days = (prochain - start).days
        if days <= 7:
            return "📋 Même ordonnance"
        else:
            return "🆕 Nouvelle ordonnance requise"
    except:
        return ""

# ─── Sessions ─────────────────────────────────────────────────────────────────

sessions = {}

def new_session():
    return {
        "state": "start",
        "is_admin": False,
        "unit": None,
        "product": None,
        "form": {},
        "edit_idx": None,
        "page": 0,
        "list_action": None
    }

def get_session(chat_id):
    if chat_id not in sessions:
        sessions[chat_id] = new_session()
    return sessions[chat_id]

# ─── Keyboards ────────────────────────────────────────────────────────────────

def kb_start():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("👁  Consultation", callback_data="role_user")],
        [InlineKeyboardButton("🔑  Administrateur", callback_data="role_admin")]
    ])

def kb_units():
    rows = []
    for i in range(0, len(UNITS), 4):
        rows.append([InlineKeyboardButton(u, callback_data=f"unit_{u}") for u in UNITS[i:i+4]])
    rows.append([InlineKeyboardButton("⬅️ Retour", callback_data="back_start")])
    return InlineKeyboardMarkup(rows)

def kb_products(back_cb="back_units"):
    rows = [[InlineKeyboardButton(f"{PRODUCT_ICONS[p]}  {p}", callback_data=f"prod_{p}")] for p in PRODUCTS]
    rows.append([InlineKeyboardButton("⬅️ Retour", callback_data=back_cb)])
    return InlineKeyboardMarkup(rows)

def kb_admin_options():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📸  Nouvelle liste via photo", callback_data="admin_photo_list")],
        [InlineKeyboardButton("📋  Nouvelle liste manuelle", callback_data="admin_new_list")],
        [InlineKeyboardButton("➕  Ajouter un patient", callback_data="add_patient")],
        [InlineKeyboardButton("⬅️ Changer médicament", callback_data="back_products")]
    ])

def kb_patient_list(unit, product, is_admin, page=0):
    data = load_data()
    patients = data[unit][product]
    rows = []
    if is_admin:
        rows.append([InlineKeyboardButton("⚙️  Options", callback_data="admin_options")])
    start = page * 5
    for i, p in enumerate(patients[start:start+5]):
        idx = start + i
        d = days_until(p.get("prochainFlacon", ""))
        icon = "🔴" if d is not None and d < 0 else "🟠" if d is not None and d <= 3 else "👤"
        rows.append([InlineKeyboardButton(f"{icon}  {p['nomPrenom']}", callback_data=f"view_{idx}")])
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("◀️", callback_data=f"page_{page-1}"))
    if start + 5 < len(patients):
        nav.append(InlineKeyboardButton("▶️", callback_data=f"page_{page+1}"))
    if nav:
        rows.append(nav)
    rows.append([InlineKeyboardButton("⬅️ Changer médicament", callback_data="back_products")])
    rows.append([InlineKeyboardButton("🏠  Accueil", callback_data="back_start")])
    return InlineKeyboardMarkup(rows)

def kb_patient_view(is_admin, idx):
    rows = []
    if is_admin:
        rows.append([
            InlineKeyboardButton("✏️  Modifier", callback_data=f"edit_{idx}"),
            InlineKeyboardButton("🗑️  Supprimer", callback_data=f"delete_{idx}")
        ])
    rows.append([InlineKeyboardButton("⬅️  Retour liste", callback_data="back_list")])
    return InlineKeyboardMarkup(rows)

def kb_confirm_delete(idx):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✅  Confirmer suppression", callback_data=f"confirm_delete_{idx}")],
        [InlineKeyboardButton("❌  Annuler", callback_data=f"view_{idx}")]
    ])

def kb_cancel():
    return InlineKeyboardMarkup([[InlineKeyboardButton("❌  Annuler", callback_data="back_list")]])

def kb_confirm_clear():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✅  Oui, effacer et recommencer", callback_data="confirm_clear")],
        [InlineKeyboardButton("❌  Annuler", callback_data="back_list")]
    ])

def kb_pending_confirm(total):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(f"✅  Confirmer ({total} patients)", callback_data="pending_confirm")],
        [InlineKeyboardButton("✏️  Modifier un patient", callback_data="pending_edit_select")],
        [InlineKeyboardButton("🗑️  Supprimer un patient", callback_data="pending_delete_select")],
        [InlineKeyboardButton("❌  Annuler tout", callback_data="back_list")]
    ])

def kb_pending_select(patients, action):
    rows = []
    for i, p in enumerate(patients):
        rows.append([InlineKeyboardButton(f"{i+1}. {p['nomPrenom']}", callback_data=f"pending_{action}_{i}")])
    rows.append([InlineKeyboardButton("⬅️ Retour", callback_data="pending_review")])
    return InlineKeyboardMarkup(rows)

def txt_pending_list(patients):
    lines = ["📋 *Patients extraits de la photo :*\n"]
    for i, p in enumerate(patients):
        pf = ""
        if p.get("prochainFlacon"):
            try: pf = datetime.strptime(p["prochainFlacon"], "%Y-%m-%d").strftime("%d/%m/%Y")
            except: pf = p["prochainFlacon"]
        dt = ""
        if p.get("date"):
            try: dt = datetime.strptime(p["date"], "%Y-%m-%d").strftime("%d/%m/%Y")
            except: dt = p["date"]
        lines.append(f"*{i+1}. {p.get('nomPrenom','—')}*")
        if dt: lines.append(f"   📅 {dt}")
        if p.get("nOrdonnance"): lines.append(f"   📋 {p['nOrdonnance']}")
        if p.get("nbreGTT"): lines.append(f"   💧 {p['nbreGTT']} GTT/j")
        if pf: lines.append(f"   🔮 Prochain : {pf}")
        lines.append("")
    return "\n".join(lines)

# ─── Text builders ────────────────────────────────────────────────────────────

def txt_welcome():
    return "🏥 *H24TR — Suivi des Unités Hospitalières*\n\nChoisissez votre rôle :"

def txt_list_header(unit, product, is_admin):
    data = load_data()
    patients = data[unit][product]
    alerts = sum(1 for p in patients if days_until(p.get("prochainFlacon","")) is not None and days_until(p.get("prochainFlacon","")) <= 3)
    icon = PRODUCT_ICONS[product]
    lines = [
        f"🏥 *Unité {unit}*  •  {icon} *{product}*",
        f"👥 {len(patients)} patient(s)",
    ]
    if alerts:
        lines.append(f"⚠️ {alerts} alerte(s) flacon")
    lines.append(f"{'👤 Admin' if is_admin else '👁 Consultation'}  •  📅 {date.today().strftime('%d/%m/%Y')}")
    if len(patients) == 0:
        lines.append("\n_Aucun patient enregistré._")
    return "\n".join(lines)

def txt_patient_card(p):
    sep = "─────────────────────"
    lines = [sep, f"👤  *{p.get('nomPrenom','—').upper()}*", sep]
    if p.get("date"):
        try: d = datetime.strptime(p["date"], "%Y-%m-%d").strftime("%d/%m/%Y")
        except: d = p["date"]
        lines.append(f"📅  *Date :*  {d}")
    if p.get("nOrdonnance"):
        lines.append(f"📋  *N° Ordonnance :*  {p['nOrdonnance']}")
    if p.get("nbreGTT"):
        lines.append(f"💧  *Nbre de GTT :*  {p['nbreGTT']}")
    if p.get("prochainFlacon"):
        try: fd = datetime.strptime(p["prochainFlacon"], "%Y-%m-%d").strftime("%d/%m/%Y")
        except: fd = p["prochainFlacon"]
        lines.append(f"🔮  *Prochain flacon :*  {fd}")
        lines.append(f"      {flacon_badge(p['prochainFlacon'])}")
        ord_status = ordonnance_status(p.get("date",""), p.get("prochainFlacon",""))
        if ord_status:
            lines.append(f"      {ord_status}")
    if p.get("notes"):
        lines.append(f"📝  *Notes :*  {p['notes']}")
    lines.append(sep)
    return "\n".join(lines)

# ─── Notifications ────────────────────────────────────────────────────────────

async def send_morning_notifications(app):
    data = load_data()
    users = load_users()
    today_str = date.today().strftime("%Y-%m-%d")
    unit_alerts = {}
    for unit in UNITS:
        for product in PRODUCTS:
            for p in data[unit][product]:
                if p.get("prochainFlacon") == today_str:
                    if unit not in unit_alerts:
                        unit_alerts[unit] = []
                    ord_status = ordonnance_status(p.get("date",""), p.get("prochainFlacon",""))
                    unit_alerts[unit].append(f"{PRODUCT_ICONS[product]} {product} — {ord_status}")
    if not unit_alerts:
        return
    msg_lines = ["⏰ *Rappel flacons — 7h00*\n"]
    for unit, items in unit_alerts.items():
        msg_lines.append(f"🏥 *Unité {unit}*")
        for item in items:
            msg_lines.append(f"   • {item}")
        msg_lines.append("")
    msg_lines.append("_Connectez-vous avec votre code pour voir les détails._")
    msg = "\n".join(msg_lines)
    for chat_id in users:
        try:
            await app.bot.send_message(chat_id=int(chat_id), text=msg, parse_mode="Markdown")
        except Exception as e:
            logger.warning(f"Could not notify {chat_id}: {e}")

# ─── Handlers ─────────────────────────────────────────────────────────────────

async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    sessions[chat_id] = new_session()
    register_user(chat_id)
    await update.message.reply_text(txt_welcome(), parse_mode="Markdown", reply_markup=kb_start())

async def safe_edit(query, text, keyboard):
    try:
        await query.edit_message_text(text, parse_mode="Markdown", reply_markup=keyboard)
    except Exception as e:
        logger.warning(f"edit failed: {e}")

async def handle_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    chat_id = query.message.chat_id
    cb = query.data
    s = get_session(chat_id)

    if cb == "back_start":
        s.update(new_session())
        await safe_edit(query, txt_welcome(), kb_start())
        return

    if cb == "role_user":
        s["is_admin"] = False
        s["state"] = "select_unit"
        await safe_edit(query, "👁  *Consultation*\n\nChoisissez votre unité :", kb_units())
        return

    if cb == "role_admin":
        s["state"] = "await_admin_pin"
        await safe_edit(query, "🔑  *Connexion Admin*\n\nEntrez votre code administrateur :", None)
        return

    if cb == "back_units":
        s["unit"] = None
        s["state"] = "select_unit"
        title = "👤  *Admin*\n\n" if s["is_admin"] else "👁  *Consultation*\n\n"
        await safe_edit(query, f"{title}Choisissez votre unité :", kb_units())
        return

    if cb.startswith("unit_"):
        unit = cb[5:]
        s["unit"] = unit
        s["page"] = 0
        if s["is_admin"]:
            s["state"] = "select_product"
            await safe_edit(query, f"🏥  *Unité {unit}*\n\nChoisissez le médicament :", kb_products("back_units"))
        else:
            s["state"] = "await_unit_pin"
            await safe_edit(query, f"🏥  *Unité {unit}*\n\n🔑  Entrez le code de l'unité :", None)
        return

    if cb == "back_products":
        s["product"] = None
        s["state"] = "select_product"
        await safe_edit(query, f"🏥  *Unité {s['unit']}*\n\nChoisissez le médicament :", kb_products("back_units"))
        return

    if cb.startswith("prod_"):
        product = cb[5:]
        s["product"] = product
        s["page"] = 0
        s["state"] = "view_list"
        unit = s["unit"]
        await safe_edit(query, txt_list_header(unit, product, s["is_admin"]), kb_patient_list(unit, product, s["is_admin"], 0))
        return

    if cb == "back_list":
        unit = s["unit"]
        product = s["product"]
        s["state"] = "view_list"
        await safe_edit(query, txt_list_header(unit, product, s["is_admin"]), kb_patient_list(unit, product, s["is_admin"], s.get("page", 0)))
        return

    if cb.startswith("page_"):
        page = int(cb[5:])
        s["page"] = page
        unit = s["unit"]
        product = s["product"]
        await safe_edit(query, txt_list_header(unit, product, s["is_admin"]), kb_patient_list(unit, product, s["is_admin"], page))
        return

    if cb.startswith("view_"):
        idx = int(cb[5:])
        unit = s["unit"]
        product = s["product"]
        data = load_data()
        patients = data[unit][product]
        if idx >= len(patients):
            await query.answer("Patient introuvable.", show_alert=True)
            return
        await safe_edit(query, txt_patient_card(patients[idx]), kb_patient_view(s["is_admin"], idx))
        return

    if cb == "admin_options":
        await safe_edit(query, f"🏥 *Unité {s['unit']}*\n{PRODUCT_ICONS[s['product']]} *{s['product']}*\n\nQue voulez-vous faire ?", kb_admin_options())
        return

    if cb == "admin_photo_list":
        s["state"] = "await_photo"
        s["pending_patients"] = []
        await safe_edit(query, "📸  *Envoyez la photo de la feuille de suivi*\n\nJe vais lire et extraire les patients automatiquement.", kb_cancel())
        return

    if cb == "admin_new_list":
        data = load_data()
        count = len(data[s["unit"]][s["product"]])
        if count > 0:
            await safe_edit(query, f"⚠️ *Attention !*\n\nCette action va effacer les *{count} patient(s)* existants et recommencer à zéro.\n\nConfirmer ?", kb_confirm_clear())
        else:
            s["state"] = "form_nom"
            s["form"] = {}
            s["edit_idx"] = None
            s["list_action"] = "new_list"
            await safe_edit(query, "📋  *Nouvelle liste*\n\n👤  Entrez le *Nom et Prénom* du premier patient :", kb_cancel())
        return

    if cb == "confirm_clear":
        data = load_data()
        data[s["unit"]][s["product"]] = []
        save_data(data)
        s["state"] = "form_nom"
        s["form"] = {}
        s["edit_idx"] = None
        s["list_action"] = "new_list"
        await safe_edit(query, "✅  Liste effacée.\n\n👤  Entrez le *Nom et Prénom* du premier patient :", kb_cancel())
        return

    if cb == "pending_review":
        patients = s.get("pending_patients", [])
        await safe_edit(query, txt_pending_list(patients), kb_pending_confirm(len(patients)))
        return

    if cb == "pending_confirm":
        patients = s.get("pending_patients", [])
        unit = s["unit"]
        product = s["product"]
        data = load_data()
        data[unit][product] = []
        for p in patients:
            p["id"] = int(datetime.now().timestamp())
            data[unit][product].append(p)
        save_data(data)
        s["pending_patients"] = []
        s["state"] = "view_list"
        s["page"] = 0
        await safe_edit(query, f"✅  *{len(patients)} patient(s) enregistré(s) !*\n\n{txt_list_header(unit, product, True)}", kb_patient_list(unit, product, True, 0))
        return

    if cb == "pending_edit_select":
        patients = s.get("pending_patients", [])
        await safe_edit(query, "✏️  *Quel patient voulez-vous modifier ?*", kb_pending_select(patients, "edit"))
        return

    if cb == "pending_delete_select":
        patients = s.get("pending_patients", [])
        await safe_edit(query, "🗑️  *Quel patient voulez-vous supprimer ?*", kb_pending_select(patients, "delete"))
        return

    if cb.startswith("pending_edit_"):
        idx = int(cb[13:])
        s["pending_edit_idx"] = idx
        s["state"] = "pending_form_nom"
        s["form"] = dict(s["pending_patients"][idx])
        p = s["pending_patients"][idx]
        await safe_edit(query, f"✏️  *Modifier*\n\nNom actuel : *{p.get('nomPrenom','—')}*\n\n👤  Nouveau *Nom et Prénom* :", kb_cancel())
        return

    if cb.startswith("pending_delete_"):
        idx = int(cb[15:])
        nom = s["pending_patients"][idx].get("nomPrenom", "—")
        s["pending_patients"].pop(idx)
        patients = s["pending_patients"]
        if not patients:
            await safe_edit(query, "✅  Patient supprimé. Liste vide.", kb_cancel())
            return
        await safe_edit(query, f"✅  *{nom}* retiré.\n\n{txt_pending_list(patients)}", kb_pending_confirm(len(patients)))
        return

    if cb == "add_patient":
        s["state"] = "form_nom"
        s["form"] = {}
        s["edit_idx"] = None
        s["list_action"] = None
        await safe_edit(query, "➕  *Nouveau patient*\n\n👤  Entrez le *Nom et Prénom* :", kb_cancel())
        return

    if cb.startswith("edit_"):
        idx = int(cb[5:])
        data = load_data()
        p = data[s["unit"]][s["product"]][idx]
        s["state"] = "form_nom"
        s["form"] = dict(p)
        s["edit_idx"] = idx
        await safe_edit(query, f"✏️  *Modifier patient*\n\nNom actuel : *{p.get('nomPrenom','—')}*\n\n👤  Nouveau *Nom et Prénom* :", kb_cancel())
        return

    if cb.startswith("delete_"):
        idx = int(cb[7:])
        data = load_data()
        p = data[s["unit"]][s["product"]][idx]
        await safe_edit(query, f"🗑️  *Supprimer ce patient ?*\n\n👤  *{p.get('nomPrenom','—')}*\n\n⚠️  Cette action est irréversible.", kb_confirm_delete(idx))
        return

    if cb.startswith("confirm_delete_"):
        idx = int(cb[15:])
        unit = s["unit"]
        product = s["product"]
        data = load_data()
        nom = data[unit][product][idx].get("nomPrenom", "—")
        data[unit][product].pop(idx)
        save_data(data)
        s["state"] = "view_list"
        s["page"] = 0
        await safe_edit(query, f"✅  *{nom}* supprimé.\n\n{txt_list_header(unit, product, True)}", kb_patient_list(unit, product, True, 0))
        return

async def handle_message(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    s = get_session(chat_id)
    state = s.get("state", "start")

    # ── Photo handler ──
    if update.message.photo and state == "await_photo":
        await update.message.reply_text("📸  *Photo reçue. Lecture en cours...*\n\n⏳ Veuillez patienter.", parse_mode="Markdown")
        try:
            photo = update.message.photo[-1]
            file = await ctx.bot.get_file(photo.file_id)
            image_bytes = await file.download_as_bytearray()
            patients_raw = await read_sheet_with_gemini(bytes(image_bytes))
            patients_filtered = filter_patients(patients_raw)
            if not patients_filtered:
                await update.message.reply_text("⚠️  Aucun patient valide trouvé. Réessayez avec une image plus nette.")
                return
            s["pending_patients"] = patients_filtered
            s["state"] = "pending_review"
            await update.message.reply_text(
                txt_pending_list(patients_filtered),
                parse_mode="Markdown",
                reply_markup=kb_pending_confirm(len(patients_filtered))
            )
        except Exception as e:
            logger.error(f"Gemini error: {e}")
            await update.message.reply_text("❌  Erreur lors de la lecture. Réessayez.")
        return

    if not update.message.text:
        return
    text = update.message.text.strip()

    if state == "await_admin_pin":
        if text == ADMIN_PIN:
            s["is_admin"] = True
            s["state"] = "select_unit"
            await update.message.reply_text("✅  *Accès admin accordé !*\n\nChoisissez votre unité :", parse_mode="Markdown", reply_markup=kb_units())
        else:
            await update.message.reply_text("❌  Code incorrect. Réessayez :")
        return

    if state == "await_unit_pin":
        unit = s["unit"]
        if text == ADMIN_PIN:
            s["is_admin"] = True
            s["state"] = "select_product"
            await update.message.reply_text(f"✅  *Accès admin — Unité {unit}*\n\nChoisissez le médicament :", parse_mode="Markdown", reply_markup=kb_products("back_units"))
        elif text == UNIT_PINS.get(unit, ""):
            s["state"] = "select_product"
            await update.message.reply_text(f"✅  *Unité {unit}*\n\nChoisissez le médicament :", parse_mode="Markdown", reply_markup=kb_products("back_units"))
        else:
            await update.message.reply_text("❌  Code incorrect. Réessayez :")
        return

    if state == "form_nom":
        s["form"]["nomPrenom"] = text
        s["state"] = "form_ord"
        await update.message.reply_text("📋  *N° Ordonnance*\n\n(ou `-` pour ignorer) :", parse_mode="Markdown", reply_markup=kb_cancel())
        return

    if state == "form_ord":
        s["form"]["nOrdonnance"] = "" if text == "-" else text
        s["state"] = "form_date"
        await update.message.reply_text("📅  *Date de début*\n\nFormat JJ/MM/AAAA (ou `-`) :", parse_mode="Markdown", reply_markup=kb_cancel())
        return

    if state == "form_date":
        if text != "-":
            try:
                s["form"]["date"] = datetime.strptime(text, "%d/%m/%Y").strftime("%Y-%m-%d")
            except:
                await update.message.reply_text("⚠️  Format invalide ! JJ/MM/AAAA ou `-` :", reply_markup=kb_cancel())
                return
        else:
            s["form"]["date"] = ""
        s["state"] = "form_gtt"
        await update.message.reply_text("💧  *Nombre de GTT*\n\n(ou `-`) :", parse_mode="Markdown", reply_markup=kb_cancel())
        return

    if state == "form_gtt":
        s["form"]["nbreGTT"] = "" if text == "-" else text
        s["state"] = "form_flacon"
        await update.message.reply_text("🔮  *Date prochain flacon*\n\nFormat JJ/MM/AAAA (ou `-`) :", parse_mode="Markdown", reply_markup=kb_cancel())
        return

    if state == "form_flacon":
        if text != "-":
            try:
                s["form"]["prochainFlacon"] = datetime.strptime(text, "%d/%m/%Y").strftime("%Y-%m-%d")
            except:
                await update.message.reply_text("⚠️  Format invalide ! JJ/MM/AAAA ou `-` :", reply_markup=kb_cancel())
                return
        else:
            s["form"]["prochainFlacon"] = ""
        s["state"] = "form_notes"
        await update.message.reply_text("📝  *Notes*\n\n(ou `-`) :", parse_mode="Markdown", reply_markup=kb_cancel())
        return

    if state == "form_notes":
        s["form"]["notes"] = "" if text == "-" else text
        saved = s["form"].copy()
        data = load_data()
        unit = s["unit"]
        product = s["product"]
        edit_idx = s.get("edit_idx")
        if edit_idx is not None:
            data[unit][product][edit_idx] = {**data[unit][product][edit_idx], **saved}
            action = "modifié"
        else:
            saved["id"] = int(datetime.now().timestamp())
            data[unit][product].append(saved)
            action = "ajouté"
        save_data(data)
        s["form"] = {}
        s["edit_idx"] = None

        # If adding new list, ask for next patient
        if s.get("list_action") == "new_list":
            s["state"] = "form_nom"
            s["form"] = {}
            await update.message.reply_text(
                f"✅  *{saved.get('nomPrenom','—')}* ajouté !\n\n{txt_patient_card(saved)}\n\n👤  Patient suivant — *Nom et Prénom*\n(ou tapez `fin` pour terminer) :",
                parse_mode="Markdown",
                reply_markup=kb_cancel()
            )
        else:
            s["state"] = "view_list"
            s["page"] = 0
            await update.message.reply_text(f"✅  *Patient {action} !*\n\n{txt_patient_card(saved)}", parse_mode="Markdown")
            await update.message.reply_text(txt_list_header(unit, product, True), parse_mode="Markdown", reply_markup=kb_patient_list(unit, product, True, 0))
        return

    # Handle "fin" to stop adding patients in new list mode
    if state == "form_nom" and text.lower() == "fin" and s.get("list_action") == "new_list":
        s["list_action"] = None
        s["state"] = "view_list"
        s["page"] = 0
        unit = s["unit"]
        product = s["product"]
        await update.message.reply_text(
            f"✅  *Liste enregistrée !*\n\n{txt_list_header(unit, product, True)}",
            parse_mode="Markdown",
            reply_markup=kb_patient_list(unit, product, True, 0)
        )
        return

    # ── Pending form (editing extracted patient) ──
    if state == "pending_form_nom":
        s["form"]["nomPrenom"] = text
        s["state"] = "pending_form_ord"
        await update.message.reply_text("📋  *N° Ordonnance* (ou `-`) :", parse_mode="Markdown", reply_markup=kb_cancel())
        return

    if state == "pending_form_ord":
        s["form"]["nOrdonnance"] = "" if text == "-" else text
        s["state"] = "pending_form_date"
        await update.message.reply_text("📅  *Date* JJ/MM/AAAA (ou `-`) :", parse_mode="Markdown", reply_markup=kb_cancel())
        return

    if state == "pending_form_date":
        if text != "-":
            try:
                s["form"]["date"] = datetime.strptime(text, "%d/%m/%Y").strftime("%Y-%m-%d")
            except:
                await update.message.reply_text("⚠️  Format invalide ! JJ/MM/AAAA ou `-` :", reply_markup=kb_cancel())
                return
        else:
            s["form"]["date"] = ""
        s["state"] = "pending_form_gtt"
        await update.message.reply_text("💧  *Nbre de GTT* (ou `-`) :", parse_mode="Markdown", reply_markup=kb_cancel())
        return

    if state == "pending_form_gtt":
        s["form"]["nbreGTT"] = "" if text == "-" else text
        s["state"] = "pending_form_flacon"
        await update.message.reply_text("🔮  *Date prochain flacon* JJ/MM/AAAA (ou `-`) :", parse_mode="Markdown", reply_markup=kb_cancel())
        return

    if state == "pending_form_flacon":
        if text != "-":
            try:
                s["form"]["prochainFlacon"] = datetime.strptime(text, "%d/%m/%Y").strftime("%Y-%m-%d")
            except:
                await update.message.reply_text("⚠️  Format invalide ! JJ/MM/AAAA ou `-` :", reply_markup=kb_cancel())
                return
        else:
            s["form"]["prochainFlacon"] = ""
        s["state"] = "pending_form_notes"
        await update.message.reply_text("📝  *Notes* (ou `-`) :", parse_mode="Markdown", reply_markup=kb_cancel())
        return

    if state == "pending_form_notes":
        s["form"]["notes"] = "" if text == "-" else text
        idx = s.get("pending_edit_idx")
        s["pending_patients"][idx] = s["form"].copy()
        s["form"] = {}
        s["pending_edit_idx"] = None
        s["state"] = "pending_review"
        patients = s["pending_patients"]
        await update.message.reply_text(
            f"✅  Modifié !\n\n{txt_pending_list(patients)}",
            parse_mode="Markdown",
            reply_markup=kb_pending_confirm(len(patients))
        )
        return

    await update.message.reply_text("Tapez /start pour commencer. 🏥")

async def post_init(app):
    await app.bot.set_my_commands([BotCommand("start", "Démarrer / Accueil")])
    scheduler = AsyncIOScheduler(timezone="Africa/Algiers")
    scheduler.add_job(send_morning_notifications, "cron", hour=7, minute=0, args=[app])
    scheduler.start()
    logger.info("Scheduler started.")

def main():
    application = Application.builder().token(TOKEN).post_init(post_init).build()
    application.add_handler(CommandHandler("start", cmd_start))
    application.add_handler(CallbackQueryHandler(handle_callback))
    application.add_handler(MessageHandler(filters.PHOTO, handle_message))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    logger.info("Bot H24TR started...")
    application.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)

if __name__ == "__main__":
    main()
