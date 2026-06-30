"""Production entry point for the Budget app.

Runs the Flask app through Waitress, a lightweight pure-Python WSGI server
that is well suited to low-power hardware such as a Raspberry Pi Zero 2 W.

Configuration via environment variables:
  BUDGET_HOST     bind address   (default 0.0.0.0)
  BUDGET_PORT     bind port      (default 5000)
  BUDGET_THREADS  worker threads (default 4)
"""
import os

from waitress import serve

from app import app

if __name__ == "__main__":
    host = os.environ.get("BUDGET_HOST", "0.0.0.0")
    port = int(os.environ.get("BUDGET_PORT", "5000"))
    threads = int(os.environ.get("BUDGET_THREADS", "4"))
    print(f"Budget app serving on http://{host}:{port} ({threads} threads)")
    serve(app, host=host, port=port, threads=threads)
