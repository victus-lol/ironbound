/* IRONBOUND — first-paint theme bootstrap (runs before CSS paints to avoid flash).
   Load this synchronously in <head>. Same logic previously inline in each page. */
(function () {
    'use strict';
    try {
        var t = localStorage.getItem('ib_theme');
        if (!t) {
            t = (window.matchMedia && window.matchMedia('(prefers-color-scheme: light)').matches) ? 'light' : 'dark';
        }
        document.documentElement.setAttribute('data-theme', t);
    } catch (e) {}
    document.documentElement.classList.add('theme-ready');
})();