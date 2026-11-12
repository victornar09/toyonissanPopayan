"""Cola de impresión centralizada para BarTender.

Un único worker procesa los trabajos en orden.  Cada trabajo usa su propio
archivo CSV dentro de ``temp/`` y, antes de imprimir, esos mismos datos se
publican en el CSV compartido que tienen enlazado las plantillas BarTender
(``productos_temp.csv`` por defecto).
"""

from __future__ import annotations

import csv
import json
import logging
import os
import sqlite3
import threading
import time
from datetime import datetime

import db


BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TEMPORAL_DIR = os.path.join(BASE_DIR, "temp")

PENDIENTE = "PENDIENTE"
PROCESANDO = "PROCESANDO"
IMPRESO = "IMPRESO"
ERROR = "ERROR"

ESTADOS = (PENDIENTE, PROCESANDO, IMPRESO, ERROR)

_log = logging.getLogger("toyonissan.impresion")


# ---------------------------------------------------------------------------
# Normalización de artículos
# ---------------------------------------------------------------------------

def _a_texto(valor):
    return "" if valor is None else str(valor)


def normalizar_articulos(articulos):
    """Convierte tuplas o diccionarios a una lista uniforme de diccionarios."""

    resultado = []
    for articulo in articulos or []:
        if isinstance(articulo, dict):
            codigo = articulo.get("codigo") or articulo.get("codigoBarras")
            descripcion = articulo.get("descripcion")
            precio = articulo.get("precio")
            if precio in (None, ""):
                precio = articulo.get("precioVentaCifrado")
            cantidad = articulo.get("cantidad", 1)
            proveedor = articulo.get("proveedor") or articulo.get("siglas")
        else:
            valores = list(articulo)
            codigo = valores[0] if len(valores) > 0 else None
            descripcion = valores[1] if len(valores) > 1 else None
            precio = valores[2] if len(valores) > 2 else None
            cantidad = valores[3] if len(valores) > 3 else 1
            proveedor = valores[4] if len(valores) > 4 else None

        try:
            cantidad = int(cantidad)
        except (TypeError, ValueError):
            cantidad = 1
        if cantidad < 1:
            cantidad = 1

        resultado.append(
            {
                "codigo": _a_texto(codigo),
                "descripcion": _a_texto(descripcion),
                "precio": _a_texto(precio),
                "cantidad": cantidad,
                "proveedor": _a_texto(proveedor),
            }
        )

    return resultado


def _fila_a_dict(fila):
    if fila is None:
        return None

    columnas = [
        "idTrabajo",
        "usuario",
        "fecha",
        "fechaInicio",
        "fechaFin",
        "articulos",
        "cantidades",
        "idPlantilla",
        "plantilla",
        "rutaPlantilla",
        "archivoTemporal",
        "columnaInicio",
        "origen",
        "estado",
        "mensajeError",
        "intentos",
        "claveUnica",
    ]
    datos = dict(zip(columnas, fila))
    try:
        datos["articulos"] = json.loads(datos.get("articulos") or "[]")
    except ValueError:
        datos["articulos"] = []
    return datos


_COLUMNAS = """
    idTrabajo, usuario, fecha, fechaInicio, fechaFin, articulos,
    cantidades, idPlantilla, plantilla, rutaPlantilla, archivoTemporal,
    columnaInicio, origen, estado, mensajeError, intentos, claveUnica
"""


# ---------------------------------------------------------------------------
# Creación y consulta
# ---------------------------------------------------------------------------

def crear_trabajo(
    ruta_db,
    usuario,
    articulos,
    id_plantilla=None,
    plantilla=None,
    ruta_plantilla=None,
    origen=None,
    columna_inicio=1,
    clave_unica=None,
):
    """Crea un trabajo PENDIENTE.

    Si ``clave_unica`` ya existe (doble clic o doble envío) devuelve el
    trabajo existente sin crear otro.
    """

    articulos_normalizados = normalizar_articulos(articulos)
    if not articulos_normalizados:
        raise ValueError("El trabajo de impresión no contiene artículos.")

    if not ruta_plantilla and id_plantilla:
        conn = db.conectar(ruta_db)
        try:
            fila = conn.execute(
                "SELECT name, ubicacion FROM bartenderFiles WHERE idBartenderFiles = ?",
                (id_plantilla,),
            ).fetchone()
        finally:
            conn.close()
        if not fila:
            raise ValueError("La plantilla BarTender seleccionada no existe.")
        plantilla = plantilla or fila[0]
        ruta_plantilla = fila[1]

    if not ruta_plantilla:
        raise ValueError("La plantilla BarTender no está configurada.")

    clave_unica = (str(clave_unica).strip() or None) if clave_unica else None

    conn = db.conectar(ruta_db)
    try:
        cur = conn.execute(
            """
            INSERT INTO trabajoImpresion
                (usuario, articulos, cantidades, idPlantilla, plantilla,
                 rutaPlantilla, columnaInicio, origen, estado, claveUnica)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                usuario,
                json.dumps(articulos_normalizados, ensure_ascii=False),
                json.dumps(
                    [articulo["cantidad"] for articulo in articulos_normalizados]
                ),
                id_plantilla,
                plantilla,
                ruta_plantilla,
                int(columna_inicio or 1),
                origen,
                PENDIENTE,
                clave_unica,
            ),
        )
        conn.commit()
        id_trabajo = cur.lastrowid
    except sqlite3.IntegrityError:
        if not clave_unica:
            raise
        fila = conn.execute(
            f"SELECT {_COLUMNAS} FROM trabajoImpresion WHERE claveUnica = ?",
            (clave_unica,),
        ).fetchone()
        conn.rollback()
        if not fila:
            raise
        datos = _fila_a_dict(fila)
        datos["duplicado"] = True
        return datos
    finally:
        conn.close()

    trabajo = obtener_trabajo(ruta_db, id_trabajo)
    trabajo["duplicado"] = False
    return trabajo


def obtener_trabajo(ruta_db, id_trabajo):
    conn = db.conectar(ruta_db)
    try:
        fila = conn.execute(
            f"SELECT {_COLUMNAS} FROM trabajoImpresion WHERE idTrabajo = ?",
            (id_trabajo,),
        ).fetchone()
    finally:
        conn.close()
    return _fila_a_dict(fila)


def listar_trabajos(ruta_db, limite=25, estados=None):
    limite = max(1, min(int(limite or 25), 200))
    consulta = f"SELECT {_COLUMNAS} FROM trabajoImpresion"
    parametros = []
    if estados:
        consulta += f" WHERE estado IN ({','.join('?' * len(estados))})"
        parametros.extend(estados)
    consulta += " ORDER BY idTrabajo DESC LIMIT ?"
    parametros.append(limite)

    conn = db.conectar(ruta_db)
    try:
        filas = conn.execute(consulta, tuple(parametros)).fetchall()
    finally:
        conn.close()

    return [_fila_a_dict(fila) for fila in filas]


def reintentar_trabajo(ruta_db, id_trabajo):
    """Devuelve un trabajo en ERROR a la cola sin duplicar inventariado."""

    conn = db.conectar(ruta_db)
    try:
        fila = conn.execute(
            "SELECT estado FROM trabajoImpresion WHERE idTrabajo = ?",
            (id_trabajo,),
        ).fetchone()
        if not fila:
            raise ValueError("El trabajo de impresión no existe.")
        if fila[0] not in {ERROR, PENDIENTE}:
            raise ValueError(
                "Solo se pueden reintentar trabajos en estado ERROR o PENDIENTE."
            )
        conn.execute(
            """
            UPDATE trabajoImpresion
            SET estado = ?, mensajeError = NULL, fechaInicio = NULL,
                fechaFin = NULL
            WHERE idTrabajo = ?
            """,
            (PENDIENTE, id_trabajo),
        )
        conn.commit()
    finally:
        conn.close()

    return obtener_trabajo(ruta_db, id_trabajo)


def reimprimir_trabajo(ruta_db, id_trabajo, usuario=None):
    """Crea un trabajo nuevo a partir de uno anterior (reimpresión)."""

    original = obtener_trabajo(ruta_db, id_trabajo)
    if not original:
        raise ValueError("El trabajo de impresión no existe.")

    return crear_trabajo(
        ruta_db,
        usuario or original.get("usuario"),
        original.get("articulos"),
        id_plantilla=original.get("idPlantilla"),
        plantilla=original.get("plantilla"),
        ruta_plantilla=original.get("rutaPlantilla"),
        origen=original.get("origen"),
        columna_inicio=original.get("columnaInicio") or 1,
        clave_unica=None,
    )


# ---------------------------------------------------------------------------
# Motor BarTender
# ---------------------------------------------------------------------------

def _ruta_csv_compartido():
    """CSV enlazado en las plantillas BarTender.

    Por defecto es ``productos_temp.csv`` en la raíz del proyecto, que es el
    archivo que leen las plantillas existentes.  Se puede cambiar con la
    variable de entorno ``TOYONISSAN_CSV_IMPRESION``.
    """

    personalizada = os.environ.get("TOYONISSAN_CSV_IMPRESION", "").strip()
    if personalizada:
        return os.path.abspath(os.path.expanduser(personalizada))
    return os.path.join(BASE_DIR, "productos_temp.csv")


def _filas_csv(articulos, columna_inicio=1):
    incluye_proveedor = any(
        articulo.get("proveedor") for articulo in articulos
    )

    if incluye_proveedor:
        filas = [
            ["codigo", "descripcion", "precio", "copias", "proveedor",
             "inicio_columna"]
        ]
    else:
        filas = [
            ["codigo", "descripcion", "precio", "copias", "inicio_columna"]
        ]

    for articulo in articulos:
        fila = [
            articulo.get("codigo", ""),
            articulo.get("descripcion", ""),
            articulo.get("precio", ""),
            articulo.get("cantidad", 1),
        ]
        if incluye_proveedor:
            fila.append(articulo.get("proveedor", ""))
        fila.append(columna_inicio)
        filas.append(fila)

    return filas


def _escribir_csv_en(ruta, articulos, columna_inicio=1):
    directorio = os.path.dirname(ruta)
    if directorio:
        os.makedirs(directorio, exist_ok=True)

    with open(ruta, "w", newline="", encoding="utf-8") as archivo:
        csv.writer(archivo).writerows(
            _filas_csv(articulos, columna_inicio)
        )


def _escribir_csv(id_trabajo, articulos, columna_inicio=1):
    fecha = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    ruta_csv = os.path.join(
        TEMPORAL_DIR, f"impresion_{id_trabajo}_{fecha}.csv"
    )
    _escribir_csv_en(ruta_csv, articulos, columna_inicio)
    return ruta_csv


def _publicar_csv_compartido(articulos, columna_inicio=1):
    """Actualiza de forma atómica el CSV que leen las plantillas BarTender.

    BarTender abre el archivo al imprimir; si está bloqueado se reintenta unos
    instantes.  Si no se puede actualizar se lanza un error para no imprimir
    etiquetas con los valores del trabajo anterior.
    """

    destino = _ruta_csv_compartido()
    directorio = os.path.dirname(destino) or BASE_DIR
    os.makedirs(directorio, exist_ok=True)

    temporal = os.path.join(
        directorio,
        f".productos_temp_{os.getpid()}_{threading.get_ident()}.tmp",
    )

    try:
        _escribir_csv_en(temporal, articulos, columna_inicio)
        for intento in range(6):
            try:
                os.replace(temporal, destino)
                break
            except PermissionError:
                if intento == 5:
                    raise RuntimeError(
                        "No se pudo actualizar el CSV de impresión "
                        f"'{destino}'. Cierre BarTender o verifique que el "
                        "archivo no esté bloqueado e intente de nuevo."
                    )
                time.sleep(0.2)
    finally:
        if os.path.exists(temporal):
            try:
                os.remove(temporal)
            except OSError:
                pass

    return destino


def _motor_bartender(ruta_csv, ruta_plantilla):
    """Abre BarTender por COM y envía la plantilla a la impresora."""

    if os.environ.get("TOYONISSAN_BARTENDER_SIMULADO") == "1":
        _log.warning(
            "BarTender simulado (TOYONISSAN_BARTENDER_SIMULADO=1): %s",
            ruta_csv,
        )
        time.sleep(0.1)
        return

    import pythoncom
    import win32com.client

    pythoncom.CoInitialize()
    aplicacion = None
    formato = None
    try:
        aplicacion = win32com.client.Dispatch("BarTender.Application")
        aplicacion.Visible = False
        formato = aplicacion.Formats.Open(
            os.path.abspath(ruta_plantilla), False, ""
        )
        formato.PrintOut(False, False)
    finally:
        try:
            if formato is not None:
                formato.Close(0)
        except Exception:
            pass
        try:
            if aplicacion is not None:
                aplicacion.Quit(1)
        except Exception:
            pass
        pythoncom.CoUninitialize()


# ---------------------------------------------------------------------------
# Procesamiento
# ---------------------------------------------------------------------------

def _actualizar_estado_inventario(conn, articulos, estado):
    codigos = [a.get("codigo") for a in articulos if a.get("codigo")]
    if not codigos:
        return
    hoy = datetime.now().strftime("%Y-%m-%d")
    for codigo in codigos:
        try:
            conn.execute(
                """
                UPDATE inventarioUnico
                SET estadoEtiquetado = ?, fechaActualizacion = ?
                WHERE codigoBarras = ?
                """,
                (estado, hoy, codigo),
            )
        except sqlite3.Error:
            pass


def procesar_siguiente(ruta_db=None, motor=None):
    """Toma el siguiente trabajo PENDIENTE y lo procesa.

    La transacción que marca el trabajo como PROCESANDO se cierra antes de
    llamar a BarTender, de modo que no queda abierta durante COM ni archivos.
    """

    ruta_db = ruta_db or db.ruta_bd_activa()
    if not ruta_db:
        return None

    motor = motor or _motor_bartender

    conn = db.conectar(ruta_db)
    try:
        conn.execute("BEGIN IMMEDIATE")
        fila = conn.execute(
            f"""
            SELECT {_COLUMNAS}
            FROM trabajoImpresion
            WHERE estado = ?
            ORDER BY fecha, idTrabajo
            LIMIT 1
            """,
            (PENDIENTE,),
        ).fetchone()

        if not fila:
            conn.rollback()
            return None

        trabajo = _fila_a_dict(fila)
        conn.execute(
            """
            UPDATE trabajoImpresion
            SET estado = ?, fechaInicio = datetime('now', 'localtime'),
                intentos = intentos + 1, mensajeError = NULL
            WHERE idTrabajo = ? AND estado = ?
            """,
            (PROCESANDO, trabajo["idTrabajo"], PENDIENTE),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    id_trabajo = trabajo["idTrabajo"]
    articulos = trabajo.get("articulos") or []
    ruta_csv = None
    error_mensaje = None

    try:
        ruta_csv = _escribir_csv(
            id_trabajo, articulos, trabajo.get("columnaInicio") or 1
        )
        # Las plantillas BarTender están enlazadas al CSV compartido; hay que
        # publicar en él los valores de este trabajo justo antes de imprimir.
        _publicar_csv_compartido(
            articulos, trabajo.get("columnaInicio") or 1
        )

        conn = db.conectar(ruta_db)
        try:
            conn.execute(
                "UPDATE trabajoImpresion SET archivoTemporal = ? WHERE idTrabajo = ?",
                (os.path.basename(ruta_csv), id_trabajo),
            )
            conn.commit()
        finally:
            conn.close()

        motor(ruta_csv, trabajo.get("rutaPlantilla"))
        estado_final = IMPRESO
    except Exception as error:
        estado_final = ERROR
        error_mensaje = str(error) or error.__class__.__name__
        _log.exception("Error imprimiendo trabajo %s", id_trabajo)

    conn = db.conectar(ruta_db)
    try:
        conn.execute(
            """
            UPDATE trabajoImpresion
            SET estado = ?, fechaFin = datetime('now', 'localtime'),
                mensajeError = ?
            WHERE idTrabajo = ?
            """,
            (estado_final, error_mensaje, id_trabajo),
        )
        if estado_final == IMPRESO:
            _actualizar_estado_inventario(conn, articulos, IMPRESO)
        conn.commit()
    finally:
        conn.close()

    trabajo = obtener_trabajo(ruta_db, id_trabajo)
    return trabajo


def recuperar_colgados(ruta_db=None):
    """Marca como ERROR trabajos que quedaron PROCESANDO por un cierre brusco."""

    ruta_db = ruta_db or db.ruta_bd_activa()
    if not ruta_db:
        return 0
    conn = db.conectar(ruta_db)
    try:
        cur = conn.execute(
            """
            UPDATE trabajoImpresion
            SET estado = ?, mensajeError = ?,
                fechaFin = datetime('now', 'localtime')
            WHERE estado = ?
            """,
            (
                ERROR,
                "El servidor se interrumpió durante la impresión. "
                "Use Reintentar para volver a enviar el trabajo.",
                PROCESANDO,
            ),
        )
        conn.commit()
        return cur.rowcount or 0
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Worker único
# ---------------------------------------------------------------------------

_worker_thread = None
_worker_parar = None
_worker_lock = threading.Lock()


def _bucle_worker(ruta_db_getter, intervalo, motor, parar):
    ruta = ruta_db_getter()
    if ruta:
        try:
            colgados = recuperar_colgados(ruta)
            if colgados:
                _log.warning("Trabajos de impresión recuperados: %s", colgados)
        except Exception:
            _log.exception("No se pudieron recuperar trabajos colgados")

    while not parar.is_set():
        try:
            ruta = ruta_db_getter()
            if ruta:
                trabajo = procesar_siguiente(ruta, motor=motor)
                if trabajo:
                    _log.info(
                        "Trabajo %s -> %s",
                        trabajo.get("idTrabajo"),
                        trabajo.get("estado"),
                    )
                    continue
        except Exception:
            _log.exception("Error en el worker de impresión")

        parar.wait(intervalo)


def iniciar_worker(ruta_db_getter=None, intervalo=2.0, motor=None):
    """Arranca el worker único. Llamadas adicionales no crean otro hilo."""

    global _worker_thread, _worker_parar

    with _worker_lock:
        if _worker_thread is not None and _worker_thread.is_alive():
            return _worker_thread

        ruta_db_getter = ruta_db_getter or db.ruta_bd_activa
        _worker_parar = threading.Event()
        _worker_thread = threading.Thread(
            target=_bucle_worker,
            args=(ruta_db_getter, max(0.5, float(intervalo)), motor, _worker_parar),
            name="impresion-worker",
            daemon=True,
        )
        _worker_thread.start()
        _log.info("Worker de impresión iniciado")
        return _worker_thread


def detener_worker(timeout=5):
    global _worker_thread, _worker_parar

    with _worker_lock:
        if _worker_thread is None:
            return
        if _worker_parar is not None:
            _worker_parar.set()
        _worker_thread.join(timeout=timeout)
        _worker_thread = None
        _worker_parar = None
        _log.info("Worker de impresión detenido")


def worker_activo():
    return _worker_thread is not None and _worker_thread.is_alive()
