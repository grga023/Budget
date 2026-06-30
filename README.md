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

## Project structure

```
Budget/
├── app.py                # Flask backend + REST API
├── requirements.txt
├── run.sh                # One-command setup & run
├── templates/
│   └── index.html
└── static/
    ├── css/styles.css
    └── js/app.js
```
