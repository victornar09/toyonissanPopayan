"""Rutas HTTP del módulo Consolidado."""

from __future__ import annotations

import math

from flask import (
    Blueprint,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    session,
    url_for,
)

import db
from consolidado import servicio


consolidado_bp = Blueprint(
    "consolidado", __name__, url_prefix="/consolidado"
)

POR_PAGINA = 25


def _autenticado():
    return "usuario" in session


def _datos_formulario():
    if request.is_json:
        return request.get_json(silent=True) or {}
    return request.form.to_dict()


def _filtros_desde_request():
    campos = [
        "descripcion",
        "descripcion_estandarizada",
        "referencia_proveedor",
        "referencia_repuesto",
        "equivalente",
        "marca",
        "marca_vehiculo",
        "modelo",
        "lado",
        "proveedor",
        "estado",
        "inventariado",
    ]
    return {
        campo: (request.args.get(campo, "") or "").strip()
        for campo in campos
    }


def _consulta_listado(filtros):
    condiciones = []
    parametros = []

    mapa = {
        "descripcion": "descripcion",
        "descripcion_estandarizada": "descripcionEstandarizada",
        "referencia_proveedor": "referenciaProveedor",
        "referencia_repuesto": "referenciaRepuesto",
        "equivalente": "equivalente",
        "marca": "marca",
        "marca_vehiculo": "carro",
        "modelo": "modelo",
        "lado": "lado",
    }

    for clave, columna in mapa.items():
        valor = filtros.get(clave)
        if valor:
            condiciones.append(
                f"UPPER(TRIM(COALESCE({columna}, ''))) LIKE ?"
            )
            parametros.append(f"%{valor.upper()}%")

    if filtros.get("proveedor"):
        condiciones.append(
            """
            (UPPER(TRIM(COALESCE(abrevProveedor, ''))) LIKE ?
             OR UPPER(TRIM(COALESCE(proveedor, ''))) LIKE ?)
            """
        )
        patron = f"%{filtros['proveedor'].upper()}%"
        parametros.extend([patron, patron])

    if filtros.get("estado"):
        condiciones.append("estadoConsolidado = ?")
        parametros.append(filtros["estado"])

    inventariado = filtros.get("inventariado")
    if inventariado == "si":
        condiciones.append(
            "(estadoConsolidado = 'INVENTARIADO' "
            "OR idInventarioUnico IS NOT NULL)"
        )
    elif inventariado == "no":
        condiciones.append(
            "(estadoConsolidado <> 'INVENTARIADO' "
            "AND idInventarioUnico IS NULL)"
        )

    where = ("WHERE " + " AND ".join(condiciones)) if condiciones else ""
    return where, parametros


@consolidado_bp.route("/", methods=["GET"])
def consolidado_home():
    if not _autenticado():
        return redirect(url_for("auth.login"))

    filtros = _filtros_desde_request()
    pagina = max(1, request.args.get("pagina", 1, type=int) or 1)

    registros = []
    total = 0
    proveedores = []
    plantillas = []
    resumen = {}
    ruta_db = db.ruta_bd_activa()

    if ruta_db:
        where, parametros = _consulta_listado(filtros)

        with db.conexion(ruta_db) as conn:
            cursor = conn.cursor()
            cursor.execute(
                f"SELECT COUNT(*) FROM consolidado {where}", tuple(parametros)
            )
            total = cursor.fetchone()[0]

            cursor.execute("""
                SELECT estadoConsolidado, COUNT(*)
                FROM consolidado
                GROUP BY estadoConsolidado
            """)
            resumen = {fila[0]: fila[1] for fila in cursor.fetchall()}

            total_paginas = max(1, math.ceil(total / POR_PAGINA))
            pagina = min(pagina, total_paginas)
            desplazamiento = (pagina - 1) * POR_PAGINA

            cursor.execute(
                f"""
                SELECT id, abrevProveedor, referenciaProveedor,
                       referenciaRepuesto, equivalente, descripcion,
                       descripcionEstandarizada, marca, carro, modelo, lado,
                       medida, grupo, estadoConsolidado, idInventarioUnico,
                       fechaInventariado, usuarioInventariado,
                       fechaModificacion, usuarioModificacion,
                       (SELECT iu.codigoBarras FROM inventarioUnico iu
                        WHERE iu.id = consolidado.idInventarioUnico) AS codigoBarras
                FROM consolidado
                {where}
                ORDER BY
                    CASE estadoConsolidado
                        WHEN 'BLOQUEADO' THEN 1
                        WHEN 'LISTO_INVENTARIAR' THEN 2
                        WHEN 'PENDIENTE_REVISION' THEN 3
                        ELSE 4
                    END,
                    descripcionEstandarizada,
                    id
                LIMIT ? OFFSET ?
                """,
                tuple(parametros) + (POR_PAGINA, desplazamiento),
            )
            registros = cursor.fetchall()

            cursor.execute("""
                SELECT DISTINCT abrevProveedor
                FROM consolidado
                WHERE abrevProveedor IS NOT NULL
                  AND TRIM(abrevProveedor) <> ''
                ORDER BY abrevProveedor
            """)
            proveedores = [fila[0] for fila in cursor.fetchall()]

            cursor.execute("""
                SELECT idBartenderFiles, name
                FROM bartenderFiles
                WHERE activo = 1
                ORDER BY name
            """)
            plantillas = cursor.fetchall()

    total_paginas = max(1, math.ceil(total / POR_PAGINA)) if total else 1

    return render_template(
        "consolidado/consolidado.html",
        registros=registros,
        total=total,
        pagina=pagina,
        total_paginas=total_paginas,
        por_pagina=POR_PAGINA,
        filtros=filtros,
        proveedores=proveedores,
        plantillas=plantillas,
        estados=servicio.ESTADOS_VALIDOS,
        resumen=resumen,
        usuario=session.get("usuario"),
    )


@consolidado_bp.route("/importar/preview", methods=["POST"])
def importar_preview():
    if not _autenticado():
        return jsonify({"ok": False, "error": "No autenticado"}), 401

    datos = _datos_formulario()
    ruta_db = db.ruta_bd_activa()
    if not ruta_db:
        return jsonify({"ok": False, "error": "No hay base de datos."}), 400

    resultado = servicio.importar_desde_facturas(
        ruta_db,
        (datos.get("fecha_inicial") or "").strip(),
        (datos.get("fecha_final") or "").strip(),
        proveedor=(datos.get("proveedor") or "").strip() or None,
        solo_no_procesadas=str(datos.get("solo_no_procesadas", "1"))
        in ("1", "true", "True", "on"),
        confirmar=False,
        usuario=session.get("usuario"),
    )
    return jsonify(resultado)


@consolidado_bp.route("/importar", methods=["POST"])
def importar_confirmar():
    if not _autenticado():
        return jsonify({"ok": False, "error": "No autenticado"}), 401

    datos = _datos_formulario()
    ruta_db = db.ruta_bd_activa()
    if not ruta_db:
        return jsonify({"ok": False, "error": "No hay base de datos."}), 400

    resultado = servicio.importar_desde_facturas(
        ruta_db,
        (datos.get("fecha_inicial") or "").strip(),
        (datos.get("fecha_final") or "").strip(),
        proveedor=(datos.get("proveedor") or "").strip() or None,
        solo_no_procesadas=str(datos.get("solo_no_procesadas", "1"))
        in ("1", "true", "True", "on"),
        confirmar=True,
        usuario=session.get("usuario"),
    )

    if resultado.get("ok"):
        flash(
            "Importación completada: "
            f"creados {resultado.get('creados', 0)}, "
            f"actualizados {resultado.get('actualizados', 0)}, "
            f"omitidos {resultado.get('omitidas', 0)}, "
            f"bloqueados {resultado.get('bloqueadas', 0)}, "
            f"errores {resultado.get('errores', 0)}.",
            "success" if not resultado.get("errores") else "warning",
        )

    return jsonify(resultado)


@consolidado_bp.route("/editar/<int:consolidado_id>", methods=["POST"])
def editar(consolidado_id):
    if not _autenticado():
        return jsonify({"ok": False, "error": "No autenticado"}), 401

    ruta_db = db.ruta_bd_activa()
    if not ruta_db:
        return jsonify({"ok": False, "error": "No hay base de datos."}), 400

    datos = _datos_formulario()
    resultado = servicio.editar_consolidado(
        ruta_db,
        consolidado_id,
        datos,
        usuario=session.get("usuario"),
    )
    return jsonify(resultado), (200 if resultado.get("ok") else 400)


@consolidado_bp.route("/inventariar", methods=["POST"])
def inventariar():
    if not _autenticado():
        return jsonify({"ok": False, "error": "No autenticado"}), 401

    ruta_db = db.ruta_bd_activa()
    if not ruta_db:
        return jsonify({"ok": False, "error": "No hay base de datos."}), 400

    datos = _datos_formulario()
    ids = datos.get("ids") or datos.get("id")
    if isinstance(ids, (int, str)):
        ids = [ids]

    opciones = {
        "ubicacion": datos.get("ubicacion"),
        "estante": datos.get("estante"),
        "grupo": datos.get("grupo"),
        "costo": datos.get("costo"),
        "precio": datos.get("precio"),
    }

    resultado = servicio.inventariar_consolidado(
        ruta_db, ids, usuario=session.get("usuario"), opciones=opciones
    )

    template_id = datos.get("template_id")
    if resultado.get("etiquetas") and template_id:
        try:
            trabajo = _imprimir_etiquetas(
                resultado["etiquetas"], template_id, session.get("usuario")
            )
            resultado["impresion"] = "encolada"
            resultado["idTrabajoImpresion"] = trabajo.get("idTrabajo")
        except Exception as error:
            resultado["errores_impresion"] = len(resultado["etiquetas"])
            resultado["error_impresion"] = str(error)

    return jsonify(resultado)


@consolidado_bp.route("/reimprimir", methods=["POST"])
def reimprimir():
    if not _autenticado():
        return jsonify({"ok": False, "error": "No autenticado"}), 401

    ruta_db = db.ruta_bd_activa()
    if not ruta_db:
        return jsonify({"ok": False, "error": "No hay base de datos."}), 400

    datos = _datos_formulario()
    ids = datos.get("ids") or datos.get("id") or []
    if isinstance(ids, (int, str)):
        ids = [ids]

    etiquetas = servicio.etiquetas_reimpresion(ruta_db, ids)
    if not etiquetas:
        return jsonify(
            {"ok": False, "error": "No hay etiquetas para reimprimir."}
        ), 400

    template_id = datos.get("template_id")
    if not template_id:
        return jsonify(
            {"ok": False, "error": "Seleccione una etiqueta BarTender."}
        ), 400

    try:
        trabajo = _imprimir_etiquetas(
            etiquetas, template_id, session.get("usuario")
        )
        return jsonify(
            {
                "ok": True,
                "impresas": len(etiquetas),
                "impresion": "encolada",
                "idTrabajoImpresion": trabajo.get("idTrabajo"),
            }
        )
    except Exception as error:
        return jsonify({"ok": False, "error": str(error)}), 500


def _imprimir_etiquetas(etiquetas, template_id, usuario=None):
    """Encola la impresión; el worker del servidor la procesa después."""

    from impresion import servicio as impresion_servicio

    with db.conexion() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT name, ubicacion FROM bartenderFiles WHERE idBartenderFiles = ?",
            (template_id,),
        )
        fila = cursor.fetchone()

    if not fila:
        raise RuntimeError("Template BarTender no encontrado.")

    productos = [
        (
            etiqueta.get("codigo"),
            etiqueta.get("descripcion"),
            etiqueta.get("precio"),
            etiqueta.get("cantidad", 1),
        )
        for etiqueta in etiquetas
    ]

    return impresion_servicio.crear_trabajo(
        db.ruta_bd_activa(),
        usuario=usuario,
        articulos=productos,
        id_plantilla=template_id,
        plantilla=fila[0],
        ruta_plantilla=fila[1],
        origen="consolidado",
        columna_inicio=1,
    )
