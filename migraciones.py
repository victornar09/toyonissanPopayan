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


def migrar(conn):
    """Aplica todas las migraciones pendientes. Idempotente."""

    _migrar_inventario_unico(conn)
    _migrar_inventario_facturas(conn)
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
