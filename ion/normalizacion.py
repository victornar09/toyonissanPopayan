"""Normalización, tokenización e interpretación de consultas de ION.

El objetivo es transformar lo que escribe un vendedor en un conjunto de
criterios y variantes de búsqueda, sin depender de coincidencias literales.
No inventa datos: sólo reconoce términos que están en el lexicón o en el
conocimiento aprobado de la base de datos.
"""

from __future__ import annotations

import re
import unicodedata

from . import lexico


_RE_NO_ALFANUMERICO = re.compile(r"[^A-Z0-9]+")
_RE_ESPACIOS = re.compile(r"\s+")
_RE_ANIO = re.compile(r"\b(19[89]\d|20[0-3]\d)\b")
_RE_DECIMAL = re.compile(r"\b([1-9](?:[.,]\d))\b")
_RE_MOTOR_CODIGO = re.compile(
    r"\b([1-9][A-Z]{1,2}[0-9]{1,3}[A-Z]?|[1-9][A-Z][0-9]?[A-Z]?)\b"
)
_RE_MEDIDA = re.compile(
    r"\b(\d+(?:[.,]\d+)?\s*(?:MM|CM|MILIMETROS|CENTIMETROS|PULG|PULGADAS|''|\"))"
    r"|\b(\d{2,3}\s*[X*]\s*\d{2,3})\b"
)


def normalizar(texto):
    """Mayúsculas, sin tildes, sin signos y con espacios colapsados."""

    if texto is None:
        return ""

    texto = unicodedata.normalize("NFD", str(texto).upper())
    texto = "".join(
        caracter
        for caracter in texto
        if unicodedata.category(caracter) != "Mn"
    )
    texto = _RE_NO_ALFANUMERICO.sub(" ", texto)
    return _RE_ESPACIOS.sub(" ", texto).strip()


def tokenizar(texto):
    normalizado = normalizar(texto)
    return [token for token in normalizado.split(" ") if token]


def _quitar_plural(token):
    """Candidatos de singular para un token en español.

    Devuelve varias posibilidades porque las reglas no son perfectas
    (``bujes`` -> ``buje``, ``retenes`` -> ``reten``). Incluir de más es
    inofensivo: se usa para comparar, no para mostrar.
    """

    candidatos = {token}

    if len(token) > 3 and token.endswith("S"):
        candidatos.add(token[:-1])
        if len(token) > 4 and token.endswith("ES"):
            candidatos.add(token[:-2])

        if token.endswith("CES") and len(token) > 4:
            candidatos.add(token[:-3] + "Z")
        if token.endswith("NES") and len(token) > 4:
            candidatos.add(token[:-2])

    return candidatos


def _plural(token):
    if not token or not token.isalpha():
        return token
    if token.endswith("Z"):
        return token[:-1] + "CES"
    if token.endswith(("A", "E", "I", "O", "U")):
        return token + "S"
    return token + "ES"


def variantes_token(token):
    """Variantes morfológicas de un token (singular/plural)."""

    variantes = set(_quitar_plural(token))
    for candidato in list(variantes):
        variantes.add(_plural(candidato))
    variantes.discard("")
    return variantes


def _aplicar_frases(texto):
    """Reemplaza expresiones completas por su término canónico."""

    resultado = texto
    frase_detectada = ""
    for frase in sorted(lexico.FRASES, key=len, reverse=True):
        if frase in resultado:
            reemplazo = lexico.FRASES[frase]
            resultado = resultado.replace(frase, f" {reemplazo} ")
            if not frase_detectada:
                frase_detectada = reemplazo
    resultado = _RE_ESPACIOS.sub(" ", resultado).strip()
    return resultado, frase_detectada


def _aplicar_intencion(texto):
    resultado = texto
    for frase in sorted(lexico.FRASES_INTENCION, key=len, reverse=True):
        resultado = resultado.replace(frase, " ")
    return _RE_ESPACIOS.sub(" ", resultado).strip()


def _mapa_sinonimos(sinonimos_extra=None):
    mapa = dict(lexico.SINONIMOS)
    if sinonimos_extra:
        for termino, reemplazo in sinonimos_extra.items():
            termino = normalizar(termino)
            reemplazo = normalizar(reemplazo)
            if termino and reemplazo:
                mapa[termino] = reemplazo
    return mapa


def _expandir_token(token, mapa):
    canonico = mapa.get(token, token)
    variantes = set()
    for base in (token, canonico):
        variantes |= variantes_token(base)

    # Incluir todas las variantes conocidas que apunten al mismo canónico.
    for termino, destino in mapa.items():
        if destino == canonico:
            variantes |= variantes_token(termino)

    return canonico, variantes


def _detectar_por_valores(normalizado):
    """Detecta dimensiones cuyo valor aparece literalmente en el texto."""

    encontrados = {}
    relleno = f" {normalizado} "
    for campo, valores in lexico.VALORES_DIMENSION.items():
        for canonico, variantes in valores.items():
            for variante in variantes:
                if f" {normalizar(variante)} " in relleno:
                    encontrados[campo] = canonico
                    break
            if campo in encontrados:
                break
    return encontrados


def _extraer_motor(original):
    texto = normalizar(original)

    decimal = _RE_DECIMAL.search(str(original or ""))
    if decimal:
        return decimal.group(1).replace(",", ".")

    # Códigos de motor habituales (4D34, 4JH1, 2F, B6, 1HZ, KZ...).
    for token in texto.split(" "):
        if not token.isalnum() or len(token) < 2:
            continue
        if token[0].isdigit() and any(c.isalpha() for c in token):
            return token
    return ""


def _extraer_medida(original):
    if not original:
        return ""
    coincidencia = _RE_MEDIDA.search(str(original).upper())
    if not coincidencia:
        return ""
    valor = coincidencia.group(0)
    return _RE_ESPACIOS.sub("", valor).replace("*", "X")


def _detectar_marca_modelo(normalizado):
    tokens = normalizado.split(" ")
    criterios = {}

    for token in tokens:
        if token in lexico.MARCAS:
            criterios["marca"] = token
            break

    for modelo in sorted(lexico.MODELOS, key=len, reverse=True):
        modelo_normalizado = normalizar(modelo)
        if f" {modelo_normalizado} " in f" {normalizado} ":
            criterios["modelo"] = modelo_normalizado
            break

    return criterios


def detectar_criterios(consulta):
    """Extrae los datos técnicos que el vendedor ya expresó."""

    original = str(consulta or "")
    normalizado = _aplicar_intencion(_aplicar_frases(normalizar(original))[0])
    criterios = _detectar_por_valores(normalizado)
    criterios.update(_detectar_marca_modelo(normalizado))

    anio = _RE_ANIO.search(normalizado)
    if anio:
        criterios["anio"] = anio.group(1)

    motor = _extraer_motor(original)
    if motor:
        criterios["motor"] = motor

    medida = _extraer_medida(original)
    if medida:
        criterios["medida"] = medida

    return criterios


def detectar_dimensiones(texto):
    """Datos técnicos detectables en la descripción de un artículo.

    Devuelve ``{campo: conjunto_de_valores}``. Se usa para decidir qué
    pregunta permite diferenciar candidatos y para comparar criterios.
    """

    normalizado = normalizar(texto)
    relleno = f" {normalizado} "
    encontrados = {}

    for campo, valores in lexico.VALORES_DIMENSION.items():
        for canonico, variantes in valores.items():
            if any(
                f" {normalizar(variante)} " in relleno
                for variante in variantes
            ):
                encontrados.setdefault(campo, set()).add(canonico)

    anios = set(_RE_ANIO.findall(normalizado))
    if anios:
        encontrados["anio"] = anios

    motor = _extraer_motor(texto)
    if motor:
        encontrados.setdefault("motor", set()).add(motor)

    medida = _extraer_medida(texto)
    if medida:
        encontrados.setdefault("medida", set()).add(medida)

    return encontrados


def interpretar(consulta, sinonimos_extra=None):
    """Interpreta una consulta libre.

    Devuelve un diccionario con el texto normalizado, los criterios técnicos
    detectados, el nombre interpretado del repuesto y los grupos de variantes
    que se usarán para puntuar los artículos.
    """

    original = str(consulta or "").strip()
    normalizada = normalizar(original)
    con_frases, frase = _aplicar_frases(normalizada)
    con_intencion = _aplicar_intencion(con_frases)

    tokens = [
        token
        for token in con_intencion.split(" ")
        if token and token not in lexico.STOPWORDS and len(token) >= 2
    ]
    if not tokens:
        tokens = [
            token
            for token in con_intencion.split(" ")
            if token and len(token) >= 2
        ]

    mapa = _mapa_sinonimos(sinonimos_extra)
    grupos = []
    principales = []
    canonicos = []
    terminos = []
    vistos = set()

    for token in tokens:
        canonico, variantes = _expandir_token(token, mapa)
        if not variantes:
            continue
        terminos.append(canonico)
        if canonico not in vistos:
            grupos.append(sorted(variantes))
            principales.append(sorted(variantes_token(token)))
            canonicos.append(sorted(variantes_token(canonico)))
            vistos.add(canonico)

    criterios = detectar_criterios(original)
    criterios.update(_detectar_marca_modelo(con_intencion))

    # Distinguir las palabras que describen el repuesto de las que describen
    # el vehículo o una condición técnica. El repuesto debe coincidir para
    # que un resultado sea válido.
    variantes_criterio = set()
    for campo, valor in criterios.items():
        valor_normalizado = normalizar(valor)
        variantes_criterio |= variantes_token(valor_normalizado)
        for canonico, variantes in lexico.VALORES_DIMENSION.get(campo, {}).items():
            if (
                valor_normalizado == normalizar(canonico)
                or valor_normalizado in {
                    normalizar(variante) for variante in variantes
                }
            ):
                for variante in variantes:
                    variantes_criterio |= variantes_token(
                        normalizar(variante)
                    )
                variantes_criterio |= variantes_token(normalizar(canonico))

    indices_repuesto = []
    for indice, termino in enumerate(terminos):
        if not (variantes_token(termino) & variantes_criterio):
            indices_repuesto.append(indice)

    if frase:
        repuesto = frase
    elif terminos:
        # El canónico conocido más específico; si no hay, las palabras útiles.
        candidatos = [termino for termino in terminos if termino in mapa.values()]
        repuesto = max(candidatos, key=len) if candidatos else " ".join(terminos[:4])
    else:
        repuesto = " ".join(tokens[:4]) or normalizada

    return {
        "original": original,
        "normalizada": normalizada,
        "tokens": tokens,
        "terminos": terminos,
        "grupos": grupos,
        "principales": principales,
        "canonicos": canonicos,
        "indicesRepuesto": indices_repuesto,
        "criterios": criterios,
        "repuesto": repuesto.upper(),
        "frase": frase,
    }


def combinar_criterios(interpretacion, criterios_extra=None):
    """Une los criterios detectados con los respondidos en preguntas."""

    criterios = dict(interpretacion.get("criterios") or {})
    for campo, valor in (criterios_extra or {}).items():
        if valor is None:
            continue
        valor = str(valor).strip()
        if valor:
            criterios[campo] = normalizar(valor)
    interpretacion["criterios"] = criterios
    return interpretacion
