"""Arranque de producción local con Waitress.

Uso::

    python run_server.py

El host, puerto e hilos se leen de ``config/servidor.json`` (editable desde
Configuración).  Para operación diaria no se usa el servidor de desarrollo de
Flask.
"""

from __future__ import annotations

import logging
import os
import sys
from logging.handlers import RotatingFileHandler

import configuracion_servidor as config_servidor
import db
import migraciones
import respaldo_sistema
import seguridad
from impresion import servicio as impresion_servicio


BASE_DIR = os.path.dirname(os.path.abspath(__file__))
LOGS_DIR = os.path.join(BASE_DIR, "logs")


def configurar_logs():
    os.makedirs(LOGS_DIR, exist_ok=True)
    ruta_log = os.path.join(LOGS_DIR, "servidor.log")

    formato = logging.Formatter(
        "%(asctime)s %(levelname)s [%(name)s] %(message)s"
    )

    archivo = RotatingFileHandler(
        ruta_log,
        maxBytes=5 * 1024 * 1024,
        backupCount=5,
        encoding="utf-8",
    )
    archivo.setFormatter(formato)
    archivo.setLevel(logging.INFO)

    consola = logging.StreamHandler()
    consola.setFormatter(formato)
    consola.setLevel(logging.INFO)

    raiz = logging.getLogger()
    raiz.setLevel(logging.INFO)
    if not any(isinstance(h, RotatingFileHandler) for h in raiz.handlers):
        raiz.addHandler(archivo)
    if not any(isinstance(h, logging.StreamHandler) for h in raiz.handlers):
        raiz.addHandler(consola)

    return logging.getLogger("toyonissan.servidor")


def _preparar_aplicacion(app):
    """Inicializa secreto, base de datos y migraciones."""

    app.secret_key = seguridad.cargar_secreto()
    app.config["DEBUG"] = False
    app.config["PROPAGATE_EXCEPTIONS"] = False

    if not os.environ.get("TOYONISSAN_SECRET_KEY"):
        logging.getLogger("toyonissan.servidor").warning(
            "TOYONISSAN_SECRET_KEY no está definida. Se usa la clave persistida "
            "en config/.secret_key. En producción defina la variable de entorno."
        )

    ruta_db = db.ruta_bd_activa()
    if ruta_db:
        conn = db.conectar(ruta_db)
        try:
            migraciones.migrar(conn)
        finally:
            conn.close()
    else:
        logging.getLogger("toyonissan.servidor").warning(
            "No hay base de datos activa. Créela desde Configuración."
        )

    os.makedirs(impresion_servicio.TEMPORAL_DIR, exist_ok=True)


def main(app=None, host=None, puerto=None):
    configurar_logs()
    log = logging.getLogger("toyonissan.servidor")

    if app is None:
        from app import app as aplicacion

        app = aplicacion

    configuracion = config_servidor.cargar_configuracion()
    host = host or config_servidor.host_escucha(configuracion)
    puerto = int(puerto or configuracion.get("puerto", 5000))
    hilos = int(configuracion.get("hilos", 6))

    _preparar_aplicacion(app)

    try:
        from waitress import serve
    except ImportError:
        log.error(
            "Waitress no está instalado. Ejecute: pip install waitress "
            "(o pip install -r requirementsWindows.txt)."
        )
        print(
            "ERROR: Waitress no está instalado. Ejecute:\n"
            "    pip install waitress"
        )
        return 2

    url_local = f"http://127.0.0.1:{puerto}"
    url_lan = config_servidor.url_clientes(configuracion)

    config_servidor.escribir_estado_servidor(configuracion)

    impresion_servicio.iniciar_worker()
    try:
        hora_respaldo = int(os.environ.get("TOYONISSAN_BACKUP_HORA", "2"))
    except ValueError:
        hora_respaldo = 2
    respaldo_sistema.iniciar_respaldo_diario(hora=hora_respaldo)

    log.info("=" * 64)
    log.info("Toyonissan Popayán - servidor local")
    log.info("Nombre: %s", configuracion.get("nombre_servidor"))
    log.info("Escuchando en %s:%s con %s hilos", host, puerto, hilos)
    log.info("Acceso local: %s", url_local)
    log.info("Acceso para clientes LAN: %s", url_lan)
    log.info("Respaldo diario: %02d:00 en backups/", hora_respaldo)
    log.info("Detener con Ctrl+C")
    log.info("=" * 64)

    print("")
    print("=" * 64)
    print(" Toyonissan Popayán - servidor local")
    print(f" Acceso en este equipo : {url_local}")
    print(f" Acceso para la LAN    : {url_lan}")
    print(" Detener con Ctrl+C")
    print("=" * 64)
    print("")

    try:
        serve(
            app,
            host=host,
            port=puerto,
            threads=hilos,
            url_scheme="http",
            channel_timeout=180,
            ident="Toyonissan",
        )
    except KeyboardInterrupt:
        log.info("Detención solicitada por el usuario")
    finally:
        impresion_servicio.detener_worker()
        respaldo_sistema.detener_respaldo_diario()
        config_servidor.eliminar_estado_servidor()
        log.info("Servidor detenido")

    return 0


def _main_cli():
    import argparse

    parser = argparse.ArgumentParser(description="Servidor Toyonissan con Waitress")
    parser.add_argument("--host", default=None)
    parser.add_argument("--puerto", type=int, default=None)
    argumentos = parser.parse_args()
    return main(host=argumentos.host, puerto=argumentos.puerto)


if __name__ == "__main__":
    sys.exit(_main_cli())
