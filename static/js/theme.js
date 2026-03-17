/* ── Card Maze — Theme Manager ─────────────────────────────────────────────
   Apply the saved theme immediately (called inline in <head> before CSS)
   and expose window.setTheme() for dropdown menu clicks.
   ────────────────────────────────────────────────────────────────────────── */
(function () {
  'use strict';

  var KEY   = 'cardmaze-theme';
  var BACKS = {
    dark:  '/static/images/back_dark.png',
    light: '/static/images/back_light.png'
  };

  function _apply(theme) {
    /* 1 ── HTML attribute drives all CSS variable overrides */
    document.documentElement.setAttribute('data-theme', theme);
    localStorage.setItem(KEY, theme);

    /* 2 ── Swap the card-back image */
    var img = document.getElementById('deck-img');
    if (img) img.src = BACKS[theme];

    /* 3 ── Dropdown checkmarks: show ✓ next to active theme */
    var ckD = document.getElementById('theme-check-dark');
    var ckL = document.getElementById('theme-check-light');
    if (ckD) ckD.style.opacity = theme === 'dark'  ? '1' : '0';
    if (ckL) ckL.style.opacity = theme === 'light' ? '1' : '0';
  }

  /* Called by onclick in navbar dropdown */
  window.setTheme = function (theme, e) {
    if (e) { e.preventDefault(); e.stopPropagation(); }
    _apply(theme);
  };

  /* On DOMContentLoaded apply fully (img + checkmarks need DOM) */
  document.addEventListener('DOMContentLoaded', function () {
    _apply(localStorage.getItem(KEY) || 'dark');
  });
}());
