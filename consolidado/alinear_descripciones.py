"""Rutina segura para alinear descripciones históricas con Consolidado.

Uso (por defecto sólo simula, no escribe):

    python -m consolidado.alinear_descripciones
    python -m consolidado.alinear_descripciones --aplicar

La rutina sólo toca artículos de ``inventarioUnico`` vinculados a un registro
de ``consolidado`` con ``descripcionEstandarizada`` no vacía. Omite artículos
cuya nueva clave choque con otro artículo existente o cuyos registros de
Consolidado tengan descripciones estándar en conflicto.
"""

from __future__ import annotations

import argparse
import json

import db
from consolidado import servicio


def main():
    parser = argparse.ArgumentParser(
        description="Alinea descripciones de inventario con Consolidado."
    )
    parser.add_argument(
        "--aplicar",
        action="store_true",
        help="Confirma los cambios. Sin esta opción sólo se simula.",
    )
    parser.add_argument(
        "--db",
        dest="ruta_db",
        default=None,
        help="Ruta explícita a la base de datos (por defecto la activa).",
    )
    args = parser.parse_args()

    ruta_db = args.ruta_db or db.ruta_bd_activa()
    if not ruta_db:
        raise SystemExit("No hay base de datos activa. Indica --db <ruta>.")

    resultado = servicio.alinear_descripciones_historicas(
        ruta_db, aplicar=args.aplicar
    )
    print(json.dumps(resultado, ensure_ascii=False, indent=2))

    if not args.aplicar and resultado["actualizados"]:
        print("\nSimulación: use --aplicar para confirmar los cambios.")


if __name__ == "__main__":
    main()
