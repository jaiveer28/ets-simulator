"""
run_ui.py -- start the web interface.

    python run_ui.py

Then open http://127.0.0.1:5000 in a browser.

Everything is served from local files and the local SQLite databases, so this
works with no internet connection.
"""

from web import create_app

app = create_app()

if __name__ == "__main__":
    print("=" * 62)
    print("  ETS — Educational Trading Simulator")
    print("  Open http://127.0.0.1:5000 in your browser")
    print("  Running fully offline from data/market.db")
    print("  Press CTRL+C to stop.")
    print("=" * 62)
    if app.config.get("USING_DEV_SECRET"):
        print("  Note: using the built-in dev SECRET_KEY. That's fine for local")
        print("  use. For a public deployment, set the SECRET_KEY environment")
        print("  variable to a long random value first.")
        print("=" * 62)
    app.run(debug=True, port=5000)
