"""Acceso centralizado a la base de datos SQLite.

Antes cada módulo escogía la base con ``os.listdir(...)[-1]`` (orden no
garantizado). Ahora la base activa se persiste en ``bdlocal/.bd_activa``
y todo el código obtiene la conexión desde aquí.
"""

import os
import sqlite3
from contextlib import contextmanager


BASE_DIR = os.path.dirname(os.path.abspath(__file__))
BD_DIR = os.path.join(BASE_DIR, "bdlocal")
_MARCA_BD = os.path.join(BD_DIR, ".bd_activa")


def asegurar_directorio():
    os.makedirs(BD_DIR, exist_ok=True)


def listar_bases():
    """Devuelve las bases .sqlite ordenadas por fecha de modificación."""

    asegurar_directorio()

    archivos = [
        nombre
        for nombre in os.listdir(BD_DIR)
        if nombre.endswith(".sqlite")
    ]

    archivos.sort(
        key=lambda nombre: os.path.getmtime(os.path.join(BD_DIR, nombre)),
        reverse=True,
    )

    return archivos


def _leer_marca():
    try:
        with open(_MARCA_BD, "r", encoding="utf-8") as archivo:
            nombre = archivo.read().strip()
        return nombre or None
    except FileNotFoundError:
        return None


def obtener_bd_activa():
    """Nombre del archivo .sqlite activo o ``None`` si no hay bases."""

    asegurar_directorio()

    bases = listar_bases()

    if not bases:
        return None

    marca = _leer_marca()

    if marca and marca in bases:
        return marca

    # La marca no existe o apunta a una base borrada: usar la más reciente
    # y dejarla persistida para las siguientes ejecuciones.
    return establecer_bd_activa(bases[0])


def establecer_bd_activa(nombre):
    """Persiste el nombre de la base activa. Devuelve el nombre."""

    asegurar_directorio()

    with open(_MARCA_BD, "w", encoding="utf-8") as archivo:
        archivo.write(nombre)

    return nombre


def ruta_bd(nombre):
    return os.path.join(BD_DIR, nombre)


def ruta_bd_activa():
    """Ruta absoluta de la base activa o ``None`` si no hay bases."""

    nombre = obtener_bd_activa()

    return ruta_bd(nombre) if nombre else None


def conectar(ruta=None):
    """Crea una conexión SQLite con configuración razonable."""

    if ruta is None:
        ruta = ruta_bd_activa()

    if ruta is None:
        raise RuntimeError("No hay una base de datos activa.")

    conn = sqlite3.connect(ruta, timeout=30)
    conn.execute("PRAGMA foreign_keys = ON")

    return conn


@contextmanager
def conexion(ruta=None):
    """Context manager que confirma y cierra la conexión."""

    conn = conectar(ruta)

    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
