CREATE TABLE IF NOT EXISTS bartenderFiles (
    idBartenderFiles INTEGER NOT NULL
        CONSTRAINT bartenderFiles_pk
        PRIMARY KEY AUTOINCREMENT,
    name VARCHAR(200) NOT NULL,
    ubicacion VARCHAR(250) NOT NULL,
    numeroColumnas INTEGER NOT NULL,
    activo BLOB
);

CREATE TABLE IF NOT EXISTS configuracion (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    nombre_db TEXT,
    email_revision TEXT,
    token_contraseña TEXT,
    correos_varios TEXT
);

CREATE TABLE IF NOT EXISTS facturas (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    id_factura TEXT,
    fecha DATETIME,
    proveedor TEXT,
    valor_total REAL,
    correo TEXT,
    texto_xml TEXT,
    ubicacion_pdf TEXT
);

CREATE TABLE IF NOT EXISTS grupoUnico (
    idGrupo INTEGER NOT NULL
        CONSTRAINT grupoUnico_pk
        PRIMARY KEY AUTOINCREMENT,
    grupo VARCHAR(100) NOT NULL,
    variante VARCHAR(200) NOT NULL,
    tipo VARCHAR(100),
    abreviacion VARCHAR(20) NOT NULL
);

CREATE TABLE IF NOT EXISTS inventarioFacturas (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    id_factura TEXT,
    descripcion_item TEXT UNIQUE,
    cantidad INTEGER,
    valor_unitario REAL,
    referencia TEXT,
    inventariado BOOLEAN DEFAULT 0,
    id_inventarioUnico INTEGER
);

CREATE TABLE IF NOT EXISTS inventarioUnico (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    codigoBarras TEXT,
    referencia VARCHAR(100),
    descripcion TEXT,
    cantidad INTEGER,
    precioVenta REAL,
    precioVentaCifrado TEXT,
    precioMaxDescuento REAL,
    grupo TEXT,
    proveedor VARCHAR(200) NOT NULL,
    fechaActualizacion DATE NOT NULL,
    idUbicacion INTEGER
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_inventario_descripcion
ON inventarioUnico (descripcion);

CREATE TRIGGER IF NOT EXISTS set_codigoBarras
AFTER INSERT ON inventarioUnico
FOR EACH ROW
BEGIN
    UPDATE inventarioUnico
    SET codigoBarras = printf('%08d', NEW.id)
    WHERE id = NEW.id;
END;

create table proveedor
(
    idProveedor               integer      not null
        constraint proveedor_pk
            primary key autoincrement,
    nombreProveedor           varchar(500) not null,
    nombreProveedorSecundario varchar(100),
    siglas                    varchar(100) not null,
    correoFacturas            varchar(500) not null,
    prefijo                   varchar(20)  not null,
    nit                       integer      not null,
    ciudad                    varchar(100),
    telefono                  integer
);


CREATE TABLE IF NOT EXISTS Ubicacion (
    idUbicacion INTEGER NOT NULL
        CONSTRAINT Ubicacion_pk
        PRIMARY KEY AUTOINCREMENT,
    descripcion VARCHAR(100)
        CONSTRAINT Ubicacion_pk_2
        UNIQUE,
    piso INTEGER DEFAULT 1 NOT NULL,
    pasillo INTEGER DEFAULT NULL,
    numero INTEGER,
    tipo VARCHAR(100),
    codigoUbicacion VARCHAR(10) NOT NULL
);

CREATE TRIGGER IF NOT EXISTS trg_ubicacion_descripcion
AFTER INSERT ON Ubicacion
BEGIN
    UPDATE Ubicacion
    SET descripcion =
        CASE
            WHEN tipo = 'E'
                THEN 'P-' || piso || ' Pllo-' || pasillo || ' E-' || numero
            WHEN tipo = 'V'
                THEN 'P-' || piso || ' Pllo-' || pasillo || ' V-' || numero
            WHEN tipo = 'C'
                THEN 'P-' || piso || ' Pllo-' || pasillo || ' Caja-' || numero
        END
    WHERE rowid = NEW.rowid;
END;

CREATE TABLE IF NOT EXISTS consolidado (
    id INTEGER PRIMARY KEY AUTOINCREMENT,

    idProveedor INTEGER,
    abrevProveedor TEXT NOT NULL,
    referenciaProveedor TEXT NOT NULL,

    referenciaRepuesto TEXT,
    equivalente TEXT,
    descripcion TEXT,
    marca TEXT,
    carro TEXT,
    tipo TEXT,
    tipoMotor TEXT,
    precioListado REAL,
    proveedor TEXT,
    lado TEXT,
    ladoAbreviado TEXT,
    repuesto TEXT,
    modelo TEXT,
    otros TEXT,
    medida TEXT,
    descripcionEstandarizada TEXT,

    fechaActualizacion DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE UNIQUE INDEX IF NOT EXISTS
idx_consolidado_llave
ON consolidado (
    abrevProveedor,
    referenciaProveedor
);

INSERT INTO proveedor (nombreProveedor, nombreProveedorSecundario, siglas, correoFacturas, prefijo, nit, ciudad, telefono)
VALUES ('MOTORES JAPONESES SAS', null, 'MJN', 'siesafe@siesa.com', '2FE', 805015305, null, null);

INSERT INTO proveedor (nombreProveedor, nombreProveedorSecundario, siglas, correoFacturas, prefijo, nit, ciudad, telefono)
VALUES ('REPUESTOS CALI SAS', 'REPUESTOS CALI SAS', 'RCLI', 'notificaciones@thefactoryhka.com.co>', 'FM4', 800074047, null, null);

INSERT INTO proveedor (nombreProveedor, nombreProveedorSecundario, siglas, correoFacturas, prefijo, nit, ciudad, telefono)
VALUES ('NIPONAUTOS S.A.S.', 'NIPONAUTOS SAS', 'NPNT', 'siesafe@siesa.com', 'FN6', 890329366, null, null);

INSERT INTO proveedor (nombreProveedor, nombreProveedorSecundario, siglas, correoFacturas, prefijo, nit, ciudad, telefono)
VALUES ('FRACO S.A.', 'FRACO FABRICA COLOMBIANA DE REPUESTOS AUTOMOTORES S.A. ', 'FCO', 'notificaciones@estupendo.com.co', 'CLA', 860012340, null, null);

INSERT INTO proveedor (nombreProveedor, nombreProveedorSecundario, siglas, correoFacturas, prefijo, nit, ciudad, telefono)
VALUES ('BOLIVARIANA DE CAMPEROS SAS', 'BOLIVARIANA DE CAMPEROS SAS', 'BDC', 'software@mg.oficinapro.co', 'EST', 811013352, null, null);

INSERT INTO proveedor (nombreProveedor, nombreProveedorSecundario, siglas, correoFacturas, prefijo, nit, ciudad, telefono)
VALUES ('REDPARTES S.A.S.', 'REDPARTES S.A.S.', 'REDP', 'factura-e@redpartes.com ', 'FRG', 900720611, null, null);

INSERT INTO proveedor (nombreProveedor, nombreProveedorSecundario, siglas, correoFacturas, prefijo, nit, ciudad, telefono)
VALUES ('DISCAMARGO S.A.', 'DISCAMARGO', 'DMGO', 'factura@transaccionesco.siigo', 'COO', 800130324, null, null);

INSERT INTO proveedor (nombreProveedor, nombreProveedorSecundario, siglas, correoFacturas, prefijo, nit, ciudad, telefono)
VALUES ('COMERCIALIZADORA C EFFE SAS', 'COMERCIALIZADORA C EFFE SAS', 'CCE', 'facturacionelectronica@dian.gov.co', 'FE', 900339657, null, null);

INSERT INTO proveedor (nombreProveedor, nombreProveedorSecundario, siglas, correoFacturas, prefijo, nit, ciudad, telefono)
VALUES ('JOTA REPUESTOS S.A.', 'JOTA REPUESTOS S.A.', 'JR', 'fedominadigital@domina.com.co', 'FE', 800079726, null, null);

INSERT INTO proveedor (nombreProveedor, nombreProveedorSecundario, siglas, correoFacturas, prefijo, nit, ciudad, telefono)
VALUES ('EDGAR EMIRO ROMERO GUTIERREZ TODO CAMPEROS JAPONESES', 'EDGAR EMIRO ROMERO GUTIERREZ TODO CAMPEROS JAPONESES', 'TCPR', 'facturacion@isiigo.com', 'NC', 3129475, null, null);

INSERT INTO proveedor (nombreProveedor, nombreProveedorSecundario, siglas, correoFacturas, prefijo, nit, ciudad, telefono)
VALUES ('MULTI PACKING S A S', 'MULTI PACKING S A S', 'MCKN', 'facturacion@multi-packing.com.co>', 'FVE', 900374850, null, null);

INSERT INTO proveedor (nombreProveedor, nombreProveedorSecundario, siglas, correoFacturas, prefijo, nit, ciudad, telefono)
VALUES ('VEHINYECCION S.A.S', 'VEHINYECCION S.A.S', 'VYCC', 'factura@transaccionesco.siigo.net', 'VH', 901245489, null, null);

INSERT INTO proveedor (nombreProveedor, nombreProveedorSecundario, siglas, correoFacturas, prefijo, nit, ciudad, telefono)
VALUES ('COMERCIAL AUTOPARTES SM S.A.S', 'COMERCIAL AUTOPARTES SM S.A.S', 'CAPT', 'mesadecontrol@facturatech.co', 'A8', 901244966, null, null);

INSERT INTO proveedor (nombreProveedor, nombreProveedorSecundario, siglas, correoFacturas, prefijo, nit, ciudad, telefono)
VALUES ('MOTOR JAPON S.A.S', 'MOTOR JAPON SM S.A.S', 'MPON', 'facturaemotorjapon@gmail.com', 'FCEP', 800249713, null, null);

INSERT INTO proveedor (nombreProveedor, nombreProveedorSecundario, siglas, correoFacturas, prefijo, nit, ciudad, telefono)
VALUES ('CASTELAUTO SAS', 'CASTELAUTO SAS', 'CAST', 'ateb.facturacion@ateb.com.co', 'FVEC', 800043701, null, null);

INSERT INTO proveedor (nombreProveedor, nombreProveedorSecundario, siglas, correoFacturas, prefijo, nit, ciudad, telefono)
VALUES ('AUTODICOL S.A.S', 'AUTODICOL S.A.S', 'ATCOL', 'facturacion@autodicol.com', 'FEBO', 900493066, null, null);

INSERT INTO proveedor (nombreProveedor, nombreProveedorSecundario, siglas, correoFacturas, prefijo, nit, ciudad, telefono)
VALUES ('YAMATO COLOMBIA CIA LTDA', 'YAMATO COLOMBIA CIA LTDA', 'YMCOL', 'siesafe@siesa.com', 'FYC', 860532352, null, null);

INSERT INTO proveedor (nombreProveedor, nombreProveedorSecundario, siglas, correoFacturas, prefijo, nit, ciudad, telefono)
VALUES ('IMPORTADORA INP AUTOPARTES S.A.S', 'IMPORTADORA INP AUTOPARTES S.A.S', 'INPA', 'notificaciones@thefactoryhka.com.co', 'FMA', 900617277, null, null);

INSERT INTO proveedor (nombreProveedor, nombreProveedorSecundario, siglas, correoFacturas, prefijo, nit, ciudad, telefono)
VALUES ('E&R EMPAQUES & AUTOPARTES S.A.S', 'E&R EMPAQUES & AUTOPARTES S.A.S', 'E&R', 'facturaelectronicaeyr@gmail.com', 'EYR', 900816986, null, null);

INSERT INTO proveedor (nombreProveedor, nombreProveedorSecundario, siglas, correoFacturas, prefijo, nit, ciudad, telefono)
VALUES ('PERALTA ALVIS Y CIA. S EN C', 'IMPORTADORA USA', 'PAYC', 'facturacionelectronica@impo-usa.com', 'FS', 800086855, null, null);

INSERT INTO proveedor (nombreProveedor, nombreProveedorSecundario, siglas, correoFacturas, prefijo, nit, ciudad, telefono)
VALUES ('INVERSIONES DAJUNI LTDA', 'INVERSIONES DAJUNI LTDA', 'IDL', 'facturacion@siigo.com', '58752', 800153249, null, null);

INSERT INTO proveedor (nombreProveedor, nombreProveedorSecundario, siglas, correoFacturas, prefijo, nit, ciudad, telefono)
VALUES ('AUTO NR SAS', 'AUTO NR SAS', 'ANS', 'no-responder@aliaddo.email', 'FE', 901552937, null, null);

INSERT INTO proveedor (nombreProveedor, nombreProveedorSecundario, siglas, correoFacturas, prefijo, nit, ciudad, telefono)
VALUES ('FRENIRODAMIENTOS LIMITADA', 'FRENIRODAMIENTOS LIMITADA', 'FLMTA', 'facturacion@siigo.com', 'CRE', 860403248, null, null);

INSERT INTO proveedor (nombreProveedor, nombreProveedorSecundario, siglas, correoFacturas, prefijo, nit, ciudad, telefono)
VALUES ('COMPAÑIA NACIONAL DE PRODUCTOS S.A.', 'COMPAÑIA NACIONAL DE PRODUCTOS S.A.', 'CNDP', 'cnpzonafranca@hotmail.com', 'BL', 901153000, null, null);

INSERT INTO proveedor (nombreProveedor, nombreProveedorSecundario, siglas, correoFacturas, prefijo, nit, ciudad, telefono) 
VALUES ('IMPORIENTE S.A.S', 'IMPORIENTE S.A.S', 'IMPRNTE', 'siesafe@siesa.com', 'FE', 830108123, null, null);

INSERT INTO proveedor (nombreProveedor, nombreProveedorSecundario, siglas, correoFacturas, prefijo, nit, ciudad, telefono)
VALUES ('GABRIEL PULIDO NEIRA', 'AUTOPERNOS', 'AUTPRNS', 'facturaelectronica@autopernos.com', 'APE', 17054684, null, null);

INSERT INTO configuracion (id, nombre_db, email_revision, token_contraseña, correos_varios) VALUES (1, 'toyonissan', 'jhonnarvaeztorres@gmail.com', 'gyse hxeh qgzy ujcm', 'siesafe@siesa.com,facturaelectronicaeyr@gmail.com,notificaciones@thefactoryhka.com.co,notificaciones@contapyme.com,facturacion@siigo.com,software@mg.oficinapro.co,software@mg.oficinapro.co,facturacionelectronica@impo-usa.com,motorjaponsas@gmail.com,facturacionelectronica@tymautopartes.com.co,no-responder@facture.co,cnpzonafranca@hotmail.com,no-responder@worldoffice.com.co');

INSERT INTO bartenderFiles (idBartenderFiles, name, ubicacion, numeroColumnas, activo) VALUES (1, '40x50', 'bartenderFiles/Documento1.btw', 2, 1);
INSERT INTO bartenderFiles (idBartenderFiles, name, ubicacion, numeroColumnas, activo) VALUES (2, '30x40', 'bartenderFiles/Documento2.btw', 1, 1);
