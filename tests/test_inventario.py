"""Pruebas del módulo Inventario y del conteo físico.

Cubren la interfaz sin teórico/físico, los datos provenientes de Consolidado,
el respaldo CSV, las sesiones de conteo, lecturas, incidencias, deshacer,
pausa/reanudación, finalización, aplicación y prevención de doble aplicación.
"""

import csv
import glob
import os
import shutil
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

import migraciones
from inventario import respaldo, servicio_conteo


RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ESTRUCTURA = os.path.join(RAIZ, "database", "estructura.sql")


class BaseInventario(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="inventario_test_")
        self.ruta = os.path.join(self.dir, "test.sqlite")
        self.respaldos = os.path.join(self.dir, "backups")

        with open(ESTRUCTURA, "r", encoding="utf-8") as archivo:
            sql = archivo.read()

        conn = sqlite3.connect(self.ruta)
        conn.executescript(sql)
        migraciones.migrar(conn)
        conn.commit()
        conn.close()

        conn = self.conectar()
        conn.execute(
            """
            INSERT INTO Ubicacion
                (piso, pasillo, numero, tipo, codigoUbicacion)
            VALUES (1, 2, 3, 'E', 'E-2')
            """
        )
        conn.commit()
        self.id_ubicacion = conn.execute(
            "SELECT idUbicacion FROM Ubicacion ORDER BY idUbicacion DESC LIMIT 1"
        ).fetchone()[0]
        conn.close()

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    # ------------------------------------------------------------------
    # Utilidades
    # ------------------------------------------------------------------
    def conectar(self):
        return sqlite3.connect(self.ruta)

    def crear_item(
        self,
        descripcion,
        cantidad=5,
        estante="E-2",
        ubicacion="P-1 Pllo-2 E-2",
        referencia="REF-001",
        id_ubicacion=None,
        codigo=None,
        **extra,
    ):
        conn = self.conectar()
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO inventarioUnico (
                referencia, descripcion, descripcionClave, cantidad,
                precioVenta, precioVentaCifrado, precioMaxDescuento,
                proveedor, fechaActualizacion, idUbicacion, costo,
                ubicacion, estante, estadoEtiquetado, estadoConteo,
                stockMinimo, fechaCreacion
            )
            VALUES (?, ?, ?, ?, ?, 'x', 0, 'PROVEEDOR TEST', '2026-01-01',
                    ?, ?, ?, ?, 'PENDIENTE', 'SIN_CONTAR', 1, '2026-01-01')
            """,
            (
                referencia,
                descripcion,
                descripcion.upper(),
                cantidad,
                extra.get("precio", 10000),
                self.id_ubicacion if id_ubicacion is None else id_ubicacion,
                extra.get("costo", 5000),
                ubicacion,
                estante,
            ),
        )
        identificador = cursor.lastrowid
        if codigo is not None:
            cursor.execute(
                "UPDATE inventarioUnico SET codigoBarras = ? WHERE id = ?",
                (codigo, identificador),
            )
        conn.commit()
        codigo_final = conn.execute(
            "SELECT codigoBarras FROM inventarioUnico WHERE id = ?",
            (identificador,),
        ).fetchone()[0]
        conn.close()
        return identificador, codigo_final

    def crear_consolidado(self, id_inventario, **campos):
        datos = {
            "abrevProveedor": "PRV",
            "referenciaProveedor": "PROV-001",
            "referenciaRepuesto": "REP-001",
            "equivalente": "EQ-001",
            "descripcion": "DESCRIPCION BASE",
            "descripcionEstandarizada": "DESCRIPCION ESTANDARIZADA",
            "marca": "MARCA ARTICULO",
            "carro": "TOYOTA",
            "modelo": "HILUX",
            "lado": "IZQUIERDO",
            "medida": "20X30",
            "proveedor": "PROVEEDOR TEST",
            "grupo": "FRENOS",
            "idInventarioUnico": id_inventario,
        }
        datos.update(campos)
        conn = self.conectar()
        conn.execute(
            """
            INSERT INTO consolidado (
                abrevProveedor, referenciaProveedor, referenciaRepuesto,
                equivalente, descripcion, descripcionEstandarizada, marca,
                carro, modelo, lado, medida, proveedor, grupo,
                idInventarioUnico
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                datos["abrevProveedor"],
                datos["referenciaProveedor"],
                datos["referenciaRepuesto"],
                datos["equivalente"],
                datos["descripcion"],
                datos["descripcionEstandarizada"],
                datos["marca"],
                datos["carro"],
                datos["modelo"],
                datos["lado"],
                datos["medida"],
                datos["proveedor"],
                datos["grupo"],
                datos["idInventarioUnico"],
            ),
        )
        conn.commit()
        identificador = conn.execute(
            "SELECT id FROM consolidado ORDER BY id DESC LIMIT 1"
        ).fetchone()[0]
        conn.close()
        return identificador

    def iniciar(self, **kwargs):
        datos = {
            "nombre": "Conteo de prueba",
            "responsable": "tester",
            "id_ubicacion": self.id_ubicacion,
            "estante": "E-2",
            "usuario": "tester",
        }
        datos.update(kwargs)
        return servicio_conteo.iniciar_sesion(self.ruta, **datos)

    def finalizar_y_aplicar(self, id_sesion):
        finalizado = servicio_conteo.finalizar_sesion(self.ruta, id_sesion)
        aplicado = servicio_conteo.aplicar_conteo(
            self.ruta, id_sesion, usuario="tester"
        )
        return finalizado, aplicado

    def cantidad(self, id_item):
        conn = self.conectar()
        valor = conn.execute(
            "SELECT cantidad FROM inventarioUnico WHERE id = ?", (id_item,)
        ).fetchone()[0]
        conn.close()
        return valor

    def contar(self, tabla, where="", params=()):
        conn = self.conectar()
        consulta = f"SELECT COUNT(*) FROM {tabla}"
        if where:
            consulta += " WHERE " + where
        total = conn.execute(consulta, params).fetchone()[0]
        conn.close()
        return total

    def _cliente(self):
        from app import app

        app.config.update(TESTING=True)
        cliente = app.test_client()
        with cliente.session_transaction() as sesion:
            sesion["usuario"] = "tester"
        return cliente


class TestInterfazInventario(BaseInventario):
    def test_interfaz_sin_teorico_ni_fisico(self):
        self.crear_item("FILTRO DE ACEITE")

        with patch("db.ruta_bd_activa", return_value=self.ruta):
            respuesta = self._cliente().get("/inventario/")

        self.assertEqual(respuesta.status_code, 200)
        html = respuesta.get_data(as_text=True)
        self.assertNotIn("Generar inventario teórico", html)
        self.assertNotIn("inventariadoTeorico", html)
        self.assertNotIn("info-tile-teorico", html)
        self.assertNotIn("badge-teorico", html)
        self.assertNotIn("badge-fisico", html)
        self.assertIn("Iniciar inventario físico", html)
        self.assertIn("Verificar respaldo", html)
        self.assertIn("Exportar catálogo de códigos", html)

    def test_datos_provenientes_de_consolidado(self):
        id_item, codigo = self.crear_item("FILTRO DE ACEITE")
        self.crear_consolidado(
            id_item,
            descripcionEstandarizada="FILTRO DE ACEITE TOYOTA HILUX",
            referenciaProveedor="PROV-999",
            referenciaRepuesto="REP-999",
            equivalente="EQ-999",
            marca="DENSO",
            carro="TOYOTA",
            modelo="HILUX",
            lado="IZQUIERDO",
            medida="20X30",
            grupo="FILTROS",
        )

        with patch("db.ruta_bd_activa", return_value=self.ruta):
            respuesta = self._cliente().get(
                "/inventario/?codigo=" + codigo
            )

        self.assertEqual(respuesta.status_code, 200)
        html = respuesta.get_data(as_text=True)
        for valor in (
            "FILTRO DE ACEITE TOYOTA HILUX",
            "PROV-999",
            "REP-999",
            "EQ-999",
            "DENSO",
            "HILUX",
            "IZQUIERDO",
            "20X30",
            "FILTROS",
        ):
            self.assertIn(valor, html)


class TestRespaldo(BaseInventario):
    def test_generacion_y_actualizacion_del_respaldo(self):
        id_item, codigo = self.crear_item("BUJIA", referencia="REF-BUJIA")
        id_consolidado = self.crear_consolidado(
            id_item, referenciaProveedor="PROV-BUJIA"
        )

        with patch.object(respaldo, "DIRECTORIO", self.respaldos):
            resultado = respaldo.generar_respaldo(
                self.ruta, directorio=self.respaldos
            )
            self.assertTrue(resultado["ok"])
            principal = resultado["ruta_principal"]
            self.assertTrue(os.path.isfile(principal))

            with open(principal, "r", encoding="utf-8-sig", newline="") as f:
                filas = list(csv.DictReader(f))
            self.assertEqual(len(filas), 1)
            self.assertEqual(filas[0]["codigoBarras"], codigo)
            self.assertEqual(filas[0]["referenciaProveedor"], "PROV-BUJIA")
            self.assertEqual(str(filas[0]["idConsolidado"]), str(id_consolidado))
            for campo in respaldo.CAMPOS:
                self.assertIn(campo, filas[0])

            conn = self.conectar()
            conn.execute(
                "UPDATE consolidado SET referenciaProveedor = 'PROV-NUEVA' "
                "WHERE idInventarioUnico = ?",
                (id_item,),
            )
            conn.commit()
            conn.close()

            respaldo.generar_respaldo(self.ruta, directorio=self.respaldos)
            with open(principal, "r", encoding="utf-8-sig", newline="") as f:
                actualizado = list(csv.DictReader(f))
            self.assertEqual(
                actualizado[0]["referenciaProveedor"], "PROV-NUEVA"
            )
            historicos = glob.glob(
                os.path.join(
                    self.respaldos, "catalogo_codigos_barras_*.csv"
                )
            )
            self.assertGreaterEqual(len(historicos), 2)

    def test_deteccion_de_codigos_duplicados(self):
        _, codigo = self.crear_item("ARTICULO UNO", codigo="00001111")
        self.crear_item("ARTICULO DOS", codigo="00001111")

        respaldo.generar_respaldo(self.ruta, directorio=self.respaldos)
        resultado = respaldo.verificar_respaldo(
            self.ruta, directorio=self.respaldos
        )

        self.assertFalse(resultado["ok"])
        self.assertTrue(resultado["duplicados_sqlite"])
        self.assertTrue(
            any("repetido" in error for error in resultado["errores"])
        )

    def test_recuperacion_de_codigo_y_referencia_desde_respaldo(self):
        id_item, codigo = self.crear_item("MANGUERA", referencia="MANG-1")
        self.crear_consolidado(
            id_item,
            referenciaProveedor="PROV-MANG",
            referenciaRepuesto="REP-MANG",
        )
        respaldo.generar_respaldo(self.ruta, directorio=self.respaldos)

        relaciones = respaldo.recuperar_relaciones(self.respaldos)
        self.assertIn(codigo, relaciones)
        self.assertEqual(
            relaciones[codigo]["referenciaProveedor"], "PROV-MANG"
        )
        self.assertEqual(
            relaciones[codigo]["referenciaRepuesto"], "REP-MANG"
        )


class TestConteo(BaseInventario):
    def test_seleccion_de_ubicacion(self):
        ubicaciones = servicio_conteo.listar_ubicaciones(self.ruta)
        self.assertEqual(ubicaciones[0]["idUbicacion"], self.id_ubicacion)

        sin_ubicacion = servicio_conteo.iniciar_sesion(
            self.ruta, responsable="tester"
        )
        self.assertFalse(sin_ubicacion["ok"])

        con_ubicacion = self.iniciar()
        self.assertTrue(con_ubicacion["ok"])
        self.assertEqual(
            con_ubicacion["sesion"]["idUbicacion"], self.id_ubicacion
        )

    def test_lectura_de_codigo_valido(self):
        id_item, codigo = self.crear_item("PASTILLA", cantidad=5)
        sesion = self.iniciar()

        resultado = servicio_conteo.leer_codigo(
            self.ruta, sesion["idSesion"], codigo
        )

        self.assertTrue(resultado["ok"])
        self.assertTrue(resultado["encontrado"])
        self.assertEqual(resultado["tipo"], "OK")
        self.assertEqual(resultado["cantidadContada"], 1)
        self.assertEqual(self.cantidad(id_item), 5)

    def test_varias_lecturas_del_mismo_producto(self):
        _, codigo = self.crear_item("ACEITE", cantidad=10)
        sesion = self.iniciar()

        primera = servicio_conteo.leer_codigo(
            self.ruta, sesion["idSesion"], codigo
        )
        segunda = servicio_conteo.leer_codigo(
            self.ruta, sesion["idSesion"], codigo
        )

        self.assertEqual(primera["cantidadContada"], 1)
        self.assertEqual(segunda["cantidadContada"], 2)
        self.assertEqual(segunda["totalProductos"], 1)
        self.assertEqual(segunda["totalLecturas"], 2)

    def test_codigo_desconocido_no_crea_articulo(self):
        sesion = self.iniciar()
        antes = self.contar("inventarioUnico")

        resultado = servicio_conteo.leer_codigo(
            self.ruta, sesion["idSesion"], "99999999"
        )

        self.assertTrue(resultado["ok"])
        self.assertFalse(resultado["encontrado"])
        self.assertEqual(resultado["tipo"], "DESCONOCIDO")
        self.assertEqual(self.contar("inventarioUnico"), antes)

        estado = servicio_conteo.obtener_sesion(
            self.ruta, sesion["idSesion"]
        )
        self.assertEqual(len(estado["desconocidos"]), 1)
        self.assertEqual(
            estado["desconocidos"][0]["codigoBarras"], "99999999"
        )

    def test_producto_ubicado_en_otro_estante(self):
        _, codigo = self.crear_item(
            "AMORTIGUADOR", estante="E-9", id_ubicacion=999
        )
        sesion = self.iniciar()

        resultado = servicio_conteo.leer_codigo(
            self.ruta, sesion["idSesion"], codigo
        )

        self.assertTrue(resultado["ok"])
        self.assertEqual(resultado["tipo"], "UBICACION_INCORRECTA")
        self.assertTrue(resultado["advertencia"])
        self.assertEqual(resultado["cantidadContada"], 1)

    def test_deshacer_ultima_lectura(self):
        _, codigo = self.crear_item("CORREA", cantidad=3)
        sesion = self.iniciar()
        servicio_conteo.leer_codigo(self.ruta, sesion["idSesion"], codigo)
        segunda = servicio_conteo.leer_codigo(
            self.ruta, sesion["idSesion"], codigo
        )
        self.assertEqual(segunda["cantidadContada"], 2)

        resultado = servicio_conteo.deshacer_ultima_lectura(
            self.ruta, sesion["idSesion"]
        )

        self.assertTrue(resultado["ok"])
        self.assertEqual(resultado["cantidadContada"], 1)
        self.assertEqual(
            self.contar(
                "lecturaConteo",
                "idSesion = ? AND anulada = 1",
                (sesion["idSesion"],),
            ),
            1,
        )

    def test_pausar_y_reanudar(self):
        _, codigo = self.crear_item("BOMBA", cantidad=1)
        sesion = self.iniciar()

        pausada = servicio_conteo.pausar_sesion(
            self.ruta, sesion["idSesion"]
        )
        self.assertTrue(pausada["ok"])
        self.assertEqual(pausada["estado"], "PAUSADA")

        rechazada = servicio_conteo.leer_codigo(
            self.ruta, sesion["idSesion"], codigo
        )
        self.assertFalse(rechazada["ok"])

        reanudada = servicio_conteo.reanudar_sesion(
            self.ruta, sesion["idSesion"]
        )
        self.assertTrue(reanudada["ok"])
        self.assertEqual(reanudada["estado"], "ABIERTA")

        lectura = servicio_conteo.leer_codigo(
            self.ruta, sesion["idSesion"], codigo
        )
        self.assertTrue(lectura["ok"])

    def test_finalizar_y_aplicar_conteo(self):
        id_item, codigo = self.crear_item("RADIADOR", cantidad=5)
        sesion = self.iniciar()
        servicio_conteo.leer_codigo(self.ruta, sesion["idSesion"], codigo)
        servicio_conteo.leer_codigo(self.ruta, sesion["idSesion"], codigo)

        finalizado = servicio_conteo.finalizar_sesion(
            self.ruta, sesion["idSesion"]
        )
        self.assertTrue(finalizado["ok"])
        self.assertEqual(finalizado["sesion"]["estado"], "FINALIZADA")
        self.assertEqual(finalizado["resumen"]["cantidadContada"], 2)
        self.assertEqual(finalizado["resumen"]["faltantes"], 1)
        # Finalizar no modifica la cantidad oficial.
        self.assertEqual(self.cantidad(id_item), 5)

        aplicado = servicio_conteo.aplicar_conteo(
            self.ruta, sesion["idSesion"], usuario="tester"
        )
        self.assertTrue(aplicado["ok"])
        self.assertEqual(aplicado["ajustados"], 1)
        self.assertEqual(aplicado["movimientos"], 1)
        self.assertEqual(self.cantidad(id_item), 2)
        self.assertEqual(
            self.contar("movimientoInventario", "tipo = 'AJUSTE_CONTEO'"),
            1,
        )

    def test_pantalla_conteo_renderiza(self):
        _, codigo = self.crear_item("PANTALLA", cantidad=2)
        sesion = self.iniciar()

        with patch("db.ruta_bd_activa", return_value=self.ruta):
            cliente = self._cliente()
            respuesta = cliente.get(
                f"/inventario/conteo/{sesion['idSesion']}"
            )

        self.assertEqual(respuesta.status_code, 200)
        html = respuesta.get_data(as_text=True)
        self.assertIn("Escanea un código de barras", html)
        self.assertIn("campoEscaneo", html)
        self.assertIn("Finalizar conteo", html)
        self.assertIn("Deshacer última lectura", html)

    def test_previene_doble_aplicacion(self):
        id_item, codigo = self.crear_item("FARO", cantidad=4)
        sesion = self.iniciar()
        servicio_conteo.leer_codigo(self.ruta, sesion["idSesion"], codigo)
        servicio_conteo.finalizar_sesion(self.ruta, sesion["idSesion"])

        primera = servicio_conteo.aplicar_conteo(
            self.ruta, sesion["idSesion"]
        )
        segunda = servicio_conteo.aplicar_conteo(
            self.ruta, sesion["idSesion"]
        )

        self.assertTrue(primera["ok"])
        self.assertFalse(segunda["ok"])
        self.assertEqual(self.cantidad(id_item), 1)
        self.assertEqual(self.contar("movimientoInventario"), 1)


if __name__ == "__main__":
    unittest.main()
