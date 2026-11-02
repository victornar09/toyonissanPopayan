from flask import (
    Blueprint,
    render_template,
    session,
    redirect,
    url_for,
    request,
    Response,
)
import os
import csv
from datetime import date
from reportlab.pdfgen import canvas
from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader
import barcode
from barcode.writer import ImageWriter
from io import BytesIO, StringIO
from PIL import Image
from reportlab.platypus import Paragraph, Frame
from reportlab.lib.styles import getSampleStyleSheet
import win32com.client

import db
from utilidades import cifrar_precio, a_entero, normalizar_clave


def imprimir_varios(productos, ruta_template, columna_inicio=1):
    # CSV temporal (BarTender lo lee)
    ruta_csv = os.path.abspath("productos_temp.csv")

    with open(ruta_csv, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["codigo", "descripcion", "precio", "copias", "inicio_columna"])
        for codigo, descripcion, precio, cantidad in productos:
            writer.writerow([
                codigo,
                descripcion,
                precio,
                cantidad,
                columna_inicio
            ])

    bt = win32com.client.Dispatch("BarTender.Application")
    bt.Visible = True

    # Abrir template dinámico
    formato = bt.Formats.Open(
        os.path.abspath(ruta_template),
        False,
        ""
    )

    formato.PrintOut(False, False)


def _actualizar_inventario_desde_facturas(ruta_db):
    """Consolida los ítems de facturas en inventarioUnico.

    REGLA: Solo permite pasar líneas con coincidencia válida en consolidado.
    Usa descripción estandarizada de consolidado y genera descripcionClave desde ella.
    """

    from facturas.validacion_consolidado import obtener_estado_linea

    with db.conexion(ruta_db) as conn:

        cursor = conn.cursor()

        cursor.execute("""
            SELECT
                a.id,
                a.descripcion_item,
                a.cantidad,
                a.valor_unitario,
                a.referencia,
                b.proveedor,
                b.fecha,
                a.inventariado,
                a.id_inventarioUnico
            FROM inventarioFacturas a
            LEFT JOIN facturas b ON a.id_factura = b.id_factura
            ORDER BY b.fecha DESC
        """)

        for fila in cursor.fetchall():
            (
                id_fila, descripcion_item, cantidad,
                valor_unitario, referencia, proveedor, fecha,
                inventariado, id_inventario_unico
            ) = fila

            if inventariado == 1 or inventariado is True:
                continue
            if id_inventario_unico:
                cur2 = conn.cursor()
                cur2.execute("SELECT id FROM inventarioUnico WHERE id = ?", (id_inventario_unico,))
                if cur2.fetchone():
                    continue

            estado = obtener_estado_linea(conn, (proveedor, fecha, None, None, None), fila)
            if not estado.get('permitido'):
                continue

            datos_est = estado.get('datos_estandarizados') or {}
            descripcion_est = datos_est.get('descripcion') or datos_est.get('descripcionEstandarizada') or descripcion_item
            clave = normalizar_clave(descripcion_est)
            if not clave:
                continue

            precio_venta = int(round(a_entero(valor_unitario, 0), -3)) if a_entero(valor_unitario, 0) else 0
            precio_max_desc = round(precio_venta * 0.9, 0)
            precio_cifrado = cifrar_precio(precio_venta)
            prov_use = datos_est.get('proveedor') or proveedor or ''

            cursor.execute("""
                INSERT INTO inventarioUnico (
                    descripcion, descripcionClave, referencia, cantidad,
                    precioVenta, precioVentaCifrado, precioMaxDescuento,
                    proveedor, fechaActualizacion, grupo
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(descripcionClave) DO UPDATE SET
                    descripcion = excluded.descripcion,
                    referencia = excluded.referencia,
                    cantidad = inventarioUnico.cantidad + excluded.cantidad,
                    precioVenta = excluded.precioVenta,
                    precioVentaCifrado = excluded.precioVentaCifrado,
                    precioMaxDescuento = excluded.precioMaxDescuento,
                    proveedor = excluded.proveedor,
                    fechaActualizacion = excluded.fechaActualizacion,
                    grupo = excluded.grupo
            """, (
                descripcion_est,
                clave,
                referencia,
                a_entero(cantidad, 0),
                precio_venta,
                precio_cifrado,
                precio_max_desc,
                prov_use,
                fecha or str(date.today()),
                None,
            ))

            cursor.execute(
                "SELECT id FROM inventarioUnico WHERE descripcionClave = ?",
                (clave,),
            )
            row_inv = cursor.fetchone()
            if not row_inv:
                continue
            nuevo_id = row_inv[0]

            cursor.execute("""
                UPDATE inventarioFacturas
                SET inventariado = 1,
                    id_inventarioUnico = ?,
                    id_consolidado = COALESCE(?, id_consolidado)
                WHERE id = ?
            """, (nuevo_id, datos_est.get('id_consolidado'), id_fila))


def _exportar_inventario_csv(ruta_db):

    with db.conexion(ruta_db) as conn:

        cursor = conn.cursor()

        cursor.execute("""
            SELECT
                id,
                codigoBarras,
                referencia,
                descripcion,
                cantidad,
                precioVenta,
                precioVentaCifrado,
                precioMaxDescuento,
                proveedor,
                fechaActualizacion,
                grupo
            FROM inventarioUnico
            ORDER BY descripcion
        """)

        filas = cursor.fetchall()

    buffer = StringIO()
    writer = csv.writer(buffer)

    writer.writerow([
        "id", "codigoBarras", "referencia", "descripcion", "cantidad",
        "precioVenta", "precioVentaCifrado", "precioMaxDescuento",
        "proveedor", "fechaActualizacion", "grupo",
    ])

    for fila in filas:
        writer.writerow(fila)

    return Response(
        buffer.getvalue().encode("utf-8-sig"),
        mimetype="text/csv",
        headers={
            "Content-Disposition": "attachment; filename=inventario.csv"
        },
    )


inventario_bp = Blueprint('inventario', __name__, url_prefix='/inventario')

@inventario_bp.route('/', methods=['GET', 'POST'])
def inventario_home():

    if 'usuario' not in session:
        return redirect(url_for('auth.login'))

    ruta_db = db.ruta_bd_activa()

    if ruta_db and request.method == 'POST':

        accion = request.form.get('accion')

        if accion == 'descargar_inventario':
            return _exportar_inventario_csv(ruta_db)

        if accion == 'actualizar_inventario':
            _actualizar_inventario_desde_facturas(ruta_db)

    items_list = []
    bartender_templates = []

    if ruta_db:

        with db.conexion(ruta_db) as conn:

            cursor = conn.cursor()

            cursor.execute("""
                SELECT
                    id,
                    codigoBarras,
                    referencia,
                    descripcion,
                    cantidad,
                    precioVenta,
                    precioVentaCifrado,
                    precioMaxDescuento,
                    proveedor,
                    fechaActualizacion,
                    grupo
                FROM inventarioUnico;
            """)

            items_list = cursor.fetchall()

            cursor.execute("""
                SELECT idBartenderFiles, name
                FROM bartenderFiles
                WHERE activo = 1
                ORDER BY name
            """)

            bartender_templates = cursor.fetchall()

    return render_template(
        'inventario/inventario.html',
        usuario=session.get('usuario'),
        items=items_list,
        bartender_templates=bartender_templates)


@inventario_bp.route('/actualizar_item', methods=['POST'])
def actualizar_item():
    """Actualizar datos de un item en el inventario"""
    if 'usuario' not in session:
        return {'ok': False, 'error': 'No autenticado'}, 401

    try:
        data = request.get_json()
        item_id = data.get('id')
        codigo = data.get('codigoBarras')
        descripcion = data.get('descripcion')
        cantidad = data.get('cantidad')
        precio = data.get('precio')
        grupo = data.get('grupo', '')

        precio_num = a_entero(precio, 0)
        cantidad_num = a_entero(cantidad, 0)

        precio_cifrado = cifrar_precio(precio_num)
        precio_descuento = precio_num * 0.9
        precio_descuento_redondeado = int(round(precio_descuento, -3))

        with db.conexion() as conn:

            cursor = conn.cursor()

            cursor.execute("""
                UPDATE inventarioUnico
                SET descripcion = ?,
                    descripcionClave = ?,
                    cantidad = ?,
                    precioVenta = ?,
                    precioVentaCifrado = ?,
                    precioMaxDescuento = ?,
                    grupo = ?
                WHERE id = ?
            """, (
                descripcion,
                normalizar_clave(descripcion),
                cantidad_num,
                precio_num,
                precio_cifrado,
                precio_descuento_redondeado,
                grupo,
                item_id,
            ))

        return {'ok': True, 'mensaje': 'Item actualizado correctamente'}
    except Exception as e:
        return {'ok': False, 'error': str(e)}, 500


@inventario_bp.route('/imprimir_etiqueta_nueva', methods=['POST'])
def imprimir_etiqueta_nueva():
    """Imprimir etiqueta(s) de un item"""
    if 'usuario' not in session:
        return {'ok': False, 'error': 'No autenticado'}, 401

    try:
        data = request.get_json()

        codigo = data.get('codigo')
        descripcion = data.get('descripcion')
        precio = data.get('precio')
        cantidad = data.get('cantidad', 1)
        columna_inicio = data.get('columna_inicio', 1)
        template_id = data.get('template_id', 'default')

        precioCifrado = cifrar_precio(
            str(precio).replace(".", "").strip()
        )

        with db.conexion() as conn:

            cursor = conn.cursor()

            cursor.execute("""
                SELECT ubicacion
                FROM bartenderFiles
                WHERE idBartenderFiles = ?
            """, (template_id,))

            row = cursor.fetchone()

        if not row:
            raise Exception("Template BarTender no encontrado")

        ruta_template = row[0]

        productos = [(codigo, descripcion, precioCifrado, cantidad)]

        imprimir_varios(
            productos,
            ruta_template=ruta_template,
            columna_inicio=1
        )

        return {'ok': True, 'mensaje': f'{cantidad} etiqueta(s) enviada(s) a impresión'}
    except Exception as e:
        return {'ok': False, 'error': str(e)}, 500
