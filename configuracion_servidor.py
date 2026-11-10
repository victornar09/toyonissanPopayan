"""Configuración persistente del servidor y utilidades de red local.

La configuración estable (nombre, host, puerto e hilos) se guarda en
``config/servidor.json``.  La IP detectada y el estado de ejecución son
datos transitorios y **nunca** se escriben en ese archivo.

Este módulo no importa Flask ni arranca nada: solo lee/escribe configuración
y expone helpers que usan tanto la interfaz web como ``run_server.py``.
"""

from __future__ import annotations

import ipaddress
import json
import os
import re
import socket
import tempfile
import time
import urllib.error
import urllib.request


BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_DIR = os.path.join(BASE_DIR, "config")
RUTA_CONFIG = os.path.join(CONFIG_DIR, "servidor.json")
RUTA_ESTADO = os.path.join(CONFIG_DIR, "estado_servidor.json")

HOST_RECOMENDADO = "0.0.0.0"
PUERTO_POR_DEFECTO = 5000
HILOS_POR_DEFECTO = 6

_RE_HOSTNAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9.\-]{0,253}[A-Za-z0-9]$")


def _config_por_defecto():
    return {
        "nombre_servidor": socket.gethostname() or "Toyonissan",
        "host": HOST_RECOMENDADO,
        "puerto": PUERTO_POR_DEFECTO,
        "hilos": HILOS_POR_DEFECTO,
        "debug": False,
    }


def _normalizar_entero(valor, defecto):
    try:
        return int(str(valor).strip())
    except (TypeError, ValueError):
        return defecto


def _es_ip_publica(valor):
    try:
        direccion = ipaddress.ip_address(str(valor).strip())
    except ValueError:
        return False
    return not (
        direccion.is_private
        or direccion.is_loopback
        or direccion.is_link_local
        or direccion.is_unspecified
    )


def _resolver_host(host):
    """Devuelve las IPs asociadas a un nombre de host (sin lanzar error)."""

    try:
        infos = socket.getaddrinfo(host, None)
    except OSError:
        return []

    return sorted({info[4][0] for info in infos})


def validar_configuracion(datos):
    """Valida y normaliza la configuración recibida.

    Devuelve ``(configuracion, errores)``.  ``errores`` es una lista de
    mensajes legibles; si está vacía la configuración es segura.
    """

    datos = datos or {}
    errores = []

    nombre = str(datos.get("nombre_servidor", "") or "").strip()
    if not nombre:
        nombre = socket.gethostname() or "Toyonissan"
    if len(nombre) > 120:
        errores.append("El nombre del servidor no puede superar 120 caracteres.")
        nombre = nombre[:120]

    host = str(datos.get("host", "") or "").strip()
    if not host:
        errores.append("El host de escucha es obligatorio.")

    host_valido = False
    if host:
        if host in {"0.0.0.0", "::"}:
            host_valido = True
        elif host.lower() in {"localhost", "127.0.0.1", "::1"}:
            host_valido = True
        else:
            try:
                direccion = ipaddress.ip_address(host)
            except ValueError:
                direccion = None

            if direccion is not None:
                if direccion.is_loopback or direccion.is_private or direccion.is_link_local:
                    host_valido = True
                else:
                    errores.append(
                        "El host no puede ser una IP pública. Use 0.0.0.0 "
                        "para escuchar en la red local."
                    )
            elif _RE_HOSTNAME.match(host):
                # Un nombre de equipo solo es aceptable si no resuelve a Internet.
                ips = _resolver_host(host)
                if any(_es_ip_publica(ip) for ip in ips):
                    errores.append(
                        "El nombre de host resuelve a una dirección pública. "
                        "Use 0.0.0.0 o una IP privada."
                    )
                else:
                    host_valido = True
            else:
                errores.append("El host de escucha no es válido.")

    if host and not host_valido and not errores:
        errores.append("El host de escucha no es válido.")

    puerto = _normalizar_entero(datos.get("puerto"), 0)
    if puerto < 1024 or puerto > 65535:
        errores.append("El puerto debe estar entre 1024 y 65535.")

    hilos = _normalizar_entero(datos.get("hilos"), HILOS_POR_DEFECTO)
    if hilos < 4 or hilos > 8:
        errores.append(
            "Los hilos deben estar entre 4 y 8 para este servidor local."
        )

    # ``debug`` nunca se habilita desde la interfaz.
    configuracion = {
        "nombre_servidor": nombre,
        "host": host,
        "puerto": puerto,
        "hilos": hilos,
        "debug": False,
    }

    return configuracion, errores


def cargar_configuracion():
    """Lee la configuración estable. Si no existe o es inválida, usa valores
    seguros por defecto (sin escribir nada)."""

    defecto = _config_por_defecto()

    try:
        with open(RUTA_CONFIG, "r", encoding="utf-8") as archivo:
            datos = json.load(archivo)
    except (FileNotFoundError, ValueError, OSError):
        return defecto

    configuracion, errores = validar_configuracion({**defecto, **datos})
    if errores:
        # No se pisa el archivo: se opera con valores seguros y se avisa en logs.
        print(
            "[configuracion_servidor] Configuración inválida, usando valores "
            f"seguros: {'; '.join(errores)}"
        )
        return defecto

    return configuracion


def guardar_configuracion(datos):
    """Valida y guarda la configuración de forma atómica.

    Devuelve ``(configuracion, errores)``.  No guarda si hay errores.
    """

    configuracion, errores = validar_configuracion(datos)
    if errores:
        return configuracion, errores

    os.makedirs(CONFIG_DIR, exist_ok=True)

    descriptor, ruta_temp = tempfile.mkstemp(
        prefix="servidor_", suffix=".json", dir=CONFIG_DIR
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as archivo:
            json.dump(configuracion, archivo, ensure_ascii=False, indent=2)
            archivo.write("\n")
        os.replace(ruta_temp, RUTA_CONFIG)
    finally:
        if os.path.exists(ruta_temp):
            try:
                os.remove(ruta_temp)
            except OSError:
                pass

    return configuracion, []


def detectar_ip_local():
    """Detecta la IPv4 privada de salida del servidor (solo lectura).

    No envía tráfico: usa un socket UDP ``connect`` que solo resuelve la ruta.
    """

    candidatos = []

    for destino in ("8.8.8.8", "1.1.1.1"):
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
                sock.settimeout(0.3)
                sock.connect((destino, 80))
                candidatos.append(sock.getsockname()[0])
        except OSError:
            continue

    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            candidatos.append(info[4][0])
    except OSError:
        pass

    for ip in candidatos:
        try:
            direccion = ipaddress.ip_address(ip)
        except ValueError:
            continue
        if direccion.is_loopback:
            continue
        if direccion.is_private:
            return ip

    return candidatos[0] if candidatos else None


def url_clientes(configuracion=None, ip_local=None):
    """URL sugerida para que otros equipos de la LAN abran la aplicación."""

    configuracion = configuracion or cargar_configuracion()
    puerto = configuracion.get("puerto", PUERTO_POR_DEFECTO)
    host = configuracion.get("host", HOST_RECOMENDADO)

    if host in {"127.0.0.1", "localhost", "::1"}:
        return f"http://127.0.0.1:{puerto}"

    ip = ip_local or detectar_ip_local()
    if ip:
        return f"http://{ip}:{puerto}"

    return f"http://localhost:{puerto}"


def url_local(configuracion=None):
    configuracion = configuracion or cargar_configuracion()
    return f"http://127.0.0.1:{configuracion.get('puerto', PUERTO_POR_DEFECTO)}"


def puerto_abierto(host="127.0.0.1", puerto=PUERTO_POR_DEFECTO, timeout=0.35):
    try:
        with socket.create_connection((host, int(puerto)), timeout=timeout):
            return True
    except OSError:
        return False


def escribir_estado_servidor(configuracion, pid=None):
    """Registra datos transitorios de ejecución (no es configuración)."""

    os.makedirs(CONFIG_DIR, exist_ok=True)
    estado = {
        "pid": pid or os.getpid(),
        "nombre_servidor": configuracion.get("nombre_servidor"),
        "host": configuracion.get("host"),
        "puerto": configuracion.get("puerto"),
        "hilos": configuracion.get("hilos"),
        "url_local": url_local(configuracion),
        "iniciado": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    try:
        with open(RUTA_ESTADO, "w", encoding="utf-8") as archivo:
            json.dump(estado, archivo, ensure_ascii=False, indent=2)
    except OSError:
        pass
    return estado


def eliminar_estado_servidor():
    try:
        os.remove(RUTA_ESTADO)
    except OSError:
        pass


def estado_servidor():
    """Estado mostrado en la interfaz (incluye comprobación de puerto)."""

    configuracion = cargar_configuracion()
    estado = {
        "en_ejecucion": False,
        "url_local": url_local(configuracion),
        "iniciado": None,
        "pid": None,
        "mensaje": "Detenido",
    }

    try:
        with open(RUTA_ESTADO, "r", encoding="utf-8") as archivo:
            datos = json.load(archivo)
        estado.update(
            {
                "iniciado": datos.get("iniciado"),
                "pid": datos.get("pid"),
                "url_local": datos.get("url_local") or estado["url_local"],
            }
        )
    except (FileNotFoundError, ValueError, OSError):
        pass

    if puerto_abierto("127.0.0.1", configuracion.get("puerto", PUERTO_POR_DEFECTO)):
        estado["en_ejecucion"] = True
        estado["mensaje"] = "En ejecución"

    return estado


def probar_acceso_local(configuracion=None, timeout=4):
    """Realiza una petición HTTP local a la aplicación.

    Devuelve un diccionario ``{ok, mensaje, codigo, url}``.  Acepta cualquier
    respuesta HTTP (incluido el redirect al login) que no sea 5xx.
    """

    configuracion = configuracion or cargar_configuracion()
    url = url_local(configuracion) + "/login"

    # Evitar que un proxy del sistema intercepte la petición local.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    solicitud = urllib.request.Request(url, headers={"User-Agent": "Toyonissan-SelfTest"})

    try:
        with opener.open(solicitud, timeout=timeout) as respuesta:
            codigo = respuesta.status
    except urllib.error.HTTPError as error:
        codigo = error.code
    except (urllib.error.URLError, OSError) as error:
        return {
            "ok": False,
            "codigo": None,
            "url": url,
            "mensaje": (
                "No se pudo conectar con el servidor local. Verifique que el "
                f"servicio esté iniciado en el puerto {configuracion.get('puerto')}. "
                f"Detalle: {error}"
            ),
        }

    if codigo >= 500:
        return {
            "ok": False,
            "codigo": codigo,
            "url": url,
            "mensaje": f"El servidor respondió con error HTTP {codigo}.",
        }

    return {
        "ok": True,
        "codigo": codigo,
        "url": url,
        "mensaje": f"Acceso local correcto (HTTP {codigo}).",
    }


def host_escucha(configuracion=None):
    """Host efectivo para Waitress."""

    configuracion = configuracion or cargar_configuracion()
    host = configuracion.get("host") or HOST_RECOMENDADO
    if host == "localhost":
        return "127.0.0.1"
    return host
