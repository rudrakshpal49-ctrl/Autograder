# AutoGrader — C Assignment Grading System

AutoGrader is a **Python + Streamlit + SQLite** application that helps teachers create C programming assignments, manage students and test cases, and automatically grade submitted `.c` files.

**Features:** 9 analyzers (correctness, logical, syntax, structure, modularity, memory safety, readability, originality, time complexity), weighted final grades, teacher JSON reports, student TXT/PDF reports, and manual/JSON/AI-assisted test-case creation.

## Requirements

- **Python 3.11+** and **GCC** (C compiler)
- **Ollama** with model **`qwen2.5-coder:3b`** for AI-assisted features
- **Valgrind** on Linux/WSL2 for time-complexity grading
- **VS Code** (optional; any terminal works)

> **Important:** Native Windows can run the app, but **time-complexity grading requires Linux or WSL2**. This is a local classroom prototype; do not run untrusted submissions on a public server without stronger isolation.

## 1. Install Ollama (Windows or Linux)

Install Ollama from **https://ollama.com/download**. On Linux, you can use:

```bash
curl -fsSL https://ollama.com/install.sh | sh
```

Then download the model **in the environment where Ollama runs**:

```bash
ollama pull qwen2.5-coder:3b
ollama list
```

Keep Ollama running while generating tests or using AI analyzers. If needed, start its server with `ollama serve` (don't start a second copy if one is already running).

## 2. Windows setup (VS Code PowerShell)

1. Install **Python** from https://www.python.org/downloads/.
2. Install **MSYS2** from https://www.msys2.org/. In its **UCRT64** terminal, run:

   ```bash
   pacman -S --needed mingw-w64-ucrt-x86_64-gcc
   ```

   Add `C:\msys64\ucrt64\bin` to Windows **PATH**. Restart the VS Code terminal and check `gcc --version`.
3. Open the AutoGrader project folder in VS Code → **Terminal → New Terminal** (PowerShell). Run:

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m database.init_db
.\.venv\Scripts\python.exe -m database.create_user --role teacher --username teacher1
.\.venv\Scripts\python.exe -m streamlit run app.py
```

The teacher creation command asks for a password. **Create the first teacher only once.** Open **http://localhost:8501** in your browser.

For later runs, use only:

```powershell
.\.venv\Scripts\python.exe -m streamlit run app.py
```

## 3. Linux / Ubuntu setup (all 9 analyzers)

Open a terminal **inside the project folder**, then run:

```bash
sudo apt update
sudo apt install -y python3 python3-venv python3-pip build-essential valgrind
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python -m database.init_db
python -m database.create_user --role teacher --username teacher1
python -m streamlit run app.py
```

Create the teacher only once. After the first setup, activate `.venv` and run `python -m streamlit run app.py`.

**Using Windows + VS Code WSL2?** Install Ubuntu using `wsl --install -d Ubuntu` in administrator PowerShell. In the VS Code **Ubuntu/WSL terminal**, go to the project folder (example: `cd "/mnt/d/Study Material/Computing Lab/AutoGrader"`). Follow the Linux setup above, but use **`.venv-wsl`** instead of `.venv` in its two environment commands. Never activate Windows `.venv` inside WSL. Keep your existing `data/` and `storage/` folders.

## 4. How to use

1. **Teacher:** Log in → create students and assignments → upload the assignment document and its `rules.json`.
2. **Teacher:** Add normal/edge test cases manually, by **JSON import**, or with Ollama. Verify/approve inputs and expected outputs; use a trusted reference program when available.
3. **Student:** Log in → upload a `.c` solution → open the submission to download **TXT/PDF feedback**.
4. **Teacher:** Open a submission → run evaluation → view the **9 analyzer scores** and the **final weighted grade** → download **report.json**.

Weights come from each assignment's `rules.json` and should add up to **1.0 (100%)**. An unavailable analyzer may prevent a final grade; originality with insufficient comparisons can produce a **provisional** grade instead.

## 5. Check your installation

From the project folder and active Python environment:

```bash
python tools/check_ollama.py --probe
python tools/check_complexity_install.py
python -m pytest -q
```

On Windows, replace `python` with `.\.venv\Scripts\python.exe` if needed. Callgrind is **not available on native Windows**; run Streamlit from WSL2/Linux to get numerical time-complexity results.

**Common errors:**
- **`gcc not found`** → install GCC and check PATH.
- **Ollama `Connection refused`** → start Ollama; check `http://127.0.0.1:11434` from the same environment as Streamlit.
- **Ollama `404`** → check `ollama list` for `qwen2.5-coder:3b` and the configured Ollama URL.
- **0 approved tests** → provide verified expected outputs/reference code and approve valid cases.
- **No final grade** → check which analyzer is unavailable; re-evaluate after fixing it.

