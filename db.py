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
    """Crea una conexión SQLite lista para uso concurrente en LAN.

    Cada solicitud abre su propia conexión. Se activa WAL para permitir
    lectores concurrentes mientras hay un escritor, ``busy_timeout`` para
    esperar bloqueos breves y claves foráneas para mantener integridad.
    """

    if ruta is None:
        ruta = ruta_bd_activa()

    if ruta is None:
        raise RuntimeError("No hay una base de datos activa.")

    conn = sqlite3.connect(ruta, timeout=30)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 30000")

    try:
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA synchronous = NORMAL")
    except sqlite3.DatabaseError:
        # Bases en memoria o de solo lectura: se mantienen operativas.
        pass

    return conn


def es_error_de_bloqueo(error):
    """Indica si el error corresponde a una base SQLite ocupada."""

    texto = str(error).lower()
    return "database is locked" in texto or "database table is locked" in texto


def mensaje_de_bloqueo():
    return (
        "La base de datos está ocupada por otra operación. Espere unos "
        "segundos y vuelva a intentarlo."
    )


def respaldar_base(destino, ruta=None):
    """Copia consistente de la base activa usando la API de backup de SQLite.

    No se debe copiar el archivo ``.sqlite`` activo con ``shutil`` porque
    WAL puede contener transacciones aún no consolidadas.
    """

    ruta = ruta or ruta_bd_activa()
    if not ruta:
        raise RuntimeError("No hay una base de datos activa para respaldar.")

    destino = os.path.abspath(destino)
    os.makedirs(os.path.dirname(destino), exist_ok=True)

    origen = sqlite3.connect(ruta, timeout=30)
    copia = sqlite3.connect(destino)
    try:
        origen.backup(copia)
        copia.commit()
    finally:
        copia.close()
        origen.close()

    return destino


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
