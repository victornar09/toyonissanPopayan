"""Pruebas del módulo Consolidado.

Cubren importación idempotente, duplicados/ambigüedades, edición,
inventariado individual y múltiple, doble envío, reutilización de artículos,
generación de código, relación Factura -> Consolidado -> Inventario y
bloqueo de solicitudes manipuladas.
"""

import os
import shutil
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

import migraciones
from consolidado import servicio
from inventario import respaldo
from utilidades import normalizar_clave


RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ESTRUCTURA = os.path.join(RAIZ, "database", "estructura.sql")


class BaseConsolidado(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="consolidado_test_")
        self.ruta = os.path.join(self.dir, "test.sqlite")
        self.ruta_sin_reglas = os.path.join(self.dir, "no_existe.xlsx")

        # Aislar el respaldo: las pruebas nunca deben escribir en backups/.
        parche_respaldo = patch.object(
            respaldo, "DIRECTORIO", os.path.join(self.dir, "backups")
        )
        parche_respaldo.start()
        self.addCleanup(parche_respaldo.stop)

        with open(ESTRUCTURA, "r", encoding="utf-8") as archivo:
            sql = archivo.read()

        conn = sqlite3.connect(self.ruta)
        conn.executescript(sql)
        migraciones.migrar(conn)
        conn.commit()
        conn.close()

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    # ------------------------------------------------------------------
    # Utilidades
    # ------------------------------------------------------------------
    def conectar(self):
        return sqlite3.connect(self.ruta)

    def crear_factura(self, id_factura="F1", fecha="2026-01-10",
                      proveedor="MOTORES JAPONESES SAS", lineas=None):
        conn = self.conectar()
        conn.execute(
            """
            INSERT OR REPLACE INTO facturas
                (id_factura, fecha, proveedor, valor_total)
            VALUES (?, ?, ?, ?)
            """,
            (id_factura, fecha, proveedor, 100000),
        )
        for referencia, descripcion in lineas or []:
            conn.execute(
                """
                INSERT INTO inventarioFacturas
                    (id_factura, descripcion_item, cantidad, valor_unitario,
                     referencia)
                VALUES (?, ?, 1, 1000, ?)
                """,
                (id_factura, descripcion, referencia),
            )
        conn.commit()
        conn.close()

    def crear_consolidado(self, referencia, descripcion="ARTICULO EXISTENTE",
                          abrev="MJN", estado="LISTO_INVENTARIAR",
                          descripcion_estandarizada=None):
        conn = self.conectar()
        conn.execute(
            """
            INSERT INTO consolidado (
                abrevProveedor, referenciaProveedor, descripcion,
                descripcionEstandarizada, proveedor, estadoConsolidado
            )
            VALUES (?, ?, ?, ?, 'MOTORES JAPONESES SAS', ?)
            """,
            (
                abrev,
                referencia,
                descripcion,
                descripcion_estandarizada or descripcion,
                estado,
            ),
        )
        conn.commit()
        identificador = conn.execute(
            "SELECT id FROM consolidado WHERE referenciaProveedor = ? "
            "ORDER BY id DESC LIMIT 1",
            (referencia,),
        ).fetchone()[0]
        conn.close()
        return identificador

    def importar(self, confirmar=False, **kwargs):
        datos = {
            "fecha_inicial": "2026-01-01",
            "fecha_final": "2026-01-31",
            "solo_no_procesadas": True,
            "confirmar": confirmar,
            "usuario": "tester",
            "ruta_estandarizacion": self.ruta_sin_reglas,
        }
        datos.update(kwargs)
        return servicio.importar_desde_facturas(self.ruta, **datos)

    def contar(self, tabla, where="", params=()):
        conn = self.conectar()
        consulta = f"SELECT COUNT(*) FROM {tabla}"
        if where:
            consulta += f" WHERE {where}"
        total = conn.execute(consulta, params).fetchone()[0]
        conn.close()
        return total

    # ------------------------------------------------------------------
    # 1. Importación por rango y vista previa
    # ------------------------------------------------------------------
    def test_importacion_por_rango_y_preview(self):
        self.crear_factura(lineas=[
            ("REF-001", "PASTILLA FRENO TOYOTA HILUX"),
            ("REF-002", "FILTRO ACEITE NISSAN FRONTIER"),
            ("SIN_REFERENCIA", "ARTICULO SIN REFERENCIA"),
            ("REF-AMB", "AMORTIGUADOR DELANTERO"),
        ])
        self.crear_factura(
            id_factura="F2", fecha="2026-03-01",
            lineas=[("REF-999", "FUERA DEL RANGO")],
        )
        self.crear_consolidado("REF-002", "FILTRO ACEITE NISSAN FRONTIER")
        self.crear_consolidado("REF-AMB", "AMORTIGUADOR DELANTERO UNO")
        self.crear_consolidado(
            "ref-amb ", descripcion="AMORTIGUADOR DELANTERO DOS",
            descripcion_estandarizada="AMORTIGUADOR DELANTERO DOS",
        )

        resultado = self.importar()

        self.assertTrue(resultado["ok"])
        self.assertEqual(resultado["facturas_encontradas"], 1)
        self.assertEqual(resultado["lineas_encontradas"], 4)
        self.assertEqual(resultado["nuevos"], 1)
        self.assertEqual(resultado["existentes"], 1)
        self.assertEqual(resultado["sin_referencia"], 1)
        self.assertEqual(resultado["ambiguas"], 1)
        self.assertEqual(resultado["bloqueadas"], 2)

    # ------------------------------------------------------------------
    # 2. Importación idempotente
    # ------------------------------------------------------------------
    def test_reimportacion_sin_duplicados(self):
        self.crear_factura(lineas=[
            ("REF-001", "PASTILLA FRENO TOYOTA HILUX"),
        ])

        primera = self.importar(confirmar=True)
        self.assertTrue(primera["ok"])
        self.assertEqual(primera["creados"], 1)

        total_1 = self.contar("consolidado")
        vinculadas_1 = self.contar(
            "inventarioFacturas", "id_consolidado IS NOT NULL"
        )

        segunda = self.importar(confirmar=True)
        self.assertTrue(segunda["ok"])
        self.assertEqual(segunda["creados"], 0)
        self.assertEqual(segunda["actualizados"], 0)
        self.assertEqual(self.contar("consolidado"), total_1)
        self.assertEqual(
            self.contar("inventarioFacturas", "id_consolidado IS NOT NULL"),
            vinculadas_1,
        )

    # ------------------------------------------------------------------
    # 3. Línea sin referencia
    # ------------------------------------------------------------------
    def test_articulo_sin_referencia_no_se_importa(self):
        self.crear_factura(lineas=[
            ("SIN_REFERENCIA", "ARTICULO SIN REFERENCIA"),
        ])
        resultado = self.importar(confirmar=True)
        self.assertEqual(resultado["creados"], 0)
        self.assertEqual(resultado["sin_referencia"], 1)
        self.assertEqual(self.contar("consolidado"), 0)

    # ------------------------------------------------------------------
    # 4. Coincidencia ambigua
    # ------------------------------------------------------------------
    def test_coincidencia_ambigua_se_bloquea(self):
        self.crear_consolidado("REF-AMB", "ARTICULO UNO")
        self.crear_consolidado("ref-amb ", "ARTICULO DOS")
        self.crear_factura(lineas=[("REF-AMB", "ARTICULO AMBIGUO")])

        resultado = self.importar(confirmar=True)
        self.assertEqual(resultado["ambiguas"], 1)
        self.assertEqual(resultado["creados"], 0)
        self.assertEqual(self.contar("consolidado"), 2)

    # ------------------------------------------------------------------
    # 5. Edición y recálculo de estado
    # ------------------------------------------------------------------
    def test_edicion_recalcula_estado_y_rechaza_duplicado(self):
        self.crear_factura(lineas=[
            ("REF-001", "PASTILLA FRENO TOYOTA HILUX"),
        ])
        self.importar(confirmar=True)

        conn = self.conectar()
        identificador = conn.execute(
            "SELECT id FROM consolidado WHERE referenciaProveedor = 'REF-001'"
        ).fetchone()[0]
        conn.close()

        pendiente = servicio.editar_consolidado(
            self.ruta,
            identificador,
            {"descripcion": "OK"},
            usuario="tester",
            ruta_estandarizacion=self.ruta_sin_reglas,
        )
        self.assertTrue(pendiente["ok"])
        self.assertEqual(pendiente["estadoConsolidado"], "PENDIENTE_REVISION")

        listo = servicio.editar_consolidado(
            self.ruta,
            identificador,
            {"descripcion": "PASTILLA FRENO DELANTERA TOYOTA HILUX"},
            usuario="tester",
            ruta_estandarizacion=self.ruta_sin_reglas,
        )
        self.assertTrue(listo["ok"])
        self.assertEqual(listo["estadoConsolidado"], "LISTO_INVENTARIAR")

        self.crear_consolidado("REF-002", "OTRO ARTICULO")
        duplicado = servicio.editar_consolidado(
            self.ruta,
            identificador,
            {"referenciaProveedor": "REF-002"},
            usuario="tester",
            ruta_estandarizacion=self.ruta_sin_reglas,
        )
        self.assertFalse(duplicado["ok"])
        self.assertIn("duplicado", duplicado["error"].lower())

    # ------------------------------------------------------------------
    # 6/7/8. Inventariado individual, múltiple y doble envío
    # ------------------------------------------------------------------
    def _preparar_dos_listos(self):
        id_1 = self.crear_consolidado(
            "REF-A", "PASTILLA FRENO TOYOTA",
            descripcion_estandarizada="PASTILLA FRENO TOYOTA",
        )
        id_2 = self.crear_consolidado(
            "REF-B", "FILTRO ACEITE NISSAN",
            descripcion_estandarizada="FILTRO ACEITE NISSAN",
        )
        return id_1, id_2

    def test_inventariado_individual_y_doble_envio(self):
        identificador, _ = self._preparar_dos_listos()
        antes = self.contar("inventarioUnico")

        primero = servicio.inventariar_consolidado(
            self.ruta, [identificador], usuario="tester",
            opciones={"ubicacion": "P-1", "estante": "E-2", "precio": "50000"},
        )
        self.assertTrue(primero["ok"])
        self.assertEqual(primero["inventariados"], 1)
        self.assertEqual(self.contar("inventarioUnico"), antes + 1)

        conn = self.conectar()
        fila = conn.execute(
            "SELECT iu.cantidad, iu.codigoBarras, iu.estadoEtiquetado, "
            "iu.estadoConteo, c.estadoConsolidado, c.idInventarioUnico "
            "FROM consolidado c JOIN inventarioUnico iu "
            "ON iu.id = c.idInventarioUnico WHERE c.id = ?",
            (identificador,),
        ).fetchone()
        conn.close()

        self.assertEqual(fila[0], 0)  # inventariar no modifica cantidades
        self.assertTrue(fila[1])
        self.assertEqual(fila[2], "PENDIENTE")
        self.assertEqual(fila[3], "SIN_CONTAR")
        self.assertEqual(fila[4], "INVENTARIADO")
        self.assertEqual(fila[5], fila[5])

        segundo = servicio.inventariar_consolidado(
            self.ruta, [identificador], usuario="tester"
        )
        self.assertEqual(segundo["inventariados"], 0)
        self.assertEqual(segundo["omitidos"], 1)
        self.assertEqual(self.contar("inventarioUnico"), antes + 1)

    def test_inventariado_multiple(self):
        id_1, id_2 = self._preparar_dos_listos()
        antes = self.contar("inventarioUnico")

        resultado = servicio.inventariar_consolidado(
            self.ruta, [id_1, id_2], usuario="tester"
        )
        self.assertTrue(resultado["ok"])
        self.assertEqual(resultado["inventariados"], 2)
        self.assertEqual(self.contar("inventarioUnico"), antes + 2)

    # ------------------------------------------------------------------
    # 9/10. Reutilización y generación de código
    # ------------------------------------------------------------------
    def test_reutiliza_articulo_existente(self):
        identificador = self.crear_consolidado(
            "REF-A", "PASTILLA FRENO TOYOTA",
            descripcion_estandarizada="PASTILLA FRENO TOYOTA",
        )
        descripcion = "PASTILLA FRENO TOYOTA"
        conn = self.conectar()
        conn.execute(
            """
            INSERT INTO inventarioUnico (
                descripcion, descripcionClave, referencia, cantidad,
                precioVenta, proveedor, fechaActualizacion
            )
            VALUES (?, ?, 'X', 7, 1000, 'MOTORES JAPONESES SAS', '2026-01-01')
            """,
            (descripcion, normalizar_clave(descripcion)),
        )
        conn.commit()
        existente = conn.execute(
            "SELECT id, codigoBarras FROM inventarioUnico WHERE descripcionClave = ?",
            (normalizar_clave(descripcion),),
        ).fetchone()
        conn.close()

        antes = self.contar("inventarioUnico")
        resultado = servicio.inventariar_consolidado(
            self.ruta, [identificador], usuario="tester"
        )

        self.assertEqual(resultado["inventariados"], 1)
        self.assertEqual(resultado["reutilizados"], 1)
        self.assertEqual(self.contar("inventarioUnico"), antes)

        conn = self.conectar()
        fila = conn.execute(
            "SELECT c.idInventarioUnico, iu.cantidad, iu.codigoBarras "
            "FROM consolidado c JOIN inventarioUnico iu "
            "ON iu.id = c.idInventarioUnico WHERE c.id = ?",
            (identificador,),
        ).fetchone()
        conn.close()

        self.assertEqual(fila[0], existente[0])
        self.assertEqual(fila[1], 7)  # la cantidad existente no se altera
        self.assertEqual(fila[2], existente[1])

    def test_generacion_de_codigo_para_articulo_nuevo(self):
        identificador = self.crear_consolidado(
            "REF-A", "PASTILLA FRENO TOYOTA",
            descripcion_estandarizada="PASTILLA FRENO TOYOTA",
        )
        resultado = servicio.inventariar_consolidado(
            self.ruta, [identificador], usuario="tester"
        )
        etiqueta = resultado["etiquetas"][0]
        self.assertTrue(etiqueta["codigo"])
        self.assertEqual(len(str(etiqueta["codigo"])), 8)
        self.assertTrue(str(etiqueta["codigo"]).isdigit())

    # ------------------------------------------------------------------
    # 11/12/13. Impresión desacoplada, aparición y trazabilidad
    # ------------------------------------------------------------------
    def test_falla_impresion_no_duplica_y_permite_reimprimir(self):
        identificador = self.crear_consolidado(
            "REF-A", "PASTILLA FRENO TOYOTA",
            descripcion_estandarizada="PASTILLA FRENO TOYOTA",
        )
        antes = self.contar("inventarioUnico")
        resultado = servicio.inventariar_consolidado(
            self.ruta, [identificador], usuario="tester"
        )

        # Simular que la impresión falla después del guardado.
        try:
            raise RuntimeError("BarTender no disponible")
        except RuntimeError:
            pass

        self.assertEqual(resultado["inventariados"], 1)
        self.assertEqual(self.contar("inventarioUnico"), antes + 1)

        etiquetas = servicio.etiquetas_reimpresion(self.ruta, [identificador])
        self.assertEqual(len(etiquetas), 1)
        self.assertEqual(
            etiquetas[0]["codigo"], resultado["etiquetas"][0]["codigo"]
        )
        self.assertEqual(self.contar("inventarioUnico"), antes + 1)

    def test_aparece_en_inventario_y_relacion_trazable(self):
        self.crear_factura(lineas=[
            ("REF-001", "PASTILLA FRENO TOYOTA HILUX"),
        ])
        self.importar(confirmar=True)

        conn = self.conectar()
        consolidado_id = conn.execute(
            "SELECT id FROM consolidado WHERE referenciaProveedor = 'REF-001'"
        ).fetchone()[0]
        conn.close()

        servicio.inventariar_consolidado(
            self.ruta, [consolidado_id], usuario="tester"
        )

        conn = self.conectar()
        fila = conn.execute(
            """
            SELECT inf.id_consolidado, c.idInventarioUnico,
                   iu.codigoBarras, iu.descripcion
            FROM inventarioFacturas inf
            JOIN consolidado c ON c.id = inf.id_consolidado
            JOIN inventarioUnico iu ON iu.id = c.idInventarioUnico
            WHERE inf.referencia = 'REF-001'
            """
        ).fetchone()
        conn.close()

        self.assertIsNotNone(fila)
        self.assertEqual(fila[0], consolidado_id)
        self.assertIsNotNone(fila[1])
        self.assertTrue(fila[2])
        self.assertIn("PASTILLA", fila[3].upper())

    # ------------------------------------------------------------------
    # 14. Bloqueo de solicitudes manipuladas
    # ------------------------------------------------------------------
    def test_bloquea_inventariado_de_registro_pendiente(self):
        identificador = self.crear_consolidado(
            "REF-P", "REGISTRO INCOMPLETO",
            descripcion_estandarizada="",
            estado="PENDIENTE_REVISION",
        )
        antes = self.contar("inventarioUnico")

        resultado = servicio.inventariar_consolidado(
            self.ruta, [identificador], usuario="tester"
        )

        self.assertEqual(resultado["inventariados"], 0)
        self.assertEqual(resultado["bloqueados"], 1)
        self.assertEqual(self.contar("inventarioUnico"), antes)

        conn = self.conectar()
        estado = conn.execute(
            "SELECT estadoConsolidado, idInventarioUnico FROM consolidado WHERE id = ?",
            (identificador,),
        ).fetchone()
        conn.close()
        self.assertEqual(estado[0], "PENDIENTE_REVISION")
        self.assertIsNone(estado[1])

    def test_bloquea_inventariado_de_id_inexistente(self):
        resultado = servicio.inventariar_consolidado(
            self.ruta, [999999], usuario="tester"
        )
        self.assertEqual(resultado["inventariados"], 0)
        self.assertEqual(resultado["errores"], 1)


if __name__ == "__main__":
    unittest.main()
