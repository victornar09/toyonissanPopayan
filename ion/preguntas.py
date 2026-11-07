"""Selección de la pregunta mínima necesaria para desambiguar resultados.

ION no hace un cuestionario: elige la única dimensión técnica que realmente
diferencia a los candidatos y que el vendedor todavía no ha indicado.
"""

from __future__ import annotations

from . import lexico
from .normalizacion import normalizar


ETIQUETAS = {
    "lado": {
        "IZQUIERDO": "Izquierdo",
        "DERECHO": "Derecho",
    },
    "posicion": {
        "DELANTERO": "Delantero",
        "TRASERO": "Trasero",
    },
    "cabina": {
        "SENCILLA": "Cabina sencilla",
        "DOBLE": "Doble cabina",
    },
    "traccion": {
        "4X2": "4x2",
        "4X4": "4x4",
    },
    "transmision": {
        "MECANICA": "Mecánica",
        "AUTOMATICA": "Automática",
    },
    "combustible": {
        "GASOLINA": "Gasolina",
        "DIESEL": "Diésel",
    },
}


def _etiqueta(campo, valor):
    return ETIQUETAS.get(campo, {}).get(valor, str(valor).title())


def elegir(candidatos, criterios):
    """Devuelve ``None`` o una pregunta con opciones.

    ``candidatos`` son los resultados ya puntuados; ``criterios`` son los
    datos que el vendedor ya expresó (no se vuelven a preguntar).
    """

    if not candidatos or len(candidatos) < 2:
        return None

    criterios = criterios or {}
    puntajes = [item.get("puntaje", 0) for item in candidatos]
    maximo = max(puntajes)
    umbral = max(0.60, maximo - 0.18)
    relevantes = [
        item for item in candidatos if item.get("puntaje", 0) >= umbral
    ]
    if len(relevantes) < 2:
        return None

    for campo in lexico.ORDEN_DIMENSIONES:
        if campo in criterios:
            continue

        conteo = {}
        for item in relevantes:
            for valor in item.get("dimensiones", {}).get(campo, set()):
                if valor:
                    conteo[valor] = conteo.get(valor, 0) + 1

        if len(conteo) < 2:
            continue

        opciones = sorted(
            conteo.items(),
            key=lambda par: (-par[1], str(par[0])),
        )[:6]

        return {
            "campo": campo,
            "pregunta": lexico.PREGUNTAS.get(
                campo, "¿Puede precisar %s?" % campo
            ),
            "opciones": [
                {
                    "valor": valor,
                    "etiqueta": _etiqueta(campo, valor),
                    "cantidad": cantidad,
                }
                for valor, cantidad in opciones
            ],
        }

    return None


def normalizar_respuesta(campo, valor):
    """Lleva la respuesta del vendedor al valor canónico de la dimensión."""

    valor = normalizar(valor)
    if not valor:
        return ""

    valores = lexico.VALORES_DIMENSION.get(campo)
    if not valores:
        return valor

    for canonico, variantes in valores.items():
        for variante in variantes:
            if valor == normalizar(variante):
                return canonico
    return valor
