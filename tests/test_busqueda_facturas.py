"""Pruebas de la búsqueda transversal de artículos en facturas.

La búsqueda es de sólo lectura: se verifican filtros, estados, paginación y
navegación al detalle sin modificar Facturas, Consolidado ni Inventario.
"""

import os
import shutil
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

import migraciones
from facturas import busqueda


RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ESTRUCTURA = os.path.join(RAIZ, "database", "estructura.sql")


class BaseBusqueda(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="busqueda_test_")
        self.ruta = os.path.join(self.dir, "test.sqlite")

        with open(ESTRUCTURA, "r", encoding="utf-8") as archivo:
            sql = archivo.read()

        conn = sqlite3.connect(self.ruta)
        conn.executescript(sql)
        migraciones.migrar(conn)
        conn.execute(
            "INSERT OR IGNORE INTO proveedor "
            "(nombreProveedor, nombreProveedorSecundario, siglas, correoFacturas, prefijo, nit) "
            "VALUES ('PROVEEDOR ALFA SAS', 'ALFA COMERCIAL', 'ALFA', 'alfa@test.com', 'AL', 900000001)"
        )
        conn.execute(
            "INSERT OR IGNORE INTO proveedor "
            "(nombreProveedor, nombreProveedorSecundario, siglas, correoFacturas, prefijo, nit) "
            "VALUES ('PROVEEDOR BETA SAS', 'BETA COMERCIAL', 'BETA', 'beta@test.com', 'BE', 900000002)"
        )
        conn.commit()
        conn.close()

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def conectar(self):
        return sqlite3.connect(self.ruta)

    # ------------------------------------------------------------------
    # Datos de prueba
    # ------------------------------------------------------------------
    def crear_factura(self, id_factura, proveedor="PROVEEDOR ALFA SAS", fecha="2026-01-15"):
        conn = self.conectar()
        conn.execute(
            "INSERT INTO facturas (id_factura, fecha, proveedor, valor_total) "
            "VALUES (?, ?, ?, 100000)",
            (id_factura, fecha, proveedor),
        )
        conn.commit()
        conn.close()

    def crear_linea(
        self,
        id_factura,
        referencia="REF-001",
        descripcion="BOMBA DE FRENO",
        cantidad=2,
        valor=10000,
        referencia_original=None,
        referencia_corregida=None,
        inventariado=0,
        id_inventario=None,
        id_consolidado=None,
    ):
        conn = self.conectar()
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO inventarioFacturas (
                id_factura, descripcion_item, cantidad, valor_unitario,
                referencia, inventariado, id_inventarioUnico, id_consolidado,
                referenciaOriginal, referenciaCorregida
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                id_factura,
                descripcion,
                cantidad,
                valor,
                referencia,
                inventariado,
                id_inventario,
                id_consolidado,
                referencia_original or referencia,
                referencia_corregida,
            ),
        )
        conn.commit()
        linea_id = cursor.lastrowid
        conn.close()
        return linea_id

    def crear_consolidado(
        self,
        abrev="ALFA",
        referencia="REF-001",
        descripcion_estandarizada="BOMBA DE FRENO TOYOTA HILUX",
        id_inventario=None,
        estado="LISTO_INVENTARIAR",
        equivalente=None,
        referencia_repuesto=None,
    ):
        conn = self.conectar()
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO consolidado (
                abrevProveedor, referenciaProveedor,
                descripcionEstandarizada, estadoConsolidado,
                idInventarioUnico, equivalente, referenciaRepuesto
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                abrev,
                referencia,
                descripcion_estandarizada,
                estado,
                id_inventario,
                equivalente,
                referencia_repuesto,
            ),
        )
        conn.commit()
        id_consolidado = cursor.lastrowid
        conn.close()
        return id_consolidado

    def crear_articulo_inventario(self, descripcion="BOMBA DE FRENO TOYOTA HILUX"):
        conn = self.conectar()
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO inventarioUnico (
                referencia, descripcion, descripcionClave, cantidad,
                precioVenta, precioVentaCifrado, precioMaxDescuento,
                proveedor, fechaActualizacion, estadoEtiquetado, estadoConteo
            ) VALUES ('REF-001', ?, ?, 2, 10000, 'x', 0,
                      'PROVEEDOR ALFA SAS', '2026-01-15', 'PENDIENTE', 'SIN_CONTAR')
            """,
            (descripcion, descripcion.upper()),
        )
        conn.commit()
        id_item = cursor.lastrowid
        conn.close()
        return id_item

    def buscar(self, **filtros):
        completos = busqueda.extraer_filtros(filtros)
        return busqueda.buscar_articulos(self.ruta, completos)

    def _cliente(self):
        from app import app

        app.config.update(TESTING=True)
        cliente = app.test_client()
        with cliente.session_transaction() as sesion:
            sesion["usuario"] = "tester"
        return cliente


class TestFiltrosBusqueda(BaseBusqueda):
    def test_referencia_exacta(self):
        self.crear_factura("F-001")
        self.crear_linea("F-001", referencia="ABC-123")
        self.crear_linea("F-001", referencia="XYZ-999", descripcion="OTRA COSA")

        resultado = self.buscar(referencia="ABC-123")

        self.assertEqual(resultado["total"], 1)
        self.assertEqual(resultado["filas"][0]["referencia"], "ABC-123")

    def test_referencia_parcial(self):
        self.crear_factura("F-001")
        self.crear_linea("F-001", referencia="ABC-123")
        self.crear_linea("F-001", referencia="XYZ-999", descripcion="OTRA COSA")

        resultado = self.buscar(referencia="123")

        self.assertEqual(resultado["total"], 1)
        self.assertEqual(resultado["filas"][0]["referencia"], "ABC-123")

    def test_descripcion_con_y_sin_tildes(self):
        self.crear_factura("F-001")
        self.crear_linea("F-001", descripcion="BÓMBA  DE FRENO - DELANTERA")

        self.assertEqual(self.buscar(descripcion="bomba freno")["total"], 1)
        self.assertEqual(self.buscar(descripcion="BOMBA DE FRENO")["total"], 1)
        self.assertEqual(self.buscar(descripcion="bómba freno delantera")["total"], 1)

    def test_varias_palabras_en_diferente_orden(self):
        self.crear_factura("F-001")
        self.crear_linea("F-001", descripcion="BOMBA DE FRENO DELANTERA TOYOTA")

        self.assertEqual(self.buscar(descripcion="toyota freno bomba")["total"], 1)
        self.assertEqual(self.buscar(descripcion="delantera bomba")["total"], 1)

    def test_filtro_por_proveedor(self):
        self.crear_factura("F-001", proveedor="PROVEEDOR ALFA SAS")
        self.crear_linea("F-001", referencia="REF-1")
        self.crear_factura("F-002", proveedor="PROVEEDOR BETA SAS")
        self.crear_linea("F-002", referencia="REF-2")

        resultado = self.buscar(proveedor="alfa")

        self.assertEqual(resultado["total"], 1)
        self.assertEqual(resultado["filas"][0]["proveedor"], "PROVEEDOR ALFA SAS")

        por_siglas = self.buscar(proveedor="BETA")
        self.assertEqual(por_siglas["total"], 1)
        self.assertEqual(por_siglas["filas"][0]["id_factura"], "F-002")

    def test_filtro_por_rango_de_fechas(self):
        self.crear_factura("F-001", fecha="2026-01-10")
        self.crear_linea("F-001")
        self.crear_factura("F-002", fecha="2026-02-10")
        self.crear_linea("F-002", referencia="REF-002")
        self.crear_factura("F-003", fecha="2026-03-10")
        self.crear_linea("F-003", referencia="REF-003")

        resultado = self.buscar(fecha_desde="2026-02-01", fecha_hasta="2026-02-28")

        self.assertEqual(resultado["total"], 1)
        self.assertEqual(resultado["filas"][0]["id_factura"], "F-002")

    def test_articulo_en_varias_facturas(self):
        for numero in range(1, 4):
            self.crear_factura(f"F-00{numero}")
            self.crear_linea(f"F-00{numero}", referencia="MISMA-REF")

        resultado = self.buscar(referencia="MISMA-REF")

        self.assertEqual(resultado["total"], 3)
        self.assertEqual(
            sorted(fila["id_factura"] for fila in resultado["filas"]),
            ["F-001", "F-002", "F-003"],
        )

    def test_referencia_repetida_entre_proveedores(self):
        self.crear_factura("F-001", proveedor="PROVEEDOR ALFA SAS")
        self.crear_linea("F-001", referencia="COMUN-1", descripcion="ARTICULO ALFA")
        self.crear_factura("F-002", proveedor="PROVEEDOR BETA SAS")
        self.crear_linea("F-002", referencia="COMUN-1", descripcion="ARTICULO BETA")

        resultado = self.buscar(referencia="COMUN-1")
        self.assertEqual(resultado["total"], 2)

        solo_alfa = self.buscar(referencia="COMUN-1", proveedor="ALFA")
        self.assertEqual(solo_alfa["total"], 1)
        self.assertEqual(solo_alfa["filas"][0]["proveedor"], "PROVEEDOR ALFA SAS")

    def test_referencia_original_y_corregida(self):
        self.crear_factura("F-001")
        self.crear_linea(
            "F-001",
            referencia="CORREGIDA-1",
            referencia_original="ORIGINAL-1",
            referencia_corregida="CORREGIDA-1",
        )

        por_original = self.buscar(referencia="ORIGINAL-1")
        self.assertEqual(por_original["total"], 1)
        fila = por_original["filas"][0]
        self.assertEqual(fila["referencia_original_mostrar"], "ORIGINAL-1")
        self.assertEqual(fila["referencia_corregida_mostrar"], "CORREGIDA-1")

        por_corregida = self.buscar(referencia="CORREGIDA-1")
        self.assertEqual(por_corregida["total"], 1)

    def test_referencia_en_consolidado_y_equivalente(self):
        self.crear_factura("F-001")
        id_cons = self.crear_consolidado(
            referencia="REF-PROV-1",
            equivalente="EQ-777",
            referencia_repuesto="REP-555",
        )
        self.crear_linea("F-001", referencia="REF-PROV-1", id_consolidado=id_cons)

        self.assertEqual(self.buscar(referencia="EQ-777")["total"], 1)
        self.assertEqual(self.buscar(referencia="REP-555")["total"], 1)


class TestEstadosBusqueda(BaseBusqueda):
    def test_pendiente_permitido(self):
        self.crear_factura("F-001")
        self.crear_linea("F-001", referencia="REF-001")
        self.crear_consolidado(abrev="ALFA", referencia="REF-001")

        fila = self.buscar(referencia="REF-001")["filas"][0]

        self.assertEqual(fila["estado_inventariado"], "PENDIENTE_PERMITIDO")
        self.assertEqual(fila["estado_consolidado"], "CONSOLIDADO")

    def test_pendiente_no_permitido(self):
        self.crear_factura("F-001")
        self.crear_linea("F-001", referencia="SIN-MATCH")

        fila = self.buscar(referencia="SIN-MATCH")["filas"][0]

        self.assertEqual(fila["estado_inventariado"], "PENDIENTE_NO_PERMITIDO")
        self.assertEqual(fila["estado_consolidado"], "NO_CONSOLIDADO")

    def test_consolidado_sin_inventariar(self):
        self.crear_factura("F-001")
        self.crear_linea("F-001", referencia="REF-001")
        self.crear_consolidado(abrev="ALFA", referencia="REF-001")

        fila = self.buscar(estado="consolidado")["filas"][0]
        self.assertEqual(fila["estado_consolidado"], "CONSOLIDADO")
        self.assertEqual(fila["estado_inventariado"], "PENDIENTE_PERMITIDO")

    def test_linea_ya_inventariada(self):
        id_item = self.crear_articulo_inventario()
        self.crear_factura("F-001")
        self.crear_linea(
            "F-001",
            referencia="REF-001",
            inventariado=1,
            id_inventario=id_item,
        )

        fila = self.buscar(estado="inventariado")["filas"][0]

        self.assertEqual(fila["estado_inventariado"], "INVENTARIADO")
        self.assertEqual(fila["codigoBarras"], f"{id_item:08d}")

    def test_filtros_de_estado_backend(self):
        self.crear_factura("F-001")
        self.crear_linea("F-001", referencia="P-1")
        self.crear_consolidado(abrev="ALFA", referencia="P-1")

        self.crear_factura("F-002")
        self.crear_linea("F-002", referencia="P-2")

        self.assertEqual(self.buscar(estado="pendiente_permitido")["total"], 1)
        self.assertEqual(self.buscar(estado="pendiente_no_permitido")["total"], 1)
        self.assertEqual(self.buscar(estado="consolidado")["total"], 1)
        self.assertEqual(self.buscar(estado="no_consolidado")["total"], 1)
        self.assertEqual(self.buscar(estado="todos")["total"], 2)

    def test_estado_del_navegador_no_se_usa(self):
        self.crear_factura("F-001")
        self.crear_linea("F-001", referencia="REF-001")

        filtros = busqueda.extraer_filtros(
            {"estado": "inventariado", "estado_oculto": "INVENTARIADO"}
        )

        self.assertEqual(filtros["estado"], "inventariado")


class TestNavegacionDetalle(BaseBusqueda):
    def test_detalle_abre_en_la_fila_correcta(self):
        self.crear_factura("F-001")
        self.crear_linea("F-001", referencia="REF-001", descripcion="PRIMERA")
        linea = self.crear_linea("F-001", referencia="REF-002", descripcion="SEGUNDA")

        with patch("db.ruta_bd_activa", return_value=self.ruta):
            respuesta = self._cliente().get(
                f"/facturas/ver/F-001?linea={linea}"
                "&return_to=/facturas/buscar-articulos%3Freferencia%3DREF-002"
            )

        self.assertEqual(respuesta.status_code, 200)
        html = respuesta.get_data(as_text=True)
        self.assertIn(f'id="item-{linea}"', html)
        self.assertIn("fila-destacada", html)
        self.assertIn("Volver a resultados", html)

    def test_regreso_conserva_filtros_y_pagina(self):
        self.crear_factura("F-001")
        for numero in range(55):
            self.crear_linea(
                "F-001",
                referencia=f"REF-{numero:03d}",
                descripcion=f"ARTICULO {numero:03d}",
            )

        return_to = "/facturas/buscar-articulos?descripcion=articulo&pagina=2"
        from urllib.parse import quote

        with patch("db.ruta_bd_activa", return_value=self.ruta):
            respuesta = self._cliente().get(
                "/facturas/ver/F-001?linea=1&return_to=" + quote(return_to, safe="")
            )

        html = respuesta.get_data(as_text=True)
        from urllib.parse import unquote

        html_decodificado = unquote(html)
        self.assertIn("Volver a resultados", html_decodificado)
        self.assertIn("descripcion=articulo", html_decodificado)
        self.assertIn("pagina=2", html_decodificado)

    def test_linea_que_no_pertenece_a_la_factura_no_se_resalta(self):
        self.crear_factura("F-001")
        self.crear_linea("F-001", referencia="REF-001")
        self.crear_factura("F-002")
        linea_ajena = self.crear_linea("F-002", referencia="REF-002")

        with patch("db.ruta_bd_activa", return_value=self.ruta):
            respuesta = self._cliente().get(
                f"/facturas/ver/F-001?linea={linea_ajena}"
            )

        self.assertEqual(respuesta.status_code, 200)
        html = respuesta.get_data(as_text=True)
        self.assertNotIn("fila-destacada", html)
        self.assertNotIn(f'id="item-{linea_ajena}"', html)

    def test_return_to_externo_es_rechazado(self):
        self.crear_factura("F-001")
        self.crear_linea("F-001", referencia="REF-001")

        destinos = [
            "https://evil.example.com/",
            "//evil.example.com/",
            "/otra-ruta?x=1",
            "\\\\evil\\compartido",
        ]

        from facturas.routes import return_to_seguro

        for destino in destinos:
            self.assertIsNone(return_to_seguro(destino))
        self.assertEqual(
            return_to_seguro("/facturas/buscar-articulos?referencia=X"),
            "/facturas/buscar-articulos?referencia=X",
        )

        with patch("db.ruta_bd_activa", return_value=self.ruta):
            respuesta = self._cliente().get(
                "/facturas/ver/F-001?return_to=https://evil.example.com/"
            )
        self.assertNotIn("Volver a resultados", respuesta.get_data(as_text=True))


class TestPaginaYConsulta(BaseBusqueda):
    def test_paginacion_con_muchos_registros(self):
        self.crear_factura("F-001")
        for numero in range(120):
            self.crear_linea(
                "F-001",
                referencia=f"REF-{numero:03d}",
                descripcion=f"ARTICULO NUMERO {numero:03d}",
            )

        primera = self.buscar(descripcion="articulo")
        self.assertEqual(primera["total"], 120)
        self.assertEqual(primera["por_pagina"], 50)
        self.assertEqual(len(primera["filas"]), 50)
        self.assertEqual(primera["total_paginas"], 3)

        segunda = busqueda.buscar_articulos(
            self.ruta,
            busqueda.extraer_filtros({"descripcion": "articulo"}),
            pagina=2,
        )
        self.assertEqual(len(segunda["filas"]), 50)
        self.assertEqual(segunda["pagina"], 2)
        self.assertEqual(segunda["desde"], 51)
        self.assertEqual(segunda["hasta"], 100)

        tercera = busqueda.buscar_articulos(
            self.ruta,
            busqueda.extraer_filtros({"descripcion": "articulo"}),
            pagina=99,
        )
        self.assertEqual(tercera["pagina"], 3)
        self.assertEqual(len(tercera["filas"]), 20)

    def test_ruta_buscar_articulos_pagina(self):
        self.crear_factura("F-001")
        for numero in range(60):
            self.crear_linea(
                "F-001",
                referencia=f"REF-{numero:03d}",
                descripcion=f"ARTICULO NUMERO {numero:03d}",
            )

        with patch("db.ruta_bd_activa", return_value=self.ruta):
            respuesta = self._cliente().get(
                "/facturas/buscar-articulos?descripcion=articulo&pagina=2"
            )

        self.assertEqual(respuesta.status_code, 200)
        html = respuesta.get_data(as_text=True)
        self.assertIn("60 coincidencias", html)
        self.assertIn("Página 2 de 2", html)
        self.assertIn("tabla-buscar-articulos", html)
        self.assertIn("Ver factura", html)

    def test_busqueda_no_modifica_datos(self):
        self.crear_factura("F-001")
        self.crear_linea("F-001", referencia="REF-001")
        self.crear_consolidado(abrev="ALFA", referencia="REF-001")
        id_item = self.crear_articulo_inventario()
        self.crear_linea("F-001", referencia="REF-002", inventariado=1, id_inventario=id_item)

        def conteos():
            conn = self.conectar()
            try:
                return {
                    tabla: conn.execute(f"SELECT COUNT(*) FROM {tabla}").fetchone()[0]
                    for tabla in (
                        "facturas",
                        "inventarioFacturas",
                        "consolidado",
                        "inventarioUnico",
                    )
                }
            finally:
                conn.close()

        antes = conteos()
        self.buscar(descripcion="bomba")
        self.buscar(referencia="REF")
        self.buscar(estado="todos")
        despues = conteos()

        self.assertEqual(antes, despues)


if __name__ == "__main__":
    unittest.main()
