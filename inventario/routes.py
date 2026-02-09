from flask import Blueprint, render_template, session, redirect, url_for, request, send_from_directory, abort
import os
import sqlite3
from reportlab.lib.pagesizes import mm
from reportlab.pdfgen import canvas
from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader
import barcode
from barcode.writer import ImageWriter
from io import BytesIO
from PIL import Image
from reportlab.platypus import Paragraph, Frame
from reportlab.lib.styles import getSampleStyleSheet
import win32com.client
import csv

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

    # 👉 Abrir template dinámico
    formato = bt.Formats.Open(
        os.path.abspath(ruta_template),
        False,
        ""
    )

    formato.PrintOut(False, False)

    # Si luego quieres cerrar:
    # formato.Close(0)
    # bt.Quit(1)

mapa_chapineros = {
    "1": "c", "2": "h", "3": "a", "4": "p",
    "5": "i", "6": "n", "7": "e", "8": "r", "9": "o", "0": "s"
}

def cifrar_precio(precio):
    return "".join(mapa_chapineros.get(d, d) for d in str(int(precio))).upper()



inventario_bp = Blueprint('inventario', __name__, url_prefix='/inventario')

@inventario_bp.route('/', methods=['GET', 'POST'])
def inventario_home():

    if 'usuario' not in session:
        return redirect(url_for('auth.login'))
    
    carpeta_bd = os.path.join(os.getcwd(), 'bdlocal')
    db_files = [f for f in os.listdir(carpeta_bd) if f.endswith('.sqlite')]

    if request.method == 'POST':

        if db_files:


            ruta_db = os.path.join(carpeta_bd, db_files[-1])
            conn = sqlite3.connect(ruta_db)
            cursor = conn.cursor()
            cursor.execute("SELECT MAX(fecha) FROM facturas")
            ultima_fecha = cursor.fetchone()[0]

            # Consulta de items únicos por descripcion_item (el más reciente)
            cursor.execute("""
                SELECT 
                    id,
                    id_factura,
                    descripcion_item,
                    cantidad,
                    valor_unitario,
                    referencia,
                    inventariado,
                    proveedor,
                    fecha
                FROM (
                    SELECT
                        a.id,
                        a.id_factura,
                        a.descripcion_item,
                        a.cantidad,
                        a.valor_unitario,
                        a.referencia,
                        a.inventariado,
                        b.proveedor,
                        b.fecha
                    FROM inventarioFacturas a
                    LEFT JOIN facturas b ON a.id_factura = b.id_factura
                ) t
            """)
            items_unicos = cursor.fetchall()

            print(items_unicos)

            cursor.execute("update inventarioUnico set cantidad = 0")

            # Preparamos para recorrer items
            for item in items_unicos:
                (
                    id, id_factura, descripcion_item, cantidad,
                    valor_unitario, referencia, inventariado, proveedor, fecha
                ) = item

                # Calcular valores requeridos
                cantidad_final = 0
                precio_venta = int(round(valor_unitario, -3))  

                precio_str = str(valor_unitario).replace(".", "").strip()
                #precio_venta = int(precio_str) if precio_str.isdigit() else 0

                # aplicar 10% descuento y redondear a miles
                precio_descuento = precio_venta * 0.9
                precio_descuento_redondeado = int(round(precio_descuento, -3))  

                precio_cifrado = cifrar_precio(precio_venta)
                precio_max_desc = round(precio_venta * 0.9, 0)
                # cifrar precio
                precio_cifrado = cifrar_precio(precio_venta)

                

                cursor.execute("""
                    INSERT INTO inventarioUnico (
                    descripcion, referencia, cantidad,
                    precioVenta, precioVentaCifrado, precioMaxDescuento, proveedor, fechaActualizacion, grupo
                )
                VALUES ( ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(descripcion) DO UPDATE SET
                    referencia = excluded.referencia,
                    cantidad = inventarioUnico.cantidad + excluded.cantidad,
                    precioVenta = excluded.precioVenta,
                    precioVentaCifrado = excluded.precioVentaCifrado,
                    precioMaxDescuento = excluded.precioMaxDescuento,
                    proveedor = excluded.proveedor,
                    fechaActualizacion = excluded.fechaActualizacion
                    grupo = excluded.grupo;
                """, (
                    
                    descripcion_item,
                    referencia,
                    cantidad,
                    precio_venta,
                    precio_cifrado,
                    precio_max_desc,
                    proveedor,
                    fecha,
                    ''
                    ""
                ))

                # Obtener el id del registro recién insertado/actualizado
                nuevo_id = cursor.lastrowid

                print(nuevo_id)

                # Guardar ese id en inventarioFacturas
                cursor.execute("""
                    UPDATE inventarioFacturas
                    SET inventariado = 1,
                        id_inventarioUnico = ?
                    WHERE id = ?
                """, (nuevo_id, id))
        
        conn.commit()
        conn.close()



    items_list = []
    
    if db_files:
        ruta_db = os.path.join(carpeta_bd, db_files[-1])
        conn = sqlite3.connect(ruta_db)
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

        # ===============================
        # Cargar templates BarTender
        # ===============================
        cursor.execute("""
            SELECT idBartenderFiles, name
            FROM bartenderFiles
            WHERE activo = 1
            ORDER BY name
        """)
        bartender_templates = cursor.fetchall()

        conn.close()

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

        carpeta_bd = os.path.join(os.getcwd(), 'bdlocal')
        db_files = [f for f in os.listdir(carpeta_bd) if f.endswith('.sqlite')]
        
        if not db_files:
            return {'ok': False, 'error': 'Base de datos no encontrada'}, 400

        ruta_db = os.path.join(carpeta_bd, db_files[-1])
        conn = sqlite3.connect(ruta_db)
        cursor = conn.cursor()

        precio_cifrado = cifrar_precio(precio)
        precio_descuento = precio * 0.9
        precio_descuento_redondeado = int(round(precio_descuento, -3))

        # Actualizar el item
        cursor.execute("""
            UPDATE inventarioUnico
            SET descripcion = ?,
                cantidad = ?,
                precioVenta = ?,
                precioVentaCifrado = ?,
                precioMaxDescuento = ?,
                grupo = ?
            WHERE id = ?
        """, (descripcion, cantidad, int(precio), precio_cifrado, precio_descuento_redondeado, grupo, item_id))

        conn.commit()
        conn.close()

        return {'ok': True, 'mensaje': 'Item actualizado correctamente'}
    except Exception as e:
        return {'ok': False, 'error': str(e)}, 500


@inventario_bp.route('/imprimir_etiqueta_nueva', methods=['POST'])
def imprimir_etiqueta_nueva():
    """Imprimir etiqueta(s) de un item"""
    if 'usuario' not in session:
        return {'ok': False, 'error': 'No autenticado'}, 401
    

    carpeta_bd = os.path.join(os.getcwd(), 'bdlocal')
    db_files = [f for f in os.listdir(carpeta_bd) if f.endswith('.sqlite')]

    if not db_files:
        return "No hay base de datos creada", 404

    ruta_db = os.path.join(carpeta_bd, db_files[-1])
    conn = sqlite3.connect(ruta_db)
    cursor = conn.cursor()
    
    try:
        data = request.get_json()

        print(data)
        codigo = data.get('codigo')
        descripcion = data.get('descripcion')
        precio = data.get('precio')
        cantidad = data.get('cantidad', 1)
        columna_inicio = data.get('columna_inicio', 1)
        template_id = data.get('template_id', 'default')

        precioCifrado = cifrar_precio(precio.replace(".", "").strip())

        # -------------------------------
        # Obtener plantilla BarTender
        # -------------------------------
        cursor.execute("""
            SELECT ubicacion
            FROM bartenderFiles
            WHERE idBartenderFiles = ?
        """, (template_id,))
        row = cursor.fetchone()

        if not row:
            conn.close()
            raise Exception("Template BarTender no encontrado")

        ruta_template = row[0]
        conn.close()

        # Crear lista de productos a imprimir
        productos = [(codigo, descripcion, precioCifrado, cantidad)]
        
        # Usar la función imprimir_varios existente
        imprimir_varios(
            productos,
            ruta_template=ruta_template,
            columna_inicio=1
        )

        return {'ok': True, 'mensaje': f'{cantidad} etiqueta(s) enviada(s) a impresión'}
    except Exception as e:
        return {'ok': False, 'error': str(e)}, 500