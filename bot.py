import os
import json
import logging
from datetime import datetime, date
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, BotCommand
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
            d = json.load(f)
            # migrate old format if needed
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

# ─── Sessions ────────────────────────────────────────────────────────────────

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
        "last_msg_id": None
    }

def get_session(chat_id):
    if chat_id not in sessions:
        sessions[chat_id] = new_session()
    return sessions[chat_id]

# ─── Keyboards ───────────────────────────────────────────────────────────────

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

def kb_patient_list(unit, product, is_admin, page=0):
    data = load_data()
    patients = data[unit][product]
    rows = []
    if is_admin:
        rows.append([InlineKeyboardButton("➕  Ajouter un patient", callback_data="add_patient")])
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
    return InlineKeyboardMarkup([[InlineKeyboardButton("❌  Annuler la saisie", callback_data="back_list")]])

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
    if p.get("notes"):
        lines.append(f"📝  *Notes :*  {p['notes']}")
    lines.append(sep)
    return "\n".join(lines)

# ─── Safe edit helper ─────────────────────────────────────────────────────────

async def safe_edit(query, text, keyboard):
    try:
        await query.edit_message_text(text, parse_mode="Markdown", reply_markup=keyboard)
    except Exception as e:
        logger.warning(f"edit_message_text failed: {e}")

# ─── Handlers ─────────────────────────────────────────────────────────────────

async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    sessions[chat_id] = new_session()
    await update.message.reply_text(txt_welcome(), parse_mode="Markdown", reply_markup=kb_start())

async def handle_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    chat_id = query.message.chat_id
    cb = query.data
    s = get_session(chat_id)

    # ── Start / roles ──
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

    # ── Units ──
    if cb == "back_units":
        s["state"] = "select_unit"
        s["unit"] = None
        title = "👁  *Consultation*\n\n" if not s["is_admin"] else "👤  *Admin*\n\n"
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

    # ── Products ──
    if cb == "back_products":
        s["state"] = "select_product"
        s["product"] = None
        unit = s["unit"]
        await safe_edit(query, f"🏥  *Unité {unit}*\n\nChoisissez le médicament :", kb_products("back_units"))
        return

    if cb.startswith("prod_"):
        product = cb[5:]
        s["product"] = product
        s["page"] = 0
        s["state"] = "view_list"
        unit = s["unit"]
        await safe_edit(query, txt_list_header(unit, product, s["is_admin"]), kb_patient_list(unit, product, s["is_admin"], 0))
        return

    # ── List / pagination ──
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

    # ── View patient ──
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

    # ── Add patient ──
    if cb == "add_patient":
        s["state"] = "form_nom"
        s["form"] = {}
        s["edit_idx"] = None
        await safe_edit(query, "➕  *Nouveau patient*\n\n👤  Entrez le *Nom et Prénom* :", kb_cancel())
        return

    # ── Edit patient ──
    if cb.startswith("edit_"):
        idx = int(cb[5:])
        data = load_data()
        p = data[s["unit"]][s["product"]][idx]
        s["state"] = "form_nom"
        s["form"] = dict(p)
        s["edit_idx"] = idx
        await safe_edit(query, f"✏️  *Modifier patient*\n\nNom actuel : *{p.get('nomPrenom','—')}*\n\n👤  Nouveau *Nom et Prénom* :", kb_cancel())
        return

    # ── Delete patient ──
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
        await safe_edit(query, f"✅  *{nom}* supprimé avec succès.\n\n{txt_list_header(unit, product, True)}", kb_patient_list(unit, product, True, 0))
        return

async def handle_message(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    text = update.message.text.strip()
    s = get_session(chat_id)
    state = s.get("state", "start")

    # ── Admin PIN ──
    if state == "await_admin_pin":
        if text == ADMIN_PIN:
            s["is_admin"] = True
            s["state"] = "select_unit"
            await update.message.reply_text("✅  *Accès admin accordé !*\n\nChoisissez votre unité :", parse_mode="Markdown", reply_markup=kb_units())
        else:
            await update.message.reply_text("❌  Code incorrect. Réessayez :")
        return

    # ── Unit PIN ──
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

    # ── Patient form ──
    if state == "form_nom":
        s["form"]["nomPrenom"] = text
        s["state"] = "form_ord"
        await update.message.reply_text("📋  *N° Ordonnance*\n\nEntrez le numéro (ou `-` pour ignorer) :", parse_mode="Markdown", reply_markup=kb_cancel())
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
                await update.message.reply_text("⚠️  Format invalide ! Utilisez JJ/MM/AAAA\nEx : 05/06/2026  ou  `-` :", reply_markup=kb_cancel())
                return
        else:
            s["form"]["date"] = ""
        s["state"] = "form_gtt"
        await update.message.reply_text("💧  *Nombre de GTT*\n\nEntrez le nombre (ou `-`) :", parse_mode="Markdown", reply_markup=kb_cancel())
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
                await update.message.reply_text("⚠️  Format invalide ! Utilisez JJ/MM/AAAA\nEx : 10/06/2026  ou  `-` :", reply_markup=kb_cancel())
                return
        else:
            s["form"]["prochainFlacon"] = ""
        s["state"] = "form_notes"
        await update.message.reply_text("📝  *Notes / Remarques*\n\n(ou `-` pour ignorer) :", parse_mode="Markdown", reply_markup=kb_cancel())
        return

    if state == "form_notes":
        s["form"]["notes"] = "" if text == "-" else text
        data = load_data()
        unit = s["unit"]
        product = s["product"]
        edit_idx = s.get("edit_idx")
        if edit_idx is not None:
            data[unit][product][edit_idx] = {**data[unit][product][edit_idx], **s["form"]}
            action = "modifié"
        else:
            s["form"]["id"] = int(datetime.now().timestamp())
            data[unit][product].append(s["form"])
            action = "ajouté"
        save_data(data)
        saved = s["form"].copy()
        s["form"] = {}
        s["edit_idx"] = None
        s["state"] = "view_list"
        s["page"] = 0
        await update.message.reply_text(
            f"✅  *Patient {action} avec succès !*\n\n{txt_patient_card(saved)}",
            parse_mode="Markdown"
        )
        await update.message.reply_text(
            txt_list_header(unit, product, True),
            parse_mode="Markdown",
            reply_markup=kb_patient_list(unit, product, True, 0)
        )
        return

    # ── Default ──
    await update.message.reply_text("Tapez /start pour commencer. 🏥")

async def post_init(app):
    await app.bot.set_my_commands([
        BotCommand("start", "Démarrer / Accueil")
    ])

def main():
    application = Application.builder().token(TOKEN).post_init(post_init).build()
    application.add_handler(CommandHandler("start", cmd_start))
    application.add_handler(CallbackQueryHandler(handle_callback))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    logger.info("Bot H24TR started...")
    application.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)

if __name__ == "__main__":
    main()
    
