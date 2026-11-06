/* Pre-paint theme bootstrap.
 *
 * Loaded as a blocking same-origin script from <head> so the stored dark-mode
 * preference is on <html> before the first paint (the production CSP forbids
 * inline scripts — see public/serve.json). Keep this dependency-free.
 */
(function () {
  try {
    var settings = JSON.parse(localStorage.getItem('settings-storage'));
    if (settings && settings.state && settings.state.theme === 'dark') {
      document.documentElement.classList.add('dark');
    }
  } catch (e) {}
})();
