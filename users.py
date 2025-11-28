from werkzeug.security import generate_password_hash

# Aquí defines tus usuarios y contraseñas (hasheadas)
USUARIOS = {
    "admin": generate_password_hash("clave123"),
    "jhon": generate_password_hash("jon12345"),
}