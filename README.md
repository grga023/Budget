# Budget

A simple, modern personal budgeting web app. Track income and expenses, see your
balance, and visualize spending by category. Built with Flask + SQLite and a
vanilla JS frontend. No Docker required.

## Features

- Add income & expense transactions with category, date, and notes
- Live balance, income, and expense totals
- Doughnut chart of spending by category (Chart.js)
- Filter transactions by type
- Data persisted in a local SQLite database (`budget.db`)

## Run

```bash
./run.sh
```

Then open http://127.0.0.1:5000 in your browser.

### Manual setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python app.py
```

## Run as a service on a Raspberry Pi (Zero 2 W)

The app ships with a production server ([Waitress](https://github.com/Pylons/waitress),
pure-Python and light enough for the Pi Zero 2 W) and a systemd unit so it starts
automatically on boot and restarts on failure.

```bash
# On the Pi, from the app directory:
sudo apt install -y python3 python3-venv      # if not already present
sudo ./install-service.sh
```

The installer creates a virtual environment, installs dependencies, writes
`/etc/systemd/system/budget.service` tailored to your user and path, then enables
and starts the service.

```bash
sudo systemctl status budget     # check status
journalctl -u budget -f          # follow logs
sudo systemctl restart budget    # restart after changes
```

Open `http://<pi-ip>:5000` from any device on your network.

Configuration is via environment variables in the service unit (`BUDGET_HOST`,
`BUDGET_PORT`, `BUDGET_THREADS`, and optional `LLM_*` for the categorizer). To run
the production server manually:

```bash
python serve.py
```

## Project structure

```
Budget/
├── app.py                # Flask backend + REST API
├── serve.py              # Production WSGI entry point (Waitress)
├── budget.service        # systemd unit template
├── install-service.sh    # One-command service install for the Pi
├── requirements.txt
├── run.sh                # One-command dev setup & run
├── templates/
│   └── index.html
└── static/
    ├── css/styles.css
    └── js/app.js
```

