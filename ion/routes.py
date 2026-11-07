"""Rutas HTTP del asistente ION.

ION sólo busca repuestos: no modifica inventario, precios, cantidades ni
Consolidado, y no crea artículos. Toda consulta queda auditada.
"""

from __future__ import annotations

import json
from datetime import datetime

from flask import (
    Blueprint,
    jsonify,
    redirect,
    render_template,
    request,
    session,
    url_for,
)

import db
from . import busqueda, conocimiento, preguntas


ion_bp = Blueprint("ion", __name__, url_prefix="/ion")


# ---------------------------------------------------------------------------
# Utilidades
# ---------------------------------------------------------------------------
def _autenticado():
    return "usuario" in session


def _usuario():
    return session.get("usuario")


def _datos():
    if request.is_json:
        return request.get_json(silent=True) or {}
    return request.form.to_dict()


def _json_entrada(valor, defecto):
    if valor is None:
        return defecto
    if isinstance(valor, (dict, list)):
        return valor
    try:
        return json.loads(valor)
    except (TypeError, ValueError):
        return defecto


def _respuesta_no_autorizada():
    return jsonify({"ok": False, "mensaje": "Sesión no válida."}), 401


def _resumen_resultados(resultado):
    resumen = []
    for grupo in ("inventario", "consolidado"):
        for item in resultado.get(grupo, []):
            resumen.append(
                {
                    "tipo": item.get("tipo"),
                    "id": item.get("id"),
                    "puntaje": item.get("puntaje"),
                    "nivel": item.get("nivel"),
                    "descripcion": item.get("descripcion"),
                    "codigoBarras": item.get("codigoBarras"),
                }
            )
    return resumen


def _limpiar_dimensiones(resultado):
    """Las dimensiones internas son conjuntos: no se envían al navegador."""

    for grupo in ("inventario", "consolidado"):
        for item in resultado.get(grupo, []):
            item.pop("dimensiones", None)
    return resultado


def _cargar_consulta(conn, id_consulta, usuario=None):
    sql = """
        SELECT idConsulta, consultaOriginal, criterios, respuestas, preguntas
        FROM ionConsulta
        WHERE idConsulta = ?
    """
    parametros = [id_consulta]
    if usuario is not None:
        sql += " AND usuario = ?"
        parametros.append(usuario)
    fila = conn.execute(sql, parametros).fetchone()
    if not fila:
        return None
    return {
        "idConsulta": fila[0],
        "consultaOriginal": fila[1] or "",
        "criterios": _json_entrada(fila[2], {}),
        "respuestas": _json_entrada(fila[3], {}),
        "preguntas": _json_entrada(fila[4], []),
    }


def _registrar_pregunta_pendiente(conn, id_consulta, pregunta):
    if not id_consulta or not pregunta:
        return
    campo = pregunta.get("campo")
    existente = conn.execute(
        """
        SELECT idPregunta FROM ionConsultaPregunta
        WHERE idConsulta = ? AND campo = ? AND respuesta IS NULL
        ORDER BY idPregunta DESC LIMIT 1
        """,
        (id_consulta, campo),
    ).fetchone()
    if existente:
        conn.execute(
            "UPDATE ionConsultaPregunta SET pregunta = ? WHERE idPregunta = ?",
            (pregunta.get("pregunta"), existente[0]),
        )
    else:
        conn.execute(
            """
            INSERT INTO ionConsultaPregunta (idConsulta, campo, pregunta)
            VALUES (?, ?, ?)
            """,
            (id_consulta, campo, pregunta.get("pregunta")),
        )


def _registrar_respuesta(conn, id_consulta, campo, pregunta_texto, respuesta):
    if not id_consulta or not campo:
        return
    existente = conn.execute(
        """
        SELECT idPregunta FROM ionConsultaPregunta
        WHERE idConsulta = ? AND campo = ? AND respuesta IS NULL
        ORDER BY idPregunta DESC LIMIT 1
        """,
        (id_consulta, campo),
    ).fetchone()
    if existente:
        conn.execute(
            """
            UPDATE ionConsultaPregunta
            SET pregunta = COALESCE(?, pregunta), respuesta = ?
            WHERE idPregunta = ?
            """,
            (pregunta_texto, respuesta, existente[0]),
        )
    else:
        conn.execute(
            """
            INSERT INTO ionConsultaPregunta (idConsulta, campo, pregunta, respuesta)
            VALUES (?, ?, ?, ?)
            """,
            (id_consulta, campo, pregunta_texto, respuesta),
        )


# ---------------------------------------------------------------------------
# Vistas
# ---------------------------------------------------------------------------
@ion_bp.route("/")
def ion_home():
    if not _autenticado():
        return redirect(url_for("auth.login"))
    return render_template("ion/ion.html", usuario=_usuario())


@ion_bp.route("/conocimiento")
def ion_conocimiento():
    if not _autenticado():
        return redirect(url_for("auth.login"))

    conn = db.conectar()
    try:
        pendientes = conocimiento.listar(conn, "PENDIENTE")
        aprobados = conocimiento.listar(conn, "APROBADO")
        rechazados = conocimiento.listar(conn, "RECHAZADO")
        compatibilidades = conocimiento.listar_compatibilidades(conn, 50)
    finally:
        conn.close()

    return render_template(
        "ion/conocimiento.html",
        usuario=_usuario(),
        pendientes=pendientes,
        aprobados=aprobados,
        rechazados=rechazados,
        compatibilidades=compatibilidades,
    )


# ---------------------------------------------------------------------------
# Búsqueda y acciones
# ---------------------------------------------------------------------------
@ion_bp.route("/buscar", methods=["POST"])
def ion_buscar():
    if not _autenticado():
        return _respuesta_no_autorizada()

    datos = _datos()
    consulta = str(datos.get("consulta") or "").strip()
    criterios = _json_entrada(datos.get("criterios"), {})
    if not isinstance(criterios, dict):
        criterios = {}

    try:
        id_consulta = int(datos.get("idConsulta") or 0) or None
    except (TypeError, ValueError):
        id_consulta = None

    respuesta_campo = str(datos.get("respuestaCampo") or "").strip()
    respuesta_valor = str(datos.get("respuestaValor") or "").strip()
    respuesta_texto = str(datos.get("respuestaTexto") or "").strip()

    conn = db.conectar()
    try:
        anterior = _cargar_consulta(
            conn, id_consulta, _usuario()
        ) if id_consulta else None

        if anterior:
            if not consulta:
                consulta = anterior["consultaOriginal"]
            criterios_previos = anterior["criterios"] or {}
            criterios_previos.update(criterios)
            criterios = criterios_previos
            respuestas = anterior["respuestas"] or {}
            preguntas_previas = anterior["preguntas"] or []
        else:
            id_consulta = None
            respuestas = {}
            preguntas_previas = []

        if not consulta:
            return jsonify(
                {"ok": False, "mensaje": "Describe el repuesto que busca."}
            ), 400

        if respuesta_campo and respuesta_valor:
            valor_canonico = preguntas.normalizar_respuesta(
                respuesta_campo, respuesta_valor
            )
            criterios[respuesta_campo] = valor_canonico
            respuestas[respuesta_campo] = valor_canonico

        resultado = busqueda.buscar(conn, consulta, criterios)
        pregunta_actual = resultado.get("pregunta")

        if respuesta_campo and respuesta_valor:
            _registrar_respuesta(
                conn, id_consulta, respuesta_campo,
                respuesta_texto or None,
                criterios.get(respuesta_campo, respuesta_valor),
            )

        interpretacion_json = json.dumps(
            resultado.get("interpretacion", {}), ensure_ascii=False
        )
        criterios_json = json.dumps(
            resultado.get("interpretacion", {}).get("criterios", criterios),
            ensure_ascii=False, sort_keys=True,
        )

        if pregunta_actual:
            if not any(
                p.get("campo") == pregunta_actual.get("campo")
                for p in preguntas_previas
            ):
                preguntas_previas.append(
                    {
                        "campo": pregunta_actual.get("campo"),
                        "pregunta": pregunta_actual.get("pregunta"),
                    }
                )

        preguntas_json = json.dumps(preguntas_previas, ensure_ascii=False)
        respuestas_json = json.dumps(respuestas, ensure_ascii=False)
        resultados_json = json.dumps(
            _resumen_resultados(resultado), ensure_ascii=False
        )
        sin_resultados = 1 if resultado.get("sinResultados") else 0

        if id_consulta:
            conn.execute(
                """
                UPDATE ionConsulta
                SET consultaOriginal = ?, interpretacion = ?, criterios = ?,
                    respuestas = ?, preguntas = ?, resultados = ?,
                    sinResultados = ?
                WHERE idConsulta = ?
                """,
                (
                    consulta, interpretacion_json, criterios_json,
                    respuestas_json, preguntas_json, resultados_json,
                    sin_resultados, id_consulta,
                ),
            )
        else:
            cursor = conn.execute(
                """
                INSERT INTO ionConsulta (
                    usuario, consultaOriginal, interpretacion, criterios,
                    respuestas, preguntas, resultados, sinResultados
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    _usuario(), consulta, interpretacion_json, criterios_json,
                    respuestas_json, preguntas_json, resultados_json,
                    sin_resultados,
                ),
            )
            id_consulta = cursor.lastrowid

        _registrar_pregunta_pendiente(conn, id_consulta, pregunta_actual)
        conn.commit()

        respuesta = _limpiar_dimensiones(resultado)
        respuesta["ok"] = True
        respuesta["idConsulta"] = id_consulta
        respuesta["usuario"] = _usuario()
        return jsonify(respuesta)
    except Exception:  # pragma: no cover - protección de la vista
        conn.rollback()
        return jsonify(
            {
                "ok": False,
                "mensaje": (
                    "No fue posible completar la búsqueda. "
                    "Intente de nuevo o refine los datos."
                ),
            }
        ), 500
    finally:
        conn.close()


@ion_bp.route("/seleccionar", methods=["POST"])
def ion_seleccionar():
    if not _autenticado():
        return _respuesta_no_autorizada()

    datos = _datos()
    try:
        id_consulta = int(datos.get("idConsulta"))
        id_resultado = int(datos.get("idResultado"))
    except (TypeError, ValueError):
        return jsonify({"ok": False, "mensaje": "Datos incompletos."}), 400

    tipo = str(datos.get("tipoResultado") or "").upper()
    if tipo not in ("INVENTARIO", "CONSOLIDADO"):
        return jsonify({"ok": False, "mensaje": "Tipo no válido."}), 400

    conn = db.conectar()
    try:
        conn.execute(
            """
            UPDATE ionConsulta
            SET idResultadoSeleccionado = ?, tipoResultadoSeleccionado = ?
            WHERE idConsulta = ? AND usuario = ?
            """,
            (id_resultado, tipo, id_consulta, _usuario()),
        )
        conn.commit()
    finally:
        conn.close()

    return jsonify(
        {
            "ok": True,
            "mensaje": "Selección registrada.",
        }
    )


@ion_bp.route("/feedback", methods=["POST"])
def ion_feedback():
    if not _autenticado():
        return _respuesta_no_autorizada()

    datos = _datos()
    try:
        id_consulta = int(datos.get("idConsulta")) if datos.get("idConsulta") else None
    except (TypeError, ValueError):
        id_consulta = None

    tipo = str(datos.get("tipo") or "").upper()
    if tipo not in ("NINGUNO", "INCORRECTO", "CORRECTO"):
        return jsonify({"ok": False, "mensaje": "Tipo no válido."}), 400

    try:
        id_resultado = int(datos.get("idResultado")) if datos.get("idResultado") else None
    except (TypeError, ValueError):
        id_resultado = None

    tipo_resultado = str(datos.get("tipoResultado") or "").upper() or None
    motivo = str(datos.get("motivo") or "").strip()

    conn = db.conectar()
    try:
        conn.execute(
            """
            INSERT INTO ionFeedback (
                idConsulta, tipoResultado, idResultado, tipo, motivo, usuario
            )
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                id_consulta, tipo_resultado, id_resultado, tipo, motivo,
                _usuario(),
            ),
        )
        conn.commit()
    finally:
        conn.close()

    mensajes = {
        "NINGUNO": "Registrado. ION no marcará ninguno de estos resultados como válido.",
        "INCORRECTO": "Gracias. El reporte quedó registrado para revisión.",
        "CORRECTO": "Gracias. La selección quedó confirmada.",
    }
    return jsonify({"ok": True, "mensaje": mensajes[tipo]})


@ion_bp.route("/sinonimo", methods=["POST"])
def ion_sinonimo():
    if not _autenticado():
        return _respuesta_no_autorizada()

    datos = _datos()
    termino = str(datos.get("termino") or "").strip()
    reemplazo = str(datos.get("reemplazo") or "").strip()
    tipo = str(datos.get("tipo") or "SINONIMO").upper()
    try:
        id_consulta = int(datos.get("idConsulta")) if datos.get("idConsulta") else None
    except (TypeError, ValueError):
        id_consulta = None

    conn = db.conectar()
    try:
        resultado = conocimiento.sugerir(
            conn, termino, reemplazo,
            usuario=_usuario(), origen="VENDEDOR",
            id_consulta=id_consulta, tipo=tipo,
        )
        conn.commit()
    finally:
        conn.close()

    return jsonify(resultado), (200 if resultado.get("ok") else 400)


@ion_bp.route("/conocimiento/<int:id_sinonimo>/estado", methods=["POST"])
def ion_cambiar_estado(id_sinonimo):
    if not _autenticado():
        return _respuesta_no_autorizada()

    datos = _datos()
    estado = str(datos.get("estado") or "").upper()

    conn = db.conectar()
    try:
        resultado = conocimiento.cambiar_estado(
            conn, id_sinonimo, estado, usuario=_usuario()
        )
        conn.commit()
    finally:
        conn.close()

    return jsonify(resultado), (200 if resultado.get("ok") else 400)


@ion_bp.route("/compatibilidad", methods=["POST"])
def ion_compatibilidad():
    if not _autenticado():
        return _respuesta_no_autorizada()

    datos = _datos()
    conn = db.conectar()
    try:
        resultado = conocimiento.agregar_compatibilidad(
            conn, datos, usuario=_usuario()
        )
        conn.commit()
    finally:
        conn.close()

    return jsonify(resultado), (200 if resultado.get("ok") else 400)


@ion_bp.route("/reindexar", methods=["POST"])
def ion_reindexar():
    if not _autenticado():
        return _respuesta_no_autorizada()

    conn = db.conectar()
    try:
        disponible = busqueda.asegurar_indice(conn, forzar=True)
        conn.commit()
        inventario, consolidado = busqueda._contar_fuentes(conn)
    finally:
        conn.close()

    return jsonify(
        {
            "ok": True,
            "indice": "fts5" if disponible else "respaldo",
            "inventario": inventario,
            "consolidado": consolidado,
            "fecha": datetime.now().isoformat(timespec="seconds"),
        }
    )
