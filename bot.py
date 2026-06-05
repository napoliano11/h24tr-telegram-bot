import os
import json
import logging
from datetime import datetime, date
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ContextTypes

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

TOKEN = os.environ.get("BOT_TOKEN", "YOUR_BOT_TOKEN_HERE")

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

# ─── Data ────────────────────────────────────────────────────────────────────

def load_data():
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    d = {}
    for u in UNITS:
        d[u] = {p: [] for p in PRODUCTS}
    return d

def save_data(data):
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def days_until(date_str):
    if not date_str:
        return None
    try:
        d = datetime.strptime(date_str, "%Y-%m-%d").date()
        return (d - date.today()).days
    except:
        return None

def flacon_badge(date_str):
    d = days_until(date_str)
    if d is None: return ""
    if d < 0: return f"🔴 En retard de {abs(d)} jour(s)"
    if d == 0: return "🟡 Aujourd'hui !"
    if d <= 3: return f"🟠 Dans {d} jour(s)"
    return f"🟢 Dans {d} jour(s)"

# ─── Sessions ────────────────────────────────────────────────────────────────

sessions = {}

def get_session(chat_id):
    if chat_id not in sessions:
        sessions[chat_id] = {
            "state": "start",
            "is_admin": False,
            "unit": None,
            "product": None,
            "form": {},
            "edit_idx": None,
            "page": 0
        }
    return sessions[chat_id]

# ─── Keyboards ───────────────────────────────────────────────────────────────

def start_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("👁 Consultation", callback_data="role_user")],
        [InlineKeyboardButton("🔑 Administrateur", callback_data="role_admin")]
    ])

def unit_select_keyboard():
    rows = []
    for i in range(0, len(UNITS), 4):
        rows.append([InlineKeyboardButton(u, callback_data=f"unit_{u}") for u in UNITS[i:i+4]])
    rows.append([InlineKeyboardButton("⬅️ Retour", callback_data="back_start")])
    return InlineKeyboardMarkup(rows)

def product_select_keyboard(back="back_unit"):
    rows = []
    for p in PRODUCTS:
        icon = PRODUCT_ICONS[p]
        rows.append([InlineKeyboardButton(f"{icon} {p}", callback_data=f"prod_{p}")])
    rows.append([InlineKeyboardButton("⬅️ Retour", callback_data=back)])
    return InlineKeyboardMarkup(rows)

def user_patient_list_keyboard(unit, product, page=0):
    data = load_data()
    patients = data.get(unit, {}).get(product, [])
    rows = []
    start = page * 5
    end = start + 5
    for i, p in enumerate(patients[start:end]):
        real_idx = start + i
        d = days_until(p.get("prochainFlacon", ""))
        icon = "🔴" if d is not None and d < 0 else "🟠" if d is not None and d <= 3 else "👤"
        rows.append([InlineKeyboardButton(f"{icon} {p['nomPrenom']}", callback_data=f"view_{real_idx}")])
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("◀️ Précédent", callback_data=f"page_{page-1}"))
    if end < len(patients):
        nav.append(InlineKeyboardButton("Suivant ▶️", callback_data=f"page_{page+1}"))
    if nav:
        rows.append(nav)
    rows.append([InlineKeyboardButton("⬅️ Changer médicament", callback_data="back_product")])
    rows.append([InlineKeyboardButton("🏠 Accueil", callback_data="back_start")])
    return InlineKeyboardMarkup(rows)

def admin_product_keyboard(unit, product, page=0):
    data = load_data()
    patients = data.get(unit, {}).get(product, [])
    rows = []
    rows.append([InlineKeyboardButton("➕ Ajouter un patient", callback_data="add_patient")])
    start = page * 5
    end = start + 5
    for i, p in enumerate(patients[start:end]):
        real_idx = start + i
        d = days_until(p.get("prochainFlacon", ""))
        icon = "🔴" if d is not None and d < 0 else "🟠" if d is not None and d <= 3 else "👤"
        rows.append([InlineKeyboardButton(f"{icon} {p['nomPrenom']}", callback_data=f"view_{real_idx}")])
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("◀️", callback_data=f"page_{page-1}"))
    if end < len(patients):
        nav.append(InlineKeyboardButton("▶️", callback_data=f"page_{page+1}"))
    if nav:
        rows.append(nav)
    rows.append([InlineKeyboardButton("⬅️ Changer médicament", callback_data="back_product")])
    rows.append([InlineKeyboardButton("🏠 Accueil", callback_data="back_start")])
    return InlineKeyboardMarkup(rows)

def patient_view_keyboard(is_admin, idx):
    rows = []
    if is_admin:
        rows.append([
            InlineKeyboardButton("✏️ Modifier", callback_data=f"edit_{idx}"),
            InlineKeyboardButton("🗑️ Supprimer", callback_data=f"delete_{idx}")
        ])
    rows.append([InlineKeyboardButton("⬅️ Retour liste", callback_data="back_product_list")])
    return InlineKeyboardMarkup(rows)

def confirm_delete_keyboard(idx):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Oui, supprimer", callback_data=f"confirm_delete_{idx}")],
        [InlineKeyboardButton("❌ Annuler", callback_data=f"view_{idx}")]
    ])

def cancel_keyboard():
    return InlineKeyboardMarkup([[InlineKeyboardButton("❌ Annuler la saisie", callback_data="back_product_list")]])

# ─── Message builders ─────────────────────────────────────────────────────────

def patient_card(p, idx=None):
    lines = []
    lines.append(f"━━━━━━━━━━━━━━━━━━━━━")
    lines.append(f"👤 *{p.get('nomPrenom', '—').upper()}*")
    lines.append(f"━━━━━━━━━━━━━━━━━━━━━")
    if p.get("date"):
        try:
            d = datetime.strptime(p["date"], "%Y-%m-%d").strftime("%d/%m/%Y")
        except:
            d = p["date"]
        lines.append(f"📅 *Date :* {d}")
    if p.get("nOrdonnance"):
        lines.append(f"📋 *N° Ordonnance :* {p['nOrdonnance']}")
    if p.get("nbreGTT"):
        lines.append(f"💧 *Nbre de GTT :* {p['nbreGTT']}")
    if p.get("prochainFlacon"):
        try:
            fd = datetime.strptime(p["prochainFlacon"], "%Y-%m-%d").strftime("%d/%m/%Y")
        except:
            fd = p["prochainFlacon"]
        badge = flacon_badge(p["prochainFlacon"])
        lines.append(f"🔮 *Prochain flacon :* {fd}")
        lines.append(f"   {badge}")
    if p.get("notes"):
        lines.append(f"📝 *Notes :* {p['notes']}")
    lines.append(f"━━━━━━━━━━━━━━━━━━━━━")
    return "\n".join(lines)

def product_header(unit, product, patients, is_admin):
    icon = PRODUCT_ICONS.get(product, "💊")
    alerts = sum(1 for p in patients if days_until(p.get("prochainFlacon","")) is not None and days_until(p.get("prochainFlacon","")) <= 3)
    role = "👤 Admin" if is_admin else "👁 Consultation"
    lines = [
        f"🏥 *Unité {unit}*",
        f"{icon} *{product}*",
        f"👥 *{len(patients)}* patient(s) enregistré(s)",
    ]
    if alerts:
        lines.append(f"⚠️ *{alerts}* alerte(s) flacon")
    lines.append(f"🔑 {role} — 📅 {date.today().strftime('%d/%m/%Y')}")
    return "\n".join(lines)

# ─── Handlers ─────────────────────────────────────────────────────────────────

async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    sessions[chat_id] = {
        "state": "start", "is_admin": False, "unit": None,
        "product": None, "form": {}, "edit_idx": None, "page": 0
    }
    await update.message.reply_text(
        "🏥 *H24TR — Suivi des Unités Hospitalières*\n\nBienvenue ! Choisissez votre rôle :",
        parse_mode="Markdown",
        reply_markup=start_keyboard()
    )

async def handle_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    chat_id = query.message.chat_id
    cb = query.data
    session = get_session(chat_id)

    # ── Role selection ──
    if cb == "role_user":
        session["is_admin"] = False
        session["state"] = "select_unit"
        await query.edit_message_text(
            "👁 *Mode Consultation*\n\nChoisissez votre unité :",
            parse_mode="Markdown",
            reply_markup=unit_select_keyboard()
        )
        return

    if cb == "role_admin":
        session["state"] = "await_admin_pin"
        await query.edit_message_text(
            "🔑 *Connexion Administrateur*\n\nEntrez votre code admin :",
            parse_mode="Markdown"
        )
        return

    # ── Back to start ──
    if cb == "back_start":
        session["state"] = "start"
        session["unit"] = None
        session["product"] = None
        await query.edit_message_text(
            "🏥 *H24TR — Suivi des Unités Hospitalières*\n\nBienvenue ! Choisissez votre rôle :",
            parse_mode="Markdown",
            reply_markup=start_keyboard()
        )
        return

    # ── Unit selection ──
    if cb.startswith("unit_"):
        unit = cb[5:]
        session["unit"] = unit
        session["page"] = 0
        if session.get("is_admin"):
            session["state"] = "select_product"
            await query.edit_message_text(
                f"🏥 *Unité {unit}*\n\nChoisissez le médicament :",
                parse_mode="Markdown",
                reply_markup=product_select_keyboard("back_unit")
            )
        else:
            session["state"] = "await_unit_pin"
            await query.edit_message_text(
                f"🏥 *Unité {unit}*\n\n🔑 Entrez le code de l'unité :",
                parse_mode="Markdown"
            )
        return

    if cb == "back_unit":
        session["state"] = "select_unit"
        await query.edit_message_text(
            "🏥 Choisissez votre unité :",
            parse_mode="Markdown",
            reply_markup=unit_select_keyboard()
        )
        return

    # ── Product selection ──
    if cb.startswith("prod_"):
        product = cb[5:]
        session["product"] = product
        session["page"] = 0
        session["state"] = "view_list"
        unit = session["unit"]
        data = load_data()
        patients = data.get(unit, {}).get(product, [])
        header = product_header(unit, product, patients, session["is_admin"])
        if session["is_admin"]:
            kb = admin_product_keyboard(unit, product, 0)
        else:
            kb = user_patient_list_keyboard(unit, product, 0)
        await query.edit_message_text(header, parse_mode="Markdown", reply_markup=kb)
        return

    if cb == "back_product":
        unit = session["unit"]
        session["state"] = "select_product"
        session["product"] = None
        await query.edit_message_text(
            f"🏥 *Unité {unit}*\n\nChoisissez le médicament :",
            parse_mode="Markdown",
            reply_markup=product_select_keyboard("back_unit")
        )
        return

    if cb == "back_product_list":
        unit = session["unit"]
        product = session["product"]
        session["state"] = "view_list"
        data = load_data()
        patients = data.get(unit, {}).get(product, [])
        header = product_header(unit, product, patients, session["is_admin"])
        if session["is_admin"]:
            kb = admin_product_keyboard(unit, product, session.get("page", 0))
        else:
            kb = user_patient_list_keyboard(unit, product, session.get("page", 0))
        await query.edit_message_text(header, parse_mode="Markdown", reply_markup=kb)
        return

    # ── Pagination ──
    if cb.startswith("page_"):
        page = int(cb[5:])
        session["page"] = page
        unit = session["unit"]
        product = session["product"]
        data = load_data()
        patients = data.get(unit, {}).get(product, [])
        header = product_header(unit, product, patients, session["is_admin"])
        if session["is_admin"]:
            kb = admin_product_keyboard(unit, product, page)
        else:
            kb = user_patient_list_keyboard(unit, product, page)
        await query.edit_message_text(header, parse_mode="Markdown", reply_markup=kb)
        return

    # ── View patient ──
    if cb.startswith("view_"):
        idx = int(cb[5:])
        unit = session["unit"]
        product = session["product"]
        data = load_data()
        patients = data.get(unit, {}).get(product, [])
        if idx >= len(patients):
            await query.answer("Patient introuvable.")
            return
        p = patients[idx]
        await query.edit_message_text(
            patient_card(p, idx),
            parse_mode="Markdown",
            reply_markup=patient_view_keyboard(session["is_admin"], idx)
        )
        return

    # ── Add patient ──
    if cb == "add_patient":
        session["state"] = "form_nom"
        session["form"] = {}
        session["edit_idx"] = None
        await query.edit_message_text(
            "➕ *Nouveau patient*\n\n👤 Entrez le *Nom et Prénom* du patient :",
            parse_mode="Markdown",
            reply_markup=cancel_keyboard()
        )
        return

    # ── Edit patient ──
    if cb.startswith("edit_"):
        idx = int(cb[5:])
        unit = session["unit"]
        product = session["product"]
        data = load_data()
        p = data[unit][product][idx]
        session["state"] = "form_nom"
        session["form"] = dict(p)
        session["edit_idx"] = idx
        await query.edit_message_text(
            f"✏️ *Modifier patient*\n\nNom actuel : *{p.get('nomPrenom','—')}*\n\n👤 Nouveau *Nom et Prénom* (ou envoyez le même) :",
            parse_mode="Markdown",
            reply_markup=cancel_keyboard()
        )
        return

    # ── Delete patient ──
    if cb.startswith("delete_"):
        idx = int(cb[7:])
        unit = session["unit"]
        product = session["product"]
        data = load_data()
        p = data[unit][product][idx]
        await query.edit_message_text(
            f"🗑️ *Supprimer ce patient ?*\n\n👤 *{p.get('nomPrenom','—')}*\n\nCette action est irréversible.",
            parse_mode="Markdown",
            reply_markup=confirm_delete_keyboard(idx)
        )
        return

    if cb.startswith("confirm_delete_"):
        idx = int(cb[15:])
        unit = session["unit"]
        product = session["product"]
        data = load_data()
        nom = data[unit][product][idx].get("nomPrenom", "—")
        data[unit][product].pop(idx)
        save_data(data)
        session["state"] = "view_list"
        patients = data[unit][product]
        header = product_header(unit, product, patients, True)
        await query.edit_message_text(
            f"✅ *{nom}* a été supprimé avec succès.\n\n{header}",
            parse_mode="Markdown",
            reply_markup=admin_product_keyboard(unit, product, 0)
        )
        return

async def handle_message(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    text = update.message.text.strip()
    session = get_session(chat_id)
    state = session.get("state", "start")

    # ── Admin PIN ──
    if state == "await_admin_pin":
        if text == ADMIN_PIN:
            session["is_admin"] = True
            session["state"] = "select_unit"
            await update.message.reply_text(
                "✅ *Accès admin accordé !*\n\nChoisissez votre unité :",
                parse_mode="Markdown",
                reply_markup=unit_select_keyboard()
            )
        else:
            await update.message.reply_text(
                "❌ Code incorrect. Réessayez ou tapez /start :",
                parse_mode="Markdown"
            )
        return

    # ── Unit PIN (user) ──
    if state == "await_unit_pin":
        unit = session.get("unit")
        if text == ADMIN_PIN:
            session["is_admin"] = True
            session["state"] = "select_product"
            await update.message.reply_text(
                f"✅ *Accès admin — Unité {unit}*\n\nChoisissez le médicament :",
                parse_mode="Markdown",
                reply_markup=product_select_keyboard("back_unit")
            )
        elif unit and text == UNIT_PINS[unit]:
            session["is_admin"] = False
            session["state"] = "select_product"
            await update.message.reply_text(
                f"✅ *Unité {unit}*\n\nChoisissez le médicament :",
                parse_mode="Markdown",
                reply_markup=product_select_keyboard("back_unit")
            )
        else:
            await update.message.reply_text("❌ Code incorrect. Réessayez :")
        return

    # ── Patient form ──
    if state == "form_nom":
        session["form"]["nomPrenom"] = text
        session["state"] = "form_ord"
        await update.message.reply_text(
            "📋 *N° Ordonnance*\n\nEntrez le numéro d'ordonnance\n(ou tapez `-` pour ignorer) :",
            parse_mode="Markdown", reply_markup=cancel_keyboard()
        )
        return

    if state == "form_ord":
        session["form"]["nOrdonnance"] = "" if text == "-" else text
        session["state"] = "form_date"
        await update.message.reply_text(
            "📅 *Date de début*\n\nFormat : JJ/MM/AAAA\n(ou `-` pour ignorer) :",
            parse_mode="Markdown", reply_markup=cancel_keyboard()
        )
        return

    if state == "form_date":
        if text != "-":
            try:
                d = datetime.strptime(text, "%d/%m/%Y")
                session["form"]["date"] = d.strftime("%Y-%m-%d")
            except:
                await update.message.reply_text(
                    "⚠️ Format invalide !\nUtilisez JJ/MM/AAAA (ex: 05/06/2026)\nou `-` pour ignorer :",
                    reply_markup=cancel_keyboard()
                )
                return
        else:
            session["form"]["date"] = ""
        session["state"] = "form_gtt"
        await update.message.reply_text(
            "💧 *Nombre de GTT*\n\nEntrez le nombre de GTT\n(ou `-` pour ignorer) :",
            parse_mode="Markdown", reply_markup=cancel_keyboard()
        )
        return

    if state == "form_gtt":
        session["form"]["nbreGTT"] = "" if text == "-" else text
        session["state"] = "form_flacon"
        await update.message.reply_text(
            "🔮 *Date du prochain flacon*\n\nFormat : JJ/MM/AAAA\n(ou `-` pour ignorer) :",
            parse_mode="Markdown", reply_markup=cancel_keyboard()
        )
        return

    if state == "form_flacon":
        if text != "-":
            try:
                d = datetime.strptime(text, "%d/%m/%Y")
                session["form"]["prochainFlacon"] = d.strftime("%Y-%m-%d")
            except:
                await update.message.reply_text(
                    "⚠️ Format invalide !\nUtilisez JJ/MM/AAAA (ex: 10/06/2026)\nou `-` pour ignorer :",
                    reply_markup=cancel_keyboard()
                )
                return
        else:
            session["form"]["prochainFlacon"] = ""
        session["state"] = "form_notes"
        await update.message.reply_text(
            "📝 *Notes / Remarques*\n\nEntrez vos remarques\n(ou `-` pour ignorer) :",
            parse_mode="Markdown", reply_markup=cancel_keyboard()
        )
        return

    if state == "form_notes":
        session["form"]["notes"] = "" if text == "-" else text
        data = load_data()
        unit = session["unit"]
        product = session["product"]
        edit_idx = session.get("edit_idx")

        if unit not in data:
            data[unit] = {p: [] for p in PRODUCTS}
        if product not in data[unit]:
            data[unit][product] = []

        if edit_idx is not None:
            data[unit][product][edit_idx] = {**data[unit][product][edit_idx], **session["form"]}
            action = "modifié"
        else:
            session["form"]["id"] = int(datetime.now().timestamp())
            data[unit][product].append(session["form"])
            action = "ajouté"

        save_data(data)
        p = session["form"]
        session["form"] = {}
        session["edit_idx"] = None
        session["state"] = "view_list"

        # Show complete patient card
        card = patient_card(p)
        patients = data[unit][product]
        await update.message.reply_text(
            f"✅ *Patient {action} avec succès !*\n\n{card}",
            parse_mode="Markdown"
        )
        await update.message.reply_text(
            product_header(unit, product, patients, True),
            parse_mode="Markdown",
            reply_markup=admin_product_keyboard(unit, product, session.get("page", 0))
        )
        return

    # Default
    await update.message.reply_text(
        "🏥 *H24TR*\n\nTapez /start pour recommencer.",
        parse_mode="Markdown"
    )

def main():
    application = Application.builder().token(TOKEN).build()
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CallbackQueryHandler(handle_callback))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    logger.info("Bot H24TR started...")
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
