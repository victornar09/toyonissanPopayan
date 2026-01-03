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

def imprimir_varios(productos, columna_inicio=1):
    # Mapeo chapineros para cifrar precio
    mapa_chapineros = {
        "1": "c", "2": "h", "3": "a", "4": "p",
        "5": "i", "6": "n", "7": "e", "8": "r", "9": "o", "0": "s"
    }

    def cifrar_precio(precio):
        precio_int = int(float(precio))  # Convertir float primero, luego int
        return "".join(mapa_chapineros.get(d, d) for d in str(precio_int)).upper()

    ruta_csv = os.path.abspath("productos_temp.csv")
    with open(ruta_csv, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["codigo", "descripcion", "precio", "copias", "inicio_columna"])
        for codigo, descripcion, precio, cantidad in productos:
            precio_cifrado = cifrar_precio(precio)
            writer.writerow([codigo, descripcion, precio_cifrado, cantidad, columna_inicio])

    bt = win32com.client.Dispatch("BarTender.Application")
    bt.Visible = True

    formato = bt.Formats.Open(r"Documento1.btw", False, "")
    formato.PrintOut(False, False)


inventario_bp = Blueprint('inventario', __name__, url_prefix='/inventario')

@inventario_bp.route('/', methods=['GET', 'POST'])
def inventario_home():

    if 'usuario' not in session:
        return redirect(url_for('auth.login'))
    
    carpeta_bd = os.path.join(os.getcwd(), 'bdlocal')
    db_files = [f for f in os.listdir(carpeta_bd) if f.endswith('.sqlite')]

    if request.method == 'POST':

        if db_files:

            # Mapeo chapineros
            mapa_chapineros = {
                "1": "c", "2": "h", "3": "a", "4": "p",
                "5": "i", "6": "n", "7": "e", "8": "r", "9": "o", "0": "s"
            }

            def cifrar_precio(precio):
                return "".join(mapa_chapineros.get(d, d) for d in str(int(precio))).upper()

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
                    valor_unitario, referencia, inventariado, fecha
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
                    descripcion, cantidad,
                    precioVenta, precioVentaCifrado, precioMaxDescuento, grupo
                )
                VALUES ( ?, ?, ?, ?, ?, ?)
                ON CONFLICT(descripcion) DO UPDATE SET
                    
                    cantidad = inventarioUnico.cantidad + excluded.cantidad,
                    precioVenta = excluded.precioVenta,
                    precioVentaCifrado = excluded.precioVentaCifrado,
                    precioMaxDescuento = excluded.precioMaxDescuento,
                    grupo = excluded.grupo;
                """, (
                    
                    descripcion_item,
                    cantidad,
                    precio_venta,
                    precio_cifrado,
                    precio_max_desc, 
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
                descripcion,
                cantidad,
                precioVenta,
                precioVentaCifrado, 
                precioMaxDescuento,
                grupo
            FROM inventarioUnico;
        """)
        
        items_list = cursor.fetchall()
        conn.close()

    return render_template(
        'inventario/inventario.html', 
        usuario=session.get('usuario'),
        items=items_list)

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

        # Mapeo chapineros para cifrar precio
        mapa_chapineros = {
            "1": "c", "2": "h", "3": "a", "4": "p",
            "5": "i", "6": "n", "7": "e", "8": "r", "9": "o", "0": "s"
        }

        def cifrar_precio(precio):
            return "".join(mapa_chapineros.get(d, d) for d in str(int(precio))).upper()

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
    
    try:
        data = request.get_json()

        print(data)
        codigo = data.get('codigo')
        descripcion = data.get('descripcion')
        precio = data.get('precio')
        cantidad = data.get('cantidad', 1)
        columna_inicio = data.get('columna_inicio', 1)

        # Crear lista de productos a imprimir
        productos = [(codigo, descripcion, precio, cantidad)]
        
        # Usar la función imprimir_varios existente
        imprimir_varios(productos, columna_inicio)

        return {'ok': True, 'mensaje': f'{cantidad} etiqueta(s) enviada(s) a impresión'}
    except Exception as e:
        return {'ok': False, 'error': str(e)}, 500