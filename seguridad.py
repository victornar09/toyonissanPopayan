"""Seguridad mínima para operación en red local.

Incluye:

* secreto de sesión configurable por variable de entorno;
* protección CSRF para métodos que modifican estado;
* guardia que solo permite loopback y direcciones privadas (LAN);
* auditoría de operaciones importantes;
* cifrado del token de correo almacenado en la base de datos.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import ipaddress
import os
import secrets
import sqlite3

from flask import (
    current_app,
    jsonify,
    redirect,
    request,
    session,
    url_for,
)

import db


BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_DIR = os.path.join(BASE_DIR, "config")
RUTA_SECRETO = os.path.join(CONFIG_DIR, ".secret_key")

_PREFIJO_TOKEN_CIFRADO = "enc:v1:"


# ---------------------------------------------------------------------------
# Secreto de sesión
# ---------------------------------------------------------------------------

def _leer_archivo_secreto():
    try:
        with open(RUTA_SECRETO, "r", encoding="utf-8") as archivo:
            valor = archivo.read().strip()
        return valor or None
    except FileNotFoundError:
        return None


def _generar_archivo_secreto():
    os.makedirs(CONFIG_DIR, exist_ok=True)
    valor = secrets.token_hex(32)
    try:
        with open(RUTA_SECRETO, "w", encoding="utf-8") as archivo:
            archivo.write(valor)
    except OSError:
        # Si no se puede persistir, al menos la sesión usa un valor aleatorio.
        pass
    return valor


def cargar_secreto():
    """Obtiene la clave secreta.

    Orden: ``TOYONISSAN_SECRET_KEY`` -> archivo local generado con
    ``secrets.token_hex``.  Nunca se usa una clave fija escrita en el código.
    """

    valor = os.environ.get("TOYONISSAN_SECRET_KEY", "").strip()
    if valor:
        return valor

    valor = _leer_archivo_secreto()
    if valor:
        print(
            "[seguridad] TOYONISSAN_SECRET_KEY no está definida; se usa la "
            "clave persistida en config/.secret_key."
        )
        return valor

    print(
        "[seguridad] TOYONISSAN_SECRET_KEY no está definida; se generó una "
        "clave aleatoria en config/.secret_key. Para producción se recomienda "
        "definir la variable de entorno."
    )
    return _generar_archivo_secreto()


# ---------------------------------------------------------------------------
# CSRF
# ---------------------------------------------------------------------------

def generar_token_csrf():
    token = session.get("_csrf_token")
    if not token:
        token = secrets.token_urlsafe(32)
        session["_csrf_token"] = token
    return token


def _token_recibido():
    token = request.headers.get("X-CSRF-Token") or request.headers.get("X-CSRFToken")
    if token:
        return token
    if request.is_json:
        datos = request.get_json(silent=True) or {}
        return datos.get("csrf_token")
    return request.form.get("csrf_token")


def csrf_valido():
    esperado = session.get("_csrf_token")
    recibido = _token_recibido()
    if not esperado or not recibido:
        return False
    return hmac.compare_digest(str(esperado), str(recibido))


def _csrf_deshabilitado():
    return bool(
        current_app.testing
        or current_app.config.get("CSRF_DESHABILITADO")
    )


def validar_csrf():
    """before_request: valida CSRF en POST/PUT/PATCH/DELETE."""

    if request.method in {"GET", "HEAD", "OPTIONS", "TRACE"}:
        return None

    if request.endpoint == "static" or _csrf_deshabilitado():
        return None

    if csrf_valido():
        return None

    if _peticion_json():
        return jsonify(
            {
                "ok": False,
                "error": (
                    "Token CSRF inválido o vencido. Recargue la página e "
                    "intente de nuevo."
                ),
            }
        ), 400

    return (
        "Token CSRF inválido o vencido. Recargue la página e intente de nuevo.",
        400,
    )


# ---------------------------------------------------------------------------
# Guardia de red local
# ---------------------------------------------------------------------------

def _peticion_json():
    if request.is_json:
        return True
    acepta = request.headers.get("Accept", "")
    return (
        "application/json" in acepta
        and "text/html" not in acepta
        and request.endpoint not in {"auth.login"}
    )


def ip_cliente():
    """IP del cliente. Solo se confía en ``request.remote_addr``.

    No se acepta ``X-Forwarded-For`` porque la aplicación no está detrás de
    un proxy y aceptar esa cabecera permitiría saltarse la guardia de LAN.
    """

    return (request.remote_addr or "").strip()


def ip_es_local_o_privada(valor):
    try:
        direccion = ipaddress.ip_address(valor)
    except ValueError:
        return False
    return (
        direccion.is_loopback
        or direccion.is_private
        or direccion.is_link_local
    )


def _red_publica_permitida():
    return os.environ.get("TOYONISSAN_PERMITIR_RED_PUBLICA", "0") == "1"


def guardia_lan():
    """Rechaza peticiones provenientes de direcciones públicas."""

    if _red_publica_permitida():
        return None

    ip = ip_cliente()

    if ip and ip_es_local_o_privada(ip):
        return None

    mensaje = (
        "Acceso bloqueado: esta aplicación solo está disponible desde la "
        "red local del servidor."
    )

    if _peticion_json():
        return jsonify({"ok": False, "error": mensaje}), 403

    return mensaje, 403


# ---------------------------------------------------------------------------
# Guardia global de autenticación
# ---------------------------------------------------------------------------

_ENDPOINTS_PUBLICOS = {"auth.login", "static"}


def guardia_login():
    """Obliga a iniciar sesión en todas las rutas salvo login y estáticos."""

    if request.endpoint in _ENDPOINTS_PUBLICOS:
        return None

    if session.get("usuario"):
        return None

    if _peticion_json() or request.path.startswith(
        ("/inventario/", "/consolidado/", "/facturas/", "/ion/", "/impresion/")
    ):
        return jsonify({"ok": False, "error": "No autenticado"}), 401

    return redirect(url_for("auth.login", next=request.path))


# ---------------------------------------------------------------------------
# Auditoría
# ---------------------------------------------------------------------------

_CAMPOS_ACCION = ("accion", "action", "tipo")


def _detalle_auditoria():
    accion = ""
    for campo in _CAMPOS_ACCION:
        valor = request.form.get(campo)
        if valor:
            accion = str(valor)[:120]
            break

    if not accion:
        accion = request.endpoint or request.path

    return accion, request.path[:300]


def registrar_auditoria(metodo, ruta, accion, detalle, estado_http):
    """Inserta una fila de auditoría sin interrumpir la petición."""

    try:
        ruta_db = db.ruta_bd_activa()
        if not ruta_db:
            return
        conn = db.conectar(ruta_db)
        try:
            conn.execute(
                """
                INSERT INTO auditoria
                    (fecha, usuario, ip, equipo, metodo, ruta, accion,
                     detalle, estadoHttp)
                VALUES (datetime('now', 'localtime'), ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    session.get("usuario"),
                    ip_cliente(),
                    request.headers.get("X-Equipo", "")[:120],
                    metodo,
                    ruta,
                    accion,
                    detalle,
                    estado_http,
                ),
            )
            conn.commit()
        finally:
            conn.close()
    except Exception as error:  # la auditoría nunca debe romper la petición
        print(f"[auditoria] No se pudo registrar la operación: {error}")


def auditar_after_request(respuesta):
    """after_request: registra POST/PUT/PATCH/DELETE."""

    if request.method not in {"POST", "PUT", "PATCH", "DELETE"}:
        return respuesta

    if request.endpoint == "static":
        return respuesta

    try:
        accion, detalle = _detalle_auditoria()
        registrar_auditoria(
            request.method,
            detalle,
            accion,
            "Operación web",
            respuesta.status_code,
        )
    except Exception as error:
        print(f"[auditoria] Error preparando registro: {error}")

    return respuesta


# ---------------------------------------------------------------------------
# Cabeceras y errores
# ---------------------------------------------------------------------------

def cabeceras_seguridad(respuesta):
    respuesta.headers.setdefault("X-Content-Type-Options", "nosniff")
    respuesta.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
    respuesta.headers.setdefault("Referrer-Policy", "same-origin")
    return respuesta


def manejar_bloqueo_sqlite(error):
    """Convierte un bloqueo de SQLite en un mensaje comprensible."""

    texto = str(error).lower()
    if "database is locked" not in texto and "database table is locked" not in texto:
        return None

    mensaje = (
        "La base de datos está ocupada por otra operación. Espere unos "
        "segundos y vuelva a intentarlo. Si persiste, evite ejecutar varias "
        "escrituras simultáneas."
    )

    if _peticion_json():
        return jsonify({"ok": False, "error": mensaje}), 503

    return mensaje, 503


# ---------------------------------------------------------------------------
# Cifrado del token de correo
# ---------------------------------------------------------------------------

def _clave_fernet():
    secreto = cargar_secreto().encode("utf-8")
    digest = hashlib.sha256(secreto).digest()
    return base64.urlsafe_b64encode(digest)


def cifrar_token(valor):
    """Cifra un token para almacenarlo. Devuelve texto plano si está vacío."""

    if not valor:
        return valor

    texto = str(valor)
    if texto.startswith(_PREFIJO_TOKEN_CIFRADO):
        return texto

    try:
        from cryptography.fernet import Fernet

        cifrado = Fernet(_clave_fernet()).encrypt(texto.encode("utf-8"))
        return _PREFIJO_TOKEN_CIFRADO + cifrado.decode("ascii")
    except Exception as error:
        print(f"[seguridad] No se pudo cifrar el token: {error}")
        return texto


def descifrar_token(valor):
    """Descifra un token. Compatible con valores antiguos en texto plano."""

    if not valor:
        return valor

    texto = str(valor)
    if not texto.startswith(_PREFIJO_TOKEN_CIFRADO):
        return texto

    try:
        from cryptography.fernet import Fernet

        datos = texto[len(_PREFIJO_TOKEN_CIFRADO):].encode("ascii")
        return Fernet(_clave_fernet()).decrypt(datos).decode("utf-8")
    except Exception as error:
        print(f"[seguridad] No se pudo descifrar el token: {error}")
        return ""


# ---------------------------------------------------------------------------
# Integración con Flask
# ---------------------------------------------------------------------------

def basename(valor):
    """Filtro Jinja para no exponer rutas físicas del servidor."""

    if not valor:
        return ""
    return os.path.basename(str(valor).replace("\\", "/"))


def configurar_seguridad(app):
    """Registra guardias, CSRF, auditoría y helpers en la aplicación."""

    app.before_request(guardia_lan)
    app.before_request(guardia_login)
    app.before_request(validar_csrf)

    app.after_request(cabeceras_seguridad)
    app.after_request(auditar_after_request)

    app.jinja_env.globals["csrf_token"] = generar_token_csrf
    app.jinja_env.filters["basename"] = basename

    @app.errorhandler(sqlite3.OperationalError)
    def _error_sqlite(error):
        respuesta = manejar_bloqueo_sqlite(error)
        if respuesta is not None:
            return respuesta
        print(f"[db] Error SQLite: {error}")
        if _peticion_json():
            return jsonify({"ok": False, "error": str(error)}), 500
        return f"Error de base de datos: {error}", 500
