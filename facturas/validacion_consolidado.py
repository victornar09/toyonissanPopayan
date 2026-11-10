"""Validación obligatoria contra la tabla consolidado para inventariado de facturas.

Regla central: Ninguna línea de factura puede pasar a inventarioUnico sin una
coincidencia válida y no ambigua en consolidado. La clave es (proveedor/siglas + referencia del proveedor).
"""
from __future__ import annotations

from typing import Any, Dict, Optional, Tuple
import re

from utilidades import normalizar_clave


def _norm_str(v: Any) -> str:
    if v is None:
        return ""
    s = str(v).strip()
    return s


def _norm_up(v: Any) -> str:
    return _norm_str(v).upper()


def resolver_proveedor(conn, nombre_proveedor_factura: str) -> Optional[Dict[str, Any]]:
    """Resuelve proveedor desde el nombre que viene en la factura."""
    nombre = _norm_str(nombre_proveedor_factura)
    if not nombre:
        return None
    cur = conn.cursor()
    cur.execute(
        """
        SELECT idProveedor, nombreProveedor, nombreProveedorSecundario, siglas, correoFacturas, prefijo, nit, ciudad, telefono
        FROM proveedor
        WHERE UPPER(TRIM(nombreProveedor)) = ? OR UPPER(TRIM(COALESCE(nombreProveedorSecundario,''))) = ?
        """,
        (nombre.upper(), nombre.upper()),
    )
    row = cur.fetchone()
    if not row:
        return None
    cols = [d[0] for d in cur.description]
    data = dict(zip(cols, row))
    return data


def buscar_consolidado_por_proveedor_ref(conn, siglas_abrev: str, referencia_proveedor: str) -> list:
    """Busca en consolidado por (abrevProveedor, referenciaProveedor) normalizados."""
    abrev = _norm_up(siglas_abrev)
    ref = _norm_up(referencia_proveedor)
    if not abrev or not ref:
        return []
    cur = conn.cursor()
    cur.execute(
        """
        SELECT id, idProveedor, abrevProveedor, referenciaProveedor,
               referenciaRepuesto, equivalente, descripcion, marca, carro,
               tipo, tipoMotor, precioListado, proveedor, lado, ladoAbreviado,
               repuesto, modelo, otros, medida, descripcionEstandarizada,
               grupo, fechaActualizacion
        FROM consolidado
        WHERE UPPER(TRIM(abrevProveedor)) = ? AND UPPER(TRIM(referenciaProveedor)) = ?
        """,
        (abrev, ref),
    )
    rows = cur.fetchall()
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, r)) for r in rows]


def _es_referencia_valida(ref: str) -> bool:
    r = _norm_str(ref)
    if not r:
        return False
    if r.upper() == "SIN_REFERENCIA":
        return False
    return True


def _consolidado_valido_minimo(reg: Dict[str, Any]) -> Tuple[bool, str]:
    desc_est = _norm_str(reg.get("descripcionEstandarizada"))
    if not desc_est:
        return False, "El registro de consolidado no tiene descripcionEstandarizada válida."
    # Requerimos al menos datos suficientes para estandarizar/inventariar
    if len(desc_est) < 3:
        return False, "descripcionEstandarizada incompleta en consolidado."
    return True, ""


def obtener_estado_linea(
    conn,
    factura_row: tuple,
    linea_row: tuple,
    proveedor_info_resuelto: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Calcula estado para una línea de inventarioFacturas.

    factura_row esperado: (proveedor, fecha, valor_total, texto_xml, ubicacion_pdf) o similar
    linea_row esperado: (id, referencia, descripcion_item, cantidad, valor_unitario, inventariado, id_inventarioUnico?) 
    """
    # Extraer campos de línea flexiblemente
    try:
        linea_id = linea_row[0]
        referencia = linea_row[1]
        descripcion_item = linea_row[2]
        cantidad = linea_row[3]
        valor_unitario = linea_row[4]
        inventariado = linea_row[5]
        id_inventario_unico = linea_row[6] if len(linea_row) > 6 else None
    except Exception:
        return {
            "codigo_estado": "NO_PERMITIDO",
            "etiqueta_estado": "NO INVENTARIADO NO PERMITIDO",
            "permitido": False,
            "motivo": "Estructura de línea inválida.",
            "registro_consolidado": None,
            "datos_estandarizados": None,
            "advertencias": ["estructura_invalida"],
            "linea_id": None,
        }

    # 1. INVENTARIADO
    if inventariado == 1 or inventariado is True:
        # Verificar relación real si existe
        if id_inventario_unico:
            cur = conn.cursor()
            cur.execute("SELECT id FROM inventarioUnico WHERE id = ?", (id_inventario_unico,))
            if cur.fetchone():
                return {
                    "codigo_estado": "INVENTARIADO",
                    "etiqueta_estado": "INVENTARIADO",
                    "permitido": False,
                    "motivo": "Ya fue inventariado.",
                    "registro_consolidado": None,
                    "datos_estandarizados": None,
                    "advertencias": [],
                    "linea_id": linea_id,
                }
        # Aún si marcado inventariado sin relación clara, seguir como INVENTARIADO para no duplicar
        return {
            "codigo_estado": "INVENTARIADO",
            "etiqueta_estado": "INVENTARIADO",
            "permitido": False,
            "motivo": "Ya fue inventariado.",
            "registro_consolidado": None,
            "datos_estandarizados": None,
            "advertencias": ["inventariado_sin_relacion_clara"],
            "linea_id": linea_id,
        }

    # Verificar relación con inventarioUnico aunque inventariado sea 0 (inconsistencia histórica)
    if id_inventario_unico:
        cur = conn.cursor()
        cur.execute("SELECT id FROM inventarioUnico WHERE id = ?", (id_inventario_unico,))
        if cur.fetchone():
            return {
                "codigo_estado": "INVENTARIADO",
                "etiqueta_estado": "INVENTARIADO",
                "permitido": False,
                "motivo": "Ya fue inventariado.",
                "registro_consolidado": None,
                "datos_estandarizados": None,
                "advertencias": ["inventariado_inconsistente"],
                "linea_id": linea_id,
            }

    # 2. Resolver proveedor
    prov_info = proveedor_info_resuelto
    if prov_info is None and factura_row:
        try:
            nombre_prov_factura = factura_row[0]
        except Exception:
            nombre_prov_factura = None
        if nombre_prov_factura:
            prov_info = resolver_proveedor(conn, nombre_prov_factura)

    if not prov_info or not prov_info.get("siglas"):
        return {
            "codigo_estado": "NO_PERMITIDO",
            "etiqueta_estado": "NO INVENTARIADO NO PERMITIDO",
            "permitido": False,
            "motivo": "Proveedor no identificado o sin siglas en maestro de proveedores.",
            "registro_consolidado": None,
            "datos_estandarizados": None,
            "advertencias": ["proveedor_sin_siglas"],
            "linea_id": linea_id,
        }

    siglas = prov_info.get("siglas")

    # 3. Validar referencia
    if not _es_referencia_valida(referencia):
        return {
            "codigo_estado": "NO_PERMITIDO",
            "etiqueta_estado": "NO INVENTARIADO NO PERMITIDO",
            "permitido": False,
            "motivo": "Referencia de proveedor vacía o inválida (SIN_REFERENCIA). Debe corregirse.",
            "registro_consolidado": None,
            "datos_estandarizados": None,
            "advertencias": ["referencia_invalida"],
            "linea_id": linea_id,
        }

    # 4. Buscar en consolidado
    matches = buscar_consolidado_por_proveedor_ref(conn, siglas, referencia)
    if len(matches) == 0:
        return {
            "codigo_estado": "NO_PERMITIDO",
            "etiqueta_estado": "NO INVENTARIADO NO PERMITIDO",
            "permitido": False,
            "motivo": "No existe coincidencia en consolidado para este proveedor y referencia. Regístrelo o corríjalo primero en consolidado.",
            "registro_consolidado": None,
            "datos_estandarizados": None,
            "advertencias": ["sin_coincidencia_consolidado"],
            "linea_id": linea_id,
        }
    if len(matches) > 1:
        return {
            "codigo_estado": "NO_PERMITIDO",
            "etiqueta_estado": "NO INVENTARIADO NO PERMITIDO",
            "permitido": False,
            "motivo": "Se encontraron múltiples registros en consolidado para el proveedor y la referencia. Corrija el catálogo maestro antes de inventariar.",
            "registro_consolidado": None,
            "datos_estandarizados": None,
            "advertencias": ["coincidencia_ambigua"],
            "linea_id": linea_id,
        }

    reg = matches[0]
    ok_min, motivo_min = _consolidado_valido_minimo(reg)
    if not ok_min:
        return {
            "codigo_estado": "NO_PERMITIDO",
            "etiqueta_estado": "NO INVENTARIADO NO PERMITIDO",
            "permitido": False,
            "motivo": motivo_min,
            "registro_consolidado": reg,
            "datos_estandarizados": None,
            "advertencias": ["consolidado_incompleto"],
            "linea_id": linea_id,
        }

    # 5. PERMITIDO
    datos_est = {
        "descripcion": reg.get("descripcionEstandarizada"),
        "descripcionEstandarizada": reg.get("descripcionEstandarizada"),
        "proveedor": reg.get("proveedor") or prov_info.get("nombreProveedor"),
        "idProveedor": reg.get("idProveedor") or prov_info.get("idProveedor"),
        "referencia": referencia,
        "referenciaRepuesto": reg.get("referenciaRepuesto"),
        "marca": reg.get("marca"),
        "carro": reg.get("carro"),
        "repuesto": reg.get("repuesto"),
        "modelo": reg.get("modelo"),
        "medida": reg.get("medida"),
        "lado": reg.get("lado"),
        "ladoAbreviado": reg.get("ladoAbreviado"),
        "otros": reg.get("otros"),
        "precioListado": reg.get("precioListado"),
        "grupo": reg.get("grupo"),
        "id_consolidado": reg.get("id"),
    }
    return {
        "codigo_estado": "PERMITIDO",
        "etiqueta_estado": "NO INVENTARIADO PERMITIDO",
        "permitido": True,
        "motivo": "Coincidencia válida en consolidado.",
        "registro_consolidado": reg,
        "datos_estandarizados": datos_est,
        "advertencias": [],
        "linea_id": linea_id,
    }


def obtener_estados_factura(conn, id_factura: str) -> Dict[int, Dict[str, Any]]:
    cur = conn.cursor()
    cur.execute(
        """
        SELECT proveedor, fecha, valor_total, texto_xml, ubicacion_pdf
        FROM facturas
        WHERE id_factura = ?
        """,
        (id_factura,),
    )
    factura = cur.fetchone()
    cur.execute(
        """
        SELECT id, referencia, descripcion_item, cantidad, valor_unitario, inventariado, id_inventarioUnico
        FROM inventarioFacturas
        WHERE id_factura = ?
        """,
        (id_factura,),
    )
    lineas = cur.fetchall()
    prov_info = None
    if factura and factura[0]:
        prov_info = resolver_proveedor(conn, factura[0])
    estados = {}
    for linea in lineas:
        est = obtener_estado_linea(conn, factura, linea, prov_info)
        estados[est["linea_id"]] = est
    return estados
