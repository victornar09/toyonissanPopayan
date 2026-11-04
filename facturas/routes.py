from flask import Blueprint, render_template, session, redirect, url_for, request, send_from_directory, flash
from datetime import datetime
import os
import imaplib
import email
import zipfile
from email.utils import parseaddr
import xml.etree.ElementTree as ET
import csv
import win32com.client
import pandas as pd

import db
from utilidades import cifrar_precio, a_entero, normalizar_clave
from facturas.validacion_consolidado import obtener_estados_factura, obtener_estado_linea, resolver_proveedor
from facturas.xml_utils import (
    extraer_total_xml,
    extraer_total_desde_elemento,
    obtener_invoice_root,
)
from procesamiento.consolidado import procesar_dataframe_pendientes



def imprimir_varios(productos, ruta_template, columna_inicio=1):
    # CSV temporal (BarTender lo lee)
    ruta_csv = os.path.abspath("productos_temp.csv")

    with open(ruta_csv, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        # Detectar si viene con proveedor
        if productos and len(productos[0]) == 5:
            writer.writerow(["codigo", "descripcion", "precio", "copias", "proveedor", "inicio_columna"])
            for codigo, descripcion, precio, cantidad, proveedor in productos:
                writer.writerow([
                    codigo,
                    descripcion,
                    precio,
                    cantidad,
                    proveedor,
                    columna_inicio
                ])
        else:
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


def _ruta_xml_factura(id_factura):
    """Ubica el XML original de una factura dentro de bin/xml."""

    base = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "bin",
        "xml",
    )

    candidatos = [
        os.path.join(base, f"{id_factura}.xml"),
        os.path.join(base, f"{id_factura}.XML"),
    ]

    for candidato in candidatos:
        if os.path.isfile(candidato):
            return candidato

    try:
        for nombre in os.listdir(base):
            if (
                nombre.lower().endswith(".xml")
                and os.path.splitext(nombre)[0] == str(id_factura)
            ):
                return os.path.join(base, nombre)
    except OSError:
        pass

    return None


def reparar_total_factura(conn, id_factura):
    """Recalcula el total de una factura desde su XML original.

    Devuelve el total (float) o ``None`` si no fue posible obtenerlo.
    """

    ruta_xml = _ruta_xml_factura(id_factura)

    if not ruta_xml:
        print(f"[facturas] No se encontró el XML original de {id_factura}.")
        return None

    try:
        with open(ruta_xml, "rb") as archivo:
            contenido = archivo.read()
    except OSError as error:
        print(f"[facturas] No se pudo leer {ruta_xml}: {error}")
        return None

    resultado = extraer_total_xml(contenido)
    total = resultado.get("total")

    if total is None:
        print(
            "[facturas] Total no válido en "
            f"{os.path.basename(ruta_xml)}: {resultado.get('advertencias')}"
        )
        return None

    cursor = conn.cursor()
    cursor.execute(
        "UPDATE facturas SET valor_total = ? WHERE id_factura = ?",
        (float(total), id_factura),
    )
    return float(total)


def reparar_totales_faltantes(conn, limite=500):
    """Recalcula los totales guardados en NULL/0 usando los XML originales."""

    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT id_factura
        FROM facturas
        WHERE valor_total IS NULL OR valor_total = 0
        ORDER BY fecha DESC
        LIMIT ?
        """,
        (limite,),
    )
    pendientes = [fila[0] for fila in cursor.fetchall() if fila[0]]

    corregidas = 0

    for id_factura in pendientes:
        if reparar_total_factura(conn, id_factura) is not None:
            corregidas += 1

    if corregidas:
        print(f"[facturas] Totales reparados: {corregidas}")

    return corregidas


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

        ruta_db = db.ruta_bd_activa()

        if not ruta_db:
            mensaje = "No existe base de datos."
            return render_template(
                "facturas/facturas.html",
                mensaje=mensaje
            )

        conn = db.conectar(ruta_db)
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

                                    # Misma lógica reutilizable que usa el lector
                                    # de importes: soporta <Invoice> directo y
                                    # <AttachedDocument> con la factura embebida.
                                    invoice_root = obtener_invoice_root(root)

                                    if invoice_root is None:
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

                                    # Total: lectura reutilizable y tolerante a
                                    # namespaces/formatos (Decimal, no float directo).
                                    resultado_total = extraer_total_desde_elemento(
                                        invoice_root
                                    )
                                    total_decimal = resultado_total.get("total")
                                    valor_total = (
                                        float(total_decimal)
                                        if total_decimal is not None
                                        else None
                                    )
                                    if valor_total is None:
                                        print(
                                            "[facturas] Sin total válido en "
                                            f"{nombre_archivo}: "
                                            f"{resultado_total.get('advertencias')}"
                                        )
                                    elif resultado_total.get("advertencias"):
                                        print(
                                            "[facturas] Total con advertencias en "
                                            f"{nombre_archivo}: "
                                            f"{resultado_total.get('advertencias')}"
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
                                            INSERT INTO inventarioFacturas
                                            (
                                                id_factura,
                                                descripcion_item,
                                                cantidad,
                                                valor_unitario,
                                                referencia
                                            )
                                            VALUES
                                            (?,?,?,?,?)
                                            ON CONFLICT (id_factura, referencia, descripcion_item)
                                            DO UPDATE SET
                                                cantidad = excluded.cantidad,
                                                valor_unitario = excluded.valor_unitario,
                                                descripcion_item = excluded.descripcion_item
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
    ruta_db = db.ruta_bd_activa()
    if ruta_db:
        conn = db.conectar(ruta_db)
        cursor = conn.cursor()
        try:
            reparar_totales_faltantes(conn)
            conn.commit()
        except Exception as error:
            print(f"[facturas] No se pudieron reparar totales: {error}")
            conn.rollback()
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

    ruta_db = db.ruta_bd_activa()

    if not ruta_db:
        return "No hay base de datos creada", 404

    # ===============================
    # POST → Inventariar o procesar pendientes a consolidado
    # ===============================
    if request.method == 'POST':

        accion_extra = request.form.get('accion_extra')
        if accion_extra == 'procesar_consolidado':
            ruta_estandarizacion = os.path.join(
                os.path.dirname(os.path.dirname(__file__)),
                "bin/xlsx",
                "estandarizacion.xlsx"
            )
            try:
                with db.conexion(ruta_db) as conn:
                    cursor = conn.cursor()
                    cursor.execute("""
                        SELECT fac.id_factura, prov.idProveedor, fac.proveedor, fac.fecha, fac.valor_total, prov.siglas
                        FROM facturas fac
                        LEFT JOIN proveedor prov ON fac.proveedor = prov.nombreProveedor OR fac.proveedor = prov.nombreProveedorSecundario
                        WHERE fac.id_factura = ?
                    """, (id_factura,))
                    factura = cursor.fetchone()
                    if not factura:
                        flash("No se encontró la factura.", "danger")
                        return redirect(url_for('facturas.ver_factura', id_factura=id_factura))

                    seleccionados_idx = request.form.getlist("seleccionados_consolidado[]")
                    referencias_proveedor = request.form.getlist("referenciaProveedor[]")
                    referencias_repuesto = request.form.getlist("referenciaRepuesto[]")
                    equivalentes = request.form.getlist("equivalente[]")
                    descripciones = request.form.getlist("descripcion[]")
                    marcas = request.form.getlist("marcaRepuesto[]")
                    carros = request.form.getlist("carro[]")
                    lados = request.form.getlist("lado[]")
                    modelos = request.form.getlist("modelo[]")
                    medidas = request.form.getlist("medida[]")
                    precioListado = request.form.getlist("precioListado[]")
                    ids_inv_factura = request.form.getlist("idInventarioFactura[]")

                    seleccionados_set = set()
                    for s in seleccionados_idx:
                        try:
                            seleccionados_set.add(int(s))
                        except Exception:
                            pass

                    creados = 0
                    omitidos = 0
                    errores = 0
                    articulos_df = []
                    for i in range(len(referencias_proveedor)):
                        if i not in seleccionados_set:
                            continue
                        ref_p = (referencias_proveedor[i] or '').strip()
                        if not ref_p:
                            errores += 1
                            continue
                        # Validar backend: línea pertenece a factura actual
                        inv_line_id = None
                        if i < len(ids_inv_factura):
                            try:
                                inv_line_id = int(ids_inv_factura[i])
                            except Exception:
                                inv_line_id = None
                        if inv_line_id is not None:
                            cursor.execute("SELECT id FROM inventarioFacturas WHERE id = ? AND id_factura = ?", (inv_line_id, id_factura))
                            if not cursor.fetchone():
                                errores += 1
                                continue
                        # Validar backend: no duplicar en consolidado
                        if factura[5]:
                            try:
                                cursor.execute("SELECT 1 FROM consolidado WHERE UPPER(TRIM(abrevProveedor)) = ? AND UPPER(TRIM(referenciaProveedor)) = ?", (str(factura[5]).upper().strip(), ref_p.upper()))
                                if cursor.fetchone():
                                    omitidos += 1
                                    continue
                            except Exception:
                                pass
                        desc = (descripciones[i] if i < len(descripciones) else '') or ''
                        if not desc.strip():
                            errores += 1
                            continue
                        articulos_df.append({
                            "referenciaProveedor": ref_p,
                            "referenciaRepuesto": (referencias_repuesto[i] if i < len(referencias_repuesto) else '') or '',
                            "equivalente": (equivalentes[i] if i < len(equivalentes) else '') or '',
                            "descripcion": desc.strip(),
                            "marca": (marcas[i] if i < len(marcas) else '') or '',
                            "carro": (carros[i] if i < len(carros) else '') or '',
                            "tipo": "",
                            "tipoMotor": "",
                            "precioListado": (precioListado[i] if i < len(precioListado) else '') or '',
                            "proveedor": factura[2],
                            "idProveedor": factura[1],
                            "abrevProveedor": factura[5]
                        })
                    if not articulos_df:
                        flash(f"Procesados: creados {creados}, omitidos {omitidos}, errores {errores}", "warning")
                        return redirect(url_for('facturas.ver_factura', id_factura=id_factura, procesar=1))
                    df = pd.DataFrame(articulos_df)
                    df = procesar_dataframe_pendientes(df, ruta_estandarizacion)
                    from app import actualizar_consolidado
                    resultado = actualizar_consolidado(conn, df)
                    creados_res = resultado.get('nuevos', 0)
                    mod_res = resultado.get('modificados', 0)
                    flash(
                        f"Procesados: creados {creados_res}, modificados {mod_res}, omitidos {omitidos}, errores {errores}",
                        "success"
                    )
                    return redirect(url_for('facturas.ver_factura', id_factura=id_factura, procesar=1))
            except Exception as e:
                flash(f"Error procesando artículos a consolidado: {e}", "danger")
                return redirect(url_for('facturas.ver_factura', id_factura=id_factura, procesar=1))

        seleccionados = request.form.getlist('seleccionados')
        template_id = request.form.get("template_id")

        if not seleccionados:
            flash("Selecciona al menos un artículo para inventariar", "warning")
            return redirect(
                url_for('facturas.ver_factura', id_factura=id_factura)
            )

        ids = [a_entero(x, 0) for x in seleccionados]

        items_para_imprimir = []
        ruta_template = None
        procesados = 0
        omitidos = 0
        bloqueados = 0

        # ---- Transacción completa para todas las líneas seleccionadas ----
        try:
            with db.conexion(ruta_db) as conn:

                cursor = conn.cursor()

                placeholders = ",".join("?" * len(ids))

                cursor.execute(f"""
                    SELECT
                        a.id,
                        a.referencia,
                        a.descripcion_item,
                        a.cantidad,
                        a.valor_unitario,
                        a.inventariado,
                        a.id_inventarioUnico,
                        b.proveedor,
                        b.fecha
                    FROM inventarioFacturas a
                    LEFT JOIN facturas b ON a.id_factura = b.id_factura
                    WHERE a.id IN ({placeholders})
                      AND a.id_factura = ?
                """, (*ids, id_factura))

                items_bd = {fila[0]: fila for fila in cursor.fetchall()}

                ids_para_imprimir = []
                datos_impresion = []

                for id_fact_item in ids:

                    fila = items_bd.get(id_fact_item)

                    if not fila:
                        omitidos += 1
                        continue

                    (
                        id_fila, referencia, descripcion_orig, cantidad,
                        valor_unitario, inventariado, id_inventario_unico_bd, proveedor, fecha
                    ) = fila

                    # Revalidar estado real en backend
                    estado = obtener_estado_linea(conn, (proveedor, fecha, None, None, None), fila)
                    if not estado.get('permitido'):
                        bloqueados += 1
                        continue

                    datos_est = estado.get('datos_estandarizados') or {}
                    descripcion_est = datos_est.get('descripcion') or datos_est.get('descripcionEstandarizada') or descripcion_orig
                    referencia_use = referencia

                    valor_publico = request.form.get(
                        f'valor_publico_{id_fact_item}', ''
                    )

                    precio_venta = a_entero(
                        str(valor_publico).replace(".", "").strip(), 0
                    )
                    if precio_venta == 0 and valor_unitario:
                        try:
                            precio_venta = int(round(float(valor_unitario) * 1.5, -3))
                        except Exception:
                            precio_venta = 0
                    precio_cifrado = cifrar_precio(precio_venta)
                    precio_max_desc = round(precio_venta * 0.9, 0)
                    clave = normalizar_clave(descripcion_est)
                    if not clave:
                        bloqueados += 1
                        continue

                    prov_use = datos_est.get('proveedor') or proveedor or ''

                    cursor.execute("""
                        INSERT INTO inventarioUnico (
                            descripcion, descripcionClave, referencia, cantidad,
                            precioVenta, precioVentaCifrado, precioMaxDescuento,
                            proveedor, fechaActualizacion, grupo
                        )
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT(descripcionClave) DO UPDATE SET
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
                        referencia_use,
                        cantidad,
                        precio_venta,
                        precio_cifrado,
                        precio_max_desc,
                        prov_use,
                        fecha or datetime.now().strftime('%Y-%m-%d'),
                        ''
                    ))

                    cursor.execute("""
                        SELECT id
                        FROM inventarioUnico
                        WHERE descripcionClave = ?
                    """, (clave,))
                    row_inv = cursor.fetchone()
                    if not row_inv:
                        raise Exception(f"No se pudo obtener inventarioUnico para {id_fact_item}")
                    inventario_id = row_inv[0]

                    cursor.execute("""
                        UPDATE inventarioFacturas
                        SET inventariado = 1,
                            id_inventarioUnico = ?,
                            id_consolidado = COALESCE(?, id_consolidado)
                        WHERE id = ?
                    """, (inventario_id, datos_est.get('id_consolidado'), id_fact_item))

                    ids_para_imprimir.append(inventario_id)
                    procesados += 1

                # Datos para impresión
                if ids_para_imprimir and template_id:
                    placeholders_imp = ",".join("?" * len(ids_para_imprimir))

                    cursor.execute(f"""
                        SELECT
                            iU.codigoBarras,
                            iU.descripcion,
                            iU.precioVentaCifrado,
                            iF.cantidad,
                            PR.siglas
                        FROM inventarioFacturas iF
                        JOIN inventarioUnico iU ON iF.id_inventarioUnico = iU.id
                        LEFT JOIN proveedor PR
                            ON PR.nombreProveedor = iU.proveedor
                            OR PR.nombreProveedorSecundario = iU.proveedor
                        WHERE iF.id_factura = ?
                          AND iU.id IN ({placeholders_imp})
                    """, (id_factura, *ids_para_imprimir))
                    datos_impresion = cursor.fetchall()

                    cursor.execute("""
                        SELECT ubicacion
                        FROM bartenderFiles
                        WHERE idBartenderFiles = ?
                    """, (template_id,))
                    row_tpl = cursor.fetchone()

                    if not row_tpl:
                        raise Exception("Template BarTender no encontrado")

                    ruta_template = row_tpl[0]
                elif ids_para_imprimir and not template_id:
                    raise Exception("Debe seleccionar una etiqueta BarTender")

            # Fuera de transacción: imprimir solo si inventariado confirmado
            if datos_impresion and ruta_template:
                try:
                    imprimir_varios(
                        datos_impresion,
                        ruta_template=ruta_template,
                        columna_inicio=1
                    )
                except Exception as e_imp:
                    flash(f"Inventariado correcto, pero error al imprimir: {e_imp}", "warning")
                    return redirect(url_for('facturas.ver_factura', id_factura=id_factura))

            if procesados > 0:
                flash(f"Inventariados: {procesados}. Omitidos/bloqueados: {omitidos + bloqueados}", "success")
            elif bloqueados > 0:
                flash("Ninguna línea seleccionada pudo inventariarse. Verifique coincidencia con consolidado.", "danger")
            else:
                flash("No se procesaron líneas.", "warning")

        except Exception as e:
            flash(f"Error al inventariar: {e}", "danger")
            return redirect(url_for('facturas.ver_factura', id_factura=id_factura))

        return redirect(
            url_for('facturas.ver_factura', id_factura=id_factura)
        )

    # ===============================
    # GET → Mostrar factura
    # ===============================
    with db.conexion(ruta_db) as conn:

        cursor = conn.cursor()

        # Reparar totales históricos guardados en 0/NULL (una sola pasada).
        try:
            reparar_totales_faltantes(conn)
        except Exception as error:
            print(f"[facturas] No se pudieron reparar totales: {error}")

        cursor.execute("""
            SELECT idBartenderFiles, name
            FROM bartenderFiles
            WHERE activo = 1
            ORDER BY name
        """)
        bartender_templates = cursor.fetchall()

        cursor.execute("""
            SELECT proveedor, fecha, valor_total, texto_xml, ubicacion_pdf
            FROM facturas
            WHERE id_factura = ?
        """, (id_factura,))
        factura = cursor.fetchone()

        # Si el total sigue vacío, intentar una reparación puntual.
        if factura and not factura[2]:
            total_reparado = reparar_total_factura(conn, id_factura)
            if total_reparado is not None:
                factura = (
                    factura[0],
                    factura[1],
                    total_reparado,
                    factura[3],
                    factura[4],
                )

        cursor.execute("""
            SELECT fac.id_factura, prov.idProveedor, fac.proveedor, fac.fecha, fac.valor_total, prov.siglas
            FROM facturas fac
            LEFT JOIN proveedor prov ON fac.proveedor = prov.nombreProveedor OR fac.proveedor = prov.nombreProveedorSecundario
            WHERE fac.id_factura = ?
        """, (id_factura,))
        factura_consolidado = cursor.fetchone()

        cursor.execute("""
            SELECT id, referencia, descripcion_item, cantidad, valor_unitario, inventariado, id_inventarioUnico
            FROM inventarioFacturas
            WHERE id_factura = ?
        """, (id_factura,))
        factura_items = cursor.fetchall()

        prov_info_get = None
        if factura and factura[0]:
            try:
                prov_info_get = resolver_proveedor(conn, factura[0])
            except Exception:
                prov_info_get = None
        siglas_get = prov_info_get.get('siglas') if prov_info_get else (factura_consolidado[5] if factura_consolidado and len(factura_consolidado) > 5 else None)

        articulos_pendientes = []
        for fila in factura_items:
            ref_p = fila[1] or ""
            ya_consolidado = False
            if siglas_get and ref_p:
                try:
                    cursor.execute("SELECT 1 FROM consolidado WHERE UPPER(TRIM(abrevProveedor)) = ? AND UPPER(TRIM(referenciaProveedor)) = ?", (str(siglas_get).upper().strip(), str(ref_p).upper().strip()))
                    if cursor.fetchone():
                        ya_consolidado = True
                except Exception:
                    ya_consolidado = False
            articulos_pendientes.append({
                "idInventarioFactura": fila[0],
                "id_factura": id_factura,
                "referenciaProveedor": ref_p,
                "referenciaRepuesto": "",
                "equivalente": "",
                "descripcion": fila[2] or "",
                "marcaRepuesto": "",
                "carro": "",
                "lado": "",
                "modelo": "",
                "medida": "",
                "precioListado": fila[4],
                "ya_consolidado": ya_consolidado
            })

        estados = {}
        try:
            estados = obtener_estados_factura(conn, id_factura)
        except Exception:
            estados = {}

        mostrar_consolidado = request.args.get('procesar') == '1' or request.args.get('consolidar') == '1'

    return render_template(
        'facturas/detalle.html',
        factura=factura,
        id_factura=id_factura,
        items_factura=factura_items,
        bartender_templates=bartender_templates,
        estados_linea=estados,
        factura_consolidado=factura_consolidado,
        articulos_pendientes=articulos_pendientes,
        mostrar_consolidado=mostrar_consolidado
    )

@facturas_bp.route('/pdf/<filename>')
def ver_pdf(filename):
    pdf_folder = os.path.join(os.getcwd(), 'bin', 'pdf')
    print(f"Buscando PDF en: {pdf_folder} filename: {filename}")
    return send_from_directory(pdf_folder, filename)

def obtener_ultima_configuracion():
    ruta_db = db.ruta_bd_activa()
    if not ruta_db:
        return '', '', ''
    try:
        conn = db.conectar(ruta_db)
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
    ruta_db = db.ruta_bd_activa()
    if not ruta_db:
        return ''
    try:
        conn = db.conectar(ruta_db)
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

