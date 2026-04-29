"""
Sync-compatible Playwright stealth helpers.

The JavaScript payloads (`_JS_BASE_STEALTH`, `_JS_ADVANCED_STEALTH`,
`_build_locale_script`) are ported verbatim from `pw-stealth-enhanced`
(https://github.com/fukukei23/pw-stealth-enhanced, MIT). The upstream
package targets `playwright.async_api`; this module wraps the same
scripts for the synchronous API used in this project.

Used by the river backend only — see `STEALTH_BACKENDS` in
`common/utils/browser.py`.
"""

from typing import Optional

from playwright.sync_api import BrowserContext


_JS_BASE_STEALTH = """\
try { localStorage.setItem('a11y-contrast','off'); localStorage.setItem('high-contrast','off'); } catch(e){}
Object.defineProperty(navigator, 'language', {get: () => 'en-GB'});
Object.defineProperty(navigator, 'languages', {get: () => ['en-GB','en']});
(function(){
  const _rz=Intl.DateTimeFormat.prototype.resolvedOptions;
  Intl.DateTimeFormat.prototype.resolvedOptions=function(){
    const o=_rz.call(this); o.timeZone='UTC'; return o;
  };
})();
"""

_JS_ADVANCED_STEALTH = r"""
(() => {
  try {
    const rand = (min, max) => Math.random() * (max - min) + min;
    const nav = navigator;
    if (nav) {
      const lang = (nav.language || 'en-GB');
      const langs = Array.isArray(nav.languages) && nav.languages.length
        ? nav.languages : ['en-GB','en'];
      Object.defineProperty(nav, 'webdriver', { get: () => undefined });
      Object.defineProperty(nav, 'hardwareConcurrency', { get: () => 8 });
      Object.defineProperty(nav, 'deviceMemory', { get: () => 8 });
      Object.defineProperty(nav, 'language', { get: () => lang });
      Object.defineProperty(nav, 'languages', { get: () => langs });
      Object.defineProperty(nav, 'maxTouchPoints', { get: () => 0 });
      Object.defineProperty(nav, 'platform', { get: () => 'Win32' });
    }
    /* Canvas fingerprint noise */
    const patchCanvas = (proto) => {
      if (!proto) return;
      const toDataURL = proto.toDataURL;
      proto.toDataURL = function(...args) {
        const ctx = this.getContext && this.getContext('2d');
        if (ctx) {
          const shift = () => (Math.random() - 0.5) * 2;
          ctx.fillStyle = `rgba(${128+shift()},${128+shift()},${128+shift()},0.01)`;
          ctx.fillRect(0, 0, 2, 2);
        }
        return toDataURL.apply(this, args);
      };
    };
    if (typeof HTMLCanvasElement !== 'undefined' && HTMLCanvasElement.prototype)
      patchCanvas(HTMLCanvasElement.prototype);
    if (typeof OffscreenCanvas !== 'undefined' && OffscreenCanvas.prototype)
      patchCanvas(OffscreenCanvas.prototype);

    /* WebGL vendor/renderer spoofing */
    const patchWebGL = (proto) => {
      if (!proto) return;
      const getParameter = proto.getParameter;
      proto.getParameter = function(param) {
        const VENDOR = 0x1F00, RENDERER = 0x1F01;
        if (param === VENDOR) {
          const v = getParameter.call(this, param);
          return typeof v === 'string' ? v.replace(/Google Inc\./, 'Google LLC') : v;
        }
        if (param === RENDERER) {
          const r = getParameter.call(this, param);
          return typeof r === 'string' ? r.replace(/ANGLE \(|\)/g, '') : r;
        }
        return getParameter.call(this, param);
      };
    };
    if (typeof WebGLRenderingContext !== 'undefined' && WebGLRenderingContext.prototype)
      patchWebGL(WebGLRenderingContext.prototype);
    if (typeof WebGL2RenderingContext !== 'undefined' && WebGL2RenderingContext.prototype)
      patchWebGL(WebGL2RenderingContext.prototype);

    /* Audio fingerprint perturbation */
    if (typeof AnalyserNode !== 'undefined' && AnalyserNode.prototype) {
      const getFloat = AnalyserNode.prototype.getFloatFrequencyData;
      if (getFloat) {
        AnalyserNode.prototype.getFloatFrequencyData = function(arr) {
          const res = getFloat.call(this, arr);
          for (let i = 0; i < arr.length; i += Math.floor(arr.length / 8) || 1) {
            arr[i] = arr[i] * (0.99 + Math.random() * 0.02);
          }
          return res;
        };
      }
    }

    /* Font enumeration spoofing */
    if (typeof Navigator !== 'undefined' && Navigator.prototype) {
      const origFonts = Navigator.prototype.fonts;
      if (origFonts) {
        Navigator.prototype.fonts = function() {
          const it = origFonts.apply(this, arguments);
          if (it && typeof it.status === 'string') return it;
          return {
            status: 'loaded', check: () => true,
            load: () => Promise.resolve(), values: () => [].values()
          };
        };
      }
    }

    /* permissions.query patch */
    if (typeof navigator !== 'undefined' && navigator.permissions && navigator.permissions.query) {
      const origQuery = navigator.permissions.query;
      navigator.permissions.query = function(descriptor) {
        if (descriptor && descriptor.name)
          return Promise.resolve({ state: 'granted', onchange: null });
        return origQuery.call(this, descriptor);
      };
    }
  } catch (e) { /* swallow */ }
})();
"""


def _build_locale_script(locale: Optional[str], timezone_id: Optional[str]) -> str:
    parts: list[str] = []
    if locale:
        parts.append(
            f"Object.defineProperty(navigator, 'language', {{get: () => '{locale}'}});"
        )
        parts.append(
            f"Object.defineProperty(navigator, 'languages', {{get: () => ['{locale}', 'en']}});"
        )
    if timezone_id:
        parts.append(
            f"const _rz=Intl.DateTimeFormat.prototype.resolvedOptions;"
            f"Intl.DateTimeFormat.prototype.resolvedOptions=function(){{"
            f"const o=_rz.call(this); o.timeZone='{timezone_id}'; return o;}};"
        )
    if not parts:
        return ""
    return f"(() => {{ try {{ {''.join(parts)} }} catch(e){{}} }})();"


def apply_stealth_sync(
    context: BrowserContext,
    *,
    locale: Optional[str] = None,
    timezone_id: Optional[str] = None,
) -> None:
    """Apply stealth/anti-fingerprinting init scripts to a sync BrowserContext.

    Equivalent to upstream's ``apply_stealth`` but for ``playwright.sync_api``.
    Pass ``locale`` / ``timezone_id`` to override the base script's defaults
    (en-GB / UTC).
    """
    context.add_init_script(script=_JS_BASE_STEALTH)
    context.add_init_script(script=_JS_ADVANCED_STEALTH)
    locale_script = _build_locale_script(locale, timezone_id)
    if locale_script:
        context.add_init_script(script=locale_script)
