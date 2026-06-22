from db import db
from datetime import datetime

# -----------------------------------------------------------
# MODELO: TwitchUser
# -----------------------------------------------------------
class TwitchUser(db.Model):
    id            = db.Column(db.Integer, primary_key=True)
    twitch_id     = db.Column(db.String(80),  unique=True, nullable=False)
    login         = db.Column(db.String(120), nullable=False)
    display_name  = db.Column(db.String(120))
    avatar_url    = db.Column(db.String(300))   # ← NUEVO: guardamos avatar en BD
    is_admin      = db.Column(db.Boolean, default=False)  # ← NUEVO: rol admin

    reservas_apoyo = db.relationship("ReservaApoyo", backref="usuario", lazy=True)
    directos       = db.relationship("Directo",       backref="streamer", lazy=True)


# -----------------------------------------------------------
# MODELO: Directo
# -----------------------------------------------------------
class Directo(db.Model):
    id           = db.Column(db.Integer, primary_key=True)
    streamer_id  = db.Column(db.Integer, db.ForeignKey("twitch_user.id"), nullable=False)
    titulo       = db.Column(db.String(150), nullable=False)
    fecha_inicio = db.Column(db.DateTime,    nullable=False)
    fecha_fin    = db.Column(db.DateTime,    nullable=False)
    url_twitch   = db.Column(db.String(200), nullable=False)

    reservas_apoyo = db.relationship("ReservaApoyo", backref="directo", lazy=True)


# -----------------------------------------------------------
# MODELO: SlotApoyo
# -----------------------------------------------------------
class SlotApoyo(db.Model):
    id          = db.Column(db.Integer, primary_key=True)
    dia_semana  = db.Column(db.Integer, nullable=False)   # 0=lunes … 5=sábado
    hora_inicio = db.Column(db.Time,    nullable=False)
    hora_fin    = db.Column(db.Time,    nullable=False)

    reservas = db.relationship("ReservaApoyo", backref="slot", lazy=True)


# -----------------------------------------------------------
# MODELO: ReservaApoyo
# -----------------------------------------------------------
class ReservaApoyo(db.Model):
    id         = db.Column(db.Integer, primary_key=True)
    slot_id    = db.Column(db.Integer, db.ForeignKey("slot_apoyo.id"),    nullable=False)
    usuario_id = db.Column(db.Integer, db.ForeignKey("twitch_user.id"),   nullable=False)
    directo_id = db.Column(db.Integer, db.ForeignKey("directo.id"),       nullable=True)
    estado     = db.Column(db.String(20), default="pendiente")
    creada_en  = db.Column(db.DateTime,   default=datetime.utcnow)


# -----------------------------------------------------------
# MODELO: AdminCode  ← NUEVO
# Guarda los códigos de acceso al panel de admin.
# -----------------------------------------------------------
class AdminCode(db.Model):
    id        = db.Column(db.Integer, primary_key=True)
    codigo    = db.Column(db.String(100), unique=True, nullable=False)
    nombre    = db.Column(db.String(80))   # etiqueta (ej: "Patry", "Admin2")
    activo    = db.Column(db.Boolean, default=True)
    creado_en = db.Column(db.DateTime, default=datetime.utcnow)


# -----------------------------------------------------------
# MODELO: RegistroBot
# -----------------------------------------------------------
class RegistroBot(db.Model):
    id         = db.Column(db.Integer, primary_key=True)
    usuario_id = db.Column(db.Integer, db.ForeignKey("twitch_user.id"), nullable=False)
    directo_id = db.Column(db.Integer, db.ForeignKey("directo.id"),     nullable=False)
    timestamp  = db.Column(db.DateTime, nullable=False)
    tipo_evento= db.Column(db.String(50))
    detalle    = db.Column(db.String(200))


# -----------------------------------------------------------
# MODELO: Evento
# -----------------------------------------------------------
class Evento(db.Model):
    id           = db.Column(db.Integer, primary_key=True)
    titulo       = db.Column(db.String(200), nullable=False)
    contenido    = db.Column(db.Text,        nullable=True)
    fecha_inicio = db.Column(db.DateTime,    nullable=False)
    fecha_fin    = db.Column(db.DateTime,    nullable=False)
    url_twitch   = db.Column(db.String,      nullable=True)
    creado       = db.Column(db.DateTime, default=datetime.utcnow)
    actualizado  = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def __repr__(self):
        return f"<Evento {self.titulo}>"