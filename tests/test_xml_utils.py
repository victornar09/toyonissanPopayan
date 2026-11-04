"""Pruebas de lectura de importes en XML UBL (sin dependencias externas).

Ejecutar con::

    python -m unittest discover -s tests -v
"""

import html
import os
import sys
import unittest
from decimal import Decimal

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if RAIZ not in sys.path:
    sys.path.insert(0, RAIZ)

from facturas.xml_utils import (  # noqa: E402
    extraer_total_xml,
    formatear_moneda,
    parsear_importe,
)


INVOICE = """<?xml version="1.0" encoding="UTF-8"?>
<Invoice xmlns="urn:oasis:names:specification:ubl:schema:xsd:Invoice-2"
         xmlns:cac="urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2"
         xmlns:cbc="urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2">
  <cac:LegalMonetaryTotal>
    <cbc:LineExtensionAmount>1000.00</cbc:LineExtensionAmount>
    <cbc:TaxInclusiveAmount>1190.00</cbc:TaxInclusiveAmount>
    <cbc:PayableAmount>258498.70</cbc:PayableAmount>
  </cac:LegalMonetaryTotal>
</Invoice>"""

INVOICE_ENTEROS = INVOICE.replace("258498.70", "900000")

INVOICE_SEPARADORES = INVOICE.replace("258498.70", "1.234.567,89")

INVOICE_SIN_TOTAL = """<?xml version="1.0" encoding="UTF-8"?>
<Invoice xmlns="urn:oasis:names:specification:ubl:schema:xsd:Invoice-2"
         xmlns:cac="urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2"
         xmlns:cbc="urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2">
  <cac:LegalMonetaryTotal>
    <cbc:PayableAmount>N/A</cbc:PayableAmount>
  </cac:LegalMonetaryTotal>
</Invoice>"""

INVOICE_PREFIJOS = """<?xml version="1.0" encoding="UTF-8"?>
<fe:Invoice xmlns:fe="urn:oasis:names:specification:ubl:schema:xsd:Invoice-2"
            xmlns:a="urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2"
            xmlns:b="urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2">
  <a:LegalMonetaryTotal>
    <b:PayableAmount>753572.50</b:PayableAmount>
  </a:LegalMonetaryTotal>
</fe:Invoice>"""


def attached_document(invoice_xml):
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<AttachedDocument xmlns="urn:oasis:names:specification:ubl:schema:xsd:AttachedDocument-2"'
        ' xmlns:cac="urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2"'
        ' xmlns:cbc="urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2">'
        "<cac:Attachment><cac:ExternalReference><cbc:Description>"
        + html.escape(invoice_xml, quote=False)
        + "</cbc:Description></cac:ExternalReference></cac:Attachment>"
        "</AttachedDocument>"
    )


class ParsearImporteTest(unittest.TestCase):
    def test_formatos(self):
        casos = {
            "11405735.88": Decimal("11405735.88"),
            "258498.70": Decimal("258498.70"),
            "$ 1.234.567,89": Decimal("1234567.89"),
            "1,234,567.89": Decimal("1234567.89"),
            "1.234.567": Decimal("1234567"),
            "1,234": Decimal("1234"),
            "  11 405 735,88 ": Decimal("11405735.88"),
            "COP 401810.28": Decimal("401810.28"),
            "0": Decimal("0"),
        }
        for texto, esperado in casos.items():
            with self.subTest(texto=texto):
                self.assertEqual(parsear_importe(texto), esperado)

    def test_invalidos(self):
        for texto in ("", None, "N/A", "abc", "$"):
            with self.subTest(texto=texto):
                self.assertIsNone(parsear_importe(texto))

    def test_formato_moneda(self):
        self.assertEqual(formatear_moneda(Decimal("11405735.88")), "$ 11.405.736")
        self.assertEqual(formatear_moneda("258498.70"), "$ 258.499")
        self.assertEqual(formatear_moneda(None), "$ 0")


class ExtraerTotalTest(unittest.TestCase):
    def test_invoice_ubl_directo(self):
        resultado = extraer_total_xml(INVOICE)
        self.assertEqual(resultado["total"], Decimal("258498.70"))
        self.assertEqual(resultado["origen"], "LegalMonetaryTotal/PayableAmount")

    def test_attached_document(self):
        resultado = extraer_total_xml(attached_document(INVOICE))
        self.assertEqual(resultado["total"], Decimal("258498.70"))

    def test_valores_enteros(self):
        self.assertEqual(
            extraer_total_xml(INVOICE_ENTEROS)["total"], Decimal("900000")
        )

    def test_separadores_colombianos(self):
        self.assertEqual(
            extraer_total_xml(INVOICE_SEPARADORES)["total"], Decimal("1234567.89")
        )

    def test_namespaces_con_prefijos_distintos(self):
        self.assertEqual(
            extraer_total_xml(INVOICE_PREFIJOS)["total"], Decimal("753572.50")
        )

    def test_xml_sin_total_valido(self):
        resultado = extraer_total_xml(INVOICE_SIN_TOTAL)
        self.assertIsNone(resultado["total"])
        self.assertTrue(resultado["advertencias"])

    def test_xml_invalido(self):
        resultado = extraer_total_xml("<Invoice>")
        self.assertIsNone(resultado["total"])
        self.assertTrue(resultado["advertencias"])

    def test_respaldo_tax_inclusive(self):
        xml = INVOICE.replace(
            "<cbc:PayableAmount>258498.70</cbc:PayableAmount>", ""
        )
        self.assertEqual(extraer_total_xml(xml)["total"], Decimal("1190.00"))


if __name__ == "__main__":
    unittest.main()
