from flask import Blueprint, render_template, session, redirect, url_for, request, send_from_directory
from datetime import datetime
import config
import os
import sqlite3
import imaplib, email, os, zipfile
from email.utils import parseaddr
import xml.etree.ElementTree as ET
import csv
import win32com.client



# def imprimir_varios(productos, columna_inicio=1):
#     # """
#     # productos: array de tuplas con estructura:
#     #     (codigo, descripcion, precio, cantidad)
#     # columna_inicio: número de columna inicial
#     # """

#     # # 1) Crear CSV temporal con todos los productos
#     # ruta_csv = "productos_temp.csv"
#     # with open(ruta_csv, "w", newline="", encoding="utf-8") as f:
#     #     writer = csv.writer(f)
#     #     writer.writerow(["codigo", "descripcion", "precio", "copias", "inicio_columna"])
#     #     for codigo, descripcion, precio, cantidad in productos:
#     #         writer.writerow([
#     #             codigo,
#     #             descripcion,
#     #             precio,
#     #             cantidad,
#     #             columna_inicio
#     #         ])

#     # # 2) Abrir BarTender
#     # bt = win32com.client.Dispatch("BarTender.Application")
#     # bt.Visible = True

#     # # 3) Abrir plantilla
#     # formato = bt.Formats.Open(r"Documento1.btw", False, "")

#     # # 4) Conectar el CSV como base de datos
#     # formato.DatabaseConnections.SetFileName('Conexión 1', ruta_csv)

#     # # 5) Imprimir todos los registros del array
#     # formato.PrintOut(False, False)

#     # # 6) Cerrar plantilla y BarTender
#     # formato.Close(0)
#     # bt.Quit(1)
    




def imprimir_varios(productos, columna_inicio=1):
    ruta_csv = os.path.abspath("productos_temp.csv")
    with open(ruta_csv, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["codigo", "descripcion", "precio", "copias", "inicio_columna"])
        for codigo, descripcion, precio, cantidad in productos:
            writer.writerow([codigo, descripcion, precio, cantidad, columna_inicio])


    bt = win32com.client.Dispatch("BarTender.Application")
    bt.Visible = True

    formato = bt.Formats.Open(r"Documento1.btw", False, "")
    formato.PrintOut(False, False)

    #formato.Close(0)
    #bt.Quit(1)


facturas_bp = Blueprint('facturas', __name__, url_prefix='/facturas')

@facturas_bp.route('/', methods=['GET', 'POST'])
def facturas():
    if 'usuario' not in session:
        return redirect(url_for('auth.login'))

    mensaje = None
    hoy = datetime.now().strftime('%Y-%m-%d')
    fecha_desde = request.form.get('fecha_desde', hoy)
    fecha_hasta = request.form.get('fecha_hasta', hoy)

    print(f"Fecha desde: {fecha_desde}, Fecha hasta: {fecha_hasta}")

    if request.method == 'POST':
        EMAIL = obtener_email_configurado()
        PASSWORD = obtener_password_configurado()
        DOMINIOS_DE_INTERES = obtener_dominios_configurados()
        IMAP_SERVER = 'imap.gmail.com'
        FOLDER_DESTINO = 'bin'
        FOLDER_DESTINO_XML = 'bin/xml/'
        FOLDER_DESTINO_PDF = 'bin/pdf/'

        #print(f"Email: {EMAIL}, Password: {PASSWORD}, Dominios: {DOMINIOS_DE_INTERES}") 

        fecha_desde_imap = datetime.strptime(fecha_desde, '%Y-%m-%d').strftime('%d-%b-%Y')
        fecha_hasta_imap = datetime.strptime(fecha_hasta, '%Y-%m-%d').strftime('%d-%b-%Y')


        os.makedirs(FOLDER_DESTINO_XML, exist_ok=True)
        os.makedirs(FOLDER_DESTINO_PDF, exist_ok=True)
        mail = imaplib.IMAP4_SSL(IMAP_SERVER)
        mail.login(EMAIL, PASSWORD)
        mail.select("[Gmail]/Todos")

        criterio_busqueda = f'(SINCE {fecha_desde_imap} BEFORE {fecha_hasta_imap})'
        status, messages = mail.search(None, criterio_busqueda)
        contador = 0
        for num in messages[0].split():
            status, data = mail.fetch(num, '(RFC822)')
            msg = email.message_from_bytes(data[0][1])
            raw_remitente = msg.get("From")
            _, correo_real = parseaddr(raw_remitente)

            print(f"Procesando correo: {correo_real} y remitente: {raw_remitente}")

            if not any(d.lower() in correo_real.lower() for d in DOMINIOS_DE_INTERES):
                continue
            for part in msg.walk():
                if part.get_content_maintype() == 'multipart':
                    continue
                if part.get('Content-Disposition') is None:
                    continue
                nombre_archivo = part.get_filename()
                if nombre_archivo and nombre_archivo.lower().endswith('.zip'):
                    zip_path = os.path.join(FOLDER_DESTINO, nombre_archivo)
                    with open(zip_path, 'wb') as f:
                        f.write(part.get_payload(decode=True))
                    with zipfile.ZipFile(zip_path, 'r') as zip_ref:
                        for file in zip_ref.namelist():
                            if file.lower().endswith('.xml'):
                                zip_ref.extract(file, FOLDER_DESTINO_XML)
                                ruta_xml = os.path.join(FOLDER_DESTINO_XML, file)
                                try:
                                    tree = ET.parse(ruta_xml)
                                    root = tree.getroot()
                                    ns = {
                                        'cac': 'urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2',
                                        'cbc': 'urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2'
                                    }
                                    # Detectar si es AttachedDocument con CDATA o Invoice directo
                                    if root.tag.endswith("AttachedDocument"):
                                        description_el = root.find('.//cbc:Description', ns)
                                        if description_el is not None and description_el.text:
                                            invoice_root = ET.fromstring(description_el.text)
                                        else:
                                            continue
                                    elif root.tag.endswith("Invoice"):
                                        invoice_root = root
                                    else:
                                        continue

                                    proveedor_el = invoice_root.find('.//cac:AccountingSupplierParty//cac:PartyName/cbc:Name', ns)
                                    nombre_proveedor = proveedor_el.text.strip() if proveedor_el is not None and proveedor_el.text else 'Desconocido'

                                    fecha_el = invoice_root.find('.//cbc:IssueDate', ns)
                                    fecha_venta = fecha_el.text.strip() if fecha_el is not None and fecha_el.text else 'Sin fecha'

                                    valor_total_el = invoice_root.find('.//cbc:LegalMonetaryTotal/cbc:PayableAmount', ns)
                                    valor_total = float(valor_total_el.text.strip()) if valor_total_el is not None and valor_total_el.text else 0

                                    # Extraer fragmento XML de los items
                                    items_fragment = invoice_root.findall('.//cac:InvoiceLine', ns)
                                    items_xml = ''.join([ET.tostring(item, encoding='unicode') for item in items_fragment])

                                    id_factura = os.path.splitext(file)[0]

                                    # Buscar el PDF correspondiente en el ZIP
                                    pdf_filename = None
                                    for pdf_file in zip_ref.namelist():
                                        if pdf_file.lower().endswith('.pdf'):
                                            # Puedes mejorar la lógica de asociación aquí si hay más de un PDF
                                            pdf_filename = pdf_file
                                            zip_ref.extract(pdf_file, FOLDER_DESTINO_PDF)
                                            break

                                    if pdf_filename:
                                        ubicacion_pdf = os.path.join(FOLDER_DESTINO_PDF, pdf_filename)
                                    else:
                                        ubicacion_pdf = ''

                                    # Insertar en la base de datos
                                    carpeta_bd = os.path.join(os.getcwd(), 'bdlocal')
                                    db_files = [f for f in os.listdir(carpeta_bd) if f.endswith('.sqlite')]
                                    if db_files:
                                        ruta_db = os.path.join(carpeta_bd, db_files[-1])
                                        conn = sqlite3.connect(ruta_db)
                                        cursor = conn.cursor()
                                        cursor.execute("SELECT COUNT(*) FROM facturas WHERE id_factura = ?", (id_factura,))
                                        existe = cursor.fetchone()[0]
                                        if not existe:
                                            cursor.execute("""
                                                INSERT INTO facturas (id_factura, fecha, proveedor, valor_total, correo, texto_xml, ubicacion_pdf)
                                                VALUES (?, ?, ?, ?, ?, ?, ?)
                                            """, (id_factura, fecha_venta, nombre_proveedor, valor_total, correo_real, items_xml, ubicacion_pdf))
                                            #conn.commit()

                                            # Insertar los ítems de la factura en inventarioFacturas
                                            # for line in invoice_root.findall('.//cac:InvoiceLine', ns):
                                            #     descripcion_el = line.find('.//cac:Item/cbc:Description', ns)
                                            #     descripcion = descripcion_el.text.strip() if descripcion_el is not None and descripcion_el.text else 'SIN_DESCRIPCION'

                                            #     cantidad_el = line.find('cbc:InvoicedQuantity', ns)
                                            #     cantidad = int(float(cantidad_el.text.strip() if cantidad_el is not None and cantidad_el.text else 0))

                                            #     precio_unitario_el = line.find('.//cac:Price/cbc:PriceAmount', ns)
                                            #     precio_unitario = float(precio_unitario_el.text.strip() if precio_unitario_el is not None and precio_unitario_el.text else 0)

                                            #     cursor.execute("""
                                            #         INSERT OR REPLACE INTO inventarioFacturas (id_factura, descripcion_item, cantidad, valor_unitario)
                                            #         VALUES (?, ?, ?, ?)
                                            #     """, (id_factura, descripcion, cantidad, precio_unitario))
                                            # conn.commit()

                                            for line in invoice_root.findall('.//cac:InvoiceLine', ns):
                                                # Descripción
                                                descripcion_el = line.find('.//cac:Item/cbc:Description', ns)
                                                descripcion = descripcion_el.text.strip() if descripcion_el is not None and descripcion_el.text else 'SIN_DESCRIPCION'

                                                # Cantidad
                                                cantidad_el = line.find('cbc:InvoicedQuantity', ns)
                                                cantidad = int(float(cantidad_el.text.strip() if cantidad_el is not None and cantidad_el.text else 0))

                                                # Precio unitario
                                                precio_unitario_el = line.find('.//cac:Price/cbc:PriceAmount', ns)
                                                precio_unitario = float(precio_unitario_el.text.strip() if precio_unitario_el is not None and precio_unitario_el.text else 0)

                                                # Referencia (toma SellersItemIdentification o StandardItemIdentification, el que exista)
                                                referencia_1_el = line.find('.//cac:SellersItemIdentification/cbc:ID', ns)
                                                referencia_2_el = line.find('.//cac:StandardItemIdentification/cbc:ID', ns)
                                                referencia = None
                                                if referencia_1_el is not None and referencia_1_el.text:
                                                    referencia = referencia_1_el.text.strip()
                                                elif referencia_2_el is not None and referencia_2_el.text:
                                                    referencia = referencia_2_el.text.strip()
                                                else:
                                                    referencia = 'SIN_REFERENCIA'

                                                # Insertar en la base de datos (agregando el campo referencia)
                                                cursor.execute("""
                                                    INSERT OR REPLACE INTO inventarioFacturas (
                                                        id_factura, descripcion_item, cantidad, valor_unitario, referencia
                                                    )
                                                    VALUES (?, ?, ?, ?, ?)
                                                """, (id_factura, descripcion, cantidad, precio_unitario, referencia))
                                            conn.commit()

                                        conn.close()
                                except Exception as e:
                                    print(f"Error procesando XML {file}: {e}")

                            if file.lower().endswith('.pdf'):
                                zip_ref.extract(file, FOLDER_DESTINO_PDF)
                    os.remove(zip_path)
                    contador += 1
        mail.logout()
        mensaje = f"Descarga completada. Se procesaron {contador} archivos ZIP."

    # Consultar facturas para mostrar en la tabla
    facturas_list = []
    carpeta_bd = os.path.join(os.getcwd(), 'bdlocal')
    db_files = [f for f in os.listdir(carpeta_bd) if f.endswith('.sqlite')]
    if db_files:
        ruta_db = os.path.join(carpeta_bd, db_files[-1])
        conn = sqlite3.connect(ruta_db)
        cursor = conn.cursor()
        cursor.execute("""
            SELECT 
                f.id_factura,
                f.proveedor,
                f.fecha,
                f.valor_total,
                f.ubicacion_pdf,
                COUNT(inf.id) as total_articulos,
                SUM(CASE WHEN inf.inventariado = 0 THEN 1 ELSE 0 END) as articulos_sin_procesar
            FROM facturas f
            LEFT JOIN inventarioFacturas inf ON f.id_factura = inf.id_factura
            GROUP BY f.id_factura
            ORDER BY f.fecha DESC
        """)
        
        facturas_list = cursor.fetchall()
        conn.close()

    return render_template(
        'facturas/facturas.html',
        usuario=session.get('usuario'),
        fecha_desde=fecha_desde,
        fecha_hasta=fecha_hasta,
        mensaje=mensaje,
        facturas_list=facturas_list
    )

@facturas_bp.route('/ver/<id_factura>', methods=['GET', 'POST'])
def ver_factura(id_factura):
    carpeta_bd = os.path.join(os.getcwd(), 'bdlocal')
    db_files = [f for f in os.listdir(carpeta_bd) if f.endswith('.sqlite')]
    factura = None
    factura_items = []

    if not db_files:
        return "No hay base de datos creada", 404

    ruta_db = os.path.join(carpeta_bd, db_files[-1])
    conn = sqlite3.connect(ruta_db)
    cursor = conn.cursor()

    if request.method == 'POST':
        seleccionados = request.form.getlist('seleccionados')

        print("Seleccionados:", seleccionados)
        
        valores_publicos = request.form.getlist('valor_publico')

        # Mapeo chapineros
        mapa_chapineros = {
            "1": "c", "2": "h", "3": "a", "4": "p",
            "5": "i", "6": "n", "7": "e", "8": "r", "9": "o", "0": "s"
        }

        def cifrar_precio(precio):
            return "".join(mapa_chapineros.get(d, d) for d in str(int(precio))).upper()

        # Traer datos de inventarioFacturas
        cursor.execute("""
            SELECT id, descripcion_item, cantidad, valor_unitario, inventariado
            FROM inventarioFacturas
            WHERE id_factura = ?
        """, (id_factura,))
        items_bd = cursor.fetchall()

        print("Items en BD:", items_bd)

        para_imprmir = []

        # Insertar seleccionados en inventarioUnico
        for idx in seleccionados:
            idx = int(idx) - 1  # loop.index en Jinja empieza en 1
            id, descripcion, cantidad, valor_unitario, inventariado = items_bd[idx]

            # Obtener precioVenta ingresado
            precio_str = str(valores_publicos[idx]).replace(".", "").strip()
            precio_venta = int(precio_str) if precio_str.isdigit() else 0

            precio_cifrado = cifrar_precio(precio_venta)
            precio_max_desc = round(precio_venta * 0.9, 0)  # 10% menos

            # Generar código de barras (simple incremental)
            cursor.execute("SELECT MAX(id) FROM inventarioUnico")
            max_id = cursor.fetchone()[0] or 0
            codigo_barras = str(max_id + 1).zfill(8)  # 8 dígitos

            print(codigo_barras,
                descripcion,
                cantidad,
                precio_venta,
                precio_cifrado,
                precio_max_desc)

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
                
                descripcion,
                cantidad,
                precio_venta,
                precio_cifrado,
                precio_max_desc, 
                ''
                ""
            ))

            # Obtener el id del registro recién insertado/actualizado
            nuevo_id = cursor.lastrowid

            para_imprmir.append(nuevo_id)
            # Guardar ese id en inventarioFacturas
            cursor.execute("""
                UPDATE inventarioFacturas
                SET inventariado = 1,
                    id_inventarioUnico = ?
                WHERE id = ?
            """, (nuevo_id, id))

            
        conn.commit()

        ids = para_imprmir  # por ejemplo [40, 41]
        placeholders = ",".join("?" * len(ids)) 

        query = f"""
            SELECT codigoBarras, descripcion, precioVentaCifrado, inventarioFacturas.cantidad
            FROM inventarioFacturas
            LEFT JOIN main.inventarioUnico iU on inventarioFacturas.id_inventarioUnico = iU.id
            WHERE id_factura = ? AND iU.id IN ({placeholders})
        """
        cursor.execute(query, (id_factura, *ids))
        items_para_imprimir = cursor.fetchall()

        imprimir_varios(items_para_imprimir, columna_inicio=1)


        conn.close()
        return redirect(url_for('facturas.ver_factura', id_factura=id_factura))

    # Modo GET: mostrar factura
    cursor.execute("""
        SELECT proveedor, fecha, valor_total, texto_xml, ubicacion_pdf
        FROM facturas
        WHERE id_factura = ?
    """, (id_factura,))
    factura = cursor.fetchone()

    cursor.execute("""
        SELECT descripcion_item, cantidad, valor_unitario, inventariado
        FROM inventarioFacturas
        WHERE id_factura = ?
    """, (id_factura,))
    factura_items = cursor.fetchall()
    conn.close()

    return render_template(
        'facturas/detalle.html',
        factura=factura,
        id_factura=id_factura,
        items_factura=factura_items
    )

@facturas_bp.route('/pdf/<filename>')
def ver_pdf(filename):
    pdf_folder = os.path.join(os.getcwd(), 'bin', 'pdf')
    print(f"Buscando PDF en: {pdf_folder} filename: {filename}")
    return send_from_directory(pdf_folder, filename)

def obtener_ultima_configuracion():
    carpeta_bd = os.path.join(os.getcwd(), 'bdlocal')
    db_files = [f for f in os.listdir(carpeta_bd) if f.endswith('.sqlite')]
    if not db_files:
        return '', '', ''
    ruta_db = os.path.join(carpeta_bd, db_files[-1])
    try:
        conn = sqlite3.connect(ruta_db)
        cursor = conn.cursor()
        cursor.execute("SELECT email_revision, token_contraseña, correos_varios FROM configuracion ORDER BY id DESC LIMIT 1")
        row = cursor.fetchone()
        conn.close()
        if row:
            email_revision, token_contraseña, correos_varios = row
            return email_revision, token_contraseña, correos_varios
    except Exception:
        pass
    return '', '', ''

def obtener_email_configurado():
    email, _, _ = obtener_ultima_configuracion()
    return email

def obtener_password_configurado():
    _, password, _ = obtener_ultima_configuracion()
    return password

def obtener_dominios_configurados():
    _, _, correos = obtener_ultima_configuracion()
    return [correo.strip() for correo in correos.split(',') if correo.strip()]
