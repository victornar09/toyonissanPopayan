from flask import Blueprint, render_template, session, redirect, url_for

ion_bp = Blueprint('ion', __name__, url_prefix='/ion')

@ion_bp.route('/')
def ion_home():
    if 'usuario' not in session:
        return redirect(url_for('auth.login'))
    return render_template('ion/ion.html', usuario=session['usuario'])