# -----------------------------------------------------------
# IMPORTS
# -----------------------------------------------------------
from flask import Flask, render_template, request, redirect, url_for, session, jsonify, flash
from flask_sqlalchemy import SQLAlchemy
from flask_migrate import Migrate
from apscheduler.schedulers.background import BackgroundScheduler
from datetime import datetime, timedelta, time
from functools import wraps
import requests, os, pytz

from db import db
from werkzeug.utils import secure_filename
from models import TwitchUser, Directo, SlotApoyo, ReservaApoyo, Evento, AdminCode


# -----------------------------------------------------------
# CONFIGURACIÓN
# -----------------------------------------------------------
app = Flask(__name__)
app.config["SQLALCHEMY_DATABASE_URI"]        = os.environ.get("DATABASE_URL", "sqlite:///alasnegras.db")
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
app.config["SECRET_KEY"]                     = os.environ.get("SECRET_KEY", "alasnegras-secret-2026")

db.init_app(app)
migrate = Migrate(app, db)

with app.app_context():
    db.create_all()
    if AdminCode.query.count() == 0:
        db.session.add_all([
            AdminCode(codigo="admin-patry-2026",  nombre="Patry"),
            AdminCode(codigo="admin-mod1-2026",   nombre="Admin2"),
            AdminCode(codigo="admin-mod2-2026",   nombre="Admin3"),
        ])
        db.session.commit()


# -----------------------------------------------------------
# CONFIGURACIÓN TWITCH
# -----------------------------------------------------------
TWITCH_CLIENT_ID     = os.environ.get("TWITCH_CLIENT_ID",     "k02pn5l6olnt5tfn8d1eachqr9t7zf")
TWITCH_CLIENT_SECRET = os.environ.get("TWITCH_CLIENT_SECRET", "n33waq1ied91ve49g79n4xnwtvszw6")
TWITCH_REDIRECT_URI  = os.environ.get("TWITCH_REDIRECT_URI",  "http://localhost:5000/callback_twitch")

TZ_SPAIN = pytz.timezone("Europe/Madrid")


# -----------------------------------------------------------
# TELEGRAM
# -----------------------------------------------------------
def enviar_mensaje_telegram(mensaje):
    token   = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            data={"chat_id": chat_id, "text": mensaje, "parse_mode": "HTML"}
        )
    except Exception as e:
        print(f"Error enviando mensaje Telegram: {e}")


# -----------------------------------------------------------
# SCHEDULER
# -----------------------------------------------------------
def reset_reservas_apoyo():
    with app.app_context():
        from models import Clip
        Clip.query.delete()
        ReservaApoyo.query.delete()
        db.session.commit()
        print(">>> Reservas y clips reseteados:", datetime.utcnow())


def notificar_directos_telegram():
    with app.app_context():
        ahora_spain = hora_spain()
        dia_actual  = ahora_spain.weekday()
        hora_actual = ahora_spain.hour

        slots = SlotApoyo.query.all()
        mapa  = {(s.dia_semana, s.hora_inicio.hour): s for s in slots}
        slot_actual = mapa.get((dia_actual, hora_actual))

        if not slot_actual or not slot_actual.reservas:
            return

        lineas = ["🎮 <b>¡Hora de apoyar en Twitch!</b>\n"]
        for reserva in slot_actual.reservas:
            u = reserva.usuario
            if u:
                lineas.append(f"👤 <b>{u.display_name}</b>")
                lineas.append(f"🔗 https://twitch.tv/{u.login}\n")
        lineas.append("¡Entra y apóyales ahora! 🖤")
        enviar_mensaje_telegram("\n".join(lineas))


scheduler = BackgroundScheduler()

scheduler.add_job(
    func=reset_reservas_apoyo,
    trigger="cron",
    day_of_week="sat",
    hour=23,
    minute=59,
    timezone=pytz.timezone("Europe/Madrid")
)

scheduler.add_job(
    func=notificar_directos_telegram,
    trigger="cron",
    minute=0,
    timezone=pytz.timezone("Europe/Madrid")
)

if not app.debug or os.environ.get("WERKZEUG_RUN_MAIN") == "true":
    scheduler.start()

# -----------------------------------------------------------
# CLIPS
# -----------------------------------------------------------

@app.route("/clips")
def clips():
    from models import Clip
    lista = Clip.query.order_by(Clip.creado_en.desc()).all()
    return render_template("clips.html", clips=lista)




def recoger_clips_streamers():
    """Recoge clips recientes de los streamers agendados en la hora actual."""
    with app.app_context():
        from models import Clip
        ahora_spain = hora_spain()
        dia_actual  = ahora_spain.weekday()
        hora_actual = ahora_spain.hour

        slots = SlotApoyo.query.all()
        mapa  = {(s.dia_semana, s.hora_inicio.hour): s for s in slots}
        slot_actual = mapa.get((dia_actual, hora_actual))

        if not slot_actual or not slot_actual.reservas:
            return

        token = get_twitch_app_token()
        headers = {
            "Client-ID":     TWITCH_CLIENT_ID,
            "Authorization": f"Bearer {token}",
        }

        for reserva in slot_actual.reservas:
            usuario = reserva.usuario
            if not usuario:
                continue

            resp = requests.get(
                "https://api.twitch.tv/helix/clips",
                headers=headers,
                params={
                    "broadcaster_id": usuario.twitch_id,
                    "first": 10
                }
            ).json()

            for clip_data in resp.get("data", []):
                # Solo guardar si no existe ya
                if Clip.query.filter_by(clip_id=clip_data["id"]).first():
                    continue

                embed = f"https://clips.twitch.tv/embed?clip={clip_data['id']}&parent=alasnegras.onrender.com"

                db.session.add(Clip(
                    clip_id      = clip_data["id"],
                    usuario_id   = usuario.id,
                    titulo       = clip_data.get("title", "Sin título"),
                    url          = clip_data.get("url", ""),
                    thumbnail_url= clip_data.get("thumbnail_url", ""),
                    embed_url    = embed,
                    duracion     = clip_data.get("duration", 0),
                    vistas       = clip_data.get("view_count", 0),
                ))

        db.session.commit()
        print(">>> Clips recogidos:", datetime.utcnow())



# -----------------------------------------------------------
# HELPERS
# -----------------------------------------------------------

def hora_spain():
    return datetime.now(TZ_SPAIN)


def get_or_create_twitch_user_from_session():
    twitch_id    = session.get("twitch_id")
    display_name = session.get("display_name")
    login        = session.get("login")
    avatar       = session.get("avatar")

    if not twitch_id:
        return None

    user = TwitchUser.query.filter_by(twitch_id=twitch_id).first()
    if not user:
        user = TwitchUser(twitch_id=twitch_id, login=login,
                          display_name=display_name, avatar_url=avatar)
        db.session.add(user)
        db.session.commit()
    else:
        changed = False
        if login and user.login != login:
            user.login = login; changed = True
        if avatar and user.avatar_url != avatar:
            user.avatar_url = avatar; changed = True
        if changed:
            db.session.commit()
    return user


def semana_actual_bounds():
    ahora  = datetime.utcnow()
    inicio = ahora - timedelta(days=ahora.weekday())
    inicio = inicio.replace(hour=0, minute=0, second=0, microsecond=0)
    return inicio, inicio + timedelta(days=7)


def get_twitch_app_token():
    resp = requests.post("https://id.twitch.tv/oauth2/token", data={
        "client_id":     TWITCH_CLIENT_ID,
        "client_secret": TWITCH_CLIENT_SECRET,
        "grant_type":    "client_credentials",
    }).json()
    return resp.get("access_token")


def get_stream_info(twitch_id=None, login=None, token=None):
    if not token:
        token = get_twitch_app_token()

    headers = {
        "Client-ID":     TWITCH_CLIENT_ID,
        "Authorization": f"Bearer {token}",
    }

    param_key   = "user_id" if twitch_id else "user_login"
    param_val   = twitch_id or login
    stream_data = requests.get(
        "https://api.twitch.tv/helix/streams",
        headers=headers, params={param_key: param_val}
    ).json().get("data", [])

    is_live       = bool(stream_data)
    thumbnail_url = None
    if is_live:
        raw = stream_data[0].get("thumbnail_url", "")
        thumbnail_url = raw.replace("{width}x{height}", "320x180")

    user_param = {"id": twitch_id} if twitch_id else {"login": login}
    user_data  = requests.get(
        "https://api.twitch.tv/helix/users",
        headers=headers, params=user_param
    ).json().get("data", [])

    avatar_url     = user_data[0].get("profile_image_url") if user_data else None
    resolved_login = user_data[0].get("login", login or "") if user_data else (login or "")
    resolved_id    = user_data[0].get("id")                if user_data else twitch_id

    return {
        "is_live":       is_live,
        "thumbnail_url": thumbnail_url,
        "avatar_url":    avatar_url,
        "channel_url":   f"https://twitch.tv/{resolved_login}",
        "login":         resolved_login,
        "twitch_id":     resolved_id,
        "display_name":  user_data[0].get("display_name", resolved_login) if user_data else resolved_login,
    }


def get_or_create_user_by_login(login: str) -> TwitchUser | None:
    login = login.strip().lower()
    user  = TwitchUser.query.filter_by(login=login).first()
    if user:
        return user

    token = get_twitch_app_token()
    info  = get_stream_info(login=login, token=token)

    if not info.get("twitch_id"):
        return None

    user = TwitchUser(
        twitch_id    = info["twitch_id"],
        login        = info["login"],
        display_name = info["display_name"],
        avatar_url   = info["avatar_url"],
    )
    db.session.add(user)
    db.session.commit()
    return user


def generar_slots_agenda_base():
    """Solo añade slots que no existen — NO borra reservas."""
    with app.app_context():
        for dia in range(0, 6):          # lunes–sábado
            for hora in range(15, 24):   # 15:00–23:00
                inicio = time(hora, 0)
                fin    = time((hora + 1) % 24, 0)
                if not SlotApoyo.query.filter_by(dia_semana=dia,
                                                  hora_inicio=inicio,
                                                  hora_fin=fin).first():
                    db.session.add(SlotApoyo(dia_semana=dia,
                                             hora_inicio=inicio,
                                             hora_fin=fin))
        db.session.commit()
        print(">>> Slots generados sin borrar reservas")


# -----------------------------------------------------------
# DECORADOR: requiere admin
# -----------------------------------------------------------

def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get("es_admin"):
            flash("Necesitas acceso de administrador.", "error")
            return redirect(url_for("index"))
        return f(*args, **kwargs)
    return decorated


# -----------------------------------------------------------
# LOGIN / LOGOUT TWITCH
# -----------------------------------------------------------

@app.route("/login_twitch")
def login_twitch():
    auth_url = (
        "https://id.twitch.tv/oauth2/authorize"
        "?response_type=code"
        f"&client_id={TWITCH_CLIENT_ID}"
        f"&redirect_uri={TWITCH_REDIRECT_URI}"
        "&scope=user:read:email"
    )
    return redirect(auth_url)


@app.route("/callback_twitch")
def callback_twitch():
    code = request.args.get("code")
    token_resp = requests.post("https://id.twitch.tv/oauth2/token", data={
        "client_id":     TWITCH_CLIENT_ID,
        "client_secret": TWITCH_CLIENT_SECRET,
        "code":          code,
        "grant_type":    "authorization_code",
        "redirect_uri":  TWITCH_REDIRECT_URI,
    }).json()

    if "access_token" not in token_resp:
        return f"Error obteniendo token: {token_resp}"

    headers   = {"Client-ID": TWITCH_CLIENT_ID,
                 "Authorization": f"Bearer {token_resp['access_token']}"}
    user_info = requests.get("https://api.twitch.tv/helix/users", headers=headers).json()

    if "data" not in user_info:
        return f"Error obteniendo usuario: {user_info}"

    u = user_info["data"][0]
    session["twitch_id"]    = u["id"]
    session["display_name"] = u["display_name"]
    session["avatar"]       = u["profile_image_url"]
    session["login"]        = u["login"]
    session["es_admin"]     = False
    session["modo_admin"]   = False

    get_or_create_twitch_user_from_session()
    return redirect(url_for("index"))


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("index"))


# -----------------------------------------------------------
# LOGIN ADMIN
# -----------------------------------------------------------

@app.route("/admin_codigo", methods=["GET", "POST"])
def admin_codigo():
    if request.method == "POST":
        codigo   = request.form.get("codigo", "").strip()
        code_obj = AdminCode.query.filter_by(codigo=codigo, activo=True).first()
        if code_obj:
            session["es_admin"]     = True
            session["modo_admin"]   = True
            session["admin_nombre"] = code_obj.nombre
            flash(f"Bienvenido al panel de administración, {code_obj.nombre}.", "success")
            return redirect(url_for("admin_agendas_calendario"))
        else:
            flash("Código incorrecto.", "error")
    return render_template("admin_codigo.html")


@app.route("/admin_login")
def admin_login():
    if not session.get("es_admin"):
        return redirect(url_for("admin_codigo"))
    session["modo_admin"] = True
    return redirect(url_for("admin_agendas_calendario"))


@app.route("/admin_logout")
def admin_logout():
    session["modo_admin"] = False
    session["es_admin"]   = False
    return redirect(url_for("index"))


# -----------------------------------------------------------
# RUTAS PRINCIPALES
# -----------------------------------------------------------

@app.route("/")
def index():
    return render_template("index.html")


# -----------------------------------------------------------
# EN DIRECTO
# -----------------------------------------------------------

@app.route("/emitiendo")
def emitiendo():
    ahora_spain = hora_spain()
    dia_actual  = ahora_spain.weekday()
    hora_actual = ahora_spain.hour

    slots = SlotApoyo.query.all()
    mapa  = {(s.dia_semana, s.hora_inicio.hour): s for s in slots}
    slot_actual = mapa.get((dia_actual, hora_actual))

    reservas_info = []
    if slot_actual and slot_actual.reservas:
        app_token = get_twitch_app_token()
        for reserva in slot_actual.reservas:
            usuario = reserva.usuario
            if not usuario:
                continue
            info = get_stream_info(twitch_id=usuario.twitch_id,
                                   login=usuario.login,
                                   token=app_token)
            reservas_info.append({
                "display_name":  usuario.display_name,
                "login":         usuario.login,
                "thumbnail_url": info["thumbnail_url"],
                "avatar_url":    info["avatar_url"],
                "channel_url":   info["channel_url"],
                "is_live":       info["is_live"],
            })

    return render_template("emitiendo.html", reservas=reservas_info)


# -----------------------------------------------------------
# CALENDARIO
# -----------------------------------------------------------

@app.route("/calendario")
def calendario():
    directos = Directo.query.order_by(Directo.fecha_inicio).all()
    slots    = SlotApoyo.query.all()
    mapa     = {(s.dia_semana, s.hora_inicio.hour): s for s in slots}
    return render_template("calendario.html", slots=mapa, directos=directos)


# -----------------------------------------------------------
# AGENDAS
# -----------------------------------------------------------

@app.route("/agendas")
def agendas():
    slots   = SlotApoyo.query.all()
    mapa    = {(s.dia_semana, s.hora_inicio.hour): s for s in slots}
    usuario = get_or_create_twitch_user_from_session()
    current_user_id = usuario.id if usuario else None
    return render_template("agendas.html", slots=mapa, current_user_id=current_user_id)


@app.route("/reservar_slot/<int:slot_id>")
def reservar_slot(slot_id):
    if not session.get("twitch_id"):
        flash("Debes iniciar sesión para reservar.", "error")
        return redirect(url_for("login_twitch"))

    slot    = SlotApoyo.query.get_or_404(slot_id)
    usuario = get_or_create_twitch_user_from_session()

    if not usuario:
        return redirect(url_for("login_twitch"))

    if len(slot.reservas) >= 2:
        flash("Esta hora ya está ocupada.", "error")
        return redirect(url_for("agendas"))

    if ReservaApoyo.query.filter_by(usuario_id=usuario.id, slot_id=slot.id).first():
        flash("Ya tienes reservada esta hora.", "error")
        return redirect(url_for("agendas"))

    inicio_sem, fin_sem = semana_actual_bounds()
    if ReservaApoyo.query.filter(
        ReservaApoyo.usuario_id == usuario.id,
        ReservaApoyo.creada_en >= inicio_sem,
        ReservaApoyo.creada_en <  fin_sem,
    ).count() >= 3:
        flash("Has alcanzado tu límite semanal de 3 horas.", "error")
        return redirect(url_for("agendas"))

    if (ReservaApoyo.query
            .join(SlotApoyo, ReservaApoyo.slot_id == SlotApoyo.id)
            .filter(ReservaApoyo.usuario_id == usuario.id,
                    SlotApoyo.dia_semana == slot.dia_semana)
            .count() >= 2):
        flash("Solo puedes reservar 2 horas por día.", "error")
        return redirect(url_for("agendas"))

    db.session.add(ReservaApoyo(slot_id=slot.id, usuario_id=usuario.id, estado="pendiente"))
    db.session.commit()
    flash("¡Reserva realizada correctamente!", "success")
    return redirect(url_for("agendas"))


@app.route("/cancelar_reserva/<int:reserva_id>")
def cancelar_reserva(reserva_id):
    if not session.get("twitch_id"):
        return redirect(url_for("login_twitch"))

    reserva = ReservaApoyo.query.get_or_404(reserva_id)
    usuario = get_or_create_twitch_user_from_session()

    if not usuario:
        return redirect(url_for("agendas"))

    if reserva.usuario_id != usuario.id and not session.get("es_admin"):
        flash("No tienes permiso para cancelar esta reserva.", "error")
        return redirect(url_for("agendas"))

    db.session.delete(reserva)
    db.session.commit()
    flash("Reserva cancelada.", "success")
    return redirect(url_for("agendas"))


# -----------------------------------------------------------
# ADMIN — AGENDAS
# -----------------------------------------------------------

@app.route("/admin_agendas_calendario", methods=["GET", "POST"])
@admin_required
def admin_agendas_calendario():
    if request.method == "POST":
        action = request.form.get("action", "add")

        if action == "add":
            slot_id      = request.form.get("slot_id")
            twitch_login = request.form.get("twitch_login", "").strip().lower()

            if not slot_id or not twitch_login:
                flash("Faltan datos para crear la reserva.", "error")
                return redirect(url_for("admin_agendas_calendario"))

            usuario = get_or_create_user_by_login(twitch_login)
            if not usuario:
                flash(f"No se encontró el canal '{twitch_login}' en Twitch.", "error")
                return redirect(url_for("admin_agendas_calendario"))

            slot_obj = SlotApoyo.query.get(int(slot_id))
            if not slot_obj:
                flash("Slot no encontrado.", "error")
                return redirect(url_for("admin_agendas_calendario"))

            if len(slot_obj.reservas) >= 2:
                flash("Ese slot ya está completo (máx. 2 personas).", "error")
                return redirect(url_for("admin_agendas_calendario"))

            if ReservaApoyo.query.filter_by(usuario_id=usuario.id, slot_id=slot_obj.id).first():
                flash(f"{usuario.display_name} ya tiene esa hora reservada.", "error")
                return redirect(url_for("admin_agendas_calendario"))

            db.session.add(ReservaApoyo(slot_id=slot_obj.id,
                                         usuario_id=usuario.id,
                                         estado="pendiente"))
            db.session.commit()
            flash(f"Reserva de {usuario.display_name} creada correctamente.", "success")

        elif action == "move":
            reserva_id    = int(request.form.get("reserva_id"))
            nuevo_slot_id = int(request.form.get("nuevo_slot_id"))
            reserva       = ReservaApoyo.query.get_or_404(reserva_id)
            nuevo_slot    = SlotApoyo.query.get_or_404(nuevo_slot_id)

            if ReservaApoyo.query.filter_by(usuario_id=reserva.usuario_id,
                                             slot_id=nuevo_slot_id).first():
                flash("Ese usuario ya tiene reservada esa hora.", "error")
            elif len(nuevo_slot.reservas) >= 2:
                flash("El slot destino ya está completo.", "error")
            else:
                reserva.slot_id = nuevo_slot_id
                db.session.commit()
                flash("Reserva movida correctamente.", "success")

        return redirect(url_for("admin_agendas_calendario"))

    slots     = SlotApoyo.query.all()
    mapa      = {(s.dia_semana, s.hora_inicio.hour): s for s in slots}
    all_slots = SlotApoyo.query.order_by(SlotApoyo.dia_semana, SlotApoyo.hora_inicio).all()
    return render_template("admin_agendas_calendario.html", slots=mapa, all_slots=all_slots)


@app.route("/admin_eliminar_reserva/<int:reserva_id>")
@admin_required
def admin_eliminar_reserva(reserva_id):
    reserva = ReservaApoyo.query.get_or_404(reserva_id)
    db.session.delete(reserva)
    db.session.commit()
    flash("Reserva eliminada.", "success")
    return redirect(url_for("admin_agendas_calendario"))


@app.route("/admin_generar_slots")
@admin_required
def admin_generar_slots():
    generar_slots_agenda_base()
    flash("Slots generados correctamente.", "success")
    return redirect(url_for("admin_agendas_calendario"))


# -----------------------------------------------------------
# ADMIN — CÓDIGOS
# -----------------------------------------------------------

@app.route("/admin_codigos", methods=["GET", "POST"])
@admin_required
def admin_codigos():
    if request.method == "POST":
        action = request.form.get("action")
        if action == "create":
            nombre = request.form.get("nombre", "").strip()
            codigo = request.form.get("codigo", "").strip()
            if nombre and codigo:
                if AdminCode.query.filter_by(codigo=codigo).first():
                    flash("Ese código ya existe.", "error")
                else:
                    db.session.add(AdminCode(nombre=nombre, codigo=codigo))
                    db.session.commit()
                    flash(f"Código para '{nombre}' creado.", "success")
        elif action == "toggle":
            code_id = int(request.form.get("code_id"))
            c = AdminCode.query.get(code_id)
            if c:
                c.activo = not c.activo
                db.session.commit()
                flash("Estado actualizado.", "success")
        elif action == "delete":
            code_id = int(request.form.get("code_id"))
            c = AdminCode.query.get(code_id)
            if c:
                db.session.delete(c)
                db.session.commit()
                flash("Código eliminado.", "success")
        return redirect(url_for("admin_codigos"))

    codigos = AdminCode.query.order_by(AdminCode.creado_en).all()
    return render_template("admin_codigos.html", codigos=codigos)


# -----------------------------------------------------------
# UPLOAD IMÁGENES
# -----------------------------------------------------------

@app.route('/upload_image', methods=['POST'])
def upload_image():
    if 'file' not in request.files:
        return jsonify({'error': 'No file part'}), 400
    file = request.files['file']
    if file.filename == '':
        return jsonify({'error': 'No selected file'}), 400
    filename    = secure_filename(file.filename)
    upload_path = os.path.join('static/uploads', filename)
    os.makedirs(os.path.dirname(upload_path), exist_ok=True)
    file.save(upload_path)
    return jsonify({"location": f"/static/uploads/{filename}"})


# -----------------------------------------------------------
# EVENTOS
# -----------------------------------------------------------

@app.route("/eventos")
def eventos():
    lista = Evento.query.order_by(Evento.creado.asc()).all()
    return render_template("eventos.html", eventos=lista)


@app.route("/admin_eventos", methods=["GET", "POST"])
@admin_required
def admin_eventos():
    if request.method == "POST":
        titulo     = request.form["titulo"]
        inicio_str = request.form.get("inicio", "").strip()
        fin_str    = request.form.get("fin", "").strip()
        inicio     = datetime.strptime(inicio_str, "%Y-%m-%dT%H:%M") if inicio_str else None
        fin        = datetime.strptime(fin_str,    "%Y-%m-%dT%H:%M") if fin_str    else None
        url        = request.form.get("url")
        contenido  = request.form.get("contenido", "")
        db.session.add(Evento(titulo=titulo, fecha_inicio=inicio,
                               fecha_fin=fin, url_twitch=url, contenido=contenido))
        db.session.commit()
        return redirect(url_for("eventos"))

    return render_template("admin_eventos.html",
                           eventos=Evento.query.order_by(Evento.creado).all())


@app.route("/editar_evento/<int:id>", methods=["GET", "POST"])
@admin_required
def editar_evento(id):
    evento = Evento.query.get_or_404(id)
    if request.method == "POST":
        evento.titulo       = request.form["titulo"]
        inicio_str          = request.form.get("inicio", "").strip()
        fin_str             = request.form.get("fin", "").strip()
        evento.fecha_inicio = datetime.strptime(inicio_str, "%Y-%m-%dT%H:%M") if inicio_str else None
        evento.fecha_fin    = datetime.strptime(fin_str,    "%Y-%m-%dT%H:%M") if fin_str    else None
        evento.url_twitch   = request.form.get("url")
        evento.contenido    = request.form.get("contenido", "")
        db.session.commit()
        flash("Evento actualizado.", "success")
        return redirect(url_for("eventos"))
    return render_template("editar_evento.html", evento=evento)


@app.route("/eliminar_evento/<int:id>")
@admin_required
def eliminar_evento(id):
    evento = Evento.query.get_or_404(id)
    db.session.delete(evento)
    db.session.commit()
    return redirect(url_for("eventos"))



# -----------------------------------------------------------
# APOYOS
# -----------------------------------------------------------

@app.route("/apoyos")
def apoyos():
    if not session.get("twitch_id"):
        flash("Debes iniciar sesión para ver los apoyos.", "error")
        return redirect(url_for("login_twitch"))
    return render_template("apoyos.html")



# -----------------------------------------------------------
# EJECUCIÓN
# -----------------------------------------------------------
if __name__ == "__main__":
    app.run(debug=True)