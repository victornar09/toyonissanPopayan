"""Pruebas del asistente ION.

Cubren los escenarios mínimos pedidos: sinónimos/populares, referencia exacta,
desambiguación por lado, tracción, cabina, Inventario frente a Consolidado,
errores de escritura, consultas sin resultados y sugerencias pendientes.
"""

import os
import shutil
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

import migraciones
from ion import busqueda, conocimiento, normalizacion


RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ESTRUCTURA = os.path.join(RAIZ, "database", "estructura.sql")


class BaseIon(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="ion_test_")
        self.ruta = os.path.join(self.dir, "test.sqlite")

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
    def conectar(self):
        return sqlite3.connect(self.ruta)

    def crear_item(self, descripcion, referencia, cantidad=3, precio=50000,
                   codigo=None):
        conn = self.conectar()
        cursor = conn.execute(
            """
            INSERT INTO inventarioUnico (
                referencia, descripcion, descripcionClave, cantidad,
                precioVenta, precioVentaCifrado, precioMaxDescuento,
                proveedor, fechaActualizacion, costo, ubicacion, estante,
                estadoEtiquetado, estadoConteo, stockMinimo, fechaCreacion
            )
            VALUES (?, ?, ?, ?, ?, 'x', 0, 'PROVEEDOR TEST', '2026-01-01',
                    0, 'Piso 1 · Estante A', 'A-1', 'PENDIENTE',
                    'SIN_CONTAR', 1, '2026-01-01')
            """,
            (
                referencia,
                descripcion,
                normalizacion.normalizar(descripcion),
                cantidad,
                precio,
            ),
        )
        identificador = cursor.lastrowid
        if codigo is not None:
            conn.execute(
                "UPDATE inventarioUnico SET codigoBarras = ? WHERE id = ?",
                (codigo, identificador),
            )
        conn.commit()
        conn.close()
        return identificador

    def crear_consolidado(self, referencia, descripcion, **campos):
        datos = {
            "abrevProveedor": "PRV",
            "referenciaProveedor": referencia,
            "referenciaRepuesto": "",
            "equivalente": "",
            "descripcion": descripcion,
            "descripcionEstandarizada": descripcion,
            "marca": "",
            "carro": "",
            "modelo": "",
            "tipoMotor": "",
            "lado": "",
            "medida": "",
            "proveedor": "PROVEEDOR CONSOLIDADO",
            "grupo": "",
            "estadoConsolidado": "PENDIENTE_REVISION",
            "idInventarioUnico": None,
        }
        datos.update(campos)
        conn = self.conectar()
        cursor = conn.execute(
            """
            INSERT INTO consolidado (
                abrevProveedor, referenciaProveedor, referenciaRepuesto,
                equivalente, descripcion, descripcionEstandarizada, marca,
                carro, modelo, tipoMotor, lado, medida, proveedor, grupo,
                estadoConsolidado, idInventarioUnico
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                datos["abrevProveedor"], datos["referenciaProveedor"],
                datos["referenciaRepuesto"], datos["equivalente"],
                datos["descripcion"], datos["descripcionEstandarizada"],
                datos["marca"], datos["carro"], datos["modelo"],
                datos["tipoMotor"], datos["lado"], datos["medida"],
                datos["proveedor"], datos["grupo"],
                datos["estadoConsolidado"], datos["idInventarioUnico"],
            ),
        )
        conn.commit()
        identificador = cursor.lastrowid
        conn.close()
        return identificador

    def buscar(self, consulta, criterios=None):
        conn = self.conectar()
        try:
            return busqueda.buscar(conn, consulta, criterios)
        finally:
            conn.close()

    # ------------------------------------------------------------------
    def test_juego_de_anillos_encuentra_anillos(self):
        self.crear_item("ANILLOS MAZDA ALLEGRO 1.6 B6 KIA SEPHIA", "16208")

        resultado = self.buscar("Juego de anillos")

        self.assertEqual(len(resultado["inventario"]), 1)
        self.assertEqual(resultado["inventario"][0]["nivel"], "alta")
        self.assertIn("ANILLOS", resultado["inventario"][0]["descripcion"])

    def test_referencia_exacta_sin_preguntas(self):
        self.crear_item("FAROLA DELANTERA IZQUIERDA HILUX", "REF-EXACTA-9")

        resultado = self.buscar("REF-EXACTA-9")

        self.assertEqual(len(resultado["inventario"]), 1)
        self.assertEqual(resultado["inventario"][0]["puntaje"], 1.0)
        self.assertIsNone(resultado["pregunta"])

    def test_codigo_de_barras_exacto_sin_preguntas(self):
        self.crear_item(
            "FAROLA DELANTERA IZQUIERDA HILUX", "REF-1", codigo="00000123"
        )

        resultado = self.buscar("00000123")

        self.assertEqual(len(resultado["inventario"]), 1)
        self.assertEqual(resultado["inventario"][0]["puntaje"], 1.0)
        self.assertEqual(resultado["inventario"][0]["codigoBarras"], "00000123")
        self.assertIsNone(resultado["pregunta"])

    def test_farola_sin_lado_pregunta_izquierdo_o_derecho(self):
        self.crear_item("FAROLA DELANTERA IZQUIERDA HILUX", "FAR-IZQ")
        self.crear_item("FAROLA DELANTERA DERECHA HILUX", "FAR-DER")

        resultado = self.buscar("farola hilux")

        self.assertEqual(len(resultado["inventario"]), 2)
        self.assertIsNotNone(resultado["pregunta"])
        self.assertEqual(resultado["pregunta"]["campo"], "lado")
        valores = {opcion["valor"] for opcion in resultado["pregunta"]["opciones"]}
        self.assertEqual(valores, {"IZQUIERDO", "DERECHO"})

    def test_respuesta_de_lado_filtra_y_muestra_resultado(self):
        self.crear_item("FAROLA DELANTERA IZQUIERDA HILUX", "FAR-IZQ")
        self.crear_item("FAROLA DELANTERA DERECHA HILUX", "FAR-DER")

        resultado = self.buscar("farola hilux", {"lado": "DERECHO"})

        self.assertEqual(len(resultado["inventario"]), 1)
        self.assertIn("DERECHA", resultado["inventario"][0]["descripcion"])
        self.assertIsNone(resultado["pregunta"])

    def test_diferencia_4x2_y_4x4_genera_pregunta_traccion(self):
        self.crear_item("BUJE BARRA ESTABILIZADORA 4X2", "BUJ-42")
        self.crear_item("BUJE BARRA ESTABILIZADORA 4X4", "BUJ-44")

        resultado = self.buscar("buje barra estabilizadora")

        self.assertIsNotNone(resultado["pregunta"])
        self.assertEqual(resultado["pregunta"]["campo"], "traccion")

    def test_cabina_sencilla_y_doble_genera_pregunta_cabina(self):
        self.crear_item("VIDRIO PUERTA HILUX CABINA SENCILLA", "VID-S")
        self.crear_item("VIDRIO PUERTA HILUX DOBLE CABINA", "VID-D")

        resultado = self.buscar("vidrio hilux")

        self.assertIsNotNone(resultado["pregunta"])
        self.assertEqual(resultado["pregunta"]["campo"], "cabina")

    def test_articulo_disponible_en_inventario(self):
        self.crear_item("FILTRO ACEITE HILUX", "FIL-01", cantidad=4)

        resultado = self.buscar("filtro aceite")

        item = resultado["inventario"][0]
        self.assertTrue(item["disponible"])
        self.assertEqual(item["cantidad"], 4)
        self.assertEqual(item["estado"], "Disponible en Inventario")
        self.assertEqual(item["codigoBarras"], "00000001")

    def test_articulo_solo_en_consolidado_no_se_muestra_disponible(self):
        self.crear_consolidado(
            "CON-777", "FAROLA DELANTERA DERECHA FRONTIER 2015",
            marca="NISSAN", carro="NISSAN", modelo="FRONTIER",
            lado="DERECHO",
        )

        resultado = self.buscar("CON-777")

        self.assertEqual(resultado["inventario"], [])
        self.assertEqual(len(resultado["consolidado"]), 1)
        item = resultado["consolidado"][0]
        self.assertFalse(item["disponible"])
        self.assertIn("no está disponible en Inventario", item["mensaje"])
        self.assertIsNone(resultado["pregunta"])

    def test_consolidado_vinculado_no_duplica_inventario(self):
        id_item = self.crear_item("ANILLOS MAZDA ALLEGRO", "16208")
        self.crear_consolidado(
            "16208", "ANILLOS MAZDA ALLEGRO",
            idInventarioUnico=id_item,
            estadoConsolidado="INVENTARIADO",
        )

        resultado = self.buscar("anillos mazda")

        self.assertEqual(len(resultado["inventario"]), 1)
        self.assertEqual(resultado["consolidado"], [])

    def test_error_ortografico_tolera_similitud(self):
        self.crear_item("ANILLOS MAZDA ALLEGRO 1.6 B6", "16208")

        resultado = self.buscar("anilos")

        self.assertEqual(len(resultado["inventario"]), 1)
        self.assertEqual(resultado["inventario"][0]["nivel"], "alta")

    def test_consulta_sin_resultados(self):
        self.crear_item("ANILLOS MAZDA ALLEGRO", "16208")

        resultado = self.buscar("xyzqwerty sin sentido")

        self.assertTrue(resultado["sinResultados"])
        self.assertEqual(resultado["inventario"], [])
        self.assertEqual(resultado["consolidado"], [])

    def test_sugerencia_sinonimo_queda_pendiente(self):
        conn = self.conectar()
        try:
            sugerencia = conocimiento.sugerir(
                conn, "aro motor", "ANILLOS", usuario="tester",
            )
            conn.commit()

            self.assertTrue(sugerencia["ok"])
            self.assertEqual(sugerencia["estado"], "PENDIENTE")

            aprobados = conocimiento.cargar_sinonimos_aprobados(conn)
            self.assertNotIn("ARO MOTOR", aprobados)

            cambio = conocimiento.cambiar_estado(
                conn, sugerencia["idSinonimo"], "APROBADO", usuario="admin"
            )
            conn.commit()
            self.assertTrue(cambio["ok"])

            aprobados = conocimiento.cargar_sinonimos_aprobados(conn)
            self.assertEqual(aprobados.get("ARO MOTOR"), "ANILLOS")
        finally:
            conn.close()

    def test_indice_fts5_reconstruye_y_cuenta(self):
        self.crear_item("ANILLOS MAZDA ALLEGRO", "16208")
        self.crear_consolidado("CON-1", "FAROLA FRONTIER")

        conn = self.conectar()
        try:
            disponible = busqueda.asegurar_indice(conn, forzar=True)
            conn.commit()
            self.assertTrue(disponible)
            inv, con = busqueda._contar_fuentes(conn)
            self.assertEqual((inv, con), (1, 1))
            total_indice = conn.execute(
                "SELECT COUNT(*) FROM ionIndice"
            ).fetchone()[0]
            self.assertEqual(total_indice, 2)
        finally:
            conn.close()

    def test_pagina_ion_responde(self):
        from app import app

        app.config.update(TESTING=True)
        cliente = app.test_client()
        with cliente.session_transaction() as sesion:
            sesion["usuario"] = "tester"

        with patch("db.ruta_bd_activa", return_value=self.ruta):
            respuesta = cliente.get("/ion/")
            conocimiento_pagina = cliente.get("/ion/conocimiento")

        self.assertEqual(respuesta.status_code, 200)
        html = respuesta.get_data(as_text=True)
        self.assertIn("Describe el repuesto que busca el cliente", html)
        self.assertIn("Disponible en Inventario", html)
        self.assertEqual(conocimiento_pagina.status_code, 200)

    def test_flujo_pregunta_respuesta_y_auditoria(self):
        from app import app

        self.crear_item("FAROLA DELANTERA IZQUIERDA HILUX", "FAR-IZQ")
        self.crear_item("FAROLA DELANTERA DERECHA HILUX", "FAR-DER")

        app.config.update(TESTING=True)
        cliente = app.test_client()
        with cliente.session_transaction() as sesion:
            sesion["usuario"] = "tester"

        with patch("db.ruta_bd_activa", return_value=self.ruta):
            primera = cliente.post(
                "/ion/buscar", json={"consulta": "farola hilux"}
            )
            datos = primera.get_json()
            id_consulta = datos["idConsulta"]

            self.assertIsNotNone(datos["pregunta"])
            self.assertEqual(datos["pregunta"]["campo"], "lado")

            segunda = cliente.post(
                "/ion/buscar",
                json={
                    "consulta": "farola hilux",
                    "idConsulta": id_consulta,
                    "criterios": {},
                    "respuestaCampo": "lado",
                    "respuestaValor": "DERECHO",
                    "respuestaTexto": "Derecho",
                },
            )
            respuesta = segunda.get_json()

        self.assertEqual(segunda.status_code, 200)
        self.assertEqual(respuesta["idConsulta"], id_consulta)
        self.assertEqual(len(respuesta["inventario"]), 1)
        self.assertIn("DERECHA", respuesta["inventario"][0]["descripcion"])
        self.assertIsNone(respuesta["pregunta"])

        conn = self.conectar()
        try:
            consulta = conn.execute(
                "SELECT criterios, respuestas FROM ionConsulta WHERE idConsulta = ?",
                (id_consulta,),
            ).fetchone()
            self.assertIn("DERECHO", consulta[0])
            self.assertIn("DERECHO", consulta[1])

            pregunta = conn.execute(
                """
                SELECT campo, respuesta FROM ionConsultaPregunta
                WHERE idConsulta = ? ORDER BY idPregunta DESC LIMIT 1
                """,
                (id_consulta,),
            ).fetchone()
            self.assertEqual(pregunta[0], "lado")
            self.assertEqual(pregunta[1], "DERECHO")
        finally:
            conn.close()


if __name__ == "__main__":
    unittest.main()
