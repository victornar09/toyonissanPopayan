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


CAMPOS_OPERATIVOS = (
    "descripcionEstandarizada",
    "referenciaProveedor",
    "referenciaRepuesto",
    "equivalente",
    "marcaArticulo",
    "marcaVehiculo",
    "modelo",
    "lado",
    "medida",
)


def _texto_operativo(datos, campo):
    """Extrae un campo operativo de forma tolerante a None/espacios."""

    if not datos:
        return ""
    return _texto(datos.get(campo))


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
    datos_operativos=None,
):
    """Crea el artículo de inventario o reutiliza el existente.

    ``datos_operativos`` contiene la información del Consolidado que debe
    quedar persistida en Inventario (referencias, marcas, modelo, lado,
    medida y descripción estandarizada). En un artículo existente sólo se
    completan campos vacíos: nunca se sobrescribe un dato válido.

    Devuelve ``dict`` con ``id``, ``codigoBarras`` y ``reutilizado``.
    No imprime etiquetas: la impresión se maneja fuera de la transacción.
    """

    operativos = {
        campo: _texto_operativo(datos_operativos, campo)
        for campo in CAMPOS_OPERATIVOS
    }
    if not operativos["descripcionEstandarizada"]:
        operativos["descripcionEstandarizada"] = _texto(descripcion)
    if not operativos["referenciaProveedor"]:
        operativos["referenciaProveedor"] = _texto(referencia)

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

        # Los datos de identidad se completan únicamente cuando están vacíos.
        # Los datos comerciales/ubicación sí respetan lo que el usuario acaba
        # de indicar en el formulario de inventariado.
        cursor.execute(
            """
            UPDATE inventarioUnico
            SET descripcion = CASE
                    WHEN descripcion IS NULL OR TRIM(descripcion) = ''
                    THEN NULLIF(?, '') ELSE descripcion END,
                descripcionEstandarizada = CASE
                    WHEN descripcionEstandarizada IS NULL
                         OR TRIM(descripcionEstandarizada) = ''
                    THEN NULLIF(?, '') ELSE descripcionEstandarizada END,
                referencia = CASE
                    WHEN referencia IS NULL OR TRIM(referencia) = ''
                    THEN NULLIF(?, '') ELSE referencia END,
                referenciaProveedor = CASE
                    WHEN referenciaProveedor IS NULL
                         OR TRIM(referenciaProveedor) = ''
                    THEN NULLIF(?, '') ELSE referenciaProveedor END,
                referenciaRepuesto = CASE
                    WHEN referenciaRepuesto IS NULL
                         OR TRIM(referenciaRepuesto) = ''
                    THEN NULLIF(?, '') ELSE referenciaRepuesto END,
                equivalente = CASE
                    WHEN equivalente IS NULL OR TRIM(equivalente) = ''
                    THEN NULLIF(?, '') ELSE equivalente END,
                marcaArticulo = CASE
                    WHEN marcaArticulo IS NULL OR TRIM(marcaArticulo) = ''
                    THEN NULLIF(?, '') ELSE marcaArticulo END,
                marcaVehiculo = CASE
                    WHEN marcaVehiculo IS NULL OR TRIM(marcaVehiculo) = ''
                    THEN NULLIF(?, '') ELSE marcaVehiculo END,
                modelo = CASE
                    WHEN modelo IS NULL OR TRIM(modelo) = ''
                    THEN NULLIF(?, '') ELSE modelo END,
                lado = CASE
                    WHEN lado IS NULL OR TRIM(lado) = ''
                    THEN NULLIF(?, '') ELSE lado END,
                medida = CASE
                    WHEN medida IS NULL OR TRIM(medida) = ''
                    THEN NULLIF(?, '') ELSE medida END,
                proveedor = CASE
                    WHEN proveedor IS NULL OR TRIM(proveedor) = ''
                    THEN NULLIF(?, '') ELSE proveedor END,
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
                operativos["descripcionEstandarizada"],
                _texto(referencia),
                operativos["referenciaProveedor"],
                operativos["referenciaRepuesto"],
                operativos["equivalente"],
                operativos["marcaArticulo"],
                operativos["marcaVehiculo"],
                operativos["modelo"],
                operativos["lado"],
                operativos["medida"],
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
            descripcion, descripcionClave, descripcionEstandarizada,
            referencia, referenciaProveedor, referenciaRepuesto,
            equivalente, marcaArticulo, marcaVehiculo, modelo, lado, medida,
            cantidad, precioVenta, precioVentaCifrado, precioMaxDescuento,
            grupo, proveedor, fechaActualizacion,
            ubicacion, estante, costo,
            estadoEtiquetado, estadoConteo
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                'PENDIENTE', 'SIN_CONTAR')
        """,
        (
            _texto(descripcion),
            descripcion_clave,
            operativos["descripcionEstandarizada"],
            _texto(referencia),
            operativos["referenciaProveedor"],
            operativos["referenciaRepuesto"],
            operativos["equivalente"],
            operativos["marcaArticulo"],
            operativos["marcaVehiculo"],
            operativos["modelo"],
            operativos["lado"],
            operativos["medida"],
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
