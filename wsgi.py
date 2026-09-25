"""WSGI entrypoint for hosting on PythonAnywhere, Gunicorn, or cloud servers."""
import os
import sys

# Ensure this project directory is in the Python module search path
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from app import app as application

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    application.run(host="0.0.0.0", port=port)
