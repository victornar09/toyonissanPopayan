"""Respaldos automáticos del servidor Toyonissan.

Genera una carpeta por ejecución con:

* copia consistente de la base SQLite activa (API ``backup``);
* XML y PDF de facturas;
* códigos de barras generados;
* plantillas BarTender;
* configuración del servidor y catálogos de estandarización.

Uso manual::

    python respaldo_sistema.py
    python respaldo_sistema.py --retencion 30
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import shutil
import threading
import time
from datetime import datetime, timedelta

import db


BASE_DIR = os.path.dirname(os.path.abspath(__file__))
BACKUPS_DIR = os.path.join(BASE_DIR, "backups")

_log = logging.getLogger("toyonissan.respaldo")

_CARPETAS = (
    ("bin/xml", "bin/xml"),
    ("bin/pdf", "bin/pdf"),
    ("bin/codigos", "bin/codigos"),
    ("bin/xlsx", "bin/xlsx"),
    ("bartenderFiles", "bartenderFiles"),
    ("config", "config"),
)

_ARCHIVOS = (
    ("backups/catalogo_codigos_barras.csv", "catalogo_codigos_barras.csv"),
    ("database/estructura.sql", "estructura.sql"),
)


def _copiar_carpeta(origen, destino):
    if not os.path.isdir(origen):
        return False
    if os.path.isdir(destino):
        shutil.rmtree(destino, ignore_errors=True)
    shutil.copytree(
        origen,
        destino,
        ignore=shutil.ignore_patterns(
            "*.pyc",
            "__pycache__",
            ".gitkeep",
            ".secret_key",
            "estado_servidor.json",
        ),
    )
    return True


def _copiar_archivo(origen, destino):
    if not os.path.isfile(origen):
        return False
    os.makedirs(os.path.dirname(destino), exist_ok=True)
    shutil.copy2(origen, destino)
    return True


def crear_respaldo(
    destino=None,
    base_dir=None,
    ruta_db=None,
    retencion=14,
    incluir_config=True,
):
    """Crea un respaldo completo y devuelve la ruta de la carpeta generada."""

    base_dir = base_dir or BASE_DIR
    backups_dir = os.path.join(base_dir, "backups")
    os.makedirs(backups_dir, exist_ok=True)

    marca = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    carpeta = destino or os.path.join(backups_dir, f"respaldo_{marca}")
    os.makedirs(carpeta, exist_ok=True)

    manifiesto = {
        "fecha": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "base_datos": None,
        "elementos": [],
        "errores": [],
    }

    ruta_db = ruta_db or db.ruta_bd_activa()
    if ruta_db and os.path.isfile(ruta_db):
        nombre = os.path.basename(ruta_db)
        destino_db = os.path.join(carpeta, "bdlocal", nombre)
        try:
            db.respaldar_base(destino_db, ruta=ruta_db)
            manifiesto["base_datos"] = f"bdlocal/{nombre}"
            manifiesto["elementos"].append(f"bdlocal/{nombre}")
        except Exception as error:
            manifiesto["errores"].append(f"Base de datos: {error}")
            _log.exception("No se pudo respaldar la base de datos")
    else:
        manifiesto["errores"].append("No hay base de datos activa.")

    for relativa, nombre_destino in _CARPETAS:
        if relativa == "config" and not incluir_config:
            continue
        origen = os.path.join(base_dir, relativa)
        destino_carpeta = os.path.join(carpeta, nombre_destino)
        try:
            if _copiar_carpeta(origen, destino_carpeta):
                manifiesto["elementos"].append(nombre_destino)
        except Exception as error:
            manifiesto["errores"].append(f"{relativa}: {error}")
            _log.exception("No se pudo respaldar %s", relativa)

    for relativa, nombre_destino in _ARCHIVOS:
        origen = os.path.join(base_dir, relativa)
        destino_archivo = os.path.join(carpeta, "catalogos", nombre_destino)
        try:
            if _copiar_archivo(origen, destino_archivo):
                manifiesto["elementos"].append(f"catalogos/{nombre_destino}")
        except Exception as error:
            manifiesto["errores"].append(f"{relativa}: {error}")
            _log.exception("No se pudo respaldar %s", relativa)

    with open(os.path.join(carpeta, "manifiesto.json"), "w", encoding="utf-8") as archivo:
        json.dump(manifiesto, archivo, ensure_ascii=False, indent=2)

    _log.info(
        "Respaldo creado en %s (%s elementos, %s errores)",
        carpeta,
        len(manifiesto["elementos"]),
        len(manifiesto["errores"]),
    )

    if retencion and int(retencion) > 0:
        limpiar_respaldos(backups_dir, retencion)

    return carpeta


def limpiar_respaldos(backups_dir=None, retencion=14):
    """Conserva las carpetas ``respaldo_*`` más recientes."""

    backups_dir = backups_dir or BACKUPS_DIR
    if not os.path.isdir(backups_dir):
        return []

    carpetas = sorted(
        (
            os.path.join(backups_dir, nombre)
            for nombre in os.listdir(backups_dir)
            if nombre.startswith("respaldo_")
            and os.path.isdir(os.path.join(backups_dir, nombre))
        ),
        key=os.path.getmtime,
        reverse=True,
    )

    eliminadas = []
    for carpeta in carpetas[int(retencion):]:
        shutil.rmtree(carpeta, ignore_errors=True)
        eliminadas.append(carpeta)

    return eliminadas


# ---------------------------------------------------------------------------
# Programador diario
# ---------------------------------------------------------------------------

_hilo = None
_parar = None
_lock = threading.Lock()


def _segundos_hasta(hora, minuto):
    ahora = datetime.now()
    objetivo = ahora.replace(hour=hora, minute=minuto, second=0, microsecond=0)
    if objetivo <= ahora:
        objetivo += timedelta(days=1)
    return max(30, (objetivo - ahora).total_seconds())


def _bucle_diario(hora, minuto, retencion, parar):
    while not parar.is_set():
        espera = _segundos_hasta(hora, minuto)
        if parar.wait(espera):
            break
        try:
            crear_respaldo(retencion=retencion)
        except Exception:
            _log.exception("Falló el respaldo automático")
        # Evita ejecutar dos veces en el mismo minuto.
        parar.wait(61)


def iniciar_respaldo_diario(hora=2, minuto=0, retencion=14):
    """Arranca un hilo daemon que respalda todos los días a la hora indicada."""

    global _hilo, _parar

    with _lock:
        if _hilo is not None and _hilo.is_alive():
            return _hilo

        _parar = threading.Event()
        _hilo = threading.Thread(
            target=_bucle_diario,
            args=(int(hora), int(minuto), int(retencion), _parar),
            name="respaldo-diario",
            daemon=True,
        )
        _hilo.start()
        _log.info(
            "Respaldo diario programado a las %02d:%02d (retención %s)",
            int(hora),
            int(minuto),
            retencion,
        )
        return _hilo


def detener_respaldo_diario(timeout=3):
    global _hilo, _parar

    with _lock:
        if _hilo is None:
            return
        if _parar is not None:
            _parar.set()
        _hilo.join(timeout=timeout)
        _hilo = None
        _parar = None


def _main():
    parser = argparse.ArgumentParser(description="Respaldo del servidor Toyonissan")
    parser.add_argument("--retencion", type=int, default=14)
    parser.add_argument("--destino", default=None)
    argumentos = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    carpeta = crear_respaldo(
        destino=argumentos.destino, retencion=argumentos.retencion
    )
    print(f"Respaldo creado: {carpeta}")


if __name__ == "__main__":
    _main()
