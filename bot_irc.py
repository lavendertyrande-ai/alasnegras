# -----------------------------------------------------------
# BOT IRC DE TWITCH — Supervisor de apoyos
# -----------------------------------------------------------
import os
import threading
import websocket
import time
from datetime import datetime, timedelta
import pytz

TZ_SPAIN = pytz.timezone("Europe/Madrid")

BOT_NICK  = os.environ.get("BOT_NICK", "")
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")

# Canales activos y sus viewers
# { login_canal: { login_usuario: { "entrada": datetime, "mensajes": int } } }
_estado = {}
_lock   = threading.Lock()
_ws     = None
_app    = None


def iniciar_bot(app):
    """Llama esto desde main.py para arrancar el bot en un hilo."""
    global _app
    _app = app
    t = threading.Thread(target=_run_bot, daemon=True)
    t.start()


def unirse_canal(canal_login):
    """Llama esto cuando empieza una hora agendada."""
    global _ws, _estado
    if not _ws:
        return
    canal = canal_login.lower()
    with _lock:
        if canal not in _estado:
            _estado[canal] = {}
    _ws.send(f"JOIN #{canal}\r\n")
    print(f">>> Bot unido a #{canal}")


def salir_canal(canal_login):
    """Llama esto cuando termina la hora agendada."""
    global _ws, _estado
    if not _ws:
        return
    canal = canal_login.lower()
    _ws.send(f"PART #{canal}\r\n")
    with _lock:
        if canal in _estado:
            # Guardar datos antes de salir
            _guardar_apoyos(canal)
            del _estado[canal]
    print(f">>> Bot salió de #{canal}")


def _guardar_apoyos(canal_login):
    """Guarda los datos de apoyo en la BD."""
    if not _app:
        return
    with _app.app_context():
        from models import TwitchUser, RegistroApoyo
        from db import db

        canal_user = TwitchUser.query.filter_by(login=canal_login).first()
        if not canal_user:
            return

        semana = datetime.now(TZ_SPAIN).strftime("%Y-W%W")

        with _lock:
            viewers = dict(_estado.get(canal_login, {}))

        for login_usuario, datos in viewers.items():
            if login_usuario == canal_login:
                continue  # No registrar al propio streamer

            usuario = TwitchUser.query.filter_by(login=login_usuario).first()
            if not usuario:
                continue

            # Calcular minutos
            entrada = datos.get("entrada")
            minutos = 0
            if entrada:
                delta = datetime.now(TZ_SPAIN) - entrada
                minutos = int(delta.total_seconds() / 60)

            mensajes = datos.get("mensajes", 0)

            # Buscar registro existente de esta semana
            registro = RegistroApoyo.query.filter_by(
                usuario_id=usuario.id,
                canal_id=canal_user.id,
                semana=semana
            ).first()

            if registro:
                registro.minutos  += minutos
                registro.mensajes += mensajes
                registro.ultima_vez = datetime.utcnow()
            else:
                db.session.add(RegistroApoyo(
                    usuario_id=usuario.id,
                    canal_id=canal_user.id,
                    semana=semana,
                    minutos=minutos,
                    mensajes=mensajes,
                ))
        db.session.commit()
        print(f">>> Apoyos guardados para #{canal_login}")


def _on_message(ws, message):
    global _estado

    # Responder PING de Twitch
    if message.startswith("PING"):
        ws.send("PONG :tmi.twitch.tv\r\n")
        return

    # Parsear mensajes
    # JOIN: alguien entra al canal
    if "JOIN #" in message:
        try:
            usuario = message.split("!")[0].lstrip(":")
            canal   = message.split("JOIN #")[1].strip()
            with _lock:
                if canal in _estado:
                    if usuario not in _estado[canal]:
                        _estado[canal][usuario] = {
                            "entrada":  datetime.now(TZ_SPAIN),
                            "mensajes": 0
                        }
        except:
            pass

    # PART: alguien sale del canal
    elif "PART #" in message:
        try:
            usuario = message.split("!")[0].lstrip(":")
            canal   = message.split("PART #")[1].strip()
            with _lock:
                if canal in _estado and usuario in _estado[canal]:
                    # Guardar tiempo parcial
                    entrada = _estado[canal][usuario].get("entrada")
                    if entrada:
                        delta   = datetime.now(TZ_SPAIN) - entrada
                        minutos = int(delta.total_seconds() / 60)
                        _estado[canal][usuario]["minutos_acum"] = (
                            _estado[canal][usuario].get("minutos_acum", 0) + minutos
                        )
                    del _estado[canal][usuario]
        except:
            pass

    # PRIVMSG: mensaje en el chat
    elif "PRIVMSG #" in message:
        try:
            usuario = message.split("!")[0].lstrip(":")
            canal   = message.split("PRIVMSG #")[1].split(":")[0].strip()
            with _lock:
                if canal in _estado:
                    if usuario not in _estado[canal]:
                        _estado[canal][usuario] = {
                            "entrada":  datetime.now(TZ_SPAIN),
                            "mensajes": 0
                        }
                    _estado[canal][usuario]["mensajes"] += 1
        except:
            pass


def _on_open(ws):
    global _ws
    _ws = ws
    ws.send(f"PASS oauth:{BOT_TOKEN}\r\n")
    ws.send(f"NICK {BOT_NICK}\r\n")
    ws.send("CAP REQ :twitch.tv/membership\r\n")
    print(">>> Bot IRC conectado a Twitch")


def _on_error(ws, error):
    print(f">>> Bot IRC error: {error}")


def _on_close(ws, close_status_code, close_msg):
    print(">>> Bot IRC desconectado, reconectando en 30s...")
    time.sleep(30)
    _run_bot()


def _run_bot():
    if not BOT_NICK or not BOT_TOKEN:
        print(">>> Bot IRC: faltan BOT_NICK o BOT_TOKEN")
        return
    ws = websocket.WebSocketApp(
        "wss://irc-ws.chat.twitch.tv:443",
        on_open=_on_open,
        on_message=_on_message,
        on_error=_on_error,
        on_close=_on_close,
    )
    ws.run_forever()