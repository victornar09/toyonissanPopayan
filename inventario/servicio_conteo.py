"""Lógica de negocio del conteo físico por estante.

Flujo: sesión -> lecturas -> finalización (comparación) -> aplicación.

Reglas clave:

* Escanear nunca modifica la cantidad oficial. Sólo ``aplicar_conteo``
  actualiza ``inventarioUnico`` y registra movimientos.
* Una sesión ``APLICADA`` no puede volver a aplicarse.
* Un código desconocido no crea artículos: se registra como incidencia.
* Las lecturas aceptan un ``token`` para evitar dobles envíos del lector.
"""

from __future__ import annotations

from datetime import datetime

import db
from utilidades import normalizar_clave


ESTADO_ABIERTA = "ABIERTA"
ESTADO_PAUSADA = "PAUSADA"
ESTADO_FINALIZADA = "FINALIZADA"
ESTADO_APLICADA = "APLICADA"

TIPOS_LECTURA = {
    "OK": "OK",
    "DESCONOCIDO": "DESCONOCIDO",
    "UBICACION_INCORRECTA": "UBICACION_INCORRECTA",
}


def _texto(valor):
    if valor is None:
        return ""
    return str(valor).strip()


def _clave_ubicacion(valor):
    return " ".join(_texto(valor).upper().split())


def _fila_dict(cursor, fila):
    if fila is None:
        return None
    return {col[0]: valor for col, valor in zip(cursor.description, fila)}


def _sesion(conn, id_sesion):
    cur = conn.cursor()
    cur.execute("SELECT * FROM sesionConteo WHERE idSesion = ?", (id_sesion,))
    return _fila_dict(cur, cur.fetchone())


def _etiqueta_ubicacion(conn, id_ubicacion=None, estante=None):
    if id_ubicacion:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT idUbicacion, descripcion, codigoUbicacion
            FROM Ubicacion
            WHERE idUbicacion = ?
            """,
            (id_ubicacion,),
        )
        fila = cur.fetchone()
        if not fila:
            return None
        ubicacion = _texto(fila[1]) or _texto(fila[2])
        return {
            "idUbicacion": fila[0],
            "ubicacion": ubicacion,
            "estante": _texto(estante) or _texto(fila[2]),
        }

    estante = _texto(estante)
    if not estante:
        return None
    return {"idUbicacion": None, "ubicacion": estante, "estante": estante}


def iniciar_sesion(
    ruta_db,
    *,
    nombre=None,
    responsable=None,
    id_ubicacion=None,
    estante=None,
    observaciones=None,
    usuario=None,
):
    """Crea una sesión de conteo para una ubicación o estante."""

    responsable = _texto(responsable)
    if not responsable:
        return {"ok": False, "error": "El responsable es obligatorio."}

    conn = db.conectar(ruta_db)
    try:
        etiqueta = _etiqueta_ubicacion(conn, id_ubicacion, estante)
        if not etiqueta:
            return {
                "ok": False,
                "error": (
                    "Seleccione una ubicación de la tabla o indique un "
                    "estante válido."
                ),
            }

        nombre = _texto(nombre) or (
            "Conteo " + datetime.now().strftime("%Y-%m-%d %H:%M")
            + " - " + etiqueta["ubicacion"]
        )

        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO sesionConteo (
                nombre, responsable, idUbicacion, ubicacion, estante,
                estado, observaciones, usuarioCreacion
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                nombre,
                responsable,
                etiqueta["idUbicacion"],
                etiqueta["ubicacion"],
                etiqueta["estante"],
                ESTADO_ABIERTA,
                _texto(observaciones) or None,
                _texto(usuario) or responsable,
            ),
        )
        conn.commit()
        id_sesion = cur.lastrowid

        return {
            "ok": True,
            "idSesion": id_sesion,
            "sesion": _sesion(conn, id_sesion),
        }
    except Exception as error:
        conn.rollback()
        return {"ok": False, "error": str(error)}
    finally:
        conn.close()


def _buscar_articulo_por_codigo(conn, codigo):
    codigo = _texto(codigo)
    if not codigo:
        return None

    candidatos = [codigo]
    if codigo.isdigit() and len(codigo) < 8:
        candidatos.append(codigo.zfill(8))

    cur = conn.cursor()
    for candidato in candidatos:
        cur.execute(
            """
            SELECT iu.id, iu.codigoBarras, iu.descripcion, iu.referencia,
                   iu.cantidad, iu.precioVenta, iu.precioVentaCifrado,
                   iu.proveedor, iu.grupo, iu.idUbicacion, iu.ubicacion,
                   iu.estante, iu.estadoEtiquetado, iu.estadoConteo,
                   iu.fechaUltimoConteo,
                   COALESCE(c.descripcionEstandarizada, iu.descripcion)
                       AS descripcionEstandarizada,
                   c.referenciaProveedor, c.referenciaRepuesto,
                   c.carro AS marcaVehiculo, c.modelo
            FROM inventarioUnico iu
            LEFT JOIN consolidado c
                ON c.id = (
                    SELECT MIN(c2.id) FROM consolidado c2
                    WHERE c2.idInventarioUnico = iu.id
                )
            WHERE iu.codigoBarras = ?
            """,
            (candidato,),
        )
        fila = _fila_dict(cur, cur.fetchone())
        if fila:
            return fila
    return None


def _datos_articulo(articulo):
    return {
        "idInventarioUnico": articulo["id"],
        "codigoBarras": articulo["codigoBarras"],
        "descripcion": (
            articulo.get("descripcionEstandarizada")
            or articulo["descripcion"]
        ),
        "descripcionInventario": articulo["descripcion"],
        "referencia": articulo["referencia"],
        "referenciaProveedor": articulo.get("referenciaProveedor"),
        "referenciaRepuesto": articulo.get("referenciaRepuesto"),
        "precio": articulo["precioVenta"],
        "precioCifrado": articulo["precioVentaCifrado"],
        "proveedor": articulo["proveedor"],
        "grupo": articulo["grupo"],
        "idUbicacion": articulo["idUbicacion"],
        "ubicacion": articulo["ubicacion"],
        "estante": articulo["estante"],
        "marcaVehiculo": articulo.get("marcaVehiculo"),
        "modelo": articulo.get("modelo"),
        "estadoEtiquetado": articulo["estadoEtiquetado"],
        "estadoConteo": articulo["estadoConteo"],
        "fechaUltimoConteo": articulo["fechaUltimoConteo"],
    }


def _ubicacion_incorrecta(sesion, articulo):
    """Devuelve (es_incorrecta, advertencia) comparando sesión y artículo."""

    estante_sesion = _clave_ubicacion(sesion.get("estante"))
    estante_item = _clave_ubicacion(articulo.get("estante"))
    id_sesion = sesion.get("idUbicacion")
    id_item = articulo.get("idUbicacion")

    if estante_sesion and estante_item and estante_sesion != estante_item:
        return True, (
            "El artículo pertenece al estante "
            f"{articulo.get('estante')} y la sesión es {sesion.get('estante')}."
        )

    if id_sesion and id_item and int(id_sesion) != int(id_item):
        return True, (
            "El artículo pertenece a otra ubicación "
            f"({articulo.get('ubicacion') or articulo.get('idUbicacion')})."
        )

    if estante_sesion and not estante_item and not id_item:
        return False, "El artículo no tiene ubicación registrada."

    return False, None


def _contadores(conn, id_sesion):
    cur = conn.cursor()
    cur.execute(
        """
        SELECT COUNT(*) FROM lecturaConteo
        WHERE idSesion = ? AND anulada = 0
        """,
        (id_sesion,),
    )
    total_lecturas = cur.fetchone()[0]

    cur.execute(
        """
        SELECT COUNT(*), COALESCE(SUM(cantidadContada), 0)
        FROM detalleConteo
        WHERE idSesion = ?
        """,
        (id_sesion,),
    )
    total_productos, total_unidades = cur.fetchone()

    return {
        "totalLecturas": total_lecturas,
        "totalProductos": total_productos,
        "totalUnidades": total_unidades or 0,
    }


def leer_codigo(
    ruta_db,
    id_sesion,
    codigo,
    *,
    token=None,
    usuario=None,
):
    """Registra una lectura del lector de códigos.

    ``token`` es opcional y sirve para que un doble envío del mismo escaneo
    no incremente dos veces la cantidad.
    """

    codigo = _texto(codigo)
    if not codigo:
        return {"ok": False, "error": "Código vacío."}

    conn = db.conectar(ruta_db)
    try:
        sesion = _sesion(conn, id_sesion)
        if not sesion:
            return {"ok": False, "error": "La sesión de conteo no existe."}
        if sesion["estado"] != ESTADO_ABIERTA:
            return {
                "ok": False,
                "error": (
                    "La sesión no está abierta para lectura "
                    f"({sesion['estado']})."
                ),
            }

        token = _texto(token) or None
        if token:
            cur = conn.cursor()
            cur.execute(
                "SELECT * FROM lecturaConteo WHERE token = ?", (token,)
            )
            existente = _fila_dict(cur, cur.fetchone())
            if existente:
                detalle = None
                if existente["idInventarioUnico"]:
                    cur.execute(
                        """
                        SELECT cantidadContada FROM detalleConteo
                        WHERE idSesion = ? AND idInventarioUnico = ?
                        """,
                        (id_sesion, existente["idInventarioUnico"]),
                    )
                    fila = cur.fetchone()
                    detalle = fila[0] if fila else 0
                return {
                    "ok": True,
                    "duplicado": True,
                    "encontrado": existente["idInventarioUnico"] is not None,
                    "tipo": existente["tipo"],
                    "codigoBarras": existente["codigoBarras"],
                    "cantidadContada": detalle or 0,
                    "mensaje": "Lectura ya registrada (doble envío evitado).",
                    **_contadores(conn, id_sesion),
                }

        articulo = _buscar_articulo_por_codigo(conn, codigo)

        if not articulo:
            cur = conn.cursor()
            cur.execute(
                """
                INSERT INTO lecturaConteo (
                    idSesion, idInventarioUnico, codigoBarras, token, tipo,
                    ubicacionSesion, usuario, observacion
                )
                VALUES (?, NULL, ?, ?, 'DESCONOCIDO', ?, ?, ?)
                """,
                (
                    id_sesion,
                    codigo,
                    token,
                    sesion.get("ubicacion"),
                    _texto(usuario),
                    "Código no reconocido; no se creó ningún artículo.",
                ),
            )
            conn.commit()
            return {
                "ok": True,
                "encontrado": False,
                "tipo": "DESCONOCIDO",
                "codigoBarras": codigo,
                "mensaje": "Código no reconocido. Regístrelo como incidencia o busque el artículo manualmente.",
                "idLectura": cur.lastrowid,
                "cantidadContada": 0,
                **_contadores(conn, id_sesion),
            }

        incorrecta, advertencia = _ubicacion_incorrecta(sesion, articulo)
        tipo = "UBICACION_INCORRECTA" if incorrecta else "OK"

        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO lecturaConteo (
                idSesion, idInventarioUnico, codigoBarras, token, tipo,
                ubicacionRegistrada, ubicacionSesion, usuario
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                id_sesion,
                articulo["id"],
                articulo["codigoBarras"] or codigo,
                token,
                tipo,
                articulo.get("ubicacion") or articulo.get("estante"),
                sesion.get("ubicacion"),
                _texto(usuario),
            ),
        )

        cur.execute(
            """
            SELECT cantidadRegistrada, cantidadContada
            FROM detalleConteo
            WHERE idSesion = ? AND idInventarioUnico = ?
            """,
            (id_sesion, articulo["id"]),
        )
        existente = cur.fetchone()

        if existente:
            cur.execute(
                """
                UPDATE detalleConteo
                SET cantidadContada = cantidadContada + 1,
                    ubicacionRegistrada = ?,
                    ubicacionSesion = ?
                WHERE idSesion = ? AND idInventarioUnico = ?
                """,
                (
                    articulo.get("ubicacion") or articulo.get("estante"),
                    sesion.get("ubicacion"),
                    id_sesion,
                    articulo["id"],
                ),
            )
        else:
            cur.execute(
                """
                INSERT INTO detalleConteo (
                    idSesion, idInventarioUnico, codigoBarras,
                    cantidadRegistrada, cantidadContada, diferencia,
                    estado, ubicacionRegistrada, ubicacionSesion
                )
                VALUES (?, ?, ?, ?, 1, 0, 'CONTADO', ?, ?)
                """,
                (
                    id_sesion,
                    articulo["id"],
                    articulo["codigoBarras"] or codigo,
                    articulo.get("cantidad") or 0,
                    articulo.get("ubicacion") or articulo.get("estante"),
                    sesion.get("ubicacion"),
                ),
            )

        conn.commit()

        cur.execute(
            """
            SELECT cantidadContada FROM detalleConteo
            WHERE idSesion = ? AND idInventarioUnico = ?
            """,
            (id_sesion, articulo["id"]),
        )
        cantidad_contada = cur.fetchone()[0]

        return {
            "ok": True,
            "encontrado": True,
            "tipo": tipo,
            "advertencia": advertencia,
            "articulo": _datos_articulo(articulo),
            "cantidadContada": cantidad_contada,
            "cantidadRegistrada": articulo.get("cantidad") or 0,
            "mensaje": advertencia or "Lectura registrada.",
            **_contadores(conn, id_sesion),
        }
    except Exception as error:
        conn.rollback()
        return {"ok": False, "error": str(error)}
    finally:
        conn.close()


def deshacer_ultima_lectura(ruta_db, id_sesion, usuario=None):
    """Anula la última lectura no anulada y descuenta su cantidad."""

    conn = db.conectar(ruta_db)
    try:
        sesion = _sesion(conn, id_sesion)
        if not sesion:
            return {"ok": False, "error": "La sesión de conteo no existe."}
        if sesion["estado"] not in (ESTADO_ABIERTA, ESTADO_PAUSADA):
            return {
                "ok": False,
                "error": "No se puede deshacer una lectura de una sesión cerrada.",
            }

        cur = conn.cursor()
        cur.execute(
            """
            SELECT * FROM lecturaConteo
            WHERE idSesion = ? AND anulada = 0
            ORDER BY idLectura DESC
            LIMIT 1
            """,
            (id_sesion,),
        )
        lectura = _fila_dict(cur, cur.fetchone())
        if not lectura:
            return {"ok": False, "error": "No hay lecturas para deshacer."}

        cur.execute(
            "UPDATE lecturaConteo SET anulada = 1 WHERE idLectura = ?",
            (lectura["idLectura"],),
        )

        cantidad = 0
        if lectura["idInventarioUnico"]:
            cur.execute(
                """
                UPDATE detalleConteo
                SET cantidadContada = MAX(cantidadContada - 1, 0)
                WHERE idSesion = ? AND idInventarioUnico = ?
                """,
                (id_sesion, lectura["idInventarioUnico"]),
            )
            cur.execute(
                """
                SELECT cantidadContada FROM detalleConteo
                WHERE idSesion = ? AND idInventarioUnico = ?
                """,
                (id_sesion, lectura["idInventarioUnico"]),
            )
            cantidad = cur.fetchone()[0]

        conn.commit()
        return {
            "ok": True,
            "idLectura": lectura["idLectura"],
            "codigoBarras": lectura["codigoBarras"],
            "tipo": lectura["tipo"],
            "cantidadContada": cantidad,
            "mensaje": "Última lectura deshecha.",
            **_contadores(conn, id_sesion),
        }
    except Exception as error:
        conn.rollback()
        return {"ok": False, "error": str(error)}
    finally:
        conn.close()


def pausar_sesion(ruta_db, id_sesion, usuario=None):
    return _cambiar_estado(
        ruta_db, id_sesion, ESTADO_PAUSADA, [ESTADO_ABIERTA], usuario
    )


def reanudar_sesion(ruta_db, id_sesion, usuario=None):
    return _cambiar_estado(
        ruta_db, id_sesion, ESTADO_ABIERTA, [ESTADO_PAUSADA], usuario
    )


def _cambiar_estado(ruta_db, id_sesion, nuevo, permitidos, usuario):
    conn = db.conectar(ruta_db)
    try:
        sesion = _sesion(conn, id_sesion)
        if not sesion:
            return {"ok": False, "error": "La sesión de conteo no existe."}
        if sesion["estado"] not in permitidos:
            return {
                "ok": False,
                "error": (
                    f"No se puede cambiar de {sesion['estado']} a {nuevo}."
                ),
            }
        conn.execute(
            "UPDATE sesionConteo SET estado = ? WHERE idSesion = ?",
            (nuevo, id_sesion),
        )
        conn.commit()
        return {"ok": True, "estado": nuevo, "sesion": _sesion(conn, id_sesion)}
    except Exception as error:
        conn.rollback()
        return {"ok": False, "error": str(error)}
    finally:
        conn.close()


def _detalle_sesion(conn, id_sesion):
    cur = conn.cursor()
    cur.execute(
        """
        SELECT d.*, iu.descripcion AS descripcionArticulo,
               COALESCE(c.descripcionEstandarizada, iu.descripcion)
                   AS descripcionEstandarizada,
               iu.referencia AS referenciaArticulo,
               c.referenciaProveedor, c.referenciaRepuesto,
               iu.precioVenta AS precioArticulo,
               iu.ubicacion AS ubicacionActual,
               iu.estante AS estanteActual
        FROM detalleConteo d
        LEFT JOIN inventarioUnico iu ON iu.id = d.idInventarioUnico
        LEFT JOIN consolidado c
            ON c.id = (
                SELECT MIN(c2.id) FROM consolidado c2
                WHERE c2.idInventarioUnico = d.idInventarioUnico
            )
        WHERE d.idSesion = ?
        ORDER BY d.idDetalle
        """,
        (id_sesion,),
    )
    return [_fila_dict(cur, fila) for fila in cur.fetchall()]


def _desconocidos(conn, id_sesion):
    cur = conn.cursor()
    cur.execute(
        """
        SELECT codigoBarras, COUNT(*) AS lecturas
        FROM lecturaConteo
        WHERE idSesion = ? AND tipo = 'DESCONOCIDO' AND anulada = 0
        GROUP BY codigoBarras
        ORDER BY codigoBarras
        """,
        (id_sesion,),
    )
    return [
        {"codigoBarras": fila[0], "lecturas": fila[1]}
        for fila in cur.fetchall()
    ]


def _esperados_en_ubicacion(conn, sesion):
    estante = _texto(sesion.get("estante"))
    id_ubicacion = sesion.get("idUbicacion")

    condiciones = []
    parametros = []

    if estante:
        condiciones.append(
            "UPPER(TRIM(COALESCE(estante, ''))) = ?"
        )
        parametros.append(_clave_ubicacion(estante))

    if id_ubicacion:
        condiciones.append("idUbicacion = ?")
        parametros.append(id_ubicacion)

    if not condiciones:
        return []

    cur = conn.cursor()
    cur.execute(
        f"""
        SELECT id, codigoBarras, cantidad, ubicacion, estante
        FROM inventarioUnico
        WHERE {" OR ".join(condiciones)}
        ORDER BY descripcion
        """,
        tuple(parametros),
    )
    return [_fila_dict(cur, fila) for fila in cur.fetchall()]


def _resumen(conn, id_sesion):
    detalles = _detalle_sesion(conn, id_sesion)
    resumen = {
        "productos": len(detalles),
        "correctos": 0,
        "faltantes": 0,
        "sobrantes": 0,
        "noContados": 0,
        "ubicacionesIncorrectas": 0,
        "cantidadRegistrada": 0,
        "cantidadContada": 0,
        "diferencia": 0,
    }

    for detalle in detalles:
        resumen["cantidadRegistrada"] += detalle["cantidadRegistrada"] or 0
        resumen["cantidadContada"] += detalle["cantidadContada"] or 0
        resumen["diferencia"] += detalle["diferencia"] or 0

        estado = detalle["estado"]
        if estado == "CORRECTO":
            resumen["correctos"] += 1
        elif estado == "FALTANTE":
            resumen["faltantes"] += 1
        elif estado == "SOBRANTE":
            resumen["sobrantes"] += 1
        elif estado == "NO_CONTADO":
            resumen["noContados"] += 1
        elif estado == "UBICACION_INCORRECTA":
            resumen["ubicacionesIncorrectas"] += 1

    contadores = _contadores(conn, id_sesion)
    resumen.update(contadores)
    return resumen


def finalizar_sesion(ruta_db, id_sesion, usuario=None):
    """Cierra la sesión y calcula la comparación sin tocar cantidades."""

    conn = db.conectar(ruta_db)
    try:
        sesion = _sesion(conn, id_sesion)
        if not sesion:
            return {"ok": False, "error": "La sesión de conteo no existe."}

        if sesion["estado"] == ESTADO_APLICADA:
            return {"ok": False, "error": "La sesión ya fue aplicada."}

        if sesion["estado"] == ESTADO_FINALIZADA:
            return {
                "ok": True,
                "idSesion": id_sesion,
                "sesion": sesion,
                "resumen": _resumen(conn, id_sesion),
                "detalle": _detalle_sesion(conn, id_sesion),
                "desconocidos": _desconocidos(conn, id_sesion),
            }

        if sesion["estado"] not in (ESTADO_ABIERTA, ESTADO_PAUSADA):
            return {
                "ok": False,
                "error": f"Estado de sesión no válido ({sesion['estado']}).",
            }

        cur = conn.cursor()

        # Artículos registrados en la ubicación que no fueron escaneados.
        for articulo in _esperados_en_ubicacion(conn, sesion):
            cur.execute(
                """
                SELECT cantidadContada FROM detalleConteo
                WHERE idSesion = ? AND idInventarioUnico = ?
                """,
                (id_sesion, articulo["id"]),
            )
            if cur.fetchone():
                continue
            cur.execute(
                """
                INSERT INTO detalleConteo (
                    idSesion, idInventarioUnico, codigoBarras,
                    cantidadRegistrada, cantidadContada, diferencia,
                    estado, ubicacionRegistrada, ubicacionSesion,
                    observacion
                )
                VALUES (?, ?, ?, ?, 0, ?, 'NO_CONTADO', ?, ?, ?)
                """,
                (
                    id_sesion,
                    articulo["id"],
                    articulo["codigoBarras"],
                    articulo["cantidad"] or 0,
                    -(articulo["cantidad"] or 0),
                    articulo.get("ubicacion") or articulo.get("estante"),
                    sesion.get("ubicacion"),
                    "Artículo registrado en la ubicación y no encontrado.",
                ),
            )

        # Recalcular cada detalle contra la cantidad oficial actual.
        for detalle in _detalle_sesion(conn, id_sesion):
            cur.execute(
                "SELECT cantidad, idUbicacion, estante, ubicacion "
                "FROM inventarioUnico WHERE id = ?",
                (detalle["idInventarioUnico"],),
            )
            articulo = _fila_dict(cur, cur.fetchone())
            if not articulo:
                continue

            if detalle["estado"] == "NO_CONTADO":
                continue

            incorrecta, _ = _ubicacion_incorrecta(
                sesion,
                {
                    "idUbicacion": articulo["idUbicacion"],
                    "estante": articulo["estante"],
                    "ubicacion": articulo["ubicacion"],
                },
            )
            contada = detalle["cantidadContada"] or 0
            registrada = articulo["cantidad"] or 0
            diferencia = contada - registrada

            if incorrecta:
                estado = "UBICACION_INCORRECTA"
            elif diferencia > 0:
                estado = "SOBRANTE"
            elif diferencia < 0:
                estado = "FALTANTE"
            else:
                estado = "CORRECTO"

            cur.execute(
                """
                UPDATE detalleConteo
                SET cantidadRegistrada = ?, diferencia = ?, estado = ?,
                    ubicacionRegistrada = ?, ubicacionSesion = ?
                WHERE idDetalle = ?
                """,
                (
                    registrada,
                    diferencia,
                    estado,
                    articulo["ubicacion"] or articulo["estante"],
                    sesion.get("ubicacion"),
                    detalle["idDetalle"],
                ),
            )

        conn.execute(
            """
            UPDATE sesionConteo
            SET estado = ?, fechaFin = CURRENT_TIMESTAMP
            WHERE idSesion = ?
            """,
            (ESTADO_FINALIZADA, id_sesion),
        )
        conn.commit()

        return {
            "ok": True,
            "idSesion": id_sesion,
            "sesion": _sesion(conn, id_sesion),
            "resumen": _resumen(conn, id_sesion),
            "detalle": _detalle_sesion(conn, id_sesion),
            "desconocidos": _desconocidos(conn, id_sesion),
        }
    except Exception as error:
        conn.rollback()
        return {"ok": False, "error": str(error)}
    finally:
        conn.close()


def obtener_sesion(ruta_db, id_sesion):
    conn = db.conectar(ruta_db)
    try:
        sesion = _sesion(conn, id_sesion)
        if not sesion:
            return {"ok": False, "error": "La sesión de conteo no existe."}

        return {
            "ok": True,
            "sesion": sesion,
            "resumen": _resumen(conn, id_sesion),
            "detalle": _detalle_sesion(conn, id_sesion),
            "desconocidos": _desconocidos(conn, id_sesion),
            "ultimaLectura": _ultima_lectura(conn, id_sesion),
            "lecturas": _ultimas_lecturas(conn, id_sesion),
        }
    finally:
        conn.close()


def _ultima_lectura(conn, id_sesion):
    cur = conn.cursor()
    cur.execute(
        """
        SELECT l.*, iu.descripcion, iu.referencia, iu.precioVenta,
               iu.ubicacion, iu.estante
        FROM lecturaConteo l
        LEFT JOIN inventarioUnico iu ON iu.id = l.idInventarioUnico
        WHERE l.idSesion = ? AND l.anulada = 0
        ORDER BY l.idLectura DESC
        LIMIT 1
        """,
        (id_sesion,),
    )
    return _fila_dict(cur, cur.fetchone())


def _ultimas_lecturas(conn, id_sesion, limite=50):
    cur = conn.cursor()
    cur.execute(
        """
        SELECT l.idLectura, l.codigoBarras, l.tipo, l.anulada,
               l.fecha, iu.descripcion, iu.referencia
        FROM lecturaConteo l
        LEFT JOIN inventarioUnico iu ON iu.id = l.idInventarioUnico
        WHERE l.idSesion = ?
        ORDER BY l.idLectura DESC
        LIMIT ?
        """,
        (id_sesion, limite),
    )
    return [_fila_dict(cur, fila) for fila in cur.fetchall()]


def aplicar_conteo(ruta_db, id_sesion, usuario=None):
    """Aplica las diferencias aprobadas y registra movimientos.

    La transición ``FINALIZADA -> APLICADA`` se hace con un UPDATE
    condicional, de modo que un segundo envío (incluso concurrente) no
    vuelve a ajustar cantidades.
    """

    conn = db.conectar(ruta_db)
    try:
        sesion = _sesion(conn, id_sesion)
        if not sesion:
            return {"ok": False, "error": "La sesión de conteo no existe."}

        if sesion["estado"] == ESTADO_APLICADA:
            return {"ok": False, "error": "La sesión ya fue aplicada."}

        if sesion["estado"] != ESTADO_FINALIZADA:
            return {
                "ok": False,
                "error": "Finalice y revise el conteo antes de aplicarlo.",
            }

        cur = conn.cursor()
        cur.execute(
            """
            UPDATE sesionConteo
            SET estado = 'APLICADA',
                fechaAplicacion = CURRENT_TIMESTAMP,
                usuarioAplicacion = ?
            WHERE idSesion = ? AND estado = 'FINALIZADA'
            """,
            (_texto(usuario), id_sesion),
        )
        if cur.rowcount != 1:
            conn.rollback()
            return {"ok": False, "error": "La sesión ya fue aplicada."}

        movimientos = 0
        ajustados = 0

        for detalle in _detalle_sesion(conn, id_sesion):
            if detalle["estado"] in ("NO_CONTADO", "UBICACION_INCORRECTA"):
                # No se ajusta: requiere revisión humana o pertenece a otra
                # ubicación. Se conserva el resultado para auditoría.
                continue

            cur.execute(
                "SELECT cantidad FROM inventarioUnico WHERE id = ?",
                (detalle["idInventarioUnico"],),
            )
            fila = cur.fetchone()
            if not fila:
                continue

            anterior = fila[0] or 0
            contada = detalle["cantidadContada"] or 0
            diferencia = contada - anterior

            estado_conteo = "CONTADO" if diferencia == 0 else "CON_DIFERENCIA"
            cur.execute(
                """
                UPDATE inventarioUnico
                SET cantidad = ?,
                    cantidadFisica = ?,
                    fechaUltimoConteo = CURRENT_TIMESTAMP,
                    estadoConteo = ?
                WHERE id = ?
                """,
                (
                    contada,
                    contada,
                    estado_conteo,
                    detalle["idInventarioUnico"],
                ),
            )

            if diferencia != 0:
                cur.execute(
                    """
                    INSERT INTO movimientoInventario (
                        idInventarioUnico, tipo, cantidadAnterior,
                        cantidadMovimiento, cantidadNueva, origen, idOrigen,
                        usuario, observacion
                    )
                    VALUES (?, 'AJUSTE_CONTEO', ?, ?, ?, 'SESION_CONTEO', ?,
                            ?, ?)
                    """,
                    (
                        detalle["idInventarioUnico"],
                        anterior,
                        diferencia,
                        contada,
                        id_sesion,
                        _texto(usuario),
                        (
                            f"Sesión {sesion.get('nombre') or id_sesion}; "
                            f"estado {detalle['estado']}."
                        ),
                    ),
                )
                movimientos += 1
                ajustados += 1

        conn.commit()
        return {
            "ok": True,
            "idSesion": id_sesion,
            "ajustados": ajustados,
            "movimientos": movimientos,
            "resumen": _resumen(conn, id_sesion),
        }
    except Exception as error:
        conn.rollback()
        return {"ok": False, "error": str(error)}
    finally:
        conn.close()


def buscar_articulos(ruta_db, texto, limite=20):
    """Búsqueda manual para resolver códigos desconocidos."""

    texto = _texto(texto)
    if not texto:
        return {"ok": True, "articulos": []}

    patron = f"%{texto.upper()}%"
    conn = db.conectar(ruta_db)
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT iu.id, iu.codigoBarras, iu.descripcion, iu.referencia,
                   iu.cantidad, iu.ubicacion, iu.estante,
                   COALESCE(c.descripcionEstandarizada, iu.descripcion)
                       AS descripcionEstandarizada
            FROM inventarioUnico iu
            LEFT JOIN consolidado c
                ON c.id = (
                    SELECT MIN(c2.id) FROM consolidado c2
                    WHERE c2.idInventarioUnico = iu.id
                )
            WHERE UPPER(TRIM(COALESCE(iu.codigoBarras, ''))) LIKE ?
               OR UPPER(TRIM(COALESCE(iu.descripcion, ''))) LIKE ?
               OR UPPER(TRIM(COALESCE(iu.referencia, ''))) LIKE ?
               OR UPPER(TRIM(COALESCE(c.referenciaProveedor, ''))) LIKE ?
               OR UPPER(TRIM(COALESCE(c.referenciaRepuesto, ''))) LIKE ?
            ORDER BY iu.descripcion
            LIMIT ?
            """,
            (patron, patron, patron, patron, patron, limite),
        )
        filas = [
            {
                "id": fila[0],
                "codigoBarras": fila[1],
                "descripcion": fila[7] or fila[2],
                "referencia": fila[3],
                "cantidad": fila[4],
                "ubicacion": fila[5],
                "estante": fila[6],
            }
            for fila in cur.fetchall()
        ]
        return {"ok": True, "articulos": filas}
    finally:
        conn.close()


def listar_ubicaciones(ruta_db):
    conn = db.conectar(ruta_db)
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT idUbicacion, descripcion, piso, pasillo, numero, tipo,
                   codigoUbicacion
            FROM Ubicacion
            ORDER BY descripcion, codigoUbicacion
            """
        )
        return [
            {
                "idUbicacion": fila[0],
                "descripcion": fila[1],
                "piso": fila[2],
                "pasillo": fila[3],
                "numero": fila[4],
                "tipo": fila[5],
                "codigoUbicacion": fila[6],
            }
            for fila in cur.fetchall()
        ]
    finally:
        conn.close()


def listar_sesiones(ruta_db, limite=20):
    conn = db.conectar(ruta_db)
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT * FROM sesionConteo
            ORDER BY idSesion DESC
            LIMIT ?
            """,
            (limite,),
        )
        return [_fila_dict(cur, fila) for fila in cur.fetchall()]
    finally:
        conn.close()
