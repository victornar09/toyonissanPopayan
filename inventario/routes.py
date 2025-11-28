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

def imprimir_etiqueta(codigo, descripcion, precio, cantidad, columna_inicio=1):
    bt = win32com.client.Dispatch('BarTender.Application')
    bt.Visible = False

    # Ruta absoluta al documento BTW
    formato = bt.Formats.Open(r"Documento1.btw", False, "")

    formato.SetNamedSubStringValue("codigo", codigo)
    formato.SetNamedSubStringValue("descripcion", descripcion)
    formato.SetNamedSubStringValue("precio", str(precio))
    formato.SetNamedSubStringValue("copias", str(cantidad))
    formato.SetNamedSubStringValue("inicio_columna", str(columna_inicio))

    formato.PrintOut(True, False)

    formato.Close(0)
    # bt.Quit(1)git

ANCHO_MM = 50
ALTO_MM = 40

# Definición de cajas (origen arriba-izquierda)
cajas = {
    "referencia":   {"x": 2, "y": 21,  "w": 30, "h": 4},
    "descripcion":  {"x": 2, "y": 3,  "w": 46, "h": 18},
    "barcode":      {"x": -1, "y": 25, "w": 36, "h": 10},
    "barcode_text": {"x": 2, "y": 35, "w": 30, "h": 4},
    "logo":         {"x": 32, "y": 21.5, "w": 16, "h": 16},
}

def y_top(y_mm, h_mm):
    """Convierte coordenada superior-izquierda a sistema ReportLab (origen abajo)."""
    return (ALTO_MM - y_mm - h_mm) * mm

def dibujar_etiqueta_pdf(descripcion, codigo="", referencia="",
                         logo_path="bin/png/tnp-blanconegro-sinfondo.png",
                          debug=False):
    
    archivo = f"bin/codigos/{codigo}.pdf"

    c = canvas.Canvas(archivo, pagesize=(ANCHO_MM*mm, ALTO_MM*mm))

    # =========================
    # Dibujar cajas de referencia si debug=True
    # =========================
    if debug:
        for caja in cajas.values():
            c.setLineWidth(0.5)
            c.setDash(3,3)
            c.rect(caja["x"]*mm,
                   y_top(caja["y"], caja["h"]),
                   caja["w"]*mm,
                   caja["h"]*mm)
        c.setDash()  # reset

    # =========================
    # Referencia
    # =========================
    caja = cajas["referencia"]
    c.setFont("Helvetica-Bold", 7)

    # calcular ancho del texto
    text_w = c.stringWidth(referencia, "Helvetica-Bold", 7)

    # posición centrada
    x_pos = caja["x"]*mm + (caja["w"]*mm - text_w)/2
    y_pos = y_top(caja["y"], caja["h"]) + (caja["h"]*mm - 7)/2  # más o menos centrado verticalmente

    c.drawString(x_pos, y_pos, referencia)

    # =========================
    # Descripción (multilínea)
    # =========================
    caja = cajas["descripcion"]
    styles = getSampleStyleSheet()
    style = styles["Normal"]
    style.fontName = "Helvetica-Bold"
    style.fontSize = 8
    style.leading = 11

    p = Paragraph(descripcion, style)
    frame = Frame(caja["x"]*mm, y_top(caja["y"], caja["h"]),
                  caja["w"]*mm, caja["h"]*mm, showBoundary=0)
    frame.addFromList([p], c)

    # =========================
    # Código de barras (escalar al ancho de la caja)
    # =========================
    caja = cajas["barcode"]
    code128 = barcode.get("code128", codigo, writer=ImageWriter())
    buffer = BytesIO()
    code128.write(buffer, {"module_height": 15.0, "font_size": 0})
    buffer.seek(0)

    barcode_img = Image.open(buffer)
    barcode_reader = ImageReader(barcode_img)

    img_w, img_h = barcode_img.size
    box_w, box_h = caja["w"]*mm, caja["h"]*mm

    # Escalamos SOLO al ancho
    scale = box_w / img_w
    new_w, new_h = img_w*scale, img_h*scale

    # Si se pasa de alto, reducimos proporcionalmente
    if new_h > box_h:
        scale = box_h / img_h
        new_w, new_h = img_w*scale, img_h*scale

        


    x_pos = caja["x"]*mm + (box_w - new_w)/2
    y_pos = y_top(caja["y"], caja["h"]) + (box_h - new_h)/2
    c.drawImage(barcode_reader, caja["x"]*mm, y_top(caja["y"], caja["h"]),
            width=box_w, height=box_h, preserveAspectRatio=False, mask='auto')


    # =========================
    # Texto del código
    # =========================
    caja = cajas["barcode_text"]
    c.setFont("Helvetica", 8)
    text_w = c.stringWidth(codigo, "Helvetica", 8)
    c.drawString(caja["x"]*mm + (caja["w"]*mm - text_w)/2,
                 y_top(caja["y"], caja["h"]) + 1*mm, codigo)

    # =========================
    # Logo
    # =========================
    caja = cajas["logo"]
    try:
        logo_img = Image.open(logo_path)
        logo_reader = ImageReader(logo_img)
        c.drawImage(logo_reader,
                    caja["x"]*mm,
                    y_top(caja["y"], caja["h"]),
                    caja["w"]*mm,
                    caja["h"]*mm,
                    preserveAspectRatio=True,
                    mask='auto')
    except FileNotFoundError:
        c.setFont("Helvetica", 6)
        c.drawString(caja["x"]*mm, y_top(caja["y"], caja["h"]), "NO LOGO")

    c.showPage()
    c.save()
    print(f"Etiqueta guardada en {archivo}")


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
                SELECT id_factura,
                    descripcion_item,
                    cantidad,
                    valor_unitario,
                    referencia,
                    inventariado,
                    fecha
                FROM (
                    SELECT
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

            # Preparamos para recorrer items
            for item in items_unicos:
                (
                    id_factura, descripcion_item, cantidad,
                    valor_unitario, referencia, inventariado, fecha
                ) = item

                # Calcular valores requeridos
                cantidad_final = 0
                precio_venta = int(round(valor_unitario, -3))  

                # aplicar 10% descuento y redondear a miles
                precio_descuento = precio_venta * 0.9
                precio_descuento_redondeado = int(round(precio_descuento, -3))  

                # cifrar precio
                precio_cifrado = cifrar_precio(precio_venta)

                # Verificar si ya existe en inventarioUnico
                cursor.execute("""
                    SELECT codigoBarras 
                    FROM inventarioUnico 
                    WHERE descripcion = ?
                """, (descripcion_item,))
                existente = cursor.fetchone()


                if existente:  
                    
                    # ✅ Ya existe → actualizar (pero sin tocar el código de barras)
                    cursor.execute("""
                        UPDATE inventarioUnico
                        SET descripcion = ?,
                            cantidad = ?,
                            precioVenta = ?,
                            precioVentaCifrado = ?,
                            precioMaxDescuento = ?,
                            grupo = ?
                        WHERE codigoBarras = ?
                    """, (
                        descripcion_item,
                        cantidad_final,
                        precio_venta,
                        precio_cifrado,
                        precio_descuento_redondeado,
                        "",
                        existente[0]
                    ))

                    dibujar_etiqueta_pdf(
                        descripcion=descripcion_item,
                        codigo=existente[0],
                        referencia=precio_cifrado,
                        debug=False
                    )

                else:
                    # ❌ No existe → crear nuevo código de barras incremental
                    cursor.execute("SELECT MAX(id) FROM inventarioUnico")
                    max_id = cursor.fetchone()[0] or 0
                    codigo_barras = str(max_id + 1).zfill(8)  # 8 dígitos

                    dibujar_etiqueta_pdf(
                        descripcion=descripcion_item,
                        codigo=codigo_barras,
                        referencia=precio_cifrado,
                        debug=False
                    )

                    # Insertar el nuevo item en inventarioUnico
                    cursor.execute("""
                        INSERT INTO inventarioUnico
                        (codigoBarras, descripcion, cantidad, precioVenta, precioVentaCifrado, precioMaxDescuento, grupo)
                        VALUES (?, ?, ?, ?, ?, ?, ?)
                    """, (
                        codigo_barras,
                        descripcion_item,
                        cantidad_final,
                        precio_venta,
                        precio_cifrado,
                        precio_descuento_redondeado,
                        ""
                    ))

        conn.commit()
        conn.close()



    items_list = []
    
    if db_files:
        ruta_db = os.path.join(carpeta_bd, db_files[-1])
        conn = sqlite3.connect(ruta_db)
        cursor = conn.cursor()
        cursor.execute("""
            SELECT
                --id,
                codigoBarras,
                descripcion,
                cantidad,
                precioVenta,
                --precioVentaCifrado,
                --precioMaxDescuento,
                grupo
            FROM inventarioUnico;
        """)
        
        items_list = cursor.fetchall()
        conn.close()

    return render_template(
        'inventario/inventario.html', 
        usuario=session.get('usuario'),
        items=items_list)




@inventario_bp.route("/imprimir_etiqueta", methods=["POST"])
def imprimir_etiqueta_route():
    data = request.json

    codigo = data.get("codigo")
    descripcion = data.get("descripcion")
    precio = data.get("precio")
    cantidad = data.get("cantidad", 1)
    columna_inicio = data.get("columna_inicio", 1)

    try:
        imprimir_etiqueta(
            codigo=codigo,
            descripcion=descripcion,
            precio=precio,
            cantidad=cantidad,
            columna_inicio=columna_inicio
        )
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500