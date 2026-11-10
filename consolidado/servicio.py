"""Lógica de negocio del módulo Consolidado.

Reglas principales:

* Consolidado es el catálogo maestro; no crea artículos de Inventario.
* La clave real es ``proveedor + referenciaProveedor`` normalizados.
* Varias coincidencias => ``BLOQUEADO``, nunca se elige la primera.
* La importación es idempotente: no duplica registros ni asociaciones.
* Inventariar asigna identidad y código, sin modificar cantidades.
"""

from __future__ import annotations

import os
import re
import unicodedata
from datetime import date

import pandas as pd

import db
import migraciones
from utilidades import normalizar_clave, cifrar_precio, a_entero
from facturas.validacion_consolidado import resolver_proveedor
from consolidado.servicio_inventario import obtener_o_crear_articulo
from inventario import respaldo


ESTADO_PENDIENTE = "PENDIENTE_REVISION"
ESTADO_LISTO = "LISTO_INVENTARIAR"
ESTADO_INVENTARIADO = "INVENTARIADO"
ESTADO_BLOQUEADO = "BLOQUEADO"

ESTADOS_VALIDOS = (
    ESTADO_PENDIENTE,
    ESTADO_LISTO,
    ESTADO_INVENTARIADO,
    ESTADO_BLOQUEADO,
)

CAMPOS_ESTANDARIZADOS = (
    "repuesto",
    "lado",
    "ladoAbreviado",
    "modelo",
    "otros",
    "medida",
    "descripcionEstandarizada",
)

CAMPOS_EDITABLES = (
    "descripcion",
    "referenciaProveedor",
    "referenciaRepuesto",
    "equivalente",
    "marca",
    "carro",
    "modelo",
    "lado",
    "medida",
    "grupo",
    "tipo",
    "tipoMotor",
    "otros",
)


def _texto(valor):
    if valor is None:
        return None
    if isinstance(valor, float) and pd.isna(valor):
        return None
    valor = str(valor).strip()
    return valor or None


def _mayus(valor):
    valor = _texto(valor)
    return valor.upper() if valor else None


def _numero(valor):
    if valor is None:
        return None
    try:
        if pd.isna(valor):
            return None
    except (TypeError, ValueError):
        pass
    try:
        return float(valor)
    except (TypeError, ValueError):
        return None


def _clave(abrev_proveedor, referencia_proveedor):
    abrev = _mayus(abrev_proveedor)
    ref = _mayus(referencia_proveedor)
    if not abrev or not ref:
        return None
    return abrev, ref


def _referencia_valida(referencia):
    ref = _mayus(referencia)
    return bool(ref) and ref not in ("SIN_REFERENCIA", "SIN REFERENCIA", "NONE")


def _ruta_estandarizacion_por_defecto():
    base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, "bin", "xlsx", "estandarizacion.xlsx")


def _fila_a_dict(cursor, fila):
    if fila is None:
        return None
    return {col[0]: valor for col, valor in zip(cursor.description, fila)}


def _buscar_coincidencias(conn, abrev, referencia):
    cur = conn.cursor()
    cur.execute(
        """
        SELECT *
        FROM consolidado
        WHERE UPPER(TRIM(abrevProveedor)) = ?
          AND UPPER(TRIM(referenciaProveedor)) = ?
        """,
        (_mayus(abrev), _mayus(referencia)),
    )
    return [_fila_a_dict(cur, fila) for fila in cur.fetchall()]


def _contar_coincidencias(conn, abrev, referencia, excluir_id=None):
    cur = conn.cursor()
    sql = """
        SELECT COUNT(*)
        FROM consolidado
        WHERE UPPER(TRIM(abrevProveedor)) = ?
          AND UPPER(TRIM(referenciaProveedor)) = ?
    """
    params = [_mayus(abrev), _mayus(referencia)]
    if excluir_id is not None:
        sql += " AND id <> ?"
        params.append(excluir_id)
    cur.execute(sql, params)
    return cur.fetchone()[0]


def _advertencias_secundarias(conn, referencia, descripcion):
    """Coincidencias secundarias: sólo advierten, no fusionan."""

    referencia = _mayus(referencia)
    descripcion = _mayus(descripcion)
    advertencias = []

    if referencia:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT id, abrevProveedor, referenciaProveedor,
                   referenciaRepuesto, equivalente, descripcionEstandarizada
            FROM consolidado
            WHERE (UPPER(TRIM(COALESCE(referenciaRepuesto, ''))) = ?
                   AND TRIM(COALESCE(referenciaRepuesto, '')) <> '')
               OR (UPPER(TRIM(COALESCE(equivalente, ''))) = ?
                   AND TRIM(COALESCE(equivalente, '')) <> '')
            LIMIT 5
            """,
            (referencia, referencia),
        )
        for fila in cur.fetchall():
            advertencias.append(
                "Posible equivalencia secundaria con Consolidado id "
                f"{fila[0]} ({fila[1]}/{fila[2]})."
            )

    if descripcion:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT id, abrevProveedor, referenciaProveedor
            FROM consolidado
            WHERE UPPER(TRIM(COALESCE(descripcion, ''))) = ?
               OR UPPER(TRIM(COALESCE(descripcionEstandarizada, ''))) = ?
            LIMIT 5
            """,
            (descripcion, descripcion),
        )
        for fila in cur.fetchall():
            advertencias.append(
                "Descripción coincidente con Consolidado id "
                f"{fila[0]} ({fila[1]}/{fila[2]}); verifique antes de fusionar."
            )

    return advertencias


def _fallback_estandarizar(df, campos_manuales=None):
    """Estandarización mínima cuando el archivo de reglas no está disponible."""

    campos_manuales = set(campos_manuales or ())

    def _simple(valor):
        texto = _texto(valor)
        if not texto:
            return ""
        texto = unicodedata.normalize("NFD", texto.upper())
        texto = "".join(c for c in texto if unicodedata.category(c) != "Mn")
        texto = re.sub(r"[^A-Z0-9]+", " ", texto)
        return re.sub(r"\s+", " ", texto).strip()

    if "descripcionEstandarizada" in campos_manuales:
        df["descripcionEstandarizada"] = df.apply(
            lambda fila: (
                _texto(fila.get("descripcionEstandarizada"))
                or _simple(fila.get("descripcion"))
            ),
            axis=1,
        )
    else:
        df["descripcionEstandarizada"] = df["descripcion"].apply(_simple)

    if "repuesto" not in df.columns:
        df["repuesto"] = ""
    df["repuesto"] = df.apply(
        lambda fila: (
            _texto(fila.get("repuesto"))
            or _simple(fila.get("descripcionEstandarizada"))
        ),
        axis=1,
    )

    for columna in ("lado", "ladoAbreviado", "modelo", "otros", "medida", "grupo"):
        if columna not in df.columns:
            df[columna] = ""
        df[columna] = df[columna].apply(
            lambda v: _texto(v) if _texto(v) else ""
        )
    return df


def _estandarizar(filas, ruta_estandarizacion=None, campos_manuales=None):
    """Aplica ``procesamiento.consolidado`` a una lista de diccionarios."""

    if not filas:
        return []

    df = pd.DataFrame(filas)

    columnas = [
        "abrevProveedor", "referenciaProveedor", "referenciaRepuesto",
        "equivalente", "descripcion", "marca", "carro", "tipo",
        "tipoMotor", "precioListado", "proveedor", "idProveedor",
    ]
    for columna in columnas:
        if columna not in df.columns:
            df[columna] = ""

    ruta = ruta_estandarizacion or _ruta_estandarizacion_por_defecto()

    if os.path.isfile(ruta):
        try:
            from procesamiento.consolidado import procesar_dataframe_pendientes

            df = procesar_dataframe_pendientes(
                df, ruta, campos_manuales=campos_manuales
            )
        except Exception:
            df = _fallback_estandarizar(df, campos_manuales)
    else:
        df = _fallback_estandarizar(df, campos_manuales)

    if "descripcionEstandarizada" not in df.columns:
        df = _fallback_estandarizar(df, campos_manuales)

    return df.to_dict("records")


def _estado_para_datos(datos, duplicado=False):
    if duplicado:
        return ESTADO_BLOQUEADO
    desc = _texto(datos.get("descripcionEstandarizada"))
    ref = datos.get("referenciaProveedor")
    abrev = datos.get("abrevProveedor")
    if desc and len(desc) >= 3 and _referencia_valida(ref) and _texto(abrev):
        return ESTADO_LISTO
    return ESTADO_PENDIENTE


def _datos_crudos_desde_linea(factura, linea, proveedor_info):
    return {
        "idProveedor": proveedor_info.get("idProveedor") if proveedor_info else None,
        "abrevProveedor": proveedor_info.get("siglas") if proveedor_info else None,
        "referenciaProveedor": _texto(linea.get("referencia")),
        "referenciaRepuesto": "",
        "equivalente": "",
        "descripcion": _texto(linea.get("descripcion_item")),
        "marca": "",
        "carro": "",
        "tipo": "",
        "tipoMotor": "",
        "precioListado": linea.get("valor_unitario"),
        "proveedor": factura.get("proveedor") or (
            proveedor_info.get("nombreProveedor") if proveedor_info else None
        ),
    }


def _obtener_facturas(conn, fecha_inicial, fecha_final, proveedor=None):
    cur = conn.cursor()
    cur.execute(
        """
        SELECT id_factura, proveedor, fecha
        FROM facturas
        WHERE SUBSTR(COALESCE(fecha, ''), 1, 10) BETWEEN ? AND ?
        ORDER BY fecha, id_factura
        """,
        (fecha_inicial, fecha_final),
    )
    facturas = []
    patron = _mayus(proveedor)
    cache_prov = {}

    for id_factura, nombre_proveedor, fecha in cur.fetchall():
        clave_prov = _texto(nombre_proveedor) or ""
        if clave_prov not in cache_prov:
            try:
                cache_prov[clave_prov] = resolver_proveedor(conn, nombre_proveedor)
            except Exception:
                cache_prov[clave_prov] = None
        info = cache_prov[clave_prov]

        if patron:
            valores = {
                _mayus(proveedor),
                _mayus(nombre_proveedor),
                _mayus(info.get("siglas")) if info else None,
                _mayus(info.get("nombreProveedor")) if info else None,
                _mayus(info.get("nombreProveedorSecundario")) if info else None,
            }
            if patron not in valores:
                continue

        facturas.append(
            {
                "id_factura": id_factura,
                "proveedor": nombre_proveedor,
                "fecha": fecha,
                "proveedor_info": info,
            }
        )

    return facturas


def _lineas_por_facturas(conn, ids_factura):
    resultado = {}
    cur = conn.cursor()
    ids = list(ids_factura)

    for inicio in range(0, len(ids), 900):
        lote = ids[inicio:inicio + 900]
        if not lote:
            continue
        marcadores = ",".join("?" * len(lote))
        cur.execute(
            f"""
            SELECT id, id_factura, referencia, descripcion_item,
                   valor_unitario, id_consolidado,
                   referenciaOriginal, referenciaCorregida
            FROM inventarioFacturas
            WHERE id_factura IN ({marcadores})
            """,
            tuple(lote),
        )
        for fila in cur.fetchall():
            referencia = (
                _texto(fila[7]) or _texto(fila[2])
            )
            resultado.setdefault(fila[1], []).append(
                {
                    "id": fila[0],
                    "id_factura": fila[1],
                    "referencia": referencia,
                    "descripcion_item": fila[3],
                    "valor_unitario": fila[4],
                    "id_consolidado": fila[5],
                    "referenciaOriginal": _texto(fila[6]) or _texto(fila[2]),
                    "referenciaCorregida": _texto(fila[7]),
                }
            )

    return resultado


def importar_desde_facturas(
    ruta_db,
    fecha_inicial,
    fecha_final,
    proveedor=None,
    solo_no_procesadas=True,
    confirmar=False,
    usuario=None,
    ruta_estandarizacion=None,
):
    """Vista previa o importación de líneas de factura hacia Consolidado."""

    if not fecha_inicial or not fecha_final:
        return {"ok": False, "error": "Debe indicar fecha inicial y final."}
    if fecha_inicial > fecha_final:
        return {
            "ok": False,
            "error": "La fecha inicial no puede ser posterior a la fecha final.",
        }

    resultado = {
        "ok": True,
        "confirmado": bool(confirmar),
        "fecha_inicial": fecha_inicial,
        "fecha_final": fecha_final,
        "proveedor": _texto(proveedor),
        "facturas_encontradas": 0,
        "lineas_encontradas": 0,
        "nuevos": 0,
        "existentes": 0,
        "sin_referencia": 0,
        "ambiguas": 0,
        "incompletas": 0,
        "bloqueadas": 0,
        "omitidas": 0,
        "creados": 0,
        "actualizados": 0,
        "vinculados": 0,
        "errores": 0,
        "advertencias": [],
        "detalle": [],
    }

    conn = db.conectar(ruta_db)
    try:
        migraciones.migrar(conn)

        facturas = _obtener_facturas(
            conn, fecha_inicial, fecha_final, proveedor
        )
        ids_factura = [f["id_factura"] for f in facturas]
        lineas_por_factura = _lineas_por_facturas(conn, ids_factura)

        resultado["facturas_encontradas"] = len(facturas)

        acciones_nuevas = []
        acciones_existentes = []
        advertencias_detalle = []

        for factura in facturas:
            lineas = lineas_por_factura.get(factura["id_factura"], [])
            resultado["lineas_encontradas"] += len(lineas)

            for linea in lineas:
                referencia = _texto(linea.get("referencia"))
                descripcion = _texto(linea.get("descripcion_item"))
                ya_procesada = linea.get("id_consolidado") is not None

                if solo_no_procesadas and ya_procesada:
                    resultado["omitidas"] += 1
                    continue

                if not _referencia_valida(referencia):
                    resultado["sin_referencia"] += 1
                    resultado["bloqueadas"] += 1
                    resultado["detalle"].append(
                        {
                            "linea": linea["id"],
                            "id_factura": factura["id_factura"],
                            "referencia": referencia,
                            "motivo": "Línea sin referencia válida.",
                        }
                    )
                    continue

                if not factura.get("proveedor_info") or not _texto(
                    factura["proveedor_info"].get("siglas")
                ):
                    resultado["incompletas"] += 1
                    resultado["bloqueadas"] += 1
                    resultado["detalle"].append(
                        {
                            "linea": linea["id"],
                            "id_factura": factura["id_factura"],
                            "referencia": referencia,
                            "motivo": "Proveedor sin siglas en el maestro.",
                        }
                    )
                    continue

                if not descripcion:
                    resultado["incompletas"] += 1
                    resultado["bloqueadas"] += 1
                    resultado["detalle"].append(
                        {
                            "linea": linea["id"],
                            "id_factura": factura["id_factura"],
                            "referencia": referencia,
                            "motivo": "Línea sin descripción.",
                        }
                    )
                    continue

                coincidencias = _buscar_coincidencias(
                    conn, factura["proveedor_info"]["siglas"], referencia
                )

                if len(coincidencias) > 1:
                    resultado["ambiguas"] += 1
                    resultado["bloqueadas"] += 1
                    resultado["detalle"].append(
                        {
                            "linea": linea["id"],
                            "id_factura": factura["id_factura"],
                            "referencia": referencia,
                            "motivo": "Coincidencia ambigua en Consolidado.",
                        }
                    )
                    continue

                advertencias = _advertencias_secundarias(
                    conn, referencia, descripcion
                )

                if coincidencias:
                    existente = coincidencias[0]
                    resultado["existentes"] += 1

                    if ya_procesada and linea.get("id_consolidado") == existente["id"]:
                        resultado["omitidas"] += 1
                        continue

                    acciones_existentes.append(
                        {
                            "linea": linea,
                            "factura": factura,
                            "consolidado_id": existente["id"],
                            "advertencias": advertencias,
                        }
                    )
                else:
                    resultado["nuevos"] += 1
                    acciones_nuevas.append(
                        {
                            "linea": linea,
                            "factura": factura,
                            "datos": _datos_crudos_desde_linea(
                                factura, linea, factura["proveedor_info"]
                            ),
                            "advertencias": advertencias,
                        }
                    )

                for advertencia in advertencias:
                    if len(advertencias_detalle) < 200:
                        advertencias_detalle.append(advertencia)

        resultado["advertencias"] = advertencias_detalle

        if confirmar:
            _confirmar_importacion(
                conn,
                resultado,
                acciones_nuevas,
                acciones_existentes,
                usuario,
                ruta_estandarizacion,
            )
            conn.commit()

    except Exception as error:
        conn.rollback()
        resultado["ok"] = False
        resultado["error"] = str(error)
        resultado["errores"] += 1
    finally:
        conn.close()

    return resultado


def _confirmar_importacion(
    conn,
    resultado,
    acciones_nuevas,
    acciones_existentes,
    usuario,
    ruta_estandarizacion,
):
    estandarizados = _estandarizar(
        [accion["datos"] for accion in acciones_nuevas],
        ruta_estandarizacion,
    )

    ids_por_clave = {}

    for accion, datos_est in zip(acciones_nuevas, estandarizados):
        linea = accion["linea"]
        try:
            clave = _clave(
                datos_est.get("abrevProveedor"),
                datos_est.get("referenciaProveedor"),
            )
            if not clave:
                resultado["errores"] += 1
                continue

            if clave in ids_por_clave:
                consolidado_id = ids_por_clave[clave]
            else:
                duplicado = _contar_coincidencias(conn, clave[0], clave[1]) > 0
                estado = _estado_para_datos(datos_est, duplicado=duplicado)

                cursor = conn.cursor()
                cursor.execute(
                    """
                    INSERT INTO consolidado (
                        idProveedor, abrevProveedor, referenciaProveedor,
                        referenciaRepuesto, equivalente, descripcion, marca,
                        carro, tipo, tipoMotor, precioListado, proveedor,
                        lado, ladoAbreviado, repuesto, modelo, otros, medida,
                        descripcionEstandarizada, grupo, estadoConsolidado,
                        fechaCreacion, fechaActualizacion
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                            ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                    """,
                    (
                        datos_est.get("idProveedor"),
                        clave[0],
                        clave[1],
                        _texto(datos_est.get("referenciaRepuesto")),
                        _texto(datos_est.get("equivalente")),
                        _texto(datos_est.get("descripcion")),
                        _texto(datos_est.get("marca")),
                        _texto(datos_est.get("carro")),
                        _texto(datos_est.get("tipo")),
                        _texto(datos_est.get("tipoMotor")),
                        _numero(datos_est.get("precioListado")),
                        _texto(datos_est.get("proveedor")),
                        _texto(datos_est.get("lado")),
                        _texto(datos_est.get("ladoAbreviado")),
                        _texto(datos_est.get("repuesto")),
                        _texto(datos_est.get("modelo")),
                        _texto(datos_est.get("otros")),
                        _texto(datos_est.get("medida")),
                        _texto(datos_est.get("descripcionEstandarizada")),
                        _texto(datos_est.get("grupo")),
                        estado,
                    ),
                )
                consolidado_id = cursor.lastrowid
                ids_por_clave[clave] = consolidado_id
                resultado["creados"] += 1

            _vincular_linea(conn, linea["id"], consolidado_id)
            resultado["vinculados"] += 1
        except Exception:
            resultado["errores"] += 1

    for accion in acciones_existentes:
        linea = accion["linea"]
        try:
            consolidado_id = accion["consolidado_id"]
            datos = _datos_crudos_desde_linea(
                accion["factura"], linea, accion["factura"]["proveedor_info"]
            )

            cursor = conn.cursor()
            cursor.execute(
                """
                UPDATE consolidado
                SET descripcion = COALESCE(NULLIF(?, ''), descripcion),
                    precioListado = CASE
                        WHEN ? IS NOT NULL AND ? > 0 THEN ? ELSE precioListado END,
                    fechaActualizacion = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (
                    _texto(datos.get("descripcion")),
                    datos.get("precioListado"),
                    datos.get("precioListado"),
                    datos.get("precioListado"),
                    consolidado_id,
                ),
            )

            _recalcular_estado_importado(conn, consolidado_id)
            _vincular_linea(conn, linea["id"], consolidado_id)

            resultado["actualizados"] += 1
            resultado["vinculados"] += 1
        except Exception:
            resultado["errores"] += 1


def _recalcular_estado_importado(conn, consolidado_id):
    cur = conn.cursor()
    cur.execute("SELECT * FROM consolidado WHERE id = ?", (consolidado_id,))
    fila = _fila_a_dict(cur, cur.fetchone())
    if not fila:
        return

    if fila.get("estadoConsolidado") == ESTADO_INVENTARIADO:
        return
    if fila.get("idInventarioUnico"):
        return

    duplicado = _contar_coincidencias(
        conn,
        fila.get("abrevProveedor"),
        fila.get("referenciaProveedor"),
        excluir_id=consolidado_id,
    ) > 0
    estado = _estado_para_datos(fila, duplicado=duplicado)
    conn.execute(
        "UPDATE consolidado SET estadoConsolidado = ? WHERE id = ?",
        (estado, consolidado_id),
    )


def _vincular_linea(conn, linea_id, consolidado_id):
    conn.execute(
        """
        UPDATE inventarioFacturas
        SET id_consolidado = ?,
            referenciaOriginal = COALESCE(referenciaOriginal, referencia)
        WHERE id = ?
        """,
        (consolidado_id, linea_id),
    )


def editar_consolidado(
    ruta_db,
    consolidado_id,
    datos,
    usuario=None,
    ruta_estandarizacion=None,
):
    """Edita un registro, revalida duplicados, reestandariza y recalcula estado."""

    conn = db.conectar(ruta_db)
    try:
        migraciones.migrar(conn)
        cur = conn.cursor()
        cur.execute("SELECT * FROM consolidado WHERE id = ?", (consolidado_id,))
        actual = _fila_a_dict(cur, cur.fetchone())
        if not actual:
            return {"ok": False, "error": "El registro no existe."}

        merged = dict(actual)
        for campo in CAMPOS_EDITABLES:
            if campo in datos:
                merged[campo] = _texto(datos.get(campo))

        if not _texto(merged.get("referenciaProveedor")):
            return {
                "ok": False,
                "error": "La referencia del proveedor es obligatoria.",
            }
        if not _texto(merged.get("descripcion")):
            return {"ok": False, "error": "La descripción es obligatoria."}

        if _contar_coincidencias(
            conn,
            merged.get("abrevProveedor"),
            merged.get("referenciaProveedor"),
            excluir_id=consolidado_id,
        ) > 0:
            return {
                "ok": False,
                "error": (
                    "Ya existe otro artículo con el mismo proveedor y "
                    "referencia. Corrija el duplicado antes de guardar."
                ),
            }

        campos_manuales = {
            campo for campo in datos.keys() if campo in CAMPOS_EDITABLES
        }
        estandar = _estandarizar(
            [merged], ruta_estandarizacion, campos_manuales=campos_manuales
        )
        calculado = estandar[0] if estandar else {}

        # Los campos calculados sólo completan lo que sigue vacío. Nunca
        # reemplazan un valor manual o un dato válido ya guardado.
        for campo in CAMPOS_ESTANDARIZADOS:
            valor = _texto(calculado.get(campo))
            if valor and not _texto(merged.get(campo)):
                merged[campo] = valor

        # Si el usuario corrigió la descripción, la descripción estandarizada
        # es un valor derivado: se recalcula salvo que se haya enviado
        # explícitamente como dato manual.
        if (
            "descripcion" in campos_manuales
            and "descripcionEstandarizada" not in campos_manuales
        ):
            nueva_estandar = _texto(calculado.get("descripcionEstandarizada"))
            if nueva_estandar:
                merged["descripcionEstandarizada"] = nueva_estandar

        if not _texto(merged.get("descripcionEstandarizada")):
            merged["descripcionEstandarizada"] = normalizar_clave(
                merged.get("descripcion")
            )

        estado_actual = actual.get("estadoConsolidado")
        if estado_actual == ESTADO_INVENTARIADO:
            estado = ESTADO_INVENTARIADO
        else:
            estado = _estado_para_datos(merged, duplicado=False)

        cur.execute(
            """
            UPDATE consolidado
            SET descripcion = ?,
                referenciaProveedor = ?,
                referenciaRepuesto = ?,
                equivalente = ?,
                marca = ?,
                carro = ?,
                modelo = ?,
                lado = ?,
                ladoAbreviado = ?,
                medida = ?,
                grupo = ?,
                tipo = ?,
                tipoMotor = ?,
                otros = ?,
                repuesto = ?,
                descripcionEstandarizada = ?,
                estadoConsolidado = ?,
                fechaModificacion = CURRENT_TIMESTAMP,
                usuarioModificacion = ?,
                fechaActualizacion = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (
                _texto(merged.get("descripcion")),
                _mayus(merged.get("referenciaProveedor")),
                _texto(merged.get("referenciaRepuesto")),
                _texto(merged.get("equivalente")),
                _texto(merged.get("marca")),
                _texto(merged.get("carro")),
                _texto(merged.get("modelo")),
                _texto(merged.get("lado")),
                _texto(merged.get("ladoAbreviado")),
                _texto(merged.get("medida")),
                _texto(merged.get("grupo")),
                _texto(merged.get("tipo")),
                _texto(merged.get("tipoMotor")),
                _texto(merged.get("otros")),
                _texto(merged.get("repuesto")),
                _texto(merged.get("descripcionEstandarizada")),
                estado,
                _texto(usuario),
                consolidado_id,
            ),
        )
        conn.commit()

        cur.execute("SELECT * FROM consolidado WHERE id = ?", (consolidado_id,))
        actualizado = _fila_a_dict(cur, cur.fetchone())

        respaldo.actualizar_respaldo_si_posible(conn=conn)

        return {
            "ok": True,
            "registro": actualizado,
            "estadoConsolidado": estado,
        }
    except Exception as error:
        conn.rollback()
        return {"ok": False, "error": str(error)}
    finally:
        conn.close()


def _precio_inventario(fila, opciones):
    precio = a_entero((opciones or {}).get("precio"), 0)
    if precio > 0:
        return precio
    precio_listado = fila.get("precioListado")
    try:
        if precio_listado:
            return int(round(float(precio_listado) * 1.5, -3))
    except Exception:
        pass
    return 0


def inventariar_consolidado(
    ruta_db,
    ids,
    usuario=None,
    opciones=None,
):
    """Lleva uno o varios registros de Consolidado a Inventario.

    No modifica cantidades. La impresión se ejecuta después del commit.
    """

    opciones = opciones or {}
    ids_limpios = []
    for valor in ids or []:
        try:
            numero = int(valor)
        except Exception:
            continue
        if numero not in ids_limpios:
            ids_limpios.append(numero)

    resultado = {
        "ok": True,
        "inventariados": 0,
        "reutilizados": 0,
        "bloqueados": 0,
        "omitidos": 0,
        "errores": 0,
        "etiquetas": [],
        "detalle": [],
    }

    if not ids_limpios:
        resultado["ok"] = False
        resultado["error"] = "No se recibieron registros válidos."
        return resultado

    conn = db.conectar(ruta_db)
    try:
        migraciones.migrar(conn)
        # Serializa el inventariado: evita que dos envíos simultáneos creen
        # dos artículos para el mismo registro de Consolidado.
        if conn.in_transaction:
            conn.commit()
        conn.execute("BEGIN IMMEDIATE")
        cur = conn.cursor()

        for consolidado_id in ids_limpios:
            cur.execute("SELECT * FROM consolidado WHERE id = ?", (consolidado_id,))
            fila = _fila_a_dict(cur, cur.fetchone())

            if not fila:
                resultado["errores"] += 1
                resultado["detalle"].append(
                    {"id": consolidado_id, "motivo": "Registro inexistente."}
                )
                continue

            if fila.get("idInventarioUnico") or fila.get(
                "estadoConsolidado"
            ) == ESTADO_INVENTARIADO:
                cur.execute(
                    "SELECT id FROM inventarioUnico WHERE id = ?",
                    (fila.get("idInventarioUnico"),),
                )
                if cur.fetchone():
                    resultado["omitidos"] += 1
                    resultado["detalle"].append(
                        {"id": consolidado_id, "motivo": "Ya estaba inventariado."}
                    )
                    continue
                resultado["bloqueados"] += 1
                resultado["detalle"].append(
                    {
                        "id": consolidado_id,
                        "motivo": "Estado INVENTARIADO sin relación válida.",
                    }
                )
                continue

            if fila.get("estadoConsolidado") != ESTADO_LISTO:
                resultado["bloqueados"] += 1
                resultado["detalle"].append(
                    {
                        "id": consolidado_id,
                        "motivo": (
                            "El registro no está LISTO_INVENTARIAR "
                            f"({fila.get('estadoConsolidado')})."
                        ),
                    }
                )
                continue

            descripcion = _texto(fila.get("descripcionEstandarizada"))
            if not descripcion or not _referencia_valida(
                fila.get("referenciaProveedor")
            ):
                resultado["bloqueados"] += 1
                resultado["detalle"].append(
                    {"id": consolidado_id, "motivo": "Datos mínimos incompletos."}
                )
                continue

            clave = normalizar_clave(descripcion)
            if not clave:
                resultado["bloqueados"] += 1
                resultado["detalle"].append(
                    {"id": consolidado_id, "motivo": "Descripción no válida."}
                )
                continue

            precio_venta = _precio_inventario(fila, opciones)
            precio_cifrado = cifrar_precio(precio_venta)
            precio_max = int(round(precio_venta * 0.9, -3)) if precio_venta else 0
            costo = opciones.get("costo")
            if costo in (None, ""):
                costo = fila.get("precioListado")

            datos_operativos = {
                "descripcionEstandarizada": descripcion,
                "referenciaProveedor": fila.get("referenciaProveedor"),
                "referenciaRepuesto": fila.get("referenciaRepuesto"),
                "equivalente": fila.get("equivalente"),
                "marcaArticulo": fila.get("marca"),
                "marcaVehiculo": fila.get("carro"),
                "modelo": fila.get("modelo"),
                "lado": fila.get("lado"),
                "medida": fila.get("medida"),
            }

            articulo = obtener_o_crear_articulo(
                conn,
                descripcion=descripcion,
                descripcion_clave=clave,
                referencia=fila.get("referenciaProveedor")
                or fila.get("referenciaRepuesto"),
                proveedor=fila.get("proveedor") or "",
                precio_venta=precio_venta,
                precio_cifrado=precio_cifrado,
                precio_max_descuento=precio_max,
                grupo=opciones.get("grupo") or fila.get("grupo") or "",
                ubicacion=opciones.get("ubicacion"),
                estante=opciones.get("estante"),
                costo=costo,
                fecha=date.today().strftime("%Y-%m-%d"),
                cantidad=0,
                sumar_cantidad=False,
                datos_operativos=datos_operativos,
            )

            cur.execute(
                """
                UPDATE consolidado
                SET idInventarioUnico = ?,
                    estadoConsolidado = 'INVENTARIADO',
                    fechaInventariado = CURRENT_TIMESTAMP,
                    usuarioInventariado = ?,
                    fechaModificacion = CURRENT_TIMESTAMP,
                    usuarioModificacion = ?
                WHERE id = ?
                  AND (idInventarioUnico IS NULL OR idInventarioUnico = ?)
                """,
                (
                    articulo["id"],
                    _texto(usuario),
                    _texto(usuario),
                    consolidado_id,
                    articulo["id"],
                ),
            )
            if cur.rowcount == 0:
                # Otro proceso lo inventarió entre la validación y el UPDATE.
                resultado["omitidos"] += 1
                resultado["detalle"].append(
                    {"id": consolidado_id, "motivo": "Ya estaba inventariado."}
                )
                continue

            _vincular_lineas_por_clave(conn, fila, consolidado_id)

            resultado["inventariados"] += 1
            if articulo["reutilizado"]:
                resultado["reutilizados"] += 1

            resultado["etiquetas"].append(
                {
                    "idConsolidado": consolidado_id,
                    "idInventarioUnico": articulo["id"],
                    "codigo": articulo["codigoBarras"],
                    "descripcion": descripcion,
                    "precio": precio_cifrado,
                    "cantidad": 1,
                }
            )
            resultado["detalle"].append(
                {
                    "id": consolidado_id,
                    "idInventarioUnico": articulo["id"],
                    "codigoBarras": articulo["codigoBarras"],
                    "reutilizado": articulo["reutilizado"],
                }
            )

        conn.commit()
        respaldo.actualizar_respaldo_si_posible(conn=conn)
    except Exception as error:
        conn.rollback()
        resultado["ok"] = False
        resultado["error"] = str(error)
        resultado["errores"] += 1
    finally:
        conn.close()

    return resultado


def _vincular_lineas_por_clave(conn, fila, consolidado_id):
    """Marca la relación Factura -> Consolidado en líneas históricas."""

    referencia = _mayus(fila.get("referenciaProveedor"))
    proveedor = _mayus(fila.get("proveedor"))
    if not referencia:
        return

    conn.execute(
        """
        UPDATE inventarioFacturas
        SET id_consolidado = ?,
            referenciaOriginal = COALESCE(referenciaOriginal, referencia)
        WHERE UPPER(TRIM(COALESCE(NULLIF(TRIM(referenciaCorregida), ''),
                                   referencia))) = ?
          AND (id_consolidado IS NULL OR id_consolidado = ?)
          AND EXISTS (
              SELECT 1 FROM facturas f
              WHERE f.id_factura = inventarioFacturas.id_factura
                AND (UPPER(TRIM(f.proveedor)) = ?
                     OR UPPER(TRIM(COALESCE(f.proveedor, ''))) = ?)
          )
        """,
        (consolidado_id, referencia, consolidado_id, proveedor, proveedor),
    )


def etiquetas_reimpresion(ruta_db, ids):
    """Datos de etiqueta para reimprimir sin volver a inventariar."""

    resultado = []
    conn = db.conectar(ruta_db)
    try:
        cur = conn.cursor()
        for consolidado_id in ids or []:
            try:
                consolidado_id = int(consolidado_id)
            except Exception:
                continue
            cur.execute(
                """
                SELECT iu.codigoBarras, iu.descripcion,
                       iu.precioVentaCifrado
                FROM consolidado c
                JOIN inventarioUnico iu ON iu.id = c.idInventarioUnico
                WHERE c.id = ?
                """,
                (consolidado_id,),
            )
            fila = cur.fetchone()
            if not fila:
                continue
            resultado.append(
                {
                    "idConsolidado": consolidado_id,
                    "codigo": fila[0],
                    "descripcion": fila[1],
                    "precio": fila[2],
                    "cantidad": 1,
                }
            )
    finally:
        conn.close()

    return resultado
