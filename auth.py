from flask import Blueprint, render_template, request, redirect, url_for, session, flash
from werkzeug.security import check_password_hash
from users import USUARIOS

auth = Blueprint('auth', __name__)

@auth.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form['usuario']
        password = request.form['clave']

        if username in USUARIOS and check_password_hash(USUARIOS[username], password):
            session['usuario'] = username
            return redirect(url_for('index'))
        else:
            flash("Usuario o contraseña incorrectos", "error")
    
    return render_template('login.html')

@auth.route('/logout')
def logout():
    session.pop('usuario', None)
    return redirect(url_for('auth.login'))