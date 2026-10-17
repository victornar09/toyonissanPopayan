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



def imprimir_varios(productos, ruta_template, columna_inicio=1):
    # CSV temporal (BarTender lo lee)
    ruta_csv = os.path.abspath("productos_temp.csv")

    with open(ruta_csv, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["codigo", "descripcion", "precio", "copias","proveedor", "inicio_columna"])
        for codigo, descripcion, precio, cantidad,proveedor in productos:
            writer.writerow([
                codigo,
                descripcion,
                precio,
                cantidad,
                proveedor,
                columna_inicio
            ])

    bt = win32com.client.Dispatch("BarTender.Application")
    bt.Visible = True


    formato = bt.Formats.Open(
        os.path.abspath(ruta_template),
        False,
        ""
    )

    formato.PrintOut(False, False)

    # Si luego quieres cerrar:
    # formato.Close(0)
    # bt.Quit(1)


facturas_bp = Blueprint('facturas', __name__, url_prefix='/facturas')

@facturas_bp.route('/', methods=['GET', 'POST'])
def facturas():
    if 'usuario' not in session:
        return redirect(url_for('auth.login'))

    mensaje = None
    hoy = datetime.now().strftime('%Y-%m-%d')
    fecha_desde = request.form.get('fecha_desde', hoy)
    fecha_hasta = request.form.get('fecha_hasta', hoy)

    if request.method == 'POST':

        EMAIL = obtener_email_configurado()
        PASSWORD = obtener_password_configurado()
        IMAP_SERVER = 'imap.gmail.com'

        FOLDER_DESTINO = 'bin'
        FOLDER_DESTINO_XML = 'bin/xml/'
        FOLDER_DESTINO_PDF = 'bin/pdf/'

        fecha_desde_imap = datetime.strptime(
            fecha_desde, '%Y-%m-%d'
        ).strftime('%d-%b-%Y')

        fecha_hasta_imap = datetime.strptime(
            fecha_hasta, '%Y-%m-%d'
        ).strftime('%d-%b-%Y')

        os.makedirs(FOLDER_DESTINO_XML, exist_ok=True)
        os.makedirs(FOLDER_DESTINO_PDF, exist_ok=True)

        # -----------------------------
        # Abrir la base de datos UNA sola vez
        # -----------------------------

        carpeta_bd = os.path.join(os.getcwd(), 'bdlocal')
        db_files = [f for f in os.listdir(carpeta_bd) if f.endswith('.sqlite')]

        if not db_files:
            mensaje = "No existe base de datos."
            return render_template(
                "facturas/facturas.html",
                mensaje=mensaje
            )

        ruta_db = os.path.join(carpeta_bd, db_files[-1])

        conn = sqlite3.connect(ruta_db)
        cursor = conn.cursor()

        # -----------------------------
        # Facturas ya descargadas
        # -----------------------------

        cursor.execute("SELECT id_factura FROM facturas")
        facturas_existentes = {
            fila[0] for fila in cursor.fetchall()
        }

        # -----------------------------
        # Proveedores
        # -----------------------------

        cursor.execute("""
            SELECT correoFacturas, nit
            FROM proveedor
            WHERE nit IS NOT NULL
            AND TRIM(nit) <> ''
        """)

        proveedores = cursor.fetchall()

        # -----------------------------
        # Conectar Gmail
        # -----------------------------

        mail = imaplib.IMAP4_SSL(IMAP_SERVER)
        mail.login(EMAIL, PASSWORD)
        mail.select("[Gmail]/Todos")

        contador = 0

        # ======================================================
        # Buscar proveedor por proveedor
        # ======================================================

        for correo_proveedor, nit in proveedores:

            criterio = (
                f'(SINCE {fecha_desde_imap} '
                f'BEFORE {fecha_hasta_imap} '
                f'SUBJECT "{nit}")'
            )

            status, messages = mail.search(None, criterio)

            if status != "OK":
                continue

            if not messages[0]:
                continue

            for num in messages[0].split():

                status, data = mail.fetch(num, '(RFC822)')

                if status != "OK":
                    continue

                msg = email.message_from_bytes(data[0][1])

                raw_remitente = msg.get("From")
                _, correo_real = parseaddr(raw_remitente)

                print(
                    f"Proveedor: {nit} | "
                    f"Correo: {correo_real}"
                )

                for part in msg.walk():

                    if part.get_content_maintype() == 'multipart':
                        continue

                    if part.get('Content-Disposition') is None:
                        continue

                    nombre_archivo = part.get_filename()

                    if not nombre_archivo:
                        continue

                    if not nombre_archivo.lower().endswith(".zip"):
                        continue

                    zip_path = os.path.join(
                        FOLDER_DESTINO,
                        nombre_archivo
                    )

                    with open(zip_path, "wb") as f:
                        f.write(part.get_payload(decode=True))

                    with zipfile.ZipFile(zip_path, "r") as zip_ref:

                        for file in zip_ref.namelist():

                            if file.lower().endswith(".xml"):

                                zip_ref.extract(file, FOLDER_DESTINO_XML)

                                ruta_xml = os.path.join(
                                    FOLDER_DESTINO_XML,
                                    file
                                )

                                try:

                                    tree = ET.parse(ruta_xml)
                                    root = tree.getroot()

                                    ns = {
                                        'cac': 'urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2',
                                        'cbc': 'urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2'
                                    }

                                    if root.tag.endswith("AttachedDocument"):

                                        description_el = root.find(
                                            './/cbc:Description',
                                            ns
                                        )

                                        if description_el is None or not description_el.text:
                                            continue

                                        invoice_root = ET.fromstring(
                                            description_el.text
                                        )

                                    elif root.tag.endswith("Invoice"):

                                        invoice_root = root

                                    else:
                                        continue

                                    proveedor_el = invoice_root.find(
                                        './/cac:AccountingSupplierParty//cac:PartyName/cbc:Name',
                                        ns
                                    )

                                    nombre_proveedor = (
                                        proveedor_el.text.strip()
                                        if proveedor_el is not None and proveedor_el.text
                                        else "Desconocido"
                                    )

                                    fecha_el = invoice_root.find(
                                        ".//cbc:IssueDate",
                                        ns
                                    )

                                    fecha_venta = (
                                        fecha_el.text.strip()
                                        if fecha_el is not None and fecha_el.text
                                        else ""
                                    )

                                    valor_total_el = invoice_root.find(
                                        ".//cbc:LegalMonetaryTotal/cbc:PayableAmount",
                                        ns
                                    )

                                    valor_total = (
                                        float(valor_total_el.text)
                                        if valor_total_el is not None
                                        else 0
                                    )

                                    items_fragment = invoice_root.findall(
                                        ".//cac:InvoiceLine",
                                        ns
                                    )

                                    items_xml = ''.join(
                                        ET.tostring(
                                            item,
                                            encoding="unicode"
                                        )
                                        for item in items_fragment
                                    )

                                    id_factura = os.path.splitext(file)[0]

                                    # Ya existe
                                    if id_factura in facturas_existentes:
                                        continue

                                    pdf_filename = ""

                                    for pdf_file in zip_ref.namelist():

                                        if pdf_file.lower().endswith(".pdf"):

                                            zip_ref.extract(
                                                pdf_file,
                                                FOLDER_DESTINO_PDF
                                            )

                                            pdf_filename = pdf_file
                                            break

                                    ubicacion_pdf = (
                                        os.path.join(
                                            FOLDER_DESTINO_PDF,
                                            pdf_filename
                                        )
                                        if pdf_filename
                                        else ""
                                    )

                                    cursor.execute("""
                                        INSERT INTO facturas
                                        (
                                            id_factura,
                                            fecha,
                                            proveedor,
                                            valor_total,
                                            correo,
                                            texto_xml,
                                            ubicacion_pdf
                                        )
                                        VALUES
                                        (?,?,?,?,?,?,?)
                                    """,
                                    (
                                        id_factura,
                                        fecha_venta,
                                        nombre_proveedor,
                                        valor_total,
                                        correo_real,
                                        items_xml,
                                        ubicacion_pdf
                                    ))

                                    facturas_existentes.add(id_factura)

                                    for line in invoice_root.findall(
                                        ".//cac:InvoiceLine",
                                        ns
                                    ):

                                        descripcion_el = line.find(
                                            ".//cac:Item/cbc:Description",
                                            ns
                                        )

                                        descripcion = (
                                            descripcion_el.text.strip()
                                            if descripcion_el is not None and descripcion_el.text
                                            else "SIN_DESCRIPCION"
                                        )

                                        cantidad_el = line.find(
                                            "cbc:InvoicedQuantity",
                                            ns
                                        )

                                        cantidad = int(float(
                                            cantidad_el.text
                                            if cantidad_el is not None
                                            else 0
                                        ))

                                        precio_el = line.find(
                                            ".//cac:Price/cbc:PriceAmount",
                                            ns
                                        )

                                        precio = (
                                            float(precio_el.text)
                                            if precio_el is not None
                                            else 0
                                        )

                                        ref1 = line.find(
                                            ".//cac:SellersItemIdentification/cbc:ID",
                                            ns
                                        )

                                        ref2 = line.find(
                                            ".//cac:StandardItemIdentification/cbc:ID",
                                            ns
                                        )

                                        if ref1 is not None and ref1.text:
                                            referencia = ref1.text.strip()

                                        elif ref2 is not None and ref2.text:
                                            referencia = ref2.text.strip()

                                        else:
                                            referencia = "SIN_REFERENCIA"

                                        cursor.execute("""
                                            INSERT OR REPLACE INTO inventarioFacturas
                                            (
                                                id_factura,
                                                descripcion_item,
                                                cantidad,
                                                valor_unitario,
                                                referencia
                                            )
                                            VALUES
                                            (?,?,?,?,?)
                                        """,
                                        (
                                            id_factura,
                                            descripcion,
                                            cantidad,
                                            precio,
                                            referencia
                                        ))

                                    contador += 1

                                except Exception as e:

                                    print(
                                        f"Error procesando XML {file}: {e}"
                                    )

                            elif file.lower().endswith(".pdf"):

                                zip_ref.extract(
                                    file,
                                    FOLDER_DESTINO_PDF
                                )

                    os.remove(zip_path)

        conn.commit()
        conn.close()

        mail.logout()

        mensaje = (
            f"Descarga completada. "
            f"Se procesaron {contador} facturas."
        )

    # return render_template(
    #     "facturas/facturas.html",
    #     mensaje=mensaje
    # )

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

    if not db_files:
        return "No hay base de datos creada", 404

    ruta_db = os.path.join(carpeta_bd, db_files[-1])
    conn = sqlite3.connect(ruta_db)
    cursor = conn.cursor()

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

    # ===============================
    # POST → Inventariar
    # ===============================
    if request.method == 'POST':
        seleccionados = request.form.getlist('seleccionados')
        valores_publicos = request.form.getlist('valor_publico')
        template_id = request.form.get("template_id")

        if not seleccionados:
            conn.close()
            return redirect(url_for('facturas.ver_factura', id_factura=id_factura))

        # -------------------------------
        # Cifrado chapineros
        # -------------------------------
        mapa_chapineros = {
            "1": "c", "2": "h", "3": "a", "4": "p",
            "5": "i", "6": "n", "7": "e", "8": "r", "9": "o", "0": "s"
        }

        def cifrar_precio(precio):
            return "".join(mapa_chapineros.get(d, d) for d in str(int(precio))).upper()

        # -------------------------------
        # Items de la factura
        # -------------------------------
        cursor.execute("""
            SELECT a.id, a.referencia, a.descripcion_item, a.cantidad, a.valor_unitario, a.inventariado, b.proveedor, b.fecha
            FROM inventarioFacturas a
            JOIN facturas b 
            WHERE a.id_factura = ?
        """, (id_factura,))
        items_bd = cursor.fetchall()

        ids_para_imprimir = []

        # -------------------------------
        # Inventariar seleccionados
        # -------------------------------
        for idx in seleccionados:
            idx = int(idx) - 1  # Jinja empieza en 1
            id_fact_item, referencia, descripcion, cantidad, valor_unitario, inventariado, proveedor, fecha = items_bd[idx]

            precio_str = str(valores_publicos[idx]).replace(".", "").strip()
            precio_venta = int(precio_str) if precio_str.isdigit() else 0

            precio_cifrado = cifrar_precio(precio_venta)
            precio_max_desc = round(precio_venta * 0.9, 0)

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
                    fechaActualizacion = excluded.fechaActualizacion,
                    grupo = excluded.grupo;
                """, (
                    
                    descripcion,
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

            # Obtener ID real del inventario
            cursor.execute("""
                SELECT id
                FROM inventarioUnico
                WHERE descripcion = ?
            """, (descripcion,))
            inventario_id = cursor.fetchone()[0]

            ids_para_imprimir.append(inventario_id)

            # Marcar como inventariado
            cursor.execute("""
                UPDATE inventarioFacturas
                SET inventariado = 1,
                    id_inventarioUnico = ?
                WHERE id = ?
            """, (inventario_id, id_fact_item))

        conn.commit()

        # -------------------------------
        # Datos para impresión
        # -------------------------------
        placeholders = ",".join("?" * len(ids_para_imprimir))
        query = f"""
            SELECT 
                iU.codigoBarras,
                iU.descripcion,
                iU.precioVentaCifrado,
                iF.cantidad, 
                PR.siglas
            FROM inventarioFacturas iF
            JOIN inventarioUnico iU ON iF.id_inventarioUnico = iU.id
            JOIN proveedor PR ON PR.proveedor = iU.proveedor
            WHERE iF.id_factura = ?
              AND iU.id IN ({placeholders})
        """
        cursor.execute(query, (id_factura, *ids_para_imprimir))
        items_para_imprimir = cursor.fetchall()

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

        # -------------------------------
        # IMPRIMIR
        # -------------------------------
        imprimir_varios(
            items_para_imprimir,
            ruta_template=ruta_template,
            columna_inicio=1
        )

        return redirect(url_for('facturas.ver_factura', id_factura=id_factura))

    # ===============================
    # GET → Mostrar factura
    # ===============================
    # cursor.execute("""
    #     SELECT proveedor, fecha, valor_total
    #     FROM facturas
    #     WHERE id_factura = ?
    # """, (id_factura,))
    # factura = cursor.fetchone()

    # cursor.execute("""
    #     SELECT referencia, descripcion_item, cantidad, valor_unitario, inventariado
    #     FROM inventarioFacturas
    #     WHERE id_factura = ?
    # """, (id_factura,))
    # items_factura = cursor.fetchall()


    # return render_template(
    #     'facturas/detalle.html',
    #     factura=factura,
    #     items_factura=items_factura,
    #     bartender_templates=bartender_templates
    # )

    # Modo GET: mostrar factura
    cursor.execute("""
        SELECT proveedor, fecha, valor_total, texto_xml, ubicacion_pdf
        FROM facturas
        WHERE id_factura = ?
    """, (id_factura,))
    factura = cursor.fetchone()

    cursor.execute("""
        SELECT referencia, descripcion_item, cantidad, valor_unitario, inventariado
        FROM inventarioFacturas
        WHERE id_factura = ?
    """, (id_factura,))
    factura_items = cursor.fetchall()
    conn.close()

    return render_template(
        'facturas/detalle.html',
        factura=factura,
        id_factura=id_factura,
        items_factura=factura_items,
        bartender_templates=bartender_templates
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
    carpeta_bd = os.path.join(os.getcwd(), 'bdlocal')
    db_files = [f for f in os.listdir(carpeta_bd) if f.endswith('.sqlite')]
    if not db_files:
        return ''
    ruta_db = os.path.join(carpeta_bd, db_files[-1])
    try:
        conn = sqlite3.connect(ruta_db)
        cursor = conn.cursor()
        cursor.execute("SELECT DISTINCT correoFacturas FROM proveedor")
        correos = cursor.fetchall()
        # cambialo a una lista
        correos_lista = [c[0] for c in correos if c[0]]
        print(f"Dominios configurados: {correos_lista}")
        conn.close()
        return correos_lista
    except Exception:
        pass
    return ''

