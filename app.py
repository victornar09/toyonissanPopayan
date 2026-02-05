from flask import Flask, render_template, request, session, redirect, url_for, send_from_directory
import webbrowser
import threading
from auth import auth
from ion.routes import ion_bp
from facturas.routes import facturas_bp
from inventario.routes import inventario_bp
from grupos.routes import grupo_bp
import config
import os
import sqlite3
from datetime import datetime
import imaplib
import email
from email.header import decode_header
from email.utils import parseaddr
import os
import zipfile

app = Flask(__name__)
app.secret_key = 'clave-secreta-muy-segura'

app.register_blueprint(auth)
app.register_blueprint(ion_bp)
app.register_blueprint(facturas_bp)
app.register_blueprint(inventario_bp)
app.register_blueprint(grupo_bp)

# Ruta absoluta del directorio 'bin/codigos'
RUTA_CODIGOS = os.path.join(os.path.dirname(__file__), 'bin', 'codigos')

@app.route('/bin/codigos/<path:filename>')
def servir_codigos(filename):
    """Sirve archivos PDF desde /bin/codigos"""
    return send_from_directory(RUTA_CODIGOS, filename)


@app.route('/')
def index():
    if 'usuario' not in session:
        return redirect(url_for('auth.login'))
    
    return render_template('index.html', usuario=session['usuario'])

@app.route('/ejecutar', methods=['POST'])
def ejecutar():
    resultado = "¡Programa ejecutado con éxito!"
    return render_template('index.html', usuario=session['usuario'], mensaje=resultado)

@app.route('/configuracion', methods=['GET', 'POST'])
def configuracion():
    mensaje = None
    nombre_db = ''
    email_revision = ''
    token_contraseña = ''
    correos_varios = ''
    sql_result = None
    sql_error = None

    carpeta_bd = os.path.join(os.getcwd(), 'bdlocal')
    os.makedirs(carpeta_bd, exist_ok=True)

    # Buscar la última base de datos creada
    db_files = [f for f in os.listdir(carpeta_bd) if f.endswith('.sqlite')]
    ruta_db = os.path.join(carpeta_bd, db_files[-1]) if db_files else None

    # Leer la última configuración si existe
    if ruta_db:
        try:
            conn = sqlite3.connect(ruta_db)
            cursor = conn.cursor()
            cursor.execute("""
                SELECT nombre_db, email_revision, token_contraseña, correos_varios 
                FROM configuracion ORDER BY id DESC LIMIT 1
            """)
            row = cursor.fetchone()
            if row:
                nombre_db, email_revision, token_contraseña, correos_varios = row
            conn.close()
        except Exception as e:
            mensaje = f"Error al leer la configuración: {e}"

    if request.method == 'POST':
        accion = request.form.get('accion')

        if accion == 'crear_bd':
            nombre_db = request.form.get('nombre_db')
            if nombre_db:
                ruta_db = os.path.join(carpeta_bd, f"{nombre_db}.sqlite")
                try:
                    conn = sqlite3.connect(ruta_db)
                    cursor = conn.cursor()
                    cursor.execute("""
                                   
                        create table main.bartenderFiles
                        (
                            idBartenderFiles INTEGER      not null
                                constraint bartenderFiles_pk
                                    primary key autoincrement,
                            name             varchar(200) not null,
                            ubicacion        varchar(250) not null,
                            numeroColumnas   integer      not null,
                            activo           BLOB
                        );
                    """)

                    cursor.execute("""
                                   
                        create table configuracion
                        (
                            id               INTEGER
                                primary key autoincrement,
                            nombre_db        TEXT,
                            email_revision   TEXT,
                            token_contraseña TEXT,
                            correos_varios   TEXT
                        );
                    """)
                    
                    cursor.execute("""
                        create table facturas
                        (
                            id            INTEGER
                                primary key autoincrement,
                            id_factura    TEXT,
                            fecha         datetime,
                            proveedor     TEXT,
                            valor_total   REAL,
                            correo        TEXT,
                            texto_xml     TEXT,
                            ubicacion_pdf TEXT
                        );
                    """)

                    cursor.execute("""
                                   
                        create table main.grupoUnico
                        (
                            idGrupo     integer      not null
                                constraint grupoUnico_pk
                                    primary key autoincrement,
                            grupo       varchar(100) not null,
                            variante    varchar(200) not null,
                            tipo        varchar(100),
                            abreviacion varchar(20)  not null
                        );
                    """)
                                   
                    cursor.execute("""
                        create table inventarioFacturas
                        (
                            id                 INTEGER
                                primary key autoincrement,
                            id_factura         TEXT ,
                            descripcion_item   TEXT UNIQUE,
                            cantidad           INTEGER,
                            valor_unitario     REAL,
                            referencia         TEXT,
                            inventariado       BOOLEAN default 0,
                            id_inventarioUnico integer
                        );
                    """)



                    cursor.execute("""               
                        create table inventarioUnico
                        (
                            id                 INTEGER
                                primary key autoincrement,
                            codigoBarras       TEXT,
                            descripcion        TEXT,
                            cantidad           INTEGER,
                            precioVenta        REAL,
                            precioVentaCifrado TEXT,
                            precioMaxDescuento REAL,
                            grupo              TEXT,
                            idUbicacion        integer
                        );
                    """)
                    cursor.execute("""
                        create unique index idx_inventario_descripcion
                            on inventarioUnico (descripcion);}
                    """)
                                   
                    cursor.execute("""
                        CREATE TRIGGER set_codigoBarras
                        AFTER INSERT ON inventarioUnico
                        FOR EACH ROW
                        BEGIN
                            UPDATE inventarioUnico
                            SET codigoBarras = printf('%08d', NEW.id)
                            WHERE id = NEW.id;
                        END;
                    """)

                    cursor.execute("""
                        create table main.proveedor
                        (
                            idProveedor    integer      not null
                                constraint proveedor_pk
                                    primary key autoincrement,
                            proveedor      varchar(500) not null,
                            siglas         varchar(100) not null,
                            correoFacturas varchar(500) not null,
                            prefijo        varchar(20)  not null,
                            ciudad         varchar(100),
                            telefono       integer
                        );
                    """)

                    cursor.execute("""
                        create table Ubicacion
                        (
                            idUbicacion     integer           not null
                                constraint Ubicacion_pk
                                    primary key autoincrement,
                            descripcion     varchar(100)
                                constraint Ubicacion_pk_2
                                    unique,
                            piso            integer default 1 not null,
                            pasillo         integer default null,
                            numero          integer,
                            tipo            varchar(100),
                            codigoUbicacion varchar(10)       not null
                        );

                    """)

                    cursor.execute("""
                        CREATE TRIGGER trg_ubicacion_descripcion
                        AFTER INSERT ON Ubicacion
                        BEGIN
                            UPDATE Ubicacion
                            SET descripcion =
                                CASE
                                    WHEN tipo = 'E' THEN 'P-' || piso || ' Pllo-' || pasillo || ' E-' || numero
                                    WHEN tipo = 'V' THEN 'P-' || piso || ' Pllo-' || pasillo || ' V-' || numero
                                    WHEN tipo = 'C' THEN 'P-' || piso || ' Pllo-' || pasillo || ' Caja-' || numero
                                END
                            WHERE rowid = NEW.rowid;
                        END;
                    """)

                    conn.commit()
                    conn.close()
                    mensaje = f"Base de datos '{nombre_db}' creada correctamente."
                except Exception as e:
                    mensaje = f"Error al crear la base de datos: {e}"

        elif accion == 'guardar_config':
            email_revision = request.form.get('email_revision')
            token_contraseña = request.form.get('token_contraseña')
            correos_varios = request.form.get('correos_varios')
            if ruta_db:
                try:
                    conn = sqlite3.connect(ruta_db)
                    cursor = conn.cursor()
                    cursor.execute("""
                        INSERT INTO configuracion (nombre_db, email_revision, token_contraseña, correos_varios)
                        VALUES (?, ?, ?, ?)
                    """, (
                        os.path.splitext(os.path.basename(ruta_db))[0],
                        email_revision,
                        token_contraseña,
                        correos_varios
                    ))
                    conn.commit()
                    conn.close()
                    mensaje = "Configuración guardada correctamente."
                except Exception as e:
                    mensaje = f"Error al guardar la configuración: {e}"
            else:
                mensaje = "Primero debe crear una base de datos."

        elif accion == 'ejecutar_sql':
            sql_query = request.form.get('sql_query')
            if ruta_db and sql_query:
                try:
                    conn = sqlite3.connect(ruta_db)
                    cursor = conn.cursor()
                    cursor.execute(sql_query)
                    conn.commit()

                    if sql_query.strip().lower().startswith("select"):
                        sql_result = cursor.fetchall()
                    else:
                        sql_result = "Consulta ejecutada correctamente."
                except Exception as e:
                    sql_error = str(e)
                finally:
                    conn.close()
            else:
                sql_error = "No hay base de datos seleccionada o consulta vacía."
        
        elif accion == 'guardar_template':
            nombre = request.form.get('nombre_template')
            ubicacion = request.form.get('archivo_template')
            numColumnas = request.form.get('numeroColumnas')

            if ruta_db and nombre and ubicacion:
                try:
                    conn = sqlite3.connect(ruta_db)
                    cursor = conn.cursor()
                    cursor.execute("""          
                        INSERT INTO bartenderFiles (name, ubicacion, numeroColumnas, activo)
                        VALUES (?, ?, ?, ?);
                    """, (nombre, ubicacion, numColumnas, True))
                    conn.commit()
                    conn.close()
                    mensaje = "Template de BarTender registrado correctamente."
                except Exception as e:
                    mensaje = f"Error al registrar template: {e}"
        
        elif accion == 'guardar_ubicacion':
            
            piso = request.form.get('piso')
            pasillo = request.form.get('pasillo')
            tipo = request.form.get('tipo')
            numero = request.form.get('numero')

            if ruta_db and piso and tipo and numero:
                try:
                    conn = sqlite3.connect(ruta_db)
                    cursor = conn.cursor()
                    cursor.execute("""          
                        INSERT INTO Ubicacion (piso, pasillo, tipo, numero)
                        VALUES (?, ?, ?, ?);
                    """, (piso, pasillo, tipo, numero))
                    conn.commit()
                    conn.close()
                    mensaje = "Ubicación registrada correctamente."
                except Exception as e:
                    mensaje = f"Error al registrar ubicación: {e}"

        elif accion == 'guardar_proveedor':
            nombre_proveedor = request.form.get('nombre_proveedor')
            sigla = request.form.get('sigla')
            correo_facturas = request.form.get('correo_facturas')
            prefijo = request.form.get('prefijo')
            ciudad = request.form.get('ciudad')
            telefono = request.form.get('telefono')

            if ruta_db and nombre_proveedor and sigla and correo_facturas and prefijo:
                try:
                    conn = sqlite3.connect(ruta_db)
                    cursor = conn.cursor()
                    cursor.execute("""          
                        INSERT INTO proveedor (proveedor, siglas, correoFacturas, prefijo, ciudad, telefono)
                        VALUES (?, ?, ?, ?, ?, ?);
                    """, (nombre_proveedor, sigla, correo_facturas, prefijo, ciudad, telefono))
                    conn.commit()
                    conn.close()
                    mensaje = "Proveedor registrado correctamente."
                except Exception as e:
                    mensaje = f"Error al registrar proveedor: {e}"


    bartender_templates = []

    if ruta_db:
        try:
            conn = sqlite3.connect(ruta_db)
            cursor = conn.cursor()
            cursor.execute("""
                SELECT *
                FROM bartenderFiles
                ORDER BY name
            """)
            bartender_templates = cursor.fetchall()
            conn.close()
        except:
            bartender_templates = []
    
    ubicaciones = []

    if ruta_db:
        try:
            conn = sqlite3.connect(ruta_db)
            cursor = conn.cursor()
            cursor.execute("""
                SELECT *
                FROM Ubicacion
                ORDER BY descripcion
            """)
            ubicaciones = cursor.fetchall()
            conn.close()
        except:
            ubicaciones = []
    
    proveedores = []

    if ruta_db:
        try:
            conn = sqlite3.connect(ruta_db)
            cursor = conn.cursor()
            cursor.execute("""
                SELECT proveedor, siglas, correoFacturas, prefijo, ciudad, telefono FROM proveedor
            """)
            proveedores = cursor.fetchall()
            conn.close()
        except:
            proveedores = []

    return render_template(
        'configuracion.html',
        mensaje=mensaje,
        nombre_db=nombre_db,
        email_revision=email_revision,
        token_contraseña=token_contraseña,
        correos_varios=correos_varios,
        sql_result=sql_result,
        sql_error=sql_error,
        bartender_templates=bartender_templates,
        ubicaciones=ubicaciones,
        proveedores=proveedores
    )



def abrir_navegador():
    webbrowser.open_new("http://localhost:5000")


if __name__ == '__main__':
    threading.Timer(1.25, abrir_navegador).start()
    app.run(debug=True)
