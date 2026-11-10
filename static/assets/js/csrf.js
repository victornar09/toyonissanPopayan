/* Protección CSRF global y protección básica contra doble envío.
 *
 * - Agrega el token a todos los formularios (incluidos los dinámicos).
 * - Añade la cabecera X-CSRF-Token a fetch, XMLHttpRequest y jQuery.ajax.
 * - Genera una clave de idempotencia por formulario para que un doble clic
 *   no cree dos trabajos de impresión.
 */
(function () {
    "use strict";

    var meta = document.querySelector('meta[name="csrf-token"]');
    var token = meta ? meta.getAttribute("content") : "";

    function nuevaClave() {
        if (window.crypto && typeof window.crypto.randomUUID === "function") {
            return window.crypto.randomUUID();
        }
        return (
            Date.now().toString(36) +
            "-" +
            Math.random().toString(36).slice(2) +
            Math.random().toString(36).slice(2)
        );
    }

    function prepararFormulario(form) {
        if (!form || form.tagName !== "FORM") {
            return;
        }

        if (token && !form.querySelector('input[name="csrf_token"]')) {
            var campo = document.createElement("input");
            campo.type = "hidden";
            campo.name = "csrf_token";
            campo.value = token;
            form.appendChild(campo);
        }

        if (!form.querySelector('input[name="clave_idempotencia"]')) {
            var clave = document.createElement("input");
            clave.type = "hidden";
            clave.name = "clave_idempotencia";
            clave.value = nuevaClave();
            form.appendChild(clave);
        }
    }

    function prepararFormularios() {
        var formularios = document.querySelectorAll("form");
        for (var i = 0; i < formularios.length; i += 1) {
            prepararFormulario(formularios[i]);
        }
    }

    // Formularios creados después de cargar la página.
    document.addEventListener(
        "submit",
        function (evento) {
            prepararFormulario(evento.target);
        },
        true
    );

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", prepararFormularios);
    } else {
        prepararFormularios();
    }

    function metodoSeguro(metodo) {
        metodo = (metodo || "GET").toUpperCase();
        return metodo === "GET" || metodo === "HEAD" || metodo === "OPTIONS";
    }

    // fetch
    if (window.fetch && token) {
        var fetchOriginal = window.fetch.bind(window);
        window.fetch = function (recurso, opciones) {
            opciones = opciones || {};
            var metodo = opciones.method || (recurso && recurso.method) || "GET";
            if (metodoSeguro(metodo)) {
                return fetchOriginal(recurso, opciones);
            }

            if (typeof Request !== "undefined" && recurso instanceof Request) {
                var copia = recurso.clone();
                copia.headers.set("X-CSRF-Token", token);
                return fetchOriginal(copia, opciones);
            }

            var cabeceras = new Headers(opciones.headers || {});
            cabeceras.set("X-CSRF-Token", token);
            opciones.headers = cabeceras;
            return fetchOriginal(recurso, opciones);
        };
    }

    // XMLHttpRequest
    if (window.XMLHttpRequest && token) {
        var abrirOriginal = XMLHttpRequest.prototype.open;
        var enviarOriginal = XMLHttpRequest.prototype.send;

        XMLHttpRequest.prototype.open = function (metodo) {
            this._metodoCsrf = metodo;
            return abrirOriginal.apply(this, arguments);
        };

        XMLHttpRequest.prototype.send = function () {
            if (!metodoSeguro(this._metodoCsrf)) {
                try {
                    this.setRequestHeader("X-CSRF-Token", token);
                } catch (error) {
                    /* La petición ya pudo haberse abierto de otra forma. */
                }
            }
            return enviarOriginal.apply(this, arguments);
        };
    }

    // jQuery (se usa en varias vistas)
    if (window.jQuery && token) {
        window.jQuery.ajaxSetup({
            headers: { "X-CSRF-Token": token },
        });
    }
})();
