"""PyInstaller runtime hook – fix SSL certificate resolution on macOS.

Frozen Python on macOS cannot find the system trust store. This hook
sets SSL_CERT_FILE to the certifi CA bundle that is shipped inside the
frozen app *before* any module tries to create an SSL context.
"""
import os
import sys

if not os.environ.get("SSL_CERT_FILE"):
    # In a PyInstaller bundle, __file__ lives inside _internal/ (onedir)
    # or inside the temp dir (onefile).  certifi/ was added as a data dir.
    _base = getattr(sys, "_MEIPASS", os.path.dirname(sys.executable))
    _cert = os.path.join(_base, "certifi", "cacert.pem")
    if os.path.isfile(_cert):
        os.environ["SSL_CERT_FILE"] = _cert
