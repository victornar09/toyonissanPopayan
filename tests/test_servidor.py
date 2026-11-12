"""Pruebas de servidor local, seguridad, SQLite WAL, impresión y respaldos."""

import json
import csv
import os
import shutil
import sqlite3
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from flask import Flask

import configuracion_servidor
import db
import migraciones
import respaldo_sistema
import seguridad
from impresion import servicio as impresion


RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ESTRUCTURA = os.path.join(RAIZ, "database", "estructura.sql")


def crear_base(ruta):
    with open(ESTRUCTURA, "r", encoding="utf-8") as archivo:
        sql = archivo.read()
    conn = sqlite3.connect(ruta)
    conn.executescript(sql)
    migraciones.migrar(conn)
    conn.commit()
    conn.close()


class BaseServidor(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="servidor_test_")
        self.ruta = os.path.join(self.dir, "test.sqlite")
        crear_base(self.ruta)

        self.temp_impresion = os.path.join(self.dir, "temp")
        parche_temp = patch.object(impresion, "TEMPORAL_DIR", self.temp_impresion)
        parche_temp.start()
        self.addCleanup(parche_temp.stop)

        self.csv_compartido = os.path.join(self.dir, "productos_temp.csv")
        parche_csv = patch.object(
            impresion,
            "_ruta_csv_compartido",
            return_value=self.csv_compartido,
        )
        parche_csv.start()
        self.addCleanup(parche_csv.stop)

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)


# ---------------------------------------------------------------------------
# 1. Configuración de servidor
# ---------------------------------------------------------------------------

class TestConfiguracionServidor(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="config_servidor_")
        parches = [
            patch.object(configuracion_servidor, "CONFIG_DIR", self.dir),
            patch.object(
                configuracion_servidor,
                "RUTA_CONFIG",
                os.path.join(self.dir, "servidor.json"),
            ),
            patch.object(
                configuracion_servidor,
                "RUTA_ESTADO",
                os.path.join(self.dir, "estado_servidor.json"),
            ),
        ]
        for parche in parches:
            parche.start()
            self.addCleanup(parche.stop)

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_puerto_invalido_y_host_publico(self):
        _, errores = configuracion_servidor.validar_configuracion(
            {"host": "0.0.0.0", "puerto": 80, "hilos": 6}
        )
        self.assertTrue(any("1024" in e for e in errores))

        _, errores = configuracion_servidor.validar_configuracion(
            {"host": "8.8.8.8", "puerto": 5000, "hilos": 6}
        )
        self.assertTrue(any("pública" in e for e in errores))

        _, errores = configuracion_servidor.validar_configuracion(
            {"host": "192.168.1.50", "puerto": 5000, "hilos": 2}
        )
        self.assertTrue(any("hilos" in e for e in errores))

    def test_configuracion_se_guarda_y_sobrevive_reinicio(self):
        configuracion, errores = configuracion_servidor.guardar_configuracion(
            {
                "nombre_servidor": "TOYO-SERVER",
                "host": "0.0.0.0",
                "puerto": 5055,
                "hilos": 6,
                "ip_local": "192.168.1.99",
            }
        )
        self.assertEqual(errores, [])
        self.assertEqual(configuracion["puerto"], 5055)

        with open(configuracion_servidor.RUTA_CONFIG, "r", encoding="utf-8") as archivo:
            guardado = json.load(archivo)

        self.assertNotIn("ip_local", guardado)
        self.assertEqual(guardado["host"], "0.0.0.0")
        self.assertFalse(guardado["debug"])

        recargada = configuracion_servidor.cargar_configuracion()
        self.assertEqual(recargada["nombre_servidor"], "TOYO-SERVER")
        self.assertEqual(recargada["puerto"], 5055)

    def test_url_para_clientes_usa_ip_privada(self):
        configuracion = {
            "host": "0.0.0.0",
            "puerto": 5000,
        }
        url = configuracion_servidor.url_clientes(configuracion, ip_local="192.168.1.50")
        self.assertEqual(url, "http://192.168.1.50:5000")


# ---------------------------------------------------------------------------
# 2. Seguridad: LAN y CSRF
# ---------------------------------------------------------------------------

class TestSeguridad(unittest.TestCase):
    def _app(self):
        app = Flask(__name__)
        app.secret_key = "test-secret"
        seguridad.configurar_seguridad(app)

        @app.route("/", methods=["GET", "POST"])
        def inicio():
            if request_metodo() == "GET":
                return seguridad.generar_token_csrf()
            return "ok"

        def request_metodo():
            from flask import request

            return request.method

        return app

    def test_ip_publica_bloqueada(self):
        app = self._app()
        cliente = app.test_client()
        with cliente.session_transaction() as sesion:
            sesion["usuario"] = "tester"
        respuesta = cliente.get("/", environ_base={"REMOTE_ADDR": "8.8.8.8"})
        self.assertEqual(respuesta.status_code, 403)

    def test_ip_privada_permitida(self):
        app = self._app()
        cliente = app.test_client()
        with cliente.session_transaction() as sesion:
            sesion["usuario"] = "tester"
        respuesta = cliente.get("/", environ_base={"REMOTE_ADDR": "192.168.1.20"})
        self.assertEqual(respuesta.status_code, 200)

    def test_csrf_requerido_y_aceptado(self):
        app = self._app()
        app.testing = False
        cliente = app.test_client()
        with cliente.session_transaction() as sesion:
            sesion["usuario"] = "tester"

        token = cliente.get(
            "/", environ_base={"REMOTE_ADDR": "192.168.1.20"}
        ).get_data(as_text=True)

        sin_token = cliente.post(
            "/", data={}, environ_base={"REMOTE_ADDR": "192.168.1.20"}
        )
        self.assertEqual(sin_token.status_code, 400)

        con_token = cliente.post(
            "/",
            data={"csrf_token": token},
            environ_base={"REMOTE_ADDR": "192.168.1.20"},
        )
        self.assertEqual(con_token.status_code, 200)

    def test_sin_sesion_redirige_al_login(self):
        app = self._app()
        cliente = app.test_client()
        respuesta = cliente.get(
            "/", environ_base={"REMOTE_ADDR": "192.168.1.20"}
        )
        self.assertEqual(respuesta.status_code, 302)
        self.assertIn("/login", respuesta.headers.get("Location", ""))

    def test_token_de_correo_se_cifra_y_descifra(self):
        cifrado = seguridad.cifrar_token("token-secreto")
        self.assertTrue(cifrado.startswith("enc:v1:"))
        self.assertNotIn("token-secreto", cifrado)
        self.assertEqual(seguridad.descifrar_token(cifrado), "token-secreto")
        self.assertEqual(seguridad.descifrar_token("texto-antiguo"), "texto-antiguo")


# ---------------------------------------------------------------------------
# 3. SQLite WAL y concurrencia
# ---------------------------------------------------------------------------

class TestSQLiteWAL(BaseServidor):
    def test_pragmas_de_concurrencia(self):
        conn = db.conectar(self.ruta)
        try:
            self.assertEqual(conn.execute("PRAGMA foreign_keys").fetchone()[0], 1)
            self.assertEqual(
                str(conn.execute("PRAGMA journal_mode").fetchone()[0]).lower(),
                "wal",
            )
            self.assertEqual(conn.execute("PRAGMA busy_timeout").fetchone()[0], 30000)
        finally:
            conn.close()

    def test_escrituras_concurrentes(self):
        errores = []

        def escribir(numero):
            try:
                conn = db.conectar(self.ruta)
                try:
                    conn.execute(
                        """
                        INSERT INTO inventarioUnico
                            (descripcion, descripcionClave, proveedor,
                             fechaActualizacion, cantidad)
                        VALUES (?, ?, 'TEST', date('now'), 1)
                        """,
                        (f"ARTICULO {numero}", f"articulo {numero}"),
                    )
                    conn.commit()
                finally:
                    conn.close()
            except Exception as error:
                errores.append(error)

        hilos = [threading.Thread(target=escribir, args=(i,)) for i in range(8)]
        for hilo in hilos:
            hilo.start()
        for hilo in hilos:
            hilo.join()

        self.assertEqual(errores, [])
        conn = db.conectar(self.ruta)
        try:
            total = conn.execute("SELECT COUNT(*) FROM inventarioUnico").fetchone()[0]
        finally:
            conn.close()
        self.assertEqual(total, 8)


# ---------------------------------------------------------------------------
# 4. Cola de impresión
# ---------------------------------------------------------------------------

class TestColaImpresion(BaseServidor):
    def crear_trabajo(self, codigo="00000001", clave=None):
        return impresion.crear_trabajo(
            self.ruta,
            usuario="tester",
            articulos=[
                {
                    "codigo": codigo,
                    "descripcion": "PASTILLA FRENO",
                    "precio": "10.000",
                    "cantidad": 1,
                }
            ],
            id_plantilla=1,
            origen="prueba",
            clave_unica=clave,
        )

    def test_dos_solicitudes_cercanas_archivos_unicos(self):
        trabajos = [self.crear_trabajo(codigo=str(i)) for i in (1, 2)]
        rutas = []

        def motor(ruta_csv, ruta_plantilla):
            rutas.append(ruta_csv)
            time.sleep(0.05)

        for _ in trabajos:
            resultado = impresion.procesar_siguiente(self.ruta, motor=motor)
            self.assertEqual(resultado["estado"], impresion.IMPRESO)

        self.assertEqual(len(rutas), 2)
        self.assertEqual(len(set(rutas)), 2)
        for ruta in rutas:
            self.assertTrue(os.path.isfile(ruta))
            nombre = os.path.basename(ruta)
            self.assertTrue(nombre.startswith("impresion_"))
            self.assertTrue(nombre.endswith(".csv"))

    def test_actualiza_csv_compartido_antes_de_imprimir(self):
        self.crear_trabajo(codigo="00000042")

        def motor(ruta_csv, ruta_plantilla):
            # El CSV compartido ya debe tener los valores de este trabajo.
            with open(self.csv_compartido, newline="", encoding="utf-8") as archivo:
                filas = list(csv.reader(archivo))
            self.assertEqual(filas[0][0], "codigo")
            self.assertEqual(filas[1][0], "00000042")
            self.assertTrue(os.path.isfile(ruta_csv))

        resultado = impresion.procesar_siguiente(self.ruta, motor=motor)
        self.assertEqual(resultado["estado"], impresion.IMPRESO)

    def test_doble_clic_no_crea_dos_trabajos(self):
        primero = self.crear_trabajo(clave="misma-clave")
        segundo = self.crear_trabajo(clave="misma-clave")
        self.assertFalse(primero["duplicado"])
        self.assertTrue(segundo["duplicado"])
        self.assertEqual(primero["idTrabajo"], segundo["idTrabajo"])

        conn = db.conectar(self.ruta)
        try:
            total = conn.execute("SELECT COUNT(*) FROM trabajoImpresion").fetchone()[0]
        finally:
            conn.close()
        self.assertEqual(total, 1)

    def test_error_conserva_trabajo_y_permite_reintentar(self):
        trabajo = self.crear_trabajo()

        def motor_falla(ruta_csv, ruta_plantilla):
            raise RuntimeError("BarTender no responde")

        resultado = impresion.procesar_siguiente(self.ruta, motor=motor_falla)
        self.assertEqual(resultado["estado"], impresion.ERROR)
        self.assertIn("BarTender", resultado["mensajeError"])
        self.assertEqual(resultado["intentos"], 1)

        reintentado = impresion.reintentar_trabajo(
            self.ruta, trabajo["idTrabajo"]
        )
        self.assertEqual(reintentado["estado"], impresion.PENDIENTE)

        def motor_ok(ruta_csv, ruta_plantilla):
            return None

        resultado = impresion.procesar_siguiente(self.ruta, motor=motor_ok)
        self.assertEqual(resultado["estado"], impresion.IMPRESO)
        self.assertEqual(resultado["intentos"], 2)

    def test_un_solo_worker_procesa_un_trabajo(self):
        self.crear_trabajo()
        resultados = []
        barrera = threading.Barrier(2)

        def motor(ruta_csv, ruta_plantilla):
            time.sleep(0.15)

        def procesar():
            barrera.wait()
            resultados.append(impresion.procesar_siguiente(self.ruta, motor=motor))

        hilos = [threading.Thread(target=procesar) for _ in range(2)]
        for hilo in hilos:
            hilo.start()
        for hilo in hilos:
            hilo.join()

        procesados = [r for r in resultados if r is not None]
        self.assertEqual(len(procesados), 1)
        self.assertEqual(procesados[0]["estado"], impresion.IMPRESO)

    def test_trabajo_colgado_se_marca_error(self):
        trabajo = self.crear_trabajo()
        conn = db.conectar(self.ruta)
        try:
            conn.execute(
                "UPDATE trabajoImpresion SET estado = ? WHERE idTrabajo = ?",
                (impresion.PROCESANDO, trabajo["idTrabajo"]),
            )
            conn.commit()
        finally:
            conn.close()

        recuperados = impresion.recuperar_colgados(self.ruta)
        self.assertEqual(recuperados, 1)
        actual = impresion.obtener_trabajo(self.ruta, trabajo["idTrabajo"])
        self.assertEqual(actual["estado"], impresion.ERROR)


# ---------------------------------------------------------------------------
# 5. Respaldos
# ---------------------------------------------------------------------------

class TestRespaldos(BaseServidor):
    def test_respaldo_consistente_de_base_y_recursos(self):
        for carpeta in ("bin/xml", "bin/pdf", "bin/codigos", "bartenderFiles", "config"):
            os.makedirs(os.path.join(self.dir, carpeta), exist_ok=True)
        with open(os.path.join(self.dir, "bin/xml", "F1.xml"), "w", encoding="utf-8") as archivo:
            archivo.write("<factura/>")
        with open(os.path.join(self.dir, "config", "servidor.json"), "w", encoding="utf-8") as archivo:
            archivo.write("{}")

        conn = db.conectar(self.ruta)
        try:
            conn.execute(
                """
                INSERT INTO inventarioUnico
                    (descripcion, descripcionClave, proveedor,
                     fechaActualizacion, cantidad)
                VALUES ('RESPALDO', 'respaldo', 'TEST', date('now'), 3)
                """
            )
            conn.commit()
        finally:
            conn.close()

        carpeta = respaldo_sistema.crear_respaldo(
            base_dir=self.dir, ruta_db=self.ruta, retencion=0
        )

        self.assertTrue(os.path.isfile(os.path.join(carpeta, "manifiesto.json")))
        self.assertTrue(os.path.isfile(os.path.join(carpeta, "bin", "xml", "F1.xml")))
        self.assertTrue(
            os.path.isfile(os.path.join(carpeta, "config", "servidor.json"))
        )

        ruta_copia = os.path.join(carpeta, "bdlocal", os.path.basename(self.ruta))
        self.assertTrue(os.path.isfile(ruta_copia))

        copia = sqlite3.connect(ruta_copia)
        try:
            cantidad = copia.execute(
                "SELECT cantidad FROM inventarioUnico WHERE descripcion = 'RESPALDO'"
            ).fetchone()[0]
        finally:
            copia.close()
        self.assertEqual(cantidad, 3)

    def test_retencion_conserva_solo_los_ultimos(self):
        for indice in range(3):
            respaldo_sistema.crear_respaldo(
                base_dir=self.dir,
                destino=os.path.join(self.dir, "backups", f"respaldo_2026-01-0{indice}"),
                ruta_db=self.ruta,
                retencion=0,
            )

        eliminadas = respaldo_sistema.limpiar_respaldos(
            os.path.join(self.dir, "backups"), retencion=1
        )
        self.assertEqual(len(eliminadas), 2)


if __name__ == "__main__":
    unittest.main()
