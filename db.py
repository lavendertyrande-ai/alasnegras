from flask_sqlalchemy import SQLAlchemy

# -----------------------------------------------------------
# Este archivo define una única instancia global de SQLAlchemy.
# No se asocia a ninguna app aquí, solo se crea el objeto.
#
# Luego, en main.py, se hace:
#     db.init_app(app)
#
# Esto permite que todos los modelos importen "db" desde aquí
# sin crear múltiples instancias ni romper migraciones.
# -----------------------------------------------------------

db = SQLAlchemy()
