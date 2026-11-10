"""Pruebas de integración Facturas -> Consolidado -> InventarioUnico.

Cubren los escenarios mínimos acordados: persistencia de campos operativos,
prioridad de valores manuales, actualización sin sobrescribir con vacíos,
recálculo de estados, ambigüedades, referencias corregidas y migraciones
idempotentes.
"""

import os
import shutil
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

import pandas as pd

import migraciones
from app import actualizar_consolidado
from consolidado import servicio
from facturas.validacion_consolidado import obtener_estado_linea
from inventario import respaldo
from procesamiento.consolidado import procesar_dataframe_pendientes
from utilidades import normalizar_clave


RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ESTRUCTURA = os.path.join(RAIZ, "database", "estructura.sql")
ESTANDARIZACION = os.path.join(RAIZ, "bin", "xlsx", "estandarizacion.xlsx")


class BaseIntegracion(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="integracion_test_")
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
            VALUES ('MOTORES JAPONESES SAS', 'MJN', 'facturas@mj.com', 'FE', '900123456')
            """
        )
        conn.commit()
        conn.close()

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def conectar(self):
        return sqlite3.connect(self.ruta)

    def crear_consolidado(
        self,
        referencia="REF-001",
        abrev="MJN",
        estado="LISTO_INVENTARIAR",
        descripcion="PASTILLA FRENO DELANTERA TOYOTA HILUX",
        descripcion_estandarizada=None,
        **extra,
    ):
        datos = {
            "referenciaRepuesto": "REP-01",
            "equivalente": "EQ-01",
            "marca": "GENUINO",
            "carro": "TOYOTA",
            "modelo": "HILUX",
            "lado": "DELANTERO",
            "medida": "050",
            "grupo": "FRENOS",
            "proveedor": "MOTORES JAPONESES SAS",
            "descripcionEstandarizada": (
                descripcion_estandarizada or descripcion
            ),
            "estadoConsolidado": estado,
        }
        datos.update(extra)

        conn = self.conectar()
        columnas = list(datos.keys())
        placeholders = ", ".join("?" for _ in columnas)
        conn.execute(
            f"""
            INSERT INTO consolidado
                (abrevProveedor, referenciaProveedor, descripcion, {', '.join(columnas)})
            VALUES (?, ?, ?, {placeholders})
            """,
            (
                abrev,
                referencia,
                descripcion,
                *[datos[c] for c in columnas],
            ),
        )
        conn.commit()
        identificador = conn.execute(
            "SELECT MAX(id) FROM consolidado"
        ).fetchone()[0]
        conn.close()
        return identificador

    def crear_factura(self, referencia="REF-001", descripcion="PASTILLA FRENO"):
        conn = self.conectar()
        conn.execute(
            """
            INSERT INTO facturas (id_factura, fecha, proveedor, valor_total)
            VALUES ('F1', '2026-01-10', 'MOTORES JAPONESES SAS', 100000)
            """
        )
        cursor = conn.execute(
            """
            INSERT INTO inventarioFacturas
                (id_factura, descripcion_item, cantidad, valor_unitario, referencia)
            VALUES ('F1', ?, 2, 1000, ?)
            """,
            (descripcion, referencia),
        )
        linea_id = cursor.lastrowid
        conn.commit()
        conn.close()
        return linea_id

    def fila_consolidado(self, identificador):
        conn = self.conectar()
        conn.row_factory = sqlite3.Row
        fila = conn.execute(
            "SELECT * FROM consolidado WHERE id = ?", (identificador,)
        ).fetchone()
        conn.close()
        return dict(fila) if fila else None

    def fila_inventario(self, identificador):
        conn = self.conectar()
        conn.row_factory = sqlite3.Row
        fila = conn.execute(
            "SELECT * FROM inventarioUnico WHERE id = ?", (identificador,)
        ).fetchone()
        conn.close()
        return dict(fila) if fila else None

    # ------------------------------------------------------------------
    # 1. Inventariar desde Consolidado guarda todos los campos
    # ------------------------------------------------------------------
    def test_inventariar_consolidado_guarda_campos_operativos(self):
        identificador = self.crear_consolidado()
        resultado = servicio.inventariar_consolidado(self.ruta, [identificador])

        self.assertTrue(resultado["ok"])
        self.assertEqual(resultado["inventariados"], 1)

        fila = self.fila_consolidado(identificador)
        articulo = self.fila_inventario(fila["idInventarioUnico"])

        self.assertEqual(articulo["descripcionEstandarizada"], fila["descripcionEstandarizada"])
        self.assertEqual(articulo["referenciaProveedor"], "REF-001")
        self.assertEqual(articulo["referenciaRepuesto"], "REP-01")
        self.assertEqual(articulo["equivalente"], "EQ-01")
        self.assertEqual(articulo["marcaArticulo"], "GENUINO")
        self.assertEqual(articulo["marcaVehiculo"], "TOYOTA")
        self.assertEqual(articulo["modelo"], "HILUX")
        self.assertEqual(articulo["lado"], "DELANTERO")
        self.assertEqual(articulo["medida"], "050")
        self.assertEqual(articulo["grupo"], "FRENOS")
        self.assertEqual(articulo["proveedor"], "MOTORES JAPONESES SAS")

    # ------------------------------------------------------------------
    # 2. Artículo existente: sólo completa vacíos
    # ------------------------------------------------------------------
    def test_articulo_existente_solo_completa_vacios(self):
        descripcion = "PASTILLA FRENO DELANTERA TOYOTA HILUX"
        clave = normalizar_clave(descripcion)

        conn = self.conectar()
        conn.execute(
            """
            INSERT INTO inventarioUnico (
                descripcion, descripcionClave, descripcionEstandarizada,
                marcaArticulo, referenciaProveedor, proveedor,
                fechaActualizacion, estadoEtiquetado, estadoConteo
            )
            VALUES (?, ?, ?, ?, ?, ?, '2026-01-01', 'PENDIENTE', 'SIN_CONTAR')
            """,
            (
                descripcion,
                clave,
                "DESCRIPCION MANUAL VALIDA",
                "MARCA MANUAL",
                "REF-MANUAL",
                "MOTORES JAPONESES SAS",
            ),
        )
        inventario_id = conn.execute(
            "SELECT MAX(id) FROM inventarioUnico"
        ).fetchone()[0]
        conn.commit()
        conn.close()

        identificador = self.crear_consolidado(descripcion=descripcion)
        servicio.inventariar_consolidado(self.ruta, [identificador])

        articulo = self.fila_inventario(inventario_id)
        self.assertEqual(articulo["descripcionEstandarizada"], "DESCRIPCION MANUAL VALIDA")
        self.assertEqual(articulo["marcaArticulo"], "MARCA MANUAL")
        self.assertEqual(articulo["referenciaProveedor"], "REF-MANUAL")
        # Los campos vacíos sí se completan desde Consolidado.
        self.assertEqual(articulo["equivalente"], "EQ-01")
        self.assertEqual(articulo["modelo"], "HILUX")
        self.assertEqual(articulo["grupo"], "FRENOS")

    # ------------------------------------------------------------------
    # 3. Prioridad de valores manuales en la estandarización
    # ------------------------------------------------------------------
    def test_procesar_dataframe_respeta_valores_manuales(self):
        df = pd.DataFrame([
            {
                "referenciaProveedor": "REF-001",
                "referenciaRepuesto": "REP-MANUAL",
                "equivalente": "EQ-MANUAL",
                "descripcion": "PASTILLA FRENO TOYOTA HILUX DELANTERA",
                "marca": "MARCA-MANUAL",
                "carro": "TOYOTA",
                "lado": "IZQUIERDO",
                "modelo": "MODELO-MANUAL",
                "medida": "STD",
                "grupo": "GRUPO-MANUAL",
            }
        ])
        resultado = procesar_dataframe_pendientes(
            df,
            ESTANDARIZACION,
            campos_manuales={
                "descripcion", "referenciaRepuesto", "equivalente",
                "marca", "carro", "lado", "modelo", "medida", "grupo",
            },
        )
        fila = resultado.iloc[0]
        self.assertEqual(fila["descripcion"], "PASTILLA FRENO TOYOTA HILUX DELANTERA")
        self.assertEqual(fila["lado"], "IZQUIERDO")
        self.assertEqual(fila["modelo"], "MODELO-MANUAL")
        self.assertEqual(fila["medida"], "STD")
        self.assertEqual(fila["referenciaRepuesto"], "REP-MANUAL")
        self.assertEqual(fila["equivalente"], "EQ-MANUAL")

    # ------------------------------------------------------------------
    # 4. Los valores vacíos no sobrescriben datos válidos
    # ------------------------------------------------------------------
    def test_actualizar_consolidado_no_sobrescribe_con_vacios(self):
        identificador = self.crear_consolidado()
        conn = self.conectar()
        df = pd.DataFrame([
            {
                "abrevProveedor": "MJN",
                "referenciaProveedor": "REF-001",
                "idProveedor": None,
                "referenciaRepuesto": "",
                "equivalente": None,
                "descripcion": "",
                "marca": "",
                "carro": "",
                "tipo": "",
                "tipoMotor": "",
                "precioListado": "",
                "proveedor": "",
                "lado": "",
                "ladoAbreviado": "",
                "repuesto": "",
                "modelo": "",
                "otros": "",
                "medida": "",
                "descripcionEstandarizada": "",
                "grupo": "",
            }
        ])
        actualizar_consolidado(conn, df)
        conn.close()

        fila = self.fila_consolidado(identificador)
        self.assertEqual(fila["referenciaRepuesto"], "REP-01")
        self.assertEqual(fila["equivalente"], "EQ-01")
        self.assertEqual(fila["marca"], "GENUINO")
        self.assertEqual(fila["modelo"], "HILUX")
        self.assertEqual(fila["medida"], "050")
        self.assertEqual(fila["descripcion"], "PASTILLA FRENO DELANTERA TOYOTA HILUX")
        self.assertEqual(fila["descripcionEstandarizada"], "PASTILLA FRENO DELANTERA TOYOTA HILUX")

    # ------------------------------------------------------------------
    # 5. Recálculo de estado de registros existentes
    # ------------------------------------------------------------------
    def test_actualizar_consolidado_recalcula_estado(self):
        identificador = self.crear_consolidado(estado="PENDIENTE_REVISION")
        conn = self.conectar()
        df = pd.DataFrame([
            {
                "abrevProveedor": "MJN",
                "referenciaProveedor": "REF-001",
                "idProveedor": None,
                "referenciaRepuesto": "REP-01",
                "equivalente": "EQ-01",
                "descripcion": "PASTILLA FRENO DELANTERA TOYOTA HILUX",
                "marca": "GENUINO",
                "carro": "TOYOTA",
                "tipo": "",
                "tipoMotor": "",
                "precioListado": 0,
                "proveedor": "MOTORES JAPONESES SAS",
                "lado": "DELANTERO",
                "ladoAbreviado": "DEL",
                "repuesto": "PASTILLA FRENO",
                "modelo": "HILUX",
                "otros": "",
                "medida": "050",
                "descripcionEstandarizada": "PASTILLA FRENO DELANTERA TOYOTA HILUX",
                "grupo": "FRENOS",
            }
        ])
        actualizar_consolidado(conn, df)
        conn.close()

        fila = self.fila_consolidado(identificador)
        self.assertEqual(fila["estadoConsolidado"], "LISTO_INVENTARIAR")

    # ------------------------------------------------------------------
    # 6. Llave ambigua: no se elige la primera
    # ------------------------------------------------------------------
    def test_actualizar_consolidado_detecta_llave_ambigua(self):
        self.crear_consolidado(descripcion="ARTICULO UNO")
        self.crear_consolidado(
            referencia="ref-001 ", abrev="mjn ", descripcion="ARTICULO DOS"
        )

        conn = self.conectar()
        df = pd.DataFrame([
            {
                "abrevProveedor": "MJN",
                "referenciaProveedor": "REF-001",
                "idProveedor": None,
                "referenciaRepuesto": "",
                "equivalente": "",
                "descripcion": "ARTICULO NUEVO",
                "marca": "",
                "carro": "",
                "tipo": "",
                "tipoMotor": "",
                "precioListado": "",
                "proveedor": "",
                "lado": "",
                "ladoAbreviado": "",
                "repuesto": "",
                "modelo": "",
                "otros": "",
                "medida": "",
                "descripcionEstandarizada": "",
                "grupo": "",
            }
        ])
        resultado = actualizar_consolidado(conn, df)
        conn.close()

        self.assertEqual(resultado["ambiguos"], 1)
        self.assertEqual(resultado["nuevos"], 0)
        self.assertEqual(resultado["modificados"], 0)

        conn = self.conectar()
        filas = conn.execute(
            "SELECT estadoConsolidado FROM consolidado "
            "WHERE UPPER(TRIM(referenciaProveedor)) = 'REF-001'"
        ).fetchall()
        conn.close()
        self.assertEqual(len(filas), 2)
        self.assertTrue(all(f[0] == "BLOQUEADO" for f in filas))

    # ------------------------------------------------------------------
    # 7. Se devuelven los ids para vincular la línea de factura
    # ------------------------------------------------------------------
    def test_actualizar_consolidado_devuelve_ids_por_clave(self):
        identificador = self.crear_consolidado()
        conn = self.conectar()
        df = pd.DataFrame([
            {
                "abrevProveedor": "MJN",
                "referenciaProveedor": "ref-001",
                "idProveedor": None,
                "referenciaRepuesto": "",
                "equivalente": "",
                "descripcion": "",
                "marca": "",
                "carro": "",
                "tipo": "",
                "tipoMotor": "",
                "precioListado": "",
                "proveedor": "",
                "lado": "",
                "ladoAbreviado": "",
                "repuesto": "",
                "modelo": "",
                "otros": "",
                "medida": "",
                "descripcionEstandarizada": "",
                "grupo": "",
            }
        ])
        resultado = actualizar_consolidado(conn, df)
        conn.close()

        self.assertEqual(
            resultado["ids_por_clave"][("MJN", "REF-001")], identificador
        )

    # ------------------------------------------------------------------
    # 8-10. Referencia corregida: única, inexistente y múltiple
    # ------------------------------------------------------------------
    def test_referencia_corregida_unica_es_permitida(self):
        identificador = self.crear_consolidado(referencia="REF-CORR")
        linea_id = self.crear_factura(referencia="REF-ORIG")
        linea = (linea_id, "REF-CORR", "PASTILLA FRENO", 2, 1000, 0, None)

        estado = obtener_estado_linea(
            self.conectar(), ("MOTORES JAPONESES SAS", "2026-01-10", None, None, None), linea
        )
        self.assertTrue(estado["permitido"])
        self.assertEqual(estado["codigo_estado"], "PERMITIDO")
        self.assertEqual(estado["datos_estandarizados"]["id_consolidado"], identificador)

    def test_referencia_corregida_inexistente_no_es_permitida(self):
        linea_id = self.crear_factura(referencia="REF-ORIG")
        linea = (linea_id, "REF-NO-EXISTE", "PASTILLA FRENO", 2, 1000, 0, None)

        estado = obtener_estado_linea(
            self.conectar(), ("MOTORES JAPONESES SAS", "2026-01-10", None, None, None), linea
        )
        self.assertFalse(estado["permitido"])
        self.assertIn("sin_coincidencia_consolidado", estado["advertencias"])

    def test_referencia_corregida_multiple_es_ambigua(self):
        self.crear_consolidado(referencia="REF-CORR", descripcion="UNO")
        self.crear_consolidado(referencia="ref-corr ", descripcion="DOS")
        linea_id = self.crear_factura(referencia="REF-ORIG")
        linea = (linea_id, "REF-CORR", "PASTILLA FRENO", 2, 1000, 0, None)

        estado = obtener_estado_linea(
            self.conectar(), ("MOTORES JAPONESES SAS", "2026-01-10", None, None, None), linea
        )
        self.assertFalse(estado["permitido"])
        self.assertIn("coincidencia_ambigua", estado["advertencias"])

    # ------------------------------------------------------------------
    # 11. Trazabilidad de referencia original/corregida
    # ------------------------------------------------------------------
    def test_migracion_rellena_referencia_original(self):
        linea_id = self.crear_factura(referencia="REF-ORIG")
        conn = self.conectar()
        conn.execute(
            "UPDATE inventarioFacturas SET referenciaOriginal = NULL WHERE id = ?",
            (linea_id,),
        )
        conn.commit()
        migraciones.migrar(conn)
        conn.commit()
        fila = conn.execute(
            "SELECT referenciaOriginal FROM inventarioFacturas WHERE id = ?",
            (linea_id,),
        ).fetchone()
        conn.close()
        self.assertEqual(fila[0], "REF-ORIG")

    # ------------------------------------------------------------------
    # 12. Migraciones idempotentes
    # ------------------------------------------------------------------
    def test_migraciones_idempotentes(self):
        conn = self.conectar()
        migraciones.migrar(conn)
        migraciones.migrar(conn)
        columnas = {
            fila[1]
            for fila in conn.execute("PRAGMA table_info(inventarioUnico)").fetchall()
        }
        columnas_facturas = {
            fila[1]
            for fila in conn.execute("PRAGMA table_info(inventarioFacturas)").fetchall()
        }
        conn.close()

        esperadas = {
            "descripcionEstandarizada", "referenciaProveedor",
            "referenciaRepuesto", "equivalente", "marcaArticulo",
            "marcaVehiculo", "modelo", "lado", "medida",
        }
        self.assertTrue(esperadas.issubset(columnas))
        self.assertIn("referenciaOriginal", columnas_facturas)
        self.assertIn("referenciaCorregida", columnas_facturas)

    # ------------------------------------------------------------------
    # 13. No inventariar dos veces
    # ------------------------------------------------------------------
    def test_no_inventaria_dos_veces(self):
        identificador = self.crear_consolidado()
        primera = servicio.inventariar_consolidado(self.ruta, [identificador])
        segunda = servicio.inventariar_consolidado(self.ruta, [identificador])

        self.assertEqual(primera["inventariados"], 1)
        self.assertEqual(segunda["inventariados"], 0)
        self.assertEqual(segunda["omitidos"], 1)

        conn = self.conectar()
        total = conn.execute("SELECT COUNT(*) FROM inventarioUnico").fetchone()[0]
        conn.close()
        self.assertEqual(total, 1)


if __name__ == "__main__":
    unittest.main()
