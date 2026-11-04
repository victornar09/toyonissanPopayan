"""Lectura de importes en XML UBL (facturas electrónicas colombianas).

Reglas:
- Funciona con `<Invoice>` directo y con `<AttachedDocument>` que lleva la
  factura embebida como texto dentro de un `cbc:Description`.
- No depende de prefijos de namespace: compara por nombre local del tag.
- Usa ``Decimal`` para evitar pérdida de precisión.
- Reconoce separadores de miles/decimales, espacios y símbolos de moneda.

Ejemplo de uso::

    from facturas.xml_utils import extraer_total_xml, formatear_moneda

    resultado = extraer_total_xml(texto_xml)
    if resultado["total"] is not None:
        print(formatear_moneda(resultado["total"]))
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, List, Optional

# Caracteres que se descartan antes de interpretar el número (moneda, espacios
# duros, comillas, letras sueltas, etc.), conservando dígitos y separadores.
_LIMPIEZA_RE = re.compile(r"[^0-9,.\-+]")

# Campos de importe por orden de prioridad. El primero es el total oficial.
_CAMPOS_TOTAL = (
    "PayableAmount",
    "TaxInclusiveAmount",
    "LineExtensionAmount",
)


def nombre_local(tag: str) -> str:
    """Devuelve el nombre del tag sin namespace ni prefijo."""
    if not isinstance(tag, str):
        return ""
    return tag.rsplit("}", 1)[-1].split(":")[-1]


def _quitar_ceros_decimales(texto: str) -> str:
    if "." in texto:
        entero, decimales = texto.split(".", 1)
        decimales = decimales.rstrip("0")
        return f"{entero}.{decimales}" if decimales else entero
    return texto


def parsear_importe(valor: Any) -> Optional[Decimal]:
    """Convierte un texto/numero de importe a ``Decimal``.

    Acepta ``"11405735.88"``, ``"$ 11.405.735,88"``, ``"1,234,567.89"``,
    ``"258.498,70"``, ``"1.234"`` (miles), etc. Devuelve ``None`` si no hay
    un número interpretable.
    """

    if valor is None:
        return None

    if isinstance(valor, Decimal):
        return valor

    if isinstance(valor, bool):
        return None

    if isinstance(valor, int):
        return Decimal(valor)

    if isinstance(valor, float):
        try:
            return Decimal(str(valor))
        except InvalidOperation:
            return None

    texto = str(valor).strip()
    if not texto:
        return None

    texto = texto.replace("\u00a0", " ").replace("\u202f", " ")
    texto = _LIMPIEZA_RE.sub("", texto)

    if not texto or texto in {"-", "+", ".", ","}:
        return None

    negativo = texto.startswith("-")
    texto = texto.lstrip("+-")

    tiene_punto = "." in texto
    tiene_coma = "," in texto

    if tiene_punto and tiene_coma:
        # El separador que aparece más a la derecha es el decimal.
        if texto.rfind(",") > texto.rfind("."):
            texto = texto.replace(".", "").replace(",", ".")
        else:
            texto = texto.replace(",", "")
    elif tiene_coma:
        partes = texto.split(",")
        if len(partes) > 2:
            # Varias comas => separador de miles.
            texto = "".join(partes)
        else:
            entero, decimales = partes
            if len(decimales) == 3 and entero not in {"", "0"}:
                # "1,234" se interpreta como miles (formato en-US).
                texto = entero + decimales
            else:
                texto = f"{entero}.{decimales}"
    elif tiene_punto:
        partes = texto.split(".")
        if len(partes) > 2:
            # Varios puntos => separador de miles ("1.234.567").
            texto = "".join(partes)
        else:
            entero, decimales = partes
            if len(decimales) == 3 and entero not in {"", "0"}:
                # "1.234" se interpreta como miles (formato colombiano).
                texto = entero + decimales
            else:
                texto = f"{entero}.{decimales}"

    texto = _quitar_ceros_decimales(texto)

    try:
        numero = Decimal(texto)
    except (InvalidOperation, ValueError):
        return None

    return -numero if negativo else numero


def formatear_moneda(valor: Any) -> str:
    """Formatea un importe como moneda colombiana: ``$ 1.234.567``."""

    numero = parsear_importe(valor)
    if numero is None:
        return "$ 0"
    redondeado = numero.quantize(Decimal("1"))
    texto = f"{redondeado:,.0f}".replace(",", ".")
    return f"$ {texto}"


def _hijos_por_nombre(elemento: ET.Element, nombre: str) -> List[ET.Element]:
    return [hijo for hijo in list(elemento) if nombre_local(hijo.tag) == nombre]


def _descendientes_por_nombre(elemento: ET.Element, nombre: str) -> List[ET.Element]:
    return [nodo for nodo in elemento.iter() if nombre_local(nodo.tag) == nombre]


def _texto(elemento: Optional[ET.Element]) -> str:
    if elemento is None or elemento.text is None:
        return ""
    return elemento.text.strip()


def _extraer_de_invoice(raiz: ET.Element) -> Dict[str, Any]:
    """Extrae el total de un `<Invoice>`/`<CreditNote>` UBL."""

    advertencias: List[str] = []

    # 1. Prioridad: cac:LegalMonetaryTotal/cbc:PayableAmount
    for total in _descendientes_por_nombre(raiz, "LegalMonetaryTotal"):
        for campo in _CAMPOS_TOTAL:
            for hijo in _hijos_por_nombre(total, campo):
                numero = parsear_importe(_texto(hijo))
                if numero is not None:
                    return {
                        "total": numero,
                        "origen": f"LegalMonetaryTotal/{campo}",
                        "texto_original": _texto(hijo),
                        "advertencias": advertencias,
                    }
                advertencias.append(
                    f"Valor no convertible en LegalMonetaryTotal/{campo}: {_texto(hijo)!r}"
                )

    # 2. Respaldo: cualquier PayableAmount / TaxInclusiveAmount / LineExtensionAmount
    for campo in _CAMPOS_TOTAL:
        for nodo in _descendientes_por_nombre(raiz, campo):
            numero = parsear_importe(_texto(nodo))
            if numero is not None:
                advertencias.append(
                    f"Total obtenido por respaldo ({campo}); revise LegalMonetaryTotal."
                )
                return {
                    "total": numero,
                    "origen": campo,
                    "texto_original": _texto(nodo),
                    "advertencias": advertencias,
                }

    advertencias.append("El XML no contiene un importe total válido.")
    return {
        "total": None,
        "origen": None,
        "texto_original": None,
        "advertencias": advertencias,
    }


def _invoice_embebida(raiz: ET.Element) -> Optional[ET.Element]:
    """Devuelve la factura embebida en un AttachedDocument, si existe."""

    for nodo in raiz.iter():
        if nombre_local(nodo.tag) != "Description":
            continue
        texto = (nodo.text or "").strip()
        if not texto.startswith("<"):
            continue
        try:
            candidato = ET.fromstring(texto)
        except ET.ParseError:
            continue
        if nombre_local(candidato.tag) in {"Invoice", "CreditNote"}:
            return candidato

    # Algunos AttachedDocument anidan la factura como XML hijo real.
    for nodo in raiz.iter():
        if nombre_local(nodo.tag) in {"Invoice", "CreditNote"}:
            return nodo

    return None


def obtener_invoice_root(raiz: ET.Element) -> Optional[ET.Element]:
    """Devuelve el `<Invoice>`/`<CreditNote>` de un XML UBL ya parseado.

    Acepta una factura directa o un ``AttachedDocument`` con la factura
    embebida como texto en un ``cbc:Description``.
    """

    if nombre_local(raiz.tag) in {"Invoice", "CreditNote"}:
        return raiz
    return _invoice_embebida(raiz)


def extraer_total_desde_elemento(raiz: ET.Element) -> Dict[str, Any]:
    """Extrae el total desde un elemento ya parseado."""

    tipo_raiz = nombre_local(raiz.tag)

    if tipo_raiz == "AttachedDocument":
        embebida = _invoice_embebida(raiz)
        if embebida is None:
            return {
                "total": None,
                "origen": None,
                "texto_original": None,
                "advertencias": [
                    "AttachedDocument sin factura embebida en cbc:Description."
                ],
            }
        resultado = _extraer_de_invoice(embebida)
        if resultado["total"] is None:
            resultado["advertencias"].insert(
                0, "AttachedDocument: la factura embebida no tiene total válido."
            )
        return resultado

    if tipo_raiz in {"Invoice", "CreditNote"}:
        return _extraer_de_invoice(raiz)

    # XML desconocido: intentar factura embebida y, si no, buscar importes.
    embebida = _invoice_embebida(raiz)
    if embebida is not None:
        return _extraer_de_invoice(embebida)

    resultado = _extraer_de_invoice(raiz)
    resultado["advertencias"].insert(
        0, f"Raíz XML no reconocida: <{tipo_raiz}>. Se buscaron importes igualmente."
    )
    return resultado


def extraer_total_xml(xml_texto: Any) -> Dict[str, Any]:
    """Extrae el total de un XML UBL (texto o bytes).

    Devuelve un diccionario con ``total`` (``Decimal`` o ``None``), ``origen``,
    ``texto_original`` y ``advertencias`` (lista de diagnosticos).
    """

    if xml_texto is None:
        return {
            "total": None,
            "origen": None,
            "texto_original": None,
            "advertencias": ["XML vacío o no disponible."],
        }

    datos = xml_texto
    if isinstance(datos, str):
        datos = datos.strip()
        if not datos:
            return {
                "total": None,
                "origen": None,
                "texto_original": None,
                "advertencias": ["XML vacío o no disponible."],
            }
        datos = datos.encode("utf-8", errors="replace")

    try:
        raiz = ET.fromstring(datos)
    except ET.ParseError as error:
        return {
            "total": None,
            "origen": None,
            "texto_original": None,
            "advertencias": [f"XML inválido: {error}"],
        }

    return extraer_total_desde_elemento(raiz)
