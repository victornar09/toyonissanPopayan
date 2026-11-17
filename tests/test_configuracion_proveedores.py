"""Pruebas de edición y filtro de proveedores en Configuración."""

import os
import shutil
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

import migraciones


RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ESTRUCTURA = os.path.join(RAIZ, "database", "estructura.sql")


class BaseConfiguracionProveedores(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="config_prov_test_")
        self.ruta = os.path.join(self.dir, "test.sqlite")

        with open(ESTRUCTURA, "r", encoding="utf-8") as archivo:
            sql = archivo.read()

        conn = sqlite3.connect(self.ruta)
        conn.executescript(sql)
        migraciones.migrar(conn)
        self.base_proveedores = conn.execute(
            "SELECT COUNT(*) FROM proveedor"
        ).fetchone()[0]
        conn.commit()
        conn.close()

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def conectar(self):
        return sqlite3.connect(self.ruta)

    def crear_proveedor(
        self,
        nombre="PROVEEDOR UNO SAS",
        nombre2="PROVEEDOR UNO",
        sigla="PU",
        correo="facturas@uno.com",
        prefijo="FE",
        nit="900111222",
        ciudad="Popayan",
        telefono="3001234567",
    ):
        conn = self.conectar()
        cursor = conn.execute(
            """
            INSERT INTO proveedor (
                nombreProveedor, nombreProveedorSecundario, siglas,
                correoFacturas, prefijo, nit, ciudad, telefono
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (nombre, nombre2, sigla, correo, prefijo, nit, ciudad, telefono),
        )
        identificador = cursor.lastrowid
        conn.commit()
        conn.close()
        return identificador

    def fila_proveedor(self, identificador):
        conn = self.conectar()
        conn.row_factory = sqlite3.Row
        fila = conn.execute(
            "SELECT * FROM proveedor WHERE idProveedor = ?",
            (identificador,),
        ).fetchone()
        conn.close()
        return dict(fila) if fila else None

    def contar(self):
        conn = self.conectar()
        total = conn.execute("SELECT COUNT(*) FROM proveedor").fetchone()[0]
        conn.close()
        return total

    def _cliente(self):
        from app import app

        app.config.update(TESTING=True)
        cliente = app.test_client()
        with cliente.session_transaction() as sesion:
            sesion["usuario"] = "tester"
        return cliente

    def _post(self, datos, seguir=False):
        with patch("db.ruta_bd_activa", return_value=self.ruta):
            return self._cliente().post(
                "/configuracion", data=datos, follow_redirects=seguir
            )


class TestEdicionProveedores(BaseConfiguracionProveedores):
    def test_actualizar_proveedor_guarda_cambios_sin_duplicar(self):
        identificador = self.crear_proveedor()

        respuesta = self._post(
            {
                "accion": "actualizar_proveedor",
                "idProveedor": str(identificador),
                "nombre_proveedor": "PROVEEDOR UNO ACTUALIZADO SAS",
                "nombre_proveedor_2": "PROVEEDOR UNO NUEVO",
                "sigla": "  pu2  ",
                "correo_facturas": "nuevo@uno.com",
                "prefijo": "FC",
                "nit": "900999888",
                "ciudad": "Cali",
                "telefono": "3100000000",
            }
        )

        self.assertEqual(respuesta.status_code, 200)
        self.assertEqual(self.contar(), self.base_proveedores + 1)

        fila = self.fila_proveedor(identificador)
        self.assertEqual(fila["nombreProveedor"], "PROVEEDOR UNO ACTUALIZADO SAS")
        self.assertEqual(fila["nombreProveedorSecundario"], "PROVEEDOR UNO NUEVO")
        self.assertEqual(fila["siglas"], "PU2")
        self.assertEqual(fila["correoFacturas"], "nuevo@uno.com")
        self.assertEqual(fila["prefijo"], "FC")
        self.assertEqual(str(fila["nit"]), "900999888")
        self.assertEqual(fila["ciudad"], "Cali")
        self.assertEqual(str(fila["telefono"]), "3100000000")
        self.assertIn(
            "Proveedor actualizado correctamente.",
            respuesta.get_data(as_text=True),
        )

    def test_actualizar_proveedor_inexistente_no_crea_registro(self):
        respuesta = self._post(
            {
                "accion": "actualizar_proveedor",
                "idProveedor": "9999",
                "nombre_proveedor": "FANTASMA SAS",
                "nombre_proveedor_2": "",
                "sigla": "FAN",
                "correo_facturas": "fantasma@test.com",
                "prefijo": "FE",
                "nit": "900000001",
                "ciudad": "",
                "telefono": "",
            }
        )

        self.assertEqual(respuesta.status_code, 200)
        self.assertEqual(self.contar(), self.base_proveedores)
        self.assertIn(
            "ya no existe",
            respuesta.get_data(as_text=True),
        )

    def test_sigla_duplicada_bloquea_actualizacion(self):
        primero = self.crear_proveedor()
        segundo = self.crear_proveedor(
            nombre="PROVEEDOR DOS SAS",
            nombre2="PROVEEDOR DOS",
            sigla="PD",
            correo="facturas@dos.com",
            nit="900222333",
        )

        respuesta = self._post(
            {
                "accion": "actualizar_proveedor",
                "idProveedor": str(primero),
                "nombre_proveedor": "PROVEEDOR UNO SAS",
                "nombre_proveedor_2": "PROVEEDOR UNO",
                "sigla": "pd",
                "correo_facturas": "facturas@uno.com",
                "prefijo": "FE",
                "nit": "900111222",
                "ciudad": "Popayan",
                "telefono": "3001234567",
            }
        )

        self.assertEqual(respuesta.status_code, 200)
        self.assertEqual(self.fila_proveedor(primero)["siglas"], "PU")
        self.assertEqual(self.fila_proveedor(segundo)["siglas"], "PD")
        self.assertIn("ya está asignada", respuesta.get_data(as_text=True))

    def test_registrar_proveedor_nuevo_sigue_funcionando(self):
        respuesta = self._post(
            {
                "accion": "guardar_proveedor",
                "nombre_proveedor": "PROVEEDOR NUEVO SAS",
                "nombre_proveedor_2": "",
                "sigla": " pn ",
                "correo_facturas": "nuevo@proveedor.com",
                "prefijo": "FE",
                "nit": "901111222",
                "ciudad": "Bogota",
                "telefono": "",
            }
        )

        self.assertEqual(respuesta.status_code, 200)
        self.assertEqual(self.contar(), self.base_proveedores + 1)
        conn = self.conectar()
        fila = conn.execute(
            "SELECT * FROM proveedor WHERE nombreProveedor = ?",
            ("PROVEEDOR NUEVO SAS",),
        ).fetchone()
        conn.close()
        self.assertIsNotNone(fila)
        self.assertEqual(fila[1], "PROVEEDOR NUEVO SAS")
        self.assertEqual(fila[3], "PN")
        self.assertIsNone(fila[2])

    def test_campos_obligatorios_bloquean_registro(self):
        respuesta = self._post(
            {
                "accion": "guardar_proveedor",
                "nombre_proveedor": "",
                "sigla": "",
                "correo_facturas": "",
                "prefijo": "",
                "nit": "",
            }
        )

        self.assertEqual(respuesta.status_code, 200)
        self.assertEqual(self.contar(), self.base_proveedores)
        self.assertIn("Complete los campos obligatorios", respuesta.get_data(as_text=True))

    def test_listado_incluye_id_y_filtro(self):
        identificador = self.crear_proveedor()

        with patch("db.ruta_bd_activa", return_value=self.ruta):
            respuesta = self._cliente().get("/configuracion")

        self.assertEqual(respuesta.status_code, 200)
        html = respuesta.get_data(as_text=True)
        self.assertIn(f'data-id="{identificador}"', html)
        self.assertIn('id="filtro-proveedores"', html)
        self.assertIn("btn-editar-proveedor", html)


if __name__ == "__main__":
    unittest.main()
