from flask import Blueprint, render_template, session, redirect, url_for, request, jsonify
import sqlite3
import os

grupo_bp = Blueprint('grupos', __name__, url_prefix='/grupos')

def get_db():

    carpeta_bd = os.path.join(os.getcwd(), 'bdlocal')
    db_files = [f for f in os.listdir(carpeta_bd) if f.endswith('.sqlite')]
    if db_files:
        ruta_db = os.path.join(carpeta_bd, db_files[-1])
        return sqlite3.connect(ruta_db)
    

@grupo_bp.route('/')
def grupo_home():
    if 'usuario' not in session:
        return redirect(url_for('auth.login'))

    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT idGrupo, grupo, variante, tipo, abreviacion
        FROM grupoUnico
        ORDER BY grupo
    """)
    grupos = cursor.fetchall()
    conn.close()

    return render_template(
        'grupo/grupo.html',
        usuario=session['usuario'],
        grupos=grupos
    )


@grupo_bp.route('/crear', methods=['POST'])
def crear_grupo():
    data = request.json

    grupo = data.get('grupo')
    variante = data.get('variante')
    tipo = data.get('tipo')
    abreviacion = data.get('abreviacion')

    if not grupo or not variante or not abreviacion:
        return jsonify({'error': 'Campos obligatorios incompletos'}), 400

    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO grupoUnico (grupo, variante, tipo, abreviacion)
        VALUES (?, ?, ?, ?)
    """, (grupo, variante, tipo, abreviacion))
    conn.commit()
    conn.close()

    return jsonify({'success': True})
