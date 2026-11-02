"""Utilidades compartidas por los distintos módulos de la aplicación.

Centraliza funciones que antes estaban duplicadas en `facturas/routes.py`
y `inventario/routes.py`.
"""

import re
import unicodedata


_MAPA_CHAPINEROS = {
    "1": "c", "2": "h", "3": "a", "4": "p",
    "5": "i", "6": "n", "7": "e", "8": "r", "9": "o", "0": "s",
}


def normalizar_clave(texto):
    """Genera la clave normalizada de un texto.

    Se usa como identificador lógico para evitar duplicados por
    diferencias de mayúsculas, tildes, espacios o signos de puntuación.

        'Bomba Ppal. Clutch'  -> 'BOMBA PPAL CLUTCH'
        'BÓMBA  PPAL - CLUTCH' -> 'BOMBA PPAL CLUTCH'

    Devuelve ``None`` cuando el texto queda vacío.
    """

    if texto is None:
        return None

    texto = str(texto).strip().upper()

    if not texto:
        return None

    texto = unicodedata.normalize("NFD", texto)
    texto = "".join(
        caracter
        for caracter in texto
        if unicodedata.category(caracter) != "Mn"
    )

    texto = re.sub(r"[^A-Z0-9]+", " ", texto)
    texto = re.sub(r"\s+", " ", texto).strip()

    return texto or None


def cifrar_precio(precio):
    """Ofusca un precio usando el mapa de "chapineros".

    El objetivo es que el cliente no lea el precio directamente en la
    etiqueta. Acepta enteros o cadenas con separadores de miles.
    """

    if precio is None:
        return ""

    if isinstance(precio, (int, float)):
        digitos = str(int(precio))
    else:
        digitos = re.sub(r"\D", "", str(precio))

    if not digitos:
        return ""

    return "".join(
        _MAPA_CHAPINEROS.get(digito, digito)
        for digito in digitos
    ).upper()


def a_entero(valor, defecto=0):
    """Convierte un valor con separadores de miles a entero."""

    if valor is None:
        return defecto

    if isinstance(valor, (int, float)):
        return int(round(valor))

    digitos = re.sub(r"\D", "", str(valor))

    return int(digitos) if digitos else defecto
