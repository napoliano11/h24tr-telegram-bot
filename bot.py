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
DATA_FILE = "data.json"

def load_data():
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {u: {"produit": "", "patients": []} for u in UNITS}

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
    if d is None:
        return ""
    if d < 0:
        return f"🔴 Retard {abs(d)}j"
    if d == 0:
        return "🟡 Aujourd'hui"
    if d <= 3:
        return f"🟠 Dans {d}j"
    return f"🟢 {d}j"

sessions = {}

def get_session(chat_id):
    if chat_id not in sessions:
        sessions[chat_id] = {
            "state": "start", "is_admin": False, "unit": None,
            "produit": None, "form": {}, "edit_idx": None, "page": 0
        }
    return sessions[chat_id]

def main_menu_keyboard(is_admin=False):
    rows = []
    for i in range(0, len(UNITS), 4):
        rows.append([InlineKeyboardButton(u, callback_data=f"unit_{u}") for u in UNITS[i:i+4]])
    if is_admin:
        rows.append([InlineKeyboardButton("✅ Mode Admin actif", callback_data="noop")])
    else:
        rows.append([InlineKeyboardButton("🔑 Connexion Admin", callback_data="admin_login")])
    return InlineKeyboardMarkup(rows)

def product_keyboard(unit):
    data = load_data()
    current = data[unit]["produit"]
    buttons = []
    for p in PRODUCTS:
        label = f"✅ {p}" if current == p else p
        buttons.append([InlineKeyboardButton(label, callback_data=f"prod_{p}")])
    buttons.append([InlineKeyboardButton("⬅️ Retour", callback_data="back_unit")])
    return InlineKeyboardMarkup(buttons)

def unit_keyboard(is_admin, unit, produit, page=0):
    data = load_data()
    patients = data[unit]["patients"]
    rows = []
    rows.append([InlineKeyboardButton(f"💊 {produit or 'Choisir produit'}", callback_data="change_product")])
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
    if is_admin:
        rows.append([InlineKeyboardButton("➕ Ajouter patient", callback_data="add_patient")])
    rows.append([InlineKeyboardButton("⬅️ Retour unités", callback_data="back_main")])
    return InlineKeyboardMarkup(rows)

def patient_keyboard(is_admin, idx):
    rows = []
    if is_admin:
        rows.append([
            InlineKeyboardButton("✏️ Modifier", callback_data=f"edit_{idx}"),
            InlineKeyboardButton("🗑️ Supprimer", callback_data=f"delete_{idx}")
        ])
    rows.append([InlineKeyboardButton("⬅️ Retour", callback_data="back_unit")])
    return InlineKeyboardMarkup(rows)

def confirm_delete_keyboard(idx):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Confirmer suppression", callback_data=f"confirm_delete_{idx}")],
        [InlineKeyboardButton("❌ Annuler", callback_data=f"view_{idx}")]
    ])

def cancel_keyboard():
    return InlineKeyboardMarkup([[InlineKeyboardButton("❌ Annuler", callback_data="back_main")]])

def unit_header(unit, produit, is_admin, data):
    patients = data[unit]["patients"]
    alerts = sum(1 for p in patients if days_until(p.get("prochainFlacon", "")) is not None and days_until(p.get("prochainFlacon", "")) <= 3)
    role = "👤 Admin" if is_admin else "👁 Consultation"
    text = f"🏥 *Unité {unit}*\n"
    text += f"💊 Produit : *{produit or 'Non défini'}*\n"
    text += f"👥 Patients : *{len(patients)}*\n"
    if alerts:
        text += f"⚠️ Alertes flacons : *{alerts}*\n"
    text += f"🔑 {role}\n"
    text += f"📅 {date.today().strftime('%d/%m/%Y')}"
    return text

def patient_card(p):
    text = f"👤 *{p.get('nomPrenom', '—')}*\n\n"
    if p.get("date"):
        try:
            d = datetime.strptime(p["date"], "%Y-%m-%d").strftime("%d/%m/%Y")
        except:
            d = p["date"]
        text += f"📅 Date : {d}\n"
    if p.get("nOrdonnance"):
        text += f"📋 N° Ord. : {p['nOrdonnance']}\n"
    if p.get("nbreGTT"):
        text += f"💧 GTT : {p['nbreGTT']}\n"
    if p.get("prochainFlacon"):
        try:
            fd = datetime.strptime(p["prochainFlacon"], "%Y-%m-%d").strftime("%d/%m/%Y")
        except:
            fd = p["prochainFlacon"]
        badge = flacon_badge(p["prochainFlacon"])
        text += f"🔮 Prochain flacon : {fd}  {badge}\n"
    if p.get("notes"):
        text += f"📝 Notes : {p['notes']}\n"
    return text

async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    sessions[chat_id] = {
        "state": "start", "is_admin": False, "unit": None,
        "produit": None, "form": {}, "edit_idx": None, "page": 0
    }
    await update.message.reply_text(
        "🏥 *H24TR — Suivi des Unités Hospitalières*\n\nChoisissez une unité ou connectez-vous en tant qu'admin :",
        parse_mode="Markdown",
        reply_markup=main_menu_keyboard(False)
    )

async def handle_message(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    text = update.message.text.strip()
    session = get_session(chat_id)
    state = session.get("state", "start")

    if state == "await_pin":
        unit = session.get("unit")
        if text == ADMIN_PIN:
            session["is_admin"] = True
            session["state"] = "unit"
            data = load_data()
            produit = session.get("produit") or data[unit]["produit"] or ""
            await update.message.reply_text(
                unit_header(unit, produit, True, data),
                parse_mode="Markdown",
                reply_markup=unit_keyboard(True, unit, produit, 0)
            )
        elif unit and text == UNIT_PINS[unit]:
            session["is_admin"] = False
            session["state"] = "unit"
            data = load_data()
            produit = session.get("produit") or data[unit]["produit"] or ""
            await update.message.reply_text(
                unit_header(unit, produit, False, data),
                parse_mode="Markdown",
                reply_markup=unit_keyboard(False, unit, produit, 0)
            )
        else:
            await update.message.reply_text("❌ Code incorrect. Réessayez :", reply_markup=cancel_keyboard())
        return

    if state == "await_admin_pin":
        if text == ADMIN_PIN:
            session["is_admin"] = True
            session["state"] = "start"
            await update.message.reply_text(
                "✅ *Mode admin activé !*\nChoisissez une unité :",
                parse_mode="Markdown",
                reply_markup=main_menu_keyboard(True)
            )
        else:
            await update.message.reply_text("❌ Code admin incorrect.", reply_markup=cancel_keyboard())
        return

    if state == "form_nom":
        session["form"]["nomPrenom"] = text
        session["state"] = "form_ord"
        await update.message.reply_text("📋 *N° Ordonnance* (ou `-` pour ignorer) :", parse_mode="Markdown", reply_markup=cancel_keyboard())
        return

    if state == "form_ord":
        session["form"]["nOrdonnance"] = "" if text == "-" else text
        session["state"] = "form_date"
        await update.message.reply_text("📅 *Date* format JJ/MM/AAAA (ou `-`) :", parse_mode="Markdown", reply_markup=cancel_keyboard())
        return

    if state == "form_date":
        if text != "-":
            try:
                d = datetime.strptime(text, "%d/%m/%Y")
                session["form"]["date"] = d.strftime("%Y-%m-%d")
            except:
                await update.message.reply_text("⚠️ Format invalide. Utilisez JJ/MM/AAAA ou `-` :", reply_markup=cancel_keyboard())
                return
        else:
            session["form"]["date"] = ""
        session["state"] = "form_gtt"
        await update.message.reply_text("💧 *Nbre de GTT* (ou `-`) :", parse_mode="Markdown", reply_markup=cancel_keyboard())
        return

    if state == "form_gtt":
        session["form"]["nbreGTT"] = "" if text == "-" else text
        session["state"] = "form_flacon"
        await update.message.reply_text("🔮 *Date prochain flacon* format JJ/MM/AAAA (ou `-`) :", parse_mode="Markdown", reply_markup=cancel_keyboard())
        return

    if state == "form_flacon":
        if text != "-":
            try:
                d = datetime.strptime(text, "%d/%m/%Y")
                session["form"]["prochainFlacon"] = d.strftime("%Y-%m-%d")
            except:
                await update.message.reply_text("⚠️ Format invalide. Utilisez JJ/MM/AAAA ou `-` :", reply_markup=cancel_keyboard())
                return
        else:
            session["form"]["prochainFlacon"] = ""
        session["state"] = "form_notes"
        await update.message.reply_text("📝 *Notes* (ou `-`) :", parse_mode="Markdown", reply_markup=cancel_keyboard())
        return

    if state == "form_notes":
        session["form"]["notes"] = "" if text == "-" else text
        data = load_data()
        unit = session["unit"]
        edit_idx = session.get("edit_idx")
        if edit_idx is not None:
            data[unit]["patients"][edit_idx] = {**data[unit]["patients"][edit_idx], **session["form"]}
            msg = "✅ *Patient modifié avec succès !*"
        else:
            session["form"]["id"] = int(datetime.now().timestamp())
            data[unit]["patients"].append(session["form"])
            msg = "✅ *Patient ajouté avec succès !*"
        save_data(data)
        session["form"] = {}
        session["edit_idx"] = None
        session["state"] = "unit"
        produit = session.get("produit") or data[unit]["produit"] or ""
        await update.message.reply_text(msg, parse_mode="Markdown")
        await update.message.reply_text(
            unit_header(unit, produit, session["is_admin"], data),
            parse_mode="Markdown",
            reply_markup=unit_keyboard(session["is_admin"], unit, produit, session.get("page", 0))
        )
        return

    await update.message.reply_text(
        "🏥 *H24TR*\n\nChoisissez une unité :",
        parse_mode="Markdown",
        reply_markup=main_menu_keyboard(session.get("is_admin", False))
    )

async def handle_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    chat_id = query.message.chat_id
    data_cb = query.data
    session = get_session(chat_id)

    if data_cb == "noop":
        return

    if data_cb.startswith("unit_"):
        unit = data_cb[5:]
        session["unit"] = unit
        session["page"] = 0
        if session.get("is_admin"):
            session["state"] = "unit"
            data = load_data()
            produit = session.get("produit") or data[unit]["produit"] or ""
            session["produit"] = produit
            await query.edit_message_text(
                unit_header(unit, produit, True, data),
                parse_mode="Markdown",
                reply_markup=unit_keyboard(True, unit, produit, 0)
            )
        else:
            session["state"] = "await_pin"
            await query.edit_message_text(
                f"🏥 *Unité {unit}*\n\n🔑 Entrez votre code d'accès :",
                parse_mode="Markdown",
                reply_markup=cancel_keyboard()
            )
        return

    if data_cb == "admin_login":
        session["state"] = "await_admin_pin"
        await query.edit_message_text(
            "🔑 *Connexion Admin*\n\nEntrez le code administrateur :",
            parse_mode="Markdown",
            reply_markup=cancel_keyboard()
        )
        return

    if data_cb == "back_main":
        session["state"] = "start"
        await query.edit_message_text(
            "🏥 *H24TR — Suivi des Unités Hospitalières*\n\nChoisissez une unité :",
            parse_mode="Markdown",
            reply_markup=main_menu_keyboard(session.get("is_admin", False))
        )
        return

    if data_cb == "back_unit":
        unit = session.get("unit")
        data = load_data()
        produit = session.get("produit") or data[unit]["produit"] or ""
        session["state"] = "unit"
        await query.edit_message_text(
            unit_header(unit, produit, session["is_admin"], data),
            parse_mode="Markdown",
            reply_markup=unit_keyboard(session["is_admin"], unit, produit, session.get("page", 0))
        )
        return

    if data_cb == "change_product":
        unit = session.get("unit")
        await query.edit_message_text(
            f"💊 *Choisir le produit — Unité {unit}*\n\nSélectionnez un produit :",
            parse_mode="Markdown",
            reply_markup=product_keyboard(unit)
        )
        return

    if data_cb.startswith("prod_"):
        prod = data_cb[5:]
        unit = session.get("unit")
        session["produit"] = prod
        data = load_data()
        data[unit]["produit"] = prod
        save_data(data)
        await query.edit_message_text(
            unit_header(unit, prod, session["is_admin"], data),
            parse_mode="Markdown",
            reply_markup=unit_keyboard(session["is_admin"], unit, prod, session.get("page", 0))
        )
        return

    if data_cb.startswith("page_"):
        page = int(data_cb[5:])
        session["page"] = page
        unit = session["unit"]
        data = load_data()
        produit = session.get("produit") or data[unit]["produit"] or ""
        await query.edit_message_text(
            unit_header(unit, produit, session["is_admin"], data),
            parse_mode="Markdown",
            reply_markup=unit_keyboard(session["is_admin"], unit, produit, page)
        )
        return

    if data_cb.startswith("view_"):
        idx = int(data_cb[5:])
        unit = session["unit"]
        data = load_data()
        patients = data[unit]["patients"]
        if idx >= len(patients):
            await query.answer("Patient introuvable.")
            return
        p = patients[idx]
        await query.edit_message_text(
            patient_card(p),
            parse_mode="Markdown",
            reply_markup=patient_keyboard(session["is_admin"], idx)
        )
        return

    if data_cb == "add_patient":
        session["state"] = "form_nom"
        session["form"] = {}
        session["edit_idx"] = None
        await query.edit_message_text(
            "➕ *Nouveau patient*\n\n👤 *Nom et Prénom* :",
            parse_mode="Markdown",
            reply_markup=cancel_keyboard()
        )
        return

    if data_cb.startswith("edit_"):
        idx = int(data_cb[5:])
        unit = session["unit"]
        data = load_data()
        p = data[unit]["patients"][idx]
        session["state"] = "form_nom"
        session["form"] = dict(p)
        session["edit_idx"] = idx
        await query.edit_message_text(
            f"✏️ *Modifier patient*\n\n👤 *Nom et Prénom* (actuel: {p.get('nomPrenom', '—')}) :",
            parse_mode="Markdown",
            reply_markup=cancel_keyboard()
        )
        return

    if data_cb.startswith("delete_"):
        idx = int(data_cb[7:])
        unit = session["unit"]
        data = load_data()
        p = data[unit]["patients"][idx]
        await query.edit_message_text(
            f"🗑️ *Supprimer {p.get('nomPrenom', 'ce patient')} ?*\n\nCette action est irréversible.",
            parse_mode="Markdown",
            reply_markup=confirm_delete_keyboard(idx)
        )
        return

    if data_cb.startswith("confirm_delete_"):
        idx = int(data_cb[15:])
        unit = session["unit"]
        data = load_data()
        data[unit]["patients"].pop(idx)
        save_data(data)
        produit = session.get("produit") or data[unit]["produit"] or ""
        session["state"] = "unit"
        await query.edit_message_text("✅ *Patient supprimé.*", parse_mode="Markdown")
        await ctx.bot.send_message(
            chat_id,
            unit_header(unit, produit, session["is_admin"], data),
            parse_mode="Markdown",
            reply_markup=unit_keyboard(session["is_admin"], unit, produit, session.get("page", 0))
        )
        return

def main():
    application = Application.builder().token(TOKEN).build()
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CallbackQueryHandler(handle_callback))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    logger.info("Bot H24TR started...")
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
