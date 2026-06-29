# 🎯 HR Email Hunter ULTRA

HR Email Hunter ULTRA is a Python-based OSINT toolkit designed to discover, validate, and organize professional email addresses from publicly available sources. It combines multiple intelligence techniques into a single application with both Command-Line (CLI) and Graphical User Interface (GUI) support.

> **For educational, recruitment, and authorized OSINT research only.**

---

## ✨ Features

* 🌐 Company website email crawler
* 📱 Instagram & TikTok public profile scanning
* 🔍 Hunter.io integration
* 📧 Microsoft 365 email validation
* 📨 SMTP email verification
* 🎯 Confidence scoring system
* 💾 SQLite result caching
* 📊 Interactive HTML reports
* 📄 CSV & JSON export
* 🖥️ Modern Tkinter GUI
* ⚡ Multi-threaded scanning
* 🧩 Modular Python architecture

---

## Installation

```bash
git clone https://github.com/USERNAME/hr-email-hunter-ultra.git
cd hr-email-hunter-ultra

pip install -r requirements.txt
```

Or install manually:

```bash
pip install requests beautifulsoup4 dnspython aiohttp rich tqdm colorama
```

---

## Usage

### GUI

```bash
python hr_email_hunter_ultra.py --gui
```

### CLI

```bash
python hr_email_hunter_ultra.py -d company.com
```

Hunter.io example

```bash
python hr_email_hunter_ultra.py -d company.com -k YOUR_API_KEY
```

Generate HTML report

```bash
python hr_email_hunter_ultra.py -d company.com --report --export
```

---

## Output

The tool can generate:

* HTML Report
* CSV Export
* JSON Export
* SQLite Cache Database

---

## Technologies

* Python 3
* Requests
* BeautifulSoup
* dnspython
* SQLite
* Hunter.io API
* SMTP
* Microsoft 365
* Tkinter
* Rich

---

## Project Structure

```text
hr_email_hunter_ultra.py
hr_cache.db
reports/
exports/
```

---

## Disclaimer

This project is intended for educational purposes, recruitment workflows, and authorized OSINT research using publicly available information. Users are responsible for ensuring their use complies with applicable laws, platform terms of service, and organizational policies.

---

## License

MIT License
