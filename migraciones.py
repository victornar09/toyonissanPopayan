"""Migraciones idempotentes del esquema.

Corrige problemas de datos de la "Nivel 1":

* ``inventarioFacturas.descripcion_item`` ya no es UNIQUE (perdía ítems).
* ``inventarioUnico`` deja de usar ``descripcion`` como clave y pasa a usar
  ``descripcionClave`` (texto normalizado), evitando duplicados por
  mayúsculas/tildes/espacios.

Se pueden ejecutar al arrancar la aplicación o manualmente:

    python -m migraciones
"""

import sqlite3

from utilidades import normalizar_clave


def _tabla_existe(conn, tabla):
    cur = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (tabla,),
    )
    return cur.fetchone() is not None


def _columnas(conn, tabla):
    cur = conn.execute(f"PRAGMA table_info({tabla})")
    return [fila[1] for fila in cur.fetchall()]


def _indice_existe(conn, nombre):
    cur = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='index' AND name=?",
        (nombre,),
    )
    return cur.fetchone() is not None


def _sql_tabla(conn, tabla):
    cur = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name=?",
        (tabla,),
    )
    fila = cur.fetchone()
    return fila[0] if fila else ""


def _fusionar_duplicados_inventario(conn):
    """Fusiona filas de inventarioUnico con la misma descripcionClave."""

    cur = conn.execute("""
        SELECT descripcionClave, GROUP_CONCAT(id) AS ids, COUNT(*) AS n
        FROM inventarioUnico
        WHERE descripcionClave IS NOT NULL
        GROUP BY descripcionClave
        HAVING COUNT(*) > 1
    """)

    for clave, ids_texto, _ in cur.fetchall():

        ids = sorted(int(x) for x in ids_texto.split(","))
        principal = ids[0]
        secundarios = ids[1:]

        conn.execute(
            """
            UPDATE inventarioUnico
            SET cantidad = (
                SELECT SUM(cantidad) FROM inventarioUnico
                WHERE id IN (%s)
            )
            WHERE id = ?
            """
            % ",".join("?" * len(ids)),
            (*ids, principal),
        )

        for secundario in secundarios:
            conn.execute(
                "UPDATE inventarioFacturas SET id_inventarioUnico = ? "
                "WHERE id_inventarioUnico = ?",
                (principal, secundario),
            )

        conn.execute(
            "DELETE FROM inventarioUnico WHERE id IN (%s)"
            % ",".join("?" * len(secundarios)),
            tuple(secundarios),
        )


def _migrar_inventario_unico(conn):

    if not _tabla_existe(conn, "inventarioUnico"):
        return

    columnas = _columnas(conn, "inventarioUnico")

    if "descripcionClave" not in columnas:
        conn.execute(
            "ALTER TABLE inventarioUnico ADD COLUMN descripcionClave TEXT"
        )

    # Poblar la clave normalizada en las filas que aún no la tengan
    cur = conn.execute("""
        SELECT id, descripcion FROM inventarioUnico
        WHERE descripcionClave IS NULL
    """)

    for id_fila, descripcion in cur.fetchall():
        conn.execute(
            "UPDATE inventarioUnico SET descripcionClave = ? WHERE id = ?",
            (normalizar_clave(descripcion), id_fila),
        )

    # Eliminar el índice único antiguo sobre descripcion
    if _indice_existe(conn, "idx_inventario_descripcion"):
        conn.execute("DROP INDEX idx_inventario_descripcion")

    _fusionar_duplicados_inventario(conn)

    if not _indice_existe(conn, "idx_inventario_clave"):
        conn.execute("""
            CREATE UNIQUE INDEX idx_inventario_clave
            ON inventarioUnico (descripcionClave)
        """)


def _migrar_inventario_facturas(conn):

    if not _tabla_existe(conn, "inventarioFacturas"):
        return

    columnas = _columnas(conn, "inventarioFacturas")

    # Añadir trazabilidad con consolidado (nullable para históricos)
    if "id_consolidado" not in columnas:
        try:
            conn.execute("ALTER TABLE inventarioFacturas ADD COLUMN id_consolidado INTEGER")
        except Exception:
            pass

    # Auditoría de referencias: se conserva la referencia original de la
    # factura aunque el usuario corrija la referencia antes de consolidar.
    if "referenciaOriginal" not in columnas:
        try:
            conn.execute(
                "ALTER TABLE inventarioFacturas ADD COLUMN referenciaOriginal TEXT"
            )
        except Exception:
            pass

    if "referenciaCorregida" not in columnas:
        try:
            conn.execute(
                "ALTER TABLE inventarioFacturas ADD COLUMN referenciaCorregida TEXT"
            )
        except Exception:
            pass

    conn.execute("""
        UPDATE inventarioFacturas
        SET referenciaOriginal = referencia
        WHERE referenciaOriginal IS NULL
          AND referencia IS NOT NULL
    """)

    if not _indice_existe(conn, "idx_invf_idconsolidado"):
        try:
            conn.execute("CREATE INDEX idx_invf_idconsolidado ON inventarioFacturas (id_consolidado)")
        except Exception:
            pass

    if "UNIQUE" in _sql_tabla(conn, "inventarioFacturas").upper():

        conn.execute("""
            CREATE TABLE inventarioFacturas_migracion (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                id_factura TEXT,
                descripcion_item TEXT,
                cantidad INTEGER,
                valor_unitario REAL,
                referencia TEXT,
                inventariado BOOLEAN DEFAULT 0,
                id_inventarioUnico INTEGER,
                id_consolidado INTEGER
            )
        """)

        conn.execute("""
            INSERT INTO inventarioFacturas_migracion (
                id, id_factura, descripcion_item, cantidad,
                valor_unitario, referencia, inventariado, id_inventarioUnico, id_consolidado
            )
            SELECT
                id, id_factura, descripcion_item, cantidad,
                valor_unitario, referencia, inventariado, id_inventarioUnico, NULL
            FROM inventarioFacturas
        """)

        conn.execute("DROP TABLE inventarioFacturas")
        conn.execute(
            "ALTER TABLE inventarioFacturas_migracion "
            "RENAME TO inventarioFacturas"
        )

    # Índice de llave natural para evitar duplicados al re-descargar
    if not _indice_existe(conn, "idx_invf_llave"):

        try:
            conn.execute("""
                CREATE UNIQUE INDEX idx_invf_llave
                ON inventarioFacturas (id_factura, referencia, descripcion_item)
            """)
        except sqlite3.IntegrityError:
            # Hay datos legados que no cumplen la llave; se deja sin índice
            # para no bloquear el arranque.
            pass


def _agregar_columna(conn, tabla, columna, definicion):
    if columna not in _columnas(conn, tabla):
        try:
            conn.execute(
                f"ALTER TABLE {tabla} ADD COLUMN {columna} {definicion}"
            )
        except sqlite3.OperationalError:
            pass


def _backfill_estados_consolidado(conn):
    """Calcula el estado inicial de todo el histórico de Consolidado."""

    conn.execute("DROP TABLE IF EXISTS temp._dup_consolidado")
    conn.execute("""
        CREATE TEMP TABLE _dup_consolidado AS
        SELECT UPPER(TRIM(abrevProveedor)) AS abrev,
               UPPER(TRIM(referenciaProveedor)) AS ref
        FROM consolidado
        WHERE abrevProveedor IS NOT NULL
          AND referenciaProveedor IS NOT NULL
        GROUP BY UPPER(TRIM(abrevProveedor)),
                 UPPER(TRIM(referenciaProveedor))
        HAVING COUNT(*) > 1
    """)

    conn.execute("""
        UPDATE consolidado
        SET estadoConsolidado =
            CASE
                WHEN idInventarioUnico IS NOT NULL
                     AND EXISTS (
                         SELECT 1 FROM inventarioUnico iu
                         WHERE iu.id = consolidado.idInventarioUnico
                     )
                    THEN 'INVENTARIADO'
                WHEN EXISTS (
                    SELECT 1 FROM _dup_consolidado d
                    WHERE d.abrev = UPPER(TRIM(consolidado.abrevProveedor))
                      AND d.ref = UPPER(TRIM(consolidado.referenciaProveedor))
                )
                    THEN 'BLOQUEADO'
                WHEN descripcionEstandarizada IS NOT NULL
                     AND LENGTH(TRIM(descripcionEstandarizada)) >= 3
                     AND referenciaProveedor IS NOT NULL
                     AND TRIM(referenciaProveedor) <> ''
                     AND UPPER(TRIM(referenciaProveedor)) <> 'SIN_REFERENCIA'
                    THEN 'LISTO_INVENTARIAR'
                ELSE 'PENDIENTE_REVISION'
            END
    """)

    conn.execute("DROP TABLE IF EXISTS temp._dup_consolidado")


def _migrar_consolidado(conn):
    """Agrega estado, relación con inventario y auditoría a consolidado."""

    if not _tabla_existe(conn, "consolidado"):
        return

    estado_nuevo = "estadoConsolidado" not in _columnas(conn, "consolidado")

    _agregar_columna(
        conn, "consolidado", "estadoConsolidado",
        "TEXT NOT NULL DEFAULT 'PENDIENTE_REVISION'",
    )
    _agregar_columna(conn, "consolidado", "idInventarioUnico", "INTEGER")
    _agregar_columna(conn, "consolidado", "fechaInventariado", "DATETIME")
    _agregar_columna(conn, "consolidado", "usuarioInventariado", "TEXT")
    _agregar_columna(conn, "consolidado", "fechaCreacion", "DATETIME")
    _agregar_columna(conn, "consolidado", "fechaModificacion", "DATETIME")
    _agregar_columna(conn, "consolidado", "usuarioModificacion", "TEXT")
    _agregar_columna(conn, "consolidado", "grupo", "TEXT")

    # Backfill de auditoría sin destruir información existente.
    conn.execute("""
        UPDATE consolidado
        SET fechaCreacion = COALESCE(fechaCreacion, fechaActualizacion)
        WHERE fechaCreacion IS NULL
    """)

    # Claves duplicadas normalizadas (mayúsculas/espacios). No se eliminan:
    # se marcan como BLOQUEADO para revisión manual. Sólo se ejecuta la
    # primera vez que se crea la columna, para no revertir estados manuales.
    if estado_nuevo:
        _backfill_estados_consolidado(conn)

    # Índice funcional para búsquedas normalizadas (no único: hay histórico).
    try:
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_consolidado_llave_norm
            ON consolidado (
                UPPER(TRIM(abrevProveedor)),
                UPPER(TRIM(referenciaProveedor))
            )
        """)
    except sqlite3.OperationalError:
        pass

    try:
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_consolidado_estado
            ON consolidado (estadoConsolidado)
        """)
    except sqlite3.OperationalError:
        pass

    try:
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_consolidado_inventario
            ON consolidado (idInventarioUnico)
        """)
    except sqlite3.OperationalError:
        pass


def _migrar_inventario_estados(conn):
    """Agrega ubicación, costo y estados de etiquetado/conteo a inventarioUnico.

    No elimina columnas históricas. ``inventariadoTeorico`` nunca existió en
    este esquema; si una base externa la tuviera, se deja intacta y el módulo
    simplemente deja de usarla.
    """

    if not _tabla_existe(conn, "inventarioUnico"):
        return

    _agregar_columna(conn, "inventarioUnico", "costo", "REAL")
    _agregar_columna(conn, "inventarioUnico", "ubicacion", "TEXT")
    _agregar_columna(conn, "inventarioUnico", "estante", "TEXT")
    _agregar_columna(
        conn, "inventarioUnico", "estadoEtiquetado",
        "TEXT NOT NULL DEFAULT 'PENDIENTE'",
    )
    _agregar_columna(
        conn, "inventarioUnico", "estadoConteo",
        "TEXT NOT NULL DEFAULT 'SIN_CONTAR'",
    )

    # Campos del conteo físico aprobado (README: cantidadFisica,
    # fechaUltimoConteo, stockMinimo, fechaCreacion). Se agregan de forma
    # aditiva para no perder información histórica.
    _agregar_columna(conn, "inventarioUnico", "cantidadFisica", "INTEGER")
    _agregar_columna(conn, "inventarioUnico", "fechaUltimoConteo", "DATETIME")
    _agregar_columna(conn, "inventarioUnico", "stockMinimo", "INTEGER DEFAULT 0")
    _agregar_columna(conn, "inventarioUnico", "fechaCreacion", "DATETIME")

    conn.execute("""
        UPDATE inventarioUnico
        SET estadoEtiquetado = 'PENDIENTE'
        WHERE estadoEtiquetado IS NULL OR TRIM(estadoEtiquetado) = ''
    """)
    conn.execute("""
        UPDATE inventarioUnico
        SET estadoConteo = 'SIN_CONTAR'
        WHERE estadoConteo IS NULL OR TRIM(estadoConteo) = ''
    """)
    conn.execute("""
        UPDATE inventarioUnico
        SET fechaCreacion = COALESCE(fechaCreacion, fechaActualizacion)
        WHERE fechaCreacion IS NULL
    """)


def _migrar_inventario_operativo(conn):
    """Completa en inventarioUnico los datos operativos del catálogo.

    Antes estos datos sólo se leían desde consolidado mediante un JOIN. Ahora
    se persisten en el artículo de inventario para que la identidad operativa
    no dependa de que exista una única fila de consolidado. Las columnas son
    aditivas e idempotentes y el backfill sólo rellena valores vacíos.
    """

    if not _tabla_existe(conn, "inventarioUnico"):
        return

    _agregar_columna(conn, "inventarioUnico", "descripcionEstandarizada", "TEXT")
    _agregar_columna(conn, "inventarioUnico", "referenciaProveedor", "TEXT")
    _agregar_columna(conn, "inventarioUnico", "referenciaRepuesto", "TEXT")
    _agregar_columna(conn, "inventarioUnico", "equivalente", "TEXT")
    _agregar_columna(conn, "inventarioUnico", "marcaArticulo", "TEXT")
    _agregar_columna(conn, "inventarioUnico", "marcaVehiculo", "TEXT")
    _agregar_columna(conn, "inventarioUnico", "modelo", "TEXT")
    _agregar_columna(conn, "inventarioUnico", "lado", "TEXT")
    _agregar_columna(conn, "inventarioUnico", "medida", "TEXT")

    if not _tabla_existe(conn, "consolidado"):
        return

    # Backfill: sólo se completan campos vacíos desde el consolidado
    # relacionado. Nunca se sobrescribe un valor válido existente.
    campos = [
        ("descripcionEstandarizada", "descripcionEstandarizada"),
        ("referenciaProveedor", "referenciaProveedor"),
        ("referenciaRepuesto", "referenciaRepuesto"),
        ("equivalente", "equivalente"),
        ("marcaArticulo", "marca"),
        ("marcaVehiculo", "carro"),
        ("modelo", "modelo"),
        ("lado", "lado"),
        ("medida", "medida"),
    ]

    for columna_inventario, columna_consolidado in campos:
        conn.execute(
            f"""
            UPDATE inventarioUnico AS iu
            SET {columna_inventario} = (
                SELECT c.{columna_consolidado}
                FROM consolidado c
                WHERE c.idInventarioUnico = iu.id
                  AND c.{columna_consolidado} IS NOT NULL
                  AND TRIM(c.{columna_consolidado}) <> ''
                ORDER BY c.id
                LIMIT 1
            )
            WHERE (iu.{columna_inventario} IS NULL
                   OR TRIM(iu.{columna_inventario}) = '')
              AND EXISTS (
                  SELECT 1 FROM consolidado c
                  WHERE c.idInventarioUnico = iu.id
                    AND c.{columna_consolidado} IS NOT NULL
                    AND TRIM(c.{columna_consolidado}) <> ''
              )
            """
        )

    try:
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_inventario_referencia_proveedor
            ON inventarioUnico (referenciaProveedor)
        """)
    except sqlite3.OperationalError:
        pass


def _migrar_conteo_fisico(conn):
    """Crea las entidades de sesiones de conteo, lecturas y movimientos.

    Todas las columnas son aditivas y las tablas usan ``IF NOT EXISTS`` para
    poder ejecutarse sobre bases existentes sin perder datos.
    """

    if not _tabla_existe(conn, "inventarioUnico"):
        return

    conn.execute("""
        CREATE TABLE IF NOT EXISTS sesionConteo (
            idSesion INTEGER PRIMARY KEY AUTOINCREMENT,
            nombre TEXT NOT NULL,
            responsable TEXT,
            fechaInicio DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            fechaFin DATETIME,
            idUbicacion INTEGER,
            ubicacion TEXT,
            estante TEXT,
            estado TEXT NOT NULL DEFAULT 'ABIERTA',
            observaciones TEXT,
            usuarioCreacion TEXT,
            fechaCreacion DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            usuarioAplicacion TEXT,
            fechaAplicacion DATETIME
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS detalleConteo (
            idDetalle INTEGER PRIMARY KEY AUTOINCREMENT,
            idSesion INTEGER NOT NULL,
            idInventarioUnico INTEGER NOT NULL,
            codigoBarras TEXT,
            cantidadRegistrada INTEGER NOT NULL DEFAULT 0,
            cantidadContada INTEGER NOT NULL DEFAULT 0,
            diferencia INTEGER NOT NULL DEFAULT 0,
            estado TEXT NOT NULL DEFAULT 'PENDIENTE',
            ubicacionRegistrada TEXT,
            ubicacionSesion TEXT,
            observacion TEXT,
            UNIQUE (idSesion, idInventarioUnico)
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS lecturaConteo (
            idLectura INTEGER PRIMARY KEY AUTOINCREMENT,
            idSesion INTEGER NOT NULL,
            idInventarioUnico INTEGER,
            codigoBarras TEXT,
            token TEXT,
            tipo TEXT NOT NULL DEFAULT 'OK',
            ubicacionRegistrada TEXT,
            ubicacionSesion TEXT,
            fecha DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            usuario TEXT,
            anulada INTEGER NOT NULL DEFAULT 0,
            observacion TEXT
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS movimientoInventario (
            idMovimiento INTEGER PRIMARY KEY AUTOINCREMENT,
            idInventarioUnico INTEGER NOT NULL,
            tipo TEXT NOT NULL,
            cantidadAnterior INTEGER,
            cantidadMovimiento INTEGER,
            cantidadNueva INTEGER,
            origen TEXT,
            idOrigen INTEGER,
            fecha DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            usuario TEXT,
            observacion TEXT
        )
    """)

    indices = [
        (
            "idx_sesion_conteo_estado",
            "CREATE INDEX IF NOT EXISTS idx_sesion_conteo_estado "
            "ON sesionConteo (estado)",
        ),
        (
            "idx_detalle_conteo_sesion",
            "CREATE INDEX IF NOT EXISTS idx_detalle_conteo_sesion "
            "ON detalleConteo (idSesion)",
        ),
        (
            "idx_lectura_conteo_sesion",
            "CREATE INDEX IF NOT EXISTS idx_lectura_conteo_sesion "
            "ON lecturaConteo (idSesion)",
        ),
        (
            "idx_lectura_conteo_token",
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_lectura_conteo_token "
            "ON lecturaConteo (token)",
        ),
        (
            "idx_movimiento_inventario_item",
            "CREATE INDEX IF NOT EXISTS idx_movimiento_inventario_item "
            "ON movimientoInventario (idInventarioUnico)",
        ),
        (
            "idx_inventario_fecha_conteo",
            "CREATE INDEX IF NOT EXISTS idx_inventario_fecha_conteo "
            "ON inventarioUnico (fechaUltimoConteo)",
        ),
    ]

    for _, sql in indices:
        try:
            conn.execute(sql)
        except sqlite3.OperationalError:
            pass


def _migrar_ion(conn):
    """Estructuras del asistente ION: conocimiento, auditoría e índice FTS5.

    Todo es aditivo e idempotente. El índice es una tabla virtual FTS5; si la
    compilación de SQLite no la soporta, la búsqueda usa el respaldo LIKE y
    similitud en Python.
    """

    conn.execute("""
        CREATE TABLE IF NOT EXISTS ionSinonimo (
            idSinonimo INTEGER PRIMARY KEY AUTOINCREMENT,
            termino TEXT NOT NULL,
            terminoNormalizado TEXT NOT NULL,
            reemplazo TEXT NOT NULL,
            reemplazoNormalizado TEXT NOT NULL,
            tipo TEXT NOT NULL DEFAULT 'SINONIMO',
            estado TEXT NOT NULL DEFAULT 'PENDIENTE',
            origen TEXT,
            idConsulta INTEGER,
            usuarioCreacion TEXT,
            fechaCreacion DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            usuarioRevision TEXT,
            fechaRevision DATETIME,
            UNIQUE (terminoNormalizado, reemplazoNormalizado)
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS ionCompatibilidad (
            idCompatibilidad INTEGER PRIMARY KEY AUTOINCREMENT,
            idConsolidado INTEGER,
            idInventarioUnico INTEGER,
            marca TEXT,
            modelo TEXT,
            anioDesde INTEGER,
            anioHasta INTEGER,
            motor TEXT,
            combustible TEXT,
            transmision TEXT,
            traccion TEXT,
            cabina TEXT,
            medida TEXT,
            posicion TEXT,
            lado TEXT,
            observacion TEXT,
            estado TEXT NOT NULL DEFAULT 'APROBADO',
            usuarioCreacion TEXT,
            fechaCreacion DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS ionConsulta (
            idConsulta INTEGER PRIMARY KEY AUTOINCREMENT,
            usuario TEXT,
            consultaOriginal TEXT NOT NULL,
            interpretacion TEXT,
            criterios TEXT,
            respuestas TEXT,
            preguntas TEXT,
            resultados TEXT,
            idResultadoSeleccionado INTEGER,
            tipoResultadoSeleccionado TEXT,
            sinResultados INTEGER NOT NULL DEFAULT 0,
            fecha DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS ionConsultaPregunta (
            idPregunta INTEGER PRIMARY KEY AUTOINCREMENT,
            idConsulta INTEGER NOT NULL,
            campo TEXT,
            pregunta TEXT,
            respuesta TEXT,
            fecha DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS ionFeedback (
            idFeedback INTEGER PRIMARY KEY AUTOINCREMENT,
            idConsulta INTEGER,
            tipoResultado TEXT,
            idResultado INTEGER,
            tipo TEXT NOT NULL,
            motivo TEXT,
            usuario TEXT,
            fecha DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS ionIndiceMeta (
            clave TEXT PRIMARY KEY,
            valor TEXT
        )
    """)
    conn.execute(
        "INSERT OR IGNORE INTO ionIndiceMeta (clave, valor) VALUES (?, ?)",
        ("dirty", "1"),
    )
    conn.execute(
        "INSERT OR IGNORE INTO ionIndiceMeta (clave, valor) VALUES (?, ?)",
        ("version", "1"),
    )

    indices = [
        "CREATE INDEX IF NOT EXISTS idx_ion_sinonimo_estado "
        "ON ionSinonimo (estado)",
        "CREATE INDEX IF NOT EXISTS idx_ion_consulta_fecha "
        "ON ionConsulta (fecha)",
        "CREATE INDEX IF NOT EXISTS idx_ion_pregunta_consulta "
        "ON ionConsultaPregunta (idConsulta)",
        "CREATE INDEX IF NOT EXISTS idx_ion_feedback_consulta "
        "ON ionFeedback (idConsulta)",
        "CREATE INDEX IF NOT EXISTS idx_ion_compatibilidad_item "
        "ON ionCompatibilidad (idConsolidado, idInventarioUnico)",
    ]
    for sql in indices:
        conn.execute(sql)

    try:
        conn.execute("""
            CREATE VIRTUAL TABLE IF NOT EXISTS ionIndice USING fts5(
                tipo UNINDEXED,
                idOrigen UNINDEXED,
                contenido,
                tokenize = 'unicode61 remove_diacritics 2'
            )
        """)
    except sqlite3.OperationalError:
        # Sin FTS5 la búsqueda sigue funcionando con el respaldo LIKE/fuzzy.
        pass

    # Marcar el índice como desactualizado cuando cambien las fuentes.
    triggers = [
        """
        CREATE TRIGGER IF NOT EXISTS ion_inventario_ai
        AFTER INSERT ON inventarioUnico
        BEGIN
            INSERT INTO ionIndiceMeta (clave, valor) VALUES ('dirty', '1')
            ON CONFLICT(clave) DO UPDATE SET valor = '1';
        END
        """,
        """
        CREATE TRIGGER IF NOT EXISTS ion_inventario_au
        AFTER UPDATE ON inventarioUnico
        BEGIN
            INSERT INTO ionIndiceMeta (clave, valor) VALUES ('dirty', '1')
            ON CONFLICT(clave) DO UPDATE SET valor = '1';
        END
        """,
        """
        CREATE TRIGGER IF NOT EXISTS ion_inventario_ad
        AFTER DELETE ON inventarioUnico
        BEGIN
            INSERT INTO ionIndiceMeta (clave, valor) VALUES ('dirty', '1')
            ON CONFLICT(clave) DO UPDATE SET valor = '1';
        END
        """,
        """
        CREATE TRIGGER IF NOT EXISTS ion_consolidado_ai
        AFTER INSERT ON consolidado
        BEGIN
            INSERT INTO ionIndiceMeta (clave, valor) VALUES ('dirty', '1')
            ON CONFLICT(clave) DO UPDATE SET valor = '1';
        END
        """,
        """
        CREATE TRIGGER IF NOT EXISTS ion_consolidado_au
        AFTER UPDATE ON consolidado
        BEGIN
            INSERT INTO ionIndiceMeta (clave, valor) VALUES ('dirty', '1')
            ON CONFLICT(clave) DO UPDATE SET valor = '1';
        END
        """,
        """
        CREATE TRIGGER IF NOT EXISTS ion_consolidado_ad
        AFTER DELETE ON consolidado
        BEGIN
            INSERT INTO ionIndiceMeta (clave, valor) VALUES ('dirty', '1')
            ON CONFLICT(clave) DO UPDATE SET valor = '1';
        END
        """,
    ]
    for sql in triggers:
        try:
            conn.execute(sql)
        except sqlite3.OperationalError:
            pass


def _migrar_impresion_auditoria(conn):
    """Crea las tablas de cola de impresión y auditoría."""

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS trabajoImpresion (
            idTrabajo INTEGER PRIMARY KEY AUTOINCREMENT,
            usuario TEXT,
            fecha DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            fechaInicio DATETIME,
            fechaFin DATETIME,
            articulos TEXT NOT NULL,
            cantidades TEXT,
            idPlantilla INTEGER,
            plantilla TEXT,
            rutaPlantilla TEXT,
            archivoTemporal TEXT,
            columnaInicio INTEGER NOT NULL DEFAULT 1,
            origen TEXT,
            estado TEXT NOT NULL DEFAULT 'PENDIENTE',
            mensajeError TEXT,
            intentos INTEGER NOT NULL DEFAULT 0,
            claveUnica TEXT
        )
        """
    )

    if not _indice_existe(conn, "idx_trabajo_impresion_estado"):
        conn.execute(
            """
            CREATE INDEX idx_trabajo_impresion_estado
            ON trabajoImpresion (estado, fecha)
            """
        )

    if not _indice_existe(conn, "idx_trabajo_impresion_clave"):
        conn.execute(
            """
            CREATE UNIQUE INDEX idx_trabajo_impresion_clave
            ON trabajoImpresion (claveUnica)
            """
        )

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS auditoria (
            idAuditoria INTEGER PRIMARY KEY AUTOINCREMENT,
            fecha DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            usuario TEXT,
            ip TEXT,
            equipo TEXT,
            metodo TEXT,
            ruta TEXT,
            accion TEXT,
            detalle TEXT,
            estadoHttp INTEGER
        )
        """
    )

    if not _indice_existe(conn, "idx_auditoria_fecha"):
        conn.execute(
            "CREATE INDEX idx_auditoria_fecha ON auditoria (fecha)"
        )


def migrar(conn):
    """Aplica todas las migraciones pendientes. Idempotente."""

    _migrar_inventario_unico(conn)
    _migrar_inventario_facturas(conn)
    _migrar_consolidado(conn)
    _migrar_inventario_estados(conn)
    _migrar_inventario_operativo(conn)
    _migrar_conteo_fisico(conn)
    _migrar_ion(conn)
    _migrar_impresion_auditoria(conn)
    conn.commit()


def _main():
    import db

    ruta = db.ruta_bd_activa()

    if not ruta:
        print("No hay base de datos activa.")
        return

    print(f"Migrando: {ruta}")

    conn = db.conectar(ruta)

    try:
        migrar(conn)
        print("Migración completada.")
    finally:
        conn.close()


if __name__ == "__main__":
    _main()
