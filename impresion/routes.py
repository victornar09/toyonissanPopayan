"""Rutas de consulta y control de la cola de impresión."""

from __future__ import annotations

from flask import Blueprint, jsonify, request, session

import db
from impresion import servicio


impresion_bp = Blueprint("impresion", __name__, url_prefix="/impresion")


def _ruta_db():
    return db.ruta_bd_activa()


def _respuesta_trabajo(trabajo):
    return {
        "idTrabajo": trabajo.get("idTrabajo"),
        "estado": trabajo.get("estado"),
        "usuario": trabajo.get("usuario"),
        "fecha": trabajo.get("fecha"),
        "fechaFin": trabajo.get("fechaFin"),
        "plantilla": trabajo.get("plantilla"),
        "articulos": len(trabajo.get("articulos") or []),
        "intentos": trabajo.get("intentos"),
        "mensajeError": trabajo.get("mensajeError"),
        "archivoTemporal": trabajo.get("archivoTemporal"),
    }


@impresion_bp.route("/", methods=["GET"])
def cola():
    ruta_db = _ruta_db()
    if not ruta_db:
        return jsonify({"ok": False, "error": "No hay base de datos."}), 400

    try:
        limite = int(request.args.get("limite", 25))
    except ValueError:
        limite = 25

    trabajos = servicio.listar_trabajos(ruta_db, limite=limite)
    return jsonify(
        {
            "ok": True,
            "worker": servicio.worker_activo(),
            "trabajos": [_respuesta_trabajo(t) for t in trabajos],
        }
    )


@impresion_bp.route("/<int:id_trabajo>", methods=["GET"])
def estado_trabajo(id_trabajo):
    ruta_db = _ruta_db()
    if not ruta_db:
        return jsonify({"ok": False, "error": "No hay base de datos."}), 400

    trabajo = servicio.obtener_trabajo(ruta_db, id_trabajo)
    if not trabajo:
        return jsonify({"ok": False, "error": "Trabajo no encontrado."}), 404

    return jsonify({"ok": True, "trabajo": _respuesta_trabajo(trabajo)})


@impresion_bp.route("/<int:id_trabajo>/reintentar", methods=["POST"])
def reintentar(id_trabajo):
    ruta_db = _ruta_db()
    if not ruta_db:
        return jsonify({"ok": False, "error": "No hay base de datos."}), 400

    try:
        trabajo = servicio.reintentar_trabajo(ruta_db, id_trabajo)
    except ValueError as error:
        return jsonify({"ok": False, "error": str(error)}), 400

    return jsonify(
        {
            "ok": True,
            "mensaje": "Trabajo reenviado a la cola.",
            "trabajo": _respuesta_trabajo(trabajo),
        }
    )


@impresion_bp.route("/<int:id_trabajo>/reimprimir", methods=["POST"])
def reimprimir(id_trabajo):
    ruta_db = _ruta_db()
    if not ruta_db:
        return jsonify({"ok": False, "error": "No hay base de datos."}), 400

    try:
        trabajo = servicio.reimprimir_trabajo(
            ruta_db, id_trabajo, usuario=session.get("usuario")
        )
    except ValueError as error:
        return jsonify({"ok": False, "error": str(error)}), 400

    return jsonify(
        {
            "ok": True,
            "mensaje": "Reimpresión agregada a la cola.",
            "trabajo": _respuesta_trabajo(trabajo),
        }
    )
