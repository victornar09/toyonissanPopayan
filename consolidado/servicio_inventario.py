"""Servicio único de inventariado.

Facturas y Consolidado deben llevar artículos a ``inventarioUnico`` con la
misma lógica. Este módulo concentra la creación/reutilización del artículo,
la generación del código (trigger de la base) y el vínculo con Consolidado.

La cantidad sólo se suma cuando el llamador lo pide expresamente (flujo
histórico de Facturas). El inventariado desde Consolidado NO modifica
cantidades: asigna identidad operativa y código de barras.
"""

from __future__ import annotations

from datetime import date


def _texto(valor):
    if valor is None:
        return ""
    return str(valor).strip()


def obtener_o_crear_articulo(
    conn,
    *,
    descripcion,
    descripcion_clave,
    referencia=None,
    proveedor="",
    precio_venta=0,
    precio_cifrado="",
    precio_max_descuento=0,
    grupo="",
    ubicacion=None,
    estante=None,
    costo=None,
    fecha=None,
    cantidad=0,
    sumar_cantidad=False,
):
    """Crea el artículo de inventario o reutiliza el existente.

    Devuelve ``dict`` con ``id``, ``codigoBarras`` y ``reutilizado``.
    No imprime etiquetas: la impresión se maneja fuera de la transacción.
    """

    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT id, codigoBarras
        FROM inventarioUnico
        WHERE descripcionClave = ?
        """,
        (descripcion_clave,),
    )
    fila = cursor.fetchone()

    fecha_uso = fecha or date.today().strftime("%Y-%m-%d")

    if fila:
        articulo_id, codigo = fila

        cursor.execute(
            """
            UPDATE inventarioUnico
            SET descripcion = COALESCE(NULLIF(?, ''), descripcion),
                referencia = COALESCE(NULLIF(?, ''), referencia),
                proveedor = COALESCE(NULLIF(?, ''), proveedor),
                grupo = COALESCE(NULLIF(?, ''), grupo),
                ubicacion = COALESCE(NULLIF(?, ''), ubicacion),
                estante = COALESCE(NULLIF(?, ''), estante),
                costo = CASE WHEN ? IS NOT NULL THEN ? ELSE costo END,
                precioVenta = CASE WHEN ? > 0 THEN ? ELSE precioVenta END,
                precioVentaCifrado = CASE WHEN NULLIF(?, '') IS NOT NULL
                                          THEN ? ELSE precioVentaCifrado END,
                precioMaxDescuento = CASE WHEN ? > 0
                                          THEN ? ELSE precioMaxDescuento END,
                cantidad = CASE WHEN ? = 1
                                THEN COALESCE(cantidad, 0) + ?
                                ELSE cantidad END,
                fechaActualizacion = ?
            WHERE id = ?
            """,
            (
                _texto(descripcion),
                _texto(referencia),
                _texto(proveedor),
                _texto(grupo),
                _texto(ubicacion),
                _texto(estante),
                costo,
                costo,
                precio_venta or 0,
                precio_venta or 0,
                _texto(precio_cifrado),
                _texto(precio_cifrado),
                precio_max_descuento or 0,
                precio_max_descuento or 0,
                1 if sumar_cantidad else 0,
                int(cantidad or 0),
                fecha_uso,
                articulo_id,
            ),
        )

        cursor.execute(
            "SELECT codigoBarras FROM inventarioUnico WHERE id = ?",
            (articulo_id,),
        )
        fila_codigo = cursor.fetchone()
        codigo = fila_codigo[0] if fila_codigo else codigo

        return {
            "id": articulo_id,
            "codigoBarras": codigo,
            "reutilizado": True,
        }

    cursor.execute(
        """
        INSERT INTO inventarioUnico (
            descripcion, descripcionClave, referencia, cantidad,
            precioVenta, precioVentaCifrado, precioMaxDescuento,
            grupo, proveedor, fechaActualizacion,
            ubicacion, estante, costo,
            estadoEtiquetado, estadoConteo
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'PENDIENTE', 'SIN_CONTAR')
        """,
        (
            _texto(descripcion),
            descripcion_clave,
            _texto(referencia),
            int(cantidad or 0) if sumar_cantidad else 0,
            precio_venta or 0,
            _texto(precio_cifrado),
            precio_max_descuento or 0,
            _texto(grupo),
            _texto(proveedor),
            fecha_uso,
            _texto(ubicacion),
            _texto(estante),
            costo,
        ),
    )

    articulo_id = cursor.lastrowid
    cursor.execute(
        "SELECT codigoBarras FROM inventarioUnico WHERE id = ?",
        (articulo_id,),
    )
    fila_codigo = cursor.fetchone()

    return {
        "id": articulo_id,
        "codigoBarras": fila_codigo[0] if fila_codigo else None,
        "reutilizado": False,
    }
