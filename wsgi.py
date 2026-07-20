"""
wsgi.py -- production entry point.

A cloud host (Render, Railway, etc.) starts the app through a WSGI server such as
gunicorn, which imports this file and looks for a module-level `app`. Unlike
run_ui.py, this does NOT call app.run() and does NOT enable debug mode -- the
WSGI server handles serving.

    gunicorn wsgi:app --bind 0.0.0.0:$PORT
"""

from web import create_app

app = create_app()
