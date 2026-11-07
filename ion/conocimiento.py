"""Conocimiento administrable de ION.

El conocimiento oficial combina el lexicón incorporado con los sinónimos
aprobados en la base. Las sugerencias de los vendedores nacen en estado
``PENDIENTE`` y nunca se usan para buscar hasta que un administrador las
apruebe.
"""

from __future__ import annotations

from datetime import datetime

from .normalizacion import normalizar


ESTADOS = ("PENDIENTE", "APROBADO", "RECHAZADO")
TIPOS = (
    "SINONIMO",
    "NOMBRE_POPULAR",
    "ABREVIATURA",
    "ERROR_FRECUENTE",
    "EQUIVALENTE",
    "COMPATIBILIDAD",
    "MEDIDA",
    "SISTEMA",
    "POSICION",
)


def _texto(valor):
    return str(valor or "").strip()


def cargar_sinonimos_aprobados(conn):
    """Mapa ``termino normalizado -> reemplazo normalizado`` aprobado."""

    try:
        filas = conn.execute(
            """
            SELECT terminoNormalizado, reemplazoNormalizado
            FROM ionSinonimo
            WHERE estado = 'APROBADO'
            """
        ).fetchall()
    except Exception:
        return {}

    mapa = {}
    for termino, reemplazo in filas:
        termino = normalizar(termino)
        reemplazo = normalizar(reemplazo)
        if termino and reemplazo:
            mapa[termino] = reemplazo
    return mapa


def sugerir(conn, termino, reemplazo, *, usuario=None, origen="VENDEDOR",
            id_consulta=None, tipo="SINONIMO"):
    """Registra una sugerencia PENDIENTE. Devuelve su estado final.

    Si el término ya existe aprobado no se duplica. Si ya existe pendiente se
    reutiliza. En ningún caso una sugerencia se vuelve oficial por sí sola.
    """

    termino = _texto(termino)
    reemplazo = _texto(reemplazo)

    if not termino or not reemplazo:
        return {"ok": False, "mensaje": "Debe indicar el término y su reemplazo."}

    termino_norm = normalizar(termino)
    reemplazo_norm = normalizar(reemplazo)

    if not termino_norm or not reemplazo_norm:
        return {"ok": False, "mensaje": "Los términos no son válidos."}

    if termino_norm == reemplazo_norm:
        return {
            "ok": False,
            "mensaje": "El término y su reemplazo no pueden ser iguales.",
        }

    existente = conn.execute(
        """
        SELECT idSinonimo, estado
        FROM ionSinonimo
        WHERE terminoNormalizado = ? AND reemplazoNormalizado = ?
        """,
        (termino_norm, reemplazo_norm),
    ).fetchone()

    if existente:
        return {
            "ok": True,
            "idSinonimo": existente[0],
            "estado": existente[1],
            "mensaje": (
                "Ese término ya está registrado como %s." % existente[1].lower()
            ),
        }

    cursor = conn.execute(
        """
        INSERT INTO ionSinonimo (
            termino, terminoNormalizado, reemplazo, reemplazoNormalizado,
            tipo, estado, origen, idConsulta, usuarioCreacion
        )
        VALUES (?, ?, ?, ?, ?, 'PENDIENTE', ?, ?, ?)
        """,
        (
            termino,
            termino_norm,
            reemplazo,
            reemplazo_norm,
            tipo if tipo in TIPOS else "SINONIMO",
            origen,
            id_consulta,
            usuario,
        ),
    )

    return {
        "ok": True,
        "idSinonimo": cursor.lastrowid,
        "estado": "PENDIENTE",
        "mensaje": (
            "Sugerencia registrada. Queda pendiente de aprobación y todavía "
            "no se usa en las búsquedas."
        ),
    }


def listar(conn, estado=None):
    sql = """
        SELECT idSinonimo, termino, reemplazo, tipo, estado, origen,
               usuarioCreacion, fechaCreacion, usuarioRevision, fechaRevision
        FROM ionSinonimo
    """
    parametros = []
    if estado:
        sql += " WHERE estado = ?"
        parametros.append(estado)
    sql += " ORDER BY estado = 'PENDIENTE' DESC, fechaCreacion DESC, idSinonimo DESC"

    return conn.execute(sql, parametros).fetchall()


def cambiar_estado(conn, id_sinonimo, estado, usuario=None):
    estado = _texto(estado).upper()
    if estado not in ("APROBADO", "RECHAZADO"):
        return {"ok": False, "mensaje": "Estado no válido."}

    fila = conn.execute(
        "SELECT idSinonimo FROM ionSinonimo WHERE idSinonimo = ?",
        (id_sinonimo,),
    ).fetchone()
    if not fila:
        return {"ok": False, "mensaje": "La sugerencia no existe."}

    conn.execute(
        """
        UPDATE ionSinonimo
        SET estado = ?, usuarioRevision = ?, fechaRevision = ?
        WHERE idSinonimo = ?
        """,
        (estado, usuario, datetime.now().isoformat(timespec="seconds"),
         id_sinonimo),
    )

    return {
        "ok": True,
        "mensaje": (
            "Término aprobado. Ya participa en las búsquedas."
            if estado == "APROBADO"
            else "Término rechazado. No se usará en las búsquedas."
        ),
    }


def listar_compatibilidades(conn, limite=100):
    return conn.execute(
        """
        SELECT idCompatibilidad, idInventarioUnico, idConsolidado,
               marca, modelo, anioDesde, anioHasta, motor, combustible,
               transmision, traccion, cabina, medida, posicion, lado,
               observacion, estado, fechaCreacion
        FROM ionCompatibilidad
        ORDER BY fechaCreacion DESC, idCompatibilidad DESC
        LIMIT ?
        """,
        (limite,),
    ).fetchall()


def agregar_compatibilidad(conn, datos, usuario=None):
    """Agrega una compatibilidad aprobada explícitamente por el usuario."""

    marca = _texto(datos.get("marca"))
    modelo = _texto(datos.get("modelo"))
    if not marca and not modelo:
        return {
            "ok": False,
            "mensaje": "Indique al menos la marca o el modelo del vehículo.",
        }

    cursor = conn.execute(
        """
        INSERT INTO ionCompatibilidad (
            idInventarioUnico, idConsolidado, marca, modelo, anioDesde,
            anioHasta, motor, combustible, transmision, traccion, cabina,
            medida, posicion, lado, observacion, estado, usuarioCreacion
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'APROBADO', ?)
        """,
        (
            datos.get("idInventarioUnico") or None,
            datos.get("idConsolidado") or None,
            marca or None,
            modelo or None,
            datos.get("anioDesde") or None,
            datos.get("anioHasta") or None,
            _texto(datos.get("motor")) or None,
            _texto(datos.get("combustible")) or None,
            _texto(datos.get("transmision")) or None,
            _texto(datos.get("traccion")) or None,
            _texto(datos.get("cabina")) or None,
            _texto(datos.get("medida")) or None,
            _texto(datos.get("posicion")) or None,
            _texto(datos.get("lado")) or None,
            _texto(datos.get("observacion")) or None,
            usuario,
        ),
    )

    return {
        "ok": True,
        "idCompatibilidad": cursor.lastrowid,
        "mensaje": "Compatibilidad registrada.",
    }
