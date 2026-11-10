/*
 * Utilidades reutilizables para tablas anchas:
 *  - barra de desplazamiento horizontal sincronizada (arriba y abajo)
 *  - descripciones estandarizadas multilínea con tooltip, copiar y expandir
 *
 * Se aplica automáticamente a todo elemento con la clase .tabla-scroll y a
 * todo texto con la clase .descripcion-estandarizada.
 */
(function () {
    'use strict';

    function crearBarraSuperior(contenedor) {
        if (contenedor.dataset.scrollSuperior === '1') {
            return null;
        }
        contenedor.dataset.scrollSuperior = '1';

        const barra = document.createElement('div');
        barra.className = 'tabla-scroll-top';
        barra.setAttribute('aria-hidden', 'true');

        const contenido = document.createElement('div');
        contenido.className = 'tabla-scroll-top-contenido';
        barra.appendChild(contenido);

        contenedor.parentNode.insertBefore(barra, contenedor);

        function sincronizarDesdeArriba() {
            contenedor.scrollLeft = barra.scrollLeft;
        }

        function sincronizarDesdeAbajo() {
            barra.scrollLeft = contenedor.scrollLeft;
        }

        barra.addEventListener('scroll', sincronizarDesdeArriba);
        contenedor.addEventListener('scroll', sincronizarDesdeAbajo);

        return { barra: barra, contenido: contenido };
    }

    function ajustarAncho(contenedor, barra, contenido) {
        if (!barra || !contenido) {
            return;
        }
        const ancho = Math.max(contenedor.scrollWidth, contenedor.clientWidth);
        contenido.style.width = ancho + 'px';
        // Sólo mostrar la barra superior si realmente hay desbordamiento.
        const hayDesbordamiento = contenedor.scrollWidth > contenedor.clientWidth + 1;
        barra.hidden = !hayDesbordamiento;
        if (barra.hidden) {
            barra.scrollLeft = 0;
        }
    }

    function inicializarScroll(contenedor) {
        const partes = crearBarraSuperior(contenedor);
        if (!partes) {
            return;
        }
        const { barra, contenido } = partes;

        const recalcular = function () {
            ajustarAncho(contenedor, barra, contenido);
        };

        recalcular();

        if (typeof ResizeObserver !== 'undefined') {
            const observador = new ResizeObserver(recalcular);
            observador.observe(contenedor);
            const tabla = contenedor.querySelector('table');
            if (tabla) {
                observador.observe(tabla);
            }
        }

        if (typeof MutationObserver !== 'undefined') {
            const observadorMutaciones = new MutationObserver(recalcular);
            observadorMutaciones.observe(contenedor, {
                childList: true,
                subtree: true,
                characterData: true,
            });
        }

        window.addEventListener('resize', recalcular);
    }

    function copiarTexto(texto, boton) {
        const restaurar = function (mensaje) {
            boton.textContent = mensaje;
            window.setTimeout(function () {
                boton.textContent = 'Copiar';
            }, 1200);
        };

        if (navigator.clipboard && navigator.clipboard.writeText) {
            navigator.clipboard.writeText(texto).then(
                function () { restaurar('Copiado'); },
                function () { restaurar('Error'); }
            );
            return;
        }

        const area = document.createElement('textarea');
        area.value = texto;
        area.style.position = 'fixed';
        area.style.opacity = '0';
        document.body.appendChild(area);
        area.select();
        try {
            document.execCommand('copy');
            restaurar('Copiado');
        } catch (error) {
            restaurar('Error');
        } finally {
            document.body.removeChild(area);
        }
    }

    function mejorarDescripcion(span) {
        if (span.dataset.descMejorada === '1') {
            return;
        }
        span.dataset.descMejorada = '1';

        const texto = (span.textContent || '').trim();
        if (!texto || texto === '—') {
            return;
        }

        span.setAttribute('title', texto);
        span.setAttribute('tabindex', '0');

        const contenedor = document.createElement('span');
        contenedor.className = 'desc-acciones';

        const botonCopiar = document.createElement('button');
        botonCopiar.type = 'button';
        botonCopiar.className = 'btn btn-link btn-sm desc-accion';
        botonCopiar.textContent = 'Copiar';
        botonCopiar.setAttribute('aria-label', 'Copiar descripción');
        botonCopiar.addEventListener('click', function (evento) {
            evento.preventDefault();
            evento.stopPropagation();
            copiarTexto(texto, botonCopiar);
        });

        const botonExpandir = document.createElement('button');
        botonExpandir.type = 'button';
        botonExpandir.className = 'btn btn-link btn-sm desc-accion';
        botonExpandir.textContent = 'Expandir';
        botonExpandir.setAttribute('aria-label', 'Expandir descripción');
        botonExpandir.addEventListener('click', function (evento) {
            evento.preventDefault();
            evento.stopPropagation();
            const expandida = span.classList.toggle('descripcion-expandida');
            botonExpandir.textContent = expandida ? 'Contraer' : 'Expandir';
        });

        contenedor.appendChild(botonCopiar);
        contenedor.appendChild(botonExpandir);
        span.insertAdjacentElement('afterend', contenedor);
    }

    function mejorarDescripciones(raiz) {
        (raiz || document).querySelectorAll('.descripcion-estandarizada').forEach(mejorarDescripcion);
    }

    function inicializarTodo() {
        document.querySelectorAll('.tabla-scroll').forEach(inicializarScroll);
        mejorarDescripciones(document);
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', inicializarTodo);
    } else {
        inicializarTodo();
    }

    // Expuesto para recargar tablas creadas dinámicamente.
    window.ToyonissanTablas = {
        inicializar: inicializarTodo,
        mejorarDescripciones: mejorarDescripciones,
    };
})();
