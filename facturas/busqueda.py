"""Búsqueda transversal de artículos dentro de las facturas.

Solo consulta: no modifica Facturas, Consolidado ni Inventario.
"""

from __future__ import annotations

import math
import sqlite3
from datetime import datetime

import db
from utilidades import normalizar_clave


POR_PAGINA = 50

ESTADOS = {
    "todos": "Todos",
    "inventariado": "Inventariado",
    "pendiente_permitido": "Pendiente permitido",
    "pendiente_no_permitido": "Pendiente no permitido",
    "consolidado": "Consolidado",
    "no_consolidado": "No consolidado",
}

_ALIAS_ESTADO = {
    "": "todos",
    "todos": "todos",
    "inventariado": "inventariado",
    "pendiente permitido": "pendiente_permitido",
    "pendiente_permitido": "pendiente_permitido",
    "pendiente no permitido": "pendiente_no_permitido",
    "pendiente_no_permitido": "pendiente_no_permitido",
    "consolidado": "consolidado",
    "no consolidado": "no_consolidado",
    "no_consolidado": "no_consolidado",
}


def _texto(valor):
    return ("" if valor is None else str(valor)).strip()


def _fecha_valida(valor):
    valor = _texto(valor)
    if not valor:
        return ""
    try:
        datetime.strptime(valor, "%Y-%m-%d")
        return valor
    except ValueError:
        return ""


def extraer_filtros(args):
    estado = _ALIAS_ESTADO.get(
        _texto(args.get("estado")).lower(), "todos"
    )
    return {
        "referencia": _texto(args.get("referencia")),
        "descripcion": _texto(args.get("descripcion")),
        "proveedor": _texto(args.get("proveedor")),
        "id_factura": _texto(args.get("id_factura")),
        "fecha_desde": _fecha_valida(args.get("fecha_desde")),
        "fecha_hasta": _fecha_valida(args.get("fecha_hasta")),
        "estado": estado,
    }


def _tokens(valor):
    normalizado = normalizar_clave(valor) or ""
    return normalizado.split()


def _expresion_normalizada(campos):
    partes = [f"COALESCE({campo}, '')" for campo in campos]
    return "normalizar(" + " || ' ' || ".join(partes) + ")"


def _agregar_tokens(condiciones, parametros, campos, texto):
    expresion = _expresion_normalizada(campos)
    for token in _tokens(texto):
        condiciones.append(f"{expresion} LIKE ?")
        parametros.append(f"%{token}%")


def _where(filtros):
    condiciones = []
    parametros = []

    if filtros["referencia"]:
        _agregar_tokens(
            condiciones,
            parametros,
            [
                "referencia",
                "referenciaOriginal",
                "referenciaCorregida",
                "referencia_efectiva",
                "consolidado_referencia_proveedor",
                "consolidado_referencia_repuesto",
                "consolidado_equivalente",
            ],
            filtros["referencia"],
        )

    if filtros["descripcion"]:
        _agregar_tokens(
            condiciones,
            parametros,
            ["descripcion_item", "descripcion_estandarizada"],
            filtros["descripcion"],
        )

    if filtros["proveedor"]:
        _agregar_tokens(
            condiciones,
            parametros,
            ["proveedor", "proveedor_maestro", "proveedor_siglas"],
            filtros["proveedor"],
        )

    if filtros["id_factura"]:
        condiciones.append("UPPER(TRIM(id_factura)) LIKE ?")
        parametros.append(f"%{filtros['id_factura'].upper()}%")

    if filtros["fecha_desde"]:
        condiciones.append("date(fecha) >= date(?)")
        parametros.append(filtros["fecha_desde"])

    if filtros["fecha_hasta"]:
        condiciones.append("date(fecha) <= date(?)")
        parametros.append(filtros["fecha_hasta"])

    estado = filtros["estado"]
    if estado == "inventariado":
        condiciones.append("estado_inventariado = 'INVENTARIADO'")
    elif estado == "pendiente_permitido":
        condiciones.append("estado_inventariado = 'PENDIENTE_PERMITIDO'")
    elif estado == "pendiente_no_permitido":
        condiciones.append("estado_inventariado = 'PENDIENTE_NO_PERMITIDO'")
    elif estado == "consolidado":
        condiciones.append("estado_consolidado = 'CONSOLIDADO'")
    elif estado == "no_consolidado":
        condiciones.append("estado_consolidado = 'NO_CONSOLIDADO'")

    return (" AND ".join(condiciones) if condiciones else "1=1"), parametros


_CTE = """
WITH base AS (
    SELECT
        iF.id AS id_linea,
        iF.id_factura,
        iF.referencia,
        iF.referenciaOriginal,
        iF.referenciaCorregida,
        iF.descripcion_item,
        iF.cantidad,
        iF.valor_unitario,
        iF.inventariado,
        iF.id_inventarioUnico,
        iF.id_consolidado,
        f.fecha,
        f.proveedor,
        p.siglas AS proveedor_siglas,
        p.nombreProveedor AS proveedor_maestro,
        iu.codigoBarras,
        iu.id AS id_inventario_real,
        COALESCE(
            NULLIF(TRIM(iF.referenciaCorregida), ''),
            NULLIF(TRIM(iF.referencia), ''),
            ''
        ) AS referencia_efectiva,
        (
            SELECT c2.id
            FROM consolidado c2
            WHERE p.siglas IS NOT NULL
              AND TRIM(p.siglas) <> ''
              AND UPPER(TRIM(c2.abrevProveedor)) = UPPER(TRIM(p.siglas))
              AND UPPER(TRIM(c2.referenciaProveedor)) = UPPER(TRIM(
                    COALESCE(
                        NULLIF(TRIM(iF.referenciaCorregida), ''),
                        NULLIF(TRIM(iF.referencia), ''),
                        ''
                    )
              ))
            ORDER BY c2.id
            LIMIT 1
        ) AS id_consolidado_ref,
        (
            SELECT COUNT(*)
            FROM consolidado c3
            WHERE p.siglas IS NOT NULL
              AND TRIM(p.siglas) <> ''
              AND UPPER(TRIM(c3.abrevProveedor)) = UPPER(TRIM(p.siglas))
              AND UPPER(TRIM(c3.referenciaProveedor)) = UPPER(TRIM(
                    COALESCE(
                        NULLIF(TRIM(iF.referenciaCorregida), ''),
                        NULLIF(TRIM(iF.referencia), ''),
                        ''
                    )
              ))
        ) AS coincidencias_consolidado
    FROM inventarioFacturas iF
    JOIN facturas f ON f.id_factura = iF.id_factura
    LEFT JOIN proveedor p ON p.idProveedor = (
        SELECT p2.idProveedor
        FROM proveedor p2
        WHERE UPPER(TRIM(p2.nombreProveedor)) = UPPER(TRIM(f.proveedor))
           OR UPPER(TRIM(COALESCE(p2.nombreProveedorSecundario, ''))) =
              UPPER(TRIM(f.proveedor))
        ORDER BY p2.idProveedor
        LIMIT 1
    )
    LEFT JOIN inventarioUnico iu ON iu.id = iF.id_inventarioUnico
),
enriquecido AS (
    SELECT
        b.*,
        c.id AS id_consolidado_efectivo,
        c.descripcionEstandarizada AS descripcion_estandarizada,
        c.referenciaProveedor AS consolidado_referencia_proveedor,
        c.referenciaRepuesto AS consolidado_referencia_repuesto,
        c.equivalente AS consolidado_equivalente,
        c.estadoConsolidado AS consolidado_estado,
        CASE
            WHEN b.inventariado = 1 OR b.id_inventario_real IS NOT NULL
                THEN 'INVENTARIADO'
            WHEN b.coincidencias_consolidado = 1
                 AND LENGTH(TRIM(COALESCE(c.descripcionEstandarizada, ''))) >= 3
                 AND UPPER(TRIM(b.referencia_efectiva)) NOT IN ('', 'SIN_REFERENCIA')
                 AND b.proveedor_siglas IS NOT NULL
                 AND TRIM(b.proveedor_siglas) <> ''
                THEN 'PENDIENTE_PERMITIDO'
            ELSE 'PENDIENTE_NO_PERMITIDO'
        END AS estado_inventariado,
        CASE
            WHEN b.id_consolidado IS NOT NULL
              OR b.id_consolidado_ref IS NOT NULL
                THEN 'CONSOLIDADO'
            ELSE 'NO_CONSOLIDADO'
        END AS estado_consolidado
    FROM base b
    LEFT JOIN consolidado c
        ON c.id = COALESCE(b.id_consolidado, b.id_consolidado_ref)
)
"""


def _etiqueta_inventario(estado):
    if estado == "INVENTARIADO":
        return "Inventariado", "success"
    if estado == "PENDIENTE_PERMITIDO":
        return "Pendiente permitido", "primary"
    return "Pendiente no permitido", "danger"


def _etiqueta_consolidado(estado):
    if estado == "CONSOLIDADO":
        return "Consolidado", "success"
    return "No consolidado", "secondary"


def _presentar(fila):
    datos = dict(fila)
    original = (datos.get("referenciaOriginal") or "").strip()
    efectiva = (datos.get("referencia") or "").strip()
    corregida = (datos.get("referenciaCorregida") or "").strip()

    datos["referencia_original_mostrar"] = original or efectiva
    if corregida and normalizar_clave(corregida) != normalizar_clave(
        original or efectiva
    ):
        datos["referencia_corregida_mostrar"] = corregida
    else:
        datos["referencia_corregida_mostrar"] = ""

    datos["descripcion_original_mostrar"] = datos.get("descripcion_item") or ""
    datos["descripcion_estandarizada_mostrar"] = (
        datos.get("descripcion_estandarizada") or ""
    )

    etiqueta, clase = _etiqueta_inventario(datos["estado_inventariado"])
    datos["estado_inventario_label"] = etiqueta
    datos["estado_inventario_badge"] = clase

    etiqueta, clase = _etiqueta_consolidado(datos["estado_consolidado"])
    datos["estado_consolidado_label"] = etiqueta
    datos["estado_consolidado_badge"] = clase
    return datos


def buscar_articulos(ruta_db, filtros, pagina=1, por_pagina=POR_PAGINA):
    """Consulta paginada. Devuelve filas presentadas y metadatos."""

    where, parametros = _where(filtros)

    try:
        pagina = max(1, int(pagina))
    except (TypeError, ValueError):
        pagina = 1

    por_pagina = max(1, min(int(por_pagina or POR_PAGINA), 200))

    conn = db.conectar(ruta_db)
    try:
        conn.row_factory = sqlite3.Row

        total = conn.execute(
            f"{_CTE} SELECT COUNT(*) FROM enriquecido WHERE {where}",
            parametros,
        ).fetchone()[0]

        total_paginas = max(1, math.ceil(total / por_pagina))
        pagina = min(pagina, total_paginas)
        desplazamiento = (pagina - 1) * por_pagina

        filas = conn.execute(
            f"""
            {_CTE}
            SELECT *
            FROM enriquecido
            WHERE {where}
            ORDER BY date(fecha) DESC,
                     proveedor COLLATE NOCASE ASC,
                     id_linea ASC
            LIMIT ? OFFSET ?
            """,
            (*parametros, por_pagina, desplazamiento),
        ).fetchall()
    finally:
        conn.close()

    return {
        "filas": [_presentar(fila) for fila in filas],
        "total": total,
        "pagina": pagina,
        "por_pagina": por_pagina,
        "total_paginas": total_paginas,
        "desde": (pagina - 1) * por_pagina + 1 if total else 0,
        "hasta": min(pagina * por_pagina, total),
    }
