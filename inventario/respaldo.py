"""Respaldo independiente del catálogo código de barras ↔ referencia.

SQLite sigue siendo la fuente oficial durante la operación normal. Este
módulo mantiene un CSV estable (``backups/catalogo_codigos_barras.csv``) y
copias históricas con fecha. La escritura es atómica: se escribe un archivo
temporal en el mismo directorio y luego se reemplaza el archivo principal.
"""

from __future__ import annotations

import csv
import io
import os
import tempfile
from datetime import datetime

import db


BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DIRECTORIO = os.path.join(BASE_DIR, "backups")
NOMBRE_PRINCIPAL = "catalogo_codigos_barras.csv"

CAMPOS = [
    "id",
    "codigoBarras",
    "referenciaProveedor",
    "referenciaRepuesto",
    "descripcionEstandarizada",
    "idConsolidado",
    "fechaActualizacion",
]

CONSULTA = """
    SELECT
        iu.id,
        COALESCE(iu.codigoBarras, '') AS codigoBarras,
        COALESCE(c.referenciaProveedor, iu.referencia, '') AS referenciaProveedor,
        COALESCE(c.referenciaRepuesto, '') AS referenciaRepuesto,
        COALESCE(c.descripcionEstandarizada, iu.descripcion, '')
            AS descripcionEstandarizada,
        c.id AS idConsolidado,
        COALESCE(iu.fechaActualizacion, '') AS fechaActualizacion
    FROM inventarioUnico iu
    LEFT JOIN consolidado c
        ON c.id = (
            SELECT MIN(c2.id)
            FROM consolidado c2
            WHERE c2.idInventarioUnico = iu.id
        )
    ORDER BY iu.id
"""


def _directorio(directorio=None):
    ruta = directorio or DIRECTORIO
    os.makedirs(ruta, exist_ok=True)
    return ruta


def ruta_principal(directorio=None):
    return os.path.join(_directorio(directorio), NOMBRE_PRINCIPAL)


def _filas_desde_conexion(conn):
    cursor = conn.cursor()
    cursor.execute(CONSULTA)
    return cursor.fetchall()


def _texto(valor):
    if valor is None:
        return ""
    return str(valor).strip()


def _escribir_atomico(ruta_destino, filas):
    """Escribe el CSV en un temporal y reemplaza el destino atómicamente."""

    directorio = os.path.dirname(ruta_destino)
    os.makedirs(directorio, exist_ok=True)

    descriptor, ruta_temporal = tempfile.mkstemp(
        prefix=".catalogo_codigos_", suffix=".tmp", dir=directorio
    )

    try:
        with os.fdopen(descriptor, "w", newline="", encoding="utf-8") as archivo:
            writer = csv.writer(archivo)
            writer.writerow(CAMPOS)
            for fila in filas:
                writer.writerow([
                    fila[0],
                    _texto(fila[1]),
                    _texto(fila[2]),
                    _texto(fila[3]),
                    _texto(fila[4]),
                    fila[5] if fila[5] is not None else "",
                    _texto(fila[6]),
                ])
            archivo.flush()
            os.fsync(archivo.fileno())

        # Reemplazo atómico dentro del mismo sistema de archivos.
        os.replace(ruta_temporal, ruta_destino)
    except Exception:
        try:
            os.remove(ruta_temporal)
        except OSError:
            pass
        raise


def _ruta_historica(directorio):
    base = datetime.now().strftime("catalogo_codigos_barras_%Y-%m-%d_%H%M%S")
    ruta = os.path.join(directorio, f"{base}.csv")
    contador = 1
    while os.path.exists(ruta):
        ruta = os.path.join(directorio, f"{base}_{contador}.csv")
        contador += 1
    return ruta


def generar_respaldo_desde_conexion(
    conn, directorio=None, crear_historico=True
):
    """Genera el respaldo usando una conexión ya abierta (misma transacción).

    Devuelve ``dict`` con las rutas y el número de filas respaldadas.
    """

    filas = _filas_desde_conexion(conn)
    destino = _directorio(directorio)
    principal = os.path.join(destino, NOMBRE_PRINCIPAL)

    _escribir_atomico(principal, filas)

    historico = None
    if crear_historico:
        historico = _ruta_historica(destino)
        _escribir_atomico(historico, filas)

    return {
        "ok": True,
        "filas": len(filas),
        "ruta_principal": principal,
        "ruta_historica": historico,
    }


def generar_respaldo(ruta_db=None, directorio=None, crear_historico=True):
    """Regenera el respaldo desde la base activa o desde ``ruta_db``."""

    conn = db.conectar(ruta_db)
    try:
        return generar_respaldo_desde_conexion(
            conn, directorio=directorio, crear_historico=crear_historico
        )
    finally:
        conn.close()


def actualizar_respaldo_si_posible(conn=None, ruta_db=None, directorio=None):
    """Actualiza el respaldo sin interrumpir la operación de negocio.

    Un fallo de disco o permisos nunca debe revertir un inventariado ya
    confirmado. El error queda disponible en ``error`` para diagnóstico.
    """

    try:
        if conn is not None:
            return generar_respaldo_desde_conexion(
                conn, directorio=directorio
            )
        return generar_respaldo(ruta_db, directorio=directorio)
    except Exception as error:
        return {
            "ok": False,
            "error": f"No se pudo actualizar el respaldo: {error}",
        }


def exportar_catalogo_csv(ruta_db=None, directorio=None):
    """Contenido CSV (UTF-8 con BOM) del catálogo actual, para descargar."""

    conn = db.conectar(ruta_db)
    try:
        filas = _filas_desde_conexion(conn)
    finally:
        conn.close()

    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(CAMPOS)
    for fila in filas:
        writer.writerow([
            fila[0],
            _texto(fila[1]),
            _texto(fila[2]),
            _texto(fila[3]),
            _texto(fila[4]),
            fila[5] if fila[5] is not None else "",
            _texto(fila[6]),
        ])

    return buffer.getvalue().encode("utf-8-sig")


def leer_respaldo(directorio=None):
    """Lee el respaldo estable y devuelve una lista de diccionarios."""

    ruta = ruta_principal(directorio)
    if not os.path.isfile(ruta):
        return []

    with open(ruta, "r", encoding="utf-8-sig", newline="") as archivo:
        return list(csv.DictReader(archivo))


def recuperar_relaciones(directorio=None):
    """Mapa ``codigoBarras -> {referencia, idConsolidado, ...}`` del respaldo.

    Se usa como apoyo de recuperación si la base se pierde. No reemplaza a
    SQLite durante la operación normal.
    """

    relaciones = {}
    for fila in leer_respaldo(directorio):
        codigo = _texto(fila.get("codigoBarras"))
        if not codigo:
            continue
        relaciones[codigo] = {
            "id": fila.get("id"),
            "codigoBarras": codigo,
            "referenciaProveedor": _texto(fila.get("referenciaProveedor")),
            "referenciaRepuesto": _texto(fila.get("referenciaRepuesto")),
            "descripcionEstandarizada": _texto(
                fila.get("descripcionEstandarizada")
            ),
            "idConsolidado": fila.get("idConsolidado") or None,
        }
    return relaciones


def _duplicados(valores):
    vistos = {}
    for valor in valores:
        valor = _texto(valor)
        if not valor:
            continue
        vistos[valor] = vistos.get(valor, 0) + 1
    return sorted(
        [{"codigo": codigo, "veces": veces}
         for codigo, veces in vistos.items() if veces > 1],
        key=lambda item: item["codigo"],
    )


def verificar_respaldo(ruta_db=None, directorio=None):
    """Compara SQLite contra el CSV de respaldo y detecta inconsistencias."""

    conn = db.conectar(ruta_db)
    try:
        filas_db = _filas_desde_conexion(conn)
    finally:
        conn.close()

    catalogo_db = {
        str(fila[0]): {
            "id": str(fila[0]),
            "codigoBarras": _texto(fila[1]),
            "referenciaProveedor": _texto(fila[2]),
            "referenciaRepuesto": _texto(fila[3]),
            "descripcionEstandarizada": _texto(fila[4]),
            "idConsolidado": "" if fila[5] is None else str(fila[5]),
            "fechaActualizacion": _texto(fila[6]),
        }
        for fila in filas_db
    }

    filas_csv = leer_respaldo(directorio)
    catalogo_csv = {
        _texto(fila.get("id")): {
            campo: _texto(fila.get(campo)) for campo in CAMPOS
        }
        for fila in filas_csv
    }
    catalogo_csv.pop("", None)

    errores = []
    advertencias = []

    duplicados_db = _duplicados(
        [fila["codigoBarras"] for fila in catalogo_db.values()]
    )
    duplicados_csv = _duplicados(
        [fila["codigoBarras"] for fila in catalogo_csv.values()]
    )

    for duplicado in duplicados_db:
        errores.append(
            "Código repetido en SQLite: "
            f"{duplicado['codigo']} ({duplicado['veces']} veces)."
        )
    for duplicado in duplicados_csv:
        errores.append(
            "Código repetido en el respaldo: "
            f"{duplicado['codigo']} ({duplicado['veces']} veces)."
        )

    sin_codigo = [
        item for item in catalogo_db.values()
        if not item["codigoBarras"]
        and (item["referenciaProveedor"] or item["referenciaRepuesto"])
    ]
    codigos_sin_referencia = [
        item for item in catalogo_db.values()
        if item["codigoBarras"]
        and not item["referenciaProveedor"]
        and not item["referenciaRepuesto"]
    ]

    for item in sin_codigo:
        advertencias.append(
            f"Referencia sin código de barras (id {item['id']})."
        )
    for item in codigos_sin_referencia:
        advertencias.append(
            f"Código sin referencia (id {item['id']}, "
            f"código {item['codigoBarras']})."
        )

    ids_db = set(catalogo_db)
    ids_csv = set(catalogo_csv)

    faltantes = sorted(ids_db - ids_csv)
    sobrantes = sorted(ids_csv - ids_db)

    for identificador in faltantes:
        errores.append(
            f"Artículo id {identificador} existe en SQLite y no en el respaldo."
        )
    for identificador in sobrantes:
        errores.append(
            f"Artículo id {identificador} existe en el respaldo y no en SQLite."
        )

    campos_diferentes = []
    for identificador in sorted(ids_db & ids_csv):
        diferencias = []
        for campo in (
            "codigoBarras",
            "referenciaProveedor",
            "referenciaRepuesto",
            "descripcionEstandarizada",
            "idConsolidado",
        ):
            if catalogo_db[identificador][campo] != catalogo_csv[
                identificador
            ][campo]:
                diferencias.append(campo)
        if diferencias:
            campos_diferentes.append(
                {"id": identificador, "campos": diferencias}
            )

    for item in campos_diferentes:
        errores.append(
            f"El id {item['id']} difiere en el respaldo: "
            f"{', '.join(item['campos'])}."
        )

    return {
        "ok": not errores,
        "ruta": ruta_principal(directorio),
        "articulos_sqlite": len(catalogo_db),
        "articulos_respaldo": len(catalogo_csv),
        "errores": errores,
        "advertencias": advertencias,
        "duplicados_sqlite": duplicados_db,
        "duplicados_respaldo": duplicados_csv,
        "referencias_sin_codigo": len(sin_codigo),
        "codigos_sin_referencia": len(codigos_sin_referencia),
        "faltantes_en_respaldo": faltantes,
        "sobrantes_en_respaldo": sobrantes,
        "campos_diferentes": campos_diferentes,
    }
