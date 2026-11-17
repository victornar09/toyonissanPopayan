"""Pruebas del flujo de descripción estándar en Facturas.

Cubren: inventariar usando la descripción de Consolidado aunque el navegador
envíe otra, bloqueo de edición en la tabla y uso de la misma descripción en la
impresión diferida.
"""

import os
import shutil
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

import migraciones
from inventario import respaldo
from utilidades import normalizar_clave


RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ESTRUCTURA = os.path.join(RAIZ, "database", "estructura.sql")

DESC_FACTURA = "PASTILLA FRENO FACTURA"
DESC_ESTANDAR = "PASTILLA FRENO DELANTERA TOYOTA HILUX"
DESC_MANUAL = "DESCRIPCION MANUAL ENVIADA"


class BaseFacturasDescripcion(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="facturas_desc_test_")
        self.ruta = os.path.join(self.dir, "test.sqlite")
        self.respaldos = os.path.join(self.dir, "backups")

        parche_respaldo = patch.object(respaldo, "DIRECTORIO", self.respaldos)
        parche_respaldo.start()
        self.addCleanup(parche_respaldo.stop)

        with open(ESTRUCTURA, "r", encoding="utf-8") as archivo:
            sql = archivo.read()

        conn = sqlite3.connect(self.ruta)
        conn.executescript(sql)
        migraciones.migrar(conn)
        conn.execute(
            """
            INSERT INTO proveedor (
                nombreProveedor, siglas, correoFacturas, prefijo, nit
            )
            VALUES ('MOTORES JAPONESES SAS', 'MJN', 'facturas@mj.com', 'FE',
                    '900123456')
            """
        )
        conn.commit()
        conn.close()

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def conectar(self):
        return sqlite3.connect(self.ruta)

    def crear_consolidado(self, estado="LISTO_INVENTARIAR"):
        conn = self.conectar()
        cursor = conn.execute(
            """
            INSERT INTO consolidado (
                abrevProveedor, referenciaProveedor, descripcion,
                descripcionEstandarizada, referenciaRepuesto, equivalente,
                marca, carro, modelo, lado, medida, grupo, proveedor,
                estadoConsolidado
            ) VALUES (
                'MJN', 'REF-001', ?, ?, 'REP-01', 'EQ-01', 'GENUINO',
                'TOYOTA', 'HILUX', 'DELANTERO', '050', 'FRENOS',
                'MOTORES JAPONESES SAS', ?
            )
            """,
            (DESC_FACTURA, DESC_ESTANDAR, estado),
        )
        identificador = cursor.lastrowid
        conn.commit()
        conn.close()
        return identificador

    def crear_factura(self):
        conn = self.conectar()
        conn.execute(
            """
            INSERT INTO facturas (id_factura, fecha, proveedor, valor_total)
            VALUES ('F1', '2026-01-10', 'MOTORES JAPONESES SAS', 100000)
            """
        )
        cursor = conn.execute(
            """
            INSERT INTO inventarioFacturas (
                id_factura, descripcion_item, cantidad, valor_unitario,
                referencia
            ) VALUES ('F1', ?, 2, 1000, 'REF-001')
            """,
            (DESC_FACTURA,),
        )
        linea_id = cursor.lastrowid
        conn.commit()
        conn.close()
        return linea_id

    def crear_plantilla(self):
        conn = self.conectar()
        cursor = conn.execute(
            """
            INSERT INTO bartenderFiles (name, ubicacion, numeroColumnas, activo)
            VALUES ('Etiqueta prueba', 'C:/etiquetas/prueba.btw', 2, 1)
            """
        )
        identificador = cursor.lastrowid
        conn.commit()
        conn.close()
        return identificador

    def crear_linea_inventariada(self, descripcion_inventario="VIEJA DESCRIPCION"):
        conn = self.conectar()
        cursor = conn.execute(
            """
            INSERT INTO inventarioUnico (
                descripcion, descripcionClave, descripcionEstandarizada,
                cantidad, precioVenta, precioVentaCifrado,
                precioMaxDescuento, proveedor, fechaActualizacion,
                estadoEtiquetado, estadoConteo, codigoBarras
            ) VALUES (?, ?, ?, 1, 10000, 'cifrado', 9000,
                      'MOTORES JAPONESES SAS', '2026-01-10', 'PENDIENTE',
                      'SIN_CONTAR', '770000000001')
            """,
            (
                descripcion_inventario,
                normalizar_clave(descripcion_inventario),
                descripcion_inventario,
            ),
        )
        inventario_id = cursor.lastrowid

        cursor = conn.execute(
            """
            INSERT INTO consolidado (
                abrevProveedor, referenciaProveedor, descripcion,
                descripcionEstandarizada, referenciaRepuesto, equivalente,
                marca, carro, modelo, lado, medida, grupo, proveedor,
                estadoConsolidado, idInventarioUnico
            ) VALUES (
                'MJN', 'REF-001', ?, ?, 'REP-01', 'EQ-01', 'GENUINO',
                'TOYOTA', 'HILUX', 'DELANTERO', '050', 'FRENOS',
                'MOTORES JAPONESES SAS', 'INVENTARIADO', ?
            )
            """,
            (DESC_FACTURA, DESC_ESTANDAR, inventario_id),
        )
        consolidado_id = cursor.lastrowid

        conn.execute(
            """
            INSERT INTO facturas (id_factura, fecha, proveedor, valor_total)
            VALUES ('F1', '2026-01-10', 'MOTORES JAPONESES SAS', 100000)
            """
        )
        cursor = conn.execute(
            """
            INSERT INTO inventarioFacturas (
                id_factura, descripcion_item, cantidad, valor_unitario,
                referencia, inventariado, id_inventarioUnico, id_consolidado
            ) VALUES ('F1', ?, 2, 1000, 'REF-001', 1, ?, ?)
            """,
            (DESC_FACTURA, inventario_id, consolidado_id),
        )
        factura_id = cursor.lastrowid
        conn.commit()
        conn.close()
        return factura_id, inventario_id, consolidado_id

    def _cliente(self):
        from app import app

        app.config.update(TESTING=True)
        cliente = app.test_client()
        with cliente.session_transaction() as sesion:
            sesion["usuario"] = "tester"
        return cliente


class TestInventariarDescripcionEstandar(BaseFacturasDescripcion):
    def test_inventariar_desde_factura_usa_descripcion_de_consolidado(self):
        self.crear_consolidado()
        linea_id = self.crear_factura()

        with patch("db.ruta_bd_activa", return_value=self.ruta):
            respuesta = self._cliente().post(
                "/facturas/ver/F1",
                data={
                    "seleccionados": str(linea_id),
                    f"descripcion_{linea_id}": DESC_MANUAL,
                    f"valor_publico_{linea_id}": "10.000",
                },
            )

        self.assertEqual(respuesta.status_code, 302)

        conn = self.conectar()
        linea = conn.execute(
            "SELECT inventariado, id_inventarioUnico FROM inventarioFacturas "
            "WHERE id = ?",
            (linea_id,),
        ).fetchone()
        self.assertEqual(linea[0], 1)

        articulo = conn.execute(
            "SELECT descripcion, descripcionEstandarizada, descripcionClave "
            "FROM inventarioUnico WHERE id = ?",
            (linea[1],),
        ).fetchone()
        trabajos = conn.execute(
            "SELECT COUNT(*) FROM trabajoImpresion"
        ).fetchone()[0]
        conn.close()

        self.assertEqual(articulo[0], DESC_ESTANDAR)
        self.assertEqual(articulo[1], DESC_ESTANDAR)
        self.assertEqual(articulo[2], normalizar_clave(DESC_ESTANDAR))
        self.assertNotEqual(articulo[0], DESC_MANUAL)
        # Inventariar sin plantilla no debe encolar impresión.
        self.assertEqual(trabajos, 0)

    def test_detalle_no_permite_editar_descripcion(self):
        self.crear_consolidado()
        self.crear_factura()

        with patch("db.ruta_bd_activa", return_value=self.ruta):
            respuesta = self._cliente().get("/facturas/ver/F1")

        self.assertEqual(respuesta.status_code, 200)
        html = respuesta.get_data(as_text=True)

        self.assertNotIn('name="descripcion_', html)
        self.assertIn(DESC_ESTANDAR, html)
        self.assertIn("Descripción estándar de consolidado", html)
        self.assertNotIn("Imprimir etiquetas de artículos inventariados", html)


class TestImpresionDiferida(BaseFacturasDescripcion):
    def test_impresion_diferida_usa_descripcion_estandar(self):
        factura_id, inventario_id, _ = self.crear_linea_inventariada()
        plantilla_id = self.crear_plantilla()

        capturado = {}

        def fake_crear_trabajo(*args, **kwargs):
            capturado.update(kwargs)
            return {"idTrabajo": 99, "duplicado": False}

        with patch("db.ruta_bd_activa", return_value=self.ruta), patch(
            "facturas.routes.impresion_servicio.crear_trabajo",
            side_effect=fake_crear_trabajo,
        ):
            respuesta = self._cliente().post(
                "/facturas/imprimir-etiquetas",
                data={
                    "id_factura": "F1",
                    "lineas": [str(factura_id)],
                    "template_id": str(plantilla_id),
                },
            )

        self.assertEqual(respuesta.status_code, 302)
        self.assertIn("articulos", capturado)
        articulo = capturado["articulos"][0]
        conn = self.conectar()
        codigo_bd = conn.execute(
            "SELECT codigoBarras FROM inventarioUnico WHERE id = ?",
            (inventario_id,),
        ).fetchone()[0]
        conn.close()
        self.assertEqual(articulo["descripcion"], DESC_ESTANDAR)
        self.assertEqual(articulo["codigo"], codigo_bd)
        self.assertEqual(articulo["cantidad"], 2)

    def test_impresion_diferida_sin_lineas_avisa(self):
        self.crear_consolidado()
        self.crear_factura()
        plantilla_id = self.crear_plantilla()

        with patch("db.ruta_bd_activa", return_value=self.ruta):
            respuesta = self._cliente().post(
                "/facturas/imprimir-etiquetas",
                data={"id_factura": "F1", "template_id": str(plantilla_id)},
                follow_redirects=True,
            )

        self.assertEqual(respuesta.status_code, 200)
        self.assertIn(
            "Selecciona al menos un artículo inventariado",
            respuesta.get_data(as_text=True),
        )


if __name__ == "__main__":
    unittest.main()
